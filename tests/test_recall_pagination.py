"""MFDS recall 수집기 페이지네이션 종료조건 회귀 (B2).

recall pagination 은 data.go.kr 응답의 정렬 순서를 가정하지 않는다. 옛 코드는
날짜 기반 조기중단(max(page_dates) < start → break)을 썼는데, 요청에 order 가
미지정(admin 의 order:Y 와 달리 미검증)이라 API 기본 정렬이 오름차순/미정의면
page 1 의 과거 행으로 즉시 break → 후속 페이지의 최신 회수(Tier 3)를 누락했다.
현행은 그 날짜-break 를 제거하고 totalCount 종료에만 의존한다(admin 과 동일 패턴).

라이브 API 라 정렬 자체는 단위테스트가 어렵다 — http_get_json/_extract_items 를
스텁으로 주입해 (1) 정렬 비의존(후속 페이지 최신 회수 미누락), (2) totalCount
종료조건을 검증한다.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds_recall as r


class RecallPaginationTerminationTest(unittest.TestCase):
    def _run_with_pages(self, pages, total_count, start, end):
        fetched: list[int] = []

        def fake_http_get_json(endpoint, params=None, timeout=None, retries=None):
            fetched.append(params["pageNo"])
            return {"_page": params["pageNo"]}

        def fake_extract_items(data):
            page = data["_page"]
            raw = pages.get(page, [])
            # (raw_items, response_page, num_rows, total_count, status)
            return raw, page, r.PAGE_SIZE, total_count, "00:정상"

        orig_http, orig_extract = r.http_get_json, r._extract_items
        r.http_get_json = fake_http_get_json
        r._extract_items = fake_extract_items
        try:
            items, err = r.collect_mfds_recall(start, end, service_key="dummy")
        finally:
            r.http_get_json = orig_http
            r._extract_items = orig_extract
        return items, err, fetched

    def test_recent_recall_on_later_page_is_not_missed(self) -> None:
        # page 1 = 윈도우 밖 과거 회수만(오름차순 가정 시 먼저 옴), page 2 = 윈도우 내
        # 최신 회수 1건. 옛 날짜-break 는 page 1 에서 즉시 중단 → page 2 누락.
        page1 = [
            {"PRDUCT": f"old-{i}", "ENTRPS": "구회사", "RTRVL_RESN": "과거 회수 사유",
             "RECALL_COMMAND_DATE": "20200101"}
            for i in range(r.PAGE_SIZE)
        ]
        page2 = [
            {"PRDUCT": "신규회수의약품", "ENTRPS": "신회사", "RTRVL_RESN": "최신 회수 사유",
             "RECALL_COMMAND_DATE": "20260601"}
        ]
        total = r.PAGE_SIZE + 1
        items, err, fetched = self._run_with_pages(
            {1: page1, 2: page2}, total, date(2026, 1, 1), date(2026, 12, 31)
        )
        self.assertIsNone(err)
        self.assertIn(2, fetched)  # 조기중단 없이 page 2 까지 순회
        self.assertEqual(
            [it.headline for it in items],
            ["[회수·판매중지] 신규회수의약품 — 신회사"],
        )

    def test_terminates_at_total_count(self) -> None:
        # totalCount 도달 시 정확히 종료(무한 루프/초과 순회 없음).
        page1 = [
            {"PRDUCT": "회수의약품", "ENTRPS": "회사", "RTRVL_RESN": "회수 사유",
             "RECALL_COMMAND_DATE": "20260601"}
        ]
        items, err, fetched = self._run_with_pages(
            {1: page1}, total_count=1, start=date(2026, 1, 1), end=date(2026, 12, 31)
        )
        self.assertIsNone(err)
        self.assertEqual(fetched, [1])  # 1페이지에서 totalCount 도달 → 종료
        self.assertEqual(len(items), 1)


class RecallHealthObservabilityTest(RecallPaginationTerminationTest):
    """★페이지 실패는 `(items, None)` 으로 **성공처럼** 돌아온다.

    매일 라인에서는 옳은 계약이다(다음 날 다시 받는다). 그러나 1회성 백필에서는
    조용한 부분 수집이 가장 위험한 실패라, 반환값 규약은 그대로 두고 관측치를
    모듈 전역 `LAST_HEALTH` 에 남겨 백필이 읽어 표면화하게 한다.
    """

    def setUp(self) -> None:
        r.LAST_HEALTH = {}

    def test_success_records_total_count_and_pages(self) -> None:
        page1 = [
            {"PRDUCT": "회수의약품", "ENTRPS": "회사", "RTRVL_RESN": "회수 사유",
             "RECALL_COMMAND_DATE": "20260601"}
        ]
        items, err, _ = self._run_with_pages(
            {1: page1}, total_count=1, start=date(2026, 1, 1), end=date(2026, 12, 31)
        )
        self.assertIsNone(err)
        self.assertEqual(r.LAST_HEALTH["total_count"], 1)
        self.assertEqual(r.LAST_HEALTH["collected"], 1)
        self.assertEqual(r.LAST_HEALTH["pages_seen"], 1)
        self.assertFalse(r.LAST_HEALTH["truncated"])
        self.assertEqual(r.LAST_HEALTH["page_warnings"], [])

    def test_page_failure_after_some_items_is_recorded_though_err_is_none(self) -> None:
        page1 = [
            {"PRDUCT": "회수의약품", "ENTRPS": "회사", "RTRVL_RESN": "회수 사유",
             "RECALL_COMMAND_DATE": "20260601"}
        ]

        def fake_http_get_json(endpoint, params=None, timeout=None, retries=None):
            if params["pageNo"] >= 2:
                raise RuntimeError("HTTP GET final failure")
            return {"_page": params["pageNo"]}

        def fake_extract_items(data):
            return page1, data["_page"], r.PAGE_SIZE, 500, "00:정상"

        orig_http, orig_extract = r.http_get_json, r._extract_items
        r.http_get_json = fake_http_get_json
        r._extract_items = fake_extract_items
        try:
            items, err = r.collect_mfds_recall(
                date(2026, 1, 1), date(2026, 12, 31), service_key="dummy")
        finally:
            r.http_get_json = orig_http
            r._extract_items = orig_extract

        # 계약 유지: 부분 수집이라도 err 는 None (매일 라인 동작 불변)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        # ★그러나 흔적은 남는다 — 백필이 이걸 읽어 exit 4 로 표면화한다.
        self.assertTrue(r.LAST_HEALTH["page_warnings"])
        self.assertIn("page=2", r.LAST_HEALTH["page_warnings"][0])


if __name__ == "__main__":
    unittest.main()
