"""자료실 수집기 — PMDA ORANGE Letter 오프라인 파싱 테스트.

네트워크를 타지 않는다. 원문(품질보증 페이지 1장) 구조를 축약한 픽스처로 섹션별
doc_type 배정·제목 꼬리표 제거·id 규칙을 고정한다. 라이브 구조가 바뀌면 수집기의
하한 가드(MIN_EXPECTED_ITEMS)가 error 를 내고, 이 테스트가 파서 계약 회귀를 잡는다.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

import library_collect_pmda as pmda

CATALOG = Path(__file__).resolve().parents[1] / "web" / "data" / "library" / "pmda.json"

# 원문 구조 축약 — 실제 페이지와 같은 태그 배치(h2 섹션 / ul>li / 링크 뒤 게재월).
PAGE_HTML = """
<h1>Quality Assurance Activities</h1>
<p>intro paragraph with an <a href="/files/000999999.pdf">unsectioned link[9KB]</a></p>
<h2>GMP / GCTP Annual Report</h2>
<ul>
  <li><a href="/files/000276919.pdf">GMP / GCTP Annual Report FY 2024 (September 2025) [2.48MB]</a></li>
  <li><a href="/files/000276920.xlsx">Attachment &lt;4-3 List of Identified Deficiencies&gt;[45.5KB]</a></li>
  <li><a href="/files/000267517.pdf">GMP / GCTP Annual Report FY 2022[1.08MB]</a></li>
</ul>
<h2>Publication of Inspectional observations</h2>
<h3>List of Rapid announcement of Inspectional observations</h3>
<h4><em>O</em>bserved <em>R</em>egulatory <em>A</em>ttention (Orange Letters)</h4>
<ul>
  <li>No.1 <a href="/files/000264540.pdf">Failure to confirm adequacy of raw materials[118.71KB]</a> (April, 2022)</li>
  <li>No.10 <a href="/files/000280045.pdf">Communication within the Organization(From the Manufacturing Floor to Management)[274.43KB]</a>(October, 2023)</li>
  <li>No.13 English version is under preparation</li>
</ul>
<h2>Reviews and Related Services</h2>
<ul><li><a href="/files/000111111.pdf">Out of scope document[10KB]</a></li></ul>
"""


def _catalog_items() -> list[dict]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else payload["items"]


class PmdaParseTest(unittest.TestCase):
    def setUp(self):
        self.items = pmda.parse_index(PAGE_HTML)
        self.by_id = {item["id"]: item for item in self.items}

    def test_only_known_sections_are_collected(self):
        """섹션 밖(도입부)·범위 밖 <h2> 의 PDF 는 수집하지 않는다."""
        self.assertEqual(
            [item["id"] for item in self.items],
            ["pmda-000276919", "pmda-000267517", "pmda-000264540", "pmda-000280045"],
        )

    def test_section_decides_doc_type(self):
        self.assertEqual(self.by_id["pmda-000276919"]["doc_type"], "annual-report")
        self.assertEqual(self.by_id["pmda-000264540"]["doc_type"],
                         "inspection-observation")

    def test_non_pdf_attachments_are_excluded(self):
        """연차보고서 별첨(.xlsx)은 현행 큐레이션 범위 밖."""
        self.assertNotIn("pmda-000276920", self.by_id)

    def test_titles_drop_size_and_month_tails(self):
        self.assertEqual(self.by_id["pmda-000276919"]["title_en"],
                         "GMP / GCTP Annual Report FY 2024")
        self.assertEqual(self.by_id["pmda-000267517"]["title_en"],
                         "GMP / GCTP Annual Report FY 2022")
        self.assertEqual(self.by_id["pmda-000264540"]["title_en"],
                         "Failure to confirm adequacy of raw materials")

    def test_official_url_is_absolute_pdf(self):
        self.assertEqual(self.by_id["pmda-000264540"]["official_url"],
                         "https://www.pmda.go.jp/files/000264540.pdf")

    def test_no_published_date_is_emitted(self):
        """원문 게재 시점은 월까지만 공개된다 — 없는 '일'을 지어내 채우지 않는다."""
        for item in self.items:
            self.assertNotIn("published_date", item)

    def test_english_pending_entry_yields_nothing(self):
        """영문판 준비 중(No.13)은 링크가 없으므로 항목이 생기지 않는다."""
        self.assertEqual(len(self.items), 4)


class PmdaCatalogParityTest(unittest.TestCase):
    """id 규칙(pmda-<파일번호>)이 현행 카탈로그를 그대로 재현하는지."""

    def test_every_catalog_id_follows_the_file_number_rule(self):
        for item in _catalog_items():
            self.assertRegex(item["id"], r"^pmda-\d+$")
            self.assertEqual(
                item["official_url"],
                f"{pmda.SITE_BASE}/files/{item['id'][len('pmda-'):]}.pdf",
            )

    def test_catalog_doc_types_are_the_ones_the_parser_assigns(self):
        assigned = {doc_type for _, doc_type in pmda.SECTION_DOC_TYPES}
        for item in _catalog_items():
            self.assertIn(item["doc_type"], assigned)


class PmdaFailureTest(unittest.TestCase):
    def test_network_failure_returns_error_not_empty_success(self):
        original = pmda.http_get_html
        pmda.http_get_html = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            items, error = pmda.collect_library_items(date(2026, 7, 25))
        finally:
            pmda.http_get_html = original
        self.assertEqual(items, [])
        self.assertTrue(error)

    def test_structure_change_trips_the_floor_guard(self):
        """구조가 바뀌어 몇 건만 잡히면 부분 수집을 성공으로 넘기지 않는다."""
        original = pmda.http_get_html
        pmda.http_get_html = lambda *a, **k: PAGE_HTML
        try:
            items, error = pmda.collect_library_items(date(2026, 7, 25))
        finally:
            pmda.http_get_html = original
        self.assertEqual(items, [])
        self.assertIn("구조 변경", error)


if __name__ == "__main__":
    unittest.main()
