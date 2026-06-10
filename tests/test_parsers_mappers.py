"""정밀검토 배치3 Tier2 — 순수 파서·매퍼 회귀(D1 공백 해소, 현 동작 동결).

대상(전부 결정론·무네트워크):
- 날짜/윈도우/ID 유틸: _parse_rss2_date·_parse_atom_date·_within_window·
  _stable_doc_id·_safe_date_iso — 모든 수집기의 윈도우 필터·dedup 의 기반.
- _FDAWLTableParser·_parse_wl_date: FDA WL HTML 테이블 파싱(노이즈 게이트는
  test_noise_filters 가 커버 — 여기선 파싱만).
- build_notion_properties/children: IntakeItem→Notion 매핑. raw JSON code block
  보존(Evidence A 재검증)·1900자 청크 분할·누락 필드 graceful 생략.

테스트 전용 배치 — 프로덕션 로직 무변경.
"""
import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci


# ─────────────────────────────────────────────────────────────────────────────
# T2-a. 날짜·윈도우·ID 유틸
# ─────────────────────────────────────────────────────────────────────────────
class Rss2DateTest(unittest.TestCase):
    def test_rfc822_to_iso(self) -> None:
        self.assertEqual(ci._parse_rss2_date("Wed, 04 Jun 2026 09:30:00 GMT"),
                         "2026-06-04")
        self.assertEqual(ci._parse_rss2_date("Thu, 05 Jun 2026 01:00:00 +0900"),
                         "2026-06-05")

    def test_iso_fallback(self) -> None:
        # 일부 RSS 피드가 ISO 8601 을 쓰는 경우 atom 파서 폴백.
        self.assertEqual(ci._parse_rss2_date("2026-06-04T09:00:00Z"), "2026-06-04")

    def test_garbage_returns_empty(self) -> None:
        self.assertEqual(ci._parse_rss2_date("not a date"), "")
        self.assertEqual(ci._parse_rss2_date(""), "")


class AtomDateTest(unittest.TestCase):
    def test_common_iso_variants(self) -> None:
        self.assertEqual(ci._parse_atom_date("2026-06-04T12:00:00Z"), "2026-06-04")
        self.assertEqual(ci._parse_atom_date("2026-06-04T12:00:00+09:00"), "2026-06-04")
        self.assertEqual(ci._parse_atom_date("2026-06-04"), "2026-06-04")

    def test_garbage_and_empty_return_empty(self) -> None:
        self.assertEqual(ci._parse_atom_date("04 June 2026"), "")
        self.assertEqual(ci._parse_atom_date(""), "")


class WithinWindowTest(unittest.TestCase):
    START, END = date(2026, 6, 1), date(2026, 6, 8)

    def test_boundaries_inclusive(self) -> None:
        # [start, end] 양끝 포함 — off-by-one 동결.
        self.assertTrue(ci._within_window("2026-06-01", self.START, self.END))
        self.assertTrue(ci._within_window("2026-06-08", self.START, self.END))
        self.assertTrue(ci._within_window("2026-06-04", self.START, self.END))

    def test_one_day_outside_excluded(self) -> None:
        self.assertFalse(ci._within_window("2026-05-31", self.START, self.END))
        self.assertFalse(ci._within_window("2026-06-09", self.START, self.END))

    def test_empty_or_malformed_date_excluded(self) -> None:
        # 빈/깨진 날짜는 윈도우 밖 취급(수집 제외) — 크래시 없이.
        self.assertFalse(ci._within_window("", self.START, self.END))
        self.assertFalse(ci._within_window("06/04/2026", self.START, self.END))


class StableDocIdTest(unittest.TestCase):
    def test_deterministic_same_inputs_same_id(self) -> None:
        a = ci._stable_doc_id("EMA", "Guideline on X", "https://e.eu/x", "2026-06-04")
        b = ci._stable_doc_id("EMA", "Guideline on X", "https://e.eu/x", "2026-06-04")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)                      # sha1 상위 12자
        self.assertTrue(all(c in "0123456789abcdef" for c in a))

    def test_any_component_change_changes_id(self) -> None:
        base = ci._stable_doc_id("EMA", "title", "url", "2026-06-04")
        for variant in (
            ci._stable_doc_id("MHRA", "title", "url", "2026-06-04"),   # source
            ci._stable_doc_id("EMA", "other", "url", "2026-06-04"),    # title
            ci._stable_doc_id("EMA", "title", "url2", "2026-06-04"),   # url
            ci._stable_doc_id("EMA", "title", "url", "2026-06-05"),    # date
        ):
            self.assertNotEqual(base, variant)


class SafeDateIsoTest(unittest.TestCase):
    def test_iso_passthrough_and_yyyymmdd_conversion(self) -> None:
        self.assertEqual(ci._safe_date_iso("2026-06-04"), "2026-06-04")
        self.assertEqual(ci._safe_date_iso("20260604"), "2026-06-04")  # OpenFDA 형식

    def test_invalid_inputs_return_empty(self) -> None:
        self.assertEqual(ci._safe_date_iso(""), "")
        self.assertEqual(ci._safe_date_iso("junk"), "")
        self.assertEqual(ci._safe_date_iso("20261399"), "")   # 13월 99일 — 검증 거부


