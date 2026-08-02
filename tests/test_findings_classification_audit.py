#!/usr/bin/env python3
"""findings_classification_audit -- 분류 표류 상시 감사 테스트.

이 감사기의 존재 이유는 "v5/v6/v7 같은 오분류를 **사람보다 먼저** 발견한다" 이므로,
테스트의 중심도 거기다: 손상 종류를 **모르는 상태**에서도 잡히는가.
"""

from __future__ import annotations

import unittest

import findings_classification_audit as audit
import grm_findings as gf


def _row(fid: str, text: str, category: str, *, source: str = "FDA 483", scope: str = "ok") -> dict:
    return {
        "finding_id": fid,
        "finding_text": text,
        "category_code": category,
        "source": source,
        "scope_status": scope,
    }


class TwinKeyTest(unittest.TestCase):
    """★정규화 키 하나가 감사의 일반성을 만든다."""

    def test_space_insertion_converges_with_clean_twin(self) -> None:
        """v6 클래스 -- 공백이 끼어든 쌍둥이."""
        self.assertEqual(
            audit.normalize_twin_key("appropriate laborator y testing"),
            audit.normalize_twin_key("appropriate laboratory testing"),
        )

    def test_space_drop_converges_with_clean_twin(self) -> None:
        """v7 클래스 -- 공백이 탈락한 쌍둥이."""
        self.assertEqual(
            audit.normalize_twin_key("rejection ofcomponents"),
            audit.normalize_twin_key("rejection of components"),
        )

    def test_unnamed_damage_classes_also_converge(self) -> None:
        """★핵심 성질: 아직 **이름 붙이지 않은** 손상도 같은 키로 수렴한다. 구두점 삽입·
        줄바꿈·하이픈 절단은 어떤 복원 규칙도 다루지 않지만 감사는 규칙 없이 잡는다."""
        clean = audit.normalize_twin_key("any unexplained discrepancy in the batch")
        for damaged in (
            "any unexplained d.iscrepancy in the batch",
            "any unexplained dis-\ncrepancy in the batch",
            "any  unexplained   discrepancy  in the batch",
            "any unexplained discrepancy in the batch.",
        ):
            with self.subTest(damaged=damaged[:40]):
                self.assertEqual(audit.normalize_twin_key(damaged), clean)

    def test_character_confusion_does_not_converge_known_limitation(self) -> None:
        """정직하게 고정하는 알려진 한계 -- 문자 오인식("quaJity")은 키가 달라진다.
        편집거리 매칭을 넣으면 실제 영어 단어를 삼켜 오탐이 되므로 넣지 않았다."""
        self.assertNotEqual(
            audit.normalize_twin_key("the quaJity unit"),
            audit.normalize_twin_key("the quality unit"),
        )

    def test_different_sentences_do_not_collide(self) -> None:
        self.assertNotEqual(
            audit.normalize_twin_key("The stability program is deficient."),
            audit.normalize_twin_key("The cleaning program is deficient."),
        )


class TwinDisagreementTest(unittest.TestCase):
    def test_damaged_twin_in_a_different_category_is_flagged(self) -> None:
        long_clean = (
            "Each batch of drug product required to be free of objectionable "
            "microorganisms is not tested through appropriate laboratory testing."
        )
        long_damaged = long_clean.replace("laboratory", "laborator y")
        clusters = audit.find_twin_disagreements([
            _row("f-1", long_clean, "qc_lab_controls"),
            _row("f-2", long_clean, "qc_lab_controls"),
            _row("f-3", long_damaged, "other_quality_system"),
        ])
        self.assertEqual(len(clusters), 1)
        cluster = clusters[0]
        self.assertEqual(cluster["cluster_size"], 3)
        minority = [m for m in cluster["members"] if m["is_minority"]]
        self.assertEqual([m["finding_id"] for m in minority], ["f-3"])

    def test_agreeing_cluster_is_not_flagged(self) -> None:
        text = (
            "Each batch of drug product required to be free of objectionable "
            "microorganisms is not tested through appropriate laboratory testing."
        )
        self.assertEqual(
            audit.find_twin_disagreements([
                _row("f-1", text, "qc_lab_controls"),
                _row("f-2", text.replace("laboratory", "laborator y"), "qc_lab_controls"),
            ]),
            [],
        )

    def test_all_categories_are_reported_not_just_the_minority(self) -> None:
        """★소수파를 '틀린 쪽'이라 단정하지 않는다 -- 실측에서 다수 쪽이 손상인
        클러스터가 있었다. 사람이 판단하도록 양쪽 건수를 모두 싣는다."""
        text = "A" * 20 + " written procedures for the control of the manufacturing process are absent."
        cluster = audit.find_twin_disagreements([
            _row("f-1", text, "process_validation"),
            _row("f-2", text, "process_validation"),
            _row("f-3", text, "documentation_records"),
        ])[0]
        codes = {c["category_code"] for c in cluster["categories"]}
        self.assertEqual(codes, {"process_validation", "documentation_records"})

    def test_short_texts_do_not_form_clusters(self) -> None:
        """짧은 지적문은 우연히 같은 키가 되어 무의미한 클러스터를 만든다."""
        self.assertEqual(
            audit.find_twin_disagreements([
                _row("f-1", "Deficient.", "other_quality_system"),
                _row("f-2", "Deficient.", "equipment_facility"),
            ]),
            [],
        )


