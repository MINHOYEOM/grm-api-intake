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
import grm_notion  # 배치5 Phase1: notion_api_request 정의 모듈(preflight 대체 대상)


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

    def test_biologic_ich_q5_biotechnological(self):
        # ICH Q5A-E "Quality of Biotechnological Products" 가 Biologic 으로 잡혀야 함
        # (라이브 검증 발견 GAP-1: 'biological product' 만으로는 'biotechnological' 미매칭)
        self.assertEqual(
            ci.compute_modality({}, "Quality of Biotechnological Products (ICH Q5)"),
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

    def test_hc_immune_globulin_space_form_biologic(self):
        # 'immune globulin'(공백형) 도 immunoglobulin 과 동일하게 Biologic (용어 보강)
        payload = {"product_type": "Drugs",
                   "product_description": "Octagam (Immune globulin intravenous, Human)"}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_BIOLOGIC)

    def test_hc_brand_only_drugs_with_generic_in_text_biologic(self):
        # Category=Drugs 단독이면 Chemical 이지만, 텍스트(상세 유효성분)에 생물 원료가
        # 주입되면 Biologic 이 우선한다(Hizentra 류: collect_hc 가 body 에 유효성분 주입).
        payload = {"product_type": "Drugs", "product_description": "Hizentra"}
        self.assertEqual(
            ci.compute_modality(payload, "유효성분/함량: IMMUNOGLOBULIN (HUMAN) 200 mg/mL"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_hc_brand_only_hizentra_now_biologic_via_curated_dict(self):
        # GAP-2 supersedes the old graceful 반례: 상세 fetch 실패로 유효성분 단서가 없어도
        # 큐레이티드 브랜드 사전(MODALITY_BIOLOGIC_BRANDS)이 'Hizentra'를 Biologic 으로 잡는다.
        # (사전 주석: "HC P7 상세 fetch 누락 시 백업" — 바로 이 시나리오를 의도적으로 교정)
        payload = {"product_type": "Drugs", "product_description": "Hizentra"}
        self.assertEqual(ci.compute_modality(payload, "Hizentra"), ci.MODALITY_BIOLOGIC)


class TestGap2BrandOnlyBiologic(unittest.TestCase):
    """GAP-2: 브랜드명만 있고 원료/클래스 텍스트가 없는 생물의약품을 큐레이티드
    사전(MODALITY_BIOLOGIC_BRANDS)으로 Biologic 교정. 제형 접미사(2순위 d)가 덮어쓰기 전에
    가로채야 한다. 자닥신주=thymosin alpha-1, Hizentra=면역글로불린.
    """

    def test_gap2_1_자닥신주_brand_only_biologic(self):
        # PRDUCT='자닥신주', 본문에 생물 클래스 단서 없음 — 종전엔 '주' 접미사로 Chemical.
        self.assertEqual(
            ci.compute_modality({"PRDUCT": "자닥신주"}, "[회수·판매중지] 자닥신주"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_gap2_2_자닥신주_with_strength_variant_biologic(self):
        # 함량 포함 변이(ITEM_NAME) 도 어간 '자닥신' 으로 매칭
        self.assertEqual(
            ci.compute_modality({"ITEM_NAME": "자닥신주 0.8mg"}, "[행정처분] 자닥신주 0.8mg"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_gap2_3_hizentra_english_brand_only_biologic(self):
        # 영문 백업: 유효성분 텍스트 없이 brand-only 'Hizentra' → Biologic
        self.assertEqual(ci.compute_modality({}, "Hizentra"), ci.MODALITY_BIOLOGIC)

    def test_gap2_4_generic_chemical_forms_unbroken(self):
        # 오탐 가드: 브랜드 어간을 포함하지 않는 일반 화학 정/주는 여전히 Chemical
        self.assertEqual(
            ci.compute_modality({"PRDUCT": "세파클러정"}, "[회수] 세파클러정"),
            ci.MODALITY_CHEMICAL,
        )
        self.assertEqual(
            ci.compute_modality({"PRDUCT": "오메프라졸주"}, "[회수] 오메프라졸주"),
            ci.MODALITY_CHEMICAL,
        )

    def test_gap2_5_general_words_no_false_biologic(self):
        # 일반어는 브랜드 부분문자열 오매칭 없이 여전히 비-Biologic(Other)
        for txt in ["개정안", "행정처분", "규정 일부개정고시"]:
            self.assertEqual(ci.compute_modality({}, txt), ci.MODALITY_OTHER, msg=txt)


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
            grm_notion.notion_api_request = self._orig

    def _patch(self, fake):
        # notion_verify_modality_property 는 grm_notion(배치5 Phase1) 에 있으므로
        # 그 정의 모듈의 notion_api_request 를 대체해야 preflight 호출이 fake 를 본다.
        self._orig = grm_notion.notion_api_request
        grm_notion.notion_api_request = fake

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


class TestTier1BlindSpotRecovery(unittest.TestCase):
    """[2026-08-03] Tier 1 사각지대 회수 — 실측 450건에서 확정된 미스 유형을 고정한다.

    배경: Tier 1 은 "중요하지 않다"가 아니라 **판단 근거를 못 찾았다는 기본값**이라,
    QA 가 봐야 할 항목이 조용히 떨어졌다. 확정 미스 26제목은 `QA_CATEGORY_KEYWORDS` 에도
    전부 안 걸려(qa_relevance 전량 "Pending") — 두 목록이 **같이 비어 있던 영역**이었다.
    """

    def _tier(self, text, source=None, toc="news", qa="Pending"):
        return ci.compute_signal_tier(source or ci.SOURCE_ECA, toc, qa, "N/A", text)

    # ── ① warning letter 단독 신호 ────────────────────────────────────────────
    def test_warning_letter_alone_now_tier2(self):
        # 종전: `warning letter` 는 TIER3(2개 매칭 요구)·BOOST 에만 있어 **구조적으로 Tier 1**.
        self.assertEqual(
            self._tier("Quality Unit (QU) in the Focus of a Warning Letter"), "Tier 2")

    def test_warning_letter_plural_matches(self):
        # `_kw_match` 는 `\bwarning letter\b` 라 복수형을 **못 잡았다**(실측 0).
        self.assertEqual(
            self._tier("Several FDA Warning Letters and Untitled Letters on Talc"), "Tier 2")

    def test_warning_letter_does_not_reach_tier3_alone(self):
        # TIER2 로만 넣었으므로 단독으로 Tier 3 까지 올라가면 안 된다(티어 인플레이션 방지).
        self.assertNotEqual(
            self._tier("Quality Unit in the Focus of a Warning Letter"), "Tier 3")

    # ── ② 번호 표제 정규식(Annex N · ICH QN) ──────────────────────────────────
    def test_numbered_annex_matches(self):
        # `\bannex 1\b` 는 "Annex 15"·"Annex 19" 를 못 잡는다(뒤가 \w).
        for t in ("Corrigendum: Concept Paper on the Annex 15 Revision",
                   "Revised Annex 19 on Reference and Retention Samples"):
            self.assertEqual(self._tier(t), "Tier 2", msg=t)

    def test_numbered_ich_q_matches_slash_separated(self):
        # `ich q10` 은 "ICH Q8/Q9/Q10"(구분자 `/`)을 못 잡는다.
        self.assertEqual(
            self._tier("FDA adopts updated ICH Q8/Q9/Q10 Questions & Answers (R5)"), "Tier 2")

    def test_annex_pattern_restricted_to_pharma_sources(self):
        # ⚠️ ICAO **Annex 15** 는 항공정보업무다 — 범용 검색·연방관보에는 적용하지 않는다.
        self.assertEqual(
            self._tier("ICAO Annex 15 aeronautical information services",
                        source=ci.SOURCE_FR), "Tier 1")

    # ── ③ 어느 목록에도 없던 어휘 ─────────────────────────────────────────────
    def test_recovered_vocabulary(self):
        cases = [
            "Audit Trail: No Option to Add Comments",
            "Q&As on Automated Visual Inspection (AVI)",
            "Ph. Eur. Glass Containers",
            "EDQM publishes revised CEP Guideline",
            "New Edition of ISO 14644-15 published",
            "How to (not) use AI for GxP Inspection Responses",
            "Warning Letter regarding Deficiencies in Contamination Control",
            "FDA Warning Letter: Missing Method Validation",
        ]
        for t in cases:
            self.assertEqual(self._tier(t), "Tier 2", msg=t)

    def test_mhra_defect_notification_recovered(self):
        # `recall` 로는 안 잡힌다 — MHRA 는 "Medicines Defect Notification" 으로 쓴다.
        self.assertEqual(
            self._tier("Class 4 Medicines Defect Notification: Xaggitin XL Tablets",
                        source=ci.SOURCE_MHRA), "Tier 2")

    def test_fda_wl_refusal_to_provide_access(self):
        # 실사 거부는 최고 신호인데 어느 목록에도 없었다.
        self.assertEqual(
            self._tier("Refusal to Provide Access to and Copying of Records",
                        source=ci.SOURCE_FDA_WL, toc="CDER"), "Tier 2")

    # ── ④ 회귀 가드 — 제외 도메인은 여전히 Tier 1 ────────────────────────────
    def test_excluded_domain_still_tier1(self):
        # Unrelated 하드리턴이 신규 어휘보다 **먼저** 걸려야 한다.
        for t in ("medical device audit trail deficiency",
                   "cosmetic warning letters issued",
                   "food safety visual inspection program"):
            self.assertEqual(
                self._tier(t, source=ci.SOURCE_FDA_WL, qa="Unrelated"), "Tier 1", msg=t)

    def test_unrelated_headlines_stay_tier1(self):
        # 이번 실측에서 correct(Tier 1) 로 확정된 유형은 승격되면 안 된다.
        for t in ("New leadership team appointments",
                   "Uzbekistan applies for PIC/S membership",
                   "M8 Electronic Common Technical Document (eCTD)",
                   "False & Misleading Claims/Misbranded (Telehealth)"):
            self.assertEqual(self._tier(t), "Tier 1", msg=t)
