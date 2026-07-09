#!/usr/bin/env python3
"""FIND-1 M9b unattended CI apply service tests.

All HTTP is mocked via `findings_translate_apply_service.requests.patch` --
no real network access, no real Supabase project. These tests only exercise
the pure orchestration/transport contract: outbox file discovery/parsing,
PATCH request shape, matched-0/1/N accounting, retry-on-5xx, service-key
secrecy, dry-run (no PATCH calls), missing outbox dir, missing credentials,
and idempotent re-processing of the same file.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import findings_translate_apply_service as svc


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload

    def json(self):
        return self._payload


def _outbox_item(i: int, *, finding_text_ko: str | None = None) -> dict:
    return {
        "finding_id": f"f-{i:03d}",
        "finding_text": f"Observation number {i} was not documented.",
        "finding_text_ko": finding_text_ko or f"관찰사항 {i} 국문 번역.",
        "translation_method": "llm_assisted",
    }


def _write_outbox_file(directory: str, name: str, items: list[dict]) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return path


class OutboxDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_missing_outbox_dir_is_zero_files_not_an_error(self) -> None:
        missing_dir = os.path.join(self._tmp.name, "does-not-exist")
        with mock.patch("findings_translate_apply_service.requests.patch") as patch:
            report = svc.apply_outbox(missing_dir, _BASE_URL, _SERVICE_KEY)
        patch.assert_not_called()
        self.assertEqual(report["files_scanned"], 0)
        self.assertEqual(report["items_total"], 0)
        self.assertEqual(report["items_errored"], 0)

    def test_files_processed_in_name_sorted_order(self) -> None:
        _write_outbox_file(self._tmp.name, "b.json", [_outbox_item(2)])
        _write_outbox_file(self._tmp.name, "a.json", [_outbox_item(1)])

        seen_ids: list[str] = []

        def _fake_patch(url, params=None, json=None, headers=None, timeout=None):  # noqa: A002
            seen_ids.append(params["finding_id"])
            return _FakeResponse(200, [{"finding_id": params["finding_id"]}])

        with mock.patch(
            "findings_translate_apply_service.requests.patch", side_effect=_fake_patch
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["files_scanned"], 2)
        self.assertEqual(seen_ids, ["eq.f-001", "eq.f-002"])  # a.json (f-001) before b.json


class ApplySuccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_single_item_success(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}]),
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 1)
        _, kwargs = patch.call_args
        self.assertEqual(kwargs["params"]["finding_id"], "eq.f-001")
        self.assertEqual(
            kwargs["params"]["finding_text"],
            "eq.Observation number 1 was not documented.",
        )
        self.assertEqual(kwargs["json"]["finding_text_ko"], "관찰사항 1 국문 번역.")
        self.assertEqual(kwargs["json"]["translation_method"], "llm_assisted")
        self.assertEqual(kwargs["headers"]["apikey"], _SERVICE_KEY)
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {_SERVICE_KEY}")
        self.assertEqual(kwargs["timeout"], 15)

        self.assertEqual(report["mode"], "apply")
        self.assertEqual(report["files_scanned"], 1)
        self.assertEqual(report["items_total"], 1)
        self.assertEqual(report["items_succeeded"], 1)
        self.assertEqual(report["items_matched_zero"], 0)
        self.assertEqual(report["items_errored"], 0)
        self.assertEqual(report["errors"], [])

        # No file mutation: the outbox file is left exactly in place.
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "batch1.json")))

    def test_multiple_files_multiple_items(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1), _outbox_item(2)])
        _write_outbox_file(self._tmp.name, "batch2.json", [_outbox_item(3)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, [{"finding_id": "any"}]),
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 3)
        self.assertEqual(report["files_scanned"], 2)
        self.assertEqual(report["items_total"], 3)
        self.assertEqual(report["items_succeeded"], 3)


class MatchedZeroTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_zero_rows_matched_is_not_an_error(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, []),
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["items_matched_zero"], 1)
        self.assertEqual(report["items_succeeded"], 0)
        self.assertEqual(report["items_errored"], 0)
        self.assertEqual(report["errors"], [])

    def test_multiple_rows_matched_is_an_error(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}, {"finding_id": "f-001"}]),
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["items_errored"], 1)
        self.assertEqual(report["items_succeeded"], 0)
        self.assertTrue(any("matched 2 rows" in e for e in report["errors"]))


class HttpErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_http_error_counted_and_file_left_in_place(self) -> None:
        path = _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        before = Path(path).read_bytes()

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(403),
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["items_errored"], 1)
        self.assertEqual(report["items_succeeded"], 0)
        self.assertTrue(any("http_403" in e for e in report["errors"]))
        # File untouched -- no move/rename/delete on error.
        self.assertEqual(Path(path).read_bytes(), before)

    def test_5xx_retries_once_then_succeeds(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            side_effect=[_FakeResponse(503), _FakeResponse(200, [{"finding_id": "f-001"}])],
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["items_succeeded"], 1)
        self.assertEqual(report["items_errored"], 0)

    def test_5xx_exhausted_is_an_error(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            side_effect=[_FakeResponse(503), _FakeResponse(503)],
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["items_errored"], 1)
        self.assertTrue(any("http_503" in e for e in report["errors"]))

    def test_invalid_json_file_counted_as_error_and_left_in_place(self) -> None:
        path = os.path.join(self._tmp.name, "broken.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        with mock.patch("findings_translate_apply_service.requests.patch") as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        patch.assert_not_called()
        self.assertEqual(report["files_scanned"], 1)
        self.assertEqual(report["items_errored"], 1)
        self.assertTrue(os.path.exists(path))


class ServiceKeySecrecyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_service_key_never_appears_in_report_on_error(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(500),
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)

    def test_service_key_never_appears_in_exception_path(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        import requests as _requests

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            side_effect=_requests.exceptions.ConnectionError(f"key={_SERVICE_KEY} leaked"),
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)
        self.assertTrue(any("ConnectionError" in e for e in report["errors"]))


class DryRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_dry_run_reads_files_but_issues_no_patch_calls(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1), _outbox_item(2)])

        with mock.patch("findings_translate_apply_service.requests.patch") as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY, dry_run=True)

        patch.assert_not_called()
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["files_scanned"], 1)
        self.assertEqual(report["items_total"], 2)
        self.assertEqual(report["items_succeeded"], 2)


class IdempotentReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_processing_the_same_file_twice_is_safe(self) -> None:
        """Files are never moved/deleted, so a second CI run over an
        unchanged outbox directory must behave the same way as the first —
        this is the whole idempotency argument for leaving outbox files in
        place."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        # First run: row still has the old text -- succeeds (1 row matched).
        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}]),
        ):
            first = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(first["items_succeeded"], 1)
        # File is still there, byte-identical, ready to be reprocessed.
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "batch1.json")))

        # Second run: live finding_text_ko already matches (already applied)
        # -- PostgREST's eq filter on finding_text still matches (finding_text
        # itself is untouched by the PATCH), so this models the steady state
        # where the row simply gets re-PATCHed to the same value (still 1
        # row matched, still harmless).
        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}]),
        ) as patch:
            second = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 1)
        self.assertEqual(second["items_succeeded"], 1)
        self.assertEqual(second["items_errored"], 0)

    def test_processing_the_same_file_twice_after_source_row_removed(self) -> None:
        """Models the other steady state: the underlying finding_text no
        longer matches live data (e.g. row content changed upstream) -- the
        PATCH filter then matches zero rows on every subsequent run, which is
        reported as matched_zero, not an error, forever (until the outbox
        file eventually rolls off, which this module deliberately does not
        manage)."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, []),
        ):
            first = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)
            second = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(first["items_matched_zero"], 1)
        self.assertEqual(second["items_matched_zero"], 1)
        self.assertEqual(first["items_errored"], 0)
        self.assertEqual(second["items_errored"], 0)


class CredentialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_missing_credentials_cli_exits_2(self) -> None:
        with mock.patch("findings_translate_apply_service.requests.patch") as patch:
            with mock.patch.dict(os.environ):
                os.environ.pop("SUPABASE_URL", None)
                os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
                rc = svc.main(["--outbox-dir", self._tmp.name])
        self.assertEqual(rc, 2)
        patch.assert_not_called()

    def test_non_https_url_yields_error_in_report_not_exception(self) -> None:
        report = svc.apply_outbox(self._tmp.name, "http://example.supabase.co", _SERVICE_KEY)
        self.assertTrue(any("https://" in e for e in report["errors"]))

    def test_cli_missing_outbox_dir_exits_0(self) -> None:
        missing_dir = os.path.join(self._tmp.name, "does-not-exist")
        out = os.path.join(self._tmp.name, "report.json")
        with mock.patch("findings_translate_apply_service.requests.patch") as patch:
            rc = svc.main(
                [
                    "--outbox-dir", missing_dir,
                    "--supabase-url", _BASE_URL,
                    "--service-role-key", _SERVICE_KEY,
                    "--output", out,
                ]
            )
        self.assertEqual(rc, 0)
        patch.assert_not_called()
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["files_scanned"], 0)

    def test_cli_items_errored_still_exits_0(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        out = os.path.join(self._tmp.name, "report.json")

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(500),
        ):
            rc = svc.main(
                [
                    "--outbox-dir", self._tmp.name,
                    "--supabase-url", _BASE_URL,
                    "--service-role-key", _SERVICE_KEY,
                    "--output", out,
                ]
            )

        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["items_errored"], 1)

    def test_cli_env_fallback_credentials(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        out = os.path.join(self._tmp.name, "report.json")

        with mock.patch(
            "findings_translate_apply_service.requests.patch",
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}]),
        ) as patch:
            with mock.patch.dict(
                os.environ,
                {"SUPABASE_URL": _BASE_URL, "SUPABASE_SERVICE_ROLE_KEY": _SERVICE_KEY},
            ):
                rc = svc.main(["--outbox-dir", self._tmp.name, "--output", out])

        self.assertEqual(rc, 0)
        self.assertEqual(patch.call_args.kwargs["headers"]["apikey"], _SERVICE_KEY)


if __name__ == "__main__":
    unittest.main()
