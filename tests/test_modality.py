"""제형(Modality) 분류 회귀 테스트 (제형 확장).

compute_modality 가 회사 생산 제형(경구 고형제·경구 액상·무균 주사제·
바이오/바이오시밀러)을 의도대로 분류하고, 무균·바이오 신호가 QA 관련성에서
누락(Unrelated)되지 않는지 확인한다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci


class TestComputeModality(unittest.TestCase):
    def test_osd_from_dosage_form(self):
        payload = {"openfda": {"dosage_form": ["TABLET"], "route": ["ORAL"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_OSD)

    def test_capsule_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Extended-release capsule recall"),
            ci.MODALITY_OSD,
        )

    def test_oral_liquid(self):
        payload = {"openfda": {"dosage_form": ["ORAL SOLUTION"], "route": ["ORAL"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_ORAL_LIQUID)

    def test_oral_suspension_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Oral suspension subpotent assay"),
            ci.MODALITY_ORAL_LIQUID,
        )

    def test_sterile_injectable_route(self):
        payload = {"openfda": {"dosage_form": ["INJECTION"], "route": ["INTRAVENOUS"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_STERILE)

    def test_sterile_injectable_text(self):
        self.assertEqual(
            ci.compute_modality({}, "Lack of sterility assurance in vial filling line"),
            ci.MODALITY_STERILE,
        )

    def test_biologic_biosimilar(self):
        self.assertEqual(
            ci.compute_modality({}, "Biosimilar monoclonal antibody comparability"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_biologic_growth_hormone(self):
        # 성장호르몬(somatropin) 은 주사제이지만 바이오로 우선 분류
        payload = {"openfda": {"dosage_form": ["INJECTION"], "route": ["SUBCUTANEOUS"]}}
        self.assertEqual(
            ci.compute_modality(payload, "Somatropin growth hormone for injection"),
            ci.MODALITY_BIOLOGIC,
        )

    def test_biologic_product_type(self):
        payload = {"openfda": {"product_type": ["BIOLOGIC"]}}
        self.assertEqual(ci.compute_modality(payload), ci.MODALITY_BIOLOGIC)

    def test_guidance_unspecified(self):
        self.assertEqual(
            ci.compute_modality({}, "ICH Q9 quality risk management guideline"),
            ci.MODALITY_UNSPECIFIED,
        )

    def test_other_topical(self):
        self.assertEqual(
            ci.compute_modality({}, "Topical cream ointment manufacturing"),
            ci.MODALITY_OTHER,
        )


class TestModalityRelevanceNotDropped(unittest.TestCase):
    """무균·바이오 신호가 QA 관련성에서 Unrelated 로 떨어지지 않아야 한다."""

    def test_sterile_injectable_not_unrelated(self):
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

    def test_injectable_boosts_tier(self):
        tier = ci.compute_signal_tier(
            ci.SOURCE_RECALL, "Class II", "Likely", "N/A",
            "container closure integrity failure in injectable vial",
        )
        self.assertIn(tier, ("Tier 2", "Tier 3"))


if __name__ == "__main__":
    unittest.main()
