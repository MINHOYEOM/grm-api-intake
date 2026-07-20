"""자료실 수집기 3종(EU GMP·PIC/S·WHO) 오프라인 파싱·선별·id 정합 테스트.

네트워크를 타지 않는다 — 실제 응답에서 잘라낸 픽스처만 쓴다.
가장 중요한 회귀 가드는 **id 정합**이다: 수집기가 만드는 id 가 현행 큐레이션 id 와
다르면 기존 항목이 전부 "신규"로 중복 추가된다.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import date
from pathlib import Path

import library_collect_eu_gmp as eu_gmp
import library_collect_pics as pics
import library_collect_who as who

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIBRARY_DIR = ROOT / "web" / "data" / "library"


def _live(source: str) -> list[dict]:
    payload = json.loads((LIBRARY_DIR / f"{source}.json").read_text(encoding="utf-8"))
    return payload["items"]


class PluginContractTest(unittest.TestCase):
    def test_every_collector_declares_source_and_entry_point(self):
        for module, source in ((eu_gmp, "eu_gmp"), (pics, "pics"), (who, "who")):
            with self.subTest(source=source):
                self.assertEqual(module.LIBRARY_SOURCE, source)
                self.assertTrue(callable(module.collect_library_items))
                self.assertTrue((LIBRARY_DIR / f"{source}.json").exists())


class EuGmpCollectorTest(unittest.TestCase):
    def setUp(self):
        self.html = (FIXTURES / "library_eu_gmp_volume4.html").read_text(encoding="utf-8")
        self.items, self.seen_links = eu_gmp.derive_items(self.html)

    def test_ids_match_every_live_catalog_entry(self):
        generated = {item["id"] for item in self.items}
        self.assertEqual({item["id"] for item in _live("eu_gmp")}, generated)

    def test_official_url_maps_back_to_curated_id(self):
        for item in _live("eu_gmp"):
            with self.subTest(item_id=item["id"]):
                self.assertEqual(eu_gmp.item_id(item["official_url"]), item["id"])

    def test_keeps_only_human_gmp_sections(self):
        # Introduction·Glossary·Part IV(ATMP)·GDP·수의용은 자료실 범위가 아니다.
        titles = " | ".join(item["title_en"].lower() for item in self.items)
        self.assertLess(len(self.items), self.seen_links)
        self.assertNotIn("glossary", titles)
        self.assertNotIn("advanced therapy", titles)
        self.assertNotIn("good distribution practice", titles)
        self.assertEqual({item["doc_type"] for item in self.items},
                         {"Part I", "Part II", "Part III", "Annex"})

    def test_chapter_code_and_title_are_split(self):
        chapter = next(i for i in self.items if i["id"] == "eu-gmp-part1-ch1")
        self.assertEqual(chapter["code"], "Part I, Chapter 1")
        self.assertEqual(chapter["title_en"], "Pharmaceutical Quality System")

    def test_annex_cell_with_two_revisions_yields_two_items(self):
        annex19 = [i for i in self.items if i["code"] == "Annex 19"]
        self.assertEqual({i["id"] for i in annex19},
                         {"eu-gmp-annex19-2005", "eu-gmp-annex19-2026"})

    def test_unknown_document_gets_deterministic_hash_id(self):
        url = "https://health.ec.europa.eu/document/download/" + ("0" * 8) + "-" + (
            "1234-5678-9abc-def012345678") + "_en?filename=new.pdf"
        first = eu_gmp.item_id(url)
        self.assertTrue(first.startswith("eu-gmp-"))
        self.assertEqual(first, eu_gmp.item_id(url + "&x=1"))   # filename/쿼리 변화에 불변
        self.assertNotIn(first, {item["id"] for item in _live("eu_gmp")})

    def test_eurlex_url_variants_share_one_id(self):
        self.assertEqual(
            eu_gmp.item_id("http://eur-lex.europa.eu/legal-content/EN/TXT/PDF/"
                           "?uri=CELEX:52015XC0321(02)&from=EN"),
            eu_gmp.item_id("https://eur-lex.europa.eu/legal-content/EN/TXT/"
                           "?uri=CELEX:52015XC0321(02)"),
        )


class PicsCollectorTest(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "library_pics_publications.html").read_text(encoding="utf-8")
        self.rows = pics.parse_rows(html)
        self.items = pics.derive_items(self.rows)

    def test_ids_match_every_live_catalog_entry(self):
        for item in _live("pics"):
            with self.subTest(item_id=item["id"]):
                self.assertEqual(pics.item_id(item["code"]), item["id"])

    def test_drafts_and_concept_papers_are_filtered_out(self):
        self.assertLess(len(self.items), len(self.rows))
        for row in self.rows:
            if row["title"].lower().startswith(("draft guidelines", "concept paper")):
                self.assertFalse(pics.keep_row(row), row["title"])

    def test_official_url_and_date_come_from_the_listing(self):
        item = next(i for i in self.items if i["id"] == "pics-pi-056-1")
        self.assertEqual(item["official_url"], "https://picscheme.org/docview/9256")
        self.assertEqual(item["published_date"], "2025-01-01")
        self.assertEqual(item["doc_type"], "Guidance")

    def test_roman_numeral_guide_parts_use_curated_ids(self):
        self.assertEqual(pics.item_id("PE 009-17 (Part I)"), "pics-pe-009-17-part1")
        self.assertEqual(pics.item_id("PE 009-17 (Part II)"), "pics-pe-009-17-part2")

    def test_malformed_date_is_omitted_rather_than_invented(self):
        rows = [{"href": "/docview/1", "date": "n/a", "title": "T",
                 "reference": "PI 099-1", "category": "c", "section": "Guidance documents"}]
        self.assertNotIn("published_date", pics.derive_items(rows)[0])


class WhoCollectorTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads(
            (FIXTURES / "library_who_meetingreports.json").read_text(encoding="utf-8"))
        self.rows = payload["value"]
        self.items, self.seen_pattern = who.derive_items(self.rows)

    def test_ids_match_every_live_catalog_entry(self):
        for item in _live("who"):
            with self.subTest(item_id=item["id"]):
                trs, annex = re.match(r"TRS (\d+) Annex (\d+)", item["code"]).groups()
                self.assertEqual(who.item_id(trs, annex), item["id"])

    def test_non_gmp_annexes_are_filtered_out(self):
        kept = {item["id"] for item in self.items}
        self.assertIn("who-trs1067-annex2", kept)          # continuous manufacturing
        self.assertIn("who-trs1025-annex4", kept)          # good chromatography practices
        self.assertNotIn("who-trs1067-annex6", kept)       # 콘돔/윤활제 규격
        self.assertNotIn("who-trs1052-annex6", kept)       # biowaiver
        self.assertNotIn("who-trs1003-annex4", kept)       # 의료기기 규제 프레임워크
        self.assertNotIn("who-trs1067-annex10", kept)      # 의료용 산소 규제 고려사항

    def test_non_trs_annex_titles_are_ignored(self):
        self.assertEqual(self.seen_pattern, sum(
            1 for row in self.rows if row["Title"].startswith("TRS ")))

    def test_duplicate_api_rows_collapse_to_one_item(self):
        ids = [item["id"] for item in self.items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_fields_are_derived_from_the_api_row(self):
        item = next(i for i in self.items if i["id"] == "who-trs1067-annex2")
        self.assertEqual(item["code"], "TRS 1067 Annex 2")
        self.assertEqual(item["published_date"], "2026-06-09")
        self.assertTrue(item["official_url"].startswith(
            "https://www.who.int/publications/m/item/trs-1067---annex-2"))
        self.assertNotIn(":", item["title_en"][:10])

    def test_unparseable_date_is_omitted_rather_than_invented(self):
        rows = [{"Title": "TRS 999 - Annex 1: WHO good manufacturing practices for tests",
                 "ItemDefaultUrl": "/trs999-annex1", "FormatedDate": "soon", "Tag": ""}]
        item = who.derive_items(rows)[0][0]
        self.assertNotIn("published_date", item)
        self.assertEqual(item["doc_type"], who.DOC_TYPE_FALLBACK)


class CollectorErrorContractTest(unittest.TestCase):
    """실패는 빈 리스트가 아니라 error 문자열로 보고되어야 한다."""

    ROBOTS_ATTR = {eu_gmp: "_robots", pics: "_robots", who: "_robots_allows"}

    def _patch(self, module, attr, value):
        original = getattr(module, attr)
        setattr(module, attr, value)
        self.addCleanup(setattr, module, attr, original)

    def test_robots_disallow_returns_error_not_empty_success(self):
        for module, attr in self.ROBOTS_ATTR.items():
            with self.subTest(source=module.LIBRARY_SOURCE):
                self._patch(module, attr, lambda url: (False, None, None))
                items, error = module.collect_library_items(date(2026, 7, 20))
                self.assertEqual(items, [])
                self.assertIn("robots.txt", error or "")

    def test_fetch_failure_returns_error_not_empty_success(self):
        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        for module in (eu_gmp, pics):
            with self.subTest(source=module.LIBRARY_SOURCE):
                self._patch(module, self.ROBOTS_ATTR[module], lambda url: (True, 0.0, None))
                self._patch(module, "http_get_html", explode)
                items, error = module.collect_library_items(date(2026, 7, 20))
                self.assertEqual(items, [])
                self.assertIn("boom", error or "")

    def test_who_api_failure_returns_error_not_empty_success(self):
        def explode(*args, **kwargs):
            raise RuntimeError("api down")

        self._patch(who, "_robots_allows", lambda url: (True, 0.0, None))
        self._patch(who, "http_get_json", explode)
        items, error = who.collect_library_items(date(2026, 7, 20))
        self.assertEqual(items, [])
        self.assertIn("api down", error or "")


if __name__ == "__main__":
    unittest.main()
