#!/usr/bin/env python3
"""grm-finding-taxonomy/v7 tests -- 접착(adhesion) 손상, v6 의 거울상.

배경: v6 는 텍스트층이 **끼워 넣은** 공백("laborator y")을 지웠다. 같은 층이 반대
손상도 만든다 -- 공백이 **탈락**해 앞 단어가 신호어에 들러붙는다("rejection
ofcomponents"). 이때도 `\\b` 단어경계 키워드가 조용히 빗나간다.

★v6 과 결정적으로 다른 성질: 이 손상은 **캐치올로 떨어지지 않는다.** 라이브 109행 중
캐치올은 34행뿐이고 나머지는 엉뚱한 특정 카테고리에 앉아 있다 -- 신호어가 가려지면
매치 순서상 다른 키워드가 대신 이기기 때문이다. "미분류 건수"·"캐치올 비율" 지표로는
원리적으로 안 보인다. 그래서 아래 테스트의 중심은 캐치올 탈출이 아니라
**정본 카테고리로의 재귀속**이다.
"""

from __future__ import annotations

import unittest

import grm_findings as gf


class AdhesionRepairMechanismTest(unittest.TestCase):
    """라이브 실측 3개 조항 -- 전부 캐치올이 아니라 **엉뚱한 카테고리**에 있던 행이다."""

    def test_211_192_ofinvestigations_goes_to_deviation_capa(self) -> None:
        """실측 5건이 material_supplier_control 에 있었다("components" 가 대신 매치)."""
        self.assertEqual(
            gf.classify_finding_category(
                "Written records are not made ofinvestigations into unexplained "
                "discrepancies and the failure ofa batch or any of its components "
                "to meet specifications."
            ),
            "deviation_capa",
        )

    def test_211_80_ofcomponents_goes_to_material_supplier_control(self) -> None:
        """실측 5건이 stability_storage 에 있었다("storage" 가 대신 매치)."""
        self.assertEqual(
            gf.classify_finding_category(
                "Written procedures are lacking which describe in sufficient detail the "
                "receipt, identification, storage, handling, sampling, testing, approval, "
                "and rejection ofcomponents."
            ),
            "material_supplier_control",
        )

    def test_211_65_ofequipment_goes_to_equipment_facility(self) -> None:
        """실측 3건이 material_supplier_control 에 있었다."""
        self.assertEqual(
            gf.classify_finding_category(
                "The design and materials ofequipment and utensils does not allow proper cleaning."
            ),
            "equipment_facility",
        )

    def test_catchall_escape_cases(self) -> None:
        for text, expected in (
            ("The cleaning ofequipment is inadequate.", "equipment_facility"),
            (
                "Clothing ofpersonnel engaged in the manufacturing and processing of "
                "drug products is not appropriate.",
                "training_personnel",
            ),
        ):
            with self.subTest(text=text[:45]):
                self.assertEqual(gf.classify_finding_category(text), expected)

    def test_repair_only_ungues_of_and_leaves_prose_untouched(self) -> None:
        self.assertEqual(
            gf._repair_adhesion("rejection ofcomponents and ofequipment often at the office"),
            "rejection of components and of equipment often at the office",
        )

    def test_repair_is_a_noop_on_clean_text(self) -> None:
        clean = "the design and materials of equipment and utensils does not allow cleaning."
        self.assertEqual(gf._repair_adhesion(clean), clean)


