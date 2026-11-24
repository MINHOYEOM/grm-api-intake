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

    def test_run_audit_uses_the_latest_web_brief(self):
        self._write("brief_web_2026_07_06.json", _web_brief("2026-07-06", [_web_card("c1")]))
        result = vpb.run_audit(verify=False, web_briefs_dir=self.tmp)
        self.assertTrue(result["ok"])

    def test_run_audit_reports_missing_web_brief(self):
        result = vpb.run_audit(verify=False, web_briefs_dir=self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["skip_class"], "infra")


if __name__ == "__main__":
    unittest.main()
