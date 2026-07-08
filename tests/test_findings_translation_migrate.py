#!/usr/bin/env python3
"""FIND-1 M6a guarded SQLite translation-column migrator tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import findings_store as store
import findings_translation_migrate_sqlite as migrate
import grm_findings as gf


_TRANSLATION_METHOD_CHECK = ", ".join(f"'{m}'" for m in gf.TRANSLATION_METHODS)
_NEW_COLUMNS_BLOCK = (
    "  finding_text_ko TEXT NOT NULL DEFAULT '',\n"
    f"  translation_method TEXT NOT NULL DEFAULT '' CHECK (translation_method IN ({_TRANSLATION_METHOD_CHECK})),\n"
)

_LEGACY_FINDING_COLUMNS = tuple(
    c for c in store.FINDING_SQLITE_COLUMNS if c not in ("finding_text_ko", "translation_method")
)


def _legacy_pre_m6a_ddl() -> str:
    """Reconstruct the pre-M6a DDL (no translation columns) from the current generator.

    grm_findings.sqlite_schema_ddl() now always emits finding_text_ko/translation_method.
    To exercise the migrator against a database that predates M6a, strip that block back
    out of the current DDL.
    """
    ddl = gf.sqlite_schema_ddl()
    assert _NEW_COLUMNS_BLOCK in ddl, "sqlite_schema_ddl() shape changed; update legacy fixture"
    return ddl.replace(_NEW_COLUMNS_BLOCK, "")


def _pair(*, document_id: str, firm: str, finding_text: str) -> tuple[dict, dict]:
    row = {
        "source": "FDA 483",
        "document_id": document_id,
        "date": "2026-06-01",
        "headline": f"[FDA 483] {firm}",
        "firm": firm,
        "type_or_class": "483",
        "site_country": "US",
        "modality": "Drug",
        "source_url": f"https://example.com/{document_id}",
        "official_url": f"https://example.com/official/{document_id}",
    }
    raw = {"firm": firm, "detail": "sample raw payload"}
    raw_signal = gf.raw_signal_from_row(row, raw, collected_at="2026-06-01T00:00:00+00:00")
    finding = gf.finding_from_raw_signal(raw_signal, finding_text=finding_text)
    return raw_signal, finding


def _seed_legacy_db(db_path: str, count: int) -> None:
    """Build a pre-M6a-DDL database (no translation columns) with `count` findings."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_legacy_pre_m6a_ddl())
        for i in range(count):
            raw_signal, finding = _pair(
                document_id=f"doc-{i}",
                firm=f"Firm {i}",
                finding_text=f"Deficiency detail number {i}.",
            )
            raw_row = gf.sqlite_row(raw_signal)
            conn.execute(
                f"INSERT INTO raw_signals ({', '.join(raw_row.keys())}) "
                f"VALUES ({', '.join('?' for _ in raw_row)})",
                tuple(raw_row.values()),
            )
            # finding_from_raw_signal always includes finding_text_ko/translation_method now;
            # restrict to the legacy column set so the INSERT matches the pre-M6a table shape.
            finding_row = gf.sqlite_row({k: v for k, v in finding.items() if k in _LEGACY_FINDING_COLUMNS})
            conn.execute(
                f"INSERT INTO findings ({', '.join(finding_row.keys())}) "
                f"VALUES ({', '.join('?' for _ in finding_row)})",
                tuple(finding_row.values()),
            )
        conn.commit()
    finally:
        conn.close()


def _counts(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "raw_signals": int(conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]),
            "findings": int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]),
        }
    finally:
        conn.close()


def _findings_columns(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [str(row[1]) for row in conn.execute("PRAGMA table_info(findings)").fetchall()]
    finally:
        conn.close()


class LegacyFixtureSanityTest(unittest.TestCase):
    def test_legacy_ddl_has_no_translation_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "legacy.sqlite3")
            _seed_legacy_db(db_path, 1)
            columns = _findings_columns(db_path)
            self.assertNotIn("finding_text_ko", columns)
            self.assertNotIn("translation_method", columns)

    def test_current_schema_already_has_translation_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "current.sqlite3")
            conn = sqlite3.connect(db_path)
            try:
                store.ensure_findings_schema(conn)
                conn.commit()
            finally:
                conn.close()
            columns = _findings_columns(db_path)
            self.assertIn("finding_text_ko", columns)
            self.assertIn("translation_method", columns)


