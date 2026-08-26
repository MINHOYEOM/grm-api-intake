#!/usr/bin/env python3
"""grm-finding-taxonomy/v3 unattended CI reclassification service tests.

All HTTP is mocked -- no real network access, no real Supabase project. GET
pagination is mocked via `findings_supabase_backfill.requests.get` (the
service reuses that module's Range-header pagination helper); PATCH is mocked
via `findings_reclassify_service.requests.patch`. These tests exercise the
pure orchestration/transport contract: fetch -> plan -> (dry-run report or)
PATCH, idempotency (0 changes -> 0 PATCH calls), category migration matrix,
race-safe filtering, retry-on-5xx, and service-key secrecy.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import findings_reclassify_service as svc
import grm_findings as gf


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"


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
        self._payload = [] if payload is None else payload

    def json(self):
        return self._payload


def _row(finding_id: str, text: str, category_code: str, taxonomy_version: str = "grm-finding-taxonomy/v2") -> dict:
    return {
        "finding_id": finding_id,
        "finding_text": text,
        "category_code": category_code,
        "category_label_ko": gf.CATEGORY_BY_CODE.get(
            category_code, gf.CATEGORY_BY_CODE["other_quality_system"]
        ).label_ko,
        "taxonomy_version": taxonomy_version,
    }


def _mock_get_all(rows: list[dict]):
    """Return a mock.patch context for findings_supabase_backfill.requests.get
    that serves `rows` as a single page (Content-Range reports the exact total)."""
    resp = _FakeGetResponse(200, rows, headers={"Content-Range": f"0-{max(len(rows) - 1, 0)}/{len(rows)}"})
    return mock.patch("findings_supabase_backfill.requests.get", return_value=resp)


class PlanReclassificationTest(unittest.TestCase):
    def test_correctly_classified_row_is_excluded_from_plan(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION)]
        plan = svc.plan_reclassification(rows)
        self.assertEqual(plan, [])

    def test_stale_category_is_included_with_old_and_new(self) -> None:
        # v2-era row: "written procedure"-triggered documentation_records, now
        # reclassifies to process_validation under v3 (same 211.100(a) pattern
        # as audit case 3df6f81c).
        text = (
            "Your firm failed to establish adequate written procedures for "
            "production and process controls."
        )
        rows = [_row("f-1", text, "documentation_records", "grm-finding-taxonomy/v2")]
        plan = svc.plan_reclassification(rows)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["finding_id"], "f-1")
        self.assertEqual(plan[0]["old_category"], "documentation_records")
        self.assertEqual(plan[0]["new_category"], "process_validation")
        self.assertEqual(plan[0]["old_taxonomy_version"], "grm-finding-taxonomy/v2")

    def test_correct_category_but_stale_taxonomy_version_is_a_version_only_stamp(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "stability_storage", "grm-finding-taxonomy/v1")]
        plan = svc.plan_reclassification(rows)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["old_category"], plan[0]["new_category"])

    def test_already_current_version_and_category_yields_empty_plan(self) -> None:
        rows = [
            _row("f-1", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION),
            _row("f-2", "Written procedures are not followed for the sampling.", "other_quality_system", gf.TAXONOMY_VERSION),
        ]
        self.assertEqual(svc.plan_reclassification(rows), [])


class CategoryMigrationMatrixTest(unittest.TestCase):
    def test_matrix_counts_only_actual_category_changes(self) -> None:
        plan = [
            {"finding_id": "a", "old_category": "documentation_records", "new_category": "process_validation", "old_taxonomy_version": "grm-finding-taxonomy/v2"},
            {"finding_id": "b", "old_category": "documentation_records", "new_category": "process_validation", "old_taxonomy_version": "grm-finding-taxonomy/v2"},
            {"finding_id": "c", "old_category": "stability_storage", "new_category": "stability_storage", "old_taxonomy_version": "grm-finding-taxonomy/v1"},
        ]
        matrix = svc._category_migration_matrix(plan)
        self.assertEqual(matrix, {"documentation_records->process_validation": 2})


class RunReclassifyDryRunTest(unittest.TestCase):
    def test_dry_run_reports_plan_but_issues_no_patch(self) -> None:
        text = (
            "Your firm failed to establish adequate written procedures for "
            "production and process controls."
        )
        rows = [
            _row("f-1", text, "documentation_records", "grm-finding-taxonomy/v2"),
            _row("f-2", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION),
        ]
        with _mock_get_all(rows):
            with mock.patch("findings_reclassify_service.requests.patch") as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=True)

        patch.assert_not_called()
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["rows_scanned"], 2)
        self.assertEqual(report["changes_planned"], 1)
        self.assertEqual(report["category_changes"], 1)
        self.assertEqual(report["version_only_stamps"], 0)
        self.assertEqual(
            report["category_migration_matrix"],
            {"documentation_records->process_validation": 1},
        )
        self.assertEqual(report["patched"], 0)
        self.assertEqual(report["errors"], [])

    def test_dry_run_on_fully_current_table_plans_zero_changes(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION)]
        with _mock_get_all(rows):
            with mock.patch("findings_reclassify_service.requests.patch") as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=True)

        patch.assert_not_called()
        self.assertEqual(report["changes_planned"], 0)
        self.assertEqual(report["category_migration_matrix"], {})


class RunReclassifyApplyTest(unittest.TestCase):
    def test_single_change_patched_with_race_safe_filter(self) -> None:
        text = "Approp1iate controls are not exercised over computer or related system."
        rows = [_row("f-1", text, "other_quality_system", "grm-finding-taxonomy/v2")]

        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(200, [{"finding_id": "f-1"}]),
            ) as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(patch.call_count, 1)
        _, kwargs = patch.call_args
        self.assertEqual(kwargs["params"]["finding_id"], "eq.f-1")
        self.assertEqual(kwargs["params"]["category_code"], "eq.other_quality_system")
        self.assertEqual(kwargs["json"]["category_code"], "computer_system_validation")
        self.assertEqual(
            kwargs["json"]["category_label_ko"],
            gf.CATEGORY_BY_CODE["computer_system_validation"].label_ko,
        )
        self.assertEqual(kwargs["json"]["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual(kwargs["headers"]["apikey"], _SERVICE_KEY)
        self.assertNotIn("finding_text", kwargs["json"])

        self.assertEqual(report["mode"], "apply")
        self.assertEqual(report["patched"], 1)
        self.assertEqual(report["matched_zero"], 0)
        self.assertEqual(report["errors"], [])

    def test_second_run_over_already_patched_table_issues_zero_patches(self) -> None:
        """Idempotency: rerunning against a table that's already been
        reclassified plans (and PATCHes) nothing."""
        text = "Approp1iate controls are not exercised over computer or related system."
        # Simulate the *post*-reclassification state directly (already current).
        rows = [_row("f-1", text, "computer_system_validation", gf.TAXONOMY_VERSION)]

        with _mock_get_all(rows):
            with mock.patch("findings_reclassify_service.requests.patch") as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        patch.assert_not_called()
        self.assertEqual(report["patched"], 0)
        self.assertEqual(report["changes_planned"], 0)

    def test_matched_zero_is_not_an_error(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(200, []),
            ):
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(report["matched_zero"], 1)
        self.assertEqual(report["patched"], 0)
        self.assertEqual(report["errors"], [])

    def test_multiple_rows_matched_is_an_error(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(200, [{"finding_id": "f-1"}, {"finding_id": "f-1"}]),
            ):
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(report["patched"], 0)
        self.assertTrue(any("matched 2 rows" in e for e in report["errors"]))

    def test_http_error_counted_and_does_not_stop_other_rows(self) -> None:
        rows = [
            _row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1"),
            _row("f-2", "Complaints records are deficient in that they do not include the investigation.", "deviation_capa", "grm-finding-taxonomy/v2"),
        ]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                side_effect=[_FakePatchResponse(403), _FakePatchResponse(200, [{"finding_id": "f-2"}])],
            ) as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["patched"], 1)
        self.assertTrue(any("http_403" in e for e in report["errors"]))

    def test_5xx_retries_once_then_succeeds(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                side_effect=[_FakePatchResponse(503), _FakePatchResponse(200, [{"finding_id": "f-1"}])],
            ) as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["patched"], 1)
        self.assertEqual(report["errors"], [])

    def test_limit_caps_number_of_patches_issued(self) -> None:
        rows = [
            _row(f"f-{i}", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")
            for i in range(5)
        ]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(200, [{"finding_id": "any"}]),
            ) as patch:
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False, limit=2)

        self.assertEqual(patch.call_count, 2)
        self.assertEqual(report["patched"], 2)


class ScopeSafetyTest(unittest.TestCase):
    """finding_text/finding_text_ko/scope_status must never appear in a PATCH body."""

    def test_patch_body_never_includes_finding_text_fields(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(200, [{"finding_id": "f-1"}]),
            ) as patch:
                svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        body_keys = set(patch.call_args.kwargs["json"].keys())
        self.assertEqual(body_keys, {"category_code", "category_label_ko", "taxonomy_version"})


class ServiceKeySecrecyTest(unittest.TestCase):
    def test_service_key_never_appears_in_report_on_error(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")]
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(500),
            ):
                report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=False)

        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)

    def test_fetch_error_does_not_leak_key(self) -> None:
        import requests as _requests

        with mock.patch(
            "findings_supabase_backfill.requests.get",
            side_effect=_requests.exceptions.ConnectionError(f"key={_SERVICE_KEY} leaked"),
        ):
            report = svc.run_reclassify(_BASE_URL, _SERVICE_KEY, dry_run=True)

        report_text = json.dumps(report)
        self.assertNotIn(_SERVICE_KEY, report_text)
        self.assertTrue(any("GET findings failed" in e for e in report["errors"]))


class StdoutReportTest(unittest.TestCase):
    """CI step summaries can't be queried via `gh` CLI, so the report JSON
    must also land in the run's stdout log -- even when --output writes it
    to a file too. See _write_report's docstring for the contract."""

    def test_report_is_also_printed_to_stdout_when_output_file_given(self) -> None:
        import contextlib
        import io
        import tempfile

        rows = [_row("f-1", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION)]
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "report.json")
            buf = io.StringIO()
            with _mock_get_all(rows):
                with contextlib.redirect_stdout(buf):
                    rc = svc.main([
                        "--dry-run",
                        "--supabase-url", _BASE_URL,
                        "--service-role-key", _SERVICE_KEY,
                        "--output", out,
                    ])
            self.assertEqual(rc, 0)

            printed = json.loads(buf.getvalue())
            self.assertEqual(printed["mode"], "dry_run")

            with open(out, encoding="utf-8") as f:
                file_report = json.load(f)
            self.assertEqual(printed, file_report)

    def test_service_key_never_appears_in_stdout_report(self) -> None:
        import contextlib
        import io

        rows = [_row("f-1", "The stability program is deficient.", "other_quality_system", "grm-finding-taxonomy/v1")]
        buf = io.StringIO()
        with _mock_get_all(rows):
            with mock.patch(
                "findings_reclassify_service.requests.patch",
                return_value=_FakePatchResponse(500),
            ):
                with contextlib.redirect_stdout(buf):
                    rc = svc.main([
                        "--supabase-url", _BASE_URL,
                        "--service-role-key", _SERVICE_KEY,
                    ])

        self.assertEqual(rc, 1)
        self.assertNotIn(_SERVICE_KEY, buf.getvalue())


