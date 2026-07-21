#!/usr/bin/env python3
"""FIND-1 findings 백로그 모니터 테스트.

evaluate_backlog(순수 함수)는 실 페이로드 없이 직접 검증하고, run_monitor 의 HTTP 는
findings_backlog_monitor.requests.post 를 목킹한다(실 네트워크·실 Supabase 없음). 검증
대상: 격차/needs_review 산출, 임계 경계(초과=breach, 동값=통과), RPC 오류 시 status=error,
그리고 service-role 키가 report/에러 문자열 어디에도 새지 않음.
"""

from __future__ import annotations

import unittest
from unittest import mock

import findings_backlog_monitor as mon


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"


def _stats(findings: int, public_findings: int, needs_review: int = 0, rejected: int = 0):
    by_review = [{"review_status": "accepted", "cnt": max(0, findings - needs_review - rejected)}]
    if needs_review:
        by_review.append({"review_status": "needs_review", "cnt": needs_review})
    if rejected:
        by_review.append({"review_status": "rejected", "cnt": rejected})
    return {
        "totals": {"findings": findings, "public_findings": public_findings},
        "by_review_status": by_review,
    }


class _FakePostResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class EvaluateBacklogTest(unittest.TestCase):
    def test_within_thresholds_is_ok(self):
        report = mon.evaluate_backlog(
            _stats(11548, 11400, needs_review=50),
            gap_threshold=300,
            needs_review_threshold=300,
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["untranslated_gap"], 148)
        self.assertEqual(report["needs_review"], 50)
        self.assertEqual(report["breaches"], [])

    def test_gap_over_threshold_breaches(self):
        report = mon.evaluate_backlog(
            _stats(11548, 9187, needs_review=181, rejected=52),
            gap_threshold=300,
            needs_review_threshold=300,
        )
        self.assertEqual(report["status"], "failure")
        self.assertEqual(report["untranslated_gap"], 2361)
        codes = {b["code"] for b in report["breaches"]}
        self.assertIn("untranslated-gap-high", codes)
        self.assertEqual(report["rejected"], 52)

    def test_needs_review_over_threshold_breaches(self):
        report = mon.evaluate_backlog(
            _stats(5000, 5000, needs_review=400),
            gap_threshold=300,
            needs_review_threshold=300,
        )
        self.assertEqual(report["status"], "failure")
        codes = {b["code"] for b in report["breaches"]}
        self.assertEqual(codes, {"needs-review-backlog-high"})

    def test_threshold_is_strict_greater_than(self):
        # value == threshold is NOT a breach (정상 하루치 경계 포함).
        report = mon.evaluate_backlog(
            _stats(1300, 1000, needs_review=300),
            gap_threshold=300,
            needs_review_threshold=300,
        )
        self.assertEqual(report["untranslated_gap"], 300)
        self.assertEqual(report["needs_review"], 300)
        self.assertEqual(report["status"], "ok")

    def test_gap_never_negative(self):
        report = mon.evaluate_backlog(
            _stats(100, 120), gap_threshold=300, needs_review_threshold=300,
        )
        self.assertEqual(report["untranslated_gap"], 0)

    def test_missing_review_status_array_is_zero(self):
        report = mon.evaluate_backlog(
            {"totals": {"findings": 10, "public_findings": 10}},
            gap_threshold=300,
            needs_review_threshold=300,
        )
        self.assertEqual(report["needs_review"], 0)
        self.assertEqual(report["status"], "ok")


class RunMonitorTest(unittest.TestCase):
    def test_happy_path_reads_stats_and_reports(self):
        payload = _stats(11548, 9187, needs_review=181, rejected=52)
        with mock.patch.object(
            mon.requests, "post", return_value=_FakePostResponse(200, payload)
        ) as posted:
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "failure")
        self.assertEqual(report["untranslated_gap"], 2361)
        self.assertEqual(report["needs_review"], 181)
        self.assertEqual(report["errors"], [])
        # RPC endpoint + service key header shape.
        _args, kwargs = posted.call_args
        self.assertEqual(kwargs["json"], {})
        self.assertEqual(kwargs["headers"]["apikey"], _SERVICE_KEY)

    def test_bad_base_url_is_error(self):
        report = mon.run_monitor("http://insecure.example", _SERVICE_KEY)
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["errors"])

    def test_http_error_surfaces_status_not_key(self):
        with mock.patch.object(
            mon.requests, "post", return_value=_FakePostResponse(401, None)
        ):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["errors"])
        blob = repr(report)
        self.assertNotIn(_SERVICE_KEY, blob)
        self.assertIn("http_401", blob)

    def test_timeout_retries_then_errors(self):
        import requests as _rq
        with mock.patch.object(
            mon.requests, "post", side_effect=_rq.exceptions.Timeout()
        ) as posted:
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(posted.call_count, mon._MAX_ATTEMPTS)
        self.assertEqual(report["status"], "error")
        self.assertIn("timeout", repr(report["errors"]))

    def test_non_object_payload_is_error(self):
        with mock.patch.object(
            mon.requests, "post", return_value=_FakePostResponse(200, [1, 2, 3])
        ):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "error")


if __name__ == "__main__":
    unittest.main()
