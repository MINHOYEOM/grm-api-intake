#!/usr/bin/env python3
"""FIND-1 M2b static search export for the findings SQLite sidecar.

This module reads the findings SQLite database strictly read-only (via
`findings_views.open_findings_db_readonly`) and produces a self-contained JSON
envelope for a static search/viewer layer. It does not query Notion, write to
SQLite, or send anything to Supabase.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import findings_exporter
import findings_views
import grm_findings as gf
from grm_cli import write_json as _write_json


SEARCH_EXPORT_SCHEMA_VERSION = "grm-findings-search/v1"


def _raw_signal_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    cursor = conn.execute("SELECT raw_signal_id, source FROM raw_signals")
    return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]


def _build_coverage(
    conn: sqlite3.Connection,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_rows = _raw_signal_rows(conn)
    finding_raw_ids = {str(record.get("raw_signal_id") or "") for record in findings}
    raw_signal_like_records = [{"source": source} for _raw_signal_id, source in raw_rows]
    raw_signals_without_findings = [
        {"raw_signal_id": raw_signal_id}
        for raw_signal_id, _source in raw_rows
        if raw_signal_id not in finding_raw_ids
    ]
    return findings_exporter._coverage_summary(
        raw_signal_like_records,
        findings,
        raw_signals_without_findings,
    )


def build_search_export(db_path: str | Path) -> dict[str, Any]:
    """Build the grm-findings-search/v1 envelope from a read-only SQLite connection."""
    conn = findings_views.open_findings_db_readonly(db_path)
    try:
        summary = findings_views.db_summary(conn)
        findings = findings_views.query_findings(conn)
        facets = findings_views.facet_counts(conn)
        coverage = _build_coverage(conn, findings)

        records: list[dict[str, Any]] = []
        validation_errors: list[dict[str, Any]] = []
        for finding in findings:
            record = dict(finding)
            record["raw_signal"] = findings_views.raw_signal_summary(conn, record.get("raw_signal_id", ""))
            records.append(record)

            check_record = {key: value for key, value in record.items() if key != "raw_signal"}
            errors = gf.validate_finding(check_record)
            if errors:
                validation_errors.append({
                    "finding_id": str(record.get("finding_id") or ""),
                    "errors": errors,
                })
    finally:
        conn.close()

    blocking_errors = len(validation_errors)
    return {
        "schema_version": SEARCH_EXPORT_SCHEMA_VERSION,
        "raw_signal_schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "finding_schema_version": gf.FINDING_SCHEMA_VERSION,
        "taxonomy_version": gf.TAXONOMY_VERSION,
        "source_db": {
            "file_name": Path(db_path).name,
            "raw_signals": summary["raw_signals"],
            "findings": summary["findings"],
        },
        "records": records,
        "facets": facets,
        "coverage": coverage,
        "report": {
            "mode": "search_export",
            "records": len(records),
            "validation_errors": validation_errors,
            "blocking_errors": blocking_errors,
            "ready_for_viewer": blocking_errors == 0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="FIND-1 M2b findings SQLite to static search export")
    parser.add_argument("--db-path", required=True, help="Path to the findings SQLite sidecar, opened read-only")
    parser.add_argument("--output", help="Optional search export JSON output path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        result = build_search_export(args.db_path)
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"findings_search_export: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
