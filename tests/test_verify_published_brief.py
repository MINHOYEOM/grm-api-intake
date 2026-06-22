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
BASE_URLS = [HANDOFF_ROWS[0]["official_url"]]

NEDRUG_HANDOFF_ROWS = [
    {"source": "MFDS", "document_id": "admin-2026004434",
     "card_scaffold": (
         "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?"
         "dispsApplySeq=2026004434)"
     )},
]

HC_HANDOFF_ROWS = [
    {"source": "Health Canada", "document_id": "hc-lancora",
     "card_scaffold": (
         "[링크](https://recalls-rappels.canada.ca/en/alert-recall/"
         "lancora-tablets-broken-partial-tablets-blister-pack)"
     )},
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
            HANDOFF_ROWS, BASE_URLS + ["https://www.mfds.go.kr/brd/m_99/view.do?seq=46893"])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].code, "L17-MFDS-PROVENANCE")
        self.assertEqual(info, [])

    def test_nedrug_scaffold_reconstructed_as_m74_is_alert(self):
        alerts, _info = vpb.classify(
            NEDRUG_HANDOFF_ROWS,
            ["https://www.mfds.go.kr/brd/m_74/view.do?seq=2026004434"],
        )
        codes = {a.code for a in alerts}
        self.assertIn("L17-SCAFFOLD-FOOTER-MISSING", codes)

    def test_hc_pack_to_package_is_alert_all_domains(self):
        alerts, _info = vpb.classify(
            HC_HANDOFF_ROWS,
            ["https://recalls-rappels.canada.ca/en/alert-recall/"
             "lancora-tablets-broken-partial-tablets-blister-package"],
        )
        codes = {a.code for a in alerts}
        self.assertIn("L17-SCAFFOLD-FOOTER-MISSING", codes)

    def test_grounded_mfds_passes(self):
        alerts, info = vpb.classify(
            HANDOFF_ROWS, ["https://www.mfds.go.kr/brd/m_218/view.do?seq=33716"])
        self.assertEqual(alerts, [])
        self.assertEqual(info, [])

    def test_rendered_scope_excludes_skipped_tier1_no_false_positive(self):
        """렌더된 카드(변형됨)만 footer 검사 — 의도적으로 생략된 Tier1 row 의 scaffold
        footer 는 발행본에 없어도 오탐하지 않는다(과알림 0). 핵심 회귀: 36 row 중 14 만
        렌더된 실제 운영 분포에서 ~18 건 오탐을 막는다."""
        rendered = {
            "source": "MFDS", "document_id": "admin-2026004434",
            "card_scaffold": (
                "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?"
                "dispsApplySeq=2026004434)")}
        skipped_tier1 = {
            "source": "Federal Register", "document_id": "2026-12165",
            "card_scaffold": (
                "[링크](https://www.federalregister.gov/documents/2026/06/17/"
                "2026-12165/medical-devices-classification)")}
        # 발행본: 렌더 카드(MFDS)만 존재 — 그 카드 footer 가 m_74 로 변형됨.
        published_urls = ["https://www.mfds.go.kr/brd/m_74/view.do?seq=2026004434"]
        # 평문엔 렌더된 카드의 문서번호만 있고 생략된 Tier1 문서번호는 없다.
        published_text = "행정처분 경방신약(주) 문서번호 admin-2026004434 제조업무정지"
        alerts, _info = vpb.classify(
            [rendered, skipped_tier1], published_urls, published_text=published_text)
        footer = [a for a in alerts if a.code == "L17-SCAFFOLD-FOOTER-MISSING"]
        # 렌더된 MFDS 카드의 nedrug 변형 1건만 — 생략된 FR row 는 오탐 0.
        self.assertEqual(len(footer), 1)
        self.assertIn("nedrug.mfds.go.kr", footer[0].url)
        self.assertFalse(any("federalregister" in a.url for a in footer))

    def test_without_published_text_flags_all_backward_compat(self):
        """published_text 없으면 전수 검사(하위호환) — 생략 row footer 도 잡힌다.
        스코프 인자가 오탐 차단의 핵심임을 대조로 확인."""
        rendered = {
            "source": "MFDS", "document_id": "admin-2026004434",
            "card_scaffold": (
                "[링크](https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?"
                "dispsApplySeq=2026004434)")}
        skipped_tier1 = {
            "source": "Federal Register", "document_id": "2026-12165",
            "card_scaffold": (
                "[링크](https://www.federalregister.gov/documents/2026/06/17/"
                "2026-12165/medical-devices-classification)")}
        published_urls = ["https://www.mfds.go.kr/brd/m_74/view.do?seq=2026004434"]
        alerts, _info = vpb.classify([rendered, skipped_tier1], published_urls)
        footer = [a for a in alerts if a.code == "L17-SCAFFOLD-FOOTER-MISSING"]
        self.assertEqual(len(footer), 2)  # 스코프 없으면 생략 row 도 오탐(=수정 전 동작)

    def test_nonmfds_bad_verdict_upgrades_to_alert(self):
        alerts, info = vpb.classify(
            HANDOFF_ROWS, BASE_URLS + ["https://www.fda.gov/invented/wl-999"],
            verdict=lambda u: vpb.VERDICT_BAD)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].code, "L17-UNGROUNDED")

    def test_nonmfds_unknown_verdict_is_info_not_alert(self):
        """과알림 0: 일시 네트워크 실패(unknown)는 알림 아님(info)."""
        alerts, info = vpb.classify(
            HANDOFF_ROWS, BASE_URLS + ["https://www.fda.gov/maybe/down"],
            verdict=lambda u: vpb.VERDICT_UNKNOWN)
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)

    def test_nonmfds_ok_verdict_is_info(self):
        alerts, info = vpb.classify(
            HANDOFF_ROWS, BASE_URLS + ["https://www.fda.gov/live/wl-1"],
            verdict=lambda u: vpb.VERDICT_OK)
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)

    def test_no_verdict_treats_nonmfds_as_info(self):
        alerts, info = vpb.classify(HANDOFF_ROWS, BASE_URLS + ["https://www.fda.gov/x"])
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)

    def test_fetched_search_url_with_waf_unknown_is_not_alert(self):
        url = "https://www.fda.gov/inspections/warning-letters/acme-2026"
        alerts, info = vpb.classify(
            HANDOFF_ROWS, BASE_URLS + [url],
            verdict=lambda u: vpb.VERDICT_UNKNOWN,
            allowed_fetched=[url],
        )
        self.assertEqual(alerts, [])
        self.assertEqual(info, [])

    def test_autofix_replacement_pairs_incident_urls(self):
        replacements = vpb.build_autofix_replacements(
            HC_HANDOFF_ROWS,
            ["https://recalls-rappels.canada.ca/en/alert-recall/"
             "lancora-tablets-broken-partial-tablets-blister-package"],
        )
        self.assertEqual(
            replacements,
            {
                "https://recalls-rappels.canada.ca/en/alert-recall/"
                "lancora-tablets-broken-partial-tablets-blister-package":
                "https://recalls-rappels.canada.ca/en/alert-recall/"
                "lancora-tablets-broken-partial-tablets-blister-pack"
            },
        )


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
                               return_value=[
                                   _callout_with_link(HANDOFF_ROWS[0]["official_url"]),
                                   _callout_with_link(
                                       "https://www.mfds.go.kr/brd/m_99/view.do?seq=1"),
                               ]):
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
                               return_value=[_paragraph("발행일: 2026-06-17 화요일"),
                                             _callout_with_link(HANDOFF_ROWS[0]["official_url"])]):
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


