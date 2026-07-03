#!/usr/bin/env python3
"""RSS/Atom 수집기(EMA·MHRA·PIC/S·ECA) 특성화 테스트 — 배치6 Phase3.

이 4종 수집기는 그동안 단위 테스트가 없었다(배치6 이전 커버리지 0). Phase3 에서
collect_rss_feed(spec) 제네릭으로 통합하며, 회귀 방어를 위해 각 수집기의 추출 결과를
고정한다. 특히 document_id(=dedup 키의 일부)는 값을 명시적으로 pin 한다 — 이 값이
바뀌면 기존 Notion row 와 전량 중복 적재되므로 **가장 중요한 불변식**이다.

http_get_xml 을 스텁 XML 로 몽키패치해 모든 추출 경로를 운동:
CDATA/tail link · dc:date 폴백 · 멀티피드 accumulate 오류 · Atom published/updated/content ·
ECA rss2/atom hybrid · ECA HTTPClientError silent skip · 윈도우 필터.
"""
import unittest
import xml.etree.ElementTree as ET
from datetime import date

import collect_intake as ci
from grm_common import HTTPClientError

START = date(2026, 6, 26)
END = date(2026, 7, 3)

EMA_SG = """<rss version="2.0"><channel>
  <item>
    <title>Nitrosamine guideline update</title>
    <link>https://www.ema.europa.eu/en/doc1</link>
    <pubDate>Wed, 01 Jul 2026 10:00:00 +0000</pubDate>
    <description>Draft on nitrosamine limits</description>
    <category>Scientific guideline</category>
    <guid>ema-guid-1</guid>
  </item>
  <item>
    <title>Old item</title>
    <link>https://www.ema.europa.eu/en/old</link>
    <pubDate>Mon, 01 Jan 2020 10:00:00 +0000</pubDate>
    <description>old</description>
  </item>
</channel></rss>"""

# <link/> 빈 요소 + tail 에 URL(일부 EMA 피드의 CDATA 표현) · category/guid 없음
EMA_INSP = """<rss version="2.0"><channel>
  <item>
    <title>Inspection notice</title>
    <link/>https://www.ema.europa.eu/en/insp-tail
    <pubDate>Thu, 02 Jul 2026 09:00:00 +0000</pubDate>
    <description>GMP inspection outcome</description>
  </item>
</channel></rss>"""

# pubDate 없음 → dc:date 폴백
EMA_NEWS = """<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
  <item>
    <title>News with dc date</title>
    <link>https://www.ema.europa.eu/en/news1</link>
    <dc:date>2026-07-03</dc:date>
    <description>news body</description>
    <category>News</category>
  </item>
</channel></rss>"""

MHRA_FEED = """<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Data integrity blog</title>
    <link rel="alternate" href="https://mhrainspectorate.blog.gov.uk/post1"/>
    <published>2026-07-02T08:00:00Z</published>
    <updated>2026-07-02T09:00:00Z</updated>
    <summary>Post about data integrity</summary>
    <category term="Data Integrity"/>
    <id>mhra-id-1</id>
  </entry>
  <entry>
    <title>Inspectorate update</title>
    <link href="https://mhrainspectorate.blog.gov.uk/post3"/>
    <published>2026-07-01T12:00:00Z</published>
    <content>update via content tag</content>
    <id>mhra-id-3</id>
  </entry>
  <entry>
    <title>Old post</title>
    <link rel="alternate" href="https://mhrainspectorate.blog.gov.uk/old"/>
    <updated>2020-01-01T00:00:00Z</updated>
    <content>old content</content>
  </entry>
</feed>"""

PICS_FEED = """<rss version="2.0"><channel>
  <item>
    <title>PICS aide memoire</title>
    <link>https://picscheme.org/doc1</link>
    <pubDate>Tue, 01 Jul 2026 00:00:00 +0000</pubDate>
    <description>GMP inspection aide memoire</description>
    <guid>pics-guid-1</guid>
  </item>
  <item>
    <title>Old pics</title>
    <link>https://picscheme.org/old</link>
    <pubDate>Wed, 01 Jan 2020 00:00:00 +0000</pubDate>
    <description>old</description>
  </item>
</channel></rss>"""

ECA_RSS = """<rss version="2.0"><channel>
  <item>
    <title>ECA GMP news</title>
    <link>https://gmp-compliance.org/news1</link>
    <pubDate>Wed, 02 Jul 2026 00:00:00 +0000</pubDate>
    <description>FDA warning letter summary</description>
    <guid>eca-guid-1</guid>
  </item>
</channel></rss>"""

ECA_ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>ECA atom item</title>
    <link href="https://gmp-compliance.org/atom1"/>
    <published>2026-07-01T00:00:00Z</published>
    <summary>atom summary</summary>
  </entry>
