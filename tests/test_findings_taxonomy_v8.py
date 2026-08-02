#!/usr/bin/env python3
"""grm-finding-taxonomy/v8 tests -- 캐치올 어휘 공백.

★v5/v6/v7 과 **성질이 다른 변경**이다. 앞의 셋은 매칭 전 haystack 을 고치는 복원
계층이라 없던 신호를 되살릴 뿐 기존 정분류를 빼앗을 수 없었다. 어휘 추가는
first-match-wins 매칭 자체를 바꿔 **이미 올바른 행을 앞선 카테고리가 빼앗는다**.
그래서 이 파일의 테스트는 "회수되는가"만큼 "**빼앗지 않는가**"에 무게를 둔다.

세 가지 설계 결정을 테스트로 잠근다:
  1. 규칙은 전부 **다단어 절 구절**이다. 단일 명사(record/specification/written
     procedure)는 collateral 이 rescue 의 2.8~13.6배라 게이트가 자동 차단했다.
  2. 규칙은 전부 `patterns` 에 있고 `keywords` 에는 없다 -- `_split_repair_vocabulary()`
     가 keywords 에서만 어휘를 파생하므로 keywords 에 넣으면 v6/v7 복원 어휘까지
     조용히 넓어진다.
  3. **재정렬하지 않고 순서를 자산으로 썼다** -- 귀속 카테고리를 뒤로 옮기는 것만으로
     같은 신호의 collateral 이 54 -> 0 이 된다.
"""

from __future__ import annotations

import unittest

import grm_findings as gf


