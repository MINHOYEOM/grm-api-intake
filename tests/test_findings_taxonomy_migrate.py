#!/usr/bin/env python3
"""FIND-1 M5b guarded SQLite taxonomy v1 -> v2 DDL migrator tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import findings_store as store
import findings_taxonomy_migrate_sqlite as migrate
import grm_findings as gf


_V1_ONLY_CHECK = f"CHECK (taxonomy_version = '{gf.TAXONOMY_VERSIONS[0]}')"
_V1V2_IN_LIST_CHECK = (
    "CHECK (taxonomy_version IN ("
    + ", ".join(f"'{version}'" for version in gf.TAXONOMY_VERSIONS)
    + "))"
)


def _legacy_v1_ddl() -> str:
    """Reconstruct the pre-M5a v1-only-equality DDL from the current v2 IN-list generator.

    grm_findings.sqlite_schema_ddl() now emits the v1+v2 IN-list CHECK. To exercise the
    migrator against a database that predates M5a, substitute that IN-list back to the
    original v1-only equality CHECK it replaced.
    """
    ddl = gf.sqlite_schema_ddl()
    assert _V1V2_IN_LIST_CHECK in ddl, "sqlite_schema_ddl() shape changed; update legacy fixture"
    return ddl.replace(_V1V2_IN_LIST_CHECK, _V1_ONLY_CHECK)


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


def _seed_legacy_v1_db(db_path: str, count: int) -> None:
    """Build a v1-DDL database (v1-only equality CHECK) and seed it with v1-tagged findings."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_legacy_v1_ddl())
        for i in range(count):
            raw_signal, finding = _pair(
                document_id=f"doc-{i}",
                firm=f"Firm {i}",
                finding_text=f"Deficiency detail number {i}.",
            )
            # finding_from_raw_signal always tags gf.TAXONOMY_VERSION (v2); force v1 here
            # so the row matches the legacy v1-only equality CHECK on this database.
            finding["taxonomy_version"] = gf.TAXONOMY_VERSIONS[0]
            raw_row = gf.sqlite_row(raw_signal)
            conn.execute(
                f"INSERT INTO raw_signals ({', '.join(raw_row.keys())}) "
                f"VALUES ({', '.join('?' for _ in raw_row)})",
                tuple(raw_row.values()),
            )
            finding_row = gf.sqlite_row(finding)
            conn.execute(
                f"INSERT INTO findings ({', '.join(finding_row.keys())}) "
                f"VALUES ({', '.join('?' for _ in finding_row)})",
                tuple(finding_row.values()),
            )
        conn.commit()
    finally:
        conn.close()


def _findings_taxonomy_versions(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT taxonomy_version FROM findings").fetchall()
        return sorted(str(row[0]) for row in rows)
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


def _findings_table_ddl(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'findings'"
        ).fetchone()
        return str(row[0]) if row and row[0] else ""
    finally:
        conn.close()


class LegacyFixtureSanityTest(unittest.TestCase):
    def test_legacy_ddl_rejects_v2_and_accepts_v1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "legacy.sqlite3")
            _seed_legacy_v1_db(db_path, 1)
            self.assertEqual(_findings_taxonomy_versions(db_path), [gf.TAXONOMY_VERSIONS[0]])

            conn = sqlite3.connect(db_path)
            try:
                raw_signal, finding = _pair(document_id="doc-v2", firm="Firm V2", finding_text="x")
                raw_row = gf.sqlite_row(raw_signal)
                conn.execute(
                    f"INSERT INTO raw_signals ({', '.join(raw_row.keys())}) "
                    f"VALUES ({', '.join('?' for _ in raw_row)})",
                    tuple(raw_row.values()),
                )
                finding_row = gf.sqlite_row(finding)  # still tagged v2 by finding_from_raw_signal
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        f"INSERT INTO findings ({', '.join(finding_row.keys())}) "
                        f"VALUES ({', '.join('?' for _ in finding_row)})",
                        tuple(finding_row.values()),
                    )
            finally:
                conn.close()


class DryRunTest(unittest.TestCase):
    def test_dry_run_leaves_original_byte_identical_and_reports_verified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_v1_db(db_path, 5)
            before_bytes = open(db_path, "rb").read()

            report = migrate.migrate_taxonomy_sqlite(db_path, write_file=False)

            after_bytes = open(db_path, "rb").read()
            self.assertEqual(before_bytes, after_bytes)
            self.assertFalse(os.path.exists(db_path + ".bak-v1"))

            self.assertEqual(report["schema_version"], migrate.TAXONOMY_MIGRATE_SCHEMA_VERSION)
            self.assertEqual(report["mode"], "dry_run")
            self.assertEqual(report["backup_path"], "")
            self.assertFalse(report["committed"])
            self.assertTrue(report["ready"])
            self.assertEqual(report["counts"]["before"], {"raw_signals": 5, "findings": 5})
            self.assertEqual(report["counts"]["after"], {"raw_signals": 5, "findings": 5})

            verification = report["verification"]
            self.assertTrue(verification["verified"])
            self.assertTrue(verification["counts_match"])
            self.assertTrue(verification["finding_identity_match"])
            self.assertEqual(verification["taxonomy_versions_before"], [gf.TAXONOMY_VERSIONS[0]])
            self.assertEqual(verification["taxonomy_versions_after"], [gf.TAXONOMY_VERSIONS[0]])
            self.assertTrue(verification["taxonomy_versions_subset_of_v1v2"])
            self.assertTrue(verification["findings_ddl_has_in_list"])

    def test_dry_run_on_empty_db_reports_zero_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            conn = sqlite3.connect(db_path)
            try:
                store.ensure_findings_schema(conn)
                conn.commit()
            finally:
                conn.close()

            report = migrate.migrate_taxonomy_sqlite(db_path, write_file=False)

            self.assertEqual(report["counts"]["before"], {"raw_signals": 0, "findings": 0})
            self.assertEqual(report["counts"]["after"], {"raw_signals": 0, "findings": 0})
            self.assertTrue(report["verification"]["verified"])

    def test_missing_db_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "missing.sqlite3")
            with self.assertRaises(ValueError):
                migrate.migrate_taxonomy_sqlite(missing, write_file=False)


