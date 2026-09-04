"""용어사전 사례 건수 재측정기(glossary_cases_refresh.py) 테스트.

evaluate_item/build_report/run_refresh(순수 함수)는 실 네트워크 없이 직접 검증하고,
_post_findings_search 의 HTTP 는 glossary_cases_refresh.requests.post 를 목킹한다
(findings_backlog_monitor 테스트와 동형). 검증 대상: q/excluded 등 불변 필드 무변형 통과,
0건 유지, ±50% 변동 표시(값은 반영), 실패 항목 격리, 20% 전면 중단 게이트, anon 키가
에러 문자열에 새지 않음.
"""

from __future__ import annotations

import unittest
from unittest import mock

import glossary_cases_refresh as gcr


_BASE_URL = "https://example.supabase.co"
_ANON_KEY = "anon-public-key-not-secret"


def _item(item_id="gmp", q="GMP", findings=100, documents=80, **extra):
    row = {"id": item_id, "q": q, "findings": findings, "documents": documents}
    row.update(extra)
    return row


class EvaluateItemTest(unittest.TestCase):
    def test_normal_small_change_updates(self):
        r = gcr.evaluate_item(_item(findings=100, documents=80), 105, 82, "")
        self.assertTrue(r["updated"])
        self.assertFalse(r["large_change"])
        self.assertFalse(r["zero"])
        self.assertFalse(r["failed"])
        self.assertEqual(r["applied_findings"], 105)
        self.assertEqual(r["applied_documents"], 82)

    def test_no_drift_is_not_updated(self):
        r = gcr.evaluate_item(_item(findings=100, documents=80), 100, 80, "")
        self.assertFalse(r["updated"])
        self.assertFalse(r["large_change"])
        self.assertFalse(r["zero"])

    def test_zero_findings_keeps_previous_value(self):
        r = gcr.evaluate_item(_item(findings=100, documents=80), 0, 0, "")
        self.assertTrue(r["zero"])
        self.assertFalse(r["failed"])
        self.assertFalse(r["updated"])
        self.assertEqual(r["applied_findings"], 100)
        self.assertEqual(r["applied_documents"], 80)

    def test_large_change_is_flagged_but_applied(self):
        # 100 -> 200 은 +100% > 기본 50% 임계.
        r = gcr.evaluate_item(_item(findings=100, documents=80), 200, 150, "")
        self.assertTrue(r["large_change"])
        self.assertTrue(r["updated"])
        self.assertEqual(r["applied_findings"], 200)  # 값은 반영한다(지시서)
        self.assertEqual(r["change_pct"], 100.0)

    def test_change_at_exact_threshold_is_not_flagged(self):
        # 100 -> 150 은 정확히 +50% — "초과"가 아니므로 large_change 는 False.
        r = gcr.evaluate_item(_item(findings=100, documents=80), 150, 90, "",
                               large_change_pct=50.0)
        self.assertFalse(r["large_change"])
        self.assertTrue(r["updated"])

    def test_change_just_over_threshold_is_flagged(self):
        r = gcr.evaluate_item(_item(findings=100, documents=80), 151, 90, "",
                               large_change_pct=50.0)
        self.assertTrue(r["large_change"])

    def test_low_n_item_pct_is_computed_from_findings(self):
        # 사례 1건짜리는 2건만 돼도 +100% — 정수 하나 차이로도 임계를 넘는다(정상 동작).
        r = gcr.evaluate_item(_item(findings=1, documents=1), 2, 2, "")
        self.assertTrue(r["large_change"])
        self.assertEqual(r["change_pct"], 100.0)

    def test_call_failure_keeps_previous_value_and_is_isolated(self):
        r = gcr.evaluate_item(_item(findings=100, documents=80), None, None, "timeout")
        self.assertTrue(r["failed"])
        self.assertFalse(r["updated"])
        self.assertEqual(r["applied_findings"], 100)
        self.assertEqual(r["applied_documents"], 80)
        self.assertEqual(r["error"], "timeout")

    def test_previous_zero_new_nonzero_is_large_change_without_pct(self):
        r = gcr.evaluate_item(_item(findings=0, documents=0), 3, 2, "")
        self.assertTrue(r["large_change"])
        self.assertIsNone(r["change_pct"])
        self.assertEqual(r["applied_findings"], 3)

    def test_other_fields_are_not_touched_by_evaluate_item(self):
        # evaluate_item 은 item 딕셔너리를 읽기만 한다 — id/q 만 리포트에 반영.
        item = _item(note="사람이 남긴 메모")
        r = gcr.evaluate_item(item, 100, 80, "")
        self.assertEqual(r["id"], "gmp")
        self.assertEqual(r["q"], "GMP")


