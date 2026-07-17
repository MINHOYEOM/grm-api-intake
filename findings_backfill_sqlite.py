#!/usr/bin/env python3
"""FIND-1 M1i SQLite transaction dry-run validator for backfill plans.

This module validates a M1h backfill dry-run plan against the real SQLite DDL
and append helpers using an in-memory transaction. It always rolls back and
does not query Notion, write a SQLite file, or send anything to Supabase.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import findings_backfill
import findings_store
import grm_findings as gf
from grm_cli import load_json_object as _load_json
from grm_cli import parse_input_spec as _parse_input_spec
from grm_cli import write_json as _write_json


SQLITE_BACKFILL_DRY_RUN_SCHEMA_VERSION = "grm-findings-sqlite-backfill-dry-run/v1"


def _count_rows(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "raw_signals": int(conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]),
        "findings": int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]),
    }


def _status_inc(target: dict[str, int], status: str) -> None:
    key = status.strip() or "(blank)"
    target[key] = target.get(key, 0) + 1


def _empty_pass_summary() -> dict[str, Any]:
    return {
        "records_attempted": 0,
        "result_statuses": {},
        "raw_signal_statuses": {},
        "raw_signals_inserted": 0,
        "raw_signals_duplicate": 0,
        "raw_signals_invalid": 0,
        "findings_inserted": 0,
        "findings_duplicate": 0,
        "findings_invalid": 0,
        "errors": [],
    }


def _record_result(summary: dict[str, Any], result: Any) -> None:
    summary["records_attempted"] += 1
    _status_inc(summary["result_statuses"], str(result.status))
    _status_inc(summary["raw_signal_statuses"], str(result.raw_signal_status))
    if result.raw_signal_status == "inserted":
        summary["raw_signals_inserted"] += 1
    elif result.raw_signal_status == "duplicate":
        summary["raw_signals_duplicate"] += 1
    elif result.raw_signal_status == "invalid":
        summary["raw_signals_invalid"] += 1
    summary["findings_inserted"] += int(result.findings_inserted)
    summary["findings_duplicate"] += int(result.findings_duplicate)
    summary["findings_invalid"] += int(result.findings_invalid)
    for error in result.errors:
        summary["errors"].append(str(error))


def _findings_by_raw_signal(plan: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    records = plan.get("records", [])
    findings = plan.get("findings", [])
    if not isinstance(records, list):
        raise ValueError("plan.records must be a list")
    if not isinstance(findings, list):
        raise ValueError("plan.findings must be a list")

    raw_ids = {
        str(record.get("raw_signal_id") or "").strip()
        for record in records
        if isinstance(record, dict)
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    orphan_findings: list[dict[str, str]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            orphan_findings.append({
                "finding_id": "",
                "raw_signal_id": "",
                "reason": "finding must be an object",
            })
            continue
        raw_signal_id = str(finding.get("raw_signal_id") or "").strip()
        if raw_signal_id not in raw_ids:
            orphan_findings.append({
                "finding_id": str(finding.get("finding_id") or ""),
                "raw_signal_id": raw_signal_id,
                "reason": "raw_signal_id not found in plan.records",
            })
            continue
        grouped.setdefault(raw_signal_id, []).append(finding)
    return grouped, orphan_findings


def _append_pass(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
    grouped_findings: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    summary = _empty_pass_summary()
    for record in records:
        if not isinstance(record, dict):
            summary["records_attempted"] += 1
            summary["raw_signals_invalid"] += 1
            summary["errors"].append("record must be an object")
            continue
        raw_signal_id = str(record.get("raw_signal_id") or "").strip()
        result = findings_store.append_raw_signal_with_findings(
            conn,
            record,
            grouped_findings.get(raw_signal_id, []),
        )
        _record_result(summary, result)
    summary["errors"] = sorted(set(summary["errors"]))
    return summary


def sqlite_transaction_dry_run(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a backfill plan in an in-memory SQLite transaction, then rollback."""
    records = plan.get("records", [])
    if not isinstance(records, list):
        raise ValueError("plan.records must be a list")
    grouped_findings, orphan_findings = _findings_by_raw_signal(plan)

    conn = sqlite3.connect(":memory:")
    try:
        findings_store.ensure_findings_schema(conn)
        conn.commit()
        before_counts = _count_rows(conn)

        conn.execute("BEGIN")
        first_pass = _append_pass(conn, records, grouped_findings)
        counts_after_first = _count_rows(conn)
        replay_pass = _append_pass(conn, records, grouped_findings)
        counts_after_replay = _count_rows(conn)
        conn.rollback()
        counts_after_rollback = _count_rows(conn)
    finally:
        conn.close()

    blocking_errors = (
        first_pass["raw_signals_invalid"]
        + first_pass["findings_invalid"]
        + len(first_pass["errors"])
        + len(orphan_findings)
    )
    rollback_verified = counts_after_rollback == before_counts
    return {
        "schema_version": SQLITE_BACKFILL_DRY_RUN_SCHEMA_VERSION,
        "source_plan_schema_version": str(plan.get("schema_version") or ""),
        "raw_signal_schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "finding_schema_version": gf.FINDING_SCHEMA_VERSION,
        "mode": "sqlite_transaction_dry_run",
        "transaction": {
            "database": ":memory:",
            "committed": False,
            "rollback_verified": rollback_verified,
        },
        "report": {
            "records_input": len(records),
            "findings_input": len(plan.get("findings", [])) if isinstance(plan.get("findings", []), list) else 0,
            "orphan_findings": orphan_findings,
            "blocking_errors": blocking_errors,
            "ready_for_commit_review": blocking_errors == 0 and rollback_verified,
            "preflight": {
                "notion_api": "not_used",
                "sqlite_file_write": "not_used",
                "supabase_write": "not_used",
            },
            "sqlite_counts": {
                "before": before_counts,
                "after_first_pass": counts_after_first,
                "after_replay_pass": counts_after_replay,
                "after_rollback": counts_after_rollback,
            },
            "first_pass": first_pass,
            "replay_pass": replay_pass,
        },
    }


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


def main(argv: list[str] | None = None) -> int:
    """Exit codes: 0 clean, 2 input/IO error, 3 blocking_errors > 0 or rollback not verified."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M1i SQLite backfill transaction dry-run "
        "(exit 0=clean, 2=input/IO error, 3=blocking_errors > 0 or rollback not verified)"
    )
    parser.add_argument("--plan", help="M1h backfill dry-run plan JSON")
    parser.add_argument("--manifest", help="M1h manifest; builds a plan in memory before SQLite validation")
    parser.add_argument("--input", action="append", default=[], help="Exporter input JSON path, repeatable. Use NAME=PATH to name a batch")
    parser.add_argument("--output", help="Optional SQLite dry-run report JSON output path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        result = sqlite_transaction_dry_run(_plan_from_args(args))
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"findings_backfill_sqlite: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))

    report = result.get("report", {})
    blocking_errors = int(report.get("blocking_errors") or 0)
    rollback_verified = bool(result.get("transaction", {}).get("rollback_verified"))
    return 3 if blocking_errors > 0 or not rollback_verified else 0


if __name__ == "__main__":
    raise SystemExit(main())
