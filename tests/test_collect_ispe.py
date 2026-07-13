#!/usr/bin/env python3
"""ISPE iSpeak RSS 수집 — 전문지 브리핑 소스확장 2026-07-13(flag ENABLE_ISPE, 기본 off).

ECA(gmp-compliance.org)에 이은 두 번째 "전문지 브리핑" 소스. RSS 2.0(Drupal 생성) →
keep_item 관련성 필터(_is_ispe_gmp_relevant, grm_taxonomy.compute_relevance 어휘 재사용 —
새 키워드 리스트 미발명) → 기사 본문 excerpt 흡수(ENABLE_ISPE_ARTICLE_EXCERPT, ECA 와
동형이나 raw_payload 키는 제네릭 "article_excerpt")까지 실네트워크 0 특성화 테스트.

실측 피드(2026-07-13) 구조를 본뜬 mock Drupal 노드 teaser HTML 사용: 저자 span·<time>·
story-type div("iSpeak Blog")·<h1> 제목 중복·배너 <img> 가 앞쪽에 섞이고, 실제 요약문은
field--name-field-description div 안에 있다.
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from unittest.mock import patch

import requests
from xml.sax.saxutils import escape as _xml_escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci  # noqa: E402
from grm_common import HTTPClientError  # noqa: E402

START = date(2026, 6, 26)
END = date(2026, 7, 3)


def _teaser(inner_p: str, *, with_field_div: bool = True) -> str:
    """실측 Drupal 노드 teaser 렌더를 본뜬 <description> 내부 HTML 조립."""
    header = (
        '<span class="author">By Jane Doe</span>'
        '<time datetime="2026-07-01T12:00:00Z">July 1, 2026</time>'
        '<div class="story-type">iSpeak Blog</div>'
        '<h1>Duplicate Title</h1>'
        '<img src="https://ispe.org/sites/default/files/banner.jpg" alt="banner"/>'
    )
    if with_field_div:
        body = (
            '<div class="field field--name-field-description '
            'field--type-text-long field--label-hidden field__item">'
            f'<p>{inner_p}</p></div>'
        )
    else:
        body = f'<p>{inner_p}</p>'
    return header + body


_GMP_WATER_DESC = _teaser(
    "Water systems are critical to pharmaceutical manufacturing and must comply "
    "with GMP requirements for design, validation, and ongoing monitoring."
)
_BOARD_DESC = _teaser(
    "ISPE members can now review the slate of candidates running for the "
    "2026 Board of Directors election."
)
_SPOTLIGHT_DESC = _teaser(
    "This month's Member Spotlight features a longtime ISPE Affiliate volunteer "
    "and her journey through the pharmaceutical engineering community."
)
_AUDIT_TRAIL_DESC = _teaser(
    "GxP computer system validation and audit trail review are central to "
    "data integrity programs required under 21 CFR Part 11."
)

# 실측 피드는 <description> 안에 teaser HTML 을 엔티티 이스케이프해서 담는다(raw 태그를
# 그대로 XML 자식요소로 넣으면 ElementTree 가 <description> 의 자식으로 파싱해버려 .text 가
# 비어버린다 — 실제 Drupal RSS 산출과 동일하게 엔티티 이스케이프해 임베드한다).
ISPE_RSS_MIXED = f"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel>
  <item>
    <title>GMP Water System Design and Validation</title>
    <link>https://ispe.org/pharmaceutical-engineering/ispeak/water-system-gmp</link>
    <description>{_xml_escape(_GMP_WATER_DESC)}</description>
    <pubDate>Wed, 01 Jul 2026 12:00:00 +0000</pubDate>
    <dc:creator>Jane Doe</dc:creator>
    <guid isPermaLink="false">314001 at https://ispe.org</guid>
    <comments>https://ispe.org/comment/314001</comments>
  </item>
  <item>
    <title>Meet Our 2026 Board of Directors Candidates</title>
    <link>https://ispe.org/pharmaceutical-engineering/ispeak/board-candidates-2026</link>
    <description>{_xml_escape(_BOARD_DESC)}</description>
    <pubDate>Wed, 01 Jul 2026 13:00:00 +0000</pubDate>
    <dc:creator>ISPE Staff</dc:creator>
    <guid isPermaLink="false">314002 at https://ispe.org</guid>
    <comments>https://ispe.org/comment/314002</comments>
  </item>
  <item>
    <title>Member Spotlight: A Career in Pharmaceutical Engineering</title>
    <link>https://ispe.org/pharmaceutical-engineering/ispeak/member-spotlight-july</link>
    <description>{_xml_escape(_SPOTLIGHT_DESC)}</description>
    <pubDate>Wed, 01 Jul 2026 14:00:00 +0000</pubDate>
    <dc:creator>ISPE Staff</dc:creator>
    <guid isPermaLink="false">314003 at https://ispe.org</guid>
    <comments>https://ispe.org/comment/314003</comments>
  </item>
  <item>
    <title>Data Integrity and Audit Trail Review Under 21 CFR Part 11</title>
    <link>https://ispe.org/pharmaceutical-engineering/ispeak/data-integrity-part-11</link>
    <description>{_xml_escape(_AUDIT_TRAIL_DESC)}</description>
    <pubDate>Wed, 01 Jul 2026 15:00:00 +0000</pubDate>
    <dc:creator>John Smith</dc:creator>
    <guid isPermaLink="false">314004 at https://ispe.org</guid>
    <comments>https://ispe.org/comment/314004</comments>
  </item>
</channel></rss>"""