class BuildReportTest(unittest.TestCase):
    def _results(self, *, failed=0, zero=0, large=0, updated=0, total=10):
        results = []
        for i in range(total):
            r = {
                "id": f"t{i}", "q": f"q{i}", "failed": False, "zero": False,
                "large_change": False, "updated": False,
            }
            if i < failed:
                r["failed"] = True
            elif i < failed + zero:
                r["zero"] = True
            elif i < failed + zero + large:
                r["large_change"] = True
                r["updated"] = True
            elif i < failed + zero + large + updated:
                r["updated"] = True
            r.setdefault("previous_findings", 10)
            r.setdefault("fetched_findings", 10)
            r.setdefault("change_pct", None)
            r.setdefault("error", "timeout" if r["failed"] else "")
            results.append(r)
        return results

    def test_clean_run_allows_automatic_merge(self):
        results = self._results(total=10, updated=3)
        report = gcr.build_report(
            results, large_change_pct=50.0, fail_abort_pct=20.0, run_date="2026-08-11",
        )
        self.assertFalse(report["aborted"])
        self.assertTrue(report["gate"]["automatic_merge_allowed"])
        self.assertEqual(report["counts"]["updated"], 3)
        self.assertEqual(report["counts"]["unchanged"], 7)
        self.assertEqual(report["warnings"], [])

    def test_any_warning_blocks_automatic_merge(self):
        results = self._results(total=10, zero=1)
        report = gcr.build_report(
            results, large_change_pct=50.0, fail_abort_pct=20.0, run_date="2026-08-11",
        )
        self.assertFalse(report["aborted"])
        self.assertFalse(report["gate"]["automatic_merge_allowed"])
        self.assertEqual(report["counts"]["zero_kept"], 1)
        self.assertTrue(report["gate"]["review_reasons"])

    def test_failure_rate_under_threshold_is_not_aborted(self):
        # 20건 중 3건 실패 = 15% < 20%.
        results = self._results(total=20, failed=3)
        report = gcr.build_report(
            results, large_change_pct=50.0, fail_abort_pct=20.0, run_date="2026-08-11",
        )
        self.assertFalse(report["aborted"])
        self.assertEqual(report["counts"]["failed"], 3)

    def test_failure_rate_exactly_at_threshold_aborts(self):
        # 정확히 20% — 지시서 "20% 이상"은 경계 포함(>=).
        results = self._results(total=10, failed=2)
        report = gcr.build_report(
            results, large_change_pct=50.0, fail_abort_pct=20.0, run_date="2026-08-11",
        )
        self.assertTrue(report["aborted"])

    def test_failure_rate_over_threshold_aborts(self):
        results = self._results(total=10, failed=5)
        report = gcr.build_report(
            results, large_change_pct=50.0, fail_abort_pct=20.0, run_date="2026-08-11",
        )
        self.assertTrue(report["aborted"])
        self.assertFalse(report["gate"]["automatic_merge_allowed"])


