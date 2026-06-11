"""FDA Warning Letter 본문 위반 excerpt 회귀 — WHY-1 #2 (flag ENABLE_WL_BODY, 기본 off).

목록 메타(subject)만 잡던 WL 을 편지 본문의 위반 서술 구간까지 추출해 카드 "왜"를 살린다.
- _wl_html_to_text: script/style·태그 제거 + 엔티티 복원 + 공백 정규화(무의존·결정론).
- _extract_wl_body_excerpt: 가장 이른 위반 앵커부터. 앵커 없으면 ""(앞부분 폴백 안 함 — FDA
  페이지는 nav/푸터가 많아 메타 카드 유지가 안전).
- _fetch_wl_body_excerpt / collect_fda_warning_letters: 403/timeout graceful(키 미기록,
  목록 메타 카드 유지) · flag off=fetch 미호출.
- LAST_WL_HEALTH(P1): excerpt 시도/실패 집계 — 오케스트레이터가 stats 로 옮겨
  _evaluate_health 가 warning-only 로 표면화(조용한 실패 금지).
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci

# 실제 WL 편지 본문 형태 — nav/머리말 + 위반 서술.
_WL_BODY_HTML = (
    "<html><head><style>.x{color:red}</style></head><body>"
    "<nav>FDA Home &gt; Warning Letters</nav>"
    "<div class='letter'><p>Acme&nbsp;Pharma Inc</p><p>WARNING LETTER</p>"
    "<p>During our inspection of your drug manufacturing facility, we observed "
    "significant violations of Current Good Manufacturing Practice (CGMP) "
    "regulations. Specifically, your firm failed to establish adequate written "
    "procedures.</p></div>"
    "<script>track();</script></body></html>"
)
_WL_LIST_HTML = (
    "<table class=\"lcds-datatable-warning-letters table\">"
    "<tr><th>Posted Date</th><th>Letter Issue Date</th><th>Company Name</th>"
    "<th>Issuing Office</th><th>Subject</th></tr>"
    "<tr><td>06/10/2026</td><td>06/05/2026</td>"
    "<td><a href=\"/inspections-compliance-enforcement-and-criminal-investigations/"
    "warning-letters/acme-660999\">Acme Pharma Inc</a></td>"
    "<td>Center for Drug Evaluation and Research (CDER)</td>"
    "<td>CGMP/Finished Pharmaceuticals/Adulterated</td></tr>"
    "</table>"
)
_WIN_START = date(2026, 6, 1)
_WIN_END = date(2026, 6, 30)


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class WlHtmlToTextTest(unittest.TestCase):
    def test_strips_tags_scripts_and_unescapes_entities(self) -> None:
        text = ci._wl_html_to_text(_WL_BODY_HTML)
        self.assertNotIn("<", text)
        self.assertNotIn("track()", text)              # <script> 본문 제거
        self.assertNotIn("color:red", text)            # <style> 본문 제거
        self.assertIn("FDA Home > Warning Letters", text)   # &gt; 복원
        self.assertIn("Acme Pharma Inc", text)         # &nbsp; → 공백

    def test_empty_input(self) -> None:
        self.assertEqual(ci._wl_html_to_text(""), "")


class WlExtractExcerptTest(unittest.TestCase):
    def test_starts_at_earliest_violation_anchor(self) -> None:
        ex = ci._extract_wl_body_excerpt(_WL_BODY_HTML)
        self.assertTrue(ex.startswith("During our inspection"))
        self.assertNotIn("WARNING LETTER", ex)          # 머리말 제외
        self.assertIn("Current Good Manufacturing Practice", ex)

    def test_no_anchor_returns_empty(self) -> None:
        html = "<html><body><p>Generic page with no enforcement narrative.</p></body></html>"
        self.assertEqual(ci._extract_wl_body_excerpt(html), "")

    def test_excerpt_capped_at_max_chars(self) -> None:
        html = "<p>During our inspection " + ("x" * (ci.WL_BODY_MAX_CHARS + 500)) + "</p>"
        self.assertLessEqual(len(ci._extract_wl_body_excerpt(html)), ci.WL_BODY_MAX_CHARS)


class WlFetchExcerptTest(unittest.TestCase):
    def test_fetch_success_returns_excerpt(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp(_WL_BODY_HTML)):
            ex = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertTrue(ex.startswith("During our inspection"))

    def test_fetch_403_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp("", 403)):
            ex = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertEqual(ex, "")

    def test_fetch_timeout_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", side_effect=requests.Timeout("slow")):
            ex = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertEqual(ex, "")


class WlCollectBodyGateTest(unittest.TestCase):
    """collect_fda_warning_letters — flag on/off · graceful · 목록 메타 카드 유지."""

    def _dispatch(self, *, body):
        """FDA_WL_URL → 목록 HTML, 편지 URL → body(callable|Resp)."""
        def _get(url, *args, **kwargs):
            if url == ci.FDA_WL_URL:
                return _Resp(_WL_LIST_HTML)
            if callable(body):
                return body(url)
            return body
        return _get

    def test_flag_on_writes_body_excerpt(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WL_BODY": "true"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_Resp(_WL_BODY_HTML))):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        excerpt = items[0].raw_payload.get("wl_body_excerpt", "")
        self.assertTrue(excerpt.startswith("During our inspection"))
        # P1: 성공도 attempted 로 집계(LAST_WL_HEALTH → stats 배선용).
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body"],
                         {"enabled": True, "attempted": 1, "failed": 0})

    def test_flag_on_fetch_failure_keeps_metadata_card(self) -> None:
        def _boom(url):
            raise requests.Timeout("slow")

        with patch.dict(os.environ, {"ENABLE_WL_BODY": "true"}):
            with patch.object(ci.requests, "get", side_effect=self._dispatch(body=_boom)):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)                 # 목록 메타 카드 유지
        self.assertNotIn("wl_body_excerpt", items[0].raw_payload)
        # P1: 조용한 실패 금지 — 실패가 카운터에 남아 health warning 으로 표면화된다.
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body"],
                         {"enabled": True, "attempted": 1, "failed": 1})

    def test_flag_off_skips_body_fetch(self) -> None:
        def _must_not_fetch_letter(url):
            raise AssertionError(f"flag off 인데 편지 본문 fetch 호출됨: {url}")

        with patch.dict(os.environ, {"ENABLE_WL_BODY": "false"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_must_not_fetch_letter)):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertNotIn("wl_body_excerpt", items[0].raw_payload)
        # P1: flag off 면 카운터 0 → _evaluate_health warning 미발생(무변경 경로).
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body"],
                         {"enabled": False, "attempted": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
