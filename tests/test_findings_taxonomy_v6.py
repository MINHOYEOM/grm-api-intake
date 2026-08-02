#!/usr/bin/env python3
"""grm-finding-taxonomy/v6 tests -- 단어 중간 공백(split-word) 결함.

배경(2026-08-02, v5 역극성 수정 작업 중 발견): 스캔 PDF(FDA 483/WL)의 텍스트층은 단어
중간에 공백 한 칸을 끼워 넣는다 --

    "... is not tested through appropriate laborator y testing."
    "... of drug products purporting to be steril e are not established."

분류기의 ASCII 키워드는 `\\b` 단어경계 정규식이라 쪼개진 단어는 **조용히** 매칭에
실패하고, 그 지적은 덜 구체적인 카테고리(대개 캐치올 other_quality_system)로 떨어진다.
★이 실패가 소리를 내지 않는다는 점이 핵심이다 -- 캐치올이 정상 응답처럼 반환되므로
"분류 실패"와 "분류할 신호가 없음"이 겉보기에 구분되지 않는다.

라이브 실측(공개 findings 11,844행 전수 스윕): 쪼개진 행 134건, 그중 카테고리가 바뀌는
행 48건. 최초 제보는 10건이었다.

v6 은 매칭 **전에** 쪼개진 신호어를 되붙인다(`_repair_split_words`). 저장된 finding_text
는 절대 건드리지 않는다 -- 복원은 메모리 안 haystack 한정이다.

아래 테스트는 네 가지를 각각 고정한다:
  1. 제보된 2개 문형(211.165(b)/211.113(b))이 정본 카테고리로 간다 -- 라이브 원문 그대로.
  2. 복원이 **신호어에만** 적용된다 -- 무관한 산문의 공백은 지워지지 않는다.
  3. 보수적 경계: "a septic" 문맥 요구·"back up" 제외·2회 이상 손상은 범위 밖.
  4. 계약: 카테고리 20개·순서·코드 불변, 저장 텍스트 불변, 버전 IN-list 확장.
"""

from __future__ import annotations

import unittest

import grm_findings as gf


# 라이브 실측 finding_text 그대로(2026-08-02 스윕). 축약·정규화하지 않았다.
_LIVE_211_165B = (
    # fda483-86687 / 89977 / 85500 / 91478 -- 6건이 other_quality_system 에 있었다.
    "Each batch of drug product required to be free of objectionable microorganisms "
    "is not tested through appropriate laborator y testing.",
    # fda483-105357 -- 같은 문형, 쪼개진 위치가 다르다(앞쪽).
    "Each batch of drug product required to be free of objectionable microorganisms "
    "is not tested through appropriate la boratory testing.",
    # fda483-91790 -- "required" 까지 추가 손상("reqwred")된 실측 원문.
    "Each batch of drug product reqwred to be free of objectionable microorganisms "
    "is not tested through appropriate laborator y testing.",
)

_LIVE_211_113B = (
    # fda483-104902 / 99413 / 93385 -- contamination_control 에 잘못 있었다.
    "Procedures designed to prevent microbiological contamination of drug products "
    "purporting to be steri le are not established, written and followed.",
    # fda483-96641 / 98662 / 107233 -- 쪼개진 위치가 다른 같은 문형.
    "Procedures designed to prevent microbiological contamination of drug products "
    "purporting to be steril e are not written and followed.",
)


