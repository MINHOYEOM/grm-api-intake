"""WHO 수집기 회귀 — B4(NOC 침묵 0건 금지: 구조 sentinel + 별칭 선택자).

NOC 선택이 /prequal/node/N 패턴+연도 게이트뿐이라 URL 스킴 변경 시 전건
탈락 → ([], None) 침묵 0건이 가능했다(NOC = Tier 3 최고신호). B4:
- 후보 확장: 'notice' 포함 /prequal/ 별칭 href 도 수용(연도 게이트가 nav 차단)
- sentinel: items 0 일 때 ① prequal 앵커 0 = 렌더 이상 error,
  ② 연도 텍스트 콘텐츠 앵커가 패턴 밖 = 스킴 변경 의심 error,
  ③ 연도 콘텐츠 앵커 자체가 없음 = 진짜 빈 목록(0건 정상).
- NOC core 승격: sentinel error 가 collect_who() error 로 전파(종전 core=False
  는 RSS/WHOPIR 정상 시 NOC 오류가 로그로만 남아 health 에 묻혔다).

fixture 의 nav/엔트리 형태는 2026-06-10 라이브 페이지에서 채록.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_who as w
import collect_mfds_gmp_inspection as g

RUN = date(2026, 6, 10)

# 라이브 페이지의 nav 축약 — 'notice' 별칭 링크들은 전부 연도 없음(메뉴).
_NAV = """
<a href="/prequal/inspection-services/notice-concern">Notice of Concern</a>
<a href="/prequal/inspection-services/notices-concern-nocs-medicines">NOC - Medicines</a>
<a href="/prequal/inspection-services/who-public-inspection-reports-whopirs-medicines">WHOPIR - Medicines</a>
<a href="/prequal/about-us">About us</a>
"""
_NOC_NODE_ENTRY = ('<a href="/prequal/node/828">Panexcell Clinical Lab Pvt Ltd, '
                   'Navi Mumbai - INDIA (09 October 2020)</a>')


class _StubHtml:
    def __init__(self, html: str):
        self.html = html
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs) -> str:
        self.urls.append(url)
        return self.html


class _Patched:
    """_get_html 스텁 + 요청 딜레이 0 (무네트워크·고속)."""

    def __init__(self, html: str):
        self.stub = _StubHtml(html)

    def __enter__(self):
        self._orig_get = w._get_html
        self._orig_delay = w.REQUEST_DELAY_SECONDS
        w._get_html = self.stub
        w.REQUEST_DELAY_SECONDS = 0
        return self.stub

    def __exit__(self, *exc):
        w._get_html = self._orig_get
        w.REQUEST_DELAY_SECONDS = self._orig_delay
        return False


class NocSelectorTest(unittest.TestCase):
    def test_node_entry_collected(self) -> None:
        # (a) 정상 렌더 + node 링크 → 항목 추출(현행 스킴 동결).
        with _Patched(_NAV + _NOC_NODE_ENTRY):
            items, err = w._collect_noc(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it.date_iso, "2020-10-09")
        self.assertEqual(it.signal_tier, "Tier 3")
        self.assertEqual(it.official_url, "https://extranet.who.int/prequal/node/828")
        self.assertIn("Panexcell", it.headline)

    def test_alias_scheme_entry_collected(self) -> None:
        # (a') B4 선택자 확장: /node/N 이 별칭 경로로 바뀌어도 연도 텍스트 엔트리 수집.
        html = _NAV + ('<a href="/prequal/inspection-services/notice-concern/'
                       'panexcell-clinical-lab">Panexcell Clinical Lab Pvt Ltd '
                       '(09 October 2020)</a>')
        with _Patched(html):
            items, err = w._collect_noc(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].date_iso, "2020-10-09")

    def test_nav_menu_links_not_collected(self) -> None:
        # nav 'notice' 메뉴(연도 없음)는 별칭 후보라도 연도 게이트가 차단.
        with _Patched(_NAV + _NOC_NODE_ENTRY):
            items, _ = w._collect_noc(RUN)
        self.assertEqual(len(items), 1)               # nav 4링크는 항목화 금지

    def test_node_plus_alias_same_noc_deduped(self) -> None:
        # 같은 NOC 를 node 와 별칭이 동시에 가리키면(티저+제목) 1건만.
        html = (_NAV + _NOC_NODE_ENTRY
                + '<a href="/prequal/inspection-services/notice-concern/x">'
                  'Panexcell Clinical Lab Pvt Ltd, Navi Mumbai - INDIA '
                  '(09 October 2020)</a>')
        with _Patched(html):
            items, err = w._collect_noc(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)


class NocSentinelTest(unittest.TestCase):
    def test_scheme_change_zero_items_is_error(self) -> None:
        # (b) 렌더 정상(prequal 앵커 존재) + 연도 콘텐츠 앵커가 패턴 밖 → error.
        html = _NAV + ('<a href="/prequal/inspection-services/entries/panexcell">'
                       'Panexcell Clinical Lab (09 October 2020)</a>')
        with _Patched(html):
            items, err = w._collect_noc(RUN)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("스킴 변경 의심", err)
        self.assertIn("수동 확인 필요", err)

    def test_truly_empty_list_is_normal_zero(self) -> None:
        # (c) 렌더 정상 + 연도 콘텐츠 앵커 자체가 없음 = 진짜 빈 목록 → 0건 정상.
        with _Patched(_NAV):
            items, err = w._collect_noc(RUN)
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_blank_or_foreign_page_is_error(self) -> None:
        # 렌더 이상: prequal 앵커가 하나도 없는 200 응답(WAF 중간페이지 등) → error.
        with _Patched("<html><body><p>Service temporarily unavailable</p></body></html>"):
            items, err = w._collect_noc(RUN)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("렌더 이상", err)


class Rss2HtmlStripTest(unittest.TestCase):
    """C3-a — WHO Drupal RSS2 description 의 raw HTML 태그 제거(RSS2 분기)."""

    _RSS = """<rss version="2.0"><channel><title>WHO PQ</title>
