# -*- coding: utf-8 -*-
"""collect_edqm_cep 오프라인 회귀(네트워크 0).

`parse_cep_actions` 는 순수 함수라 실제 응답 스냅샷(fixtures/edqm_actions_on_ceps.html,
2026-08-11 실측)을 그대로 먹인다. `collect_edqm_cep` 레벨 테스트는 `grm_common.log`
을 통해 노출되는 `http_get_html` 을 패치해 네트워크를 끊는다.
"""
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_edqm_cep as mod

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "edqm_actions_on_ceps.html")
START, END = date(2026, 1, 1), date(2026, 12, 31)


def _load_fixture() -> str:
    with open(FIXTURE_PATH, encoding="utf-8", errors="replace") as fh:
        return fh.read()


class TestParseCepActionsFixture(unittest.TestCase):
    """실측 스냅샷(2026-08-11) 전수 검증."""

    @classmethod
    def setUpClass(cls):
        cls.html = _load_fixture()
        cls.actions, cls.health = mod.parse_cep_actions(cls.html)

    def test_total_row_count(self):
        # 실측 메모의 "19행" 합계와 일치(개별 카테고리 세부 카운트는 아래 별도 검증 —
        # 코드로 재확인한 결과 "Due to GMP non-compliance"(Suspensions) 은 12건이 아니라
        # 14건이었다. 합계 19는 메모와 일치하지만 이 세부 항목은 메모의 기재 오차로
        # 보인다 -- 이 테스트는 실측 HTML 에서 직접 다시 센 값을 정답으로 삼는다.)
        self.assertEqual(len(self.actions), 19)
        self.assertEqual(self.health.data_rows, 19)

    def test_sections_all_found(self):
        self.assertEqual(self.health.sections_found, {
            "CEP Suspensions": True,
            "CEP Withdrawals": True,
            "Restoration of suspended CEP": True,
        })

    def test_looking_for_a_cep_section_ignored(self):
        # 화이트리스트 밖 마커("Looking for a CEP?")는 애초에 sections_found 딕셔너리에
        # 키조차 생기지 않는다(화이트리스트 3종 고정).
        self.assertNotIn("Looking for a CEP?", self.health.sections_found)

    def test_seven_tables_total(self):
        self.assertEqual(self.health.tables_found, 7)
        self.assertEqual(self.health.tables_by_section, {
            "CEP Suspensions": 3,
            "CEP Withdrawals": 3,
            "Restoration of suspended CEP": 1,
        })

    def test_empty_tables_count(self):
        # Suspensions/certification-procedure("-|-|-" 1행) + Withdrawals/gmp-non-compliance
        # ("-|-|-" 1행) + Withdrawals/monograph-deleted("-|-|-" 1행) + Restoration(tbody
        # 완전 공백, tr 자체가 0개) = 4.
        self.assertEqual(self.health.empty_tables, 4)

    def test_no_date_or_reason_failures_on_clean_fixture(self):
        self.assertEqual(self.health.date_parse_failures, 0)
        self.assertEqual(self.health.reason_other_count, 0)
        self.assertEqual(self.health.reason_other_samples, [])

    def test_holder_request_suspension_count(self):
        rows = [a for a in self.actions if a.reason_code == "holder-request"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(a.action == "suspension" for a in rows))

    def test_gmp_non_compliance_suspension_count(self):
        rows = [a for a in self.actions
                if a.reason_code == "gmp-non-compliance" and a.action == "suspension"]
        self.assertEqual(len(rows), 14)  # 실측 재확인값(메모 "12건" 과 다름 -- 보고서 참조)

    def test_withdrawal_certification_procedure_count(self):
        rows = [a for a in self.actions
                if a.reason_code == "certification-procedure" and a.action == "withdrawal"]
        self.assertEqual(len(rows), 2)

    def test_restoration_zero_rows(self):
        rows = [a for a in self.actions if a.action == "restoration"]
        self.assertEqual(rows, [])

    def test_fulfil_and_fulfill_map_to_same_reason_code(self):
        # Suspensions 의 certification-procedure 표는 "-|-|-" 뿐이라 실제 데이터 행이
        # 없다 -- 두 철자("fulfil"/"fulfill")가 같은 reason_code 로 매핑되는지는 classify
        # 함수 자체로 직접 검증한다.
        self.assertEqual(
            mod._classify_reason(
                "Due to a failure to fulfill the requirements of the Certification procedure",
                "CEP Suspensions"),
            "certification-procedure")
        self.assertEqual(
            mod._classify_reason(
                "Due to a failure to fulfil the requirements of the Certification Procedure",
                "CEP Withdrawals"),
            "certification-procedure")

    def test_diosmin_nbsp_cleaned(self):
        diosmin_rows = [a for a in self.actions if a.substance.startswith("Diosmin")]
        self.assertEqual(len(diosmin_rows), 2)
        substances = sorted(a.substance for a in diosmin_rows)
        self.assertEqual(substances, ["Diosmin", "Diosmin"])  # &nbsp; 잔재 없이 완전 일치
        cep_numbers = sorted(a.cep_number for a in diosmin_rows)
        self.assertEqual(cep_numbers, ["CEP 2016-173", "CEP 2024-194"])

    def test_date_iso_conversion(self):
        row = next(a for a in self.actions if a.cep_number == "CEP 2023-042")
        self.assertEqual(row.action_date_raw, "27/07/2026")
        self.assertEqual(row.date_iso, "2026-07-27")

    def test_document_id_dedup_key_all_unique(self):
        keys = [f"{a.cep_number}|{a.action}|{a.reason_code}|{a.date_iso}" for a in self.actions]
        self.assertEqual(len(keys), len(set(keys)))

    def test_cep_number_normalized(self):
        for act in self.actions:
            self.assertEqual(act.cep_number, act.cep_number.strip().upper())
            self.assertNotIn("  ", act.cep_number)