class ReportedFamiliesTest(unittest.TestCase):
    """v6 §2: 제보된 두 문형이 정본 카테고리로 간다(라이브 원문 그대로)."""

    def test_211_165b_laboratory_testing_reaches_qc_lab_controls(self) -> None:
        """"laborator y" 하나 때문에 qc_lab_controls 의 "laboratory" 키워드가 놓쳐졌다.
        v5 는 이 문형을 오염관리에서 빼내는 데까지만 성공했고(캐치올 착지), 정본
        귀속처인 시험실/품질관리로 보내는 것은 이 split 복원이 있어야 가능하다."""
        for text in _LIVE_211_165B:
            with self.subTest(text=text[-45:]):
                self.assertEqual(gf.classify_finding_category(text), "qc_lab_controls")

    def test_211_113b_purporting_to_be_sterile_reaches_aseptic(self) -> None:
        """(b)항은 **무균 표방** 제품이라 무균보증이 정본이다(v5 가 (a)와 가른 그 축).
        "steril e"/"steri le" 로 쪼개지면 aseptic 패턴이 놓치고, 문장에 남은
        "contamination" 키워드 때문에 contamination_control 에 잘못 안착했다."""
        for text in _LIVE_211_113B:
            with self.subTest(text=text[-45:]):
                self.assertEqual(
                    gf.classify_finding_category(text), "aseptic_sterility_assurance"
                )

    def test_v5_negation_still_wins_over_repair_for_211_113a(self) -> None:
        """★순서 계약: 복원이 v5 중립화보다 **먼저** 돈다. (a)항은 복원 후에도 역극성
        중립화가 걸려 무균으로 가면 안 된다 -- 복원이 v5 를 무력화하지 않는지 고정한다."""
        text = (
            "Procedures designed to prevent obj ectionable microorganisms in drug pro ducts "
            "not required to be sterile are not established."
        )
        self.assertEqual(gf.classify_finding_category(text), "contamination_control")

    def test_repair_makes_the_v5_negation_guard_harder_to_evade(self) -> None:
        """v5 의 `_NEGATED_STERILE_RE` 는 온전한 단어로 쓰여 있어("not required to be
        sterile") **그 구절 자체가** 쪼개지면 무력화된다. 복원이 먼저 돌면 구절이 되붙어
        중립화가 정상 발동한다. (라이브 실측 0건이지만 구조적으로 가능한 경로다.)"""
        split_negation = (
            "Procedures designed to prevent objectionable microorganisms in drug "
            "product s not requi red to be sterile are not established."
        )
        self.assertNotEqual(
            gf.classify_finding_category(split_negation), "aseptic_sterility_assurance"
        )


class RepairIsSignalWordScopedTest(unittest.TestCase):
    """v6 §4: 복원은 **분류기가 매칭하는 신호어**로만 한정된다 -- 광범위 휴리스틱 아님."""

    def test_ordinary_prose_spaces_are_not_removed(self) -> None:
        """조인 결과가 신호어가 아니면 공백은 그대로다. "to gether"/"in formed" 처럼
        붙이면 단어가 되는 경우조차 신호어가 아니므로 건드리지 않는다."""
        for text in (
            "The operators worked to gether on the line.",
            "The manager was in formed of the result.",
            "He signed the do cument yesterday.",
        ):
            with self.subTest(text=text):
                self.assertEqual(gf._repair_split_words(text.lower()), text.lower())

    def test_repair_never_mutates_stored_text(self) -> None:
        """★계약: 복원은 메모리 안 haystack 한정이다. classify 는 순수 함수이며 입력
        문자열은 바이트 불변으로 남는다(저장 텍스트 수리가 아니라는 것의 테스트)."""
        original = (
            "Each batch of drug product required to be free of objectionable "
            "microorganisms is not tested through appropriate laborator y testing."
        )
        snapshot = str(original)
        gf.classify_finding_category(original)
        self.assertEqual(original, snapshot)
        self.assertIn("laborator y", original)

    def test_repair_joins_only_one_space_one_time(self) -> None:
        """공백 1칸 1회만 -- 실측 134행이 전부 그랬다. 여러 칸/여러 번은 복원 대상 아님."""
        self.assertEqual(
            gf._repair_split_words("appropriate laborator y testing"),
            "appropriate laboratory testing",
        )
        # 공백 2칸은 복원하지 않는다(손상 모형이 다르다).
        self.assertIn("laborator  y", gf._repair_split_words("appropriate laborator  y testing"))

    def test_vocabulary_is_derived_from_the_taxonomy_not_hardcoded(self) -> None:
        """v6 §4: 어휘가 FINDING_TAXONOMY 에서 파생되므로 나중에 키워드를 추가해도
        복원이 따라온다. 대표 신호어가 실제로 어휘에 있는지 고정한다."""
        vocabulary = gf._split_repair_vocabulary()
        for word in ("laboratory", "sterile", "aseptic", "contamination", "cleanroom"):
            with self.subTest(word=word):
                self.assertIn(word, vocabulary)
        # 복수형도 파생된다(_ascii_keyword_pattern 의 `s?` 규칙 반영, "compo nents" 실측).
        self.assertIn("components", vocabulary)

    def test_ocr_damage_confirmed_in_live_samples(self) -> None:
        """실측에서 확인된 손상 문형들 -- 각각 정본 카테고리로 간다."""
        for text, expected in (
            # fda483-91230 / 100069
            ("Protective apparel is not worn as necessary to protect drug products "
             "from contaminat ion.", "contamination_control"),
            # fda483-101487
            ("The lSO 5 positive pressure clean room is not operated appropriately "
             "to ensure adequate air flow within the room.", "environmental_monitoring"),
            # fda483-94192
            ("There is no writte n testing program designed to assess the stabil ity "
             "characterist ics of drug products.", "stability_storage"),
            # fda483-112561
            ("Employees engaged in the manufacture, processing, packing and holding of "
             "a drug product lack the t raining required to perform their assigned "
             "functions.", "training_personnel"),
            # fda483-79239
            ("each lo t of compone nts was not appropria tly identifie d as to its "
             "s tatus in term s of being quarantined, approved or rejected.",
             "material_supplier_control"),
            # fda483-94413
            ("Washing and toilet faci lities lack hot and cold water.",
             "equipment_facility"),
        ):
            with self.subTest(text=text[:45]):
                self.assertEqual(gf.classify_finding_category(text), expected)


