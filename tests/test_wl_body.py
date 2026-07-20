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

    # 2-tier 선별(2026-06-18): 위반 서술 1차 앵커가 일반 머리말보다 앞서 선택돼야 한다.
    def test_primary_anchor_beats_earlier_generic_cgmp(self) -> None:
        html = (
            "<body><p>WARNING LETTER</p>"
            "<p>This warning letter summarizes significant violations of Current Good "
            "Manufacturing Practice (CGMP) regulations. Your drug products are adulterated.</p>"
            "<p>During our inspection, our investigators observed specific violations "
            "including, but not limited to, the following. 1. Your firm failed to establish "
            "adequate written procedures (21 CFR 211.100(a)).</p></body>"
        )
        ex = ci._extract_wl_body_excerpt(html)
        self.assertTrue(ex.startswith("During our inspection"))   # 1차 앵커 우선
        self.assertNotIn("This warning letter", ex)               # 일반 머리말 절단
        self.assertIn("21 CFR 211.100(a)", ex)

    def test_primary_anchor_records_review_phrasing(self) -> None:
        # 704(a)(4) 기록검토형(Sante 류): "significant violations were observed including"
        html = (
            "<body><p>This warning letter summarizes significant violations of CGMP. "
            "Your drug products are adulterated.</p>"
            "<p>Following review of records, significant violations were observed "
            "including, but not limited to, the following: 1. Your firm failed to conduct "
            "at least one test to verify the identity of each component "
            "(21 CFR 211.84(d)(1)).</p></body>"
        )
        ex = ci._extract_wl_body_excerpt(html)
        self.assertTrue(ex.startswith("significant violations were observed including"))
        self.assertIn("211.84(d)(1)", ex)

    def test_primary_anchor_telehealth_phrasing(self) -> None:
        # 웹사이트 검토형(Telehealth): "FDA observed that ..."
        html = (
            "<body><p>This warning letter advises you of significant violations identified "
            "during a U.S. FDA review of your website. The violations cited are not "
            "all-inclusive.</p>"
            "<p>FDA observed that your website offers compounded drug products, including "
            "semaglutide products, with false or misleading claims under sections 502(a) "
            "and 502(bb).</p></body>"
        )
        ex = ci._extract_wl_body_excerpt(html)
        self.assertTrue(ex.startswith("FDA observed that"))
        self.assertIn("semaglutide", ex)

    def test_falls_back_to_generic_when_no_primary(self) -> None:
        # 1차 앵커 부재 시 종전 동작(일반 머리말 폴백) 보존 — 빈 결과로 퇴행 금지.
        html = (
            "<body><p>This warning letter summarizes significant violations of CGMP "
            "regulations. Your products are adulterated within the meaning of the Act.</p></body>"
        )
        ex = ci._extract_wl_body_excerpt(html)
        self.assertTrue(ex.startswith("This warning letter"))
        self.assertGreater(len(ex), 0)


