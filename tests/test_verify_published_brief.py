"""verify_published_brief — 발행 후 provenance 탐지(detective)의 순수 코어 회귀 (W1).

블록 URL 추출(Notion 블록 JSON)·분류(과알림 0)·audit JSON·verdict 환원을 동결한다.
Notion I/O 는 lazy import 라 순수 코어 테스트는 네트워크/requests 불필요.
"""
import json
import os
import shutil
import sys
import tempfile
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


class TestFetchLatestBriefSort(unittest.TestCase):
    """회귀: `발행일` 속성 내림차순(1차)로 정렬 요청해야 한다.

    created_time(생성시각) 단독 정렬은 나중에 생성된 과거 주차 중복 페이지를
    최신으로 잘못 뽑는 사고를 냈다(Issue #110 — 06-17 발행 중복 페이지가
    06-22 진짜 최신 페이지보다 먼저 선택됨). Notion 서버가 실제 정렬을 수행하므로
    여기서는 클라이언트가 올바른 정렬 조건을 요청하는지만 검증한다."""

    def test_requests_published_date_desc_as_primary_sort(self):
        import collect_intake as ci

        fake_response = {"results": [
            {"id": "p1", "url": "u1",
             "properties": {"Name": {"type": "title",
                                      "title": [{"plain_text": "Brief"}]}}},
        ]}
        with mock.patch.object(ci, "notion_api_request",
                                return_value=fake_response) as mock_req:
            vpb.fetch_latest_brief("tok", "db123")

        self.assertEqual(mock_req.call_count, 1)
        _args, kwargs = mock_req.call_args
        sorts = kwargs["body"]["sorts"]
        self.assertEqual(sorts[0], {"property": "발행일", "direction": "descending"})
        self.assertEqual(sorts[1],
                          {"timestamp": "created_time", "direction": "descending"})

    def test_created_time_alone_is_not_the_only_sort(self):
        """created_time 단독 정렬(수정 전 버그 재발)을 회귀 방지로 명시 배제."""
        import collect_intake as ci

        with mock.patch.object(ci, "notion_api_request",
                                return_value={"results": []}) as mock_req:
            vpb.fetch_latest_brief("tok", "db123")

        sorts = mock_req.call_args.kwargs["body"]["sorts"]
        self.assertNotEqual(
            sorts, [{"timestamp": "created_time", "direction": "descending"}])


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


class TestSkipClass(unittest.TestCase):
    """W1 탐지선 침묵 방지: skip 을 infra(탐지선 죽음)/content(대상 부재)로 분류한다.

    CI(grm-brief-audit)가 이 값으로 분기해 **infra skip 만** 스케줄에서 경보한다(과알림 0).
    각 skip 경로의 skip_class 를 동결한다 — 6 경로 = infra 4 · content 2.
    """

    _BRIEF = {"id": "p", "url": "u", "title": "t"}

    def test_skipped_json_carries_skip_class(self):
        self.assertEqual(vpb.skipped_json("x", skip_class="infra")["skip_class"], "infra")
        self.assertEqual(vpb.skipped_json("x", skip_class="content")["skip_class"], "content")
        self.assertEqual(vpb.skipped_json("x")["skip_class"], "")  # 기본값 = 미분류

    def test_no_token_is_infra(self):
        j = vpb.run("", weekly_db_id="w", intake_db_id="i")
        self.assertEqual(j["skip_class"], "infra")
        self.assertIn("NOTION_TOKEN", j["note"])

    def test_weekly_brief_query_failure_is_infra(self):
        with mock.patch.object(vpb, "fetch_latest_brief", side_effect=RuntimeError("boom")):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i")
        self.assertEqual(j["skip_class"], "infra")
        self.assertIn("Weekly Brief 조회 실패", j["note"])

    def test_weekly_brief_missing_is_content(self):
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=None):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i")
        self.assertEqual(j["skip_class"], "content")

    def test_handoff_query_failure_is_infra(self):
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=self._BRIEF), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               side_effect=RuntimeError("boom")):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i")
        self.assertEqual(j["skip_class"], "infra")
        self.assertIn("handoff", j["note"])

    def test_consumed_handoff_missing_is_content(self):
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=self._BRIEF), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows", return_value=[]):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i")
        self.assertEqual(j["skip_class"], "content")

    def test_brief_body_fetch_failure_is_infra(self):
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=self._BRIEF), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=HANDOFF_ROWS), \
             mock.patch.object(vpb, "fetch_brief_blocks", side_effect=RuntimeError("boom")):
            j = vpb.run("tok", weekly_db_id="w", intake_db_id="i")
        self.assertEqual(j["skip_class"], "infra")

    def test_six_skip_paths_are_infra4_content2(self):
        """분포 동결: 6 skip 경로 = infra 4 · content 2 (분류가 바뀌면 이 테스트가 잡는다)."""
        classes = []
        classes.append(vpb.run("", weekly_db_id="w", intake_db_id="i")["skip_class"])
        with mock.patch.object(vpb, "fetch_latest_brief", side_effect=RuntimeError("x")):
            classes.append(vpb.run("tok", weekly_db_id="w", intake_db_id="i")["skip_class"])
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=None):
            classes.append(vpb.run("tok", weekly_db_id="w", intake_db_id="i")["skip_class"])
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=self._BRIEF), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               side_effect=RuntimeError("x")):
            classes.append(vpb.run("tok", weekly_db_id="w", intake_db_id="i")["skip_class"])
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=self._BRIEF), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows", return_value=[]):
            classes.append(vpb.run("tok", weekly_db_id="w", intake_db_id="i")["skip_class"])
        with mock.patch.object(vpb, "fetch_latest_brief", return_value=self._BRIEF), \
             mock.patch.object(vpb, "fetch_latest_consumed_handoff_rows",
                               return_value=HANDOFF_ROWS), \
             mock.patch.object(vpb, "fetch_brief_blocks", side_effect=RuntimeError("x")):
            classes.append(vpb.run("tok", weekly_db_id="w", intake_db_id="i")["skip_class"])
        self.assertEqual(classes.count("infra"), 4)
        self.assertEqual(classes.count("content"), 2)


