#!/usr/bin/env python3
"""grm-finding-taxonomy/v4 tests.

Three concerns, kept in separate test classes (mirrors tests/test_findings_taxonomy_v3.py's
structure):

1. Mechanism: the new v4 keywords/patterns behave as documented -- OCR-tolerance
   (candidate 1, scoped to the 2 confirmed confusion pairs: quality/l-J-1 and
   sterile/sterih), catch-all CFR vocabulary gaps (candidate 2: annual product
   review, reserve sample, smoke study), and material/CPV wording (candidate 3:
   drug substance sampling, manufacturing-process word-order CPV) -- without
   breaking normal (uncorrupted) spellings.

2. Fixture regression: the 2026-07-12 v3 사후 재감사(post-v3 audit,
   archive/findings_classification_audit_v3_2026-07-12.md) found the classifier's
   real-world accuracy had risen to 89% (v2's 71%), with 9 "wrong" + 2
   "unclassifiable" samples remaining out of the same 100-item stratified
   protocol. tests/fixtures/taxonomy_v4_audit_wrong9.json freezes all 11 of
   those samples' real finding_text (fetched read-only from the live Supabase
   `findings_translation_rows` RPC) plus the v3-wrong category and the audit's
   expected v4 category. Despite the filename (which names the report's "wrong
   9" section), the fixture also carries the report's 2 separately-listed
   "unclassifiable" cases (a9d5baca, e2e676d2) per this task's explicit
   instruction, for 11 total entries.

   Genuinely unreachable cases (OCR corruption beyond the 2 authorized pairs,
   or extraction damage the audit itself called unclassifiable) are marked
   `known_limitation: true` with a reason -- honesty over forcing a match, per
   this taxonomy revision's own conservative-scope principle.

   tests/fixtures/taxonomy_v4_regression_correct.json is the anti-regression
   companion: 14 real finding_text samples verified correctly classified --
   2 reused directly from taxonomy_v3_regression_correct.json specifically
   because they exercise the exact strings the v4 OCR patterns must not
   mis-fire on (plain "sterile"/"aseptic", plain "quality control unit"), plus
   12 fresh samples (the v1-audit-wrong-then-v3-fixed cases from
   archive/findings_classification_audit_v3_2026-07-12.md §2's "1차 wrong 25건
   중 재등장 7건" table) covering every category the v4 changes touch.
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
    """v4 mechanism: new keywords/patterns match as documented; normal spellings
    are not broken by the new OCR-tolerance rules (candidate 1's core promise)."""

    # -- candidate 1: OCR tolerance, scoped to 2 confirmed confusion pairs --

    def test_quajity_ocr_variant_matches_quality_unit_oversight(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "The responsibilities and procedures applicable to the quaJity unit "
                "are not in writing and fully followed."
            ),
            "quality_unit_oversight",
        )

    def test_qua1ity_digit_one_ocr_variant_also_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("The qua1ity unit failed to review production data."),
            "quality_unit_oversight",
        )

    def test_normal_quality_unit_spelling_still_matches(self) -> None:
        """Anti-regression: the new character-class pattern must not change the
        outcome for the ordinary, uncorrupted spelling."""
        self.assertEqual(
            gf.classify_finding_category(
                "The responsibilities and procedures applicable to the quality unit "
                "are not in writing and fully followed."
            ),
            "quality_unit_oversight",
        )

    def test_qualities_plural_does_not_falsely_match_quality_unit_pattern(self) -> None:
        """The char-class pattern requires a literal 'ty' after the [lJ1i]+ run --
        'qualities' (q-u-a-l-i-t-i-e-s) must not satisfy it."""
        self.assertNotEqual(
            gf.classify_finding_category("Product qualities unit testing was skipped."),
            "quality_unit_oversight",
        )

    def test_sterih_ocr_variant_matches_aseptic(self) -> None:
        """Real audit text: the space before 'sterile' is also dropped
        ('of sterile' -> 'ofsterih'), so there is no leading word boundary --
        the v4 pattern intentionally has no leading \\b for this alternative."""
        self.assertEqual(
            gf.classify_finding_category(
                "Your program for the visual inspection ofsterih drug products does "
                "not provide adequate assurance."
            ),
            "aseptic_sterility_assurance",
        )

    def test_normal_sterile_spelling_still_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("The product must remain sterile at all times."),
            "aseptic_sterility_assurance",
        )

    def test_normal_aseptic_and_sterilization_spelling_still_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Procedures designed to prevent microbiological contamination of drug "
                "products purporting to be sterile did not include adequate validation "
                "of the aseptic and sterilization processes."
            ),
            "aseptic_sterility_assurance",
        )

    def test_negative_lookbehind_still_blocks_non_sterile(self) -> None:
        self.assertNotEqual(
            gf.classify_finding_category("Use of non-sterile gloves was observed."),
            "aseptic_sterility_assurance",
        )

    def test_sterih_does_not_match_unrelated_words_containing_similar_letters(self) -> None:
        """Conservative-scope guard: 'sterih' is a literal alternative, not a fuzzy
        class -- ordinary unrelated text must not match it just because it shares a
        few letters with "steril-" words."""
        self.assertNotEqual(
            gf.classify_finding_category("The stern instructions were not followed by the technician."),
            "aseptic_sterility_assurance",
        )

    # -- candidate 2: catch-all CFR vocabulary gaps --

    def test_annual_product_review_pattern_matches_quality_unit_oversight(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Written procedures are not followed for evaluations conducted at "
                "least annually to review records associated with a representative "
                "number of batches, whether approved or rejected."
            ),
            "quality_unit_oversight",
        )

    def test_annual_product_review_keyword_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("The annual product review was not completed on time."),
            "quality_unit_oversight",
        )

    def test_reserve_sample_pattern_matches_qc_lab_controls(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Reserve samples from representative sample lots or batches of drug "
                "products are not examined visually at least once a year for evidence "
                "of deterioration."
            ),
            "qc_lab_controls",
        )

    def test_evidence_of_deterioration_keyword_matches_qc_lab_controls(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "There was no examination for evidence of deterioration in the retained samples."
            ),
            "qc_lab_controls",
        )

    def test_smoke_study_pattern_matches_aseptic(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("Smoke studies were inadequately performed under dynamic conditions."),
            "aseptic_sterility_assurance",
        )

    def test_smoke_studies_plural_variant_matches(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("The smoke study was not performed for the filling line."),
            "aseptic_sterility_assurance",
        )

    # -- candidate 3: material vocabulary + CPV word-order --

    def test_sampling_of_drug_substance_pattern_matches_material_supplier_control(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Written procedures are not followed for the sampling of drug substance and excipients."
            ),
            "material_supplier_control",
        )

    def test_bare_excipient_keyword_matches_material_supplier_control(self) -> None:
        self.assertEqual(
            gf.classify_finding_category("The excipient release testing was not documented."),
            "material_supplier_control",
        )

    def test_bare_drug_substance_alone_does_not_match_material_supplier_control(self) -> None:
        """Deliberate scope decision (see grm_findings.py's material_supplier_control
        comment): a bare "drug substance" keyword was considered per the audit's
        candidate 3 but rejected after real-data verification showed it over-matches
        unrelated text (audit's own known_limitation case e2e676d2, a shelf-life
        finding that merely mentions "drug substance (DS)" in passing). Only the
        "sampling of drug substance" pattern was added."""
        self.assertNotEqual(
            gf.classify_finding_category(
                "The information provided by the firm does not conform to the "
                "specifically, the drug substance (DS) shelf life is at some "
                "temperature that was not justified."
            ),
            "material_supplier_control",
        )

    def test_manufacturing_process_cpv_word_order_pattern_matches_process_validation(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(
                "Control procedures are not established which monitor the output and "
                "validate the performance of those manufacturing processes that may "
                "be responsible for causing variability in the characteristics of "
                "in-process material and the drug product."
            ),
            "process_validation",
        )

    def test_cpv_pattern_does_not_match_when_signal_word_too_far_away(self) -> None:
        """The CPV pattern caps the gap at 60 chars -- a "manufacturing process"
        mention more than 60 chars away from any of variability/monitor/output/
        validate must not spuriously trigger process_validation (the signal word
        "validate" is present here, 172 chars after "manufacturing process", to
        actually exercise the cap rather than merely omitting the signal word)."""
        text = (
            "The manufacturing process was documented in a report that nobody on "
            "the quality team bothered to read carefully during the entire fiscal "
            "quarter due to a staffing shortage before anyone thought to validate "
            "the final numbers."
        )
        self.assertNotEqual(gf.classify_finding_category(text), "process_validation")


