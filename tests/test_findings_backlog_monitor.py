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
        # run_monitor 는 RPC 3개를 순서대로 부른다: findings_stats → extraction_gap_by_source
        # → findings_country_unmapped(배열 반환). 마지막은 배열이어야 하므로 return_value 로
        # 한 응답을 돌려쓸 수 없다.
        with mock.patch.object(
            mon.requests, "post", side_effect=[
                _FakePostResponse(200, payload),
                _FakePostResponse(200, _gap([])),
                _FakePostResponse(200, []),
            ]
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


class ExtractionGapMigrationContractTest(unittest.TestCase):
    """`extraction_gap_by_source()` 의 **비대칭 설계**를 고정한다 — 거르는 단위는
    (source, kind), 보고 단위는 source.

    되돌리면 둘 중 하나가 반드시 깨진다:
      · source 단위로 거르면 → 식약처가 영구 적색(가이던스 17건은 0건이 정상인데 섞인다).
      · kind 단위로 보고하면 → Warning Letter 가 감시에서 사라진다(kind 가 발행부서명이라
        49종으로 쪼개져 전부 소량 억제 임계에 걸린다).

    ★검사 대상은 **함수를 정의하는 가장 최신 마이그레이션**을 자동으로 찾는다. 파일명을
      손으로 적어 두면 다음 `create or replace` 때 테스트가 낡은 파일을 붙들고 초록으로
      남는다 — 이 저장소가 이미 두 번 당한 표류(CI shim 손열거)와 같은 모양이다.
    """

    def _sql(self) -> str:
        import os
        import re as _re
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "web", "migrations")
        hits = []
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".sql"):
                continue
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                body = fh.read()
            if _re.search(r"create\s+or\s+replace\s+function\s+public\.extraction_gap_by_source",
                          body, _re.I):
                hits.append((fn, body))
        self.assertTrue(hits, "extraction_gap_by_source 를 정의하는 마이그레이션이 없다")
        return hits[-1][1]        # 번호가 가장 큰 것 = 프로덕션에 마지막으로 적용된 정본

    def test_producing_set_is_scoped_by_source_and_kind(self):
        sql = self._sql()
        self.assertIn("group by source, kind having bool_or(has_finding)", sql,
                      "대상 판정이 (source, kind) 단위여야 한다")
        self.assertIn("p.source = d.source and p.kind = d.kind", sql,
                      "조인이 kind 까지 맞춰야 가이던스 문서가 제외된다")

    def test_reporting_is_grouped_by_source_only(self):
        self.assertIn("group by s.source", self._sql(),
                      "보고는 source 단위여야 WL 49개 kind 가 한 덩어리로 남는다")

    def test_source_declared_zero_is_excluded_from_the_population(self):
        """★048. 원문이 '지적사항 없음'이라고 **명시한** 문서는 추출 실패가 아니다 —
        감시 모집단에서 분자·분모 양쪽에서 빠져야 한다. 안 빼면 식약처가 영구 적색이 되고,
        무시되는 알림은 진짜 신호를 가린다(식약처 12건 전수 확인 결과 누락 지적사항 0건).
        판별은 소스별 키를 열거하지 않고 `…assessment = 'none'` 관례로 유도한다."""
        sql = self._sql()
        self.assertIn("kv.key like '%assessment' and kv.value = 'none'", sql)
        self.assertIn("where not d.source_says_none", sql,
                      "제외가 모집단(scoped) 단계에서 일어나야 분모에서도 빠진다")

    def test_excluded_count_is_reported_not_hidden(self):
        """조용한 축소 금지 — 몇 건을 뺐는지 리포트에 함께 실어야 한다."""
        self.assertIn("'source_says_none'", self._sql())

    def test_contract_returns_counts_only(self):
        """원문 무반환 계약(007/041/042 동종) — 반환 키에 텍스트 필드가 없어야 한다."""
        sql = self._sql()
        for forbidden in ("finding_text", "evidence_url", "raw_json'", "excerpt'"):
            self.assertNotIn(forbidden, sql.split("jsonb_build_object", 1)[1])


