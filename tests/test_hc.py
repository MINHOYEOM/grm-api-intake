"""Health Canada 수집기(collect_hc) 회귀 — P7(generic→Biologic)·P8(firm 교정).

opendata 피드는 브랜드명만 주므로(Hizentra 류), 상세 페이지에서 끌어온 유효성분
(Strength)을 compute_modality 가 보는 텍스트(body)에 주입해야 생물주사제가 Biologic
으로 잡힌다. 또 Organization("Drugs and health products")은 회사가 아니므로 firm 으로
쓰지 않는다(실제 회사 라벨이 있을 때만 firm, 없으면 빈 값 → 카드 '원문 미기재').

상세 페이지 fetch 는 단위테스트에서 detail_fetcher 스텁으로 주입한다(네트워크 없음).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_hc as hc
import collect_intake as ci
from datetime import date

START, END = date(2026, 1, 1), date(2026, 12, 31)


def _rec(**over):
    base = {
        "NID": "82142",
        "Title": "Hizentra: out of specification",
        "URL": "/en/alert-recall/hizentra-oos",
        "Organization": "Drugs and health products",
        "Product": "Hizentra",
        "Issue": "Product quality",
        "Category": "Drugs",
        "Recall class": "Type II",
        "Last updated": "2026-06-03",
    }
    base.update(over)
    return base


def _modality(item):
    return ci.compute_modality(item.raw_payload, item.headline, item.body,
                               item.type_or_class, item.firm)


class TestP7GenericToBiologic(unittest.TestCase):
    def test_brand_only_biologic_via_detail_strength(self):
        # 피드는 브랜드명(Hizentra)뿐 → 상세 Strength(IMMUNOGLOBULIN (HUMAN))로 Biologic.
        det = lambda u: {"product name": "Hizentra", "dosage form": "Solution",
                         "strength": "IMMUNOGLOBULIN (HUMAN) 200 mg/mL"}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertIn("IMMUNOGLOBULIN (HUMAN)", item.body)   # body 에 유효성분 주입됨
        self.assertEqual(_modality(item), ci.MODALITY_BIOLOGIC)

    def test_brand_only_chemical_without_detail_is_graceful(self):
        # 상세 fetch 실패({}) → 피드 단독 폴백. 브랜드명만으론 생물 식별 불가 → Chemical(무크래시).
        item = hc._to_item(_rec(), START, END, detail_fetcher=lambda u: {})
        self.assertNotIn("유효성분", item.body)
        self.assertEqual(_modality(item), ci.MODALITY_CHEMICAL)

    def test_no_fetcher_falls_back_to_feed_only(self):
        item = hc._to_item(_rec(), START, END)            # detail_fetcher 없음
        self.assertEqual(_modality(item), ci.MODALITY_CHEMICAL)

    def test_feed_immune_globulin_space_form_is_biologic(self):
        # 피드 Product 에 'Immune globulin'(공백형) — 상세 없이도 Biologic (용어 보강).
        rec = _rec(Product="Octagam 10% (Immune globulin intravenous, Human)",
                   Title="Octagam 10%: quality issue")
        item = hc._to_item(rec, START, END)
        self.assertEqual(_modality(item), ci.MODALITY_BIOLOGIC)


class TestP7ChemicalNotMisclassified(unittest.TestCase):
    """반례: 화학 제품이 Biologic 으로 오분류되지 않아야 한다."""

    def test_chemical_tablet_with_detail_stays_chemical(self):
        det = lambda u: {"strength": "Valsartan 50 mg", "dosage form": "Tablet"}
        rec = _rec(Product="Drug X 50 mg tablets", Title="Drug X recall",
                   Issue="nitrosamine impurity")
        item = hc._to_item(rec, START, END, detail_fetcher=det)
        self.assertEqual(_modality(item), ci.MODALITY_CHEMICAL)

    def test_small_molecule_injection_stays_chemical(self):
        det = lambda u: {"strength": "Heparin sodium 25000 unit/500 mL",
                         "dosage form": "Solution for injection"}
        rec = _rec(Product="Heparin Sodium in 5% Dextrose injection",
                   Title="Heparin Sodium: out of specification")
        item = hc._to_item(rec, START, END, detail_fetcher=det)
        self.assertEqual(_modality(item), ci.MODALITY_CHEMICAL)


class TestP8FirmMapping(unittest.TestCase):
    """Organization('Drugs and health products')을 firm 으로 쓰지 않는다."""

    def test_organization_dept_is_not_used_as_firm(self):
        item = hc._to_item(_rec(), START, END, detail_fetcher=lambda u: {})
        self.assertEqual(item.firm, "")                   # 부서명 → firm 으로 누수 금지
        self.assertNotIn("company", item.raw_payload)

    def test_real_company_label_becomes_firm(self):
        # 상세에 실제 회사 라벨이 있으면 firm 으로 채운다.
        det = lambda u: {"company": "BD Canada", "strength": "Chlorhexidine 2%"}
        rec = _rec(Product="ChloraPrep 1 mL", Title="BD Canada ChloraPrep recall")
        item = hc._to_item(rec, START, END, detail_fetcher=det)
        self.assertEqual(item.firm, "BD Canada")
        self.assertEqual(item.raw_payload.get("company"), "BD Canada")

    def test_brand_label_is_not_treated_as_company(self):
        # 'Brand' 셀은 제품 브랜드(=회사 아님)일 수 있어 firm 으로 쓰지 않는다.
        det = lambda u: {"brand": "Hizentra", "product name": "Hizentra",
                         "strength": "IMMUNOGLOBULIN (HUMAN) 200 mg/mL"}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertEqual(item.firm, "")


class TestDetailParser(unittest.TestCase):
    """_parse_detail_html / _detail_company 순수 파싱."""

    def test_parse_data_label_cells(self):
        html = ('<table><tr>'
                '<td class="x" data-label="Product Name">Hizentra</td>'
                '<td data-label="Strength">IMMUNOGLOBULIN (HUMAN) 200 mg/mL</td>'
                '<td data-label="Dosage Form">Solution</td>'
                '</tr></table>')
        d = hc._parse_detail_html(html)
        self.assertEqual(d["strength"], "IMMUNOGLOBULIN (HUMAN) 200 mg/mL")
        self.assertEqual(d["dosage form"], "Solution")
        self.assertEqual(hc._detail_company(d), "")        # 회사 라벨 없음

    def test_company_label_detected(self):
        self.assertEqual(hc._detail_company({"manufacturer": "Acme Pharma"}),
                         "Acme Pharma")

    def test_empty_html_returns_empty(self):
        self.assertEqual(hc._parse_detail_html(""), {})


if __name__ == "__main__":
    unittest.main()