def _ispe_rss_items(n: int) -> str:
    """윈도우 내 ISPE RSS 2.0 item n개(전건 keep_item 통과 — GMP 키워드 포함, cap 테스트용)."""
    items = "".join(
        f"<item><title>GMP process validation update {i}</title>"
        f"<link>https://ispe.org/pharmaceutical-engineering/ispeak/item-{i}</link>"
        f"<pubDate>Wed, 01 Jul 2026 0{i % 10}:00:00 +0000</pubDate>"
        f"<description>"
        f"{_xml_escape(_teaser('GMP process validation guidance item ' + str(i) + '.'))}"
        f"</description>"
        f"<guid isPermaLink=\"false\">{314100 + i} at https://ispe.org</guid></item>"
        for i in range(n)
    )
    return f'<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel>{items}</channel></rss>'


class _PatchXml:
    """ci.http_get_xml 을 콜러블로 교체하는 컨텍스트 매니저(tests/test_rss_generic.py 동형)."""
    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        self._orig = ci.http_get_xml
        ci.http_get_xml = self.fn
        return self

    def __exit__(self, *exc):
        ci.http_get_xml = self._orig
        return False


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


_ISPE_ARTICLE_HTML = (
    "<html><head><style>.x{color:red}</style></head><body>"
    "<nav>ISPE Home &gt; iSpeak</nav>"
    "<header>Site header text should not appear</header>"
    "<article><p>Pharmaceutical water systems require a lifecycle approach to GMP "
    "compliance, from design qualification through ongoing performance monitoring.</p>"
    "<p>Risk-based validation strategies help manufacturers focus resources on the "
    "highest-impact quality attributes.</p></article>"
    "<footer>Copyright ISPE &mdash; footer nav links</footer>"
    "<script>track();</script></body></html>"
)


class IspeTeaserTextTest(unittest.TestCase):
    """_ispe_teaser_text — field--name-field-description 우선 추출 + 폴백 + 절단."""

    def test_field_description_extracted_without_boilerplate(self) -> None:
        text = ci._ispe_teaser_text(_GMP_WATER_DESC)
        self.assertNotIn("<", text)
        self.assertNotIn("Jane Doe", text)          # 저자 span 제외
        self.assertNotIn("iSpeak Blog", text)         # story-type div 제외
        self.assertNotIn("Duplicate Title", text)     # 제목 중복 제외
        self.assertTrue(text.startswith("Water systems are critical"))

    def test_fallback_when_no_field_description_div(self) -> None:
        html = _teaser("Some raw teaser text without a wrapper div.", with_field_div=False)
        text = ci._ispe_teaser_text(html)
        # 폴백 경로는 보일러플레이트 완벽 제거를 시도하지 않는다(보수적·결정론 우선).
        self.assertIn("Some raw teaser text without a wrapper div.", text)
        self.assertNotIn("<", text)

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(ci._ispe_teaser_text(""), "")

    def test_capped_at_800_chars(self) -> None:
        html = _teaser("x" * 2000)
        text = ci._ispe_teaser_text(html)
        self.assertLessEqual(len(text), 800)