class RunMonitorExtractionGapTest(unittest.TestCase):
    def _posts(self, stats_payload, gap_payload, unmapped_payload=None):
        return [_FakePostResponse(200, stats_payload),
                _FakePostResponse(200, gap_payload),
                _FakePostResponse(200, unmapped_payload if unmapped_payload is not None else [])]

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
        self.assertTrue(urls[2].endswith("/rpc/findings_country_unmapped"), urls)


def _unmapped(rows):
    """findings_country_unmapped RPC 반환 형태 — `[{site_country, findings}]` 배열."""
    return [{"site_country": name, "findings": cnt} for name, cnt in rows]


class EvaluateCountryUnmappedTest(unittest.TestCase):
    """★2026-08-11 국가 축(055)의 짝. 정규화 사전은 반드시 낡는다 — 새 표기가 들어오면
    country_key='' 로 조용히 떨어져 그 나라가 국가 축에서 통째로 빠진다. 추출 격차와 달리
    **비율이 아니라 존재 자체가 신호**다."""

    def test_no_unmapped_is_green(self):
        breaches, summary = mon.evaluate_country_unmapped([], min_rows=1)
        self.assertEqual(breaches, [])
        self.assertEqual(summary, [])

    def test_single_new_variant_breaches_at_one_row(self):
        breaches, summary = mon.evaluate_country_unmapped(
            _unmapped([("Korea, Republic of", 1)]), min_rows=1)
        self.assertEqual([b["code"] for b in breaches], ["country-unmapped"])
        self.assertEqual(breaches[0]["site_country"], "Korea, Republic of")
        self.assertEqual(breaches[0]["value"], 1)
        self.assertEqual(summary[0]["findings"], 1)

    def test_breaches_are_per_variant_never_summed(self):
        """변종마다 수리(매핑 추가)가 따로다 — 합산하면 무엇을 고칠지 알 수 없다."""
        breaches, _ = mon.evaluate_country_unmapped(
            _unmapped([("Viet Nam", 4), ("Korea, Republic of", 2)]), min_rows=1)
        self.assertEqual(len(breaches), 2)
        self.assertEqual([b["site_country"] for b in breaches],
                         ["Viet Nam", "Korea, Republic of"])  # 건수 내림차순

    def test_threshold_can_suppress_low_volume_noise(self):
        breaches, summary = mon.evaluate_country_unmapped(
            _unmapped([("XX-garbage", 1)]), min_rows=5)
        self.assertEqual(breaches, [])
        self.assertEqual(len(summary), 1, "임계 미달이어도 요약에는 남아야 한다")

    def test_mass_event_is_capped_but_never_silently(self):
        """★조용한 절단 금지 — 잘라낸 개수를 마지막 메시지에 적는다."""
        rows = _unmapped([(f"Country {i:02d}", 100 - i) for i in range(25)])
        breaches, summary = mon.evaluate_country_unmapped(rows, min_rows=1)
        self.assertEqual(len(breaches), mon._MAX_COUNTRY_UNMAPPED_BREACHES)
        self.assertEqual(len(summary), 25)
        self.assertIn("15종이 더 있습니다", breaches[-1]["message"])

    def test_malformed_payload_is_tolerated(self):
        breaches, summary = mon.evaluate_country_unmapped(
            [None, "x", {}, {"site_country": "", "findings": 9},
             {"site_country": "Norge", "findings": 3}], min_rows=1)
        self.assertEqual([b["site_country"] for b in breaches], ["Norge"])
        self.assertEqual(len(summary), 1, "빈 site_country 는 요약에서도 제외")

    def test_message_names_both_implementations_to_fix(self):
        """SQL 함수와 Python 사전 **양쪽**을 고쳐야 파리티가 유지된다 — 메시지가 그걸 말해야
        수리하는 사람이 한쪽만 고치고 끝내지 않는다."""
        breaches, _ = mon.evaluate_country_unmapped(_unmapped([("Norge", 3)]), min_rows=1)
        msg = breaches[0]["message"]
        self.assertIn("grm_normalize_country", msg)
        self.assertIn("_COUNTRY_CODE_MAP", msg)