<item>
  <title>Inspection update: Example Pharma</title>
  <link>https://extranet.who.int/prequal/news/inspection-update</link>
  <pubDate>Thu, 04 Jun 2026 09:00:00 GMT</pubDate>
  <description>&lt;p&gt;GMP inspection of &lt;a href="https://x.example"&gt;Example
Pharma&lt;/a&gt; manufacturing site completed.&lt;/p&gt;</description>
</item>
</channel></rss>"""

    def test_rss2_description_tags_stripped_from_body(self) -> None:
        import xml.etree.ElementTree as ET
        orig = w.http_get_xml
        w.http_get_xml = lambda url, **kw: ET.fromstring(self._RSS)
        try:
            items, err = w._collect_rss(date(2026, 6, 1), date(2026, 6, 8))
        finally:
            w.http_get_xml = orig
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        body = items[0].body
        self.assertNotIn("<", body)                   # <p>/<a> 잔존 금지
        self.assertNotIn("href", body)
        self.assertIn("GMP inspection of Example", body)
        self.assertIn("manufacturing site completed.", body)


class WhopirPdfQuerystringTest(unittest.TestCase):
    """C3-b — WHOPIR .pdf 링크에 ?download/# 꼬리가 붙어도 수집(path 검사)."""

    def test_pdf_with_querystring_and_fragment_collected(self) -> None:
        html = (
            '<a href="/sites/default/files/whopir_files/maker-a.pdf">'
            'Maker A, Site X (June 2026)</a>'
            '<a href="/sites/default/files/whopir_files/maker-b.pdf?download=1">'
            'Maker B, Site Y (June 2026)</a>'
            '<a href="/sites/default/files/whopir_files/maker-c.pdf#page=2">'
            'Maker C, Site Z (June 2026)</a>'
            '<a href="/sites/default/files/whopir_files/notes.html">Not a PDF</a>'
        )
        with _Patched(html):
            items, err = w._collect_whopir(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 3)               # 쿼리/프래그먼트 PDF 포함, html 제외
        urls = [it.official_url for it in items]
        self.assertTrue(any("maker-b.pdf?download=1" in u for u in urls))
        self.assertTrue(any("maker-c.pdf#page=2" in u for u in urls))


