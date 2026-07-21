#!/usr/bin/env python3
"""grm-finding-review-promote 검수 자동승격 서비스 테스트.

모든 HTTP 는 목킹한다 — 실 네트워크·실 Supabase 없음. GET 페이지네이션은
findings_supabase_backfill.requests.get 로, PATCH 는 findings_review_promote_service.
requests.patch 로 목킹한다. 순수 판정 계약(승격/유지/opt-in 반려)·멱등(0 계획→0 PATCH)·
경합 안전(matched_zero)·가드·키 비노출을 검증한다.
"""

from __future__ import annotations

import unittest
from unittest import mock

import findings_review_promote_service as svc


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"

# 조항 인용 + 위반 신호 + 충분한 길이 → 승격 대상.
_PROMOTABLE_TEXT = (
    "Your firm failed to establish adequate written procedures for cleaning and "
    "maintenance of equipment as required by the regulation."
)
_LABEL_TEXT = "Use thick amount of the cream on the treatment area."  # 신호 없음, 초단문


def _row(finding_id: str, text: str, cfr_refs, source: str = "FDA Warning Letter") -> dict:
    return {
        "finding_id": finding_id,
        "finding_text": text,
        "cfr_refs": cfr_refs,
        "source": source,
        "review_status": "needs_review",
    }


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


def _mock_get_all(rows: list[dict]):
    resp = _FakeGetResponse(
        200, rows, headers={"Content-Range": f"0-{max(len(rows) - 1, 0)}/{len(rows)}"}
    )
    return mock.patch("findings_supabase_backfill.requests.get", return_value=resp)


class ReviewVerdictTest(unittest.TestCase):
    def test_ref_and_signal_and_length_promotes(self):
        row = _row("f-1", _PROMOTABLE_TEXT, ["21 CFR 211.67"])
        self.assertEqual(svc.review_verdict(row, enable_reject=False), "accepted")

    def test_ref_without_signal_is_kept(self):
        # 조항만 있고 위반/조건 신호 없음(순수 조항 나열) → 유지(보수적).
        row = _row("f-2", "See 21 CFR 211.67 and 211.100 for the applicable sections here.", ["21 CFR 211.67"])
        self.assertIsNone(svc.review_verdict(row, enable_reject=False))

    def test_signal_without_ref_is_kept(self):
        # 신호만 있고 조항 없음(환경형 위반) → 유지(사람/LLM 검수 몫).
        row = _row("f-3", "Your aseptic areas had difficult to clean and visibly dirty surfaces present.", [])
        self.assertIsNone(svc.review_verdict(row, enable_reject=False))

    def test_short_promotable_text_is_kept(self):
        row = _row("f-4", "Failed. 21 CFR 211.", ["21 CFR 211.67"])  # < _PROMOTE_MIN_LEN
        self.assertIsNone(svc.review_verdict(row, enable_reject=False))

    def test_label_quote_kept_when_reject_disabled(self):
        row = _row("f-5", _LABEL_TEXT, [])
        self.assertIsNone(svc.review_verdict(row, enable_reject=False))

    def test_label_quote_rejected_when_reject_enabled(self):
        row = _row("f-5", _LABEL_TEXT, [])
        self.assertEqual(svc.review_verdict(row, enable_reject=True), "rejected")

    def test_cfr_refs_as_json_string_is_handled(self):
        row = _row("f-6", _PROMOTABLE_TEXT, '["21 CFR 211.67"]')
        self.assertEqual(svc.review_verdict(row, enable_reject=False), "accepted")


class PlanReviewTest(unittest.TestCase):
    def test_plan_selects_only_verdicts(self):
        rows = [
            _row("f-1", _PROMOTABLE_TEXT, ["21 CFR 211.67"]),          # promote
            _row("f-2", "See 21 CFR 211.67 for the sections here now.", ["21 CFR 211.67"]),  # keep
            _row("f-3", _LABEL_TEXT, []),                              # keep (reject off)
        ]
        plan = svc.plan_review(rows, enable_reject=False)
        self.assertEqual([p["finding_id"] for p in plan], ["f-1"])
        self.assertEqual(plan[0]["new_status"], "accepted")