class ConservativeBoundariesTest(unittest.TestCase):
    """v6 §5: 실측으로 정한 보수적 경계 -- 넓히지 않는다."""

    def test_a_septic_requires_gmp_context(self) -> None:
        """"a septic" 은 그 자체로 성립하는 영어라("a septic tank") 뒤 문맥을 요구한다.
        실측 7건은 전부 processing/conditions 였고 코퍼스에 septic tank 는 0건이었다."""
        # 라이브 실측(fda483-107284 / 188152 / 95216) -- 복원된다.
        self.assertEqual(
            gf.classify_finding_category(
                "Personnel engaged in a septic processing were observed with exposed mouth."
            ),
            "aseptic_sterility_assurance",
        )
        self.assertEqual(
            gf.classify_finding_category(
                "A septic processing areas are deficient regarding the system for "
                "monitoring environmental conditions."
            ),
            "aseptic_sterility_assurance",
        )

    def test_a_septic_without_gmp_context_is_left_alone(self) -> None:
        """★문맥이 없으면 건드리지 않는다 -- 배수/정화조 문장이 무균으로 뒤집히면 안 된다."""
        text = "the firm installed a septic tank behind the warehouse."
        self.assertEqual(gf._repair_split_words(text), text)
        self.assertNotEqual(
            gf.classify_finding_category(
                "The firm installed a septic tank behind the warehouse."
            ),
            "aseptic_sterility_assurance",
        )

    def test_back_up_verb_phrase_is_excluded(self) -> None:
        """"back up"(동사구)이 backup(CSV 키워드)으로 조인되면 안 된다 -- 예방적 제외."""
        self.assertIn("back up", gf._repair_split_words("the operator must back up the pallet"))

    def test_single_letter_wordlike_heads_are_not_generally_joined(self) -> None:
        """왼쪽 조각이 1글자 영어 단어("a"/"i")인 변형은 일반 규칙에서 빠진다."""
        text = "the auditor reviewed a nnual reports"  # "a nnual" -> annual 로 붙이지 않는다
        self.assertEqual(gf._repair_split_words(text), text)

    def test_known_limitation_multiple_damage_is_out_of_scope(self) -> None:
        """v6 §6 알려진 한계: 손상이 2회 이상 겹친 원문(공백 여러 개 + 문자 오인식)은
        복원 범위 밖이다. 실측 fda483-94192 의 "ste ri I izatio n" 이 그 예 --
        honesty over forcing a match(v4/v5 관례). 실제 출력을 고정해 표류를 막는다."""
        text = "do no t include adequate validat ion of the ste ri I izatio n process."
        repaired = gf._repair_split_words(text)
        self.assertIn("ste ri I izatio n", repaired)
        # "validat ion" 은 단일 손상이라 복원된다 -- 부분 복원이 일어나는 것 자체는 정상.
        self.assertIn("validation", repaired)

    def test_legitimate_spaced_spellings_are_deliberately_joined(self) -> None:
        """v6 §6: "clean room"(실측 10건)·"record keeping"(1건)은 OCR 손상이 아니라
        정당한 띄어쓰기다. 분류기 키워드가 cleanroom/recordkeeping 이라 같은 메커니즘에
        걸리는데, 실측 전건이 명사 용법이고 조인 결과가 의미상 정확하므로 **의도적으로
        허용**한다. 동작을 고정해 두어 나중에 우연히 바뀌지 않게 한다."""
        self.assertEqual(
            gf.classify_finding_category("Cleaning or Sanitizing of ISO 7 clean room is not adequate."),
            "environmental_monitoring",
        )
        self.assertEqual(
            gf.classify_finding_category(
                "Your procedure does not describe how your firm will address "
                "documentation and record keeping requirements."
            ),
            "documentation_records",
        )


