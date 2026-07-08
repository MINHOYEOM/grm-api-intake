#!/usr/bin/env python3
"""FIND-1 M1i SQLite transaction dry-run tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import findings_backfill
import findings_backfill_sqlite as sqlite_dry_run


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _plan() -> dict:
    manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
    return findings_backfill.build_internal_backfill_dry_run(findings_backfill.load_manifest(manifest))


class FindingsBackfillSqliteTest(unittest.TestCase):
    def test_sqlite_transaction_dry_run_inserts_replays_and_rolls_back(self) -> None:
        result = sqlite_dry_run.sqlite_transaction_dry_run(_plan())

        self.assertEqual(result["schema_version"], sqlite_dry_run.SQLITE_BACKFILL_DRY_RUN_SCHEMA_VERSION)
        self.assertEqual(result["transaction"]["database"], ":memory:")
        self.assertFalse(result["transaction"]["committed"])
        self.assertTrue(result["transaction"]["rollback_verified"])

        report = result["report"]
        self.assertEqual(report["records_input"], 6)
        self.assertEqual(report["findings_input"], 7)
        self.assertEqual(report["blocking_errors"], 0)
        self.assertTrue(report["ready_for_commit_review"])
        self.assertEqual(report["preflight"]["notion_api"], "not_used")
        self.assertEqual(report["preflight"]["sqlite_file_write"], "not_used")
        self.assertEqual(report["preflight"]["supabase_write"], "not_used")

        self.assertEqual(report["first_pass"]["raw_signals_inserted"], 6)
        self.assertEqual(report["first_pass"]["findings_inserted"], 7)
        self.assertEqual(report["first_pass"]["findings_invalid"], 0)
        self.assertEqual(report["first_pass"]["result_statuses"], {"inserted": 5, "raw_signal_inserted": 1})

        self.assertEqual(report["replay_pass"]["raw_signals_duplicate"], 6)
        self.assertEqual(report["replay_pass"]["findings_duplicate"], 7)
        self.assertEqual(report["replay_pass"]["findings_inserted"], 0)
        self.assertEqual(report["replay_pass"]["result_statuses"], {"duplicate": 6})

        self.assertEqual(report["sqlite_counts"]["after_first_pass"], {"raw_signals": 6, "findings": 7})
        self.assertEqual(report["sqlite_counts"]["after_replay_pass"], {"raw_signals": 6, "findings": 7})
        self.assertEqual(report["sqlite_counts"]["after_rollback"], {"raw_signals": 0, "findings": 0})

    def test_exporter_metadata_fields_do_not_break_sqlite_insert(self) -> None:
        plan = _plan()
        self.assertTrue(any("export_source" in record for record in plan["records"]))

        result = sqlite_dry_run.sqlite_transaction_dry_run(plan)

        self.assertEqual(result["report"]["blocking_errors"], 0)
        self.assertEqual(result["report"]["sqlite_counts"]["after_first_pass"], {"raw_signals": 6, "findings": 7})

    def test_orphan_finding_blocks_commit_review(self) -> None:
        plan = _plan()
        orphan = dict(plan["findings"][0])
        orphan["finding_id"] = "finding-orphan"
        orphan["raw_signal_id"] = "rawsig-missing"
        plan["findings"].append(orphan)

        result = sqlite_dry_run.sqlite_transaction_dry_run(plan)

        self.assertEqual(len(result["report"]["orphan_findings"]), 1)
        self.assertEqual(result["report"]["blocking_errors"], 1)
        self.assertFalse(result["report"]["ready_for_commit_review"])

    def test_cli_builds_plan_from_manifest_and_writes_report(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "findings_sqlite_backfill_dry_run.json")
            rc = sqlite_dry_run.main(["--manifest", manifest, "--output", out, "--pretty"])

            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["schema_version"], sqlite_dry_run.SQLITE_BACKFILL_DRY_RUN_SCHEMA_VERSION)
            self.assertEqual(result["report"]["sqlite_counts"]["after_rollback"], {"raw_signals": 0, "findings": 0})

    def test_cli_rejects_ambiguous_sources(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        fixture = os.path.join(FIXTURES, "findings_m1b_sample_export.json")

        rc = sqlite_dry_run.main(["--manifest", manifest, "--input", fixture])

        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
