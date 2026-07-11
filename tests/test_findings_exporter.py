#!/usr/bin/env python3
"""FIND-1 M1b raw_signals dry-run exporter tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import findings_exporter as exporter
import grm_findings as gf


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class FindingsExporterTest(unittest.TestCase):
    def test_sample_notion_snapshots_export_raw_signals(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        result = exporter.export_from_input(data)

        self.assertEqual(result["schema_version"], exporter.EXPORT_SCHEMA_VERSION)
        self.assertEqual(result["raw_signal_schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(result["report"]["input_rows"], 2)
        self.assertEqual(result["report"]["exported"], 2)
        self.assertEqual(result["report"]["skipped"], 0)

        records = result["records"]
        self.assertEqual(records[0]["source"], "FDA 483")
        self.assertEqual(records[0]["document_id"], "fda483-192439")
        self.assertEqual(records[0]["export_source"], "page_id:page-fda-483-192439")
        self.assertEqual(gf.validate_raw_signal(records[0]), [])
        self.assertEqual(records[1]["source"], "MFDS")
        self.assertEqual(records[1]["modality"], "Biologic")
        self.assertEqual(gf.validate_raw_signal(records[1]), [])

        again = exporter.export_from_input(data)
        self.assertEqual(
            [r["raw_signal_id"] for r in records],
            [r["raw_signal_id"] for r in again["records"]],
        )

    def test_missing_raw_is_skipped_without_side_effects(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        data["raw_by_page_id"].pop("page-mfds-gmp-2026-0007")

        result = exporter.export_from_input(data)

        self.assertEqual(result["report"]["input_rows"], 2)
        self.assertEqual(result["report"]["exported"], 1)
        self.assertEqual(result["report"]["skipped"], 1)
        self.assertEqual(result["report"]["skipped_rows"][0]["reason"], "missing_raw")

    def test_duplicate_raw_signal_id_is_skipped(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        data["notion_snapshots"].append(dict(data["notion_snapshots"][0]))

        result = exporter.export_from_input(data)

        self.assertEqual(result["report"]["input_rows"], 3)
        self.assertEqual(result["report"]["exported"], 2)
        self.assertEqual(result["report"]["skipped"], 1)
        self.assertEqual(result["report"]["skipped_rows"][0]["reason"], "duplicate_raw_signal_id")

    def test_invalid_row_and_raw_payload_are_reported(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        data["raw_by_page_id"]["page-mfds-gmp-2026-0007"] = ["not", "an", "object"]
        data["notion_snapshots"].append(["not", "a", "row"])

        result = exporter.export_from_input(data)

        self.assertEqual(result["report"]["input_rows"], 3)
        self.assertEqual(result["report"]["exported"], 1)
        self.assertEqual(result["report"]["skipped"], 2)
        self.assertEqual(
            [r["reason"] for r in result["report"]["skipped_rows"]],
            ["invalid_raw", "invalid_row"],
        )

    def test_cli_writes_dry_run_json(self) -> None:
        fixture = os.path.join(FIXTURES, "findings_m1b_sample_export.json")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "raw_signals_dry_run.json")
            rc = exporter.main(["--input", fixture, "--output", out, "--pretty"])

            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["report"]["exported"], 2)
            self.assertEqual(result["records"][0]["schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)

    def test_include_findings_adds_dry_run_findings_report(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        result = exporter.export_from_input(data, include_findings=True)

        self.assertEqual(result["schema_version"], exporter.FINDINGS_EXPORT_SCHEMA_VERSION)
        self.assertEqual(result["raw_signal_schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(result["finding_schema_version"], gf.FINDING_SCHEMA_VERSION)
        self.assertEqual(result["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual(result["report"]["exported"], 2)
        self.assertEqual(result["report"]["findings_exported"], 2)
        self.assertEqual(result["report"]["raw_signals_without_findings"], [])

        findings = result["findings"]
        self.assertEqual([f["agency"] for f in findings], ["FDA", "MFDS"])
        self.assertEqual({f["taxonomy_version"] for f in findings}, {gf.TAXONOMY_VERSION})
        self.assertEqual([f["review_status"] for f in findings], ["accepted", "needs_review"])
        for finding in findings:
            self.assertEqual(gf.validate_finding(finding), [])

    def test_cli_writes_findings_dry_run_json(self) -> None:
        fixture = os.path.join(FIXTURES, "findings_m1b_sample_export.json")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "findings_dry_run.json")
            rc = exporter.main(["--input", fixture, "--output", out, "--include-findings", "--pretty"])

            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["schema_version"], exporter.FINDINGS_EXPORT_SCHEMA_VERSION)
            self.assertEqual(result["report"]["findings_exported"], 2)
            self.assertEqual(result["findings"][0]["schema_version"], gf.FINDING_SCHEMA_VERSION)

    def test_source_coverage_fixture_reports_findings_distribution(self) -> None:
        data = _load_fixture("findings_m1f_source_coverage_export.json")
        result = exporter.export_from_input(data, include_findings=True)

        report = result["report"]
        self.assertEqual(report["exported"], 5)
        self.assertEqual(report["findings_exported"], 6)
        self.assertEqual(len(report["raw_signals_without_findings"]), 1)
        self.assertEqual(
            report["raw_signals_without_findings"][0]["row_key"],
            "WHO::who-whopir-link-only",
        )

        coverage = report["coverage"]
        self.assertEqual(coverage["raw_signals_total"], 5)
        self.assertEqual(coverage["raw_signals_with_findings"], 4)
        self.assertEqual(coverage["raw_signals_without_findings"], 1)
        self.assertEqual(coverage["findings_total"], 6)
        self.assertEqual(
            coverage["raw_signals_by_source"],
            {"FDA 483": 1, "FDA Warning Letter": 1, "MFDS": 1, "WHO": 2},
        )
        self.assertEqual(
            coverage["findings_by_source"],
            {"FDA 483": 1, "FDA Warning Letter": 1, "MFDS": 3, "WHO": 1},
        )
        self.assertEqual(coverage["findings_by_agency"], {"FDA": 2, "MFDS": 3, "WHO": 1})
        self.assertEqual(coverage["findings_by_review_status"], {"accepted": 4, "needs_review": 2})
        self.assertEqual(coverage["findings_by_evidence_level"], {"A": 4, "B": 2})
        # v3 taxonomy: same 211.100(a)-style "written procedures for production and
        # process controls" fixture text as test_findings_extractors' WL case now
        # classifies as process_validation, not documentation_records (see
        # archive/findings_classification_audit_2026-07-12.md case 3df6f81c).
        self.assertEqual(
            coverage["findings_by_category_code"],
            {
                "contamination_control": 1,
                "deviation_capa": 2,
                "process_validation": 1,
                "material_supplier_control": 1,
                "validation_qualification": 1,
            },
        )

    def test_coverage_reports_extraction_drop_counts_and_details(self) -> None:
        row_no_evidence_url = {
            "page_id": "page-wl-no-url",
            "source": "FDA Warning Letter",
            "document_id": "WL-NOURL-1",
            "date": "2026-05-20",
            "headline": "CGMP violation with no evidence url",
            "firm": "NoUrl Pharma",
            "modality": "Chemical",
            "site_country": "United States",
            "type_or_class": "Center for Drug Evaluation and Research (CDER)",
        }
        raw_no_evidence_url = {
            "firm": "NoUrl Pharma",
            "wl_body_excerpt": "During our inspection we found violations of CGMP documentation and records control.",
        }
        row_dup = {
            "page_id": "page-fda-483-dup",
            "source": "FDA 483",
            "document_id": "fda483-dup-1",
            "date": "2026-05-27",
            "headline": "[FDA 483] Dup Labs, LLC",
            "official_url": "https://www.fda.gov/media/999999/download",
            "type_or_class": "483",
            "firm": "Dup Labs, LLC",
            "modality": "Chemical",
            "site_country": "United States",
        }
        raw_dup = {
            "firm": "Dup Labs, LLC",
            "fda_483_observations": [
                {"number": "1", "deficiency": "Failure to maintain equipment records.", "detail": "detail 1"},
                {"number": "2", "deficiency": "Failure to maintain equipment records.", "detail": "detail 2"},
            ],
        }

        result = exporter.build_raw_signal_export(
            [row_no_evidence_url, row_dup],
            raw_by_page_id={
                "page-wl-no-url": raw_no_evidence_url,
                "page-fda-483-dup": raw_dup,
            },
            include_findings=True,
        )

        report = result["report"]
        self.assertEqual(report["exported"], 2)
        self.assertEqual(report["findings_exported"], 1)
        self.assertEqual(len(report["raw_signals_without_findings"]), 1)
        self.assertEqual(
            report["raw_signals_without_findings"][0]["row_key"],
            "FDA Warning Letter::WL-NOURL-1",
        )

        coverage = report["coverage"]
        self.assertEqual(coverage["extraction_dropped_invalid"], 1)
        self.assertEqual(coverage["extraction_dropped_duplicate_text"], 1)

        details = {d["row_key"]: d for d in report["extraction_drop_details"]}
        self.assertEqual(len(details), 2)
        wl_detail = details["FDA Warning Letter::WL-NOURL-1"]
        self.assertEqual(wl_detail["dropped_invalid"], 1)
        self.assertEqual(wl_detail["dropped_duplicate_text"], 0)
        self.assertEqual(wl_detail["invalid_errors"], ["findings.evidence_url required"])
        dup_detail = details["FDA 483::fda483-dup-1"]
        self.assertEqual(dup_detail["dropped_invalid"], 0)
        self.assertEqual(dup_detail["dropped_duplicate_text"], 1)
        self.assertEqual(dup_detail["invalid_errors"], [])


if __name__ == "__main__":
    unittest.main()
