#!/usr/bin/env python3
"""FIND-1 M6a guarded SQLite translation-column migrator for the findings sidecar.

Unlike the M5b taxonomy migrator (`findings_taxonomy_migrate_sqlite.py`), this
migration does not need to rebuild the file: SQLite's `ALTER TABLE ... ADD
COLUMN` supports a column-level `NOT NULL DEFAULT` and a `CHECK` constraint
(as long as the check does not reference other columns), so the two new
optional translation columns can be added to the existing `findings` table
in place.

This module never mutates the original SQLite file unless the caller
explicitly passes `--write-file`. Even a dry-run does more than just read
`PRAGMA table_info`: it runs the real `ALTER TABLE` statements inside an
explicit transaction and then rolls back (SQLite's schema changes are
transactional), so the dry-run report's `verified` flag reflects an actual
rehearsal of the migration rather than a guess -- and the file is provably
byte-identical afterwards. With `--write-file`, a `.bak-v2` backup of the
original is made first (refusing to run if that backup already exists)
before the same `ALTER TABLE` statements are committed against the live
file. Existing rows are left untouched other than picking up the new
columns' default values (`''` for both) -- this migrator does not populate
any translations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import grm_findings as gf


TRANSLATION_MIGRATE_SCHEMA_VERSION = "grm-findings-translation-migrate/v1"

_NEW_COLUMNS = ("finding_text_ko", "translation_method")


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "raw_signals": int(conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]),
        "findings": int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]),
    }


def _findings_columns(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("PRAGMA table_info(findings)").fetchall()
    return [str(row[1]) for row in rows]


def _missing_columns(conn: sqlite3.Connection) -> list[str]:
    present = set(_findings_columns(conn))
    return [column for column in _NEW_COLUMNS if column not in present]


def _translation_method_check_sql() -> str:
    values = ", ".join(f"'{method}'" for method in gf.TRANSLATION_METHODS)
    return f"CHECK (translation_method IN ({values}))"


def _apply_alter_statements(conn: sqlite3.Connection, missing: list[str]) -> None:
    """Run ALTER TABLE ADD COLUMN only for columns not already present."""
    if "finding_text_ko" in missing:
        conn.execute(
            "ALTER TABLE findings ADD COLUMN finding_text_ko TEXT NOT NULL DEFAULT ''"
        )
    if "translation_method" in missing:
        conn.execute(
            "ALTER TABLE findings ADD COLUMN translation_method TEXT NOT NULL DEFAULT '' "
            + _translation_method_check_sql()
        )


def _rehearse(
    conn: sqlite3.Connection,
    *,
    before_counts: dict[str, int],
    before_missing: list[str],
) -> dict[str, bool]:
    """Run the real ALTER statements inside a transaction, then roll back.

    Used for the dry-run path: proves the migration would succeed (or
    surfaces why it wouldn't) without leaving any trace on disk.
    """
    if not before_missing:
        return {"counts_match": True, "columns_present": True}

    conn.execute("BEGIN")
    try:
        _apply_alter_statements(conn, before_missing)
        sim_counts = _row_counts(conn)
        sim_missing = _missing_columns(conn)
    finally:
        conn.execute("ROLLBACK")
    return {
        "counts_match": sim_counts == before_counts,
        "columns_present": not sim_missing,
    }


def migrate_translation_columns_sqlite(
    db_path: str | Path,
    *,
    write_file: bool = False,
) -> dict[str, Any]:
    """Add finding_text_ko/translation_method to the findings sidecar in place.

    Without `write_file=True` this is always a dry-run: the two ALTER TABLE
    statements are executed and then rolled back inside one transaction, so
    the report's `verified` flag reflects a real rehearsal while the original
    file is left provably byte-identical. With `write_file=True`, `{db_path}
    .bak-v2` is created first (erroring if it already exists), then the same
    ALTER TABLE statements are committed against the live file, followed by a
    verification pass (`PRAGMA table_info` shows both columns, row counts
    unchanged).
    """
    source_path = Path(db_path)
    if not str(source_path).strip():
        raise ValueError("db_path is required")
    if not source_path.is_file():
        raise ValueError(f"findings_translation_migrate_sqlite: database file not found: {source_path}")

    backup_path = source_path.with_name(source_path.name + ".bak-v2")
    if write_file and backup_path.exists():
        raise ValueError(
            f"findings_translation_migrate_sqlite: backup already exists, refusing to overwrite: {backup_path}"
        )

    conn = sqlite3.connect(source_path)
    try:
        before_counts = _row_counts(conn)
        before_missing = _missing_columns(conn)
        already_present_before = [c for c in _NEW_COLUMNS if c not in before_missing]
        rehearsal = _rehearse(conn, before_counts=before_counts, before_missing=before_missing)
    finally:
        conn.close()

    dry_run_verified = rehearsal["counts_match"] and rehearsal["columns_present"]

    if not write_file:
        return {
            "schema_version": TRANSLATION_MIGRATE_SCHEMA_VERSION,
            "mode": "dry_run",
            "db_path": str(source_path),
            "backup_path": "",
            "counts": {"before": before_counts, "after": before_counts},
            "columns": {
                "missing_before": before_missing,
                "already_present_before": already_present_before,
            },
            "verification": {
                "counts_match": rehearsal["counts_match"],
                "columns_present": rehearsal["columns_present"],
                "verified": dry_run_verified,
            },
            "ready": dry_run_verified,
            "committed": False,
        }

    if not before_missing:
        # Both columns already exist -- nothing to ALTER, and no backup is made
        # since there is no destructive step to guard against.
        return {
            "schema_version": TRANSLATION_MIGRATE_SCHEMA_VERSION,
            "mode": "file_write",
            "db_path": str(source_path),
            "backup_path": "",
            "counts": {"before": before_counts, "after": before_counts},
            "columns": {
                "missing_before": before_missing,
                "already_present_before": already_present_before,
            },
            "verification": {
                "counts_match": True,
                "columns_present": True,
                "verified": True,
            },
            "ready": True,
            "committed": False,
        }

    shutil.copy2(source_path, backup_path)

    conn = sqlite3.connect(source_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _apply_alter_statements(conn, before_missing)
        conn.commit()
        after_counts = _row_counts(conn)
        after_missing = _missing_columns(conn)
    finally:
        conn.close()

    counts_match = after_counts == before_counts
    columns_present = not after_missing
    verified = counts_match and columns_present

    return {
        "schema_version": TRANSLATION_MIGRATE_SCHEMA_VERSION,
        "mode": "file_write",
        "db_path": str(source_path),
        "backup_path": str(backup_path),
        "counts": {"before": before_counts, "after": after_counts},
        "columns": {
            "missing_before": before_missing,
            "already_present_before": already_present_before,
        },
        "verification": {
            "counts_match": counts_match,
            "columns_present": columns_present,
            "verified": verified,
        },
        "ready": verified,
        "committed": True,
    }


def _write_json(path: str | Path, data: dict[str, Any], *, pretty: bool) -> None:
    text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    Path(path).write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=(
            "FIND-1 M6a guarded SQLite translation-column migrator "
            "(dry-run unless --write-file)"
        )
    )
    parser.add_argument("--db-path", required=True, help="Path to the findings SQLite sidecar")
    parser.add_argument("--output", help="Optional migration report JSON output path")
    parser.add_argument(
        "--write-file",
        action="store_true",
        help="Required guard to ALTER the live SQLite file; omit for a dry-run (original never touched)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        report = migrate_translation_columns_sqlite(args.db_path, write_file=args.write_file)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"findings_translation_migrate_sqlite: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, report, pretty=args.pretty)
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))

    if not report["ready"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
