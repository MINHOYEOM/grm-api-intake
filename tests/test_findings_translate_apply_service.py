#!/usr/bin/env python3
"""FIND-1 M9b unattended CI apply service tests.

All HTTP is mocked via `findings_translate_apply_service.requests.get` and
`...requests.patch` -- no real network access, no real Supabase project.
These tests only exercise the pure orchestration/transport contract: outbox
file discovery/parsing, the read-before-write request shape (GET the live
finding_text by primary key, compare it in-process, PATCH by primary key),
matched-0/1/N accounting, retry-on-5xx, service-key secrecy, dry-run (no HTTP
calls), missing outbox dir, missing credentials, and idempotent re-processing
of the same file.

Regression note (2026-07-23): finding_text is NEVER placed in a request URL
(it can be 30k chars and blew past the Supabase edge's ~32 KB URL limit ->
HTTP 400 -> red run + untranslated findings). test_single_item_success and
test_no_request_url_carries_finding_text pin that contract.
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


def _live_text_for(finding_id_param: str) -> str:
    """Reconstruct the outbox finding_text for an "eq.f-00N" finding_id param,
    so a fake GET can return a row that byte-matches the outbox item."""
    i = int(finding_id_param.rsplit("-", 1)[1])
    return f"Observation number {i} was not documented."


def _fake_get_matching(url, params=None, json=None, headers=None, timeout=None):  # noqa: A002
    """Default fake GET: the live row exists and still matches the outbox text,
    so the subsequent PATCH proceeds."""
    return _FakeResponse(200, [{"finding_text": _live_text_for(params["finding_id"])}])


def _patch_get(**kwargs):
    return mock.patch("findings_translate_apply_service.requests.get", **kwargs)


def _patch_patch(**kwargs):
    return mock.patch("findings_translate_apply_service.requests.patch", **kwargs)


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
        with _patch_get() as get, _patch_patch() as patch:
            report = svc.apply_outbox(missing_dir, _BASE_URL, _SERVICE_KEY)
        get.assert_not_called()
        patch.assert_not_called()
        self.assertEqual(report["files_scanned"], 0)
        self.assertEqual(report["items_total"], 0)
        self.assertEqual(report["items_errored"], 0)

    def test_files_processed_newest_first(self) -> None:
        # Date-prefixed names sort lexicographically by date; the newest batch
        # must be applied first so it beats the CI timeout even if stale
        # already-applied batches accumulate (2026-07-13 starvation incident).
        _write_outbox_file(self._tmp.name, "2026-07-14-batch.json", [_outbox_item(1)])
        _write_outbox_file(self._tmp.name, "2026-07-15-batch.json", [_outbox_item(2)])

        patched_ids: list[str] = []

        def _fake_patch(url, params=None, json=None, headers=None, timeout=None):  # noqa: A002
            patched_ids.append(params["finding_id"])
            return _FakeResponse(200, [{"finding_id": params["finding_id"]}])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(side_effect=_fake_patch):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["files_scanned"], 2)
        self.assertEqual(patched_ids, ["eq.f-002", "eq.f-001"])  # 07-15 before 07-14


class ApplySuccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_single_item_success(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(side_effect=_fake_get_matching) as get, _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}])
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        # 1) The live row is fetched by primary key with a minimal projection.
        self.assertEqual(get.call_count, 1)
        _, gkwargs = get.call_args
        self.assertEqual(gkwargs["params"]["finding_id"], "eq.f-001")
        self.assertEqual(gkwargs["params"]["select"], "finding_text")
        self.assertNotIn("finding_text", gkwargs["params"])  # never a URL predicate
        self.assertEqual(gkwargs["headers"]["apikey"], _SERVICE_KEY)
        self.assertEqual(gkwargs["headers"]["Authorization"], f"Bearer {_SERVICE_KEY}")

        # 2) The write is keyed on primary key only -- finding_text is compared
        #    in-process, never sent in the PATCH URL.
        self.assertEqual(patch.call_count, 1)
        _, kwargs = patch.call_args
        self.assertEqual(kwargs["params"], {"finding_id": "eq.f-001"})
        self.assertNotIn("finding_text", kwargs["params"])
        self.assertEqual(kwargs["json"]["finding_text_ko"], "관찰사항 1 국문 번역.")
        self.assertEqual(kwargs["json"]["translation_method"], "llm_assisted")
        self.assertEqual(kwargs["headers"]["apikey"], _SERVICE_KEY)
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {_SERVICE_KEY}")
        self.assertEqual(kwargs["headers"]["Prefer"], "return=representation")
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

    def test_no_request_url_carries_finding_text(self) -> None:
        """Regression guard for the 2026-07-23 URL-length failure: even a
        30k-char finding_text must never appear in any GET or PATCH `params`
        (it would blow past the ~32 KB edge URL limit -> HTTP 400)."""
        huge = "x" * 30000
        item = {
            "finding_id": "f-huge",
            "finding_text": huge,
            "finding_text_ko": "국문",
            "translation_method": "llm_assisted",
        }
        _write_outbox_file(self._tmp.name, "batch1.json", [item])

        def _fake_get(url, params=None, json=None, headers=None, timeout=None):  # noqa: A002
            return _FakeResponse(200, [{"finding_text": huge}])

        with _patch_get(side_effect=_fake_get) as get, _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "f-huge"}])
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        for call in list(get.call_args_list) + list(patch.call_args_list):
            params_blob = json.dumps(call.kwargs.get("params", {}))
            self.assertNotIn(huge, params_blob)
            self.assertLess(len(params_blob), 200)  # only short finding_id/select keys
        self.assertEqual(report["items_succeeded"], 1)
        self.assertEqual(report["items_errored"], 0)

    def test_multiple_files_multiple_items(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1), _outbox_item(2)])
        _write_outbox_file(self._tmp.name, "batch2.json", [_outbox_item(3)])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "any"}])
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

    def test_absent_row_is_matched_zero_and_no_patch(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(return_value=_FakeResponse(200, [])) as get, _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        get.assert_called_once()
        patch.assert_not_called()  # nothing to write -- no live row
        self.assertEqual(report["items_matched_zero"], 1)
        self.assertEqual(report["items_succeeded"], 0)
        self.assertEqual(report["items_errored"], 0)
        self.assertEqual(report["errors"], [])

    def test_source_text_changed_is_matched_zero_and_no_patch(self) -> None:
        """The live row exists but its finding_text no longer byte-matches the
        outbox item (source changed since the batch was built) -- a TOCTOU-safe
        no-op, counted as matched_zero, with no PATCH issued."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(
            return_value=_FakeResponse(200, [{"finding_text": "totally different live text"}])
        ), _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        patch.assert_not_called()
        self.assertEqual(report["items_matched_zero"], 1)
        self.assertEqual(report["items_errored"], 0)

    def test_patch_matches_zero_after_toctou_delete_is_matched_zero(self) -> None:
        """The GET saw a matching row, but between GET and PATCH the row was
        removed/changed so the PATCH updates zero rows -- still a no-op, not an
        error."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(200, [])
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["items_matched_zero"], 1)
        self.assertEqual(report["items_errored"], 0)

    def test_multiple_live_rows_matched_is_an_error(self) -> None:
        """finding_id is the primary key so this cannot happen in production,
        but the anomaly guard must still flag >1 matching live rows rather than
        over-write."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        text = "Observation number 1 was not documented."

        with _patch_get(
            return_value=_FakeResponse(200, [{"finding_text": text}, {"finding_text": text}])
        ), _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        patch.assert_not_called()  # never write when the key is ambiguous
        self.assertEqual(report["items_errored"], 1)
        self.assertEqual(report["items_succeeded"], 0)
        self.assertTrue(any("matched 2" in e for e in report["errors"]))


class HttpErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_get_http_error_counted_and_file_left_in_place(self) -> None:
        path = _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        before = Path(path).read_bytes()

        with _patch_get(return_value=_FakeResponse(403)), _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        patch.assert_not_called()  # a failed GET short-circuits before any write
        self.assertEqual(report["items_errored"], 1)
        self.assertEqual(report["items_succeeded"], 0)
        self.assertTrue(any("GET failed (http_403)" in e for e in report["errors"]))
        self.assertEqual(Path(path).read_bytes(), before)

    def test_patch_http_error_counted_and_file_left_in_place(self) -> None:
        path = _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        before = Path(path).read_bytes()

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(403)
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(report["items_errored"], 1)
        self.assertTrue(any("PATCH failed (http_403)" in e for e in report["errors"]))
        self.assertEqual(Path(path).read_bytes(), before)

    def test_get_5xx_retries_once_then_succeeds(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(
            side_effect=[_FakeResponse(503), _FakeResponse(200, [{"finding_text": _live_text_for("eq.f-001")}])]
        ) as get, _patch_patch(return_value=_FakeResponse(200, [{"finding_id": "f-001"}])):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(report["items_succeeded"], 1)
        self.assertEqual(report["items_errored"], 0)

    def test_patch_5xx_retries_once_then_succeeds(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            side_effect=[_FakeResponse(503), _FakeResponse(200, [{"finding_id": "f-001"}])]
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["items_succeeded"], 1)
        self.assertEqual(report["items_errored"], 0)

    def test_patch_5xx_exhausted_is_an_error(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            side_effect=[_FakeResponse(503), _FakeResponse(503)]
        ) as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["items_errored"], 1)
        self.assertTrue(any("http_503" in e for e in report["errors"]))

    def test_invalid_json_file_counted_as_error_and_left_in_place(self) -> None:
        path = os.path.join(self._tmp.name, "broken.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        with _patch_get() as get, _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        get.assert_not_called()
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

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(500)
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)

    def test_service_key_never_appears_in_exception_path(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        import requests as _requests

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            side_effect=_requests.exceptions.ConnectionError(f"key={_SERVICE_KEY} leaked")
        ):
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)
        self.assertTrue(any("ConnectionError" in e for e in report["errors"]))

    def test_service_key_never_leaks_from_get_exception(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        import requests as _requests

        with _patch_get(
            side_effect=_requests.exceptions.ConnectionError(f"key={_SERVICE_KEY} leaked")
        ), _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        patch.assert_not_called()
        self.assertNotIn(_SERVICE_KEY, json.dumps(report))
        self.assertTrue(any("GET failed (ConnectionError)" in e for e in report["errors"]))


class DryRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_dry_run_reads_files_but_issues_no_http_calls(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1), _outbox_item(2)])

        with _patch_get() as get, _patch_patch() as patch:
            report = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY, dry_run=True)

        get.assert_not_called()
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
        """Files are never moved/deleted, so a second CI run over an unchanged
        outbox directory must behave the same way as the first -- this is the
        whole idempotency argument for leaving outbox files in place. The live
        finding_text is untouched by the PATCH (only finding_text_ko changes),
        so the GET still matches and the row is simply re-PATCHed to the same
        value on the second run."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}])
        ):
            first = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(first["items_succeeded"], 1)
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "batch1.json")))

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}])
        ) as patch:
            second = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        self.assertEqual(patch.call_count, 1)
        self.assertEqual(second["items_succeeded"], 1)
        self.assertEqual(second["items_errored"], 0)

    def test_processing_the_same_file_twice_after_source_row_removed(self) -> None:
        """Models the other steady state: the underlying finding_text no longer
        matches live data (row content changed upstream) -- the in-process
        comparison then fails on every subsequent run, reported as matched_zero,
        not an error, forever (until the outbox file eventually rolls off, which
        this module deliberately does not manage)."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(return_value=_FakeResponse(200, [])), _patch_patch() as patch:
            first = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)
            second = svc.apply_outbox(self._tmp.name, _BASE_URL, _SERVICE_KEY)

        patch.assert_not_called()
        self.assertEqual(first["items_matched_zero"], 1)
        self.assertEqual(second["items_matched_zero"], 1)
        self.assertEqual(first["items_errored"], 0)
        self.assertEqual(second["items_errored"], 0)


class CredentialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_missing_credentials_cli_exits_2(self) -> None:
        with _patch_get() as get, _patch_patch() as patch:
            with mock.patch.dict(os.environ):
                os.environ.pop("SUPABASE_URL", None)
                os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
                rc = svc.main(["--outbox-dir", self._tmp.name])
        self.assertEqual(rc, 2)
        get.assert_not_called()
        patch.assert_not_called()

    def test_non_https_url_yields_error_in_report_not_exception(self) -> None:
        report = svc.apply_outbox(self._tmp.name, "http://example.supabase.co", _SERVICE_KEY)
        self.assertTrue(any("https://" in e for e in report["errors"]))

    def test_cli_missing_outbox_dir_exits_0(self) -> None:
        missing_dir = os.path.join(self._tmp.name, "does-not-exist")
        out = os.path.join(self._tmp.name, "report.json")
        with _patch_get() as get, _patch_patch() as patch:
            rc = svc.main(
                [
                    "--outbox-dir", missing_dir,
                    "--supabase-url", _BASE_URL,
                    "--service-role-key", _SERVICE_KEY,
                    "--output", out,
                ]
            )
        self.assertEqual(rc, 0)
        get.assert_not_called()
        patch.assert_not_called()
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["files_scanned"], 0)

    def test_cli_items_errored_exits_1(self) -> None:
        """FIND-1 M13b: items_errored>0 must surface as a red (exit 1) CI run --
        retry semantics are unchanged (the outbox file is left in place either
        way), but a silently-green run on partial failure is no longer
        acceptable."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        out = os.path.join(self._tmp.name, "report.json")

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(500)
        ):
            rc = svc.main(
                [
                    "--outbox-dir", self._tmp.name,
                    "--supabase-url", _BASE_URL,
                    "--service-role-key", _SERVICE_KEY,
                    "--output", out,
                ]
            )

        self.assertEqual(rc, 1)
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["items_errored"], 1)

    def test_cli_items_succeeded_exits_0(self) -> None:
        """items_errored == 0 (all applies succeeded) must still exit 0."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        out = os.path.join(self._tmp.name, "report.json")

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}])
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
        self.assertEqual(report["items_errored"], 0)

    def test_cli_empty_outbox_exits_0_not_an_error(self) -> None:
        """An outbox directory with zero items (nothing queued this week) is a
        normal steady state, not an error -- it must keep exiting 0 even though
        the M13b policy change gates on report["errors"]."""
        empty_dir = os.path.join(self._tmp.name, "empty-outbox")
        os.makedirs(empty_dir, exist_ok=True)
        out = os.path.join(self._tmp.name, "report.json")

        with _patch_get() as get, _patch_patch() as patch:
            rc = svc.main(
                [
                    "--outbox-dir", empty_dir,
                    "--supabase-url", _BASE_URL,
                    "--service-role-key", _SERVICE_KEY,
                    "--output", out,
                ]
            )

        self.assertEqual(rc, 0)
        get.assert_not_called()
        patch.assert_not_called()
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["items_total"], 0)
        self.assertEqual(report["errors"], [])

    def test_cli_items_errored_report_printed_to_stdout_on_exit_1(self) -> None:
        """report JSON must still be printed to stdout (no --output given) on
        the exit-1 (red) path, so CI logs / step summaries can capture it even
        though the run is failing."""
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(500)
        ):
            with mock.patch("builtins.print") as mock_print:
                rc = svc.main(
                    [
                        "--outbox-dir", self._tmp.name,
                        "--supabase-url", _BASE_URL,
                        "--service-role-key", _SERVICE_KEY,
                    ]
                )

        self.assertEqual(rc, 1)
        printed_text = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        printed_report = json.loads(printed_text)
        self.assertEqual(printed_report["items_errored"], 1)

    def test_cli_env_fallback_credentials(self) -> None:
        _write_outbox_file(self._tmp.name, "batch1.json", [_outbox_item(1)])
        out = os.path.join(self._tmp.name, "report.json")

        with _patch_get(side_effect=_fake_get_matching), _patch_patch(
            return_value=_FakeResponse(200, [{"finding_id": "f-001"}])
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