class TestClassifyReason(unittest.TestCase):
    def test_holder_request(self):
        self.assertEqual(
            mod._classify_reason(
                "Upon request from the holder, due to a temporary inability to produce "
                "under the approved conditions", "CEP Suspensions"),
            "holder-request")

    def test_gmp_non_compliance(self):
        self.assertEqual(
            mod._classify_reason("Due to GMP non-compliance", "CEP Withdrawals"),
            "gmp-non-compliance")

    def test_monograph_deleted(self):
        self.assertEqual(
            mod._classify_reason(
                "Due to the deletion of the monograph from the European Pharmacopoeia",
                "CEP Withdrawals"),
            "monograph-deleted")

    def test_restoration_section_always_restoration(self):
        # Restoration 섹션은 사유 소구분이 없다 -- reason_text 내용과 무관하게 고정.
        self.assertEqual(mod._classify_reason("", "Restoration of suspended CEP"), "restoration")
        self.assertEqual(
            mod._classify_reason("anything at all", "Restoration of suspended CEP"),
            "restoration")

    def test_unrecognized_reason_falls_to_other(self):
        self.assertEqual(
            mod._classify_reason("Due to some brand-new reason nobody has seen", "CEP Suspensions"),
            "other")


class TestParseDdmmyyyy(unittest.TestCase):
    def test_clean_date(self):
        self.assertEqual(mod._parse_ddmmyyyy("27/07/2026"), "2026-07-27")

    def test_doubled_slash_defensively_parsed(self):
        # 2026-06-11 선행 조사 실측 오타 형태 -- 조용히 죽지 않고 방어적으로 파싱된다.
        self.assertEqual(mod._parse_ddmmyyyy("22/09//2023"), "2023-09-22")

    def test_garbage_returns_empty_not_raise(self):
        self.assertEqual(mod._parse_ddmmyyyy("not-a-date"), "")
        self.assertEqual(mod._parse_ddmmyyyy(""), "")
        self.assertEqual(mod._parse_ddmmyyyy("-"), "")

    def test_invalid_calendar_date_returns_empty(self):
        self.assertEqual(mod._parse_ddmmyyyy("31/02/2026"), "")  # 2월 31일은 없다


