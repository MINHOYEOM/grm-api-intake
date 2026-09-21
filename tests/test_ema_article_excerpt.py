"""EMA 기사 본문 흡수 회귀 (flag ENABLE_EMA_ARTICLE_EXCERPT, 기본 off).

계기(2026-09-21): EMA RSS 는 `<description>` 이 비어 오는 항목이 많아 카드 입력이
**제목 한 줄**뿐인 채로 발행까지 갔다. 같은 증거 상황에서 결과가 두 갈래로 갈렸다.

  · `59e513e60f06` — 정직하게 "제목 수준이어서 원문 확인이 필요" 라고 적었다. 카드에
    남는 정보가 제목뿐이고, 시사점 문단은 문서를 읽지 않은 채 쓰였다.
  · `5f5b8279859d` — 제목만 받고도 "CHMP 가 12개 의약품 허가 권고" 라고 단정했다.
    EMA 원문과 대조하니 12 는 맞았지만 **우리가 준 입력에 그 숫자는 없었다**.

맞은 단정과 틀린 단정을 우리 층에서 가릴 수 없다는 것이 문제의 핵심이라, 본문을
받아오는 것으로 원인을 없앤다. ECA/ISPE 와 같은 층·같은 추출기를 쓴다.

이 검사가 묻는 것은 "fetch 가 되나"가 아니라 다음 네 가지다:
  ① flag off 면 아무 일도 안 일어난다(기존 산출물 byte 동일)
  ② 실패(403/timeout/본문 없음)는 카드를 죽이지 않는다
  ③ **카드가 될 항목만** 받는다(무관 항목까지 긁지 않는다)
  ④ 받아온 본문이 하류에서 `source_body_captured=True` 로 이어진다
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_scaffold as cs  # noqa: E402
import collect_intake as ci  # noqa: E402

START = date(2026, 9, 14)
END = date(2026, 9, 21)

# 실제 2026-09-21 호 카드가 된 두 항목의 표제(관련=카드가 됨).
_RELEVANT_TITLES = [
    "Co-ordinating good manufacturing practice (GMP) inspections",
    "Guidance on good manufacturing practice and good distribution practice",
]
# GMP 어휘가 없어 카드가 되지 않는 항목 — fetch 대상에서 빠져야 한다.
_IRRELEVANT_TITLE = "Annual report on the European Medicines Agency budget"


def _ema_feed(titles: list[str]) -> str:
    items = "".join(
        f"<item><title>{t}</title>"
        f"<link>https://www.ema.europa.eu/en/news/item-{i}</link>"
        f"<pubDate>Wed, 16 Sep 2026 0{i % 10}:00:00 +0000</pubDate>"
        f"<description></description>"
        f"<guid>ema-guid-{i}</guid></item>"
        for i, t in enumerate(titles)
    )
    return f'<rss version="2.0"><channel>{items}</channel></rss>'


# 실제 EMA CHMP 페이지의 첫 문단(2026-09-21 실측) — 숫자가 본문에 있다는 것이 요점.
_EMA_CHMP_HTML = (
    "<html><head><style>.x{color:red}</style></head><body>"
    "<nav>EMA Home &gt; News</nav><header>Site header</header>"
    "<article><p>EMA&rsquo;s human medicines committee (CHMP) recommended 12 medicines "
    "for approval at its September 2026 meeting.</p>"
    "<p>The committee recommended granting a marketing authorisation for Frehemgo "
    "(denecimig), for the prophylaxis of bleeding episodes in patients with haemophilia A.</p>"
    "</article><footer>Copyright EMA</footer><script>t();</script></body></html>"
)


class _PatchXml:
    """ci.http_get_xml 교체(tests/test_eca_article_excerpt.py 동형)."""

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
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def _xml_for(titles: list[str]):
    """EMA 피드 4개 중 하나에만 항목을 주고 나머지는 빈 채널(중복 방지)."""
    served = {"n": 0}

    def _fn(url, *a, **k):
        served["n"] += 1
        return ET.fromstring(_ema_feed(titles) if served["n"] == 1
                             else '<rss version="2.0"><channel></channel></rss>')
    return _fn


class EmaExcerptFlagGateTest(unittest.TestCase):
    """① flag off 면 fetch 자체가 없다."""

    def test_flag_off_does_not_fetch(self):
        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "false"}):
            with _PatchXml(_xml_for(_RELEVANT_TITLES)):
                with patch.object(ci.requests, "get") as mock_get:
                    items, err = ci.collect_ema_rss(START, END)
        self.assertIsNone(err)
        mock_get.assert_not_called()
        for it in items:
            self.assertNotIn("article_excerpt", it.raw_payload)

    def test_flag_default_off_when_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "ENABLE_EMA_ARTICLE_EXCERPT"}
        with patch.dict(os.environ, env, clear=True):
            with _PatchXml(_xml_for(_RELEVANT_TITLES)):
                with patch.object(ci.requests, "get") as mock_get:
                    ci.collect_ema_rss(START, END)
        mock_get.assert_not_called()


class EmaExcerptCaptureTest(unittest.TestCase):
    def test_body_is_captured_into_generic_key(self):
        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(_xml_for(_RELEVANT_TITLES[:1])):
                with patch.object(ci.requests, "get", return_value=_Resp(_EMA_CHMP_HTML)):
                    with patch.object(ci.time, "sleep") as mock_sleep:
                        items, err = ci.collect_ema_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        excerpt = items[0].raw_payload.get("article_excerpt", "")
        self.assertTrue(excerpt.startswith("EMA’s human medicines committee (CHMP)"))
        mock_sleep.assert_called_with(ci.ECA_ARTICLE_EXCERPT_DELAY_SECONDS)

    def test_the_number_that_was_asserted_without_evidence_is_now_in_our_input(self):
        """★이 PR 의 존재 이유 — "12" 가 우리가 가진 텍스트 안에 있어야 한다.

        2026-09-21 호는 제목만 받고 "12개 의약품 허가 권고"를 단정했다. 우연히 맞았지만
        근거는 우리 입력에 없었다. 본문을 받으면 그 숫자가 우리 텍스트가 된다.
        """
        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(_xml_for(_RELEVANT_TITLES[:1])):
                with patch.object(ci.requests, "get", return_value=_Resp(_EMA_CHMP_HTML)):
                    with patch.object(ci.time, "sleep"):
                        items, _ = ci.collect_ema_rss(START, END)
        self.assertIn("12 medicines", items[0].raw_payload["article_excerpt"])

    def test_captured_body_reaches_source_body_captured(self):
        """④ 하류 계약 — 제네릭 키라 카드 스캐폴드가 이미 안다."""
        self.assertTrue(cs._has_source_body({"article_excerpt": "some body text"}))
        self.assertFalse(cs._has_source_body({}))


class EmaExcerptRelevanceScopeTest(unittest.TestCase):
    """③ 카드가 될 항목만 받는다."""

    def test_irrelevant_items_are_not_fetched(self):
        titles = _RELEVANT_TITLES[:1] + [_IRRELEVANT_TITLE]
        calls: list[str] = []

        def _get(url, *a, **k):
            calls.append(url)
            return _Resp(_EMA_CHMP_HTML)

        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(_xml_for(titles)):
                with patch.object(ci.requests, "get", side_effect=_get):
                    with patch.object(ci.time, "sleep"):
                        items, _ = ci.collect_ema_rss(START, END)
        self.assertEqual(len(items), 2)                       # 목록은 전건 유지
        self.assertEqual(len(calls), 1)                       # fetch 는 관련 1건만
        by_title = {it.headline: it for it in items}
        self.assertIn("article_excerpt", by_title[_RELEVANT_TITLES[0]].raw_payload)
        self.assertNotIn("article_excerpt", by_title[_IRRELEVANT_TITLE].raw_payload)

    def test_irrelevant_fixture_really_is_irrelevant(self):
        """음성 대조군이 실제로 음성인지 — 이게 깨지면 위 검사는 아무것도 안 묻는다."""
        self.assertNotIn(ci.compute_relevance(_IRRELEVANT_TITLE, "", ""),
                         ("Likely", "Possible"))
        for t in _RELEVANT_TITLES:
            self.assertIn(ci.compute_relevance(t, "", ""), ("Likely", "Possible"), t)


class EmaExcerptFailureIsGracefulTest(unittest.TestCase):
    """② 실패가 카드를 죽이지 않는다."""

    def _run(self, get_side):
        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(_xml_for(_RELEVANT_TITLES[:1])):
                with patch.object(ci.requests, "get", **get_side):
                    with patch.object(ci.time, "sleep"):
                        return ci.collect_ema_rss(START, END)

    def test_timeout_keeps_the_card(self):
        def _boom(url, *a, **k):
            raise requests.Timeout("slow")
        items, err = self._run({"side_effect": _boom})
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("article_excerpt", items[0].raw_payload)

    def test_403_keeps_the_card(self):
        items, err = self._run({"return_value": _Resp("", 403)})
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("article_excerpt", items[0].raw_payload)

    def test_page_without_paragraphs_keeps_the_card(self):
        items, err = self._run({"return_value": _Resp("<html><body><div>no p</div></body></html>")})
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("article_excerpt", items[0].raw_payload)


class EmaExcerptCapTest(unittest.TestCase):
    def test_cap_limits_fetches_and_keeps_every_item(self):
        n = ci.EMA_ARTICLE_EXCERPT_CAP + 3
        titles = [f"{_RELEVANT_TITLES[0]} {i}" for i in range(n)]
        calls: list[str] = []

        def _get(url, *a, **k):
            calls.append(url)
            return _Resp(_EMA_CHMP_HTML)

        with patch.dict(os.environ, {"ENABLE_EMA_ARTICLE_EXCERPT": "true"}):
            with _PatchXml(_xml_for(titles)):
                with patch.object(ci.requests, "get", side_effect=_get):
                    with patch.object(ci.time, "sleep"):
                        items, err = ci.collect_ema_rss(START, END)
        self.assertIsNone(err)
        self.assertEqual(len(items), n)                       # 목록은 cap 과 무관 전건
        self.assertEqual(len(calls), ci.EMA_ARTICLE_EXCERPT_CAP)


if __name__ == "__main__":
    unittest.main()