class WlFetchExcerptTest(unittest.TestCase):
    """_fetch_wl_body_excerpt → (text, status) — status 는 고정 어휘(사유 전파 2026-07-20)."""

    def test_fetch_success_returns_excerpt(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp(_WL_BODY_HTML)):
            ex, status = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertTrue(ex.startswith("During our inspection"))
        self.assertEqual(status, "ok")

    def test_fetch_403_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp("", 403)):
            ex, status = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertEqual(ex, "")
        self.assertEqual(status, "fetch-403")

    def test_fetch_timeout_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", side_effect=requests.Timeout("slow")):
            ex, status = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertEqual(ex, "")
        self.assertTrue(status.startswith("fetch-fail:"))

    def test_fetch_no_anchor_is_graceful_empty(self) -> None:
        html = "<html><body><p>Generic page with no enforcement narrative.</p></body></html>"
        with patch.object(ci.requests, "get", return_value=_Resp(html)):
            ex, status = ci._fetch_wl_body_excerpt("https://www.fda.gov/.../acme-660999")
        self.assertEqual(ex, "")
        self.assertEqual(status, "no-anchor")


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
        # [사유 전파 2026-07-20] 성공은 사유가 없다 — wl_body_status 키 자체가 안 생긴다.
        self.assertNotIn("wl_body_status", items[0].raw_payload)
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
        # [사유 전파 2026-07-20] 왜 비었는지가 raw 에 남는다(하류가 이유를 지어내지 않게).
        self.assertTrue(items[0].raw_payload["wl_body_status"].startswith("fetch-fail:"))
        # P1: 조용한 실패 금지 — 실패가 카운터에 남아 health warning 으로 표면화된다.
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body"],
                         {"enabled": True, "attempted": 1, "failed": 1})

    def test_flag_on_403_records_fetch_403_status(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WL_BODY": "true"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_Resp("", 403))):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertEqual(items[0].raw_payload["wl_body_status"], "fetch-403")

    def test_flag_on_no_anchor_records_no_anchor_status(self) -> None:
        html = "<html><body><p>Generic page with no enforcement narrative.</p></body></html>"
        with patch.dict(os.environ, {"ENABLE_WL_BODY": "true"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_Resp(html))):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertEqual(items[0].raw_payload["wl_body_status"], "no-anchor")

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
        # 플래그가 꺼져 시도 자체가 없으면 사유도 남기지 않는다(수집 정책이지 결손이 아님).
        self.assertNotIn("wl_body_status", items[0].raw_payload)
        # P1: flag off 면 카운터 0 → _evaluate_health warning 미발생(무변경 경로).
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body"],
                         {"enabled": False, "attempted": 0, "failed": 0})


class WlExtractBodyFullTest(unittest.TestCase):
    """[WL 심층분석 fan-out 2026-07-01] _extract_wl_body_full — 동일 앵커·더 긴 상한.

    excerpt(1500자)와 앵커 탐색 로직을 공유(_extract_wl_body_span)하므로 시작점은 동일하고
    상한(WL_BODY_FULL_MAX_CHARS)만 다르다 — excerpt 회귀(WlExtractExcerptTest)는 무변경.
    """

    def test_starts_at_same_anchor_as_excerpt(self) -> None:
        full = ci._extract_wl_body_full(_WL_BODY_HTML)
        excerpt = ci._extract_wl_body_excerpt(_WL_BODY_HTML)
        self.assertTrue(full.startswith("During our inspection"))
        self.assertTrue(excerpt.startswith(full[:len(excerpt)]) or full.startswith(excerpt))

    def test_no_anchor_returns_empty(self) -> None:
        html = "<html><body><p>Generic page with no enforcement narrative.</p></body></html>"
        self.assertEqual(ci._extract_wl_body_full(html), "")

    def test_captures_far_more_than_excerpt_cap(self) -> None:
        # 실제 편지처럼 위반 서술 뒤 구제조치/행정리스크 단락까지 이어지는 긴 본문.
        tail = ("Required Remediation: Within 15 working days, respond with corrective "
                "actions. " * 40)
        html = "<p>During our inspection, we observed violations. " + tail + "</p>"
        full = ci._extract_wl_body_full(html)
        excerpt = ci._extract_wl_body_excerpt(html)
        self.assertGreater(len(full), len(excerpt))
        self.assertIn("Required Remediation", full)

    def test_capped_at_full_max_chars(self) -> None:
        html = "<p>During our inspection " + ("x" * (ci.WL_BODY_FULL_MAX_CHARS + 5000)) + "</p>"
        self.assertLessEqual(len(ci._extract_wl_body_full(html)), ci.WL_BODY_FULL_MAX_CHARS)


