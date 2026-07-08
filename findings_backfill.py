#!/usr/bin/env python3
"""FIND-1 M1h internal backfill dry-run planner.

This module is intentionally offline-only. It reads already-exported Intake
snapshot/raw payload fixtures, runs the M1 exporter with findings enabled, and
builds a deduped dry-run bundle plus preflight report. It does not query Notion,
write SQLite, or send anything to Supabase.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import findings_exporter
import grm_findings as gf


BACKFILL_DRY_RUN_SCHEMA_VERSION = "grm-findings-internal-backfill-dry-run/v1"


def _count_by(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(key) or "").strip() or "(blank)"
        counts[value] = counts.get(value, 0) + 1
    return {name: counts[name] for name in sorted(counts)}


def _row_key_from_raw_signal(record: dict[str, Any]) -> str:
    return f"{str(record.get('source') or '').strip()}::{str(record.get('document_id') or '').strip()}"


def _raw_signals_without_findings(
    records: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    with_findings = {
        str(finding.get("raw_signal_id") or "").strip()
        for finding in findings
        if str(finding.get("raw_signal_id") or "").strip()
    }
    return [
        {
            "raw_signal_id": str(record.get("raw_signal_id") or ""),
            "row_key": _row_key_from_raw_signal(record),
            "source": str(record.get("source") or ""),
            "document_id": str(record.get("document_id") or ""),
        }
        for record in records
        if str(record.get("raw_signal_id") or "").strip() not in with_findings
    ]


def _coverage_summary(
    records: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    without_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_signal_ids_with_findings = {
        str(finding.get("raw_signal_id") or "").strip()
        for finding in findings
        if str(finding.get("raw_signal_id") or "").strip()
    }
    return {
        "raw_signals_total": len(records),
        "raw_signals_with_findings": len(raw_signal_ids_with_findings),
        "raw_signals_without_findings": len(without_findings),
        "findings_total": len(findings),
        "raw_signals_by_source": _count_by(records, "source"),
        "findings_by_source": _count_by(findings, "source"),
        "findings_by_agency": _count_by(findings, "agency"),
        "findings_by_review_status": _count_by(findings, "review_status"),
        "findings_by_evidence_level": _count_by(findings, "evidence_level"),
        "findings_by_category_code": _count_by(findings, "category_code"),
    }


def build_internal_backfill_dry_run(
    inputs: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Build a deduped internal backfill dry-run bundle without side effects."""
    if not inputs:
        raise ValueError("at least one backfill input is required")

    records: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    raw_seen: dict[str, str] = {}
    finding_seen: dict[str, str] = {}
    raw_duplicates: list[dict[str, Any]] = []
    finding_duplicates: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    batch_reports: list[dict[str, Any]] = []
    input_rows = 0
    raw_exported = 0
    findings_exported = 0

    for batch_name, data in inputs:
        export = findings_exporter.export_from_input(data, include_findings=True)
        report = export["report"]
        batch_records = list(export.get("records", []))
        batch_findings = list(export.get("findings", []))
        batch_skipped = list(report.get("skipped_rows", []))
        batch_without = list(report.get("raw_signals_without_findings", []))

        input_rows += int(report.get("input_rows") or 0)
        raw_exported += len(batch_records)
        findings_exported += len(batch_findings)
        for item in batch_skipped:
            row = dict(item)
            row["batch"] = batch_name
            skipped_rows.append(row)

        for record in batch_records:
            raw_signal_id = str(record.get("raw_signal_id") or "").strip()
            if raw_signal_id in raw_seen:
                raw_duplicates.append({
                    "batch": batch_name,
                    "first_batch": raw_seen[raw_signal_id],
                    "raw_signal_id": raw_signal_id,
                    "row_key": _row_key_from_raw_signal(record),
                })
                continue
            raw_seen[raw_signal_id] = batch_name
            records.append(record)

        for finding in batch_findings:
            finding_id = str(finding.get("finding_id") or "").strip()
            if finding_id in finding_seen:
                finding_duplicates.append({
                    "batch": batch_name,
                    "first_batch": finding_seen[finding_id],
                    "finding_id": finding_id,
                    "raw_signal_id": str(finding.get("raw_signal_id") or ""),
                    "document_id": str(finding.get("document_id") or ""),
                })
                continue
            finding_seen[finding_id] = batch_name
            findings.append(finding)

        batch_reports.append({
            "name": batch_name,
            "input_rows": int(report.get("input_rows") or 0),
            "raw_signals_exported": len(batch_records),
            "findings_exported": len(batch_findings),
            "skipped_rows": len(batch_skipped),
            "raw_signals_without_findings": len(batch_without),
        })

    raw_validation_errors = [
        {"raw_signal_id": str(record.get("raw_signal_id") or ""), "errors": errors}
        for record in records
        for errors in [gf.validate_raw_signal(record)]
        if errors
    ]
    finding_validation_errors = [
        {"finding_id": str(finding.get("finding_id") or ""), "errors": errors}
        for finding in findings
        for errors in [gf.validate_finding(finding)]
        if errors
    ]
    raw_ids = {str(record.get("raw_signal_id") or "").strip() for record in records}
    orphan_findings = [
        {
            "finding_id": str(finding.get("finding_id") or ""),
            "raw_signal_id": str(finding.get("raw_signal_id") or ""),
        }
        for finding in findings
        if str(finding.get("raw_signal_id") or "").strip() not in raw_ids
    ]
    without_findings = _raw_signals_without_findings(records, findings)
    blocking_errors = (
        len(skipped_rows)
        + len(raw_validation_errors)
        + len(finding_validation_errors)
        + len(orphan_findings)
    )
    review_warnings = len(raw_duplicates) + len(finding_duplicates) + len(without_findings)

    report = {
        "mode": "dry_run",
        "input_batches": len(inputs),
        "input_rows": input_rows,
        "raw_signals_exported": raw_exported,
        "raw_signals_unique": len(records),
        "raw_signal_duplicates": len(raw_duplicates),
        "findings_exported": findings_exported,
        "findings_unique": len(findings),
        "finding_duplicates": len(finding_duplicates),
        "skipped_rows": len(skipped_rows),
        "raw_signals_without_findings": without_findings,
        "validation_errors": {
            "raw_signals": raw_validation_errors,
            "findings": finding_validation_errors,
            "orphan_findings": orphan_findings,
        },
        "preflight": {
            "notion_api": "not_used",
            "sqlite_write": "not_used",
            "supabase_write": "not_used",
            "blocking_errors": blocking_errors,
            "review_warnings": review_warnings,
            "ready_for_sqlite_append_dry_run": blocking_errors == 0,
        },
        "coverage": _coverage_summary(records, findings, without_findings),
    }
    return {
        "schema_version": BACKFILL_DRY_RUN_SCHEMA_VERSION,
        "raw_signal_schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "finding_schema_version": gf.FINDING_SCHEMA_VERSION,
        "taxonomy_version": gf.TAXONOMY_VERSION,
        "batches": batch_reports,
        "records": records,
        "findings": findings,
        "duplicates": {
            "raw_signals": raw_duplicates,
            "findings": finding_duplicates,
        },
        "skipped_rows": skipped_rows,
        "report": report,
    }


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return data