class CredentialsTest(unittest.TestCase):
    def test_non_https_url_yields_error_in_report_not_exception(self) -> None:
        report = svc.run_reclassify("http://example.supabase.co", _SERVICE_KEY, dry_run=True)
        self.assertTrue(any("https://" in e for e in report["errors"]))

    def test_cli_missing_credentials_exits_2(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            rc = svc.main(["--dry-run"])
        self.assertEqual(rc, 2)

    def test_cli_dry_run_writes_report_and_exits_zero(self) -> None:
        import tempfile

        rows = [_row("f-1", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION)]
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "report.json")
            with _mock_get_all(rows):
                rc = svc.main([
                    "--dry-run",
                    "--supabase-url", _BASE_URL,
                    "--service-role-key", _SERVICE_KEY,
                    "--output", out,
                ])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual(report["mode"], "dry_run")
            self.assertEqual(report["changes_planned"], 0)

    def test_cli_env_fallback_credentials(self) -> None:
        rows = [_row("f-1", "The stability program is deficient.", "stability_storage", gf.TAXONOMY_VERSION)]
        with mock.patch.dict(os.environ, {"SUPABASE_URL": _BASE_URL, "SUPABASE_SERVICE_ROLE_KEY": _SERVICE_KEY}):
            with _mock_get_all(rows):
                rc = svc.main(["--dry-run"])
        self.assertEqual(rc, 0)


