#!/usr/bin/env python3
"""grm-finding-taxonomy/v9 tests -- 503B(a)(10)(B) 용기 표시정보.

v8 이 "별건으로 남긴다"고 적어둔 건이다. 결함의 모양이 특이해서 따로 기록해 둔다:

  같은 483 관찰의 (A)/(B) 짝이 **우연한 어휘 차이로 두 카테고리에 갈려** 있었다.
    (A) "The **labels** of your outsourcing facility's ... 503B(a)(10)(A)"
        -> 문장에 "label" 이 있어 기존 키워드로 표시/포장(17). 실측 121행.
    (B) "The **containers** of your outsourcing facility's ... 503B(a)(10)(B)"
        -> 문장에 "label" 이 한 번도 안 나와 캐치올. 실측 16행.

즉 분류기가 틀린 게 아니라 **조항 어휘가 (A)에만 있었다**. "표시/포장" 필터를 쓰는
사용자는 (B) 건을 영영 못 봤다.

★이 파일이 특히 고정하는 것: **인용 조항에 의존하지 않는다.** 실측 16건의 인용이 전부
OCR 로 깨져 있어("503B(a)(I0)(B)"·"(a)(lO){B}"·"(10)(8)") 인용 기반 규칙이었다면 상당수를
놓쳤을 것이다. 손상된 부분이 아니라 **온전한 부분**에 규칙을 건다.
"""

from __future__ import annotations

import unittest

import grm_findings as gf


# 라이브 실측 finding_text 그대로(2026-08-02). 인용부 손상 양상을 축약하지 않았다.
_LIVE_503B_B = (
    "The containers of your outsourcing facility's drug products do not include "
    "information required by section 503B(a)(10)(B).",
    "The container of your outsourcing facility's drug products does not include "
    "information required b y section 503B(a)(10)(B) of the Federal Food, Drug, "
    "and Cosmetic Act (FD&C Act).",
    # 인용 손상: 1 -> I
    "The container of your outsourcing facility's drug products does not include "
    "information required by section 503B(a)(I0)(B) of the Act.",
    # 인용 손상: 1 -> I, 0 -> O
    "The container of your outsourcing facility's drug products does not include "
    "information required by section 503B(a)(IO)(B) of the Act.",
    # v7 접착("ofyour") + 인용 손상(l/O, 괄호 -> 중괄호)
    "The container ofyour outsourcing facility's drug products do not include "
    "information required by section 503B(a)(lO){B}.",
    # 인용 손상: B -> 8
    "The containers of your outsourcing facility's drug products do not include "
    "information required by section 503B(a)(10)(8).",
    # 공백 삽입된 인용
    "The containers of your outsourcing facility's drug products do not include "
    "information required by section 503B(a) ( I0)(B).",
)


class ContainerClauseRescueTest(unittest.TestCase):
    def test_all_live_variants_reach_labeling_packaging(self) -> None:
        for text in _LIVE_503B_B:
            with self.subTest(text=text[-45:]):
                self.assertEqual(gf.classify_finding_category(text), "labeling_packaging")

    def test_rule_does_not_depend_on_the_citation_at_all(self) -> None:
        """★인용을 통째로 지워도 회수된다 -- 규칙이 손상된 부분에 걸려 있지 않다는 증거."""
        self.assertEqual(
            gf.classify_finding_category(
                "The containers of your outsourcing facility's drug products do not "
                "include the information required."
            ),
            "labeling_packaging",
        )

    def test_sibling_A_clause_is_unchanged(self) -> None:
        """(A)항은 원래부터 여기 있었다 -- v9 가 형제를 합칠 뿐 기존 배치를 흔들지 않는다."""
        self.assertEqual(
            gf.classify_finding_category(
                "The labels of your outsourcing facility's drug products do not include "
                "the information required by section 503B(a)(10)(A)."
            ),
            "labeling_packaging",
        )


