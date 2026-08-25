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

    # ── [앵커 수리 2026-08-25] 172건 전수 실측 회귀 ─────────────────────────────
    def test_desk_assessment_excerpt_starts_at_conclusion_not_document_list(self) -> None:
        """실측 회귀(reliance 39건 전수): desk assessment 에는 결함 섹션이 없는데 앵커
        목록이 findings 전제라 `deficiencies` 낱말이 Part 4 **제출문서 목록**에서 먼저
        걸렸다. 그 서류 체크리스트가 LLM 근거창(issue_or_reason·body_excerpt 300자)이
        되어, 발행 카드가 desk assessment 를 "실사 결과"라 쓰고 존재하지도 않는
        "지적 항목"을 확보하라고 적었다. desk assessment 는 결론이 곧 근거다."""
        text = (
            "Part 4 Summary of the assessment of supporting documentation\n"
            "a) SRA inspection report, including deficiencies; b) GMP certificate;\n"
            "Part 5\nConclusion – Desk assessment outcome\n"
            "Based on the GMP evidence received and reviewed, the site is considered "
            "to be operating at an acceptable level of compliance with WHO GMP.\n")
        ex = w._extract_whopir_excerpt(text)
        self.assertTrue(ex.startswith("Conclusion – Desk assessment outcome"), ex[:70])
        self.assertNotIn("GMP certificate", ex.split("Conclusion")[0])

    def test_excerpt_backs_up_to_sentence_start(self) -> None:
        """낱말 앵커는 문장 한가운데 떨어진다 — 172건 중 161건이 주어 없이 시작했다
        ("deficiencies observed in last inspection were verified…"). 근거창이 잘린 채
        요약이 쓰이므로 문장 시작으로 되감는다."""
        text = ("Part 2 Summary of the inspection. The inspection was closed. "
                "Outstanding deficiencies were addressed by the firm within 30 days.")
        ex = w._extract_whopir_excerpt(text)
        self.assertTrue(ex.startswith("Outstanding deficiencies"), ex[:60])


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
                items, err = w._collect_whopir(RUN)
            # 보강은 중복 제거 뒤 별도 단계다(collect_intake 가 신규 항목만 넘긴다).
            w.enrich_whopir_items(items)
            return items, err
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

    def test_page_footer_block_is_stripped_from_section_text(self) -> None:
        """실측 회귀: PDF 평탄화가 매 쪽 하단을 본문 한가운데로 밀어 넣는다.

        Zhejiang 항목 11·15 가 "20, AVENUE APPIA … Page 10 of 14" 로 시작했고, 그 쓰레기가
        항목 본문 600자 예산까지 먹었다. 주소줄~쪽번호를 한 덩어리로 지운다(사이의 러닝헤더도
        함께). Ecron 변형은 140자 구분선이 끼어 블록이 길다 — 상한이 짧으면 다시 샌다.
        """
        footer = (
            "20, AVENUE APPIA – CH-1211 GENEVA 27 – SWITZERLAND – TEL CENTRAL "
            "+41 22 791 2111 – FAX CENTRAL +41 22 791 3111 – WWW.WHO.INT\n"
            "Ecron Acunova Limited, Manipal India - CRO\n\n10-13 February 2026\n"
            + "-" * 140 + "\n"
            "This inspection report is the property of the WHO\n"
            "Contact: prequalinspection@who.int\n\n\nPage 11 of 24\n\n\n"
            "Client Confidential")
        text = ("Part 2\n\n1. Validation\n" + footer
                + "\nThe validation master plan was reviewed and found current. "
                + ("Details were verified. " * 12)
                + "\n\nPart 3\nOutcome text.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        body = rep["sections"][0]["text"]
        self.assertTrue(body.startswith("The validation master plan"), body[:60])
        for junk in ("AVENUE APPIA", "prequalinspection", "Page 11 of 24",
                     "Client Confidential", "----"):
            self.assertNotIn(junk, body, f"푸터 잔재가 본문에 남았다: {junk}")

    def test_reliance_report_lists_authorities_and_has_no_sections(self) -> None:
        rep = w.extract_whopir_report(_WHOPIR_RELIANCE_TEXT)
        assert rep is not None
        self.assertEqual(rep["report_kind"], "reliance")
        self.assertNotIn("sections", rep)
        self.assertEqual(len(rep["reliance"]), 2)
        self.assertEqual(rep["reliance"][0]["dates"], "12-15 March 2025")

    def test_reliance_authority_is_reassembled_from_split_table_cell(self) -> None:
        """실측 회귀: PDF 평탄화가 표 셀을 여러 줄로 쪼개 앞 행 답변 꼬리가 딸려왔다.

        첫 구현이 `"t to last) and comments Dutch Health…"` 같은 값을 냈다(2026-07-27).
        건수만 세고 값을 안 본 검증이 놓친 결함이라 값 자체를 고정한다.
        """
        text = (
            "Part 2\n"
            "Summary of SRA/NRA inspection evidence considered (from most recent\n"
            "to last) and comments\n"
            "Korean\nMinistry of\nFood and Drug\nSafety (MFDS\nKorea)\n"
            "Dates of inspection:\n21 to 23 October 2025\n"
            "Any sections of GMP not\ncovered?\nNot specified\n"
            "AEMPS\n(Spain)\n\nDates of inspection:\n15-17 January 2024\n"
            "\nPart 3\nReliance was placed on the inspections listed above.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual(
            [(r["authority"], r["dates"]) for r in rep["reliance"]],
            [("Korean Ministry of Food and Drug Safety (MFDS Korea)", "21 to 23 October 2025"),
             ("AEMPS (Spain)", "15-17 January 2024")])

    def test_reliance_entry_dropped_when_authority_unreadable(self) -> None:
        """못 읽으면 안 싣는다 — 쓰레기 기관명을 싣느니 항목을 뺀다."""
        text = ("Part 2\n"
                "Summary of SRA/NRA inspection evidence considered (from most recent\n"
                "to last) and comments\n"
                "the scope was comprehensive overall.\n"
                "Dates of inspection:\n1 January 2025\n"
                "\nPart 3\nConclusion – Desk assessment outcome\nOutcome.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertNotIn("reliance", rep)
        self.assertEqual(rep["outcome"], "Outcome.")   # Desk assessment 표제도 벗긴다

    # ── [reliance 결론 슬롯 2026-08-25] 실측 회귀 — 발행 6/6 이 Part 3 을 결론으로 실었다 ──
    def test_reliance_outcome_comes_from_part5_conclusion_not_part3(self) -> None:
        """실측 회귀(2026-08-25·발행 reliance 6/6): reliance 골격은 6-Part 다 —
        findings 규칙(결론 = Part 2 뒤 첫 마커)을 그대로 쓰면 outcome 에 Part 3
        (과거 WHO 실사 요약)이 실려 1,200자 중간 절단되고, 진짜 결론(Part 5
        "Conclusion – Desk assessment outcome")은 통째로 유실된다. 결론은 번호가
        아니라 표제로 찾고, Part 6(가이드라인 목록) 앞에서 끊는다."""
        text = (
            "WHO Public Inspection Report\n\nPart 1  General information\n"
            "\nPart 2  Summary of SRA/NRA inspection evidence considered\n"
            "Inspecting authority   EDQM\nDates of\ninspection: 12-15 March 2025\n"
            "\nPart 3  Summary of the last WHO inspection\n"
            "Date and conclusion of most recent WHO inspection. This decoy summary "
            "must not be published as the outcome.\n"
            "\nPart 4  Summary of the assessment of supporting documentation\n"
            "a) List of all regulatory inspections in the last five years.\n"
            "\nPart 5  Conclusion – Desk assessment outcome\n"
            "Based on the GMP evidence received and reviewed, the manufacturer is "
            "considered to be operating at an acceptable level of compliance with "
            "WHO GMP guidelines.\n"
            "\nPart 6  List of guidelines referenced in this inspection report\n"
            "1. WHO good manufacturing practices, TRS No. 986, Annex 2: Part 2: "
            "Interpretation of Guidelines on heating, ventilation and "
            "air-conditioning systems.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual(rep["report_kind"], "reliance")
        self.assertTrue(rep["outcome"].startswith("Based on the GMP evidence"),
                        rep["outcome"][:80])
        self.assertIn("acceptable level of compliance", rep["outcome"])
        for junk in ("last WHO inspection", "supporting documentation",
                     "List of guidelines"):
            self.assertNotIn(junk, rep["outcome"], f"결론 아닌 Part 가 샜다: {junk}")
        self.assertEqual(rep["reliance"][0]["dates"], "12-15 March 2025")

    # ── [잔존 결함 3종 2026-08-25] WHO 목록 172건 전수 실측 회귀 ────────────────
    def _wla_text(self, evidence_phrase: str) -> str:
        return (
            "WHO Public Inspection Report\n\nPart 1  General information\n"
            "\nPart 2  " + evidence_phrase + "\n"
            "Inspecting authority   US FDA\nDates of\ninspection: 3-7 June 2025\n"
            "\nPart 3  Summary of the last WHO inspection\n"
            "The site was not subject to an onsite inspection by WHO in the last 5 years. "
            "Abbreviations Meaning BMR Batch Manufacturing Record\n"
            "\nPart 4  Summary of the assessment of supporting documentation\n"
            "a) Manufacturing authorization and GMP certificate.\n"
            "\nPart 5  Conclusion – Desk assessment outcome\n"
            "Based on the previous USFDA inspection and the GMP evidence received and "
            "reviewed, the site is considered to be operating at an acceptable level of "
            "compliance with WHO GMP guidelines.\n"
            "\nPart 6  List of guidelines referenced in this inspection report\n"
            "1. WHO good manufacturing practices, TRS No. 986.\n")

    def test_sra_wla_wording_is_recognized_as_reliance(self) -> None:
        """실측 회귀(ShaanxiHanjiang·SymedLabs): WHO 는 같은 자리에 **SRA/WLA**
        (WLA = WHO-Listed Authority — SRA/NRA 를 대체하는 현행 용어)도 쓴다. 어구 하나에만
        걸었더니 kind 가 findings 로 오판돼 reliance 결론 탐색이 아예 안 돌았고, outcome 에
        Part 3(과거 실사 요약 + 약어표)이 실렸다. 172건 전수: SRA/NRA 37 · SRA/WLA 2."""
        rep = w.extract_whopir_report(
            self._wla_text("Summary of SRA/WLA inspection evidence considered"))
        assert rep is not None
        self.assertEqual(rep["report_kind"], "reliance")
        self.assertTrue(rep["outcome"].startswith("Based on the previous USFDA"),
                        rep["outcome"][:70])
        self.assertNotIn("Abbreviations", rep["outcome"])

    def test_desk_conclusion_heading_alone_signals_reliance(self) -> None:
        """kind 판정을 Part 2 어구 **하나**에 걸면 용어가 또 바뀔 때 같은 사고가 난다.
        desk assessment 결론 표제의 실재를 두 번째 신호로 둔다 — 어구가 어떻게 바뀌든
        "desk assessment 로 결론냈다"는 사실 자체가 곧 reliance 다."""
        rep = w.extract_whopir_report(
            self._wla_text("Summary of regulatory inspection evidence considered"))
        assert rep is not None
        self.assertEqual(rep["report_kind"], "reliance")
        self.assertTrue(rep["outcome"].startswith("Based on the previous USFDA"))

    def test_cross_reference_part_marker_is_not_taken_as_conclusion(self) -> None:
        """실측 회귀(Dalian Institute): 본문의 "…as described in Part 3 below." 라는
        상호참조를 결론 마커로 채택해 outcome 이 "below. All toxic/hazardous reagents…"
        로 시작했고, 본문도 그 지점에서 잘려 섹션 13개를 잃었다(7 vs 20). 진짜 표제 뒤엔
        대문자가 오고 상호참조 뒤엔 소문자로 문장이 이어진다."""
        text = ("Part 2\nSummary of the findings and comments\n"
                "\n1. Quality management\n"
                + ("The system was reviewed. " * 8)
                + "Waste handling is described in Part 3 below. "
                + ("Further detail was verified. " * 6) + "\n"
                "\n2. Personnel\n" + ("Staffing was adequate. " * 8) + "\n"
                "\nPart 3\nConclusion – Inspection outcome\n"
                "Based on the areas inspected, the site was considered to be operating "
                "at an acceptable level of compliance with WHO GMP.\n"
                "\nPart 4\nList of guidelines referenced in the inspection report\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertTrue(rep["outcome"].startswith("Based on the areas inspected"),
                        rep["outcome"][:60])
        self.assertEqual([s["title"] for s in rep["sections"]],
                         ["Quality management", "Personnel"])

    def test_guideline_list_is_not_published_as_the_conclusion(self) -> None:
        """실측 회귀(AnaCipher CRO): Part 3 이 없어 Part 4 를 결론으로 잡는 폴백이
        **참고문헌 목록**을 결론 자리에 실었다(1,134자 서지). 진짜 결론은 번호가 평탄화로
        사라진 "Conclusion – inspection" 표제로 뒤에 따로 있었다."""
        text = ("Part 2\nSUMMARY OF THE FINDINGS AND COMMENTS\n"
                "\n1. Organization and management\n" + ("The CRO was organized. " * 8) + "\n"
                "\nPart 4\nList of guidelines referenced in the inspection report\n"
                "1. Guidance for organizations performing in vivo bioequivalence studies.\n"
                "\nPart\nConclusion – inspection\n\n"
                "Based on the areas inspected, the studies were considered to have been "
                "conducted at an acceptable level of compliance with WHO GCP/GLP/BE "
                "guidelines.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertTrue(rep["outcome"].startswith("Based on the areas inspected"),
                        rep["outcome"][:60])
        self.assertNotIn("Guidance for organizations", rep["outcome"])
        self.assertEqual(len(rep["sections"]), 1)

    def test_reliance_without_conclusion_heading_publishes_no_outcome(self) -> None:
        """결론 표제를 못 찾으면 outcome 은 빈다 — 과거 실사 요약을 결론이라 싣던 것이
        이번 사고다(읽은 척 금지). 인용 실사 목록만으로 카드는 산다."""
        text = (
            "Part 2\nSummary of SRA/NRA inspection evidence considered\n"
            "Inspecting authority   EDQM\nDates of\ninspection: 12-15 March 2025\n"
            "\nPart 3  Summary of the last WHO inspection\nDecoy narrative only.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual(rep["outcome"], "")
        self.assertEqual(len(rep["reliance"]), 1)

    def test_missing_part_boundaries_returns_none(self) -> None:
        self.assertIsNone(w.extract_whopir_report("본문에 Part 경계가 없는 문서"))
        self.assertIsNone(w.extract_whopir_report(""))

    # ── [섹션 회수율 2026-08-24] 실측 3서식 회귀 — MSN 급 상세가 다른 카드에서 비던 원인 ──
    def test_duplicate_source_numbering_does_not_break_the_chain(self) -> None:
        """실측 회귀(Chongqing): WHO PDF 자체가 3장을 "2."로 오기재 — 연속 번호 요구가
        그 지점에서 사슬을 끊어 15개 중 2개만 남겼다. ±1 관용(빈 줄 선행 한정)으로 잇고,
        반복 번호는 사슬 값으로 보정한다(번역 키 s<번호> 충돌 방지)."""
        text = ("Part 2\n\nSummary.\n"
                "\n1. Quality management\n" + ("Reviewed in detail. " * 8) + "\n"
                "\n2. Personnel\n" + ("Staffing was adequate. " * 8) + "\n"
                "\n2. Buildings and facilities\n" + ("Premises were suitable. " * 8) + "\n"
                "\n4. Process equipment\n" + ("Equipment was qualified. " * 8) + "\n"
                "\nPart 3\nOutcome text.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual([(s["no"], s["title"]) for s in rep["sections"]],
                         [("1", "Quality management"), ("2", "Personnel"),
                          ("3", "Buildings and facilities"), ("4", "Process equipment")])

    def test_dotless_first_heading_is_recognized(self) -> None:
        """실측 회귀(Keming): 1번 표제만 "1 Quality management"(점 없음)로 적혀 있어
        사슬이 시작조차 못 했다(15→0 — 발행 카드가 결론만 실림)."""
        text = ("Part 2\n\nSummary of the findings and comments (where applicable)\n"
                "1 Quality management\n" + ("A quality system was in place. " * 12) + "\n"
                "\n2. Personnel\n" + ("Personnel were qualified. " * 8) + "\n"
                "\nPart 3\nOutcome text.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual([(s["no"], s["title"]) for s in rep["sections"]],
                         [("1", "Quality management"), ("2", "Personnel")])

    def test_cro_template_without_part3_uses_part4_conclusion(self) -> None:
        """실측 회귀(ACDIMA·CRO/BE 서식): Part 3 이 없고 결론이 Part 4, 참고문헌이 Part 5 —
        "Part 3 필수" 전제가 항목 21개짜리 보고서의 구조화를 통째로 포기하게 했다."""
        text = ("Part 2\nSUMMARY OF THE FINDINGS AND COMMENTS\n"
                "\n1. Organization and management\n" + ("The CRO was organized. " * 8) + "\n"
                "\n2. Computer systems\n" + ("Systems were validated. " * 8) + "\n"
                "\nPart 4\nConclusion – Inspection outcome\n"
                "The CRO was considered to be operating at an acceptable level.\n"
                "\nPart 5\nList of guidelines referenced in the inspection report.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual(rep["report_kind"], "findings")
        self.assertEqual(len(rep["sections"]), 2)
        self.assertIn("acceptable level", rep["outcome"])
        self.assertNotIn("List of guidelines", rep["outcome"])

    def test_chain_continuity_keeps_short_unblanked_section(self) -> None:
        """실측 회귀(ACDIMA "4. Archive facilities" 249자·빈 줄 없음): 사슬이 2개 이상
        이어졌으면 정확한 다음 번호 자체가 증거 — 여기서 끊으면 나머지 전부를 잃는다."""
        text = ("Part 2\n\nSummary.\n"
                "\n1. Quality management\n" + ("Reviewed in detail. " * 8) + "\n"
                "\n2. Personnel\n" + ("Staffing was adequate. " * 8) + "\n"
                "3. Archive facilities\nAccess control was in place.\n"
                "\n4. Premises\n" + ("Premises were suitable. " * 8) + "\n"
                "\nPart 3\nOutcome text.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual([s["title"] for s in rep["sections"]],
                         ["Quality management", "Personnel", "Archive facilities", "Premises"])

    def test_chain_starts_at_min_number_when_first_heading_missing(self) -> None:
        """1번 표제가 후보에 아예 없는 서식 — 최소 번호에서 시작한다(종전 expect=1 고정은
        1번 미검출 = 전 항목 유실이었다)."""
        text = ("Part 2\n\nSummary.\n"
                "\n2. Personnel\n" + ("Staffing was adequate. " * 8) + "\n"
                "\n3. Premises\n" + ("Premises were suitable. " * 8) + "\n"
                "\nPart 3\nOutcome text.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertEqual([s["no"] for s in rep["sections"]], ["2", "3"])


class WhopirHeadingAnchorTest(unittest.TestCase):
    """[2026-08-25 후속] #793 이 남긴 잔여 — 표제를 '정의'만 하고 앵커로 못 쓴 자리들.

    #793 은 excerpt 를 문장 시작으로 되감아 **문법**을 고쳤다. 그런데 172건 전수에서
    findings 132건은 여전히 낱말 앵커에 끌려가 보고서마다 똑같은 마무리 보일러플레이트에서
    시작했다 — 문장으로 시작하지만 어느 사이트인지 알 수 없는 300자다. 여기서는 그 구간이
    **실제 지적 항목**을 담는지를 검사한다(형식이 아니라 내용).
    """

    def test_findings_excerpt_carries_findings_not_closing_boilerplate(self) -> None:
        """지적 요약 표제 뒤가 곧 번호 매긴 항목이다 — 거기서 시작해야 근거가 된다.

        낱말 앵커는 문서 어디든 먼저 나오는 `non-compliance`/`deficiencies` 에 걸린다.
        실측 172건에서 53건이 "All the non-compliances observed during the inspection…
        will remain valid for 3 years"(전 보고서 공통 마무리)에서 시작했다.
        """
        text = ("WHO Public Inspection Report\n\nPart 1\nGeneral information\n"
                "Name of manufacturer: Example Pharma Ltd\n"
                "All the non-compliances observed during the inspection were "
                "addressed to a satisfactory level prior to publication.\n"
                "\nPart 2\nSummary of the findings and comments\n"
                "\n1. Pharmaceutical quality system\n"
                "The site had a quality system that met WHO GMP requirements.\n")
        ex = w._extract_whopir_excerpt(text)
        self.assertTrue(ex.startswith("Summary of the findings and comments"), ex[:70])
        self.assertIn("quality system that met WHO GMP", ex)
        self.assertNotIn("Example Pharma Ltd", ex)          # 표지 유입 금지

    def test_lowercase_conclusion_bait_is_not_taken_as_a_heading(self) -> None:
        """Part 3 표 셀이 평탄화되면 "conclusion of" 라는 줄이 생긴다(172건 중 24건 보유).

        `_WHOPIR_CONCL_HEAD_RE` 가 re.I 면 이 미끼가 표제로 인정돼 진짜 결론보다 앞서
        걸린다 — Farmabios 의 excerpt 가 과거 실사 요약 한가운데서 시작한 원인이다.
        실측 표제 변형 12종은 전부 대문자로 시작하고 미끼 4종은 전부 소문자다.

        픽스처에서 `deficiencies`·`non-compliance` 를 **일부러 뺐다**. 그 낱말이 있으면
        낱말 앵커가 먼저 걸려 이 검사가 대조군에서도 통과해 버린다(발화하지 않는 가드).
        """
        self.assertFalse(w._WHOPIR_CONCL_HEAD_RE.match("conclusion of"))
        self.assertTrue(w._WHOPIR_CONCL_HEAD_RE.match("Conclusion – inspection"))
        text = ("WHO Public Inspection Report\n\nPart 1\nGeneral information\n"
                "\nPart 2\nSummary of SRA/NRA inspection evidence considered\n"
                "\nPart 3\nSummary of the last WHO inspection\n"
                "Date and\nconclusion of\nmost recent\nWHO\ninspection\n"
                "The site was not previously inspected onsite by WHO.\n"
                "\nPart 5\nConclusion – Desk assessment outcome\n"
                "Based on the GMP evidence received and reviewed, a desk assessment "
                "is acceptable in lieu of a WHO onsite inspection.\n")
        ex = w._extract_whopir_excerpt(text)
        self.assertTrue(ex.startswith("Conclusion – Desk assessment outcome"), ex[:60])
        self.assertNotIn("not previously inspected onsite", ex)

    def test_desk_conclusion_still_wins_over_the_findings_heading(self) -> None:
        """표제 앵커를 새로 추가할 때 **깨지기 쉬운 것은 이미 맞던 쪽**이다.

        #793 이 세운 성질 — reliance 39/39 가 desk 결론에 착지한다 — 를 회귀로 박는다.
        findings 표제 앵커가 desk 결론보다 먼저 걸리면 그 39건이 통째로 과거 실사 요약
        쪽으로 되돌아간다. 그래서 desk 판정을 **첫 번째** 순위에 두고, 두 표제가 한
        문서에 같이 있는 픽스처로 우선순위 자체를 고정한다.
        """
        text = ("WHO Public Inspection Report\n\nPart 1\nGeneral information\n"
                "\nPart 2\nSummary of SRA/WLA inspection evidence considered\n"
                "\nPart 3\nSummary of the findings and comments\n"
                "The previous SRA inspection findings were reviewed.\n"
                "\nPart 5\nConclusion – Desk assessment outcome\n"
                "Based on the GMP evidence received and reviewed, a desk assessment "
                "is acceptable in lieu of a WHO onsite inspection.\n")
        ex = w._extract_whopir_excerpt(text)
        self.assertTrue(ex.startswith("Conclusion – Desk assessment outcome"), ex[:60])
        self.assertNotIn("previous SRA inspection findings were reviewed", ex)

    def test_gmp_qualified_guideline_list_also_triggers_the_fallback(self) -> None:
        """서지 표제의 한정어는 실측 3종이다 — WHO / GMP / 없음.

        WHO 만 열어 두면 `List of GMP Guidelines referenced` 서식에서 이 폴백이 침묵해
        서지가 결론 자리에 그대로 남는다(#793 이 AnaCipher 한 건으로만 확인해 생긴 사각).
        """
        self.assertTrue(w._WHOPIR_BIBLIO_RE.match(
            "List of GMP Guidelines referenced in the inspection report"))
        self.assertTrue(w._WHOPIR_BIBLIO_RE.match(
            "List of guidelines referenced in the inspection report"))
        text = ("WHO Public Inspection Report\n\nPart 1\nGeneral information\n"
                "\nPart 2\nSummary of the findings and comments\n"
                "\n1. Organization and management\n"
                + ("The organization was reviewed in detail. " * 8) + "\n"
                "\nPart\nConclusion – inspection\n"
                "Based on the areas inspected, the CRO was considered to be operating "
                "at an acceptable level of compliance.\n"
                "\nPart 4\nList of GMP Guidelines referenced in the inspection report\n"
                "1. WHO good practices for pharmaceutical quality control laboratories.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertTrue(rep["outcome"].startswith("Based on the areas inspected"))
        self.assertNotIn("good practices for pharmaceutical", rep["outcome"])

    def test_conclusion_heading_never_rides_into_the_outcome_body(self) -> None:
        """표제는 카드가 따로 붙인다 — outcome 본문이 표제로 시작하면 중복이다.

        벗김 규칙이 `Conclusion – (Desk assessment|Inspection) outcome` 한 형태만 알아서
        172건 중 20건(발행 4장 포함)이 표제를 본문에 이고 있었다 — 카드 결론이 "Initial
        conclusion – Inspection outcome Based on the areas inspected…" 로 시작한다.
        """
        for head in ("Conclusion – Inspection outcome",      # en dash(실측 다수)
                     "Conclusion - Inspection outcome",      # ASCII 하이픈(실측 1건)
                     "Conclusion — Inspection outcome",      # em dash
                     "Initial conclusion – Inspection outcome",
                     "Final conclusion – Inspection outcome",
                     "FINAL CONCLUSION – INSPECTION OUTCOME",   # 전대문자(실측 1건)
                     "Conclusion – Desk assessment outcome",
                     "Conclusion – inspection",
                     "Conclusion"):
            text = ("WHO Public Inspection Report\n\nPart 1\nGeneral information\n"
                    "\nPart 2\nSummary of the findings and comments\n"
                    "\n1. Quality management\n"
                    + ("The team reviewed the quality manual. " * 8) + "\n"
                    "\nPart 3\n" + head + "\n"
                    "Based on the areas inspected, the manufacturer was considered to "
                    "be operating at an acceptable level of compliance with WHO GMP.\n")
            rep = w.extract_whopir_report(text)
            assert rep is not None
            with self.subTest(head=head):
                self.assertTrue(rep["outcome"].startswith("Based on the areas"),
                                "%r -> %r" % (head, rep["outcome"][:60]))

    def test_conclusion_word_in_prose_is_not_stripped(self) -> None:
        """안전장치의 음성 검사 — 표제가 아니라 **산문 첫 낱말**이면 건드리지 않는다.

        접두·꼬리를 전부 선택으로 열면 "Conclusion of the inspection team was…" 의 첫
        낱말까지 잘라 문장을 망가뜨릴 수 있다. 그래서 뒤가 대문자일 때만 벗기는데, 그
        조건은 `(?=[A-Z])` 로만 쓰면 re.I 때문에 **작동하지 않는다** — 실제로 첫 구현이
        이 문장을 잘랐고 그것을 잡은 것이 이 검사다.
        """
        text = ("WHO Public Inspection Report\n\nPart 1\nGeneral information\n"
                "\nPart 2\nSummary of the findings and comments\n"
                "\n1. Quality management\n"
                + ("The team reviewed the quality manual. " * 8) + "\n"
                "\nPart 3\nConclusion of the inspection team was recorded in full.\n")
        rep = w.extract_whopir_report(text)
        assert rep is not None
        self.assertIn("of the inspection team was recorded", rep["outcome"])
        self.assertFalse(rep["outcome"].startswith("of the "), rep["outcome"][:50])


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
                items, err = w._collect_whopir(RUN)
            # 보강은 중복 제거 뒤 별도 단계다(collect_intake 가 신규 항목만 넘긴다).
            w.enrich_whopir_items(items)
            return items, err
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


class WhopirEnrichAfterDedupTest(unittest.TestCase):
    """[중복 제거 후 보강 2026-07-27] fetch 예산이 목록 순서에 먹히지 않는지 고정.

    실측 회귀: WHO 목록은 최신순이 아니라 **알파벳순**이다(Accutest→ADVITY→Aizant… 사이에
    2023-09·2024-01 이 뒤섞여 있다). 종전처럼 수집 루프에서 목록 순서대로 받으면 cap 40 을
    매일 같은 앞쪽 40건이 다 써서, 새로 올라온 뒤쪽 보고서는 카드는 나오되 상세가 영영 빈다.
    """

    def _items(self, n):
        out = []
        for i in range(n):
            it = _dummy_item(f"e{i}")
            it.raw_payload.update({"channel": "whopir",
                                   "pdf_url": f"https://x/whopir-{i}.pdf"})
            out.append(it)
        return out

    def _enrich(self, items, stub):
        orig_fetch, orig_delay = w._fetch_whopir_detail, w.WHOPIR_EXCERPT_DELAY_SECONDS
        w._fetch_whopir_detail = stub
        w.WHOPIR_EXCERPT_DELAY_SECONDS = 0
        try:
            with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
                return w.enrich_whopir_items(items)
        finally:
            w._fetch_whopir_detail, w.WHOPIR_EXCERPT_DELAY_SECONDS = orig_fetch, orig_delay

    def test_only_given_items_are_fetched(self) -> None:
        """넘겨받지 않은 항목은 절대 받지 않는다 — 호출부가 신규만 넘기므로 전수 보강."""
        calls = []
        rep = {"type": "whopir_report", "report_kind": "findings", "outcome": "ok",
               "sections": [{"no": "1", "title": "T", "text": "x"}]}

        def _stub(url):
            calls.append(url)
            return ("excerpt", rep, "ok")

        items = self._items(3)
        health = self._enrich(items[1:], _stub)          # 0번은 이미 수집된 항목이라 치고 제외
        self.assertEqual(calls, ["https://x/whopir-1.pdf", "https://x/whopir-2.pdf"])
        self.assertNotIn("whopir_report", items[0].raw_payload)
        self.assertEqual(items[2].raw_payload["whopir_report"], rep)
        self.assertEqual(health["structured"], 2)
        self.assertFalse(health["capped"])

    def test_cap_applies_to_new_items_only_and_is_reported(self) -> None:
        orig_cap = w.WHOPIR_EXCERPT_MAX_ITEMS
        w.WHOPIR_EXCERPT_MAX_ITEMS = 2
        try:
            calls = []
            health = self._enrich(
                self._items(5), lambda u: (calls.append(u), ("e", None, "no-structure"))[1])
        finally:
            w.WHOPIR_EXCERPT_MAX_ITEMS = orig_cap
        self.assertEqual(len(calls), 2)
        self.assertTrue(health["capped"], "cap 도달이 관측되지 않으면 조용한 누락이 된다")

    def test_flag_off_fetches_nothing(self) -> None:
        def _must_not_call(url):
            raise AssertionError("flag off 인데 PDF fetch 가 호출됐다")

        items = self._items(2)
        orig = w._fetch_whopir_detail
        w._fetch_whopir_detail = _must_not_call
        try:
            with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "false"}):
                health = w.enrich_whopir_items(items)
        finally:
            w._fetch_whopir_detail = orig
        self.assertFalse(health["enabled"])
        self.assertEqual(health["attempted"], 0)

    def test_non_whopir_items_are_ignored(self) -> None:
        """WHO 소스에는 NOC·news 도 섞여 있다 — whopir 채널만 건드린다."""
        other = _dummy_item("noc")
        other.raw_payload.update({"channel": "noc", "pdf_url": "https://x/noc.pdf"})
        calls = []
        self._enrich([other], lambda u: (calls.append(u), ("e", None, "ok"))[1])
        self.assertEqual(calls, [])


# ── [WHOPIR 실사일 2026-08-10] ───────────────────────────────────────────────
# 종전 `_parse_text_date(앵커 텍스트)` 는 대상이 틀려 늘 ""를 냈다(→ published_date 결측
# → raw_signals POST 자체가 안 일어남). 아래 fixture 의 마크업은 2026-08-10 라이브
# 목록에서 채록한 형태다(THEME DEBUG 주석·중첩 field div 포함).
def _whopir_teaser(href: str, title: str, dates_html: str) -> str:
    """라이브 티저 1행 축약 — <a> 는 제조소명만, 날짜는 앵커 **바깥 형제 필드**."""
    return (
        '<div class="views-row"><article data-history-node-id="37454" '
        'class="node node--type-whopir node--view-mode-teaser">'
        '<div class="node__content"><div class="file-teaser">'
        f'<a href="{href}"><span class="field field--name-title '
        f'field--label-hidden">{title}</span></a>'
        '<div class="field field--name-field-whopir-inspection-dates '
        f'field--type-daterange field--label-hidden field__item">{dates_html}</div>'
        '<div class="country-city">China</div>'
        '</div></div></article></div>'
    )


_WHOPIR_TIME_ROW = _whopir_teaser(
    "/prequal/sites/default/files/whopir_files/I-05281-WHOPIR-Keming.pdf",
    "Zhejiang Keming Biopharmaceuticals",
    '<time datetime="2026-01-26T12:00:00Z" class="datetime">26  January,  2026</time>'
    ' - <time datetime="2026-01-28T12:00:00Z" class="datetime">28  January,  2026</time>',
)


class WhopirInspectionDateTextTest(unittest.TestCase):
    """_parse_inspection_dates_text — 관측된 표기 전 형태에서 **시작일**을 낸다."""

    def test_observed_range_formats_yield_start_date(self) -> None:
        cases = {
            "26 - 28 January 2026": "2026-01-26",          # 일 범위 + 월/연 뒤에 한 번
            "From 7 to 11 April 2025": "2025-04-07",       # 전치사형
            "15-17 July 2024 Bioanalytical site": "2024-07-15",   # 꼬리말 동반
            "18 – 22 March 2024": "2024-03-18",            # en-dash
            "26  January,  2026 - 28  January,  2026": "2026-01-26",  # Drupal 렌더(양끝 완전)
            "22 June, 2025": "2025-06-22",                 # 단일일
            "28 January - 2 February 2026": "2026-01-28",  # 달 넘김(앞머리에 월)
            "28 December - 3 January 2026": "2025-12-28",  # 해 넘김 → 시작은 전년도
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(w._parse_inspection_dates_text(text), expected)

    def test_unreadable_text_returns_empty_not_a_guess(self) -> None:
        # 못 읽으면 ""(추정 금지) — 호출부가 '미확인'으로 표기하고 health 에 집계한다.
        for text in ("", "Not specified", "Bioanalytical site", "12/2026", "2026-01-26"):
            with self.subTest(text=text):
                self.assertEqual(w._parse_inspection_dates_text(text), "")

    def test_day_less_text_degrades_to_month_precision(self) -> None:
        # 일이 없는 표기("January 2026")는 `_parse_text_date`(NOC 와 공용)의 기존 관례대로
        # 월 정밀도(1일)로 떨어진다. 실사일 필드는 daterange 라 라이브에선 안 나오지만,
        # 앵커 텍스트 폴백 경로에는 남아 있어 계약을 명시해 둔다.
        self.assertEqual(w._parse_inspection_dates_text("January 2026"), "2026-01-01")
        self.assertEqual(w._parse_inspection_dates_text("32 January 2026"), "2026-01-01")


class WhopirRowDateTest(unittest.TestCase):
    """_collect_whopir — 실사일 추출(마크업 1순위·텍스트 폴백)·미추출 관측·전건 sentinel."""

    def test_time_element_datetime_is_used_as_inspection_start(self) -> None:
        with _Patched(_WHOPIR_TIME_ROW):
            items, err = w._collect_whopir(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].date_iso, "2026-01-26")      # 범위의 시작일
        # 카드에는 시작일만 싣지만 원문 표기(범위)는 provenance 로 남긴다.
        self.assertIn("28", items[0].raw_payload["inspection_dates"])
        self.assertEqual(w.LAST_HEALTH["whopir_dates"]["dateless"], 0)

    def test_text_fallback_when_time_attribute_disappears(self) -> None:
        # <time datetime> 이 사라지는 드리프트 — 같은 필드의 표시 텍스트로 살린다.
        html = _whopir_teaser(
            "/prequal/sites/default/files/whopir_files/maker-x.pdf",
            "Maker X", "<span>15-17 July 2024 Bioanalytical site</span>")
        with _Patched(html):
            items, err = w._collect_whopir(RUN)
        self.assertIsNone(err)
        self.assertEqual(items[0].date_iso, "2024-07-15")

    def test_row_without_date_field_is_counted_not_silently_empty(self) -> None:
        # 날짜 필드가 없는 행(앵커 텍스트에도 날짜 없음) → date_iso=""(추정 금지) +
        # health 에 집계. 이 항목은 raw_signals 가 만들어지지 않으므로 침묵하면 안 된다.
        html = (_WHOPIR_TIME_ROW
                + '<a href="/prequal/sites/default/files/whopir_files/maker-y.pdf">'
                  'Maker Y</a>')
        with _Patched(html):
            items, err = w._collect_whopir(RUN)
        self.assertIsNone(err)                                  # 부분 결손은 graceful
        by_url = {it.official_url.rsplit("/", 1)[-1]: it for it in items}
        self.assertEqual(by_url["maker-y.pdf"].date_iso, "")
        self.assertNotIn("inspection_dates", by_url["maker-y.pdf"].raw_payload)
        health = w.LAST_HEALTH["whopir_dates"]
        self.assertEqual((health["total"], health["dated"], health["dateless"]), (2, 1, 1))
        self.assertTrue(health["samples"])

    def test_all_rows_dateless_is_an_error_not_a_silent_zero(self) -> None:
        # 전건 미추출 = 목록 마크업 변경 신호(실측 168행은 전건에 날짜가 있다). 종전엔
        # 이 상태가 곧 'WHO raw_signals 0건'이었는데 아무 경보도 없었다.
        html = ('<a href="/prequal/sites/default/files/whopir_files/maker-y.pdf">'
                'Maker Y</a>')
        with _Patched(html):
            items, err = w._collect_whopir(RUN)
        self.assertEqual(len(items), 1)                         # 항목은 링크 카드로 유지
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("실사일 전건 미추출", err)
        self.assertIn("수동 확인 필요", err)

    def test_dates_health_survives_excerpt_enrichment(self) -> None:
        # 보강 단계가 LAST_HEALTH 를 통째로 갈아끼우면 이 계기가 조용히 사라진다.
        with _Patched(_WHOPIR_TIME_ROW):
            items, _err = w._collect_whopir(RUN)
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "false"}):
            w.enrich_whopir_items(items)
        self.assertIn("whopir_dates", w.LAST_HEALTH)
        self.assertIn("whopir_excerpt", w.LAST_HEALTH)


class WhopirRawIsBareTest(unittest.TestCase):
    """[보강본 가림 방지 2026-08-24] bare 재수집분 판정 — inmemory 캐시 제외 대상.

    재수집된 WHOPIR 항목의 bare raw 가 inmemory 캐시로 Notion 의 보강본(whopir_report)을
    가려 주간 스캐폴드 상세가 통째로 비던 결함(08-03 이후 발행분 전수)의 판정 함수."""

    _BARE = {"channel": "whopir", "anchor_text": "Some Site",
             "pdf_url": "https://extranet.who.int/x.pdf", "list_page": "https://x"}

    def test_bare_whopir_payload_is_bare(self) -> None:
        self.assertTrue(w.whopir_raw_is_bare(dict(self._BARE)))

    def test_enriched_report_not_bare(self) -> None:
        raw = dict(self._BARE, whopir_report={"type": "whopir_report"})
        self.assertFalse(w.whopir_raw_is_bare(raw))

    def test_excerpt_only_not_bare(self) -> None:
        # 보강은 시도했으나 구조화가 안 된 항목(excerpt 만) — Notion 저장본과 같으므로 캐시 안전.
        raw = dict(self._BARE, whopir_excerpt="deficiencies …")
        self.assertFalse(w.whopir_raw_is_bare(raw))

    def test_non_whopir_channels_not_bare(self) -> None:
        self.assertFalse(w.whopir_raw_is_bare({"channel": "rss", "guid": "x"}))
        self.assertFalse(w.whopir_raw_is_bare(None))
        self.assertFalse(w.whopir_raw_is_bare("not-a-dict"))


if __name__ == "__main__":
    unittest.main()
