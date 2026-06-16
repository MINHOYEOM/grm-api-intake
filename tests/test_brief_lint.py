"""brief_lint 회귀 — 출처 링크 근거(provenance) 하드 가드 (URL전수검사 2026-06-16, Phase F).

정상(handoff 근거 있음)·누출(m_99/m_218 근거 없음 → FAIL)·검증실패(오류 셸·검색 URL → WARN)
케이스를 동결한다. W24 사고(handoff 근거 없는 mfds/brd/view.do 링크)의 회귀 잠금.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brief_lint as bl  # noqa: E402


# ── 대표 handoff rows(v2 형태 일부) ───────────────────────────────────────────
ADMIN_SCAFFOLD = (
    "### [행정처분 · MFDS] 대한약품공업 — **X**\n"
    "<callout icon=\"🔖\" color=\"gray_bg\">\n"
    "\t**출처**  📎 공식원본 "
    "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026003474)\n"
    "</callout>"
)
RSS_NOTICE_SCAFFOLD = (
    "### [안내서 · MFDS] 지침 개정 — **Y**\n"
    "<callout icon=\"🔖\" color=\"gray_bg\">\n"
    "\t**출처**  정보출처/공식원본 "
    "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)\n"
    "</callout>"
)

HANDOFF_ROWS = [
    {
        "source": "MFDS", "document_id": "admin-2026003474",
        "official_url": "https://www.data.go.kr/data/15058457/openapi.do",
        "api_query": "https://api.odcloud.kr/api/15058457?seq=1",
        "card_scaffold": ADMIN_SCAFFOLD,
        "prose_input": {"kind": "admin-action"},
    },
    {
        "source": "MFDS", "document_id": "data0013-33716",
        "official_url": "https://www.mfds.go.kr/brd/m_218/view.do?seq=33716",
        "card_scaffold": RSS_NOTICE_SCAFFOLD,
        "prose_input": {"kind": "mfds-notice"},
    },
]


class TestNormalizeAndExtract(unittest.TestCase):
    def test_normalize_strips_fragment_trailing_slash_and_leading_amp(self):
        self.assertEqual(
            bl.normalize_url("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?&dispsApplySeq=1#x"),
            "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=1")
        self.assertEqual(bl.normalize_url("https://A.com/Path/"), "https://a.com/Path")

    def test_normalize_handles_amp_entity(self):
        self.assertEqual(
            bl.normalize_url("https://x.go.kr/getItem?&amp;a=1"),
            "https://x.go.kr/getItem?a=1")

    def test_extract_markdown_and_bare(self):
        text = "see [a](https://x.com/a) and https://y.com/b end"
        got = bl.extract_markdown_links(text)
        self.assertIn("https://x.com/a", got)
        self.assertIn("https://y.com/b", got)

    def test_collect_allowed_includes_scaffold_and_fields(self):
        allowed = bl.collect_allowed_urls(HANDOFF_ROWS)
        self.assertIn(
            bl.normalize_url("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026003474"),
            allowed)
        self.assertIn(bl.normalize_url("https://www.mfds.go.kr/brd/m_218/view.do?seq=33716"), allowed)
        self.assertIn(bl.normalize_url("https://www.data.go.kr/data/15058457/openapi.do"), allowed)


class TestLintProvenance(unittest.TestCase):
    def test_grounded_links_pass(self):
        """발행본이 scaffold 링크를 그대로 쓰면 findings 0."""
        published = ADMIN_SCAFFOLD + "\n\n" + RSS_NOTICE_SCAFFOLD
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertEqual(findings, [], msg=[str(f) for f in findings])

    def test_m99_press_release_leak_fails(self):
        """W24 사고: handoff 에 없는 m_99(보도자료) 직링크 → HARD FAIL."""
        published = (
            "### [고시 · MFDS] 무언가 — **Z**\n"
            "**출처**  📎 공식원본 "
            "[링크](https://www.mfds.go.kr/brd/m_99/view.do?seq=46893)")
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertTrue(bl.has_failures(findings))
        fail = [f for f in findings if f.severity == bl.SEV_FAIL]
        self.assertEqual(len(fail), 1)
        self.assertEqual(fail[0].code, "L17-MFDS-PROVENANCE")
        self.assertIn("m_99", fail[0].url)

    def test_m218_wrong_seq_leak_fails(self):
        """m_218 은 수집기 보드지만, handoff 에 없는 seq(딴 게시물) 직링크면 FAIL."""
        published = (
            "**출처**  정보출처/공식원본 "
            "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=99999)")
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertTrue(bl.has_failures(findings))

    def test_grounded_m218_seq_passes(self):
        """handoff 에 있는 정확한 m_218 seq 는 통과(정상 RSS 카드)."""
        published = (
            "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)")
        self.assertEqual(bl.lint_link_provenance(HANDOFF_ROWS, published), [])

    def test_unknown_nedrug_link_fails(self):
        """근거 없는 nedrug 링크(딴 seq) → FAIL(MFDS 도메인)."""
        published = "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=9999999)"
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertTrue(bl.has_failures(findings))

    def test_global_search_card_url_warns_not_fails(self):
        """근거 없는 비-MFDS 외부 링크(검색 카드 신규)는 WARN 이지 FAIL 아님."""
        published = "[링크](https://www.fda.gov/some/new/warning-letters/acme-999)"
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertFalse(bl.has_failures(findings))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, bl.SEV_WARN)

    def test_duplicate_bad_url_reported_once(self):
        published = ("[a](https://www.mfds.go.kr/brd/m_99/view.do?seq=1) "
                     "[b](https://www.mfds.go.kr/brd/m_99/view.do?seq=1)")
        findings = bl.lint_link_provenance(HANDOFF_ROWS, published)
        self.assertEqual(len(findings), 1)


class TestErrorPageDetection(unittest.TestCase):
    def test_error_marker_detected(self):
        self.assertTrue(bl.looks_like_error_page(
            "<html>오류가 발생하였습니다 해당 화면 혹은 기능을 찾을 수 없습니다</html>"))

    def test_real_content_not_error(self):
        self.assertFalse(bl.looks_like_error_page(
            "<html>행정처분정보 대한약품공업 제조업무정지 1개월</html>"))

    def test_empty_is_error(self):
        self.assertTrue(bl.looks_like_error_page(""))


class TestVerifyUrlLive(unittest.TestCase):
    """HTTP 스텁(network 없음) — resolve&verify 의 판정 로직 동결."""

    def _resp(self, status, text):
        m = mock.Mock()
        m.status_code = status
        m.text = text
        return m

    def test_valid_record_promotes(self):
        with mock.patch("requests.get",
                        return_value=self._resp(200, "행정처분정보 " + "x" * 9000)):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026003474",
                                   expect_terms=["행정처분"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["is_error_page"])

    def test_error_shell_rejected(self):
        with mock.patch("requests.get",
                        return_value=self._resp(200, "오류가 발생하였습니다 해당 화면 혹은 기능을 찾을 수 없습니다")):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=123456")
        self.assertFalse(r["ok"])
        self.assertTrue(r["is_error_page"])

    def test_missing_expected_term_rejected(self):
        with mock.patch("requests.get", return_value=self._resp(200, "x" * 9000)):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/x", expect_terms=["대한약품"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_terms"], ["대한약품"])

    def test_network_failure_graceful(self):
        with mock.patch("requests.get", side_effect=OSError("conn reset")):
            r = bl.verify_url_live("https://nedrug.mfds.go.kr/x")
        self.assertFalse(r["ok"])
        self.assertIn("conn reset", r["error"])


if __name__ == "__main__":
    unittest.main()