class CategoryDriftExitCodeTest(unittest.TestCase):
    """[2026-08-27] 표류 감시용 종료코드.

    ★왜 필요했나(실제로 일어난 일): 이 서비스는 workflow_dispatch 전용·크론이 없다.
    2026-08-26 실측에서 마지막 실행이 08-02 였고 그 사이 233행의 category_code 가 현행
    분류기와 어긋나 있었다 — 103행은 더 나은 답이 이미 계산돼 있는데도 캐치올에 갇혀
    있었다. **분류기를 고쳐도 적용이 안 되면 사용자 화면은 그대로다.**

    ★임계를 changes_planned 가 아니라 category_changes 에 거는 이유: 전자는 taxonomy
    버전 스탬프만 바뀌는 행까지 세므로, 버전을 올린 직후에는 그게 전건이라 알람이 영원히
    켜져 있게 된다. 원인이 다른 사건을 한 카운터에 합치면 진단은 반드시 틀린다."""

    def _main(self, rows, argv):
        with _mock_get_all(rows):
            with mock.patch("findings_reclassify_service.requests.patch") as patch:
                with mock.patch.dict(os.environ, {
                    "SUPABASE_URL": _BASE_URL,
                    "SUPABASE_SERVICE_ROLE_KEY": _SERVICE_KEY,
                }, clear=False):
                    rc = svc.main(argv)
        patch.assert_not_called()
        return rc

    _STALE = "Your firm failed to establish adequate written procedures for production and process controls."
    _CURRENT = "The stability program is deficient."

    def test_drift_above_threshold_exits_1(self) -> None:
        rows = [_row("f-1", self._STALE, "documentation_records", gf.TAXONOMY_VERSION)]
        self.assertEqual(self._main(rows, ["--dry-run", "--fail-on-category-drift", "0"]), 1)

    def test_no_drift_exits_0(self) -> None:
        rows = [_row("f-1", self._CURRENT, "stability_storage", gf.TAXONOMY_VERSION)]
        self.assertEqual(self._main(rows, ["--dry-run", "--fail-on-category-drift", "0"]), 0)

    def test_version_only_stamps_do_not_trip_the_alarm(self) -> None:
        """[음성 검사] 버전만 낡은 행은 표류가 아니다 — 카테고리는 이미 맞다.

        여기서 changes_planned 는 1이지만 category_changes 는 0이다. 이 구분이 없으면
        버전을 올린 직후 전건이 알람을 켜서 감시가 쓸모없어진다."""
        rows = [_row("f-1", self._CURRENT, "stability_storage", "grm-finding-taxonomy/v2")]
        self.assertEqual(self._main(rows, ["--dry-run", "--fail-on-category-drift", "0"]), 0)

    def test_flag_is_off_by_default_so_existing_dispatch_keeps_exit_0(self) -> None:
        """[음성 검사] 기본이 off 여야 기존 운용(dry_run dispatch)의 종료코드가 안 바뀐다."""
        rows = [_row("f-1", self._STALE, "documentation_records", gf.TAXONOMY_VERSION)]
        self.assertEqual(self._main(rows, ["--dry-run"]), 0)

    def test_missing_credentials_exit_2_not_a_silent_clean_bill(self) -> None:
        """못 재고 조용히 0을 돌려주면 감시가 자기 알람을 끈다 — 자격증명 부재는 2다."""
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""},
                             clear=False):
            self.assertEqual(svc.main(["--dry-run", "--fail-on-category-drift", "0"]), 2)


if __name__ == "__main__":
    unittest.main()
