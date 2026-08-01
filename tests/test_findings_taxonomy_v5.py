#!/usr/bin/env python3
"""grm-finding-taxonomy/v5 tests -- 역극성(reverse-polarity) 신호어 결함.

배경(사용자 제보, 2026-08-01): `/findings/` 화면에서 "무균공정과 오히려 관련이 없어
보이는" 지적이 무균보증/무균공정 카테고리에 묶여 있다는 보고. 재현 결과 원인은
21 CFR 211.113(a) 문형이었다 --

    "Procedures designed to prevent objectionable microorganisms in drug products
     **not required to be sterile** are not established, written and followed."

이 조항은 **무균이 요구되지 않는**(=비무균) 제품의 미생물 관리 조항인데,
aseptic_sterility_assurance 의 `\\bsteril...` 패턴이 문장 속 "sterile" 한 단어만 보고
무균으로 끌어갔다. v4 의 방어는 `(?<!non-)(?<!non )` lookbehind 뿐이라 "non-sterile" 만
막고 이 문형은 통과시킨다. 라이브 실측: 이 문형 25건이 **전량**(25/25) 무균 버킷.

v5 는 lookbehind 를 늘리지 않고 매칭 **전에** 역극성 스팬을 중립화한다
(`_NEGATED_STERILE_RE`). 아래 테스트는 세 가지를 각각 고정한다:

1. 메커니즘: 역극성 문형이 무균에서 빠지고 정본 카테고리(contamination_control)로
   가며, 진짜 무균 신호는 그대로 남는다.
2. 스팬 단위 보존: 같은 지적문에 독립적인 무균 신호가 있으면 중립화가 문서 전체를
   강등시키지 않는다(라이브 WL 산문 2건의 성질).
3. 계약: 카테고리 20개·순서·코드 불변, 버전 IN-list 확장.
"""

from __future__ import annotations

import unittest

import grm_findings as gf


# 라이브 실측 문형(2026-07-28 발행분, firm=Gascó Industrial Corporation / Veganic SKN
# Limited 외). 저장된 finding_text 그대로.
_211_113A_VARIANTS = (
    "Procedures designed to prevent objectionable microorganisms in drug products "
    "not required to be sterile are not established, written and followed.",
    "Procedures designed to prevent objectionable microorganisms in drug products "
    "not required to be sterile are not established and followed.",
    "Procedures designed to prevent objectionable microorganisms in drug products "
    "not required to be sterile are not followed.",
    "Appropriate written procedures, designed to prevent objectionable microorganisms "
    "in drug products not required to be sterile, are not established and followed.",
)