class IspeExtractTest(unittest.TestCase):
    """_extract_ispe — title/link/date_iso/guid/type_or_class 필드 매핑."""

    def _first_item_el(self) -> ET.Element:
        root = ET.fromstring(ISPE_RSS_MIXED)
        return root.find(".//item")

    def test_extract_fields(self) -> None:
        f = ci._extract_ispe(self._first_item_el(), "ispe", ci.ISPE_RSS_URL)
        self.assertEqual(f.title, "GMP Water System Design and Validation")
        self.assertEqual(
            f.link, "https://ispe.org/pharmaceutical-engineering/ispeak/water-system-gmp")
        self.assertEqual(f.date_iso, "2026-07-01")           # RFC822 pubDate 파싱
        self.assertEqual(f.guid, "314001 at https://ispe.org")
        self.assertEqual(f.type_or_class, "GMP News")
        self.assertEqual(f.category, "")
        self.assertTrue(f.description.startswith("Water systems are critical"))


class IspeRelevantFilterTest(unittest.TestCase):
    """_is_ispe_gmp_relevant — keep(GMP/품질) vs drop(협회 홍보성) 케이스."""

    def _fields(self, title: str, description: str) -> "ci._RssItemFields":
        return ci._RssItemFields(
            title=title, link="https://ispe.org/x", date_iso="2026-07-01",
            description=description, category="", type_or_class="GMP News",
            guid="g", raw_payload={},
        )

    def test_gmp_water_system_kept(self) -> None:
        f = self._fields("GMP Water System Design and Validation",
                          ci._ispe_teaser_text(_GMP_WATER_DESC))
        self.assertTrue(ci._is_ispe_gmp_relevant(f))

    def test_audit_trail_kept(self) -> None:
        f = self._fields("Data Integrity and Audit Trail Review Under 21 CFR Part 11",
                          ci._ispe_teaser_text(_AUDIT_TRAIL_DESC))
        self.assertTrue(ci._is_ispe_gmp_relevant(f))

    def test_board_of_directors_dropped(self) -> None:
        f = self._fields("Meet Our 2026 Board of Directors Candidates",
                          ci._ispe_teaser_text(_BOARD_DESC))
        self.assertFalse(ci._is_ispe_gmp_relevant(f))

    def test_member_spotlight_dropped(self) -> None:
        f = self._fields("Member Spotlight: A Career in Pharmaceutical Engineering",
                          ci._ispe_teaser_text(_SPOTLIGHT_DESC))
        self.assertFalse(ci._is_ispe_gmp_relevant(f))


class IspeCollectRssTest(unittest.TestCase):
    """collect_ispe_rss — collect_rss_feed 경유 end-to-end(keep_item 적용·doc_id·403 skip)."""

    def _assert_doc_id(self, item):
        expected = ci._stable_doc_id(item.source, item.headline,
                                     item.official_url, item.date_iso)
        self.assertEqual(item.document_id, expected)

    def test_keep_item_filters_promotional_items(self) -> None:
        with _PatchXml(lambda u, *a, **k: ET.fromstring(ISPE_RSS_MIXED)):
            items, err = ci.collect_ispe_rss(START, END)
        self.assertIsNone(err)
        titles = {it.headline for it in items}
        self.assertEqual(len(items), 2)   # Board/Spotlight 탈락
        self.assertIn("GMP Water System Design and Validation", titles)
        self.assertIn("Data Integrity and Audit Trail Review Under 21 CFR Part 11", titles)
        self.assertNotIn("Meet Our 2026 Board of Directors Candidates", titles)
        self.assertNotIn("Member Spotlight: A Career in Pharmaceutical Engineering", titles)
        for it in items:
            self.assertEqual(it.source, ci.SOURCE_ISPE)
            self.assertEqual(it.source_type, "Expert Secondary")
            self.assertEqual(it.type_or_class, "GMP News")
            self._assert_doc_id(it)

    def test_http_403_silent_skip(self) -> None:
        def fake(url, *a, **k):
            raise HTTPClientError(403, url, "forbidden")

        with _PatchXml(fake):
            items, err = ci.collect_ispe_rss(START, END)
        # Expert Secondary: 403 은 경고 없이 skip → ([], None)
        self.assertEqual(items, [])
        self.assertIsNone(err)


