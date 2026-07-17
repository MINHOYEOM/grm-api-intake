#!/usr/bin/env python3
"""FIND-1 M5b guarded SQLite taxonomy v1 -> v2 DDL migrator for the findings sidecar.

The local `grm-findings.sqlite3` sidecar cannot ALTER a column CHECK constraint in
place, so upgrading it from the v1-only `taxonomy_version` CHECK to the v2 IN-list
CHECK (`grm_findings.sqlite_schema_ddl()`) requires rebuilding the file with the new
DDL and copying every row across unchanged.

This module never mutates the original SQLite file unless the caller explicitly
passes `--write-file`. Even then, it never overwrites data in place: it builds the
replacement database in a temp file, verifies it against the original, writes a
`.bak-v1` backup of the original (refusing to run if that backup already exists),
and only then atomically replaces the original via `os.replace`. Existing v1-tagged
rows are copied verbatim -- this migrator does not reclassify anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import findings_store
import findings_views
import grm_findings as gf
from grm_cli import write_json as _write_json


TAXONOMY_MIGRATE_SCHEMA_VERSION = "grm-findings-taxonomy-migrate/v1"

_RAW_SIGNAL_COLUMNS = findings_store.RAW_SIGNAL_SQLITE_COLUMNS
_FINDING_COLUMNS = findings_store.FINDING_SQLITE_COLUMNS


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "raw_signals": int(conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]),
        "findings": int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]),
    }


def _distinct_taxonomy_versions(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT taxonomy_version FROM findings").fetchall()
    return sorted(str(row[0]) for row in rows)


def _finding_identity_hashes(conn: sqlite3.Connection) -> set[str]:
    """Hash set of (raw_signal_id, finding_text) pairs -- identity check, not full-row diff."""
    rows = conn.execute("SELECT raw_signal_id, finding_text FROM findings").fetchall()
    hashes: set[str] = set()
    for raw_signal_id, finding_text in rows:
        payload = json.dumps([str(raw_signal_id), str(finding_text)], ensure_ascii=False)
        hashes.add(hashlib.sha256(payload.encode("utf-8")).hexdigest())
    return hashes


def _findings_table_ddl(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'findings'"
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def _copy_table(
    source_conn: sqlite3.Connection,
    dest_conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> int:
    """Copy every row of `table` from source to dest, values passed through unchanged.

    Values already stored in SQLite (TEXT/REAL, including JSON-text list fields such
    as findings.inspector_names) are read and re-inserted verbatim -- no re-encoding.
    """
    columns_sql = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = source_conn.execute(f"SELECT {columns_sql} FROM {table}").fetchall()
    for row in rows:
        dest_conn.execute(
            f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})",
            tuple(row),
        )
    return len(rows)


def _build_new_ddl_db(source_conn: sqlite3.Connection, dest_path: Path) -> None:
    dest_conn = sqlite3.connect(dest_path)
    try:
        findings_store.ensure_findings_schema(dest_conn)
        _copy_table(source_conn, dest_conn, "raw_signals", _RAW_SIGNAL_COLUMNS)
        _copy_table(source_conn, dest_conn, "findings", _FINDING_COLUMNS)
        dest_conn.commit()
    finally:
        dest_conn.close()


def _verify_new_db(
    new_db_path: Path,
    *,
    before_counts: dict[str, int],
    before_hashes: set[str],
) -> dict[str, Any]:
    new_conn = findings_views.open_findings_db_readonly(new_db_path)
    try:
        after_counts = _row_counts(new_conn)
        after_hashes = _finding_identity_hashes(new_conn)
        after_taxonomy_versions = _distinct_taxonomy_versions(new_conn)
        findings_ddl = _findings_table_ddl(new_conn)
    finally:
        new_conn.close()

    counts_match = after_counts == before_counts
    identity_match = after_hashes == before_hashes
    taxonomy_ok = set(after_taxonomy_versions) <= set(gf.TAXONOMY_VERSIONS)
    in_list_present = "taxonomy_version IN (" in findings_ddl and all(
        f"'{version}'" in findings_ddl for version in gf.TAXONOMY_VERSIONS
    )

    return {
        "counts_after": after_counts,
        "counts_match": counts_match,
        "finding_identity_match": identity_match,
        "taxonomy_versions_after": after_taxonomy_versions,
        "taxonomy_versions_subset_of_v1v2": taxonomy_ok,
        "findings_ddl_has_in_list": in_list_present,
        "verified": counts_match and identity_match and taxonomy_ok and in_list_present,
    }


def migrate_taxonomy_sqlite(
    db_path: str | Path,
    *,
    write_file: bool = False,
) -> dict[str, Any]:
    """Rebuild the findings sidecar under the v2 taxonomy_version IN-list DDL.

    Without `write_file=True` this is always a dry-run: the original is opened
    strictly read-only, a throwaway v2-DDL copy is built and verified in a temp
    directory, and the temp directory is discarded -- the original is never
    touched. With `write_file=True`, verification must pass before anything is
    written; only then is `{db_path}.bak-v1` created (erroring if it already
    exists) and the original atomically replaced via `os.replace`.
    """
    source_path = Path(db_path)
    if not str(source_path).strip():
        raise ValueError("db_path is required")

    source_conn = findings_views.open_findings_db_readonly(source_path)
    try:
        before_counts = _row_counts(source_conn)
        before_taxonomy_versions = _distinct_taxonomy_versions(source_conn)
        before_hashes = _finding_identity_hashes(source_conn)

        backup_path = source_path.with_name(source_path.name + ".bak-v1")
        if write_file and backup_path.exists():
            raise ValueError(
                f"findings_taxonomy_migrate_sqlite: backup already exists, refusing to overwrite: {backup_path}"
            )

        with tempfile.TemporaryDirectory(prefix="grm-findings-taxonomy-migrate-") as tmp_dir:
            tmp_new_db = Path(tmp_dir) / "grm-findings.v2.sqlite3"
            _build_new_ddl_db(source_conn, tmp_new_db)
            verification = _verify_new_db(
                tmp_new_db,
                before_counts=before_counts,
                before_hashes=before_hashes,
            )

            # Windows refuses to os.replace() a file that still has an open handle -- close
            # the read-only source connection before touching the original path on disk.
            source_conn.close()

            committed = False
            if write_file and verification["verified"]:
                shutil.copy2(source_path, backup_path)
                final_tmp = source_path.with_name(source_path.name + ".tmp-v2")
                if final_tmp.exists():
                    final_tmp.unlink()
                shutil.copy2(tmp_new_db, final_tmp)
                os.replace(final_tmp, source_path)
                committed = True
            # tmp_new_db (and its TemporaryDirectory) is discarded here regardless of mode --
            # the original file is only ever mutated by the os.replace() above.

        mode = "file_write" if write_file else "dry_run"
        return {
            "schema_version": TAXONOMY_MIGRATE_SCHEMA_VERSION,
            "mode": mode,
            "db_path": str(source_path),
            "backup_path": str(backup_path) if (write_file and verification["verified"]) else "",
            "counts": {
                "before": before_counts,
                "after": verification["counts_after"],
            },
            "verification": {
                "counts_match": verification["counts_match"],
                "finding_identity_match": verification["finding_identity_match"],
                "taxonomy_versions_before": before_taxonomy_versions,
                "taxonomy_versions_after": verification["taxonomy_versions_after"],
                "taxonomy_versions_subset_of_v1v2": verification["taxonomy_versions_subset_of_v1v2"],
                "findings_ddl_has_in_list": verification["findings_ddl_has_in_list"],
                "verified": verification["verified"],
            },
            "ready": verification["verified"],
            "committed": committed,
        }
    finally:
        source_conn.close()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M5b guarded SQLite taxonomy v1 -> v2 DDL migrator (dry-run unless --write-file)"
    )
    parser.add_argument("--db-path", required=True, help="Path to the findings SQLite sidecar")
    parser.add_argument("--output", help="Optional migration report JSON output path")
    parser.add_argument(
        "--write-file",
        action="store_true",
        help="Required guard to replace the original SQLite file; omit for a dry-run (original never touched)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        report = migrate_taxonomy_sqlite(args.db_path, write_file=args.write_file)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"findings_taxonomy_migrate_sqlite: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, report, pretty=args.pretty)
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))

    if not report["verification"]["verified"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