class ReversePolarityMechanismTest(unittest.TestCase):
    """v5 §1: "sterile" 을 부정하는 문형이 무균 신호로 읽히지 않는다."""

    def test_211_113a_variants_leave_the_aseptic_bucket(self) -> None:
        for text in _211_113A_VARIANTS:
            with self.subTest(text=text[:60]):
                self.assertNotEqual(
                    gf.classify_finding_category(text), "aseptic_sterility_assurance"
                )

    def test_211_113a_variants_land_in_contamination_control(self) -> None:
        """중립화만으로는 other_quality_system 으로 떨어진다(실측 확인) -- v5 §2 의
        `objectionable micro` 패턴이 정본 카테고리로 보내는지까지 고정한다."""
        for text in _211_113A_VARIANTS:
            with self.subTest(text=text[:60]):
                self.assertEqual(
                    gf.classify_finding_category(text), "contamination_control"
                )

    def test_ocr_whitespace_and_linebreak_variants_are_still_neutralised(self) -> None:
        """고정폭 lookbehind 대신 스팬 치환을 택한 이유 -- OCR 이 만드는 가변 공백/
        줄바꿈을 따라가야 한다."""
        text = (
            "Procedures designed to prevent objectionable microorganisms in drug  products "
            "not   required\nto be  sterile are not established."
        )
        self.assertEqual(gf.classify_finding_category(text), "contamination_control")

    def test_required_suffix_ocr_tolerance_stays_stem_bounded(self) -> None:
        """`requi\\w*` 는 어간 고정 + 접미부만 개방(v4 보수적 OCR 내성 원칙). 어간이
        깨진 형태까지 잡으려 들지 않는다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Procedures to prevent objectionable microorganisms in drug products "
                "not requiired to be sterile are not followed."
            ),
            "contamination_control",
        )


class AsepticSignalPreservedTest(unittest.TestCase):
    """v5 §1 의 스팬 단위 보존 성질 + v4 이전 동작 안티리그레션."""

    def test_211_113b_purporting_to_be_sterile_stays_aseptic(self) -> None:
        """(b)항은 **무균 표방** 제품 -- 극성이 반대라 그대로 무균이어야 한다.
        v5 가 (a)와 (b)를 가르는지가 이 수정의 핵심이다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Procedures designed to prevent microbiological contamination of drug "
                "products purporting to be sterile are not established and followed."
            ),
            "aseptic_sterility_assurance",
        )

    def test_independent_aseptic_signal_survives_neutralisation(self) -> None:
        """중립화는 스팬만 지운다 -- 같은 문장에 진짜 무균 신호가 따로 있으면 무균에
        남는다(라이브 WL 산문 2건, endotoxin/aseptic rooms 의 성질)."""
        self.assertEqual(
            gf.classify_finding_category(
                "Media fill runs were not performed for drug products not required to be "
                "sterile nor for the aseptic filling line."
            ),
            "aseptic_sterility_assurance",
        )

    def test_objectionable_microorganisms_with_aseptic_prose_stays_aseptic(self) -> None:
        """라이브 실측 케이스(finding-d66617501618f5d509304431 성질): 'objectionable
        microorganisms' 가 있어도 aseptic 신호가 독립적으로 있으면 무균이 맞다 --
        v5 §2 패턴이 진짜 무균 산문을 강등시키지 않는지 고정."""
        self.assertEqual(
            gf.classify_finding_category(
                "Inadequate decontamination to ensure it can safely achieve a state of "
                "control in aseptic rooms against objectionable microorganisms."
            ),
            "aseptic_sterility_assurance",
        )

    def test_plain_sterile_and_aseptic_spellings_unchanged(self) -> None:
        for text, expected in (
            ("The product must remain sterile at all times.", "aseptic_sterility_assurance"),
            ("Aseptic processing area smoke studies were not performed.", "aseptic_sterility_assurance"),
            ("Biological indicators were not used to verify the adequacy of the sterilization cycle.", "aseptic_sterility_assurance"),
            ("Use of non-sterile gloves was observed.", "other_quality_system"),
        ):
            with self.subTest(text=text[:50]):
                self.assertEqual(gf.classify_finding_category(text), expected)

    def test_known_limitation_ocr_dropped_not_stays_aseptic(self) -> None:
        """v5 §3 알려진 한계: 라이브 1건은 추출 과정에서 "not" 이 탈락해 저장된 텍스트가
        literally "required to be sterile" 이다. 저장된 값만 보면 극성이 뒤집혀 있어
        중립화가 발동하지 않는다. "objectionable micro" 를 무균 **차단** 신호로 올리면
        이 1건은 고쳐지지만 진짜 무균 산문 2건이 함께 강등되므로 채택하지 않았다 --
        honesty over forcing a match(v4 관례). 실제 출력을 고정해 조용한 표류를 막는다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Procedures designed to prevent objectionable microorganisms in drug "
                "products required to be sterile are not established, written and followed."
            ),
            "aseptic_sterility_assurance",
        )


class ContaminationControlPatternTest(unittest.TestCase):
    """v5 §2: contamination_control 의 새 패턴이 정확히 필요한 만큼만 넓다."""

    def test_existing_contamination_keywords_unchanged(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "The separate or defined areas and control systems necessary to prevent "
                "contamination or mix-ups are deficient."
            ),
            "contamination_control",
        )

    def test_objectionable_alone_without_microorganism_does_not_match(self) -> None:
        """패턴은 `objectionable` 다음에 `micro-` 어간을 요구한다 -- 형용사 단독으로는
        오염 카테고리를 끌어오지 않는다."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "The auditor found the response objectionable and incomplete."
            ),
            "contamination_control",
        )

    def test_211_165b_free_of_objectionable_is_not_pulled_into_contamination(self) -> None:
        """★패턴이 `prevent` 를 함께 요구하는 이유. 21 CFR 211.165(b)("each batch ...
        required to be **free of** objectionable microorganisms is not tested through
        appropriate **laboratory testing**")는 같은 어휘를 쓰지만 시험실 시험 실패이지
        오염 예방 실패가 아니다. 라이브 실측 6건 -- 넓은 패턴이면 이들이 오염관리로
        잘못 끌려온다. 조항의 **행위**(예방 vs 시험)로 가른다."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "Each batch of drug product required to be free of objectionable "
                "microorganisms is not tested through appropriate laboratory testing."
            ),
            "contamination_control",
        )

    def test_microorganism_plural_and_singular_both_match(self) -> None:
        for text in (
            "Procedures to prevent objectionable microorganism ingress were not followed.",
            "Written procedures designed to prevent objectionable microorganisms are absent.",
        ):
            with self.subTest(text=text[:45]):
                self.assertEqual(
                    gf.classify_finding_category(text), "contamination_control"
                )

    def test_confirmed_ocr_damage_in_the_objectionable_phrase_still_matches(self) -> None:
        """v4 §1 과 같은 규율 -- 실측에서 **확인된** 손상만 좁게 수용한다.
        ①`obj ectionable`(PDF 텍스트층 단어 중간 공백, 라이브 1건)
        ②`Inicroorganisms`(선두 'm' 이 'In' 으로 오인식, 라이브 1건).
        이 두 건은 중립화 후 갈 곳이 없어 기타 품질시스템으로 떨어지던 행이다."""
        for text in (
            "Procedures designed to prevent obj ectionable microorganisms in drug pro ducts "
            "not required to be sterile are not established.",
            "Procedures designed to prevent objectionable Inicroorganisms in drug products "
            "not required to be sterile are not established and followed.",
        ):
            with self.subTest(text=text[:50]):
                self.assertEqual(
                    gf.classify_finding_category(text), "contamination_control"
                )

    def test_ocr_tolerance_does_not_become_fuzzy_matching(self) -> None:
        """수용한 손상은 좁게 열거된 것뿐이다 -- 임의의 유사 철자까지 열어주지 않는다.

        ★2026-08-02 v6 갱신: 원래 이 테스트의 반례는 "objection able microrganisms" 였다.
        v6(단어 중간 공백 복원)이 "objection able" 을 **정당하게** 되붙이면서(공백 1칸 1회
        = v6 이 정확히 다루는 손상 모형) 이 문자열은 이제 contamination_control 로 간다.
        테스트의 **의도**(퍼지매칭 금지)는 그대로이므로, v6 의 복원 모형으로도 도달할 수
        없는 손상 -- 문자 전치·이중 분할·조인해도 신호어가 되지 않는 분할 -- 로 반례를
        교체한다. v6 은 퍼지매칭이 아니라 '공백 1칸 되붙이기'라는 것이 여기서 드러난다."""
        for text in (
            # 문자 전치(objectionabel) -- 어떤 공백 복원으로도 도달 불가.
            "Procedures designed to prevent objectionabel microrganisms were absent.",
            # 이중 분할(object ion able) -- v6 은 1회 분할만 되붙인다.
            "Procedures designed to prevent object ion able microrganisms were absent.",
            # 조인 결과가 신호어가 아님(micr organisms -> microrganisms).
            "Procedures designed to prevent objectionable micr organisms were absent.",
        ):
            with self.subTest(text=text[:55]):
                self.assertNotEqual(
                    gf.classify_finding_category(text), "contamination_control"
                )


class TaxonomyV5BoundedTest(unittest.TestCase):
    """v5 계약: 카테고리 집합·순서 불변, 버전 IN-list 만 확장."""

    def test_taxonomy_v5_is_still_an_accepted_taxonomy_version(self) -> None:
        """v6(2026-08-02 split-word 수정)으로 현재 버전이 올라갔다 -- 이 클래스가 지키는
        것은 "v5 가 현재 버전"이 아니라 **v5 로 저장된 기존 행이 계속 유효하다**는 계약이다
        (v2->v3->v4->v5 때와 동일한 additive 규율). 현재 버전 고정은
        tests/test_findings_taxonomy_v6.py 로 이동."""
        self.assertIn("grm-finding-taxonomy/v5", gf.TAXONOMY_VERSIONS)
        self.assertEqual(
            gf.TAXONOMY_VERSIONS[:5],
            (
                "grm-finding-taxonomy/v1",
                "grm-finding-taxonomy/v2",
                "grm-finding-taxonomy/v3",
                "grm-finding-taxonomy/v4",
                "grm-finding-taxonomy/v5",
            ),
        )
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)
        self.assertEqual(len(gf.FINDING_CATEGORY_CODES), len(set(gf.FINDING_CATEGORY_CODES)))

    def test_v5_introduces_no_new_category_and_no_reorder(self) -> None:
        codes = [c.code for c in gf.FINDING_TAXONOMY]
        v3_order = [
            "data_integrity", "computer_system_validation", "documentation_records",
            "aseptic_sterility_assurance", "environmental_monitoring", "cleaning_validation",
            "complaint_recall", "deviation_capa", "quality_unit_oversight", "qc_lab_controls",
            "process_validation", "equipment_facility", "material_supplier_control",
            "contamination_control", "validation_qualification", "stability_storage",
            "labeling_packaging", "regulatory_reporting", "training_personnel",
            "other_quality_system",
        ]
        self.assertEqual(codes, v3_order)

    def test_neutralisation_is_confined_to_the_sterile_polarity_span(self) -> None:
        """중립화가 다른 카테고리 신호를 삼키지 않는지 -- 같은 문장에 있는 무관한
        신호어(calibration)는 그대로 작동해야 한다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Calibration of the balance used for drug products not required to be "
                "sterile was not performed on schedule."
            ),
            "equipment_facility",
        )

    def test_empty_text_still_returns_catch_all(self) -> None:
        self.assertEqual(gf.classify_finding_category(""), "other_quality_system")
        self.assertEqual(gf.classify_finding_category("   "), "other_quality_system")


if __name__ == "__main__":
    unittest.main()