class DoesNotStealTest(unittest.TestCase):
    """labeling_packaging 은 17번째 -- 빼앗을 수 있는 것은 18·19번뿐이다."""

    def test_503b_reporting_stays_in_regulatory_reporting(self) -> None:
        """★v8 이 18번째에 세운 503B 보고 규칙을 빼앗지 않는지. 두 규칙이 같은
        "outsourcing facility" 를 공유하므로 이 경계가 이번 변경의 핵심 위험이다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Your outsourcing facility has not submitted a report to FDA identifying "
                "a product compounded during the previous six months as required by "
                "section 503B(b)(2)(A)."
            ),
            "regulatory_reporting",
        )

    def test_non_503b_container_findings_are_not_pulled_in(self) -> None:
        """★캐치올에는 용기 관련이지만 표시/포장이 아닌 지적이 28건 있다(211.94 계열
        container closure integrity 등). 조항 문맥을 함께 요구해 이들을 배제한다.
        첫 문자열은 라이브 실측 원문 그대로다(OCR 로 "validation" 이 깨져 있다)."""
        for text in (
            "Capping {tiff-O alidation data to ensure container closure integrity is inadequate.",
            "Data to ensure container closure integrity is inadequate.",
            "The containers used for drug products are not cleaned before use.",
        ):
            with self.subTest(text=text[:45]):
                self.assertEqual(
                    gf.classify_finding_category(text), "other_quality_system"
                )

    def test_bare_container_mention_does_not_reach_labeling(self) -> None:
        """규칙은 "container + outsourcing facility + 조항 문맥" 3요소를 전부 요구한다."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "The containers of your outsourcing facility were found to be visibly dirty."
            ),
            "labeling_packaging",
        )


class DesignContractTest(unittest.TestCase):
    def test_rule_lives_in_patterns_not_keywords(self) -> None:
        """v8 §6 규율 유지 -- keywords 는 `_split_repair_vocabulary()` 의 파생원이라
        여기 넣으면 v6/v7 복원 어휘가 조용히 넓어진다."""
        by_code = {c.code: c for c in gf.FINDING_TAXONOMY}
        self.assertEqual(
            by_code["labeling_packaging"].keywords,
            ("labeling", "packaging", "label", "표시", "포장", "라벨"),
        )
        self.assertEqual(len(by_code["labeling_packaging"].patterns), 1)
        vocab = set(gf._split_repair_vocabulary())
        self.assertNotIn("container", vocab)
        self.assertNotIn("containers", vocab)

    def test_v9_rules_survive_and_v1_through_v9_still_valid(self) -> None:
        """v9 는 더 이상 현행이 아니다(v10 이 현행) -- 이 파일이 지키는 것은
        "v9 가 세운 규칙이 살아 있는가"이지 "v9 가 최신인가"가 아니다.

        v3 파일이 이미 세운 관례다: 살아 있는 classify_finding_category() 는 하나뿐이라
        버전 단언은 현행을 따라가고, 각 파일은 자기 버전이 도입한 **행동**을 고정한다.
        (현행 버전 자체의 단언은 tests/test_findings_taxonomy_v10.py 가 갖는다.)"""
        for n in range(1, 10):
            self.assertIn(f"grm-finding-taxonomy/v{n}", gf.TAXONOMY_VERSIONS)
        self.assertEqual(gf.TAXONOMY_VERSION, gf.TAXONOMY_VERSIONS[-1])
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)

    def test_v9_introduces_no_new_category_and_no_reorder(self) -> None:
        codes = [c.code for c in gf.FINDING_TAXONOMY]
        self.assertEqual(codes.index("labeling_packaging"), 16)
        self.assertEqual(codes.index("regulatory_reporting"), 17)
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


class PriorRevisionRegressionTest(unittest.TestCase):
    def test_v5_through_v8_all_still_hold(self) -> None:
        for name, text, expected in (
            ("v5 역극성",
             "Procedures designed to prevent objectionable microorganisms in drug products "
             "not required to be sterile are not established.", "contamination_control"),
            ("v6 단어분리",
             "Each batch of drug product required to be free of objectionable microorganisms "
             "is not tested through appropriate laborator y testing.", "qc_lab_controls"),
            ("v7 접착",
             "The design and materials ofequipment and utensils does not allow proper cleaning.",
             "equipment_facility"),
            ("v8 시간한도",
             "Appropriate time limits for the completion of each phase of production were "
             "not established.", "process_validation"),
            ("v8 field alert 보호",
             "Your firm failed to submit a field alert report to the agency within three "
             "working days.", "complaint_recall"),
        ):
            with self.subTest(name=name):
                self.assertEqual(gf.classify_finding_category(text), expected)


if __name__ == "__main__":
    unittest.main()