class RescueTest(unittest.TestCase):
    """캐치올에서 정본 카테고리로 회수되는 9개 조항군."""

    CASES = (
        ("503B 등록·보고 의무",
         "Your outsourcing facility has not submitted a report to FDA identifying a product "
         "compounded during the previous six months as required by section 503B(b)(2)(A).",
         "regulatory_reporting"),
        ("보고 의무 불이행(능동)",
         "Your firm failed to submit a report to FDA as required by the regulation.",
         "regulatory_reporting"),
        ("보고 의무 불이행(수동 어순)",
         "The required reports were not submitted to the agency.",
         "regulatory_reporting"),
        ("211.56 해충",
         "The building used in the manufacture of drug products is not free of vermin.",
         "equipment_facility"),
        ("211.46 환기",
         "There is inadequate ventilation to prevent contamination.",
         "equipment_facility"),
        ("ISO 등급구역 인증",
         "The ISO 5 area was not certified within the required interval.",
         "validation_qualification"),
        ("211.111 시간 한도",
         "Appropriate time limits for the completion of each phase of production were not established.",
         "process_validation"),
        ("211.166(a) 안정성 배치수",
         "The stability testing program does not include an adequate number of batches.",
         "stability_storage"),
        ("규격 적합성 구절",
         "There is no assurance of conformance to written specifications prior to release.",
         "qc_lab_controls"),
        ("test method 표면형",
         "The testing methods used were not verified under actual conditions of use.",
         "qc_lab_controls"),
        ("211.180(e) 어순 역전",
         "Records were not reviewed at least annually to evaluate product quality standards.",
         "quality_unit_oversight"),
    )

    def test_all_rescued_families_reach_their_canonical_category(self) -> None:
        for name, text, expected in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(gf.classify_finding_category(text), expected)

    def test_ocr_damaged_variants_are_also_rescued(self) -> None:
        """v6 분리 복원이 먼저 도는 순서 덕분에 손상된 표면형도 함께 회수된다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Appropriate time lim its for the completion of each phase of production "
                "were not established."
            ),
            "process_validation",
        )

    def test_v7_adhesion_variant_of_test_methods_is_rescued(self) -> None:
        """선두 `of` 는 v7 접착 손상 잔재("oftest methods")를 함께 받는다."""
        self.assertEqual(
            gf.classify_finding_category("The verification oftest methods was not documented."),
            "qc_lab_controls",
        )


class DoesNotStealTest(unittest.TestCase):
    """★이번 변경의 진짜 위험 -- 멀쩡한 행을 빼앗지 않는가."""

    def test_field_alert_stays_in_complaint_recall(self) -> None:
        """★실측으로 미리 확인한 충돌. complaint_recall(7번째)의 "field alert" 키워드가
        regulatory_reporting(18번째)의 새 보고 규칙보다 앞이라 first-match-wins 로
        보호된다. 이 보호는 우연이 아니라 rescue 41 계산에 반영된 전제다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Your firm failed to submit a field alert report to the agency "
                "within three working days."
            ),
            "complaint_recall",
        )

    def test_plain_iso_grade_mention_stays_in_environmental_monitoring(self) -> None:
        """ISO 규칙은 `certif` 를 함께 요구한다 -- 단순 등급 언급은 환경모니터링 소관이다.
        이걸 안 가르면 environmental_monitoring 의 정분류를 빼앗는다."""
        self.assertEqual(
            gf.classify_finding_category(
                "Environmental monitoring of the ISO 7 room was not performed."
            ),
            "environmental_monitoring",
        )

    def test_rejected_single_noun_rules_were_not_added(self) -> None:
        """★게이트가 자동 차단한 기각군 -- 단일 명사는 collateral 이 rescue 의 2.8~13.6배라
        채택하지 않았다. 이 문형들이 캐치올에 남는 것이 **정직한 상태**다."""
        for text in (
            "The specifications were not approved before use.",
            "Written procedures were not established for this operation.",
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual(
                    gf.classify_finding_category(text), "other_quality_system"
                )

    def test_out_of_scope_503b_copy_clause_is_not_pulled_in(self) -> None:
        """★범위밖(미승인약) 12건 -- 정답 카테고리가 없다. bare `503B` 로 넓혔다면
        끌려왔을 문형이고, 절 구절이라 배제된다. 캐치올 잔류가 정직한 상태다."""
        self.assertEqual(
            gf.classify_finding_category(
                "You compound drugs that are essentially a copy of one or more approved "
                "drugs within the meaning of sections 503B(a)(5) and 503B(d)(2)."
            ),
            "other_quality_system",
        )

    def test_labeling_family_is_left_for_a_separate_rule(self) -> None:
        """★용기/표시 503B 건은 정답이 labeling_packaging(17)이라 별건으로 남겼다.
        지금 18번으로 끌어오면 그 후보가 필요하다는 신호(캐치올 잔류)까지 지운다."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "The containers of your outsourcing facility's drug products do not include "
                "information required by section 503B(a)(10)(B)."
            ),
            "regulatory_reporting",
        )


class DesignContractTest(unittest.TestCase):
    def test_new_rules_live_in_patterns_never_in_keywords(self) -> None:
        """★`_split_repair_vocabulary()` 는 keywords 에서만 어휘를 파생한다. v8 규칙이
        keywords 에 들어가면 v6 분리복원·v7 접착복원 대상까지 조용히 넓어져 폭발반경이
        통제 불능이 된다. keywords 가 v7 시점과 동일함을 고정한다."""
        by_code = {c.code: c for c in gf.FINDING_TAXONOMY}
        self.assertEqual(
            by_code["validation_qualification"].keywords,
            ("validation", "qualification", "qualified", "밸리데이션", "적격성", "검증"),
        )
        self.assertEqual(
            by_code["stability_storage"].keywords,
            ("stability", "storage", "temperature", "humidity", "안정성", "보관", "온도", "습도"),
        )
        self.assertEqual(
            by_code["regulatory_reporting"].keywords,
            ("change control", "submission", "reporting", "변경관리", "보고", "허가"),
        )
        self.assertEqual(
            by_code["equipment_facility"].keywords,
            ("equipment", "maintenance", "calibration", "building", "설비", "시설", "교정"),
        )

    def test_split_repair_vocabulary_did_not_grow_from_v8(self) -> None:
        """위 계약의 결과 -- 복원 어휘에 v8 규칙 전용 단어가 들어가지 않았다.

        ※`outsourcing` 은 여기 넣지 않는다 -- v6 이 이미 `_SPLIT_REPAIR_PATTERN_WORDS` 에
        손으로 넣어둔 단어다(equipment_facility 의 `(?<!outsourcing )facilit` 룩비하인드가
        분리 손상 때문에 무력화되는 것을 막으려고). v8 이 넣은 것이 아니다.
        """
        vocab = set(gf._split_repair_vocabulary())
        for word in ("vermin", "ventilation", "lighting", "conformance", "certification"):
            with self.subTest(word=word):
                self.assertNotIn(word, vocab)

    def test_iso_rule_is_attached_late_not_reordered(self) -> None:
        """★"순서를 자산으로 쓴다" -- ISO 신호를 environmental_monitoring(5번째) 대신
        validation_qualification(15번째)에 붙여 collateral 54 -> 0 을 만들었다.
        카테고리 순서 자체는 v3 이후 불변이다."""
        codes = [c.code for c in gf.FINDING_TAXONOMY]
        self.assertEqual(codes.index("environmental_monitoring"), 4)
        self.assertEqual(codes.index("validation_qualification"), 14)
        by_code = {c.code: c for c in gf.FINDING_TAXONOMY}
        self.assertTrue(any("certif" in p for p in by_code["validation_qualification"].patterns))
        self.assertFalse(any("certif" in p for p in by_code["environmental_monitoring"].patterns))

    def test_v8_is_still_an_accepted_taxonomy_version(self) -> None:
        """v9(503B 용기 표시정보)로 현재 버전이 올라갔다. 이 클래스가 지키는 것은
        "v8 이 현재 버전"이 아니라 **v8 로 저장된 기존 행이 계속 유효하다**는 계약이다.
        현재 버전 고정은 tests/test_findings_taxonomy_v9.py 로 이동."""
        self.assertIn("grm-finding-taxonomy/v8", gf.TAXONOMY_VERSIONS)
        self.assertEqual(
            gf.TAXONOMY_VERSIONS[:8],
            tuple(f"grm-finding-taxonomy/v{n}" for n in range(1, 9)),
        )
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)

    def test_v8_introduces_no_new_category_and_no_reorder(self) -> None:
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


class PriorRevisionRegressionTest(unittest.TestCase):
    def test_v5_v6_v7_all_still_hold(self) -> None:
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
            ("v7 접착(211.192)",
             "Written records are not made ofinvestigations into unexplained discrepancies.",
             "deviation_capa"),
        ):
            with self.subTest(name=name):
                self.assertEqual(gf.classify_finding_category(text), expected)


if __name__ == "__main__":
    unittest.main()
