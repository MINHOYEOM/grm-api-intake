#!/usr/bin/env python3
"""FDA 483 실사관 이름(inspector_names) 소급 백필 테스트.

모든 HTTP 는 목킹한다 — 실 네트워크·실 Supabase·collect_fda_483 없음. GET 페이지네이션은
findings_supabase_backfill.requests.get 로(backfill_483_inspectors 가 그 모듈의
_fetch_all_pages 를 그대로 재사용하므로), PATCH 는 backfill_483_inspectors.requests.patch
로 목킹한다. PDF fetch/파싱은 backfill_483_inspectors._fetch_text /
_extract_inspectors(지연 import 간접층)를 직접 패치해 collect_fda_483._extract_483_inspectors
가 아직 없어도(다른 세션이 병행 작업 중) 이 테스트가 net-free 로 돈다.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import backfill_483_inspectors as bi


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _raw_row(rid: str, doc_id: str, pdf_url: str | None = "https://www.fda.gov/media/1/download") -> dict:
    raw_json = {} if pdf_url is None else {"pdf_url": pdf_url}
    return {
        "raw_signal_id": rid,
        "document_id": doc_id,
        "raw_json": json.dumps(raw_json, ensure_ascii=False),
    }


def _finding_row(rid: str, inspector_names) -> dict:
    return {"raw_signal_id": rid, "inspector_names": inspector_names}


class _FakeGetResponse:
    def __init__(self, status_code: int, payload=None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakePatchResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _full_page(rows: list[dict]) -> _FakeGetResponse:
    total = len(rows)
    return _FakeGetResponse(200, rows, headers={"Content-Range": f"0-{max(total - 1, 0)}/{total}"})


def _mock_gets(raw_rows: list[dict], finding_rows: list[dict]):
    """run() fetches raw_signals then findings, in that order (see run()'s body) — two
    sequential GET calls, each satisfied in a single page."""
    return mock.patch(
        "findings_supabase_backfill.requests.get",
        side_effect=[_full_page(raw_rows), _full_page(finding_rows)],
    )


def _noop_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class ParseDocIdsTest(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(bi._parse_doc_ids(""), [])

    def test_comma_separated(self):
        self.assertEqual(bi._parse_doc_ids("a,b,c"), ["a", "b", "c"])

    def test_whitespace_separated(self):
        self.assertEqual(bi._parse_doc_ids(" a  b\tc "), ["a", "b", "c"])

    def test_mixed_comma_and_whitespace(self):
        self.assertEqual(bi._parse_doc_ids("a, b ,c"), ["a", "b", "c"])


class JsonObjectHelperTest(unittest.TestCase):
    def test_parses_json_text(self):
        self.assertEqual(bi._json_object('{"pdf_url": "x"}'), {"pdf_url": "x"})

    def test_passes_through_dict(self):
        self.assertEqual(bi._json_object({"pdf_url": "x"}), {"pdf_url": "x"})

    def test_invalid_json_is_empty_dict(self):
        self.assertEqual(bi._json_object("not json"), {})

    def test_non_object_json_is_empty_dict(self):
        self.assertEqual(bi._json_object("[1, 2]"), {})

    def test_none_is_empty_dict(self):
        self.assertEqual(bi._json_object(None), {})


class AsListHelperTest(unittest.TestCase):
    def test_native_list_passthrough(self):
        self.assertEqual(bi._as_list(["Jane Doe"]), ["Jane Doe"])

    def test_json_string_list_parsed(self):
        self.assertEqual(bi._as_list('["Jane Doe"]'), ["Jane Doe"])

    def test_empty_list_is_falsy_for_filled_check(self):
        self.assertEqual(bi._as_list([]), [])

    def test_non_list_json_string_is_empty(self):
        self.assertEqual(bi._as_list('{"a": 1}'), [])

    def test_none_is_empty(self):
        self.assertEqual(bi._as_list(None), [])


class BroadFailureThresholdTest(unittest.TestCase):
    def test_zero_attempted_is_not_broad(self):
        report = bi.InspectorBackfillReport(attempted=0, failed=0)
        self.assertFalse(bi._is_broad_failure(report))

    def test_small_batch_all_failed_is_broad(self):
        report = bi.InspectorBackfillReport(attempted=2, failed=2)
        self.assertTrue(bi._is_broad_failure(report))

    def test_small_batch_partial_failure_is_not_broad(self):
        report = bi.InspectorBackfillReport(attempted=2, failed=1)
        self.assertFalse(bi._is_broad_failure(report))

    def test_large_batch_majority_failure_is_broad(self):
        report = bi.InspectorBackfillReport(attempted=10, failed=5)
        self.assertTrue(bi._is_broad_failure(report))

    def test_large_batch_minority_failure_is_not_broad(self):
        report = bi.InspectorBackfillReport(attempted=10, failed=4)
        self.assertFalse(bi._is_broad_failure(report))


# ---------------------------------------------------------------------------
# run() — dry-run
# ---------------------------------------------------------------------------


class RunDryRunTest(unittest.TestCase):
    def test_dry_run_never_patches_but_fetches_and_parses(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("body text", "pdf-ok")) as fetch_text, \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]) as extract, \
             mock.patch.object(bi.requests, "patch") as patched:
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        patched.assert_not_called()
        fetch_text.assert_called_once_with("https://www.fda.gov/media/1/download")
        extract.assert_called_once_with("body text")
        self.assertEqual(report.mode, "dry_run")
        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.attempted, 1)
        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.findings_rows_updated, 0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(report.samples), 1)
        self.assertEqual(report.samples[0]["inspector_names"], ["Jane Doe"])


# ---------------------------------------------------------------------------
# already_filled / --force
# ---------------------------------------------------------------------------


class AlreadyFilledTest(unittest.TestCase):
    def test_already_filled_document_is_skipped_by_default(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", ["Jane Doe"])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text") as fetch_text:
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        fetch_text.assert_not_called()
        self.assertEqual(report.already_filled, 1)
        self.assertEqual(report.candidates, 0)
        self.assertEqual(report.attempted, 0)
        self.assertEqual(exit_code, 0)

    def test_force_reprocesses_already_filled_document(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", ["Jane Doe"])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe", "John Roe"]):
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True, force=True,
                sleeper=_noop_sleep,
            )

        self.assertEqual(report.already_filled, 0)
        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.attempted, 1)
        self.assertEqual(report.succeeded, 1)
        self.assertEqual(exit_code, 0)


# ---------------------------------------------------------------------------
# zero-extraction never overwrites with []
# ---------------------------------------------------------------------------


class ZeroExtractedTest(unittest.TestCase):
    def test_zero_names_extracted_does_not_patch(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=[]), \
             mock.patch.object(bi.requests, "patch") as patched:
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        patched.assert_not_called()
        self.assertEqual(report.zero_extracted, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.findings_rows_updated, 0)
        self.assertEqual(exit_code, 0)

    def test_blank_and_non_string_names_are_filtered_as_zero(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["  ", None, 42]), \
             mock.patch.object(bi.requests, "patch") as patched:
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        patched.assert_not_called()
        self.assertEqual(report.zero_extracted, 1)


# ---------------------------------------------------------------------------
# missing pdf_url
# ---------------------------------------------------------------------------


class MissingPdfUrlTest(unittest.TestCase):
    def test_missing_pdf_url_counts_as_failure_without_fetch(self):
        raw = [_raw_row("rid-1", "fda483-1", pdf_url=None)]
        findings = [_finding_row("rid-1", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text") as fetch_text:
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        fetch_text.assert_not_called()
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.failure_reasons.get("missing_pdf_url"), 1)
        self.assertTrue(any("fda483-1" in e for e in report.errors))
        # a lone attempted document that fails entirely is a broad failure by design.
        self.assertEqual(exit_code, 1)


# ---------------------------------------------------------------------------
# per-document failures do not stop the batch
# ---------------------------------------------------------------------------


class PerDocumentFailureContinuesTest(unittest.TestCase):
    def test_one_failure_does_not_block_the_other_document(self):
        # sorted by raw_signal_id ascending inside run(): "rid-a" before "rid-b".
        raw = [_raw_row("rid-a", "fda483-a"), _raw_row("rid-b", "fda483-b")]
        findings = [_finding_row("rid-a", []), _finding_row("rid-b", [])]

        with _mock_gets(raw, findings), \
             mock.patch(
                 "backfill_483_inspectors._fetch_text",
                 side_effect=[RuntimeError("boom"), ("text", "pdf-ok")],
             ), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]):
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        self.assertEqual(report.attempted, 2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.succeeded, 1)
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(any(r.startswith("fetch_raised:") for r in report.failure_reasons))
        self.assertEqual(exit_code, 0)  # 2 attempted, only 1 failed -> not broad (50% threshold not met... )

    def test_empty_extracted_text_is_a_counted_failure_not_a_crash(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("", "scan-no-text")), \
             mock.patch("backfill_483_inspectors._extract_inspectors") as extract:
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        extract.assert_not_called()
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.failure_reasons.get("scan-no-text"), 1)


# ---------------------------------------------------------------------------
# --doc-ids filter
# ---------------------------------------------------------------------------


class DocIdsFilterTest(unittest.TestCase):
    def test_doc_ids_restricts_candidates_by_document_id(self):
        raw = [_raw_row("rid-1", "fda483-1"), _raw_row("rid-2", "fda483-2")]
        findings = [_finding_row("rid-1", []), _finding_row("rid-2", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                doc_ids=["fda483-2"], sleeper=_noop_sleep,
            )

        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.attempted, 1)

    def test_doc_ids_matches_raw_signal_id_too(self):
        raw = [_raw_row("rid-1", "fda483-1"), _raw_row("rid-2", "fda483-2")]
        findings = [_finding_row("rid-1", []), _finding_row("rid-2", [])]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                doc_ids=["rid-1"], sleeper=_noop_sleep,
            )

        self.assertEqual(report.candidates, 1)


# ---------------------------------------------------------------------------
# --limit
# ---------------------------------------------------------------------------


class LimitTest(unittest.TestCase):
    def test_limit_caps_candidates(self):
        raw = [_raw_row(f"rid-{i}", f"fda483-{i}") for i in range(3)]
        findings = [_finding_row(f"rid-{i}", []) for i in range(3)]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                limit=1, sleeper=_noop_sleep,
            )

        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.attempted, 1)
        self.assertEqual(report.limit, 1)

    def test_limit_zero_means_all_not_nothing(self):
        """★[2026-07-30 실사고 회귀] `limit=0` 은 **전건**이다.

        종전 구현은 0 을 "0건 처리"로 받아 apply 모드에서 아무것도 안 하고 **성공으로
        끝났다**(1,546 문서 대상 실행이 candidates=0·exit 0). 형제 워크플로
        `grm-fda483-ocr-backfill.yml` 이 `0=전건` 규약이라 호출자가 0 을 그대로 넘긴 것이
        원인. 침묵 무동작은 이 저장소가 가장 경계하는 실패 유형이므로 규약을 통일한다.
        """
        raw = [_raw_row(f"rid-{i}", f"fda483-{i}") for i in range(3)]
        findings = [_finding_row(f"rid-{i}", []) for i in range(3)]

        for value in (0, -1):
            with self.subTest(limit=value):
                with _mock_gets(raw, findings), \
                     mock.patch("backfill_483_inspectors._fetch_text",
                                return_value=("text", "pdf-ok")), \
                     mock.patch("backfill_483_inspectors._extract_inspectors",
                                return_value=["Jane Doe"]):
                    report, _exit_code = bi.run(
                        base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                        limit=value, sleeper=_noop_sleep,
                    )
                self.assertEqual(report.candidates, 3)
                self.assertEqual(report.attempted, 3)


# ---------------------------------------------------------------------------
# raw_signals with no findings row at all are out of scope
# ---------------------------------------------------------------------------


class NoFindingsDocumentTest(unittest.TestCase):
    def test_raw_signal_without_any_findings_row_is_excluded(self):
        raw = [_raw_row("rid-1", "fda483-1"), _raw_row("rid-2", "fda483-2")]
        findings = [_finding_row("rid-1", [])]  # rid-2 never got findings extracted

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        self.assertEqual(report.raw_signals_scanned, 2)
        self.assertEqual(report.documents_with_findings, 1)
        self.assertEqual(report.candidates, 1)


# ---------------------------------------------------------------------------
# apply mode: real PATCH
# ---------------------------------------------------------------------------


class ApplyPatchTest(unittest.TestCase):
    def test_apply_patches_with_correct_params_and_body(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]
        patch_resp = _FakePatchResponse(200, [{"finding_id": "f1"}, {"finding_id": "f2"}])

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]), \
             mock.patch.object(bi.requests, "patch", return_value=patch_resp) as patched:
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        patched.assert_called_once()
        _args, kwargs = patched.call_args
        self.assertEqual(kwargs["params"]["raw_signal_id"], "eq.rid-1")
        self.assertEqual(kwargs["json"], {"inspector_names": ["Jane Doe"]})
        self.assertEqual(kwargs["headers"]["apikey"], _SERVICE_KEY)

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.findings_rows_updated, 2)
        self.assertEqual(exit_code, 0)

    def test_patch_matched_zero_is_race_safe_noop(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]
        patch_resp = _FakePatchResponse(200, [])  # race: already filled between select and patch

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]), \
             mock.patch.object(bi.requests, "patch", return_value=patch_resp):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        self.assertEqual(report.patch_matched_zero, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.errors, [])

    def test_patch_http_error_is_counted_and_key_not_leaked(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]
        patch_resp = _FakePatchResponse(403, None)

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]), \
             mock.patch.object(bi.requests, "patch", return_value=patch_resp):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        self.assertEqual(report.failed, 1)
        blob = json.dumps(report.__dict__)
        self.assertIn("http_403", blob)
        self.assertNotIn(_SERVICE_KEY, blob)


# ---------------------------------------------------------------------------
# idempotency: re-running against post-patch state does 0 further writes
# ---------------------------------------------------------------------------


class IdempotencyTest(unittest.TestCase):
    def test_second_run_against_already_filled_state_patches_nothing(self):
        raw = [_raw_row("rid-1", "fda483-1")]

        # First run: not yet filled -> succeeds and would patch.
        findings_before = [_finding_row("rid-1", [])]
        patch_resp = _FakePatchResponse(200, [{"finding_id": "f1"}])
        with _mock_gets(raw, findings_before), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]), \
             mock.patch.object(bi.requests, "patch", return_value=patch_resp):
            report1, _exit1 = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )
        self.assertEqual(report1.succeeded, 1)
        self.assertEqual(report1.findings_rows_updated, 1)

        # Second run: DB now reflects the successful patch (inspector_names filled).
        findings_after = [_finding_row("rid-1", ["Jane Doe"])]
        with _mock_gets(raw, findings_after), \
             mock.patch("backfill_483_inspectors._fetch_text") as fetch_text, \
             mock.patch.object(bi.requests, "patch") as patched:
            report2, exit2 = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        fetch_text.assert_not_called()
        patched.assert_not_called()
        self.assertEqual(report2.already_filled, 1)
        self.assertEqual(report2.candidates, 0)
        self.assertEqual(report2.findings_rows_updated, 0)
        self.assertEqual(exit2, 0)


# ---------------------------------------------------------------------------
# credential/key secrecy
# ---------------------------------------------------------------------------


class ServiceKeySecrecyTest(unittest.TestCase):
    def test_https_guard_error_never_contains_key(self):
        report, exit_code = bi.run(
            base_url="http://insecure.example.com", service_key=_SERVICE_KEY, dry_run=True,
            sleeper=_noop_sleep,
        )
        self.assertEqual(exit_code, 2)
        for err in report.errors:
            self.assertNotIn(_SERVICE_KEY, err)

    def test_fetch_error_surfaces_without_key_in_report(self):
        import requests as _requests

        def _boom(*_args, **_kwargs):
            raise _requests.exceptions.RequestException(f"connection reset apikey={_SERVICE_KEY}")

        with mock.patch("findings_supabase_backfill.requests.get", side_effect=_boom):
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        self.assertEqual(exit_code, 2)
        self.assertTrue(report.errors)
        for err in report.errors:
            self.assertNotIn(_SERVICE_KEY, err)

    def test_full_report_json_never_contains_key(self):
        raw = [_raw_row("rid-1", "fda483-1")]
        findings = [_finding_row("rid-1", [])]
        patch_resp = _FakePatchResponse(200, [{"finding_id": "f1"}])

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]), \
             mock.patch.object(bi.requests, "patch", return_value=patch_resp):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=False,
                sleeper=_noop_sleep,
            )

        from dataclasses import asdict
        self.assertNotIn(_SERVICE_KEY, json.dumps(asdict(report)))


# ---------------------------------------------------------------------------
# report schema stability
# ---------------------------------------------------------------------------


class ReportSchemaStabilityTest(unittest.TestCase):
    _EXPECTED_FIELDS = {
        "schema_version", "mode", "force", "limit", "delay_seconds",
        "raw_signals_scanned", "documents_with_findings", "already_filled",
        "candidates", "attempted", "succeeded", "zero_extracted", "failed",
        "patch_matched_zero", "findings_rows_updated", "failure_reasons",
        "errors", "samples",
    }

    def test_empty_tables_produce_stable_schema(self):
        with _mock_gets([], []):
            report, exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        from dataclasses import asdict
        payload = asdict(report)
        self.assertEqual(set(payload.keys()), self._EXPECTED_FIELDS)
        # Must be JSON-serializable end to end (workflow prints/writes this verbatim).
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        self.assertEqual(exit_code, 0)

    def test_samples_capped_at_twenty(self):
        n = 30
        raw = [_raw_row(f"rid-{i:02d}", f"fda483-{i:02d}") for i in range(n)]
        findings = [_finding_row(f"rid-{i:02d}", []) for i in range(n)]

        with _mock_gets(raw, findings), \
             mock.patch("backfill_483_inspectors._fetch_text", return_value=("text", "pdf-ok")), \
             mock.patch("backfill_483_inspectors._extract_inspectors", return_value=["Jane Doe"]):
            report, _exit_code = bi.run(
                base_url=_BASE_URL, service_key=_SERVICE_KEY, dry_run=True,
                sleeper=_noop_sleep,
            )

        self.assertEqual(report.attempted, n)
        self.assertEqual(len(report.samples), 20)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class CliTest(unittest.TestCase):
    def test_main_exits_2_when_credentials_missing(self):
        env = {k: v for k, v in os.environ.items() if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            exit_code = bi.main([])
        self.assertEqual(exit_code, 2)

    def test_default_mode_is_dry_run(self):
        args = bi.build_arg_parser().parse_args([])
        self.assertFalse(args.apply)
        self.assertFalse(args.force)
        self.assertIsNone(args.limit)
        self.assertEqual(args.delay_seconds, bi._DEFAULT_DELAY_SECONDS)

    def test_apply_flag_parses(self):
        args = bi.build_arg_parser().parse_args(["--apply", "--force", "--limit", "5"])
        self.assertTrue(args.apply)
        self.assertTrue(args.force)
        self.assertEqual(args.limit, 5)

    def test_main_dry_run_reads_credentials_from_env_and_writes_report(self):
        env = dict(os.environ)
        env["SUPABASE_URL"] = _BASE_URL
        env["SUPABASE_SERVICE_ROLE_KEY"] = _SERVICE_KEY
        with mock.patch.dict(os.environ, env, clear=True), _mock_gets([], []):
            exit_code = bi.main([])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