class TestEmptyTableSynthetic(unittest.TestCase):
    """`- | - | -` 플레이스홀더 단독 표는 0건으로 처리된다(합성 최소 HTML)."""

    def test_dash_placeholder_row_yields_zero_data_rows(self):
        html_doc = """
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Suspensions">
        <h4><strong>Due to GMP non-compliance</strong></h4>
        <table><tbody><tr><td>-</td><td>-</td><td>-</td></tr></tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Withdrawals">
        <table><tbody></tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - Restoration of suspended CEP">
        <table><tbody></tbody></table>
        </div>
        """
        actions, health = mod.parse_cep_actions(html_doc)
        self.assertEqual(actions, [])
        self.assertEqual(health.data_rows, 0)
        # Suspensions(더미행 "-|-|-") + Withdrawals(빈 tbody) + Restoration(빈 tbody) = 3.
        self.assertEqual(health.empty_tables, 3)
        # Withdrawals 섹션엔 h4 없이 table 하나뿐 -- reason_code 는 h4 부재라 "other" 로
        # 분류될 잠재값이지만, 표 자체가 데이터 0건이라 행 루프를 안 돌아 reason_other_count
        # 는 증가하지 않는다(집계는 데이터 행 단위이지 표 단위가 아니다).
        self.assertEqual(health.reason_other_count, 0)


class TestReasonOtherSurfaced(unittest.TestCase):
    """매칭 실패(reason_code=other) 발생 시 조용히 흘리지 않고 카운트+샘플로 표면화."""

    def test_unknown_reason_counted_and_sampled(self):
        html_doc = """
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Suspensions">
        <h4><strong>Due to some brand-new unheard-of reason</strong></h4>
        <table><tbody>
        <tr><td>01/01/2026</td><td>Substance X</td><td>CEP 2026-001</td></tr>
        </tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Withdrawals">
        <table><tbody></tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - Restoration of suspended CEP">
        <table><tbody></tbody></table>
        </div>
        """
        actions, health = mod.parse_cep_actions(html_doc)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].reason_code, "other")
        self.assertEqual(health.reason_other_count, 1)
        self.assertEqual(health.reason_other_samples, ["Due to some brand-new unheard-of reason"])


class TestMissingSectionDefensiveDate(unittest.TestCase):
    def test_broken_date_row_dropped_and_counted_not_silently(self):
        html_doc = """
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Suspensions">
        <h4><strong>Due to GMP non-compliance</strong></h4>
        <table><tbody>
        <tr><td>22/09//2023</td><td>Diosmin&nbsp;</td><td>CEP 2016-999</td></tr>
        <tr><td>totally-broken</td><td>Other Substance</td><td>CEP 2016-998</td></tr>
        </tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Withdrawals">
        <table><tbody></tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - Restoration of suspended CEP">
        <table><tbody></tbody></table>
        </div>
        """
        actions, health = mod.parse_cep_actions(html_doc)
        # 첫 행(슬래시 중복)은 방어 파싱으로 살아남고, 둘째 행(완전 깨짐)만 버려진다.
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].substance, "Diosmin")
        self.assertEqual(actions[0].date_iso, "2023-09-22")
        self.assertEqual(health.date_parse_failures, 1)


# ── collect_edqm_cep() 레벨 — http_get_html 패치, 네트워크 0 ──────────────────


def _patched_html(text):
    return mock.patch("collect_edqm_cep.http_get_html", return_value=text)