class TestAuditJsonSkipClass(unittest.TestCase):
    """정상 경로(build_audit_json)는 skip_class truthy 값 부재 → 스케줄 infra-경보 대상 아님."""

    def test_build_audit_json_has_no_truthy_skip_class(self):
        j_fail = vpb.build_audit_json(
            [bl.LintFinding(bl.SEV_FAIL, "C", "u", "m")], [])
        self.assertFalse(j_fail.get("skip_class"))
        j_pass = vpb.build_audit_json([], [])
        self.assertFalse(j_pass.get("skip_class"))

    def test_skipped_json_schema_is_additive(self):
        """기존 스키마 필드 불변 — skip_class 만 추가된 additive 변경."""
        j = vpb.skipped_json("x", skip_class="infra")
        for key in ("ok", "run_date_kst", "brief", "fail_count", "info_count",
                    "alerts", "info", "note"):
            self.assertIn(key, j)
        self.assertTrue(j["ok"])
        self.assertEqual(j["brief"], {"title": "", "url": ""})
        self.assertEqual(j["fail_count"], 0)
        self.assertEqual(j["info_count"], 0)
        self.assertEqual(j["alerts"], [])
        self.assertEqual(j["info"], [])


class TestMainWeeklyDbIdEnvFallback(unittest.TestCase):
    """main() 의 GRM_WEEKLY_BRIEF_DB_ID 해석: CI 에서 미등록 vars 는 빈 문자열로
    치환되어 주입된다(키 자체가 사라지지 않음). os.environ.get(key, default) 는
    키가 존재하되 값이 "" 인 경우 default 로 폴백하지 않으므로, 빈 문자열이
    그대로 Notion database_id 로 쓰이면 API 요청 URL 이 깨진다(회귀 방지).

    main() 은 2026-07-07 부로 `run_audit()`(웹 우선·Notion 폴백)을 호출한다 — Notion
    db_id 해석 로직 자체는 불변이라 이 테스트는 mock 대상만 `run_audit` 으로 갱신한다.
    """

    def test_empty_env_falls_back_to_default_db_id(self):
        with mock.patch.dict(os.environ, {"GRM_WEEKLY_BRIEF_DB_ID": "",
                                           "NOTION_TOKEN": "tok"}), \
             mock.patch.object(vpb, "run_audit",
                               return_value=vpb.build_audit_json([], [])) as mock_run:
            vpb.main([])
        self.assertEqual(mock_run.call_args.kwargs["weekly_db_id"],
                          vpb.DEFAULT_WEEKLY_BRIEF_DB_ID)

    def test_nonempty_env_is_used_verbatim(self):
        with mock.patch.dict(os.environ, {"GRM_WEEKLY_BRIEF_DB_ID": "custom-db-id",
                                           "NOTION_TOKEN": "tok"}), \
             mock.patch.object(vpb, "run_audit",
                               return_value=vpb.build_audit_json([], [])) as mock_run:
            vpb.main([])
        self.assertEqual(mock_run.call_args.kwargs["weekly_db_id"], "custom-db-id")