class TaxonomyV4BoundedTest(unittest.TestCase):
    def test_taxonomy_v4_is_current_and_v1v2v3_still_valid(self) -> None:
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

    def test_v4_introduces_no_new_category_and_no_reorder(self) -> None:
        """v4 is purely additive keywords/patterns within existing categories --
        no category added/removed/relabeled, and the v3 match order (codes and
        their relative sequence) is unchanged."""
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


class AuditWrong9FixtureTest(unittest.TestCase):
    """Regression fixture from the 2026-07-12 post-v3 audit's 9 wrong + 2
    unclassifiable samples (archive/findings_classification_audit_v3_2026-07-12.md
    §3/§4)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = _load_fixture("taxonomy_v4_audit_wrong9.json")

    def test_fixture_has_all_11_cases(self) -> None:
        self.assertEqual(len(self.cases), 11)
        for case in self.cases:
            for key in (
                "finding_id", "finding_text", "v3_wrong_category",
                "expected_v4_category", "known_limitation",
            ):
                self.assertIn(key, case)

    def test_fixable_cases_now_return_expected_category(self) -> None:
        fixable = [c for c in self.cases if not c["known_limitation"]]
        self.assertEqual(len(fixable), 6, "expected fix count changed -- update fixture/PR notes")
        failures = []
        for case in fixable:
            got = gf.classify_finding_category(case["finding_text"])
            if got != case["expected_v4_category"]:
                failures.append((case["finding_id"], got, case["expected_v4_category"]))
        self.assertEqual(failures, [], f"regressions among fixable audit cases: {failures}")

    def test_known_limitation_cases_have_reasons_and_pinned_actual_output(self) -> None:
        """5 cases remain known_limitation after v4 (07dc5ab1, 5ab99207, 8d3ae393,
        a9d5baca, e2e676d2) -- pin their actual output so a future change that
        silently shifts them gets caught, same discipline as the v3 fixture."""
        pinned = {
            "finding-07dc5ab1f92a2e8e426904e7": "equipment_facility",
            "finding-5ab992077bc68a25acea5edd": "other_quality_system",
            "finding-8d3ae3935966ee9ccd9227ae": "other_quality_system",
            "finding-a9d5baca5875ea9f2bde6682": "other_quality_system",
            "finding-e2e676d2434e55fc658ca07f": "other_quality_system",
        }
        known_limitation_ids = {c["finding_id"] for c in self.cases if c["known_limitation"]}
        self.assertEqual(known_limitation_ids, set(pinned.keys()))

        for case in self.cases:
            if not case["known_limitation"]:
                continue
            got = gf.classify_finding_category(case["finding_text"])
            self.assertEqual(
                got, pinned[case["finding_id"]],
                f"known_limitation case {case['finding_id']} output drifted",
            )
            self.assertTrue(case["known_limitation_reason"].strip())


class RegressionCorrectFixtureTest(unittest.TestCase):
    """Anti-regression fixture: 14 real samples verified correctly classified,
    including 2 reused directly from the v3 fixture specifically to stress-test
    the v4 OCR-tolerance patterns against normal (uncorrupted) spellings."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = _load_fixture("taxonomy_v4_regression_correct.json")

    def test_fixture_has_at_least_twelve_cases_covering_distinct_categories(self) -> None:
        self.assertGreaterEqual(len(self.cases), 12)
        categories = {c["expected_category"] for c in self.cases}
        self.assertGreaterEqual(len(categories), 6)

    def test_all_correct_cases_still_classify_correctly(self) -> None:
        failures = []
        for case in self.cases:
            got = gf.classify_finding_category(case["finding_text"])
            if got != case["expected_category"]:
                failures.append((case["finding_id"], got, case["expected_category"]))
        self.assertEqual(failures, [], f"anti-regression failures: {failures}")


if __name__ == "__main__":
    unittest.main()