class IspeArticleExcerptGateTest(unittest.TestCase):
    """collect_ispe_rss — ENABLE_ISPE_ARTICLE_EXCERPT flag on/off · cap · 제네릭 raw 키."""

    def test_flag_off_skips_article_fetch_byte_identical(self) -> None:
        def _must_not_fetch(url, *a, **k):
            raise AssertionError(f"flag off 인데 기사 fetch 호출됨: {url}")

        with patch.dict(os.environ, {"ENABLE_ISPE_ARTICLE_EXCERPT": "false"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ISPE_RSS_MIXED)):
                with patch.object(ci.requests, "get", side_effect=_must_not_fetch):
                    items, err = ci.collect_ispe_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertNotIn("article_excerpt", it.raw_payload)

    def test_flag_default_off_when_unset(self) -> None:
        def _must_not_fetch(url, *a, **k):
            raise AssertionError(f"flag 미설정인데 기사 fetch 호출됨: {url}")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_ISPE_ARTICLE_EXCERPT", None)
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ISPE_RSS_MIXED)):
                with patch.object(ci.requests, "get", side_effect=_must_not_fetch):
                    items, err = ci.collect_ispe_rss(START, END)
        self.assertIsNone(err)
        for it in items:
            self.assertNotIn("article_excerpt", it.raw_payload)

    def test_flag_on_writes_generic_article_excerpt_key(self) -> None:
        with patch.dict(os.environ, {"ENABLE_ISPE_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ISPE_RSS_MIXED)):
                with patch.object(ci.requests, "get", return_value=_Resp(_ISPE_ARTICLE_HTML)):
                    with patch.object(ci.time, "sleep") as mock_sleep:
                        items, err = ci.collect_ispe_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        for it in items:
            excerpt = it.raw_payload.get("article_excerpt", "")
            self.assertTrue(excerpt.startswith("Pharmaceutical water systems require"))
            # ECA 전용 키는 기록되지 않는다(제네릭 키만 사용 — 설계 결정 §3).
            self.assertNotIn("eca_article_excerpt", it.raw_payload)
        mock_sleep.assert_called_with(ci.ECA_ARTICLE_EXCERPT_DELAY_SECONDS)

    def test_flag_on_fetch_failure_keeps_metadata_card(self) -> None:
        def _boom(url, *a, **k):
            raise requests.Timeout("slow")

        with patch.dict(os.environ, {"ENABLE_ISPE_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ISPE_RSS_MIXED)):
                with patch.object(ci.requests, "get", side_effect=_boom):
                    with patch.object(ci.time, "sleep"):
                        items, err = ci.collect_ispe_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertNotIn("article_excerpt", it.raw_payload)

    def test_cap_limits_fetch_to_first_n_items(self) -> None:
        n = ci.ECA_ARTICLE_EXCERPT_CAP + 3
        calls: list[str] = []

        def _get(url, *a, **k):
            calls.append(url)
            return _Resp(_ISPE_ARTICLE_HTML)

        with patch.dict(os.environ, {"ENABLE_ISPE_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(_ispe_rss_items(n))):
                with patch.object(ci.requests, "get", side_effect=_get):
                    with patch.object(ci.time, "sleep"):
                        items, err = ci.collect_ispe_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), n)                            # 목록은 cap 과 무관 전건
        self.assertEqual(len(calls), ci.ECA_ARTICLE_EXCERPT_CAP)    # fetch 만 cap 적용
        with_excerpt = sum(1 for it in items if it.raw_payload.get("article_excerpt"))
        self.assertEqual(with_excerpt, ci.ECA_ARTICLE_EXCERPT_CAP)


if __name__ == "__main__":
    unittest.main()