class DryRunTest(unittest.TestCase):
    def test_dry_run_leaves_original_byte_identical_and_reports_verified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 5)
            before_bytes = open(db_path, "rb").read()

            report = migrate.migrate_translation_columns_sqlite(db_path, write_file=False)

            after_bytes = open(db_path, "rb").read()
            self.assertEqual(before_bytes, after_bytes)
            self.assertFalse(os.path.exists(db_path + ".bak-v2"))

            self.assertEqual(report["schema_version"], migrate.TRANSLATION_MIGRATE_SCHEMA_VERSION)
            self.assertEqual(report["mode"], "dry_run")
            self.assertEqual(report["backup_path"], "")
            self.assertFalse(report["committed"])
            self.assertTrue(report["ready"])
            self.assertEqual(report["counts"]["before"], {"raw_signals": 5, "findings": 5})
            self.assertEqual(report["counts"]["after"], {"raw_signals": 5, "findings": 5})

            self.assertEqual(
                sorted(report["columns"]["missing_before"]),
                ["finding_text_ko", "translation_method"],
            )
            self.assertEqual(report["columns"]["already_present_before"], [])

            verification = report["verification"]
            self.assertTrue(verification["verified"])
            self.assertTrue(verification["counts_match"])
            self.assertTrue(verification["columns_present"])

            # PRAGMA table_info reflects the rehearsal's rollback -- original schema untouched.
            self.assertNotIn("finding_text_ko", _findings_columns(db_path))

    def test_dry_run_on_already_migrated_db_reports_no_missing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            conn = sqlite3.connect(db_path)
            try:
                store.ensure_findings_schema(conn)
                conn.commit()
            finally:
                conn.close()

            report = migrate.migrate_translation_columns_sqlite(db_path, write_file=False)

            self.assertEqual(report["columns"]["missing_before"], [])
            self.assertEqual(
                sorted(report["columns"]["already_present_before"]),
                ["finding_text_ko", "translation_method"],
            )
            self.assertTrue(report["verification"]["verified"])
            self.assertTrue(report["ready"])

    def test_dry_run_on_empty_db_reports_zero_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 0)

            report = migrate.migrate_translation_columns_sqlite(db_path, write_file=False)

            self.assertEqual(report["counts"]["before"], {"raw_signals": 0, "findings": 0})
            self.assertEqual(report["counts"]["after"], {"raw_signals": 0, "findings": 0})
            self.assertTrue(report["verification"]["verified"])

    def test_missing_db_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "missing.sqlite3")
            with self.assertRaises(ValueError):
                migrate.migrate_translation_columns_sqlite(missing, write_file=False)


class WriteFileTest(unittest.TestCase):
    def test_write_file_backs_up_adds_columns_and_preserves_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 4)
            before_bytes = open(db_path, "rb").read()
            backup_path = db_path + ".bak-v2"

            report = migrate.migrate_translation_columns_sqlite(db_path, write_file=True)

            self.assertEqual(report["mode"], "file_write")
            self.assertTrue(report["committed"])
            self.assertEqual(report["backup_path"], backup_path)
            self.assertTrue(report["verification"]["verified"])

            # Backup preserves the exact pre-migration database.
            self.assertTrue(os.path.exists(backup_path))
            self.assertEqual(open(backup_path, "rb").read(), before_bytes)

            # Original path now has both new columns.
            columns = _findings_columns(db_path)
            self.assertIn("finding_text_ko", columns)
            self.assertIn("translation_method", columns)

            # Counts preserved; existing rows default both new columns to ''.
            self.assertEqual(_counts(db_path), {"raw_signals": 4, "findings": 4})
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT finding_text_ko, translation_method FROM findings"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [("", "")] * 4)

    def test_write_file_can_update_a_row_to_a_valid_translation_pair(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 1)
            migrate.migrate_translation_columns_sqlite(db_path, write_file=True)

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE findings SET finding_text_ko = ?, translation_method = ?",
                    ("국문 해석", "llm_assisted"),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT finding_text_ko, translation_method FROM findings"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("국문 해석", "llm_assisted"))

    def test_write_file_check_constraint_rejects_bad_method(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 1)
            migrate.migrate_translation_columns_sqlite(db_path, write_file=True)

            conn = sqlite3.connect(db_path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "UPDATE findings SET translation_method = ?", ("auto",)
                    )
            finally:
                conn.close()

    def test_write_file_refuses_when_backup_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 2)
            backup_path = db_path + ".bak-v2"
            with open(backup_path, "wb") as f:
                f.write(b"pre-existing backup, must not be overwritten")
            before_db_bytes = open(db_path, "rb").read()

            with self.assertRaisesRegex(ValueError, "backup already exists"):
                migrate.migrate_translation_columns_sqlite(db_path, write_file=True)

            self.assertEqual(open(db_path, "rb").read(), before_db_bytes)
            with open(backup_path, "rb") as f:
                self.assertEqual(f.read(), b"pre-existing backup, must not be overwritten")

    def test_second_write_file_call_refuses_due_to_backup_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 3)

            first = migrate.migrate_translation_columns_sqlite(db_path, write_file=True)
            self.assertTrue(first["committed"])

            # Second run: db already has both columns, but a second write-file attempt
            # must still refuse because .bak-v2 from the first run exists.
            with self.assertRaisesRegex(ValueError, "backup already exists"):
                migrate.migrate_translation_columns_sqlite(db_path, write_file=True)

    def test_write_file_on_already_migrated_db_is_a_noop_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            conn = sqlite3.connect(db_path)
            try:
                store.ensure_findings_schema(conn)
                conn.commit()
            finally:
                conn.close()

            report = migrate.migrate_translation_columns_sqlite(db_path, write_file=True)

            self.assertEqual(report["mode"], "file_write")
            self.assertFalse(report["committed"])
            self.assertEqual(report["backup_path"], "")
            self.assertTrue(report["ready"])
            self.assertFalse(os.path.exists(db_path + ".bak-v2"))


class CliTest(unittest.TestCase):
    def test_cli_dry_run_writes_report_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 2)
            out = os.path.join(td, "report.json")

            rc = migrate.main(["--db-path", db_path, "--output", out, "--pretty"])

            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["mode"], "dry_run")
            self.assertTrue(result["verification"]["verified"])

    def test_cli_missing_db_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "missing.sqlite3")

            rc = migrate.main(["--db-path", missing])

            self.assertEqual(rc, 2)

    def test_cli_write_file_then_rerun_backup_guard_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_db(db_path, 2)

            rc_first = migrate.main(["--db-path", db_path, "--write-file"])
            self.assertEqual(rc_first, 0)

            rc_second = migrate.main(["--db-path", db_path, "--write-file"])
            self.assertEqual(rc_second, 2)


if __name__ == "__main__":
    unittest.main()