class NoRegressionTest(unittest.TestCase):
    """v6 은 순수 additive 정규화다 -- 온전한 텍스트의 판정은 하나도 바뀌지 않는다."""

    def test_undamaged_text_classifies_exactly_as_before(self) -> None:
        for text, expected in (
            ("The product must remain sterile at all times.", "aseptic_sterility_assurance"),
            ("Aseptic processing area smoke studies were not performed.", "aseptic_sterility_assurance"),
            ("Use of non-sterile gloves was observed.", "other_quality_system"),
            ("Each batch of drug product required to be free of objectionable "
             "microorganisms is not tested through appropriate laboratory testing.",
             "qc_lab_controls"),
            ("Procedures designed to prevent objectionable microorganisms in drug "
             "products not required to be sterile are not established.",
             "contamination_control"),
            ("The audit trail was disabled for the HPLC system.", "data_integrity"),
            ("Calibration of the balance was not performed on schedule.", "equipment_facility"),
        ):
            with self.subTest(text=text[:45]):
                self.assertEqual(gf.classify_finding_category(text), expected)

    def test_empty_text_still_returns_catch_all(self) -> None:
        self.assertEqual(gf.classify_finding_category(""), "other_quality_system")
        self.assertEqual(gf.classify_finding_category("   "), "other_quality_system")

    def test_repair_recovers_signal_in_damaged_text(self) -> None:
        """쪼개진 신호어가 복원돼 캐치올을 벗어나는 대표 경로를 고정한다.

        ★2026-08-02 정정: 이 테스트의 원래 이름은 test_repair_never_demotes_into_the_
        catch_all 이었고 "복원은 신호를 지우지 않는다"는 **불변식**을 주장했다. 그 주장은
        틀렸다 -- 아래 OutsourcingLookbehindTest 참조. 이름과 주장을 실제로 참인 것
        (복원이 손상 텍스트에서 신호를 회수한다)으로 좁힌다."""
        for text in (
            "The audit trail was disabled for the labora tory system.",
            "Calibration records for the equ ipment were missing.",
        ):
            with self.subTest(text=text[:40]):
                self.assertNotEqual(
                    gf.classify_finding_category(text), "other_quality_system"
                )


