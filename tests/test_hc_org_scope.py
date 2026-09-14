"""Health Canada 수집기 — Organization 범위(2026-09-14 사각지대 수리).

라이브 피드 실측(2026-09-14, 34,070 레코드): 의약품 회수·경보가 세 Organization 으로
나뉘어 나온다. 종전 필터는 "Drugs and health products" 하나만 받아, 2026-06~09 에
Teva-Pregabalin 교차오염 Type I(08-15)·Teva 아세트아미노펜 이물 혼입(09-10)·TAVNEOS
데이터 무결성 검토(07-07) 등 7건을 **오류 0·경보 0** 으로 버렸다. 뒤의 두 기관은 식품·
소비재·의료기기도 같은 이름으로 내므로 Category 로 걸러야 한다(이 파일이 그 경계를 잠근다).
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_hc as hc

START, END = date(2026, 8, 15), date(2026, 9, 14)

# 2026-09-10 라이브 피드 레코드 그대로(NID 82615) — 종전 필터가 버린 그 건.
NOVO_GESIC = {
    "NID": "82615",
    "Title": "Generic acetaminophen by Teva recalled because it may contain foreign material",
    "Last updated": "2026-09-10",
    "Organization": "Communications and Public Affairs Branch",
    "Category": "Drugs",
    "Recall class": "",
    "URL": "/en/alert-recall/generic-acetaminophen-teva-recalled-because-it-may-contain-foreign-material",
    "Product": "Novo-Gesic Forte 500 mg tablets (acetaminophen)",
    "Issue": "Contamination - Product quality",
}


def _rec(**over):
    base = dict(NOVO_GESIC)
    base.update(over)
    return base


class RecordScopeTest(unittest.TestCase):
    def test_public_advisory_branch_with_drug_category_is_in_scope(self) -> None:
        self.assertTrue(hc._record_in_scope(_rec()))

    def test_marketed_health_products_with_drug_category_is_in_scope(self) -> None:
        self.assertTrue(hc._record_in_scope(_rec(
            Organization="Marketed health products", Category="Drugs",
            Title="TAVNEOS (avacopan) and Health Canada's Review of Data Integrity Concerns")))

    def test_primary_org_keeps_previous_behaviour(self) -> None:
        # 주기관은 Category 와 무관하게 통과(배제는 _to_item 이 맡는다 — 회귀 0).
        self.assertTrue(hc._record_in_scope(_rec(Organization="Drugs and health products",
                                                 Category="Radiopharmaceuticals")))
        self.assertTrue(hc._record_in_scope(_rec(Organization="Drugs and health products",
                                                 Category="Veterinary drugs")))

    def test_secondary_orgs_need_a_health_product_category(self) -> None:
        for category in ("Food", "Medical devices", "Household items", ""):
            with self.subTest(category=category):
                self.assertFalse(hc._record_in_scope(_rec(Category=category)))
        # 병기 카테고리는 토큰 단위 — 의약품이 섞여 있으면 받는다.
        self.assertTrue(hc._record_in_scope(_rec(Category="Drugs - Medical devices")))
        self.assertTrue(hc._record_in_scope(_rec(Category="Natural health products")))
        self.assertTrue(hc._record_in_scope(_rec(
            Organization="Marketed health products", Category="Biologic or vaccine")))

    def test_veterinary_mix_is_excluded_for_secondary_orgs(self) -> None:
        self.assertFalse(hc._record_in_scope(_rec(Category="Drugs - Veterinary drugs")))

    def test_unrelated_orgs_stay_out_even_with_drug_category(self) -> None:
        for org in ("CFIA", "TC", "Consumer product safety", "Medical devices"):
            with self.subTest(org=org):
                self.assertFalse(hc._record_in_scope(_rec(Organization=org)))


class CollectHcRegressionTest(unittest.TestCase):
    """2026-09-10 Novo-Gesic Forte 가 윈도우 안에서 실제로 수집되는지 — 끝까지 잠근다."""

    def _collect(self, feed):
        with mock.patch.object(hc, "http_get_json", return_value=feed), \
             mock.patch.object(hc, "_fetch_recall_detail", return_value={}):
            return hc.collect_hc(START, END)

    def test_novo_gesic_forte_is_collected(self) -> None:
        items, err = self._collect([NOVO_GESIC])
        self.assertIsNone(err)
        self.assertEqual([it.document_id for it in items], ["hc-82615"])
        item = items[0]
        self.assertEqual(item.date_iso, "2026-09-10")
        # 등급 미표기 → 품질 키워드("foreign")로 Tier 2.
        self.assertEqual(item.signal_tier, "Tier 2")
        self.assertIn("Novo-Gesic Forte", item.body)
        self.assertEqual(item.official_url,
                         "https://recalls-rappels.canada.ca/en/alert-recall/"
                         "generic-acetaminophen-teva-recalled-because-it-may-contain-foreign-material")
        # 부서명은 회사가 아니다(P8 계약 유지).
        self.assertEqual(item.firm, "")

    def test_feed_with_only_secondary_org_records_is_not_a_schema_error(self) -> None:
        # 종전엔 주기관 0건 = "필드/값 변경 의심" 오류였다. 이제 다른 두 기관도 모집단이다.
        items, err = self._collect([NOVO_GESIC, _rec(NID="1", Organization="CFIA",
                                                     Category="Dairy")])
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)

    def test_secondary_org_food_and_device_records_are_dropped(self) -> None:
        feed = [
            _rec(NID="2", Category="Food", Title="Cheese recalled"),
            _rec(NID="3", Category="Medical devices", Title="Stent recalled"),
            _rec(NID="4", Organization="Drugs and health products", Category="Drugs",
                 Title="Some tablets recalled", **{"Recall class": "Type II"}),
        ]
        items, err = self._collect(feed)
        self.assertIsNone(err)
        self.assertEqual([it.document_id for it in items], ["hc-4"])


if __name__ == "__main__":
    unittest.main()
