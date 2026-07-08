#!/usr/bin/env python3
"""FIND-1 M1d raw_signal -> findings extractor tests."""

from __future__ import annotations

import json
import os
import unittest

import findings_extractors as extractors
import grm_findings as gf


GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def _load_input(name: str) -> dict:
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as f:
        return json.load(f)


def _raw_signal(name: str) -> dict:
    fx = _load_input(name)
    return gf.raw_signal_from_row(fx["row"], fx["raw"])


class FindingsExtractorsTest(unittest.TestCase):
    def assertValidFindings(self, findings: list[dict]) -> None:
        self.assertTrue(findings)
        for finding in findings:
            self.assertEqual(gf.validate_finding(finding), [])

    def test_fda_483_observations_become_accepted_findings(self) -> None:
        raw_signal = _raw_signal("fda_483_observations")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "FDA")
        self.assertEqual(findings[0]["document_type"], "483")
        self.assertEqual(findings[0]["finding_text"], "There is a failure to thoroughly review any unexplained discrepancy.")
        self.assertEqual(findings[0]["category_code"], "deviation_capa")
        self.assertEqual(findings[0]["evidence_level"], "A")
        self.assertEqual(findings[0]["finding_language"], "EN")
        self.assertEqual(findings[0]["review_status"], "accepted")
        self.assertEqual(findings[0]["confidence"], 0.95)
        self.assertEqual(findings[0]["evidence_url"], "https://www.fda.gov/media/192439/download")

        again = extractors.findings_from_raw_signal(raw_signal)
        self.assertEqual(
            [f["finding_id"] for f in findings],
            [f["finding_id"] for f in again],
        )

    def test_mfds_gmp_deficiency_table_becomes_accepted_findings(self) -> None:
        raw_signal = _raw_signal("gmp_inspection_periodic")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 3)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "MFDS")
        self.assertEqual(findings[0]["document_type"], "gmp-inspection")
        self.assertEqual(findings[0]["finding_text"], "시설장비 기타 [별표1] 2.1호 제품 교차오염 방지 제조시설 운영할 것")
        self.assertEqual(findings[0]["category_code"], "contamination_control")
        self.assertEqual(findings[0]["mfds_refs"], ["[별표1] 2.1호"])
        self.assertEqual(findings[0]["evidence_level"], "A")
        self.assertEqual(findings[0]["review_status"], "accepted")
        self.assertEqual(findings[0]["confidence"], 0.90)
        self.assertEqual(findings[0]["evidence_url"], "https://nedrug.mfds.go.kr/cmn/edms/down/1PyJBfLEtwC")

    def test_mfds_gmp_excerpt_fallback_enters_needs_review_queue(self) -> None:
        raw_signal = _raw_signal("gmp_inspection_biologic")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["category_code"], "aseptic_sterility_assurance")
        self.assertEqual(findings[0]["evidence_level"], "B")
        self.assertEqual(findings[0]["finding_language"], "KO")
        self.assertEqual(findings[0]["review_status"], "needs_review")
        self.assertEqual(findings[0]["confidence"], 0.72)

    def test_warning_letter_excerpt_enters_needs_review_queue(self) -> None:
        raw_signal = _raw_signal("warning_letter_excerpt")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "FDA")
        self.assertEqual(findings[0]["category_code"], "documentation_records")
        self.assertEqual(findings[0]["evidence_level"], "B")
        self.assertEqual(findings[0]["finding_language"], "EN")
        self.assertEqual(findings[0]["review_status"], "needs_review")
        self.assertEqual(findings[0]["confidence"], 0.72)
        self.assertEqual(
            findings[0]["evidence_url"],
            "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/acme-660124",
        )

    def test_who_whopir_excerpt_enters_needs_review_queue(self) -> None:
        raw_signal = _raw_signal("who_inspection_excerpt")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "WHO")
        self.assertEqual(findings[0]["category_code"], "deviation_capa")
        self.assertEqual(findings[0]["evidence_level"], "B")
        self.assertEqual(findings[0]["review_status"], "needs_review")
        self.assertEqual(findings[0]["confidence"], 0.72)

    def test_invalid_raw_signal_or_empty_payload_returns_no_findings(self) -> None:
        self.assertEqual(extractors.findings_from_raw_signal({}), [])

        fx = _load_input("warning_letter_excerpt")
        raw_signal = gf.raw_signal_from_row(fx["row"], {})
        self.assertEqual(extractors.findings_from_raw_signal(raw_signal), [])

    def test_duplicate_finding_texts_are_deduped(self) -> None:
        fx = _load_input("fda_483_observations")
        fx["raw"]["fda_483_observations"].append(dict(fx["raw"]["fda_483_observations"][0]))
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertEqual(len({f["finding_text"] for f in findings}), 2)

    def test_with_report_clean_extraction_has_zero_drops(self) -> None:
        raw_signal = _raw_signal("fda_483_observations")

        findings, report = extractors.findings_from_raw_signal_with_report(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            report,
            {
                "extracted": 2,
                "kept": 2,
                "dropped_invalid": 0,
                "dropped_duplicate_text": 0,
                "invalid_errors": [],
            },
        )

    def test_with_report_counts_invalid_drop_when_evidence_url_is_missing(self) -> None:
        fx = _load_input("warning_letter_excerpt")
        fx["row"] = dict(fx["row"])
        fx["row"].pop("official_url", None)
        fx["row"].pop("source_url", None)
        fx["row"].pop("api_query", None)
        fx["raw"] = dict(fx["raw"])
        fx["raw"].pop("url", None)
        fx["raw"].pop("source_url", None)
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        self.assertEqual(gf.validate_raw_signal(raw_signal), [])

        findings, report = extractors.findings_from_raw_signal_with_report(raw_signal)

        self.assertEqual(findings, [])
        self.assertEqual(report["extracted"], 1)
        self.assertEqual(report["kept"], 0)
        self.assertEqual(report["dropped_invalid"], 1)
        self.assertEqual(report["dropped_duplicate_text"], 0)
        self.assertEqual(report["invalid_errors"], ["findings.evidence_url required"])

        # Unchanged public function still returns an empty list for this case.
        self.assertEqual(extractors.findings_from_raw_signal(raw_signal), [])

    def test_with_report_counts_duplicate_text_drop(self) -> None:
        fx = _load_input("fda_483_observations")
        fx["raw"]["fda_483_observations"].append(dict(fx["raw"]["fda_483_observations"][0]))
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])

        findings, report = extractors.findings_from_raw_signal_with_report(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertEqual(report["extracted"], 3)
        self.assertEqual(report["kept"], 2)
        self.assertEqual(report["dropped_invalid"], 0)
        self.assertEqual(report["dropped_duplicate_text"], 1)
        self.assertEqual(report["invalid_errors"], [])


if __name__ == "__main__":
    unittest.main()