class WlFetchBodyFullTest(unittest.TestCase):
    """_fetch_wl_body_full → (text, status) — status 어휘는 excerpt 와 동일(사유 전파)."""

    def test_fetch_success_returns_full_body(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp(_WL_BODY_HTML)):
            full, status = ci._fetch_wl_body_full("https://www.fda.gov/.../acme-660999")
        self.assertTrue(full.startswith("During our inspection"))
        self.assertEqual(status, "ok")

    def test_fetch_403_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", return_value=_Resp("", 403)):
            full, status = ci._fetch_wl_body_full("https://www.fda.gov/.../acme-660999")
        self.assertEqual(full, "")
        self.assertEqual(status, "fetch-403")

    def test_fetch_timeout_is_graceful_empty(self) -> None:
        with patch.object(ci.requests, "get", side_effect=requests.Timeout("slow")):
            full, status = ci._fetch_wl_body_full("https://www.fda.gov/.../acme-660999")
        self.assertEqual(full, "")
        self.assertTrue(status.startswith("fetch-fail:"))

    def test_fetch_no_anchor_is_graceful_empty(self) -> None:
        html = "<html><body><p>Generic page with no enforcement narrative.</p></body></html>"
        with patch.object(ci.requests, "get", return_value=_Resp(html)):
            full, status = ci._fetch_wl_body_full("https://www.fda.gov/.../acme-660999")
        self.assertEqual(full, "")
        self.assertEqual(status, "no-anchor")


class WlCollectBodyFullGateTest(unittest.TestCase):
    """collect_fda_warning_letters — ENABLE_WL_BODY_FULL 은 ENABLE_WL_BODY 와 완전 독립."""

    def _dispatch(self, *, body):
        def _get(url, *args, **kwargs):
            if url == ci.FDA_WL_URL:
                return _Resp(_WL_LIST_HTML)
            if callable(body):
                return body(url)
            return body
        return _get

    def test_flag_on_writes_body_full_independent_of_excerpt_flag(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WL_BODY_FULL": "true", "ENABLE_WL_BODY": "false"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_Resp(_WL_BODY_HTML))):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        raw = items[0].raw_payload
        self.assertNotIn("wl_body_excerpt", raw)              # excerpt 플래그 off → 미기록
        self.assertTrue(raw.get("wl_body_full", "").startswith("During our inspection"))
        self.assertNotIn("wl_body_status", raw)               # 성공은 사유가 없다
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body_full"],
                         {"enabled": True, "attempted": 1, "failed": 0})

    def test_both_flags_on_write_both_keys(self) -> None:
        with patch.dict(os.environ, {"ENABLE_WL_BODY_FULL": "true", "ENABLE_WL_BODY": "true"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_Resp(_WL_BODY_HTML))):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        raw = items[0].raw_payload
        self.assertIn("wl_body_excerpt", raw)
        self.assertIn("wl_body_full", raw)
        self.assertNotIn("wl_body_status", raw)               # 둘 다 성공 → 사유 없음

    def test_both_flags_on_excerpt_status_wins_over_full_status(self) -> None:
        # excerpt 블록이 먼저 실행돼 wl_body_status 를 남기면, full 블록의 실패는
        # setdefault 라 덮어쓰지 않는다(과제 지시: "excerpt 가 이미 사유를 남겼으면
        # 덮어쓰지 않는다"). 둘 다 403 이라 사유는 사실 같지만 우선순위 규약을 고정한다.
        with patch.dict(os.environ, {"ENABLE_WL_BODY_FULL": "true", "ENABLE_WL_BODY": "true"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_Resp("", 403))):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        raw = items[0].raw_payload
        self.assertNotIn("wl_body_excerpt", raw)
        self.assertNotIn("wl_body_full", raw)
        self.assertEqual(raw["wl_body_status"], "fetch-403")

    def test_flag_off_skips_full_body_fetch(self) -> None:
        def _must_not_fetch_letter(url):
            raise AssertionError(f"ENABLE_WL_BODY_FULL off 인데 전문 fetch 호출됨: {url}")

        with patch.dict(os.environ, {"ENABLE_WL_BODY_FULL": "false", "ENABLE_WL_BODY": "false"}):
            with patch.object(ci.requests, "get",
                              side_effect=self._dispatch(body=_must_not_fetch_letter)):
                items, err = ci.collect_fda_warning_letters(_WIN_START, _WIN_END)
        self.assertIsNone(err)
        self.assertNotIn("wl_body_full", items[0].raw_payload)
        self.assertEqual(ci.LAST_WL_HEALTH["wl_body_full"],
                         {"enabled": False, "attempted": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
