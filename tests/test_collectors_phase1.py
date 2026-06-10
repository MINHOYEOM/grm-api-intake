"""정밀검토 배치3 Tier3 — Phase 1 수집기(FR·OpenFDA) HTTP 스텁 회귀(현 동작 동결).

핵심 계약: error vs empty 구분(빈 결과=정상 0건, 네트워크/구조 오류=error 문자열),
pagination 진행·종료, truncation 상한 표면화, api_key 마스킹. http_get_json 을
스텁으로 주입(무네트워크). EMA/MHRA/PIC/S/ECA/WL 은 배치 3b 이월.

테스트 전용 배치 — 프로덕션 로직 무변경.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci
from grm_common import HTTPClientError

START, END = date(2026, 6, 1), date(2026, 6, 8)


class _StubHttp:
    """http_get_json 스텁 — 응답 시퀀스 재생 + 요청 URL 기록."""

    def __init__(self, responses):
        self._iter = iter(responses)
        self.urls: list[str] = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        nxt = next(self._iter)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _patched(stub):
    class _Ctx:
        def __enter__(self):
            self._orig = ci.http_get_json
            ci.http_get_json = stub
            return stub

        def __exit__(self, *exc):
            ci.http_get_json = self._orig
            return False
    return _Ctx()


def _fr_doc(n, **over):
    base = {
        "document_number": f"2026-{n:05d}",
        "title": f"Pharmaceutical Quality Rule {n}",
        "publication_date": "2026-06-04",
        "html_url": f"https://federalregister.gov/d/2026-{n:05d}",
        "type": "Rule",
        "abstract": "Current good manufacturing practice for finished pharmaceuticals.",
    }
    base.update(over)
    return base


class FederalRegisterCollectorTest(unittest.TestCase):
    def test_pagination_follows_next_page_url(self) -> None:
        stub = _StubHttp([
            {"results": [_fr_doc(1), _fr_doc(2)], "next_page_url": "https://fr/p2"},
            {"results": [_fr_doc(3)], "next_page_url": None},
        ])
        with _patched(stub):
            items, err = ci.collect_federal_register(START, END)
        self.assertIsNone(err)
        self.assertEqual([i.document_id for i in items],
                         ["2026-00001", "2026-00002", "2026-00003"])
        self.assertEqual(stub.urls[1], "https://fr/p2")      # next_page_url 추종
        # 매핑: 날짜·URL·raw 보존 + 윈도우가 API 쿼리 조건으로 인코딩됨.
        self.assertEqual(items[0].date_iso, "2026-06-04")
        self.assertEqual(items[0].official_url,
                         "https://federalregister.gov/d/2026-00001")
        self.assertEqual(items[0].raw_payload["document_number"], "2026-00001")
        self.assertIn("2026-06-01", items[0].api_query)      # gte=start
        self.assertIn("2026-06-08", items[0].api_query)      # lte=end

    def test_empty_results_is_normal_zero_not_error(self) -> None:
        stub = _StubHttp([{"results": [], "next_page_url": None}])
        with _patched(stub):
            items, err = ci.collect_federal_register(START, END)
        self.assertEqual(items, [])
        self.assertIsNone(err)                               # 0건 = 정상(에러 아님)

    def test_network_error_returns_partial_and_error_msg(self) -> None:
        # page 1 성공 후 page 2 네트워크 실패 → 부분 수집 + error 문자열(침묵 금지).
        stub = _StubHttp([
            {"results": [_fr_doc(1)], "next_page_url": "https://fr/p2"},
            RuntimeError("connection reset"),
        ])
        with _patched(stub):
            items, err = ci.collect_federal_register(START, END)
        self.assertEqual(len(items), 1)
        self.assertIn("connection reset", err)

    def test_pagination_cap_surfaces_truncation(self) -> None:
        # 10페이지 상한 초과 → truncated 메시지 반환(조용한 부분 수집 금지).
        stub = _StubHttp([
            {"results": [_fr_doc(n)], "next_page_url": f"https://fr/p{n + 1}"}
            for n in range(1, 12)
        ])
        with _patched(stub):
            items, err = ci.collect_federal_register(START, END)
        self.assertEqual(len(items), 10)                     # 10페이지까지만
        self.assertIn("truncated", err)


def _recall_rec(n, **over):
    base = {
        "recall_number": f"D-{n:04d}-2026",
        "classification": "Class II",
        "product_description": f"Drug Product {n} 50mg Tablets",
        "reason_for_recall": "Failed dissolution specifications; CGMP deviations.",
        "recalling_firm": "Example Pharma LLC",
        "distribution_pattern": "Nationwide",
        "report_date": "20260604",
        "product_type": "Drugs",
    }
    base.update(over)
    return base


def _meta(total):
    return {"results": {"total": total}}


class OpenFdaRecallCollectorTest(unittest.TestCase):
    def test_skip_pagination_until_meta_total(self) -> None:
        stub = _StubHttp([
            {"results": [_recall_rec(n) for n in range(100)], "meta": _meta(150)},
            {"results": [_recall_rec(n) for n in range(100, 150)], "meta": _meta(150)},
        ])
        with _patched(stub):
            items, err = ci.collect_openfda_recalls(START, END, api_key=None)
        self.assertIsNone(err)
        self.assertEqual(len(items), 150)
        self.assertIn("skip=100", stub.urls[1])              # 2페이지 skip 진행
        # 매핑: report_date YYYYMMDD → ISO, raw 보존.
        self.assertEqual(items[0].date_iso, "2026-06-04")
        self.assertEqual(items[0].document_id, "D-0000-2026")
        self.assertEqual(items[0].raw_payload["product_type"], "Drugs")

    def test_404_means_zero_results_not_error(self) -> None:
        # OpenFDA 관행: 기간 내 0건이면 404 — 정상 종료(0건, 에러 아님)로 구분해야 함.
        stub = _StubHttp([HTTPClientError(404, "https://api.fda.gov/x")])
        with _patched(stub):
            items, err = ci.collect_openfda_recalls(START, END, api_key=None)
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_non_404_client_error_is_real_error(self) -> None:
        # 403(키/권한)은 0건 관행이 아니라 실제 에러 — error 문자열로 표면화.
        stub = _StubHttp([HTTPClientError(403, "https://api.fda.gov/x")])
        with _patched(stub):
            items, err = ci.collect_openfda_recalls(START, END, api_key=None)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("403", err)

    def test_max_total_cap_surfaces_truncation(self) -> None:
        # OPENFDA_MAX_TOTAL(200) 도달 → truncated 메시지(이후 항목 누락 가시화).
        stub = _StubHttp([
            {"results": [_recall_rec(n) for n in range(100)], "meta": _meta(500)},
            {"results": [_recall_rec(n) for n in range(100, 200)], "meta": _meta(500)},
        ])
        with _patched(stub):
            items, err = ci.collect_openfda_recalls(START, END, api_key=None)
        self.assertEqual(len(items), 200)
        self.assertIn("truncated", err)

    def test_api_key_sent_to_api_but_masked_in_item_query(self) -> None:
        # 시크릿 위생: 실제 요청 URL 엔 키 포함, 항목 api_query/로그 표기는 마스킹.
        stub = _StubHttp([
            {"results": [_recall_rec(1)], "meta": _meta(1)},
        ])
        with _patched(stub):
            items, err = ci.collect_openfda_recalls(START, END, api_key="SECRETKEY")
        self.assertIsNone(err)
        self.assertIn("api_key=SECRETKEY", stub.urls[0])     # 요청엔 실제 키
        self.assertNotIn("SECRETKEY", items[0].api_query)    # 저장값엔 노출 금지
        self.assertIn("***REDACTED***", items[0].api_query)


if __name__ == "__main__":
    unittest.main()