</feed>"""


class _PatchXml:
    """ci.http_get_xml 을 콜러블로 교체하는 컨텍스트 매니저."""
    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        self._orig = ci.http_get_xml
        ci.http_get_xml = self.fn
        return self

    def __exit__(self, *exc):
        ci.http_get_xml = self._orig
        return False


class RssGenericTest(unittest.TestCase):
    def _assert_doc_id(self, item):
        """document_id 가 _stable_doc_id(source, title, link, date) 와 일치(dedup 키 불변식)."""
        expected = ci._stable_doc_id(item.source, item.headline,
                                     item.official_url, item.date_iso)
        self.assertEqual(item.document_id, expected)

    def test_ema_multifeed_accumulate(self):
        fixtures = {
            ci.EMA_RSS_FEEDS["scientific-guidelines"]: EMA_SG,
            ci.EMA_RSS_FEEDS["inspections"]: EMA_INSP,
            ci.EMA_RSS_FEEDS["news"]: EMA_NEWS,
        }

        def fake(url, *a, **k):
            if url in fixtures:
                return ET.fromstring(fixtures[url])
            raise RuntimeError("boom-regulatory")  # regulatory-guidelines 피드 실패

        with _PatchXml(fake):
            items, err = ci.collect_ema_rss(START, END)
        # 3건 수집(윈도우 밖 old 제외) + 1개 피드 실패는 accumulate(다른 피드 유지)
        self.assertEqual(len(items), 3)
        self.assertIn("regulatory-guidelines", err)
        for it in items:
            self.assertEqual(it.source, ci.SOURCE_EMA)
            self.assertEqual(it.source_type, "Official API")
            self._assert_doc_id(it)
        # doc1: category → type_or_class
        self.assertEqual(items[0].official_url, "https://www.ema.europa.eu/en/doc1")
        self.assertEqual(items[0].type_or_class, "Scientific guideline")
        self.assertEqual(items[0].document_id, "b474168d4f69")
        # insp-tail: <link/> tail 폴백 + category 없음 → feed_name
        self.assertEqual(items[1].official_url, "https://www.ema.europa.eu/en/insp-tail")
        self.assertEqual(items[1].type_or_class, "inspections")
        self.assertEqual(items[1].document_id, "99e0d7f3f56b")
        # news1: dc:date 폴백 → 2026-07-03
        self.assertEqual(items[2].date_iso, "2026-07-03")
        self.assertEqual(items[2].document_id, "49f799bc9892")

    def test_mhra_atom(self):
        with _PatchXml(lambda u, *a, **k: ET.fromstring(MHRA_FEED)):
            items, err = ci.collect_mhra_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)  # old(2020) 윈도우 제외
        self.assertEqual(items[0].type_or_class, "Data Integrity")  # category term
        self.assertEqual(items[0].source_type, "Official Regulator Blog")
        self.assertEqual(items[0].document_id, "7a77b1633e29")
        # post3: category 없음 → "Blog", summary 없음 → content 폴백
        self.assertEqual(items[1].type_or_class, "Blog")
        self.assertEqual(items[1].body, "update via content tag")
        self.assertEqual(items[1].document_id, "905be7829b72")
        for it in items:
            self._assert_doc_id(it)

    def test_pics_rss(self):
        with _PatchXml(lambda u, *a, **k: ET.fromstring(PICS_FEED)):
            items, err = ci.collect_pics_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].type_or_class, "PIC/S")
        self.assertEqual(items[0].source_type, "Official Regulatory Page")
        self.assertEqual(items[0].document_id, "78e5075e6223")
        self._assert_doc_id(items[0])

    def test_eca_rss2(self):
        with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_RSS)):
            items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].type_or_class, "GMP News")
        self.assertEqual(items[0].source_type, "Expert Secondary")
        self.assertEqual(items[0].document_id, "50c5d1733bcc")
        self._assert_doc_id(items[0])

    def test_eca_atom_hybrid(self):
        with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_ATOM)):
            items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].official_url, "https://gmp-compliance.org/atom1")
        self.assertEqual(items[0].document_id, "e46469e87f7e")
        self._assert_doc_id(items[0])

    def test_eca_http_403_silent(self):
        def fake(url, *a, **k):
            raise HTTPClientError(403, url, "forbidden")

        with _PatchXml(fake):
            items, err = ci.collect_eca_rss(START, END)
        # Expert Secondary: 403 은 경고 없이 skip → ([], None) (오류로 취급 안 함)
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_single_feed_error_returns_message(self):
        def fake(url, *a, **k):
            raise RuntimeError("network down")

        for collect in (ci.collect_mhra_rss, ci.collect_pics_rss):
            with _PatchXml(fake):
                items, err = collect(START, END)
            self.assertEqual(items, [])
            self.assertEqual(err, "network down")


if __name__ == "__main__":
    unittest.main()
