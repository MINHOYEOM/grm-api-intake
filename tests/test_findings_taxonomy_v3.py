#!/usr/bin/env python3
"""grm-finding-taxonomy/v3 tests.

Two concerns, kept in separate test classes:

1. Mechanism: the new optional `FindingCategory.patterns` field (explicit regex,
   checked alongside `keywords`) behaves as documented -- case-insensitive,
   OR'd with keywords, negative lookbehind honored.

2. Fixture regression: the 2026-07-12 classification audit
   (archive/findings_classification_audit_2026-07-12.md) found 25 "wrong"
   samples out of a 100-item stratified audit. `tests/fixtures/
   taxonomy_v3_audit_wrong25.json` freezes each sample's real finding_text
   (fetched read-only from the live Supabase `findings_translation_rows` RPC)
   plus the v2-wrong category and the audit's expected v3 category. Cases the
   v3 keyword/pattern rule set genuinely cannot reach (OCR corruption, or a
   discovered conflict between two of the rules) are marked
   `known_limitation: true` with a reason -- those are pinned to their actual
   (still-wrong) v3 output so a *future* regression is caught, without
   pretending the rule set fixes something it structurally cannot.

   `tests/fixtures/taxonomy_v3_regression_correct.json` is the anti-regression
   companion: 10 real finding_text samples the audit (or this session's own
   review) found *correctly* classified under v2, covering the categories the
   v3 changes touch (aseptic/cleaning_validation/equipment_facility/
   documentation_records/data_integrity/etc.) -- these must still classify
   correctly under v3.
"""

from __future__ import annotations

import json
import os
import unittest

import grm_findings as gf


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class PatternMechanismTest(unittest.TestCase):
    """v3 mechanism: FindingCategory.patterns is checked alongside keywords."""

    def test_finding_category_has_patterns_field_defaulting_to_empty_tuple(self) -> None:
        plain = gf.FindingCategory("x", "라벨", "Label", ("keyword",))
        self.assertEqual(plain.patterns, ())

    def test_category_with_only_patterns_matches_via_pattern(self) -> None:
        # aseptic_sterility_assurance has no bare "sterile"/"sterility" keyword in v3 --
        # it must still match via the explicit pattern.
        self.assertEqual(
            gf.classify_finding_category("The product must remain sterile at all times."),
            "aseptic_sterility_assurance",
        )

    def test_patterns_are_case_insensitive(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("STERILIZATION cycle records were incomplete."),
            "aseptic_sterility_assurance",
        )

    def test_negative_lookbehind_blocks_non_sterile(self) -> None:
        self.assertNotEqual(
            gf.classify_finding_category("Use of non-sterile gloves was observed."),
            "aseptic_sterility_assurance",
        )

    def test_negative_lookbehind_blocks_non_sterile_no_hyphen(self) -> None:
        self.assertNotEqual(
            gf.classify_finding_category("Use of non sterile gloves was observed."),
            "aseptic_sterility_assurance",
        )

    def test_sterilized_activated_form_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("Containers were not properly sterilized before use."),
            "aseptic_sterility_assurance",
        )

    def test_pyrogenic_pattern_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Containers and closures were not processed to remove pyrogenic properties."
            ),
            "aseptic_sterility_assurance",
        )

    def test_computer_or_related_system_singular_matches_csv(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Appropriate controls are not exercised over computer or related system."
            ),
            "computer_system_validation",
        )

    def test_computerized_systems_activated_form_matches_csv(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Controls were not exercised over computerized systems used in manufacturing."
            ),
            "computer_system_validation",
        )

    def test_electronic_data_pattern_matches_csv(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Controls were not established to protect the electronic data acquisition systems."
            ),
            "computer_system_validation",
        )

    def test_batch_relaxed_adjacency_pattern_matches_documentation_records(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "The batch production and control records are deficient."
            ),
            "documentation_records",
        )

    def test_residue_pattern_matches_cleaning_validation(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("Residues from the previous batch were detected."),
            "cleaning_validation",
        )

    def test_carryover_pattern_matches_cleaning_validation(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("There is a lack of controls to minimize carryover."),
            "cleaning_validation",
        )

    def test_outsourcing_facility_does_not_match_equipment_facility(self) -> None:
        self.assertNotEqual(
            gf.classify_finding_category("The labels of your outsourcing facility's products are deficient."),
            "equipment_facility",
        )

    def test_plain_facility_still_matches_equipment_facility(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("Your facility is not maintained in a good state of repair."),
            "equipment_facility",
        )

    def test_building_keyword_matches_equipment_facility(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("You did not maintain a building in a clean and sanitary condition."),
            "equipment_facility",
        )

    def test_written_procedure_alone_no_longer_matches_documentation_records(self) -> None:
        self.assertNotEqual(
            gf.classify_finding_category("Written procedures are not followed for general housekeeping."),
            "documentation_records",
        )

    def test_bare_cleaning_alone_no_longer_matches_cleaning_validation(self) -> None:
        self.assertNotEqual(
            gf.classify_finding_category(
                "Equipment is not of appropriate design to facilitate cleaning and maintenance."
            ),
            "cleaning_validation",
        )

    def test_process_parameter_keyword_matches_process_validation(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("Process Parameters are not adequately controlled within established ranges."),
            "process_validation",
        )

    def test_complaint_field_alert_beats_contamination_control(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "There is a failure to submit a field alert report concerning contamination."
            ),
            "complaint_recall",
        )

    def test_complaint_records_beats_deviation_capa(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Complaints records are deficient in that they do not include the investigation."
            ),
            "complaint_recall",
        )