def load_manifest(path: str | Path) -> list[tuple[str, dict[str, Any]]]:
    manifest_path = Path(path)
    manifest = load_json(manifest_path)
    batches = manifest.get("batches")
    if not isinstance(batches, list):
        raise ValueError("manifest.batches must be a list")
    loaded: list[tuple[str, dict[str, Any]]] = []
    for index, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict):
            raise ValueError(f"manifest.batches[{index}] must be an object")
        input_path = str(batch.get("input") or "").strip()
        if not input_path:
            raise ValueError(f"manifest.batches[{index}].input required")
        resolved = Path(input_path)
        if not resolved.is_absolute():
            resolved = manifest_path.parent / resolved
        name = str(batch.get("name") or resolved.stem).strip()
        loaded.append((name, load_json(resolved)))
    return loaded


def _parse_input_spec(spec: str) -> tuple[str, Path]:
    if "=" in spec:
        name, path = spec.split("=", 1)
        return name.strip() or Path(path).stem, Path(path)
    path = Path(spec)
    return path.stem, path


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
    """Exit codes: 0 clean, 2 input/IO error, 3 preflight blocking_errors > 0."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M1h internal backfill dry-run planner "
        "(exit 0=clean, 2=input/IO error, 3=preflight blocking_errors > 0)"
    )
    parser.add_argument("--manifest", help="JSON manifest with batches[{name,input}]")
    parser.add_argument("--input", action="append", default=[], help="Input JSON path, repeatable. Use NAME=PATH to name a batch")
    parser.add_argument("--output", help="Optional dry-run plan JSON output path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        if args.manifest and args.input:
            raise ValueError("use either --manifest or --input, not both")
        if args.manifest:
            inputs = load_manifest(args.manifest)
        else:
            inputs = [
                (name, load_json(path))
                for name, path in (_parse_input_spec(spec) for spec in args.input)
            ]
        result = build_internal_backfill_dry_run(inputs)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"findings_backfill: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))

    blocking_errors = int(result.get("report", {}).get("preflight", {}).get("blocking_errors") or 0)
    return 3 if blocking_errors > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