class NocCorePropagationTest(unittest.TestCase):
    def test_noc_sentinel_error_propagates_to_collect_who(self) -> None:
        # B4 core 승격: RSS/WHOPIR 정상이어도 NOC 구조 error 가 소스 error 로 전파
        # (종전 core=False 는 WARN 로그로만 남아 health 에 묻혔다).
        orig = (w._collect_rss, w._collect_whopir, w._collect_noc)
        w._collect_rss = lambda s, e: ([], None)
        w._collect_whopir = lambda e: ([_dummy_item("whopir")], None)
        w._collect_noc = lambda e: ([], "WHO NOC 선택자 0건 — URL 스킴 변경 의심(수동 확인 필요)")
        try:
            items, err = w.collect_who(date(2026, 6, 1), date(2026, 6, 8))
        finally:
            w._collect_rss, w._collect_whopir, w._collect_noc = orig
        self.assertEqual(len(items), 1)               # 수집분은 graceful 반환
        self.assertIsNotNone(err)
        self.assertIn("NOC", err)

    def test_all_channels_ok_no_error(self) -> None:
        orig = (w._collect_rss, w._collect_whopir, w._collect_noc)
        w._collect_rss = lambda s, e: ([], None)
        w._collect_whopir = lambda e: ([_dummy_item("whopir")], None)
        w._collect_noc = lambda e: ([], None)         # 진짜 0건 → 정상
        try:
            items, err = w.collect_who(date(2026, 6, 1), date(2026, 6, 8))
        finally:
            w._collect_rss, w._collect_whopir, w._collect_noc = orig
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)


# WHY-1 #1 — WHOPIR PDF 결함 excerpt (flag ENABLE_WHOPIR_EXCERPT, 기본 off).
# 실제 WHOPIR PDF 평탄화 형태 — 표지(general info)/개요 + 결함 섹션.
_WHOPIR_TEXT = (
    "WHO PUBLIC INSPECTION REPORT Finished Pharmaceutical Product Manufacturer "
    "Part 1 General information Name of manufacturer: Example Pharma Ltd "
    "Address: Plot 12, Industrial Area, India "
    "Part 2 Brief summary of the activities "
    "Outcome of inspection: The site was found to be operating at an acceptable "
    "level of compliance with WHO GMP, subject to corrective actions. "
    "Summary of the deficiencies Three deficiencies were identified. "
    "1. Quality management: the pharmaceutical quality system did not ensure "
    "timely closure of CAPA. 2. Production: cross-contamination controls were inadequate."
)
_WHOPIR_HTML = (
    '<a href="/sites/default/files/whopir_files/maker-a.pdf">Maker A, Site X (June 2026)</a>'
    '<a href="/sites/default/files/whopir_files/maker-b.pdf">Maker B, Site Y (June 2026)</a>'
)


class WhopirExcerptExtractTest(unittest.TestCase):
    """_extract_whopir_excerpt — 표지 건너뛰고 결함 구간부터(앵커 미스는 키 미기록)."""

    def test_excerpt_skips_cover_and_starts_at_deficiencies(self) -> None:
        ex = w._extract_whopir_excerpt(_WHOPIR_TEXT)
        self.assertTrue(ex.startswith("Summary of the deficiencies"))
        self.assertNotIn("Name of manufacturer", ex)   # 표지 제외
        self.assertIn("cross-contamination controls", ex)

    def test_excerpt_anchor_miss_returns_empty_no_cover_leak(self) -> None:
        # P2-A: 결함/결론 앵커가 전혀 없는 표지성 텍스트 → ""(키 미기록·링크 카드 유지).
        # 종전 선두 본문 폴백은 표지/General Information 유입 경로라 제거(WL 과 동일 정책).
        cover = "WHO PUBLIC INSPECTION REPORT Part 1 General information Name: X Address: Y"
        self.assertEqual(w._extract_whopir_excerpt(cover), "")

    def test_excerpt_empty_on_empty_text(self) -> None:
        self.assertEqual(w._extract_whopir_excerpt(""), "")
        self.assertEqual(w._extract_whopir_excerpt("   "), "")

    def test_excerpt_capped_at_max_chars(self) -> None:
        big = "GMP deficiencies " + ("x" * (w.WHOPIR_EXCERPT_MAX_CHARS + 500))
        self.assertLessEqual(len(w._extract_whopir_excerpt(big)), w.WHOPIR_EXCERPT_MAX_CHARS)


