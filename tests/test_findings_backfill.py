#!/usr/bin/env python3
"""FIND-1 M1h internal backfill dry-run planner tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import findings_backfill as backfill
import grm_findings as gf


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class FindingsBackfillTest(unittest.TestCase):
    def test_internal_backfill_dry_run_merges_batches_with_deduped_report(self) -> None:
        result = backfill.build_internal_backfill_dry_run([
            ("m1b-seed", _load_fixture("findings_m1b_sample_export.json")),
            ("m1f-source-coverage", _load_fixture("findings_m1f_source_coverage_export.json")),
        ])

        self.assertEqual(result["schema_version"], backfill.BACKFILL_DRY_RUN_SCHEMA_VERSION)
        self.assertEqual(result["raw_signal_schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(result["finding_schema_version"], gf.FINDING_SCHEMA_VERSION)
        self.assertEqual(result["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual([b["name"] for b in result["batches"]], ["m1b-seed", "m1f-source-coverage"])

        report = result["report"]
        self.assertEqual(report["input_batches"], 2)
        self.assertEqual(report["input_rows"], 7)
        self.assertEqual(report["raw_signals_exported"], 7)
        self.assertEqual(report["raw_signals_unique"], 6)
        self.assertEqual(report["raw_signal_duplicates"], 1)
        self.assertEqual(report["findings_exported"], 8)
        self.assertEqual(report["findings_unique"], 7)
        self.assertEqual(report["finding_duplicates"], 1)
        self.assertEqual(report["skipped_rows"], 0)
        self.assertEqual(report["preflight"]["blocking_errors"], 0)
        self.assertTrue(report["preflight"]["ready_for_sqlite_append_dry_run"])

        self.assertEqual(len(report["raw_signals_without_findings"]), 1)
        self.assertEqual(report["raw_signals_without_findings"][0]["row_key"], "WHO::who-whopir-link-only")
        self.assertEqual(result["duplicates"]["raw_signals"][0]["first_batch"], "m1b-seed")
        self.assertEqual(result["duplicates"]["raw_signals"][0]["batch"], "m1f-source-coverage")

        coverage = report["coverage"]
        self.assertEqual(coverage["raw_signals_total"], 6)
        self.assertEqual(coverage["raw_signals_with_findings"], 5)
        self.assertEqual(coverage["raw_signals_without_findings"], 1)
        self.assertEqual(coverage["findings_total"], 7)
        self.assertEqual(
            coverage["raw_signals_by_source"],
            {"FDA 483": 1, "FDA Warning Letter": 1, "MFDS": 2, "WHO": 2},
        )
        self.assertEqual(
            coverage["findings_by_review_status"],
            {"accepted": 4, "needs_review": 3},
        )
        for record in result["records"]:
            self.assertEqual(gf.validate_raw_signal(record), [])
        for finding in result["findings"]:
            self.assertEqual(gf.validate_finding(finding), [])

    def test_manifest_paths_are_relative_to_manifest_file(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        inputs = backfill.load_manifest(manifest)

        self.assertEqual([name for name, _ in inputs], ["m1b-seed", "m1f-source-coverage"])
        result = backfill.build_internal_backfill_dry_run(inputs)
        self.assertEqual(result["report"]["raw_signals_unique"], 6)

    def test_invalid_batch_rows_are_blocking_errors(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        data["raw_by_page_id"].pop("page-fda-483-192439")

        result = backfill.build_internal_backfill_dry_run([("broken", data)])

        self.assertEqual(result["report"]["skipped_rows"], 1)
        self.assertEqual(result["skipped_rows"][0]["batch"], "broken")
        self.assertEqual(result["skipped_rows"][0]["reason"], "missing_raw")
        self.assertEqual(result["report"]["preflight"]["blocking_errors"], 1)
        self.assertFalse(result["report"]["preflight"]["ready_for_sqlite_append_dry_run"])

    def test_cli_writes_dry_run_plan_from_manifest(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "internal_backfill_dry_run.json")
            rc = backfill.main(["--manifest", manifest, "--output", out, "--pretty"])

            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["schema_version"], backfill.BACKFILL_DRY_RUN_SCHEMA_VERSION)
            self.assertEqual(result["report"]["findings_unique"], 7)
            self.assertEqual(result["report"]["preflight"]["sqlite_write"], "not_used")

    def test_cli_rejects_manifest_and_input_together(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        fixture = os.path.join(FIXTURES, "findings_m1b_sample_export.json")

        rc = backfill.main(["--manifest", manifest, "--input", fixture])

        self.assertEqual(rc, 2)

    def test_cli_exits_3_when_preflight_has_blocking_errors(self) -> None:
        data = _load_fixture("findings_m1b_sample_export.json")
        data["raw_by_page_id"].pop("page-fda-483-192439")

        with tempfile.TemporaryDirectory() as td:
            broken_input = os.path.join(td, "broken_input.json")
            with open(broken_input, "w", encoding="utf-8") as f:
                json.dump(data, f)
            out = os.path.join(td, "internal_backfill_dry_run.json")

            rc = backfill.main(["--input", broken_input, "--output", out, "--pretty"])

            self.assertEqual(rc, 3)
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertGreater(result["report"]["preflight"]["blocking_errors"], 0)

    def test_cli_still_exits_0_when_preflight_is_clean(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")

        rc = backfill.main(["--manifest", manifest])

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