class RunRefreshTest(unittest.TestCase):
    def _payload(self):
        return {
            "schema": "grm-glossary-cases/v1",
            "source": "public.findings_search RPC",
            "measured_on": "2026-08-04",
            "curated_on": "2026-08-04",
            "note": "사람이 정한 값",
            "items": [
                _item("gmp", "GMP", 100, 80),
                _item("api", "원료의약품", 50, 40, note="사람 메모"),
            ],
            "excluded": [{"id": "quality", "term_ko": "품질", "reason": "너무 흔함"}],
            "corpus_note": "단일 시점 실측",
        }

    def test_updates_items_and_measured_on_preserves_everything_else(self):
        answers = {"GMP": (110, 85, ""), "원료의약품": (52, 41, "")}
        fetch = lambda q: answers[q]  # noqa: E731
        new_payload, report = gcr.run_refresh(
            self._payload(), fetch, run_date="2026-08-11",
        )
        self.assertIsNotNone(new_payload)
        self.assertEqual(new_payload["measured_on"], "2026-08-11")
        # q 는 절대 바뀌지 않는다.
        self.assertEqual(new_payload["items"][0]["q"], "GMP")
        self.assertEqual(new_payload["items"][1]["q"], "원료의약품")
        self.assertEqual(new_payload["items"][0]["findings"], 110)
        self.assertEqual(new_payload["items"][1]["findings"], 52)
        # 항목별 note 도 무변형 통과.
        self.assertEqual(new_payload["items"][1]["note"], "사람 메모")
        # excluded/기타 최상위 키는 완전히 그대로.
        self.assertEqual(new_payload["excluded"], self._payload()["excluded"])
        self.assertEqual(new_payload["curated_on"], "2026-08-04")
        self.assertEqual(new_payload["corpus_note"], "단일 시점 실측")
        self.assertFalse(report["aborted"])

    def test_zero_result_item_keeps_file_value_unchanged(self):
        answers = {"GMP": (0, 0, ""), "원료의약품": (52, 41, "")}
        fetch = lambda q: answers[q]  # noqa: E731
        new_payload, report = gcr.run_refresh(self._payload(), fetch, run_date="2026-08-11")
        self.assertEqual(new_payload["items"][0]["findings"], 100)  # 종전 값 유지
        self.assertEqual(report["counts"]["zero_kept"], 1)

    def test_original_payload_is_not_mutated(self):
        original = self._payload()
        import copy
        snapshot = copy.deepcopy(original)
        fetch = lambda q: (999, 999, "")  # noqa: E731
        gcr.run_refresh(original, fetch, run_date="2026-08-11")
        self.assertEqual(original, snapshot)

    def test_abort_returns_none_payload_when_failure_rate_high(self):
        # 2건 중 1건 실패 = 50% >= 20% 기본 임계.
        answers_err = {"GMP": (None, None, "timeout")}
        fetch = lambda q: answers_err.get(q, (10, 8, ""))  # noqa: E731
        new_payload, report = gcr.run_refresh(self._payload(), fetch, run_date="2026-08-11")
        self.assertIsNone(new_payload)
        self.assertTrue(report["aborted"])

    def test_missing_q_raises(self):
        payload = self._payload()
        payload["items"][0]["q"] = ""
        with self.assertRaises(ValueError):
            gcr.run_refresh(payload, lambda q: (1, 1, ""))


class _FakePostResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _search_payload(findings: int, documents: int) -> dict:
    return {"documents": [], "totals": {"documents": documents, "findings": findings},
            "facets": {}, "page": 1, "docs_per_page": 1, "pages": 1, "sort": "date_desc"}


class PostFindingsSearchTest(unittest.TestCase):
    def test_happy_path_posts_expected_body_and_anon_headers(self):
        with mock.patch.object(
            gcr.requests, "post", return_value=_FakePostResponse(200, _search_payload(5, 3)),
        ) as posted:
            findings, documents, err = gcr.fetch_counts(_BASE_URL, _ANON_KEY, "GMP")
        self.assertEqual((findings, documents, err), (5, 3, ""))
        _args, kwargs = posted.call_args
        self.assertEqual(kwargs["json"], {
            "p_q": "GMP", "p_page": 1, "p_docs_per_page": 1, "p_orig_lang": "",
        })
        self.assertEqual(kwargs["headers"]["apikey"], _ANON_KEY)
        self.assertEqual(posted.call_args[0][0], f"{_BASE_URL}/rest/v1/rpc/findings_search")

    def test_http_error_surfaces_status_not_key(self):
        with mock.patch.object(
            gcr.requests, "post", return_value=_FakePostResponse(401, None),
        ):
            findings, documents, err = gcr.fetch_counts(_BASE_URL, _ANON_KEY, "GMP")
        self.assertIsNone(findings)
        self.assertEqual(err, "http_401")
        self.assertNotIn(_ANON_KEY, err)

    def test_timeout_retries_then_errors(self):
        import requests as _rq
        with mock.patch.object(
            gcr.requests, "post", side_effect=_rq.exceptions.Timeout(),
        ) as posted:
            findings, documents, err = gcr.fetch_counts(_BASE_URL, _ANON_KEY, "GMP")
        self.assertEqual(posted.call_count, gcr._MAX_ATTEMPTS)
        self.assertEqual(err, "timeout")
        self.assertIsNone(findings)

    def test_missing_totals_is_invalid_shape(self):
        with mock.patch.object(
            gcr.requests, "post", return_value=_FakePostResponse(200, {"documents": []}),
        ):
            findings, documents, err = gcr.fetch_counts(_BASE_URL, _ANON_KEY, "GMP")
        self.assertEqual(err, "invalid_response_shape")

    def test_non_object_payload_is_invalid_shape(self):
        with mock.patch.object(
            gcr.requests, "post", return_value=_FakePostResponse(200, [1, 2, 3]),
        ):
            findings, documents, err = gcr.fetch_counts(_BASE_URL, _ANON_KEY, "GMP")
        self.assertEqual(err, "invalid_response_shape")

    def test_english_population_is_sent_to_the_rpc(self):
        with mock.patch.object(
            gcr.requests, "post", return_value=_FakePostResponse(200, _search_payload(5, 3)),
        ) as posted:
            got = gcr.fetch_counts(_BASE_URL, _ANON_KEY, "Good Manufacturing Practice",
                                   orig_lang="en")
        self.assertEqual(got, (5, 3, ""))
        self.assertEqual(posted.call_args.kwargs["json"]["p_orig_lang"], "en")