class RunMonitorCountryUnmappedTest(unittest.TestCase):
    def _posts(self, unmapped_payload):
        return [_FakePostResponse(200, _stats(100, 100)),
                _FakePostResponse(200, _gap([])),
                _FakePostResponse(200, unmapped_payload)]

    def test_unmapped_variant_turns_report_red(self):
        with mock.patch.object(mon.requests, "post", side_effect=self._posts(
                _unmapped([("Korea, Republic of", 12)]))):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "failure")
        self.assertEqual([b["code"] for b in report["breaches"]], ["country-unmapped"])
        self.assertEqual(report["country_unmapped"][0]["site_country"], "Korea, Republic of")

    def test_clean_mapping_keeps_report_green(self):
        with mock.patch.object(mon.requests, "post", side_effect=self._posts([])):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["country_unmapped"], [])

    def test_country_rpc_failure_is_never_silent(self):
        """★감시가 꺼진 줄 모르고 초록을 믿지 않는다 — 세 번째 RPC 가 죽어도 red.

        5xx 는 _MAX_ATTEMPTS 만큼 재시도하므로 응답을 그만큼 준비한다(재시도 계약도 함께 고정).
        """
        with mock.patch.object(mon.requests, "post", side_effect=[
                _FakePostResponse(200, _stats(100, 100)),
                _FakePostResponse(200, _gap([])),
                *([_FakePostResponse(500, None)] * mon._MAX_ATTEMPTS)]) as posted:
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "error")
        self.assertIn("findings_country_unmapped", repr(report["errors"]))
        self.assertNotIn(_SERVICE_KEY, repr(report))
        self.assertEqual(posted.call_count, 2 + mon._MAX_ATTEMPTS)

    def test_country_rpc_hard_error_does_not_retry(self):
        """4xx 는 재시도 대상이 아니다 — 한 번 만에 red."""
        with mock.patch.object(mon.requests, "post", side_effect=[
                _FakePostResponse(200, _stats(100, 100)),
                _FakePostResponse(200, _gap([])),
                _FakePostResponse(404, None)]) as posted:
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "error")
        self.assertIn("findings_country_unmapped", repr(report["errors"]))
        self.assertEqual(posted.call_count, 3)

    def test_non_array_payload_is_error(self):
        """RPC 는 배열을 준다. dict 가 오면 계약 위반이므로 조용히 0건 처리하지 않는다."""
        with mock.patch.object(mon.requests, "post", side_effect=self._posts({"oops": 1})):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["status"], "error")
        self.assertIn("non-array", repr(report["errors"]))

    def test_threshold_is_reported_for_traceability(self):
        with mock.patch.object(mon.requests, "post", side_effect=self._posts([])):
            report = mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        self.assertEqual(report["thresholds"]["country_unmapped_min_rows"],
                         mon.DEFAULT_COUNTRY_UNMAPPED_MIN_ROWS)


class CountryUnmappedWiringTest(unittest.TestCase):
    """★이 RPC 는 055 에서 만들어졌지만 2026-08-11 검증 전까지 **호출자가 0건**이었다
    (만들어만 두고 아무도 안 보는 슬롯). 그 상태로 되돌아가지 않도록 배선을 고정한다."""

    def test_monitor_declares_the_rpc(self):
        self.assertEqual(mon._COUNTRY_UNMAPPED_RPC, "findings_country_unmapped")

    def test_run_monitor_actually_calls_it(self):
        with mock.patch.object(mon.requests, "post", side_effect=[
                _FakePostResponse(200, _stats(1, 1)),
                _FakePostResponse(200, _gap([])),
                _FakePostResponse(200, [])]) as posted:
            mon.run_monitor(_BASE_URL, _SERVICE_KEY)
        urls = [c.args[0] if c.args else c.kwargs["url"] for c in posted.call_args_list]
        self.assertEqual(len(urls), 3, "RPC 3개를 모두 불러야 한다")
        self.assertTrue(any(u.endswith("/rpc/findings_country_unmapped") for u in urls), urls)


if __name__ == "__main__":
    unittest.main()