# ─────────────────────────────────────────────────────────────────────────────
# 웹 발행본(web/data/briefs) 감사 — 2026-07-07 근본 전환. 파일 기반(임시 디렉터리에
# brief_web_*.json 을 직접 써서 검증) — Notion I/O 전혀 필요 없다.
# ─────────────────────────────────────────────────────────────────────────────
def _web_card(card_id, **overrides):
    card = {
        "id": card_id, "render_order": 0, "group": "글로벌", "group_label": None,
        "agency": "FDA", "card_type": "Warning Letter", "category": "Other",
        "modality": None, "evidence_level": "A", "signal_tier": 1, "signal_label": "High",
        "type_tag": "WL", "headline_target": "Acme Pharma",
        "title_issue": "품질 결함", "summary": "정상 요약",
        "facts": [{"label": "문서번호", "value": card_id}],
        "quotes": [], "evidence_basis": "공식 인덱스",
        "key_facts": ["정상 사실"], "implication": "정상 시사점",
        "checks": ["점검1", "점검2"],
        "sources": {"info_url": "https://www.fda.gov/info",
                    "official_url": "https://www.fda.gov/official",
                    "official_is_pdf": False,
                    "link_check": {"info": "pending", "official": "pending"}},
    }
    card.update(overrides)
    return card


def _web_brief(publish_date, cards, tldr=None):
    return {"schema_version": "grm-web-card/v1",
            "brief": {"run_date_kst": publish_date, "publish_date": publish_date,
                      "tldr": tldr or [], "coverage": {"intake_total": len(cards),
                                                        "rendered": len(cards),
                                                        "evidence": {"A": len(cards), "B": 0, "C": 0}}},
            "cards": cards}