class WhopirFetchExcerptTest(unittest.TestCase):
    """_fetch_whopir_excerpt — P6 PDF 엔진(_extract_pdf_text) 재사용 + graceful."""

    def test_fetch_uses_pdf_engine_and_returns_ok(self) -> None:
        orig_bytes, orig_extract = w.http_get_bytes, g._extract_pdf_text
        w.http_get_bytes = lambda url, **kw: b"%PDF-1.7 fake"
        g._extract_pdf_text = lambda data, **kw: (_WHOPIR_TEXT, "pdf-ok")
        try:
            excerpt, status = w._fetch_whopir_excerpt("https://x/whopir-z.pdf")
        finally:
            w.http_get_bytes, g._extract_pdf_text = orig_bytes, orig_extract
        self.assertEqual(status, "ok")
        self.assertTrue(excerpt.startswith("Summary of the deficiencies"))

    def test_fetch_graceful_on_network_failure(self) -> None:
        orig_bytes = w.http_get_bytes

        def _boom(url, **kw):
            raise RuntimeError("HTTP GET final failure: timeout")

        w.http_get_bytes = _boom
        try:
            excerpt, status = w._fetch_whopir_excerpt("https://x/whopir-z.pdf")
        finally:
            w.http_get_bytes = orig_bytes
        self.assertEqual(excerpt, "")
        self.assertTrue(status.startswith("fetch-fail:"))

    def test_fetch_propagates_pdf_engine_status_on_no_text(self) -> None:
        # 암호화/스캔본 등 본문 부재 → PDF 엔진 status 그대로(키 미기록 신호).
        orig_bytes, orig_extract = w.http_get_bytes, g._extract_pdf_text
        w.http_get_bytes = lambda url, **kw: b"%PDF-1.7 fake"
        g._extract_pdf_text = lambda data, **kw: ("", "pdf-encrypted")
        try:
            excerpt, status = w._fetch_whopir_excerpt("https://x/whopir-z.pdf")
        finally:
            w.http_get_bytes, g._extract_pdf_text = orig_bytes, orig_extract
        self.assertEqual(excerpt, "")
        self.assertEqual(status, "pdf-encrypted")


class WhopirCollectExcerptGateTest(unittest.TestCase):
    """_collect_whopir — flag on/off · excerpt 기록 · graceful degrade · health."""

    def _run(self, fetch_stub):
        orig_fetch, orig_delay = w._fetch_whopir_detail, w.WHOPIR_EXCERPT_DELAY_SECONDS
        w._fetch_whopir_detail = fetch_stub
        w.WHOPIR_EXCERPT_DELAY_SECONDS = 0
        try:
            with _Patched(_WHOPIR_HTML):
                return w._collect_whopir(RUN)
        finally:
            w._fetch_whopir_detail, w.WHOPIR_EXCERPT_DELAY_SECONDS = orig_fetch, orig_delay

    def test_flag_on_writes_excerpt_to_raw_payload(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
            items, err = self._run(lambda url: ("Summary of the deficiencies …", None, "ok"))
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertEqual(it.raw_payload.get("whopir_excerpt"),
                             "Summary of the deficiencies …")
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["ok"], 2)
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["failed"], 0)

    def test_flag_on_failure_is_graceful_key_omitted_item_kept(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
            items, err = self._run(lambda url: ("", None, "fetch-fail:boom"))
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)                 # 항목은 링크 카드로 유지
        for it in items:
            self.assertNotIn("whopir_excerpt", it.raw_payload)
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["failed"], 2)

    def test_flag_off_skips_fetch_entirely(self) -> None:
        def _must_not_call(url):
            raise AssertionError("flag off 인데 excerpt fetch 가 호출됨")

        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "false"}):
            items, err = self._run(_must_not_call)
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertNotIn("whopir_excerpt", it.raw_payload)
        self.assertFalse(w.LAST_HEALTH["whopir_excerpt"]["enabled"])