class WriteFileTest(unittest.TestCase):
    def test_write_file_backs_up_replaces_and_preserves_v1_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_v1_db(db_path, 4)
            before_bytes = open(db_path, "rb").read()
            backup_path = db_path + ".bak-v1"

            report = migrate.migrate_taxonomy_sqlite(db_path, write_file=True)

            self.assertEqual(report["mode"], "file_write")
            self.assertTrue(report["committed"])
            self.assertEqual(report["backup_path"], backup_path)
            self.assertTrue(report["verification"]["verified"])

            # Backup preserves the exact pre-migration v1 database.
            self.assertTrue(os.path.exists(backup_path))
            self.assertEqual(open(backup_path, "rb").read(), before_bytes)

            # Original path now serves the new (v1+v2 IN-list) DDL.
            new_ddl = _findings_table_ddl(db_path)
            self.assertIn("taxonomy_version IN (", new_ddl)
            for version in gf.TAXONOMY_VERSIONS:
                self.assertIn(f"'{version}'", new_ddl)

            # v1 rows are preserved verbatim -- not reclassified.
            self.assertEqual(_findings_taxonomy_versions(db_path), [gf.TAXONOMY_VERSIONS[0]])
            self.assertEqual(_counts(db_path), {"raw_signals": 4, "findings": 4})

            # The migrated database now accepts a fresh current-taxonomy-tagged finding
            # (gf.TAXONOMY_VERSION, not necessarily gf.TAXONOMY_VERSIONS[1] -- the migrator
            # accepts the full IN-list, but a freshly-built finding is only ever tagged the
            # *current* version, so the DB ends up with {legacy v1} union {current version},
            # not necessarily every version the CHECK constraint merely allows).
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                raw_signal, finding = _pair(document_id="doc-new-v2", firm="Firm V2", finding_text="new v2 text")
                raw_row = gf.sqlite_row(raw_signal)
                conn.execute(
                    f"INSERT INTO raw_signals ({', '.join(raw_row.keys())}) "
                    f"VALUES ({', '.join('?' for _ in raw_row)})",
                    tuple(raw_row.values()),
                )
                finding_row = gf.sqlite_row(finding)
                conn.execute(
                    f"INSERT INTO findings ({', '.join(finding_row.keys())}) "
                    f"VALUES ({', '.join('?' for _ in finding_row)})",
                    tuple(finding_row.values()),
                )
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(
                _findings_taxonomy_versions(db_path),
                sorted({gf.TAXONOMY_VERSIONS[0], gf.TAXONOMY_VERSION}),
            )

    def test_write_file_refuses_when_backup_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_v1_db(db_path, 2)
            backup_path = db_path + ".bak-v1"
            with open(backup_path, "wb") as f:
                f.write(b"pre-existing backup, must not be overwritten")
            before_db_bytes = open(db_path, "rb").read()

            with self.assertRaisesRegex(ValueError, "backup already exists"):
                migrate.migrate_taxonomy_sqlite(db_path, write_file=True)

            # Neither the original database nor the pre-existing backup were touched.
            self.assertEqual(open(db_path, "rb").read(), before_db_bytes)
            with open(backup_path, "rb") as f:
                self.assertEqual(f.read(), b"pre-existing backup, must not be overwritten")

    def test_write_file_is_repeatable_against_a_fresh_backup_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_v1_db(db_path, 3)

            first = migrate.migrate_taxonomy_sqlite(db_path, write_file=True)
            self.assertTrue(first["committed"])

            # Second run: db is now already on v2 DDL; migrator still verifies fine, but a
            # second write-file attempt must refuse because .bak-v1 from the first run exists.
            with self.assertRaisesRegex(ValueError, "backup already exists"):
                migrate.migrate_taxonomy_sqlite(db_path, write_file=True)


class CliTest(unittest.TestCase):
    def test_cli_dry_run_writes_report_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            _seed_legacy_v1_db(db_path, 2)
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
            _seed_legacy_v1_db(db_path, 2)

            rc_first = migrate.main(["--db-path", db_path, "--write-file"])
            self.assertEqual(rc_first, 0)

            rc_second = migrate.main(["--db-path", db_path, "--write-file"])
            self.assertEqual(rc_second, 2)


if __name__ == "__main__":
    unittest.main()