class TestRunCoverage(unittest.TestCase):
    """W2: 발행 후 탐지 — 수집 현황 '수집' 숫자가 handoff 정본과 어긋나면 FAIL 로 잡는다.

    detective 는 handoff rows(여기 MFDS 1건)로 정본을 재집계(coverage_source_counts +
    build_coverage_collected)해 발행물 수집 callout 과 대조한다(네트워크 0 → 과알림 0).
    """

    def _run_with_brief_text(self, coverage_text):
        with mock.patch.object(vpb, "fetch_latest_brief",
                               return_value={"id": "p", "url": "u", "title": "Brief"}), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=HANDOFF_ROWS), \
             mock.patch.object(vpb, "fetch_brief_blocks",
                               return_value=[_paragraph(coverage_text),
                                             _callout_with_link(HANDOFF_ROWS[0]["official_url"])]):
            return vpb.run("tok", weekly_db_id="w", intake_db_id="i", verify=False)

    def test_collected_mismatch_is_alert(self):
        # handoff 정본 = MFDS 1건(total 1). 발행물이 9건/MFDS 9 로 집계 → FAIL.
        j = self._run_with_brief_text("Intake row 9건 (MFDS 9)")
        self.assertFalse(j["ok"])
        codes = [a["code"] for a in j["alerts"]]
        self.assertIn("PL15-COVERAGE-TOTAL", codes)
        self.assertIn("PL15-COVERAGE-SOURCE", codes)

    def test_collected_match_passes(self):
        # 발행물 수집 = 정본과 일치(MFDS 1, 나머지 known 0 은 생략 정상) → 과알림 0.
        j = self._run_with_brief_text("Intake row 1건 (MFDS 1)")
        self.assertTrue(j["ok"])
        self.assertEqual(j["fail_count"], 0)

    def test_no_coverage_callout_no_alert(self):
        # 수집 callout 부재(요일 푸터만) → 대조 불가, 추측 금지(과알림 0).
        j = self._run_with_brief_text("발행일: 2026-06-17 수요일")
        self.assertTrue(j["ok"])