class TaxonomyV3BoundedTest(unittest.TestCase):
    """Note: taxonomy has since moved to v4 (see tests/test_findings_taxonomy_v4.py) --
    this class keeps its v3 name for history/diff-locality, but the version/order
    assertions below are updated in place to track whatever TAXONOMY_VERSION actually
    is, since there is only one live classify_finding_category(), not a v3-frozen one."""

    def test_taxonomy_v3_is_current_and_v1v2_still_valid(self) -> None:
        self.assertEqual(gf.TAXONOMY_VERSION, "grm-finding-taxonomy/v4")
        self.assertEqual(
            gf.TAXONOMY_VERSIONS,
            (
                "grm-finding-taxonomy/v1",
                "grm-finding-taxonomy/v2",
                "grm-finding-taxonomy/v3",
                "grm-finding-taxonomy/v4",
            ),
        )
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)
        self.assertEqual(len(gf.FINDING_CATEGORY_CODES), len(set(gf.FINDING_CATEGORY_CODES)))

    def test_taxonomy_order_moves_only_csv_and_complaint_recall(self) -> None:
        """The audit-mandated reorder touches exactly two categories; every
        other category keeps its v2 relative order."""
        codes = [c.code for c in gf.FINDING_TAXONOMY]
        v2_order = [
            "data_integrity", "documentation_records", "aseptic_sterility_assurance",
            "environmental_monitoring", "cleaning_validation", "deviation_capa",
            "quality_unit_oversight", "qc_lab_controls", "process_validation",
            "equipment_facility", "material_supplier_control", "contamination_control",
            "validation_qualification", "complaint_recall", "stability_storage",
            "computer_system_validation", "labeling_packaging", "regulatory_reporting",
            "training_personnel", "other_quality_system",
        ]
        moved = {"complaint_recall", "computer_system_validation"}
        v2_relative = [c for c in v2_order if c not in moved]
        v3_relative = [c for c in codes if c not in moved]
        self.assertEqual(v2_relative, v3_relative)

        # complaint_recall now precedes both deviation_capa and contamination_control.
        self.assertLess(codes.index("complaint_recall"), codes.index("deviation_capa"))
        self.assertLess(codes.index("complaint_recall"), codes.index("contamination_control"))
        # computer_system_validation precedes documentation_records (control-tower ruling
        # on the 648323af conflict: 21 CFR 211.68(b) is fundamentally a computerized-
        # systems control clause; the master production and control records it mentions
        # are the *object* those controls protect, so the computer/electronic-data signal
        # always outranks the records keyword) -- and hence also precedes
        # process_validation and training_personnel. Only data_integrity stays ahead.
        self.assertLess(codes.index("data_integrity"), codes.index("computer_system_validation"))
        self.assertLess(codes.index("computer_system_validation"), codes.index("documentation_records"))
        self.assertLess(codes.index("computer_system_validation"), codes.index("process_validation"))
        self.assertLess(codes.index("computer_system_validation"), codes.index("training_personnel"))