class ClassifierDriftTest(unittest.TestCase):
    def test_stored_category_disagreeing_with_current_classifier_is_flagged(self) -> None:
        drift = audit.find_classifier_drift([
            _row("f-1", "The stability program is deficient.", "equipment_facility"),
        ])
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["stored_category"], "equipment_facility")
        self.assertEqual(drift[0]["current_category"], "stability_storage")

    def test_agreeing_row_is_not_flagged(self) -> None:
        self.assertEqual(
            audit.find_classifier_drift([
                _row("f-1", "The stability program is deficient.", "stability_storage"),
            ]),
            [],
        )


class ZeroCountGuardTest(unittest.TestCase):
    """★"회귀 0은 측정이 아니라 구조로" -- 정규화가 깨지면 감사가 조용히 초록이 된다."""

    def test_guard_passes_on_healthy_normalizer(self) -> None:
        self.assertEqual(audit.guard_failures(), [])

    def test_guard_catches_a_broken_normalizer(self) -> None:
        original = audit.normalize_twin_key
        try:
            audit.normalize_twin_key = lambda text: str(text)  # 정규화 무력화
            self.assertTrue(audit.guard_failures())
        finally:
            audit.normalize_twin_key = original

    def test_guard_failure_always_counts_as_breach_even_with_zero_suspects(self) -> None:
        """가드가 깨진 채 0건을 보고하는 것이 가장 위험한 상태다 -- 반드시 breach."""
        original = audit.normalize_twin_key
        try:
            audit.normalize_twin_key = lambda text: str(text)
            report = audit.build_report([])
            self.assertTrue(report["guard_failures"])
            self.assertTrue(report["breach"])
            self.assertEqual(report["totals"]["twin_clusters"], 0)
        finally:
            audit.normalize_twin_key = original


class ReportContractTest(unittest.TestCase):
    def test_clean_corpus_reports_no_breach(self) -> None:
        report = audit.build_report([
            _row("f-1", "The stability program is deficient.", "stability_storage"),
        ])
        self.assertFalse(report["breach"])
        self.assertEqual(report["totals"], {
            "twin_clusters": 0, "twin_minority_rows": 0, "drift_rows": 0,
        })

    def test_report_carries_taxonomy_version_and_scope_status(self) -> None:
        report = audit.build_report([
            _row("f-1", "The stability program is deficient.", "equipment_facility", scope="non_pharma"),
        ])
        self.assertEqual(report["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual(report["classifier_drift"][0]["scope_status"], "non_pharma")

    def test_snippets_are_truncated_so_full_text_never_reaches_a_public_issue(self) -> None:
        long_text = "x" * 5000 + " the stability program is deficient."
        report = audit.build_report([_row("f-1", long_text, "equipment_facility")])
        self.assertLessEqual(len(report["classifier_drift"][0]["snippet"]), 140)

    def test_module_never_writes(self) -> None:
        """읽기 전용 계약을 구조로 고정한다 -- 쓰기 동사가 소스에 없어야 한다."""
        import inspect
        source = inspect.getsource(audit)
        for verb in ("requests.patch", "requests.post", "requests.delete", "session.patch"):
            self.assertNotIn(verb, source)


if __name__ == "__main__":
    unittest.main()