class TestRunScaffoldCells(unittest.TestCase):
    """Track A — 발행 후 탐지가 scaffold 고정 셀 전사오류(PL18)를 FAIL 로 잡는다(06-22 FDA 483).

    handoff card_scaffold(W2 고정 셀) ↔ 발행 본문 평문을 결정론 대조(네트워크 0 → 과알림 0).
    """

    FDA483_ROW = {
        "source": "FDA 483", "document_id": "fda483-192439",
        "card_scaffold": (
            "### [FDA 483 실사 관찰 · FDA] BPI Labs, LLC — **{{TITLE_ISSUE}}**\n\n"
            "<table>\n"
            "<tr><td>**발행일**</td><td>2026-05-27</td></tr>\n"
            "<tr><td>**문서번호**</td><td>`fda483-192439`</td></tr>\n"
            "<tr><td>**제조소/업체**</td><td>BPI Labs, LLC · FEI 3015156709</td></tr>\n"
            "<tr><td>**시설 · 유형**</td><td>Outsourcing Facility · 483</td></tr>\n"
            "</table>"),
    }

    def _run(self, blocks):
        with mock.patch.object(vpb, "fetch_latest_brief",
                               return_value={"id": "p", "url": "u", "title": "Brief"}), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=[self.FDA483_ROW]), \
             mock.patch.object(vpb, "fetch_brief_blocks", return_value=blocks):
            return vpb.run("tok", weekly_db_id="w", intake_db_id="i", verify=False)

    def _cells(self, fei):
        # 발행 카드 평문: 문서번호 보존(렌더 판정) + FEI 칸만 가변.
        return [_paragraph("발행일 2026-05-27"),
                _paragraph("문서번호 fda483-192439"),
                _paragraph(f"제조소/업체 BPI Labs, LLC · FEI {fei}"),
                _paragraph("시설 · 유형 Outsourcing Facility · 483")]

    def test_fei_mutation_detected(self):
        j = self._run(self._cells("3016534068"))   # 3015156709 -> 3016534068 (6/22 오류)
        self.assertFalse(j["ok"])
        self.assertIn("PL18-SCAFFOLD-CELL", [a["code"] for a in j["alerts"]])

    def test_verbatim_card_passes(self):
        j = self._run(self._cells("3015156709"))    # scaffold 그대로 전사 → 과알림 0
        self.assertTrue(j["ok"], msg=j["alerts"])
        self.assertEqual(j["fail_count"], 0)

    def test_unrendered_card_not_flagged(self):
        # 문서번호가 발행본에 없으면(미렌더) 검사 제외 — FEI 변형이어도 무알림.
        blocks = [_paragraph("제조소/업체 BPI Labs, LLC · FEI 3016534068")]
        j = self._run(blocks)
        self.assertTrue(j["ok"], msg=j["alerts"])


if __name__ == "__main__":
    unittest.main()