# ── [WHOPIR 구조화 2026-07-27] extract_whopir_report ─────────────────────────
# WHOPIR PDF 는 [Part 2 활동범위·항목별 요약 → Part 3 결론] 구조다. 종전엔 링크와
# 1,500자 excerpt 만 실려 이 구조가 통째로 유실됐다(2026-07-27 사용자 지적).
def _whopir_findings_text(n_sections: int = 3, noise: bool = False) -> str:
    """실측 WHOPIR 형태의 합성 원문 — 항목 표제는 **빈 줄 뒤 `번호. 제목`** 한 줄."""
    head = "WHO Public Inspection Report\n\nPart 1  General information\n"
    body = "\nPart 2  Summary of the inspection\n\nBrief summary of activities.\n"
    if noise:
        # 항목 본문 속 중첩 문서 목록(빈 줄 없음·뒤따르는 본문 없음) — 표제 오인 금지.
        body += ("\nDocuments reviewed during the inspection: 1. SOP index "
                 "2. WMS Validation PQ Report 3. Deviation log\n")
    for i in range(1, n_sections + 1):
        body += "\n%d. Section Title %d\n" % (i, i)
        body += ("The inspection team reviewed this system in detail. " * 8) + "\n"
    tail = ("\nPart 3  Conclusion - Inspection outcome\n"
            "Based on the areas inspected, the manufacturer was considered to be "
            "operating at an acceptable level of compliance with WHO GMP.\n"
            "\nPart 4  Annexes\nAnnex material that must not leak into the outcome.\n")
    return head + body + tail


_WHOPIR_RELIANCE_TEXT = (
    "WHO Public Inspection Report\n\nPart 1  General information\n"
    "\nPart 2  Summary of the inspection\n"
    "This report is based on SRA/NRA inspection evidence.\n"
    "Inspecting authority   EDQM\nDates of\ninspection: 12-15 March 2025\n"
    "Inspecting authority   US FDA\nDates of inspection: 3-7 June 2025\n"
    "\nPart 3  Conclusion - Inspection outcome\n"
    "Reliance was placed on the inspections listed above.\n"
)


class WhopirReportStructureTest(unittest.TestCase):
    """extract_whopir_report — 순수 함수(LLM 0). 못 읽으면 None(읽은 척 금지)."""

    def test_findings_report_yields_outcome_and_numbered_sections(self) -> None:
        rep = w.extract_whopir_report(_whopir_findings_text(3))
        self.assertIsNotNone(rep)
        assert rep is not None
        self.assertEqual(rep["report_kind"], "findings")
        self.assertIn("acceptable level of compliance", rep["outcome"])
        self.assertEqual([s["no"] for s in rep["sections"]], ["1", "2", "3"])
        self.assertEqual(rep["sections"][1]["title"], "Section Title 2")
        self.assertIn("reviewed this system", rep["sections"][1]["text"])

    def test_part4_annex_does_not_leak_into_outcome(self) -> None:
        rep = w.extract_whopir_report(_whopir_findings_text(2))
        assert rep is not None
        self.assertNotIn("must not leak", rep["outcome"])

    def test_nested_document_list_is_not_mistaken_for_a_section(self) -> None:
        # 실측 회귀(Tianjin): 본문 속 "4. WMS Validation PQ Report" 가 진짜 항목 4를
        # 밀어냈다. 표제는 빈 줄이 앞서거나 본문이 길게 뒤따르는 후보만 인정한다.
        rep = w.extract_whopir_report(_whopir_findings_text(3, noise=True))
        assert rep is not None
        titles = [s["title"] for s in rep["sections"]]
        self.assertNotIn("WMS Validation PQ Report", titles)
        self.assertEqual(titles,
                         ["Section Title 1", "Section Title 2", "Section Title 3"])

    def test_section_text_is_capped_with_ellipsis(self) -> None:
        # 상한이 없으면 브리프 JSON 이 카드 1장당 수만 자씩 불어난다(실측 68,520자).
        long_body = ("Part 2\n\n1. Long Section\n"
                     + ("The team reviewed the system. " * 200)
                     + "\n\nPart 3\nOutcome text.\n")
        rep = w.extract_whopir_report(long_body)
        assert rep is not None
        self.assertLessEqual(len(rep["sections"][0]["text"]),
                             w.WHOPIR_SECTION_MAX_CHARS + 2)
        self.assertTrue(rep["sections"][0]["text"].endswith("…"))

    def test_reliance_report_lists_authorities_and_has_no_sections(self) -> None:
        rep = w.extract_whopir_report(_WHOPIR_RELIANCE_TEXT)
        assert rep is not None
        self.assertEqual(rep["report_kind"], "reliance")
        self.assertNotIn("sections", rep)
        self.assertEqual(len(rep["reliance"]), 2)
        self.assertEqual(rep["reliance"][0]["dates"], "12-15 March 2025")

    def test_missing_part_boundaries_returns_none(self) -> None:
        self.assertIsNone(w.extract_whopir_report("본문에 Part 경계가 없는 문서"))
        self.assertIsNone(w.extract_whopir_report(""))