class TestFindLatestWebBrief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return path

    def test_missing_dir_returns_none(self):
        self.assertIsNone(vpb.find_latest_web_brief(os.path.join(self.tmp, "nope")))

    def test_empty_dir_returns_none(self):
        self.assertIsNone(vpb.find_latest_web_brief(self.tmp))

    def test_picks_max_publish_date_not_filename_or_mtime(self):
        """파일명 알파벳 순서가 날짜 역순이어도 `brief.publish_date` 로 정본 판단."""
        self._write("brief_web_zzz_older.json", _web_brief("2026-06-22", [_web_card("c1")]))
        self._write("brief_web_aaa_newer.json", _web_brief("2026-07-06", [_web_card("c2")]))
        found = vpb.find_latest_web_brief(self.tmp)
        self.assertIsNotNone(found)
        _path, data = found
        self.assertEqual(data["brief"]["publish_date"], "2026-07-06")

    def test_malformed_file_skipped(self):
        path = os.path.join(self.tmp, "brief_web_bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        self._write("brief_web_good.json", _web_brief("2026-07-06", [_web_card("c1")]))
        found = vpb.find_latest_web_brief(self.tmp)
        self.assertIsNotNone(found)
        self.assertEqual(found[1]["brief"]["publish_date"], "2026-07-06")


class TestWebExtraction(unittest.TestCase):
    def test_extracts_only_llm_slots_not_code_fields(self):
        card = _web_card("c1", summary="요약문 https://leaked.example.com/x 포함")
        brief = _web_brief("2026-07-06", [card], tldr=["tldr1", "tldr2", "tldr3"])
        text = vpb.extract_web_llm_text(brief)
        self.assertIn("leaked.example.com", text)   # LLM 슬롯 안 URL 은 스캔 대상
        self.assertIn("tldr1", text)
        self.assertNotIn("fda.gov/official", text)  # 코드-verbatim sources 는 제외

    def test_allowed_urls_come_from_card_sources(self):
        card = _web_card("c1")
        brief = _web_brief("2026-07-06", [card])
        allowed = vpb.collect_web_allowed_urls(brief)
        self.assertIn(bl.normalize_url("https://www.fda.gov/info"), allowed)
        self.assertIn(bl.normalize_url("https://www.fda.gov/official"), allowed)


class TestClassifyWeb(unittest.TestCase):
    def test_clean_brief_passes(self):
        brief = _web_brief("2026-07-06", [_web_card("c1")])
        alerts, info = vpb.classify_web(brief, verify=False)
        self.assertEqual(alerts, [])

    def test_mfds_url_leaked_in_summary_is_alert(self):
        card = _web_card(
            "c1", agency="MFDS",
            summary="식약처 발표 https://www.mfds.go.kr/brd/m_99/view.do?seq=1 참고")
        brief = _web_brief("2026-07-06", [card])
        alerts, _info = vpb.classify_web(brief, verify=False)
        codes = {a.code for a in alerts}
        self.assertIn("L17-MFDS-PROVENANCE", codes)

    def test_grounded_url_in_summary_is_not_alert(self):
        """LLM 이 카드 자신의 official_url 을 자유텍스트에 그대로 인용하면 근거 있음."""
        card = _web_card("c1", summary="상세는 https://www.fda.gov/official 참고")
        brief = _web_brief("2026-07-06", [card])
        alerts, _info = vpb.classify_web(brief, verify=False)
        self.assertEqual(alerts, [])

    def test_nonmfds_ungrounded_bad_verdict_is_alert(self):
        card = _web_card("c1", summary="참고: https://www.fda.gov/invented/wl-999")
        brief = _web_brief("2026-07-06", [card])
        with mock.patch.object(vpb, "definitive_verdict", return_value=vpb.VERDICT_BAD):
            alerts, _info = vpb.classify_web(brief, verify=True)
        self.assertIn("L17-UNGROUNDED", {a.code for a in alerts})

    def test_nonmfds_ungrounded_unknown_verdict_is_info_not_alert(self):
        card = _web_card("c1", summary="참고: https://www.fda.gov/maybe/down")
        brief = _web_brief("2026-07-06", [card])
        with mock.patch.object(vpb, "definitive_verdict", return_value=vpb.VERDICT_UNKNOWN):
            alerts, info = vpb.classify_web(brief, verify=True)
        self.assertEqual(alerts, [])
        self.assertEqual(len(info), 1)

    def test_residual_token_in_implication_is_alert(self):
        card = _web_card("c1", implication="{{TITLE_ISSUE}} 관련 시사점")
        brief = _web_brief("2026-07-06", [card])
        alerts, _info = vpb.classify_web(brief, verify=False)
        self.assertIn("PL1-RESIDUAL-TOKEN", {a.code for a in alerts})

    def test_forbidden_markup_in_checks_is_alert(self):
        card = _web_card("c1", checks=["<toggle> 점검", "점검2"])
        brief = _web_brief("2026-07-06", [card])
        alerts, _info = vpb.classify_web(brief, verify=False)
        self.assertIn("PL3-FORBIDDEN-MD", {a.code for a in alerts})


class TestRunWebAndAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return path

    def test_run_web_clean_brief_ok(self):
        brief = _web_brief("2026-07-06", [_web_card("c1")], tldr=["a", "b", "c"])
        j = vpb.run_web("path.json", brief, verify=False)
        self.assertTrue(j["ok"])
        self.assertEqual(j["fail_count"], 0)
        self.assertEqual(j["brief"]["title"], "a")

    def test_run_web_leak_is_not_ok(self):
        card = _web_card("c1", agency="MFDS",
                         summary="https://www.mfds.go.kr/brd/m_99/view.do?seq=1")
        brief = _web_brief("2026-07-06", [card])
        j = vpb.run_web("path.json", brief, verify=False)
        self.assertFalse(j["ok"])
        self.assertEqual(j["fail_count"], 1)

    def test_run_audit_prefers_web_over_notion(self):
        """웹 발행본이 있으면 Notion 은 조회하지 않는다(중복 알림 방지)."""
        self._write("brief_web_2026_07_06.json",
                    _web_brief("2026-07-06", [_web_card("c1")]))
        with mock.patch.object(vpb, "run") as mock_notion_run:
            j = vpb.run_audit("tok", weekly_db_id="w", intake_db_id="i",
                              verify=False, web_briefs_dir=self.tmp)
        mock_notion_run.assert_not_called()
        self.assertTrue(j["ok"])

    def test_run_audit_falls_back_to_notion_when_no_web_brief(self):
        """웹 브리프 디렉터리가 비어 있으면(과도기·마이그레이션 전) 기존 Notion 경로로."""
        with mock.patch.object(vpb, "run",
                               return_value=vpb.skipped_json("no token", skip_class="infra")
                               ) as mock_notion_run:
            j = vpb.run_audit("", weekly_db_id="w", intake_db_id="i",
                              verify=False, web_briefs_dir=self.tmp)
        mock_notion_run.assert_called_once_with(
            "", weekly_db_id="w", intake_db_id="i", verify=False)
        self.assertEqual(j["skip_class"], "infra")


if __name__ == "__main__":
    unittest.main()