class RunPromoteDryRunTest(unittest.TestCase):
    def test_dry_run_plans_but_issues_no_patch(self):
        rows = [
            _row("f-1", _PROMOTABLE_TEXT, ["21 CFR 211.67"]),
            _row("f-2", _LABEL_TEXT, []),
        ]
        with _mock_get_all(rows), mock.patch.object(svc.requests, "patch") as patched:
            report = svc.run_promote(_BASE_URL, _SERVICE_KEY, dry_run=True)
        patched.assert_not_called()
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["rows_scanned"], 2)
        self.assertEqual(report["promotions_planned"], 1)
        self.assertEqual(report["kept_needs_review"], 1)


class RunPromoteApplyTest(unittest.TestCase):
    def test_apply_patches_promotions_with_guard(self):
        rows = [_row("f-1", _PROMOTABLE_TEXT, ["21 CFR 211.67"])]
        patch_resp = _FakePatchResponse(200, [{"finding_id": "f-1"}])
        with _mock_get_all(rows), mock.patch.object(
            svc.requests, "patch", return_value=patch_resp
        ) as patched:
            report = svc.run_promote(_BASE_URL, _SERVICE_KEY, dry_run=False)
        self.assertEqual(report["patched"], 1)
        self.assertEqual(report["errors"], [])
        _args, kwargs = patched.call_args
        self.assertEqual(kwargs["params"]["review_status"], "eq.needs_review")  # 가드
        self.assertEqual(kwargs["json"], {"review_status": "accepted"})

    def test_idempotent_no_pending_issues_no_patch(self):
        # 모두 유지 대상 → 0건 PATCH(구성상 멱등).
        rows = [_row("f-2", "See 21 CFR 211.67 for the sections here now.", ["21 CFR 211.67"])]
        with _mock_get_all(rows), mock.patch.object(svc.requests, "patch") as patched:
            report = svc.run_promote(_BASE_URL, _SERVICE_KEY, dry_run=False)
        patched.assert_not_called()
        self.assertEqual(report["patched"], 0)
        self.assertEqual(report["promotions_planned"], 0)

    def test_matched_zero_is_race_safe_noop(self):
        rows = [_row("f-1", _PROMOTABLE_TEXT, ["21 CFR 211.67"])]
        patch_resp = _FakePatchResponse(200, [])  # 이미 승격됨 — 가드가 0행 매칭
        with _mock_get_all(rows), mock.patch.object(
            svc.requests, "patch", return_value=patch_resp
        ):
            report = svc.run_promote(_BASE_URL, _SERVICE_KEY, dry_run=False)
        self.assertEqual(report["matched_zero"], 1)
        self.assertEqual(report["patched"], 0)
        self.assertEqual(report["errors"], [])

    def test_http_error_surfaces_status_not_key(self):
        rows = [_row("f-1", _PROMOTABLE_TEXT, ["21 CFR 211.67"])]
        patch_resp = _FakePatchResponse(403, None)
        with _mock_get_all(rows), mock.patch.object(
            svc.requests, "patch", return_value=patch_resp
        ):
            report = svc.run_promote(_BASE_URL, _SERVICE_KEY, dry_run=False)
        blob = repr(report)
        self.assertIn("http_403", blob)
        self.assertNotIn(_SERVICE_KEY, blob)

    def test_bad_base_url_is_error(self):
        report = svc.run_promote("http://insecure", _SERVICE_KEY, dry_run=False)
        self.assertTrue(report["errors"])


class SourceFilterTest(unittest.TestCase):
    def test_source_filter_quotes_values(self):
        f = svc._source_filter()
        self.assertIn('"FDA Warning Letter"', f)
        self.assertIn('"FDA 483"', f)
        self.assertTrue(f.startswith("in.("))


class FetchFilterParamsTest(unittest.TestCase):
    def test_fetch_sends_scope_review_source_filters(self):
        resp = _FakeGetResponse(200, [], headers={"Content-Range": "*/0"})
        with mock.patch("findings_supabase_backfill.requests.get", return_value=resp) as g:
            svc.fetch_needs_review(_BASE_URL, _SERVICE_KEY)
        _args, kwargs = g.call_args
        params = kwargs["params"]
        self.assertEqual(params["scope_status"], "eq.ok")
        self.assertEqual(params["review_status"], "eq.needs_review")
        self.assertTrue(params["source"].startswith("in.("))
        self.assertEqual(params["select"], svc._SELECT_COLUMNS)


if __name__ == "__main__":
    unittest.main()
