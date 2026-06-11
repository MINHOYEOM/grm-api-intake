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
        g._extract_pdf_text = lambda data: (_WHOPIR_TEXT, "pdf-ok")
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
        g._extract_pdf_text = lambda data: ("", "pdf-encrypted")
        try:
            excerpt, status = w._fetch_whopir_excerpt("https://x/whopir-z.pdf")
        finally:
            w.http_get_bytes, g._extract_pdf_text = orig_bytes, orig_extract
        self.assertEqual(excerpt, "")
        self.assertEqual(status, "pdf-encrypted")


class WhopirCollectExcerptGateTest(unittest.TestCase):
    """_collect_whopir — flag on/off · excerpt 기록 · graceful degrade · health."""

    def _run(self, fetch_stub):
        orig_fetch, orig_delay = w._fetch_whopir_excerpt, w.WHOPIR_EXCERPT_DELAY_SECONDS
        w._fetch_whopir_excerpt = fetch_stub
        w.WHOPIR_EXCERPT_DELAY_SECONDS = 0
        try:
            with _Patched(_WHOPIR_HTML):
                return w._collect_whopir(RUN)
        finally:
            w._fetch_whopir_excerpt, w.WHOPIR_EXCERPT_DELAY_SECONDS = orig_fetch, orig_delay

    def test_flag_on_writes_excerpt_to_raw_payload(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
            items, err = self._run(lambda url: ("Summary of the deficiencies …", "ok"))
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertEqual(it.raw_payload.get("whopir_excerpt"),
                             "Summary of the deficiencies …")
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["ok"], 2)
        self.assertEqual(w.LAST_HEALTH["whopir_excerpt"]["failed"], 0)

    def test_flag_on_failure_is_graceful_key_omitted_item_kept(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WHOPIR_EXCERPT": "true"}):
            items, err = self._run(lambda url: ("", "fetch-fail:boom"))
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
