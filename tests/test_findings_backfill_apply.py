#!/usr/bin/env python3
"""FIND-1 M1j guarded SQLite file write tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import findings_backfill
import findings_backfill_apply as apply_sqlite


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _plan() -> dict:
    manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
    return findings_backfill.build_internal_backfill_dry_run(findings_backfill.load_manifest(manifest))


def _counts(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "raw_signals": conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0],
            "findings": conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0],
        }
    finally:
        conn.close()


class FindingsBackfillApplyTest(unittest.TestCase):
    def test_apply_requires_explicit_write_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")

            with self.assertRaisesRegex(ValueError, "write_file=True"):
                apply_sqlite.apply_backfill_plan_to_sqlite(_plan(), db)

            self.assertFalse(os.path.exists(db))

    def test_apply_writes_plan_to_sqlite_file_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")

            result = apply_sqlite.apply_backfill_plan_to_sqlite(_plan(), db, write_file=True)

            self.assertEqual(result["schema_version"], apply_sqlite.SQLITE_BACKFILL_APPLY_SCHEMA_VERSION)
            self.assertEqual(result["mode"], "sqlite_file_write")
            self.assertTrue(result["write_guard"]["explicit_write_file"])
            self.assertFalse(result["write_guard"]["database_existed_before"])
            self.assertTrue(result["write_guard"]["committed"])

            report = result["report"]
            self.assertEqual(report["records_input"], 6)
            self.assertEqual(report["findings_input"], 7)
            self.assertEqual(report["blocking_errors"], 0)
            self.assertTrue(report["ready_for_search_export"])
            self.assertEqual(report["preflight"]["m1i_transaction_dry_run"], "passed")
            self.assertEqual(report["preflight"]["notion_api"], "not_used")
            self.assertEqual(report["preflight"]["sqlite_file_write"], "used_explicit_guard")
            self.assertEqual(report["preflight"]["supabase_write"], "not_used")
            self.assertEqual(report["preflight"]["status_handoff"], "not_used")

            self.assertEqual(report["apply_pass"]["raw_signals_inserted"], 6)
            self.assertEqual(report["apply_pass"]["findings_inserted"], 7)
            self.assertEqual(report["apply_pass"]["result_statuses"], {"inserted": 5, "raw_signal_inserted": 1})
            self.assertEqual(report["sqlite_counts"]["before"], {"raw_signals": 0, "findings": 0})
            self.assertEqual(report["sqlite_counts"]["after_commit"], {"raw_signals": 6, "findings": 7})
            self.assertEqual(_counts(db), {"raw_signals": 6, "findings": 7})

    def test_apply_is_idempotent_against_existing_sqlite_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            apply_sqlite.apply_backfill_plan_to_sqlite(_plan(), db, write_file=True)

            second = apply_sqlite.apply_backfill_plan_to_sqlite(_plan(), db, write_file=True)

            self.assertTrue(second["write_guard"]["database_existed_before"])
            self.assertTrue(second["write_guard"]["committed"])
            report = second["report"]
            self.assertEqual(report["sqlite_counts"]["before"], {"raw_signals": 6, "findings": 7})
            self.assertEqual(report["sqlite_counts"]["after_commit"], {"raw_signals": 6, "findings": 7})
            self.assertEqual(report["apply_pass"]["raw_signals_duplicate"], 6)
            self.assertEqual(report["apply_pass"]["findings_duplicate"], 7)
            self.assertEqual(report["apply_pass"]["findings_inserted"], 0)
            self.assertEqual(report["apply_pass"]["result_statuses"], {"duplicate": 6})

    def test_orphan_finding_blocks_before_sqlite_file_creation(self) -> None:
        plan = _plan()
        orphan = dict(plan["findings"][0])
        orphan["finding_id"] = "finding-orphan"
        orphan["raw_signal_id"] = "rawsig-missing"
        plan["findings"].append(orphan)

        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")

            with self.assertRaisesRegex(ValueError, "not ready"):
                apply_sqlite.apply_backfill_plan_to_sqlite(plan, db, write_file=True)

            self.assertFalse(os.path.exists(db))

    def test_cli_applies_manifest_and_writes_report(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            out = os.path.join(td, "findings_sqlite_backfill_apply.json")

            rc = apply_sqlite.main([
                "--manifest",
                manifest,
                "--db-path",
                db,
                "--write-file",
                "--output",
                out,
                "--pretty",
            ])

            self.assertEqual(rc, 0)
            self.assertEqual(_counts(db), {"raw_signals": 6, "findings": 7})
            with open(out, encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual(report["schema_version"], apply_sqlite.SQLITE_BACKFILL_APPLY_SCHEMA_VERSION)
            self.assertEqual(report["report"]["sqlite_counts"]["after_commit"], {"raw_signals": 6, "findings": 7})

    def test_cli_rejects_missing_write_guard(self) -> None:
        manifest = os.path.join(FIXTURES, "findings_m1h_backfill_manifest.json")
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")

            rc = apply_sqlite.main(["--manifest", manifest, "--db-path", db])

            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(db))


if __name__ == "__main__":
    unittest.main()