class TestCollectEdqmCepFixture(unittest.TestCase):
    """실측 스냅샷을 collect_edqm_cep() 전 경로(IntakeItem 조립까지)로 흘려 검증."""

    @classmethod
    def setUpClass(cls):
        cls.html = _load_fixture()

    def test_happy_path_item_count_and_shape(self):
        with _patched_html(self.html):
            items, err = mod.collect_edqm_cep(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 19)

        gmp_item = next(i for i in items if i.raw_payload["cep_number"] == "CEP 2023-042")
        self.assertEqual(gmp_item.source, "EDQM CEP Actions")
        self.assertEqual(gmp_item.document_id, "CEP 2023-042|suspension|gmp-non-compliance|2026-07-27")
        self.assertEqual(gmp_item.date_iso, "2026-07-27")
        self.assertEqual(gmp_item.firm, "")
        self.assertEqual(gmp_item.site_country, "")
        self.assertEqual(gmp_item.official_url, "https://www.edqm.eu/en/actions-on-ceps")
        self.assertEqual(gmp_item.source_url, "https://www.edqm.eu/en/actions-on-ceps")
        self.assertEqual(gmp_item.api_query, "https://www.edqm.eu/en/actions-on-ceps")
        self.assertEqual(gmp_item.type_or_class, "cep-action")
        self.assertEqual(gmp_item.language, "EN")
        self.assertEqual(gmp_item.region_jurisdiction, "EU/EDQM")
        self.assertEqual(gmp_item.evidence_candidate, "A")
        self.assertEqual(gmp_item.signal_tier, "Tier 3")  # gmp-non-compliance
        self.assertEqual(
            gmp_item.headline,
            "Dexamethasone sodium phosphate — CEP 정지 (GMP 비준수)")
        self.assertEqual(gmp_item.raw_payload["reason_text"], "Due to GMP non-compliance")
        self.assertEqual(gmp_item.raw_payload["action_date"], "27/07/2026")

        holder_item = next(i for i in items if i.raw_payload["cep_number"] == "CEP 2007-338")
        self.assertEqual(holder_item.signal_tier, "Tier 2")

    def test_window_filters_out_of_range_dates(self):
        with _patched_html(self.html):
            items, err = mod.collect_edqm_cep(date(2026, 8, 1), date(2026, 12, 31))
        self.assertIsNone(err)
        self.assertEqual(items, [])  # 전 항목이 2026-08-01 이전(최신 27/07/2026)


class TestCollectEdqmCepNetworkFailure(unittest.TestCase):
    def test_http_failure_returns_error_not_silent_empty(self):
        with mock.patch("collect_edqm_cep.http_get_html", side_effect=RuntimeError("HTTP 500")):
            items, err = mod.collect_edqm_cep(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("네트워크", err)


class TestCollectEdqmCepMissingSection(unittest.TestCase):
    def test_missing_section_promotes_error(self):
        html_doc = """
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Suspensions">
        <table><tbody></tbody></table>
        </div>
        """  # Withdrawals/Restoration 마커 자체가 없음 -- 구조 변경 의심
        with _patched_html(html_doc):
            items, err = mod.collect_edqm_cep(START, END)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("섹션 누락", err)


class TestCollectEdqmCepZeroIsNormal(unittest.TestCase):
    def test_all_empty_tables_yields_zero_items_no_error(self):
        html_doc = """
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Suspensions">
        <h4><strong>Due to GMP non-compliance</strong></h4>
        <table><tbody><tr><td>-</td><td>-</td><td>-</td></tr></tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - CEP Withdrawals">
        <table><tbody></tbody></table>
        </div>
        <div data-analytics-asset-title="X - Actions on CEPs - Restoration of suspended CEP">
        <table><tbody></tbody></table>
        </div>
        """
        with _patched_html(html_doc):
            items, err = mod.collect_edqm_cep(START, END)
        self.assertIsNone(err)   # 0건은 정상 -- 저빈도 롤링 창 소스
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