# ─────────────────────────────────────────────────────────────────────────────
# T2-b. _FDAWLTableParser / _parse_wl_date
# ─────────────────────────────────────────────────────────────────────────────
_WL_HTML = """
<html><body>
<table class="table lcds-datatable">
  <thead><tr><th>Posted Date</th><th>Recipient</th><th>Letter Issue Date</th>
      <th>Issuing Office</th><th>Subject</th></tr></thead>
  <tbody>
    <tr>
      <td>06/04/2026</td>
      <td><a href="/letters/example-pharma">Example Pharma Inc</a></td>
      <td>06/02/2026</td>
      <td>Center for Drug Evaluation and Research (CDER)</td>
      <td>CGMP/Finished Pharmaceuticals/Adulterated</td>
    </tr>
    <tr><td>only</td><td>three</td><td>cells</td></tr>
  </tbody>
</table>
</body></html>
"""


class FdaWlTableParserTest(unittest.TestCase):
    def test_extracts_cells_and_href(self) -> None:
        p = ci._FDAWLTableParser()
        p.feed(_WL_HTML)
        # 헤더 행(th 5개) + 본문 행 1개 — 셀 3개 행은 <4 라 드롭.
        data_rows = [r for r in p.rows if "06/04/2026" in r["_cols"][0]]
        self.assertEqual(len(data_rows), 1)
        cols = data_rows[0]["_cols"]
        self.assertEqual(cols[0], "06/04/2026")
        # recipient 셀은 "텍스트|HREF:링크" 형식으로 href 동반 보존.
        self.assertEqual(cols[1], "Example Pharma Inc|HREF:/letters/example-pharma")
        self.assertEqual(cols[3], "Center for Drug Evaluation and Research (CDER)")
        self.assertIn("CGMP", cols[4])

    def test_short_rows_are_dropped(self) -> None:
        p = ci._FDAWLTableParser()
        p.feed(_WL_HTML)
        self.assertFalse(any("only" in r["_cols"][0] for r in p.rows))

    def test_table_without_class_is_ignored(self) -> None:
        p = ci._FDAWLTableParser()
        p.feed('<table><tr><td>a</td><td>b</td><td>c</td><td>d</td></tr></table>')
        self.assertEqual(p.rows, [])

    def test_empty_and_malformed_html_no_crash(self) -> None:
        for html in ("", "<table class='table'><tr><td>x", "<div>no table</div>"):
            with self.subTest(html=html[:20]):
                p = ci._FDAWLTableParser()
                p.feed(html)        # 크래시 0 — rows 는 비거나 부분
                self.assertIsInstance(p.rows, list)


class ParseWlDateTest(unittest.TestCase):
    def test_us_format_and_iso(self) -> None:
        self.assertEqual(ci._parse_wl_date("06/04/2026"), "2026-06-04")
        self.assertEqual(ci._parse_wl_date("2026-06-04"), "2026-06-04")

    def test_garbage_returns_empty(self) -> None:
        self.assertEqual(ci._parse_wl_date("June 4, 2026"), "")
        self.assertEqual(ci._parse_wl_date("99/99/2026"), "")


# ─────────────────────────────────────────────────────────────────────────────
# T2-c. build_notion_properties / build_notion_children
# ─────────────────────────────────────────────────────────────────────────────
RUN_DATE = date(2026, 6, 10)
COLLECTED = datetime(2026, 6, 10, 3, 17)


def _item(**over):
    base = dict(
        source=ci.SOURCE_MFDS, document_id="recall-abc123", date_iso="2026-06-08",
        headline="[회수·판매중지] 테스트정", official_url="https://mfds.go.kr/x",
        type_or_class="recall-quality", firm="테스트제약",
        body="회수 사유 본문", api_query="https://apis.data.go.kr/x?y=1",
        qa_relevance="Likely", signal_tier="Tier 3",
        raw_payload={"RTRVL_RESN": "함량부적합", "PRDUCT": "테스트정"},
        language="KO", region_jurisdiction="Korea (MFDS)",
    )
    base.update(over)
    return ci.IntakeItem(**base)


class BuildNotionPropertiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop("ENABLE_MODALITY_TAG", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["ENABLE_MODALITY_TAG"] = self._saved
        else:
            os.environ.pop("ENABLE_MODALITY_TAG", None)

    def test_core_property_mapping(self) -> None:
        props = ci.build_notion_properties(_item(), RUN_DATE, COLLECTED)
        self.assertEqual(props[ci.PROP_SOURCE]["select"]["name"], ci.SOURCE_MFDS)
        self.assertEqual(props[ci.PROP_DOC_ID]["rich_text"][0]["text"]["content"],
                         "recall-abc123")
        self.assertEqual(props[ci.PROP_SIGNAL_TIER]["select"]["name"], "Tier 3")
        self.assertEqual(props[ci.PROP_QA_RELEVANCE]["select"]["name"], "Likely")
        self.assertEqual(props[ci.PROP_STATUS]["select"]["name"], "New")
        self.assertEqual(props[ci.PROP_RUN_DATE]["date"]["start"], "2026-06-10")
        self.assertEqual(props[ci.PROP_DATE]["date"]["start"], "2026-06-08")
        self.assertEqual(props[ci.PROP_OFFICIAL_URL]["url"], "https://mfds.go.kr/x")
        self.assertEqual(props[ci.PROP_API_QUERY]["url"],
                         "https://apis.data.go.kr/x?y=1")
        self.assertEqual(props[ci.PROP_LANGUAGE]["select"]["name"], "KO")
        # Name 타이틀: 소스 프리픽스 + doc id.
        title = props[ci.PROP_NAME]["title"][0]["text"]["content"]
        self.assertTrue(title.startswith("MFDS recall-abc123"))

    def test_missing_optional_fields_are_omitted_not_none(self) -> None:
        # 빈 url/date/firm → 속성 자체 생략(None 값이 props 에 들어가면 Notion 400).
        props = ci.build_notion_properties(
            _item(official_url="", date_iso="", firm="", api_query="", body=""),
            RUN_DATE, COLLECTED)
        for key in (ci.PROP_OFFICIAL_URL, ci.PROP_DATE, ci.PROP_FIRM,
                    ci.PROP_API_QUERY, ci.PROP_BODY):
            self.assertNotIn(key, props)
        self.assertTrue(all(v is not None for v in props.values()))

    def test_modality_gate_on_off(self) -> None:
        # ENABLE_MODALITY_TAG=true 일 때만 Modality select 기록(안전 게이트).
        os.environ["ENABLE_MODALITY_TAG"] = "true"
        props_on = ci.build_notion_properties(_item(), RUN_DATE, COLLECTED)
        self.assertIn(ci.PROP_MODALITY, props_on)
        self.assertIn(props_on[ci.PROP_MODALITY]["select"]["name"],
                      (ci.MODALITY_CHEMICAL, ci.MODALITY_BIOLOGIC, ci.MODALITY_OTHER))
        os.environ["ENABLE_MODALITY_TAG"] = "false"
        props_off = ci.build_notion_properties(_item(), RUN_DATE, COLLECTED)
        self.assertNotIn(ci.PROP_MODALITY, props_off)


class RichTextChunkingTest(unittest.TestCase):
    def test_long_text_split_into_1900_char_chunks(self) -> None:
        text = "가" * 4500
        parts = ci._rich_text(text)
        self.assertEqual(len(parts), 3)                       # 1900+1900+700
        self.assertTrue(all(
            len(p["text"]["content"]) <= ci.NOTION_RICH_TEXT_CHUNK for p in parts))
        self.assertEqual("".join(p["text"]["content"] for p in parts), text)

    def test_empty_returns_empty_list(self) -> None:
        self.assertEqual(ci._rich_text(""), [])

    def test_truncate_appends_ellipsis_within_limit(self) -> None:
        self.assertEqual(ci.truncate("짧음", 10), "짧음")
        out = ci.truncate("a" * 3000, 1900)
        self.assertEqual(len(out), 1900)
        self.assertTrue(out.endswith("…"))


class BuildNotionChildrenTest(unittest.TestCase):
    def test_raw_payload_round_trips_through_code_blocks(self) -> None:
        # Evidence A 재검증용 — code block 들을 이어붙이면 원본 JSON 완전 복원.
        item = _item(raw_payload={"RTRVL_RESN": "함량부적합 " * 800,   # JSON >1900자 보장
                                  "PRDUCT": "테스트정", "n": 42})
        blocks = ci.build_notion_children(item)
        self.assertEqual(blocks[0]["type"], "heading_3")
        self.assertEqual(
            blocks[0]["heading_3"]["rich_text"][0]["text"]["content"],
            "Raw API payload")
        code_blocks = [b for b in blocks[1:] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), len(blocks) - 1)   # heading 외 전부 code
        for b in code_blocks:
            self.assertEqual(b["code"]["language"], "json")
            self.assertLessEqual(len(b["code"]["rich_text"][0]["text"]["content"]),
                                 ci.NOTION_CODE_BLOCK_CHUNK)
        rejoined = "".join(
            b["code"]["rich_text"][0]["text"]["content"] for b in code_blocks)
        self.assertEqual(json.loads(rejoined), item.raw_payload)
        self.assertGreater(len(code_blocks), 1)               # 장문 → 청크 분할 검증

    def test_small_payload_single_code_block(self) -> None:
        blocks = ci.build_notion_children(_item(raw_payload={"k": "v"}))
        self.assertEqual(len(blocks), 2)                      # heading + code 1개


if __name__ == "__main__":
    unittest.main()
