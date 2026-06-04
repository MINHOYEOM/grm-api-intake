"""제품군(Modality) 분류 회귀 테스트 (제품군 확장).

compute_modality 가 '큰 틀'(원료 성격) 3분류 — 화학합성의약품(Chemical) /
생물의약품(Biologic) / 기타(Other) — 로 분류하는지, 그리고 무균·바이오 품질
신호가 QA 관련성에서 누락(Unrelated)되지 않는지 확인한다.
특정 제품 단위가 아닌 클래스 단위 분류임에 유의.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci


class TestComputeModality(unittest.TestCase):
    # ── 생물의약품(Biologic) ──────────────────────────────────────────────
    def test_biologic_product_type(self):
        payload = {"openfda": {"product_type": ["BIOLOGIC"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_BIOLOGIC)

    def test_biologic_biosimilar_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Biosimilar monoclonal antibody comparability"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_biologic_vaccine_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Recombinant vaccine aseptic filling deficiency"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_biologic_mab_inn_suffix(self):
        # 단클론항체 INN 접미사 -mab (adalimumab 등)
        self.assertEqual(
            ci.compute_modality({}, "adalimumab injection lot recall"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_mab_substring_no_false_positive(self):
        # 'Mabel' 같은 단어의 'mab' 부분문자열로 오탐하지 않아야 함 (정제는 화학합성)
        self.assertEqual(
            ci.compute_modality({}, "Mabel Labs tablet recall"),
            ci.MODALITY_CHEMICAL,
        )

    def test_biologic_injectable_still_biologic(self):
        # 생물의약품은 주사제여도 'Biologic' (제형이 아닌 원료 성격 우선)
        payload = {"openfda": {"dosage_form": ["INJECTION"], "route": ["SUBCUTANEOUS"]}}
        self.assertEqual(
            ci.compute_modality(payload, "Recombinant therapeutic protein for injection"),
            ci.MODALITY_BIOLOGIC,
        )

    # ── 화학합성의약품(Chemical) ─────────────────────────────────────────
    def test_chemical_tablet(self):
        payload = {"openfda": {"dosage_form": ["TABLET"], "route": ["ORAL"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_CHEMICAL)

    def test_chemical_injection_small_molecule(self):
        # 생물 단서 없는 주사제 → 화학합성으로 분류
        payload = {"openfda": {"dosage_form": ["INJECTION"], "route": ["INTRAVENOUS"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_CHEMICAL)

    def test_chemical_oral_liquid_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Oral solution subpotent assay failure"),
            ci.MODALITY_CHEMICAL,
        )

    def test_chemical_capsule_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Extended-release capsule dissolution recall"),
            ci.MODALITY_CHEMICAL,
        )

    # ── 기타(Other) ──────────────────────────────────────────────────────
    def test_other_guidance(self):
        self.assertEqual(
            ci.compute_modality({}, "ICH Q9 quality risk management guideline"),
            ci.MODALITY_OTHER,
        )

    def test_other_general_gmp(self):
        self.assertEqual(
            ci.compute_modality({}, "Data integrity inspection observation"),
            ci.MODALITY_OTHER,
        )

    # ── MFDS 한국어 단서 (Language=KO) ──────────────────────────────────
    def test_biologic_korean(self):
        self.assertEqual(
            ci.compute_modality({}, "생물학적제제 제조소 GMP 실태조사 결과"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_biosimilar_korean(self):
        self.assertEqual(
            ci.compute_modality({}, "바이오시밀러 품목 회수·판매중지"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_chemical_korean_tablet(self):
        self.assertEqual(
            ci.compute_modality({}, "정제 함량 부적합 행정처분"),
            ci.MODALITY_CHEMICAL,
        )

    def test_chemical_korean_injection(self):
        self.assertEqual(
            ci.compute_modality({}, "주사제 무균 공정 지적사항"),
            ci.MODALITY_CHEMICAL,
        )

    # ── top-level product_type 폴백 (openfda 구조 없는 소스) ────────────
    def test_biologic_toplevel_product_type(self):
        payload = {"product_type": ["BIOLOGIC"]}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_BIOLOGIC)

    def test_chemical_toplevel_product_type_drugs(self):
        # OpenFDA enforcement product_type=Drugs (문자열) → 화학합성
        self.assertEqual(ci.compute_modality({"product_type": "Drugs"}), ci.MODALITY_CHEMICAL)

    def test_chemical_human_prescription_drug(self):
        payload = {"openfda": {"product_type": ["HUMAN PRESCRIPTION DRUG"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_CHEMICAL)

    def test_veterinary_not_chemical(self):
        # 수의/동물용은 의약품 분류 대상 아님 → Other
        self.assertEqual(
            ci.compute_modality({"product_type": "Veterinary Drugs"}),
            ci.MODALITY_OTHER,
        )

    def test_veterinary_with_route_still_other(self):
        # product_type 이 수의용이면 route/form 폴백이 타지 않고 Other 고정
        payload = {"openfda": {"product_type": ["VETERINARY DRUGS"], "route": ["ORAL"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_OTHER)

    def test_animal_drug_with_dosage_form_still_other(self):
        payload = {"openfda": {"product_type": ["ANIMAL DRUG"], "dosage_form": ["TABLET"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_OTHER)

    def test_veterinary_vaccine_text_still_other(self):
        # 수의용 product_type 이면 제품명 'vaccine' 생물 단서가 있어도 Other (인체 범위 밖)
        payload = {"product_type": "Veterinary Drugs", "product_description": "animal vaccine"}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_OTHER)

    def test_veterinary_text_only_other(self):
        # 구조화 product_type 없이 텍스트 'animal drug'/'veterinary drug' 만 있어도 Other
        self.assertEqual(
            ci.compute_modality({}, "animal drug oral tablet recall"),
            ci.MODALITY_OTHER,
        )
        self.assertEqual(
            ci.compute_modality({}, "veterinary drug for injection"),
            ci.MODALITY_OTHER,
        )

    def test_animal_derived_human_biologic_not_excluded(self):
        # 인체 바이오의 'animal-derived' 표현은 수의 제외 대상이 아님 → Biologic 유지
        self.assertEqual(
            ci.compute_modality({}, "monoclonal antibody with animal-derived component"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_purified_water_not_tablet(self):
        # '정제수'(purified water) 는 '정제'(tablet) 오탐 금지 → Other
        self.assertEqual(
            ci.compute_modality({}, "정제수 제조설비 점검 지침"),
            ci.MODALITY_OTHER,
        )
        # 진짜 '정제'(tablet) 는 Chemical 유지
        self.assertEqual(
            ci.compute_modality({}, "정제 함량 부적합"),
            ci.MODALITY_CHEMICAL,
        )

    # ── Health Canada 정규화(raw_payload product_type/description) ───────
    def test_hc_drug_recall_chemical(self):
        # collect_hc 가 product_type=Category, product_description=Product 를 넣음
        payload = {"product_type": "Drugs", "product_description": "Some Brand 10 mg"}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_CHEMICAL)

    def test_hc_biologic_recall(self):
        payload = {"product_type": "Drugs", "product_description": "Recombinant vaccine lot"}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_BIOLOGIC)


class TestSterileBioTier3Floor(unittest.TestCase):
    """무균·바이오 치명적 단일 신호는 1개만 있어도 Tier 3 (floor) 여야 한다."""

    def test_sterility_failure_single_is_tier3(self):
        tier = ci.compute_signal_tier(
            ci.SOURCE_EMA, "news", "Pending", "N/A",
            "sterility failure observed in manufacturing line",
        )
        self.assertEqual(tier, "Tier 3")

    def test_viral_contamination_single_is_tier3(self):
        tier = ci.compute_signal_tier(
            ci.SOURCE_EMA, "news", "Pending", "N/A",
            "viral contamination of cell culture",
        )
        self.assertEqual(tier, "Tier 3")

    def test_floor_does_not_override_unrelated(self):
        # 제외 도메인(의료기기·식품 등) = QA Unrelated 이면 floor 로 Tier 3 승격 금지
        tier = ci.compute_signal_tier(
            ci.SOURCE_FDA_WL, "Warning Letter", "Unrelated", "N/A",
            "medical device sterility failure",
        )
        self.assertNotEqual(tier, "Tier 3")

    def test_floor_does_not_override_unrelated_food(self):
        tier = ci.compute_signal_tier(
            ci.SOURCE_FDA_WL, "Warning Letter", "Unrelated", "N/A",
            "food safety sterility failure",
        )
        self.assertNotEqual(tier, "Tier 3")

    def test_unrelated_not_promoted_to_tier2(self):
        # Tier 2 키워드(sterile)가 있어도 Unrelated 면 Tier 1 고정
        tier = ci.compute_signal_tier(
            ci.SOURCE_RECALL, "Class III", "Unrelated", "N/A",
            "medical device sterile package recall",
        )
        self.assertEqual(tier, "Tier 1")

    def test_unrelated_classI_still_tier3(self):
        # 강제 예외(Class I)는 Unrelated 여도 카드화 위해 Tier 3 유지
        tier = ci.compute_signal_tier(
            ci.SOURCE_RECALL, "Class I", "Unrelated", "N/A",
            "some recall",
        )
        self.assertEqual(tier, "Tier 3")


class TestModalityRelevanceNotDropped(unittest.TestCase):
    """무균·바이오 신호가 QA 관련성에서 Unrelated 로 떨어지지 않아야 한다."""

    def test_sterile_not_unrelated(self):
        rel = ci.compute_relevance(
            "Warning letter: sterility failure and aseptic processing deficiency",
        )
        self.assertNotEqual(rel, "Unrelated")
        self.assertIn(rel, ("Likely", "Possible"))

    def test_biosimilar_not_unrelated(self):
        rel = ci.compute_relevance(
            "Biosimilar monoclonal antibody GMP comparability data integrity",
        )
        self.assertNotEqual(rel, "Unrelated")

    def test_injectable_quality_boosts_tier(self):
        tier = ci.compute_signal_tier(
            ci.SOURCE_RECALL, "Class II", "Likely", "N/A",
            "container closure integrity failure in injectable vial",
        )
        self.assertIn(tier, ("Tier 2", "Tier 3"))


class TestKoreanMfdsModality(unittest.TestCase):
    """MFDS 한국어 제품명 제형 분류 (라이브 검증에서 발견한 실데이터 회귀).

    한국 의약품 명명규칙: 정제=XX정, 주사제=XX주, 캡슐=XX캡슐. 본문에 '정제'라는
    단어 없이 제품명 접미사로만 제형이 드러난다. 단, 접미사 매칭은 제품명 필드에만
    적용해 '개정/규정/행정처분' 같은 일반어 오탐을 막아야 한다.
    """

    def test_korean_tablet_suffix_chemical(self):
        for name in ["리치정", "노텍정", "마그스타에프정", "트라마펜세미정",
                     "노바스크정5밀리그램"]:
            self.assertEqual(
                ci.compute_modality({"PRDUCT": name}, f"[회수·판매중지] {name}"),
                ci.MODALITY_CHEMICAL, msg=name)

    def test_korean_injection_suffix_chemical(self):
        for name in ["예나스테론주", "멀티플렉스페리주"]:
            self.assertEqual(
                ci.compute_modality({"PRDUCT": name}, f"[회수·판매중지] {name}"),
                ci.MODALITY_CHEMICAL, msg=name)

    def test_korean_admin_item_name_field(self):
        self.assertEqual(
            ci.compute_modality({"ITEM_NAME": "하이펜에스정"}, "[행정처분] 하이펜에스정"),
            ci.MODALITY_CHEMICAL)

    def test_korean_biologic_ingredient_text_wins(self):
        # 생물 원료가 텍스트에 있으면 주 접미사보다 우선 → Biologic
        self.assertEqual(
            ci.compute_modality({"PRDUCT": "자닥신주"}, "자닥신주 자하거추출물 회수"),
            ci.MODALITY_BIOLOGIC)
        self.assertEqual(
            ci.compute_modality({"PRDUCT": "휴마로그주"}, "인슐린 제제 회수"),
            ci.MODALITY_BIOLOGIC)

    def test_korean_herbal_dental_other(self):
        # 한약·생약·치약은 제형 접미사 없음 → Other (의약품 누수 없어야)
        for name in ["갈근탕", "쌍화탕", "죽염치약"]:
            self.assertEqual(
                ci.compute_modality({"PRDUCT": name}, f"[회수] {name}"),
                ci.MODALITY_OTHER, msg=name)

    def test_suffix_not_applied_to_general_text(self):
        # 제품명 필드가 없는 일반 규제 문서의 '개정/규정/행정처분'은 정제로 오탐 금지 → Other
        for txt in ["OO에 관한 규정 일부개정고시 행정예고",
                    "[행정처분] 업무정지 3개월", "제조방법 변경 결정 공정 개선"]:
            self.assertEqual(ci.compute_modality({}, txt), ci.MODALITY_OTHER, msg=txt)


class TestVetHardExclude(unittest.TestCase):
    """수의/동물용은 boost 키워드가 있어도 hard exclude → Unrelated (구제 없음)."""

    def test_vet_with_two_boosts_still_unrelated(self):
        # 'tablet'+'sterile' 2 boost 가 있어도 'animal drug' 면 Unrelated 고정
        rel = ci.compute_relevance("animal drug oral tablet recall sterile")
        self.assertEqual(rel, "Unrelated")

    def test_vet_then_tier1(self):
        tier = ci.compute_signal_tier(
            ci.SOURCE_RECALL, "Class II",
            ci.compute_relevance("animal drug oral tablet recall sterile"),
            "N/A", "animal drug oral tablet recall sterile",
        )
        self.assertEqual(tier, "Tier 1")

    def test_food_dual_still_rescuable(self):
        # 식품은 hard 가 아니므로 강한 boost 2개면 Possible 로 구제 유지(기존 동작 보존)
        rel = ci.compute_relevance("food safety and cgmp tablet dissolution data integrity")
        self.assertEqual(rel, "Possible")


class TestModalityPreflight(unittest.TestCase):
    """Notion 'Modality' 스키마 preflight — 네트워크 없이 notion_api_request 를 대체."""

    def tearDown(self):
        if hasattr(self, "_orig"):
            ci.notion_api_request = self._orig

    def _patch(self, fake):
        self._orig = ci.notion_api_request
        ci.notion_api_request = fake

    def test_ok_select_with_all_options(self):
        self._patch(lambda *a, **k: {"properties": {"Modality": {
            "type": "select", "select": {"options": [
                {"name": "Chemical"}, {"name": "Biologic"}, {"name": "Other"}]}}}})
        self.assertTrue(ci.notion_verify_modality_property("t", "db"))

    def test_missing_property_returns_false(self):
        self._patch(lambda *a, **k: {"properties": {}})
        self.assertFalse(ci.notion_verify_modality_property("t", "db"))

    def test_wrong_type_returns_false(self):
        self._patch(lambda *a, **k: {"properties": {"Modality": {"type": "rich_text"}}})
        self.assertFalse(ci.notion_verify_modality_property("t", "db"))

    def test_missing_options_still_ok(self):
        # select 옵션은 insert 시 자동 생성되므로 일부 누락은 True(경고만)
        self._patch(lambda *a, **k: {"properties": {"Modality": {
            "type": "select", "select": {"options": [{"name": "Chemical"}]}}}})
        self.assertTrue(ci.notion_verify_modality_property("t", "db"))

    def test_db_query_error_returns_false(self):
        def boom(*a, **k):
            raise ci.NotionHandoffError("boom")
        self._patch(boom)
        self.assertFalse(ci.notion_verify_modality_property("t", "db"))


if __name__ == "__main__":
    unittest.main()
