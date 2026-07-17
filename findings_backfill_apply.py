#!/usr/bin/env python3
"""FIND-1 M1j guarded SQLite file writer for backfill plans.

This module is the first M1 boundary that can write a SQLite file. It only does
so when the caller explicitly passes the write guard. It still does not query
Notion, update Status/handoff state, or send anything to Supabase.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import findings_backfill
import findings_backfill_sqlite
import findings_store
import grm_findings as gf
from grm_cli import load_json_object as _load_json
from grm_cli import parse_input_spec as _parse_input_spec
from grm_cli import write_json as _write_json


SQLITE_BACKFILL_APPLY_SCHEMA_VERSION = "grm-findings-sqlite-backfill-apply/v1"


def _plan_from_args(args: argparse.Namespace) -> dict[str, Any]:
    selected = sum(1 for value in (args.plan, args.manifest, bool(args.input)) if value)
    if selected != 1:
        raise ValueError("use exactly one of --plan, --manifest, or --input")
    if args.plan:
        return _load_json(args.plan)
    if args.manifest:
        inputs = findings_backfill.load_manifest(args.manifest)
    else:
        inputs = [
            (name, _load_json(path))
            for name, path in (_parse_input_spec(spec) for spec in args.input)
        ]
    return findings_backfill.build_internal_backfill_dry_run(inputs)


def _apply_blocking_errors(apply_pass: dict[str, Any]) -> int:
    return (
        int(apply_pass.get("raw_signals_invalid") or 0)
        + int(apply_pass.get("findings_invalid") or 0)
        + len(apply_pass.get("errors") or [])
    )


def apply_backfill_plan_to_sqlite(
    plan: dict[str, Any],
    db_path: str | Path,
    *,
    write_file: bool = False,
) -> dict[str, Any]:
    """Apply a validated M1h plan to a SQLite file behind an explicit guard."""
    if not write_file:
        raise ValueError("SQLite file writes require write_file=True")
    path = Path(db_path)
    if not str(path).strip():
        raise ValueError("db_path is required")

    transaction_dry_run = findings_backfill_sqlite.sqlite_transaction_dry_run(plan)
    dry_run_report = transaction_dry_run["report"]
    if not dry_run_report.get("ready_for_commit_review"):
        raise ValueError("M1i transaction dry-run is not ready for SQLite file write")

    records = plan.get("records", [])
    if not isinstance(records, list):
        raise ValueError("plan.records must be a list")
    grouped_findings, orphan_findings = findings_backfill_sqlite._findings_by_raw_signal(plan)
    if orphan_findings:
        raise ValueError("plan contains orphan findings")

    existed_before = path.exists()
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    try:
        findings_store.ensure_findings_schema(conn)
        conn.commit()
        counts_before = findings_backfill_sqlite._count_rows(conn)

        conn.execute("BEGIN")
        apply_pass = findings_backfill_sqlite._append_pass(conn, records, grouped_findings)
        counts_after_apply = findings_backfill_sqlite._count_rows(conn)
        blocking_errors = _apply_blocking_errors(apply_pass)
        if blocking_errors:
            conn.rollback()
            committed = False
            counts_after_commit = findings_backfill_sqlite._count_rows(conn)
        else:
            conn.commit()
            committed = True
            counts_after_commit = findings_backfill_sqlite._count_rows(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "schema_version": SQLITE_BACKFILL_APPLY_SCHEMA_VERSION,
        "source_plan_schema_version": str(plan.get("schema_version") or ""),
        "raw_signal_schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "finding_schema_version": gf.FINDING_SCHEMA_VERSION,
        "mode": "sqlite_file_write",
        "write_guard": {
            "explicit_write_file": True,
            "db_path": str(path),
            "database_existed_before": existed_before,
            "committed": committed,
        },
        "report": {
            "records_input": len(records),
            "findings_input": len(plan.get("findings", [])) if isinstance(plan.get("findings", []), list) else 0,
            "blocking_errors": blocking_errors,
            "ready_for_search_export": committed and blocking_errors == 0,
            "preflight": {
                "m1i_transaction_dry_run": "passed",
                "notion_api": "not_used",
                "sqlite_file_write": "used_explicit_guard",
                "supabase_write": "not_used",
                "status_handoff": "not_used",
            },
            "sqlite_counts": {
                "before": counts_before,
                "after_apply": counts_after_apply,
                "after_commit": counts_after_commit,
            },
            "transaction_dry_run_counts": dry_run_report.get("sqlite_counts", {}),
            "apply_pass": apply_pass,
        },
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="FIND-1 M1j guarded SQLite backfill file writer")
    parser.add_argument("--plan", help="M1h backfill dry-run plan JSON")
    parser.add_argument("--manifest", help="M1h manifest; builds a plan in memory before applying")
    parser.add_argument("--input", action="append", default=[], help="Exporter input JSON path, repeatable. Use NAME=PATH to name a batch")
    parser.add_argument("--db-path", help="Target SQLite file path. Required with --write-file")
    parser.add_argument("--write-file", action="store_true", help="Required guard that allows SQLite file writes")
    parser.add_argument("--output", help="Optional apply report JSON output path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        if not args.write_file:
            raise ValueError("refusing to write SQLite file without --write-file")
        if not args.db_path:
            raise ValueError("--db-path is required with --write-file")
        result = apply_backfill_plan_to_sqlite(
            _plan_from_args(args),
            args.db_path,
            write_file=args.write_file,
        )
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"findings_backfill_apply: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
