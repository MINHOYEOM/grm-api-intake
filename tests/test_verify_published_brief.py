"""verify_published_brief — 발행 후 provenance 탐지(detective)의 순수 코어 회귀 (W1).

블록 URL 추출(Notion 블록 JSON)·분류(과알림 0)·audit JSON·verdict 환원을 동결한다.
Notion I/O 는 lazy import 라 순수 코어 테스트는 네트워크/requests 불필요.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brief_lint as bl  # noqa: E402
import verify_published_brief as vpb  # noqa: E402


# 그라운드 근거: handoff rows(MFDS RSS + 검색 카드 없음)
HANDOFF_ROWS = [
    {"source": "MFDS", "document_id": "data0013-33716",
     "official_url": "https://www.mfds.go.kr/brd/m_218/view.do?seq=33716",
     "card_scaffold": "[링크](https://www.mfds.go.kr/brd/m_218/view.do?seq=33716)"},
]


def _callout_with_link(url):
    return {"type": "callout", "callout": {"rich_text": [
        {"type": "text", "text": {"content": "출처 ", "link": None}, "href": None},
        {"type": "text", "text": {"content": "링크", "link": {"url": url}}, "href": url},
    ]}}


def _table_row(urls):
    return {"type": "table_row", "table_row": {"cells": [
        [{"type": "text", "text": {"content": "셀", "link": {"url": u}}, "href": u}]
        for u in urls]}}


class TestExtractUrls(unittest.TestCase):
    def test_callout_link(self):
        urls = vpb.extract_urls_from_blocks([_callout_with_link("https://x.go.kr/a")])
        self.assertEqual(urls, ["https://x.go.kr/a"])

    def test_table_row_cells(self):
        urls = vpb.extract_urls_from_blocks([_table_row(["https://a.com/1", "https://b.com/2"])])
        self.assertEqual(set(urls), {"https://a.com/1", "https://b.com/2"})

    def test_nested_children_recursion(self):
        block = {"type": "toggle", "toggle": {"rich_text": [], "children": [
            _callout_with_link("https://deep.go.kr/x")]}}
        self.assertIn("https://deep.go.kr/x", vpb.extract_urls_from_blocks([block]))

    def test_dedupe_preserves_order(self):
        urls = vpb.extract_urls_from_blocks([
            _callout_with_link("https://a.com/1"), _callout_with_link("https://a.com/1"),
            _callout_with_link("https://a.com/2")])
        self.assertEqual(urls, ["https://a.com/1", "https://a.com/2"])

    def test_href_without_text_link(self):
        block = {"type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "x"}, "href": "https://h.only/1"}]}}
        self.assertEqual(vpb.extract_urls_from_blocks([block]), ["https://h.only/1"])


def _paragraph(text):
    return {"type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "text": {"content": text}, "plain_text": text}]}}


class TestExtractText(unittest.TestCase):
    def test_paragraph_plain_text(self):
        t = vpb.extract_text_from_blocks([_paragraph("발행일: 2026-06-17 화요일")])
        self.assertIn("2026-06-17 화요일", t)

    def test_footer_in_callout_extracted(self):
        block = {"type": "callout", "callout": {"rich_text": [
            {"type": "text", "text": {"content": "발행일: 2026-06-17 "}, "plain_text": "발행일: 2026-06-17 "},
            {"type": "text", "text": {"content": "화요일"}, "plain_text": "화요일"}]}}
        t = vpb.extract_text_from_blocks([block])
        # 분절된 rich_text 도 이어붙여 연속 문자열 재구성(요일 검출 가능).
        self.assertIn("2026-06-17 화요일", t)

    def test_nested_children_text(self):
        block = {"type": "toggle", "toggle": {"rich_text": [], "children": [
            _paragraph("2026-06-17 수요일")]}}
        self.assertIn("2026-06-17 수요일", vpb.extract_text_from_blocks([block]))

    def test_table_cell_text(self):
        block = {"type": "table_row", "table_row": {"cells": [
            [{"type": "text", "text": {"content": "발행일"}, "plain_text": "발행일"}],
            [{"type": "text", "text": {"content": "2026-06-16"}, "plain_text": "2026-06-16"}]]}}
        self.assertIn("2026-06-16", vpb.extract_text_from_blocks([block]))


class TestClassify(unittest.TestCase):
    def test_mfds_leak_is_alert_no_network(self):
        """MFDS 미근거 = 결정론 alert(verdict 미사용)."""
        alerts, info = vpb.classify(
            HANDOFF_ROWS, ["https://www.mfds.go.kr/brd/m_99/view.do?seq=46893"])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].code, "L17-MFDS-PROVENANCE")
        self.assertEqual(info, [])

    def test_grounded_mfds_passes(self):
        alerts, info = vpb.classify(
            HANDOFF_ROWS, ["https://www.mfds.go.kr/brd/m_218/view.do?seq=33716"])
        self.assertEqual(alerts, [])
        self.assertEqual(info, [])

    def test_nonmfds_bad_verdict_upgrades_to_alert(self):
        alerts, info = vpb.classify(
            HANDOFF_ROWS, ["https://www.fda.gov/invented/wl-999"],
            verdict=lambda u: vpb.VERDICT_BAD)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].code, "L17-UNGROUNDED")

    def test_nonmfds_unknown_verdict_is_info_not_alert(self):
        """과알림 0: 일시 네트워크 실패(unknown)는 알림 아님(info)."""
        alerts, info = vpb.classify(
            HANDOFF_ROWS, ["https://www.fda.gov/maybe/down"],
            verdict=lambda u: vpb.VERDICT_UNKNOWN)
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)

    def test_nonmfds_ok_verdict_is_info(self):
        alerts, info = vpb.classify(
            HANDOFF_ROWS, ["https://www.fda.gov/live/wl-1"],
            verdict=lambda u: vpb.VERDICT_OK)
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)

    def test_no_verdict_treats_nonmfds_as_info(self):
        alerts, info = vpb.classify(HANDOFF_ROWS, ["https://www.fda.gov/x"])
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)


class TestVerdict(unittest.TestCase):
    def _resp(self, status, text):
        m = mock.Mock()
        m.status_code = status
        m.text = text
        return m

    def test_ok(self):
        with mock.patch("requests.get", return_value=self._resp(200, "정상 " + "x" * 9000)):
            self.assertEqual(vpb.definitive_verdict("https://x/1"), vpb.VERDICT_OK)

    def test_bad_error_shell(self):
        with mock.patch("requests.get", return_value=self._resp(200, "오류가 발생하였습니다")):
            self.assertEqual(vpb.definitive_verdict("https://x/1"), vpb.VERDICT_BAD)

    def test_bad_404(self):
        with mock.patch("requests.get", return_value=self._resp(404, "not found")):
            self.assertEqual(vpb.definitive_verdict("https://x/1"), vpb.VERDICT_BAD)

    def test_unknown_on_network_error(self):
        with mock.patch("requests.get", side_effect=OSError("conn reset")):
            self.assertEqual(vpb.definitive_verdict("https://x/1"), vpb.VERDICT_UNKNOWN)

    def test_unknown_on_waf_403(self):
        """과알림 0: WAF 차단(403)은 정당 링크일 수 있어 UNKNOWN(BAD 아님)."""
        with mock.patch("requests.get", return_value=self._resp(403, "Access Denied")):
            self.assertEqual(vpb.definitive_verdict("https://www.fda.gov/x"),
                             vpb.VERDICT_UNKNOWN)

    def test_unknown_on_short_200_nonerror(self):
        """200+짧은 비-오류 본문(WAF abuse/landing)도 UNKNOWN — 오탐 방지."""
        with mock.patch("requests.get", return_value=self._resp(200, "ok")):
            self.assertEqual(vpb.definitive_verdict("https://www.fda.gov/x"),
                             vpb.VERDICT_UNKNOWN)


class TestAuditJson(unittest.TestCase):
    def test_ok_when_no_alerts(self):
        j = vpb.build_audit_json([], [bl.LintFinding(bl.SEV_WARN, "L17-UNVERIFIED", "u", "m")])
        self.assertTrue(j["ok"])
        self.assertEqual(j["fail_count"], 0)
        self.assertEqual(j["info_count"], 1)

    def test_not_ok_with_alert(self):
        j = vpb.build_audit_json(
            [bl.LintFinding(bl.SEV_FAIL, "L17-MFDS-PROVENANCE", "u", "m")], [])
        self.assertFalse(j["ok"])
        self.assertEqual(j["fail_count"], 1)

    def test_skipped_is_ok(self):
        j = vpb.skipped_json("토큰 없음")
        self.assertTrue(j["ok"])
        self.assertIn("토큰", j["note"])

    def test_report_text(self):
        j = vpb.build_audit_json(
            [bl.LintFinding(bl.SEV_FAIL, "L17-MFDS-PROVENANCE", "https://u", "m")], [],
            brief_title="GRM Weekly Brief — 2026-06-15")
        self.assertIn("FAIL", vpb.format_audit_report(j))
        self.assertIn("PASS", vpb.format_audit_report(vpb.build_audit_json([], [])))
        self.assertIn("SKIP", vpb.format_audit_report(vpb.skipped_json("x")))


class TestRunSkips(unittest.TestCase):
    def test_no_token_skips(self):
        j = vpb.run("", weekly_db_id="w", intake_db_id="i")
        self.assertTrue(j["ok"])
        self.assertIn("NOTION_TOKEN", j["note"])

    def test_no_handoff_skips_not_alert(self):
        """근거 대조 불가(handoff 미발견)는 과알림 0 — ok:true 건너뜀."""
        with mock.patch.object(vpb, "fetch_latest_brief",
                               return_value={"id": "p", "url": "u", "title": "t"}), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows", return_value=[]):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i")
        self.assertTrue(j["ok"])
        self.assertIn("handoff", j["note"])

    def test_full_flow_detects_leak(self):
        with mock.patch.object(vpb, "fetch_latest_brief",
                               return_value={"id": "p", "url": "u", "title": "Brief"}), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=HANDOFF_ROWS), \
             mock.patch.object(vpb, "fetch_brief_blocks",
                               return_value=[_callout_with_link(
                                   "https://www.mfds.go.kr/brd/m_99/view.do?seq=1")]):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i", verify=False)
        self.assertFalse(j["ok"])
        self.assertEqual(j["fail_count"], 1)

    def test_full_flow_detects_weekday_mismatch(self):
        """W2: 구조 탐지 — 푸터 요일 오류(06-17=수인데 화요일)를 발행 후 결정론 FAIL 로 잡는다."""
        with mock.patch.object(vpb, "fetch_latest_brief",
                               return_value={"id": "p", "url": "u", "title": "Brief"}), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=HANDOFF_ROWS), \
             mock.patch.object(vpb, "fetch_brief_blocks",
                               return_value=[_paragraph("발행일: 2026-06-17 화요일")]):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i", verify=False)
        self.assertFalse(j["ok"])
        codes = [a["code"] for a in j["alerts"]]
        self.assertIn("PL14-WEEKDAY", codes)

    def test_full_flow_clean_brief_passes(self):
        """정상(요일 정확·미근거 링크 없음) → ok:true, 과알림 0."""
        with mock.patch.object(vpb, "fetch_latest_brief",
                               return_value={"id": "p", "url": "u", "title": "Brief"}), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=HANDOFF_ROWS), \
             mock.patch.object(vpb, "fetch_brief_blocks",
                               return_value=[_paragraph("발행일: 2026-06-17 수요일"),
                                             _callout_with_link(HANDOFF_ROWS[0]["official_url"])]):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i", verify=False)
        self.assertTrue(j["ok"])
        self.assertEqual(j["fail_count"], 0)


if __name__ == "__main__":
    unittest.main()