class EnglishRefreshTest(unittest.TestCase):
    def _terms(self):
        return [
            {"id": "gmp", "term_en": "Good Manufacturing Practice"},
            {"id": "api", "term_en": "Active Pharmaceutical Ingredient"},
            {"id": "empty", "term_en": "No English Hit"},
        ]

    def test_uses_term_en_for_every_candidate_and_excludes_zero_with_true_reason(self):
        calls = []

        def fetch(q):
            calls.append(q)
            return {
                "Good Manufacturing Practice": (7, 4, ""),
                "Active Pharmaceutical Ingredient": (2, 2, ""),
                "No English Hit": (0, 0, ""),
            }[q]

        payload, report = gcr.run_english_refresh(
            self._terms(), {}, fetch, run_date="2026-09-05")
        self.assertFalse(report["aborted"])
        self.assertTrue(report["gate"]["automatic_merge_allowed"],
                        "첫 영문 기준선은 비교할 종전 값이 없어 자동 병합 가능해야 한다")
        self.assertEqual(calls, [t["term_en"] for t in self._terms()])
        self.assertEqual(payload["orig_lang"], "en")
        self.assertEqual(payload["measured_on"], "2026-09-05")
        self.assertEqual([it["q"] for it in payload["items"]], calls[:2])
        self.assertEqual(payload["excluded"], [{
            "id": "empty", "q": "No English Hit", "reason": "영문 원문 검색 결과 0건",
        }])
        self.assertIn("사례를 싣지 않음", report["warnings"][-1])

    def test_zero_does_not_resurrect_a_previous_link(self):
        previous = {"items": [{"id": "empty", "q": "Old phrase", "findings": 9,
                                 "documents": 8}]}
        payload, report = gcr.run_english_refresh(
            [{"id": "empty", "term_en": "No English Hit"}], previous,
            lambda q: (0, 0, ""), run_date="2026-09-05")
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["excluded"][0]["reason"], "영문 원문 검색 결과 0건")
        self.assertTrue(report["gate"]["automatic_merge_allowed"],
                        "0건 후보는 영문 링크를 만들지 않는 정상 판정이다")

    def test_isolated_failure_keeps_only_existing_english_item(self):
        previous = {"items": [{"id": "gmp", "q": "Good Manufacturing Practice",
                                 "findings": 9, "documents": 8}]}
        payload, report = gcr.run_english_refresh(
            [{"id": "gmp", "term_en": "Good Manufacturing Practice"},
             {"id": "new", "term_en": "New Term"}], previous,
            lambda q: (None, None, "timeout"), run_date="2026-09-05",
            fail_abort_pct=101.0)
        self.assertEqual(payload["items"], [{"id": "gmp", "q": "Good Manufacturing Practice",
                                               "findings": 9, "documents": 8}])
        self.assertEqual(payload["excluded"], [{"id": "new", "q": "New Term", "reason": "조회 실패"}])
        self.assertEqual(report["counts"]["failed"], 2)


if __name__ == "__main__":
    unittest.main()