class AdhesionFalsePositiveGuardTest(unittest.TestCase):
    """★접두어를 `of` 하나로 못박은 근거를 테스트로 잠근다."""

    def test_other_prefixes_are_not_repaired_because_they_form_real_words(self) -> None:
        """실측: 후보 15종 중 of 109 · in 4 · and 1 이었고 in 의 4건은 손상이 아니라
        실제 영어 단어였다 -- "in"+"stability"=instability, "in"+"validation"=
        invalidation. 접두어를 일반화하면 즉시 오탐이 된다."""
        for text in (
            "There is instability in the product over time.",
            "The invalidation of out-of-specification results was not justified.",
        ):
            with self.subTest(text=text[:40]):
                # 복원기가 손대지 않아야 한다(문자열 불변).
                self.assertEqual(gf._repair_adhesion(text.lower()), text.lower())

    def test_real_english_of_words_are_not_split(self) -> None:
        """often/office/officer/official/offer/offset/offshore 는 뒤 조각이 신호어가
        아니므로 원리적으로 안 걸린다 -- 그래도 침묵 표류 방지로 고정한다."""
        text = (
            "records are often kept in the office of the officer who made an offer "
            "to offset offshore official documentation"
        )
        self.assertEqual(gf._repair_adhesion(text), text)

    def test_prefix_is_pinned_to_of_only(self) -> None:
        self.assertEqual(gf._ADHESION_PREFIX, "of")

    def test_adhesion_vocabulary_is_derived_not_hardcoded(self) -> None:
        """v6 §4 의 표류 방지 원칙 -- 어휘는 FINDING_TAXONOMY 에서 파생되므로 신호어를
        추가하면 분리 복원과 접착 복원이 **함께** 따라온다."""
        vocab = set(gf._split_repair_vocabulary())
        self.assertIn("components", vocab)
        self.assertIn("equipment", vocab)
        self.assertIn("investigation", vocab)
        # 파생 어휘는 전부 len>=6 -- `of`+어휘 가 실제 영어 단어가 될 수 없는 근거다.
        self.assertTrue(all(len(word) >= gf._SPLIT_REPAIR_MIN_LEN for word in vocab))

    def test_zero_count_guard_pattern_is_not_empty(self) -> None:
        """★"회귀 0은 측정이 아니라 구조로" -- 정규식 생성이 깨져 아무것도 안 잡히면
        테스트가 조용히 통과한다. 앵커로 그 침묵을 막는다."""
        self.assertTrue(gf._ADHESION_RE.pattern)
        self.assertIsNotNone(gf._ADHESION_RE.search("rejection ofcomponents"))


class PriorRevisionRegressionTest(unittest.TestCase):
    """v5·v6 이 고친 것이 v7 로 되돌아가지 않는지."""

    def test_v6_split_word_repair_still_works(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Each batch of drug product required to be free of objectionable "
                "microorganisms is not tested through appropriate laborator y testing."
            ),
            "qc_lab_controls",
        )

    def test_v5_reverse_polarity_still_works(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Procedures designed to prevent objectionable microorganisms in drug "
                "products not required to be sterile are not established."
            ),
            "contamination_control",
        )

    def test_v5_guard_survives_adhesion_inside_the_negation_phrase(self) -> None:
        """복원이 중립화보다 **먼저** 도는 순서가 실제로 하중을 받는지 -- 부정 구절
        안에서 접착이 일어나도 v5 가드가 여전히 발동해야 한다."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "Procedures designed to prevent objectionable microorganisms in drug "
                "products not required to be sterile are not established, and records "
                "ofinvestigations are absent."
            ),
            "aseptic_sterility_assurance",
        )

    def test_211_113b_purporting_still_aseptic(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Procedures designed to prevent microbiological contamination of drug "
                "products purporting to be sterile are not established and followed."
            ),
            "aseptic_sterility_assurance",
        )


class TaxonomyV7BoundedTest(unittest.TestCase):
    def test_taxonomy_v7_is_current_and_v1_through_v6_still_valid(self) -> None:
        self.assertEqual(gf.TAXONOMY_VERSION, "grm-finding-taxonomy/v7")
        self.assertEqual(
            gf.TAXONOMY_VERSIONS,
            (
                "grm-finding-taxonomy/v1",
                "grm-finding-taxonomy/v2",
                "grm-finding-taxonomy/v3",
                "grm-finding-taxonomy/v4",
                "grm-finding-taxonomy/v5",
                "grm-finding-taxonomy/v6",
                "grm-finding-taxonomy/v7",
            ),
        )
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)

    def test_v7_introduces_no_new_category_and_no_reorder(self) -> None:
        codes = [c.code for c in gf.FINDING_TAXONOMY]
        self.assertEqual(
            codes,
            [
                "data_integrity", "computer_system_validation", "documentation_records",
                "aseptic_sterility_assurance", "environmental_monitoring", "cleaning_validation",
                "complaint_recall", "deviation_capa", "quality_unit_oversight", "qc_lab_controls",
                "process_validation", "equipment_facility", "material_supplier_control",
                "contamination_control", "validation_qualification", "stability_storage",
                "labeling_packaging", "regulatory_reporting", "training_personnel",
                "other_quality_system",
            ],
        )

    def test_empty_text_still_returns_catch_all(self) -> None:
        self.assertEqual(gf.classify_finding_category(""), "other_quality_system")


if __name__ == "__main__":
    unittest.main()
