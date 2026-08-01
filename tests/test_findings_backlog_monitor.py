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


def _gap(rows):
    return {"generated_at": "2026-08-01T15:46:22Z",
            "totals": {"sources": len(rows), "docs": sum(r["docs"] for r in rows),
                       "zero_findings": sum(r["zero_findings"] for r in rows)},
            "by_source": rows}


def _src(source, docs, zero, stored=0):
    return {"source": source, "docs": docs, "with_findings": docs - zero,
            "zero_findings": zero, "zero_with_stored_text": stored,
            "zero_pct": round(100.0 * zero / docs, 1)}


class EvaluateExtractionGapTest(unittest.TestCase):
    """★2026-08-01 RCA 산물. 추출 실패는 예외를 던지지 않으므로 '산출물이 0인 입력'을
    세는 것만이 유일한 탐지 수단이다. 이 감시가 없어 FDA 483 이 444건까지 조용히 쌓였다."""

    def test_healthy_source_produces_no_breach(self):
        breaches, summary = mon.evaluate_extraction_gap(
            _gap([_src("FDA Warning Letter", 1299, 0)]),
            pct_threshold=5.0, min_docs=10)
        self.assertEqual(breaches, [])
        self.assertEqual(summary[0]["zero_findings"], 0)

    def test_real_incident_shape_breaches(self):
        """실제 사고 당시 값 — 두 소스 모두 잡혀야 한다(483 은 비율이 낮고 건수가 크며,
        식약처는 그 반대라 어느 한 축만 보면 반드시 하나를 놓친다)."""
        breaches, _ = mon.evaluate_extraction_gap(
            _gap([_src("FDA 483", 2000, 124, stored=56), _src("MFDS", 113, 29, stored=12)]),
            pct_threshold=5.0, min_docs=10)
        self.assertEqual({b["source"] for b in breaches}, {"FDA 483", "MFDS"})
        self.assertTrue(all(b["code"] == "extraction-gap-high" for b in breaches))

    def test_breaches_are_per_source_never_summed(self):
        """합산은 이 사고의 원인이었던 바로 그 실수다 — 소스마다 별개 breach 여야 한다."""
        breaches, _ = mon.evaluate_extraction_gap(
            _gap([_src("A", 500, 60), _src("B", 400, 50)]), pct_threshold=5.0, min_docs=10)
        self.assertEqual(len(breaches), 2)

    def test_small_source_noise_is_suppressed(self):
        """6건 중 1건(16.7%)은 비율은 높지만 신호가 아니다 — 절대건수 임계가 막는다."""
        breaches, _ = mon.evaluate_extraction_gap(
            _gap([_src("MHRA GMP NCR", 6, 1)]), pct_threshold=5.0, min_docs=10)
        self.assertEqual(breaches, [])

    def test_large_source_with_low_ratio_is_suppressed(self):
        """2000건 중 20건(1%)은 정상 잔여다 — 비율 임계가 막는다."""
        breaches, _ = mon.evaluate_extraction_gap(
            _gap([_src("FDA 483", 2000, 20)]), pct_threshold=5.0, min_docs=10)
        self.assertEqual(breaches, [])

    def test_stored_text_steers_the_diagnosis(self):
        """★메시지가 진단 방향을 지목해야 한다 — 이 사고는 '수집이냐 파서냐'를 세 번
        틀렸다. 저장된 본문이 있으면 파서를, 없으면 수집을 먼저 보라고 말한다."""
        parser_side, _ = mon.evaluate_extraction_gap(
            _gap([_src("FDA 483", 2000, 124, stored=56)]), pct_threshold=5.0, min_docs=10)
        self.assertIn("추출 로직", parser_side[0]["message"])
        collect_side, _ = mon.evaluate_extraction_gap(
            _gap([_src("FDA 483", 2000, 124, stored=0)]), pct_threshold=5.0, min_docs=10)
        self.assertIn("수집 단계", collect_side[0]["message"])

    def test_malformed_payload_is_tolerated(self):
        for bad in ({}, {"by_source": None}, {"by_source": ["x", 3]}):
            breaches, summary = mon.evaluate_extraction_gap(
                bad, pct_threshold=5.0, min_docs=10)
            self.assertEqual((breaches, summary), ([], []))


class RunMonitorExtractionGapTest(unittest.TestCase):
    def _posts(self, stats_payload, gap_payload):
        return [_FakePostResponse(200, stats_payload), _FakePostResponse(200, gap_payload)]

    def test_extraction_breach_turns_report_red(self):
        with mock.patch.object(mon.requests, "post", side_effect=self._posts(
                _stats(100, 100), _gap([_src("FDA 483", 2000, 124, stored=56)]))):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "failure")
        self.assertEqual([b["code"] for b in report["breaches"]], ["extraction-gap-high"])
        self.assertEqual(report["extraction_gap"][0]["source"], "FDA 483")

    def test_clean_extraction_keeps_report_green(self):
        with mock.patch.object(mon.requests, "post", side_effect=self._posts(
                _stats(100, 100), _gap([_src("FDA Warning Letter", 1299, 0)]))):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["breaches"], [])

    def test_extraction_rpc_failure_is_never_silent(self):
        """★감시가 꺼진 줄 모르고 초록을 믿는 것이 이 저장소가 이미 두 번 당한 함정이다
        (CI shim 표류 2사례). 두 번째 RPC 가 죽으면 반드시 red."""
        with mock.patch.object(mon.requests, "post", side_effect=[
                _FakePostResponse(200, _stats(100, 100)), _FakePostResponse(404, None)]):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "error")
        self.assertIn("extraction_gap_by_source", repr(report["errors"]))
        self.assertNotIn(_SERVICE_KEY, repr(report))

    def test_second_rpc_targets_the_extraction_function(self):
        with mock.patch.object(mon.requests, "post", side_effect=self._posts(
                _stats(100, 100), _gap([]))) as posted:
            mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        urls = [c.args[0] if c.args else c.kwargs["url"] for c in posted.call_args_list]
        self.assertTrue(urls[0].endswith("/rpc/findings_stats"), urls)
        self.assertTrue(urls[1].endswith("/rpc/extraction_gap_by_source"), urls)


if __name__ == "__main__":
    unittest.main()