class OutsourcingLookbehindTest(unittest.TestCase):
    """★복원이 부정 lookbehind 를 **활성화**해 매치를 제거하는 경로(전수 1건).

    equipment_facility 의 v3 패턴은 `(?<!outsourcing )facilit(?:y|ies)` 로 "Outsourcing
    Facility"(FDA 업소 유형 라벨 -- 물리적 시설 지적이 아니다)를 의도적으로 배제한다.
    원문이 쪼개져 있으면 lookbehind 가 안 걸려 설비/시설로 잘못 잡히고, 복원이 되붙이면
    v3 의 배제가 비로소 정상 작동한다. 라이브 전수 재분류 dry-run 에서 실제로 관측된
    유일한 캐치올 강등이며(fda483-90268), **의도된 정본 동작**이다.

    이 클래스가 존재하는 이유: 최초 v6 change log 는 "복원은 신호를 지우지 않는다"는
    불변식을 적었는데 그것이 틀렸음을 이 경로가 보여준다. 동작을 테스트로 고정해
    같은 오해가 다시 문서에 들어가지 않게 한다."""

    def test_split_outsourcing_facility_is_excluded_after_repair(self) -> None:
        """라이브 실측(fda483-90268) 성질 -- 483 표지 헤더 파편."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "TYPE ESTABLISHMENT INSPECTED Outsour cing Facility"
            ),
            "equipment_facility",
        )

    def test_undamaged_outsourcing_facility_was_already_excluded(self) -> None:
        """v3 이래 온전한 표기는 이미 배제돼 있었다 -- v6 은 손상본을 같은 결론으로 맞춘 것."""
        self.assertNotEqual(
            gf.classify_finding_category("TYPE ESTABLISHMENT INSPECTED Outsourcing Facility"),
            "equipment_facility",
        )

    def test_real_facility_findings_still_match(self) -> None:
        """배제는 "outsourcing" 이 붙은 경우로 한정된다 -- 진짜 시설 지적은 그대로."""
        for text in (
            "The manufacturing facility roof leaked over the filling line.",
            "Washing and toilet faci lities lack hot and cold water.",
        ):
            with self.subTest(text=text[:45]):
                self.assertEqual(
                    gf.classify_finding_category(text), "equipment_facility"
                )


class TaxonomyV6BoundedTest(unittest.TestCase):
    """v6 계약: 카테고리 집합·순서 불변, 버전 IN-list 만 확장."""

    def test_v6_is_still_an_accepted_taxonomy_version(self) -> None:
        """v7(2026-08-02 접착 손상 -- v6 의 거울상)로 현재 버전이 올라갔다. 이 클래스가
        지키는 것은 "v6 이 현재 버전"이 아니라 **v6 으로 저장된 기존 행이 계속 유효하다**는
        계약이다(v4->v5 때와 동일한 additive 규율). 현재 버전 고정은
        tests/test_findings_taxonomy_v7.py 로 이동."""
        self.assertIn("grm-finding-taxonomy/v6", gf.TAXONOMY_VERSIONS)
        self.assertEqual(
            gf.TAXONOMY_VERSIONS[:6],
            (
                "grm-finding-taxonomy/v1",
                "grm-finding-taxonomy/v2",
                "grm-finding-taxonomy/v3",
                "grm-finding-taxonomy/v4",
                "grm-finding-taxonomy/v5",
                "grm-finding-taxonomy/v6",
            ),
        )
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)
        self.assertEqual(len(gf.FINDING_CATEGORY_CODES), len(set(gf.FINDING_CATEGORY_CODES)))

    def test_v6_introduces_no_new_category_and_no_reorder(self) -> None:
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

    def test_repair_runs_before_negation_neutralisation(self) -> None:
        """순서가 계약이라는 것을 구현 수준에서 고정한다 -- 복원된 haystack 에 역극성
        구절이 온전히 나타나야 v5 중립화가 걸린다."""
        haystack = "drug product s not requi red to be steril e".lower()
        repaired = gf._repair_split_words(haystack)
        self.assertIn("not required to be sterile", repaired)
        self.assertEqual(gf._NEGATED_STERILE_RE.sub(" ", repaired).count("sterile"), 0)


if __name__ == "__main__":
    unittest.main()