class AuditWrong25FixtureTest(unittest.TestCase):
    """Regression fixture from the 2026-07-12 audit's 25 wrong samples."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = _load_fixture("taxonomy_v3_audit_wrong25.json")

    def test_fixture_has_all_25_cases(self) -> None:
        self.assertEqual(len(self.cases), 25)
        for case in self.cases:
            for key in (
                "finding_id", "finding_text", "v2_wrong_category",
                "expected_v3_category", "known_limitation",
            ):
                self.assertIn(key, case)

    def test_fixable_cases_now_return_expected_category(self) -> None:
        fixable = [c for c in self.cases if not c["known_limitation"]]
        self.assertEqual(len(fixable), 20, "expected fix count changed -- update fixture/PR notes")
        failures = []
        for case in fixable:
            got = gf.classify_finding_category(case["finding_text"])
            if got != case["expected_v3_category"]:
                failures.append((case["finding_id"], got, case["expected_v3_category"]))
        self.assertEqual(failures, [], f"regressions among fixable audit cases: {failures}")

    def test_known_limitation_cases_are_pinned_not_silently_changing(self) -> None:
        """These 5 cases were flagged `known_limitation: true` in the v3 fixture
        because the v3 rule set could not reach the audit's expected category
        (OCR corruption, or domain knowledge beyond keyword matching -- see each
        case's known_limitation_reason). The `known_limitation` flag itself is
        frozen fixture metadata (the v3 fixture JSON is not edited post-hoc), but
        classify_finding_category() is a single live function -- when a later
        taxonomy revision genuinely fixes one of these cases, this test is
        updated to assert the *new* (correct) output instead of silently letting
        the old "still wrong" assertion rot.

        v4 update (grm-finding-taxonomy/v4, 2026-07-12 v3 사후 재감사 후보1/3):
        4 of the 5 cases are exactly the wrong9 cases v4's OCR-tolerance
        (candidate 1) and CPV/material vocabulary (candidate 3) rules were
        designed to fix, and real-data verification confirms they now resolve
        to their expected_v3_category -- `fixed_by_v4` below. Only
        ab4e3d27348680a83ef3685b ("quality of water ... non-sterile drug
        products") remains a genuine known_limitation after v4: no v4 candidate
        rule targets it, so it is still pinned to its actual (still-wrong)
        output. (648323af was similarly resolved during the v2->v3 transition --
        see the historical note that used to live here.)"""
        still_wrong_pinned = {
            "finding-ab4e3d27348680a83ef3685b": "other_quality_system",
        }
        fixed_by_v4 = {
            "finding-1b836c8a1bf34727d471053a",
            "finding-0a1df74ab174e17188c68719",
            "finding-596b2bd48f06e734ff289d44",
            "finding-a2d14f0f95909fc6d168155e",
        }
        known_limitation_ids = {c["finding_id"] for c in self.cases if c["known_limitation"]}
        self.assertEqual(known_limitation_ids, set(still_wrong_pinned) | fixed_by_v4)

        for case in self.cases:
            if not case["known_limitation"]:
                continue
            got = gf.classify_finding_category(case["finding_text"])
            if case["finding_id"] in fixed_by_v4:
                self.assertEqual(
                    got, case["expected_v3_category"],
                    f"expected v4 to fix known_limitation case {case['finding_id']}",
                )
            else:
                self.assertEqual(
                    got, still_wrong_pinned[case["finding_id"]],
                    f"known_limitation case {case['finding_id']} output drifted",
                )
            self.assertTrue(case["known_limitation_reason"].strip())


class RegressionCorrectFixtureTest(unittest.TestCase):
    """Anti-regression fixture: 10 real samples the audit found correctly
    classified under v2 -- must still classify correctly under v3."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = _load_fixture("taxonomy_v3_regression_correct.json")

    def test_fixture_has_ten_cases_covering_distinct_categories(self) -> None:
        self.assertEqual(len(self.cases), 10)
        categories = {c["expected_category"] for c in self.cases}
        self.assertGreaterEqual(len(categories), 8)

    def test_all_correct_cases_still_classify_correctly(self) -> None:
        failures = []
        for case in self.cases:
            got = gf.classify_finding_category(case["finding_text"])
            if got != case["expected_category"]:
                failures.append((case["finding_id"], got, case["expected_category"]))
        self.assertEqual(failures, [], f"anti-regression failures: {failures}")


if __name__ == "__main__":
    unittest.main()
