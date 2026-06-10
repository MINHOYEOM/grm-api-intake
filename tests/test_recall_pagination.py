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


if __name__ == "__main__":
    unittest.main()
