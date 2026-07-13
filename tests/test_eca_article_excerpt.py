"""ECA 기사(gmp-compliance.org) 본문 흡수 회귀 — 전문지 브리핑 v2 §4
(flag ENABLE_ECA_ARTICLE_EXCERPT, 기본 off).

Routine summary 슬롯이 실기사 본문을 근거로 작성되도록 prose_input 을 풍부화한다(프롬프트
무접촉 — 입력만 좋아지면 자동 개선). WL 본문 excerpt(WHY-1 #2, tests/test_wl_body.py)와
동형 패턴:
- _extract_eca_article_excerpt: <p> 텍스트 결합, script/style/nav/header/footer 제거,
  ≤1200자 절단(무의존·결정론).
- _fetch_eca_article_excerpt / collect_eca_rss: 403/timeout graceful(키 미기록, 카드는
  기존 메타 그대로) · flag off = fetch 미호출(collect_rss_feed 산출과 완전 동일 → 골든 불변).
- cap(10건)·per-item delay(1s, time.sleep mock 로 실대기 없이 검증).
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci  # noqa: E402

START = date(2026, 6, 26)
END = date(2026, 7, 3)


def _eca_rss_items(n: int) -> str:
    """윈도우 내 ECA RSS 2.0 item n개(제목/링크/guid 서로 다름, pubDate 모두 윈도우 내)."""
    items = "".join(
        f"<item><title>ECA GMP news {i}</title>"
        f"<link>https://www.gmp-compliance.org/gmp-news/item-{i}</link>"
        f"<pubDate>Wed, 01 Jul 2026 0{i % 10}:00:00 +0000</pubDate>"
        f"<description>RSS teaser only {i}</description>"
        f"<guid>eca-guid-{i}</guid></item>"
        for i in range(n)
    )
    return f"<rss version=\"2.0\"><channel>{items}</channel></rss>"


ECA_RSS_ONE = _eca_rss_items(1)

_ECA_ARTICLE_HTML = (
    "<html><head><style>.x{color:red}</style></head><body>"
    "<nav>ECA Home &gt; GMP News</nav>"
    "<header>Site header text should not appear</header>"
    "<article><p>Should TGA publish GMP certificates? The debate over transparency "
    "in Australian regulatory disclosure has intensified in recent months.</p>"
    "<p>Industry stakeholders argue that publishing certificates would improve "
    "supply chain trust while regulators weigh confidentiality concerns.</p></article>"
    "<footer>Copyright ECA Academy &mdash; footer nav links</footer>"
    "<script>track();</script></body></html>"
)


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


class EcaExtractArticleExcerptTest(unittest.TestCase):
    def test_joins_p_tags_and_strips_boilerplate(self) -> None:
        ex = ci._extract_eca_article_excerpt(_ECA_ARTICLE_HTML)
        self.assertNotIn("<", ex)
        self.assertNotIn("track()", ex)                 # <script> 제거
        self.assertNotIn("color:red", ex)                # <style> 제거
        self.assertNotIn("ECA Home", ex)                  # <nav> 제거
        self.assertNotIn("Site header", ex)               # <header> 제거
        self.assertNotIn("Copyright ECA Academy", ex)     # <footer> 제거
        self.assertTrue(ex.startswith("Should TGA publish GMP certificates?"))
        self.assertIn("Industry stakeholders argue", ex)  # 두 <p> 모두 결합

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(ci._extract_eca_article_excerpt(""), "")

    def test_no_p_tags_returns_empty(self) -> None:
        html = "<html><body><div>no paragraph tags here</div></body></html>"
        self.assertEqual(ci._extract_eca_article_excerpt(html), "")

    def test_capped_at_max_chars(self) -> None:
        html = "<p>" + ("x" * (ci.ECA_ARTICLE_EXCERPT_MAX_CHARS + 500)) + "</p>"
        ex = ci._extract_eca_article_excerpt(html)
        self.assertLessEqual(len(ex), ci.ECA_ARTICLE_EXCERPT_MAX_CHARS)


class EcaFetchArticleExcerptTest(unittest.TestCase):
    def test_fetch_success_returns_excerpt(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp(_ECA_ARTICLE_HTML)):
            ex = ci._fetch_eca_article_excerpt(
                "https://www.gmp-compliance.org/gmp-news/should-tga-publish")
        self.assertTrue(ex.startswith("Should TGA publish GMP certificates?"))

    def test_fetch_403_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp("", 403)):
            ex = ci._fetch_eca_article_excerpt("https://www.gmp-compliance.org/gmp-news/x")
        self.assertEqual(ex, "")

    def test_fetch_timeout_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", side_effect=requests.Timeout("slow")):
            ex = ci._fetch_eca_article_excerpt("https://www.gmp-compliance.org/gmp-news/x")
        self.assertEqual(ex, "")


class EcaCollectRssExcerptGateTest(unittest.TestCase):
    """collect_eca_rss — ENABLE_ECA_ARTICLE_EXCERPT flag on/off · cap · per-item delay."""

    def test_flag_off_skips_article_fetch_byte_identical(self) -> None:
        # off 면 collect_rss_feed 산출과 완전 동일(§4 골든 불변 하드 요구) — 기사 fetch
        # 자체가 호출되면 안 되므로 requests.get 이 불려도 실패하게 만든다.
        def _must_not_fetch(url, *a, **k):
            raise AssertionError(f"flag off 인데 기사 fetch 호출됨: {url}")

        with patch.dict(os.environ, {"ENABLE_ECA_ARTICLE_EXCERPT": "false"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_RSS_ONE)):
                with patch.object(ci.requests, "get", side_effect=_must_not_fetch):
                    items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("eca_article_excerpt", items[0].raw_payload)

    def test_flag_default_off_when_unset(self) -> None:
        # env 자체가 없을 때도 기본 off(env_flag 기본값) — 명시적 'false' 와 동일 계약.
        def _must_not_fetch(url, *a, **k):
            raise AssertionError(f"flag 미설정인데 기사 fetch 호출됨: {url}")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_ECA_ARTICLE_EXCERPT", None)
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_RSS_ONE)):
                with patch.object(ci.requests, "get", side_effect=_must_not_fetch):
                    items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertNotIn("eca_article_excerpt", items[0].raw_payload)

    def test_flag_on_writes_excerpt_into_raw_payload(self) -> None:
        with patch.dict(os.environ, {"ENABLE_ECA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_RSS_ONE)):
                with patch.object(ci.requests, "get", return_value=_Resp(_ECA_ARTICLE_HTML)):
                    with patch.object(ci.time, "sleep") as mock_sleep:
                        items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        excerpt = items[0].raw_payload.get("eca_article_excerpt", "")
        self.assertTrue(excerpt.startswith("Should TGA publish GMP certificates?"))
        mock_sleep.assert_called_with(ci.ECA_ARTICLE_EXCERPT_DELAY_SECONDS)

    def test_flag_on_fetch_failure_keeps_metadata_card(self) -> None:
        def _boom(url, *a, **k):
            raise requests.Timeout("slow")

        with patch.dict(os.environ, {"ENABLE_ECA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_RSS_ONE)):
                with patch.object(ci.requests, "get", side_effect=_boom):
                    with patch.object(ci.time, "sleep"):
                        items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)                   # 목록 메타 카드 유지
        self.assertNotIn("eca_article_excerpt", items[0].raw_payload)

    def test_flag_on_403_keeps_metadata_card(self) -> None:
        with patch.dict(os.environ, {"ENABLE_ECA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(ECA_RSS_ONE)):
                with patch.object(ci.requests, "get", return_value=_Resp("", 403)):
                    with patch.object(ci.time, "sleep"):
                        items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("eca_article_excerpt", items[0].raw_payload)

    def test_cap_limits_fetch_to_first_n_items(self) -> None:
        n = ci.ECA_ARTICLE_EXCERPT_CAP + 3
        calls: list[str] = []

        def _get(url, *a, **k):
            calls.append(url)
            return _Resp(_ECA_ARTICLE_HTML)

        with patch.dict(os.environ, {"ENABLE_ECA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(lambda u, *a, **k: ET.fromstring(_eca_rss_items(n))):
                with patch.object(ci.requests, "get", side_effect=_get):
                    with patch.object(ci.time, "sleep"):
                        items, err = ci.collect_eca_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), n)                    # 목록 자체는 cap 과 무관 전건 유지
        self.assertEqual(len(calls), ci.ECA_ARTICLE_EXCERPT_CAP)  # fetch 만 cap 적용
        with_excerpt = sum(1 for it in items if it.raw_payload.get("eca_article_excerpt"))
        self.assertEqual(with_excerpt, ci.ECA_ARTICLE_EXCERPT_CAP)
        # cap 이후 항목은 excerpt 없이 메타 카드 그대로.
        self.assertNotIn("eca_article_excerpt",
                         items[ci.ECA_ARTICLE_EXCERPT_CAP].raw_payload)


if __name__ == "__main__":
    unittest.main()
