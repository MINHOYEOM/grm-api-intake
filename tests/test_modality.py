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


if __name__ == "__main__":
    unittest.main()