class WhopirFetchDetailTest(unittest.TestCase):
    """_fetch_whopir_detail — PDF 를 **한 번만** 받아 excerpt + 구조를 함께 낸다."""

    def _run(self, text: str, status: str = "pdf-ok"):
        orig_bytes, orig_extract = w.http_get_bytes, g._extract_pdf_text
        w.http_get_bytes = lambda url, **kw: b"%PDF-1.7 fake"
        g._extract_pdf_text = lambda data, **kw: (text, status)
        try:
            return w._fetch_whopir_detail("https://x/whopir-a.pdf")
        finally:
            w.http_get_bytes, g._extract_pdf_text = orig_bytes, orig_extract

    def test_returns_report_and_excerpt_from_single_fetch(self) -> None:
        calls: list[str] = []
        orig_bytes, orig_extract = w.http_get_bytes, g._extract_pdf_text

        def _get(url, **kw):
            calls.append(url)
            return b"%PDF-1.7 fake"

        w.http_get_bytes = _get
        g._extract_pdf_text = lambda data, **kw: (_whopir_findings_text(2), "pdf-ok")
        try:
            excerpt, report, status = w._fetch_whopir_detail("https://x/a.pdf")
        finally:
            w.http_get_bytes, g._extract_pdf_text = orig_bytes, orig_extract
        self.assertEqual(len(calls), 1)                 # PDF 는 한 번만 받는다
        self.assertEqual(status, "ok")
        assert report is not None
        self.assertEqual(len(report["sections"]), 2)
        self.assertTrue(excerpt)

    def test_unstructured_pdf_keeps_excerpt_and_omits_report(self) -> None:
        excerpt, report, status = self._run("Summary of the deficiencies: one item.")
        self.assertIsNone(report)
        self.assertTrue(excerpt.startswith("Summary of the deficiencies"))
        self.assertEqual(status, "no-structure")

    def test_engine_status_propagates_when_no_text(self) -> None:
        excerpt, report, status = self._run("", "pdf-encrypted")
        self.assertEqual((excerpt, report, status), ("", None, "pdf-encrypted"))


class WhopirCollectStructuredTest(unittest.TestCase):
    """_collect_whopir — 구조가 읽히면 raw_payload.whopir_report 로 싣는다."""

    def _run(self, stub):
        orig_fetch, orig_delay = w._fetch_whopir_detail, w.WHOPIR_EXCERPT_DELAY_SECONDS
        w._fetch_whopir_detail = stub
        w.WHOPIR_EXCERPT_DELAY_SECONDS = 0
        try:
            with _Patched(_WHOPIR_HTML):
                return w._collect_whopir(RUN)
        finally:
            w._fetch_whopir_detail, w.WHOPIR_EXCERPT_DELAY_SECONDS = orig_fetch, orig_delay

    def test_structured_report_lands_in_raw_payload(self) -> None:
        rep = {"type": "whopir_report", "report_kind": "findings",
               "outcome": "acceptable", "sections": [{"no": "1", "title": "T", "text": "x"}]}
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
            items, err = self._run(lambda url: ("excerpt", rep, "ok"))
        self.assertIsNone(err)
        for it in items:
            self.assertEqual(it.raw_payload["whopir_report"], rep)
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["structured"], 2)

    def test_unstructured_pdf_keeps_card_without_report_key(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
            items, err = self._run(lambda url: ("excerpt", None, "no-structure"))
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)                  # 항목은 그대로 유지
        for it in items:
            self.assertNotIn("whopir_report", it.raw_payload)
            self.assertEqual(it.raw_payload["whopir_excerpt"], "excerpt")
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["structured"], 0)


def _dummy_item(tag: str):
    return w.IntakeItem(
        source=w.SOURCE_WHO, document_id=f"who-test-{tag}", date_iso="2026-06-04",
        headline=f"[{tag}] t", official_url="https://extranet.who.int/x",
        type_or_class=w.TYPE_WHO_INSPECTION, firm="f", body="b",
        api_query="q", qa_relevance="Likely", osd_relevance="N/A",
        source_type=w.SRC_TYPE_OFFICIAL_PAGE, signal_tier="Tier 2",
        raw_payload={}, source_url="s", language=w.LANGUAGE_EN,
        region_jurisdiction=w.REGION_WHO,
    )


if __name__ == "__main__":
    unittest.main()
