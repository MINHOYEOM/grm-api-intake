#!/usr/bin/env python3
"""FIND-1 M1b/M1e/M1f dry-run exporter for raw_signals and optional findings.

This module is intentionally offline-only.  It does not query Notion, write a
SQLite database, or send anything to Supabase.  The input boundary is the
existing Intake page snapshot shape plus raw payloads fetched elsewhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import findings_extractors
import grm_findings as gf


EXPORT_SCHEMA_VERSION = "grm-findings-raw-export/v1"
FINDINGS_EXPORT_SCHEMA_VERSION = "grm-findings-dry-run/v1"


def row_key(row: dict[str, Any]) -> str:
    return f"{str(row.get('source') or '').strip()}::{str(row.get('document_id') or '').strip()}"


def _raw_for_row(
    row: dict[str, Any],
    *,
    raw_by_page_id: dict[str, Any],
    raw_by_key: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    page_id = str(row.get("page_id") or "").strip()
    if page_id and page_id in raw_by_page_id:
        return raw_by_page_id[page_id], f"page_id:{page_id}"

    key = row_key(row)
    if key in raw_by_key:
        return raw_by_key[key], f"source_document:{key}"

    document_id = str(row.get("document_id") or "").strip()
    if document_id and document_id in raw_by_key:
        return raw_by_key[document_id], f"document_id:{document_id}"

    return None, ""


def build_raw_signal_export(
    rows: list[dict[str, Any]],
    *,
    raw_by_page_id: dict[str, Any] | None = None,
    raw_by_key: dict[str, Any] | None = None,
    collected_at: str = "",
    include_findings: bool = False,
) -> dict[str, Any]:
    """Convert Intake snapshots to raw_signals records without side effects."""
    raw_by_page_id = raw_by_page_id or {}
    raw_by_key = raw_by_key or {}
    records: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    raw_signals_without_findings: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    extraction_drop_details: list[dict[str, Any]] = []
    extraction_dropped_invalid = 0
    extraction_dropped_duplicate_text = 0

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            skipped.append({
                "index": index,
                "row_key": "",
                "reason": "invalid_row",
                "errors": ["row must be an object"],
            })
            continue

        key = row_key(row)
        raw, raw_source = _raw_for_row(
            row,
            raw_by_page_id=raw_by_page_id,
            raw_by_key=raw_by_key,
        )
        if raw is None:
            skipped.append({"index": index, "row_key": key, "reason": "missing_raw"})
            continue
        if not isinstance(raw, dict):
            skipped.append({
                "index": index,
                "row_key": key,
                "reason": "invalid_raw",
                "raw_source": raw_source,
                "errors": ["raw payload must be an object"],
            })
            continue

        record = gf.raw_signal_from_row(
            row,
            raw,
            collected_at=collected_at or str(row.get("collected_at") or ""),
        )
        errors = gf.validate_raw_signal(record)
        if errors:
            skipped.append({
                "index": index,
                "row_key": key,
                "reason": "invalid_raw_signal",
                "errors": errors,
            })
            continue

        raw_signal_id = record["raw_signal_id"]
        if raw_signal_id in seen_ids:
            skipped.append({
                "index": index,
                "row_key": key,
                "reason": "duplicate_raw_signal_id",
                "raw_signal_id": raw_signal_id,
            })
            continue

        record["export_source"] = raw_source
        records.append(record)
        seen_ids.add(raw_signal_id)
        if include_findings:
            extracted, extraction_report = findings_extractors.findings_from_raw_signal_with_report(record)
            if extracted:
                findings.extend(extracted)
            else:
                raw_signals_without_findings.append({
                    "index": index,
                    "row_key": key,
                    "raw_signal_id": raw_signal_id,
                })
            extraction_dropped_invalid += int(extraction_report["dropped_invalid"])
            extraction_dropped_duplicate_text += int(extraction_report["dropped_duplicate_text"])
            if extraction_report["dropped_invalid"] or extraction_report["dropped_duplicate_text"]:
                extraction_drop_details.append({
                    "raw_signal_id": raw_signal_id,
                    "row_key": key,
                    "dropped_invalid": extraction_report["dropped_invalid"],
                    "dropped_duplicate_text": extraction_report["dropped_duplicate_text"],
                    "invalid_errors": extraction_report["invalid_errors"],
                })

    report: dict[str, Any] = {
        "input_rows": len(rows),
        "exported": len(records),
        "skipped": len(skipped),
        "skipped_rows": skipped,
    }
    result: dict[str, Any] = {
        "schema_version": FINDINGS_EXPORT_SCHEMA_VERSION if include_findings else EXPORT_SCHEMA_VERSION,
        "raw_signal_schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "records": records,
        "report": report,
    }
    if include_findings:
        report["findings_exported"] = len(findings)
        report["raw_signals_without_findings"] = raw_signals_without_findings
        report["extraction_drop_details"] = extraction_drop_details
        coverage = _coverage_summary(records, findings, raw_signals_without_findings)
        coverage["extraction_dropped_invalid"] = extraction_dropped_invalid
        coverage["extraction_dropped_duplicate_text"] = extraction_dropped_duplicate_text
        report["coverage"] = coverage
        result["finding_schema_version"] = gf.FINDING_SCHEMA_VERSION
        result["taxonomy_version"] = gf.TAXONOMY_VERSION
        result["findings"] = findings
    return result


def _count_by(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(key) or "").strip() or "(blank)"
        counts[value] = counts.get(value, 0) + 1
    return {name: counts[name] for name in sorted(counts)}


def _coverage_summary(
    records: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    raw_signals_without_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_signal_ids_with_findings = {
        str(finding.get("raw_signal_id") or "").strip()
        for finding in findings
        if str(finding.get("raw_signal_id") or "").strip()
    }
    return {
        "raw_signals_total": len(records),
        "raw_signals_with_findings": len(raw_signal_ids_with_findings),
        "raw_signals_without_findings": len(raw_signals_without_findings),
        "findings_total": len(findings),
        "raw_signals_by_source": _count_by(records, "source"),
        "findings_by_source": _count_by(findings, "source"),
        "findings_by_agency": _count_by(findings, "agency"),
        "findings_by_review_status": _count_by(findings, "review_status"),
        "findings_by_evidence_level": _count_by(findings, "evidence_level"),
        "findings_by_category_code": _count_by(findings, "category_code"),
    }


def load_export_input(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("export input must be a JSON object")
    return data


def export_from_input(data: dict[str, Any], *, include_findings: bool | None = None) -> dict[str, Any]:
    rows = data.get("rows", data.get("notion_snapshots", []))
    if not isinstance(rows, list):
        raise ValueError("export input rows/notion_snapshots must be a list")
    raw_by_page_id = data.get("raw_by_page_id", {})
    raw_by_key = data.get("raw_by_key", {})
    if not isinstance(raw_by_page_id, dict):
        raise ValueError("raw_by_page_id must be an object")
    if not isinstance(raw_by_key, dict):
        raise ValueError("raw_by_key must be an object")
    return build_raw_signal_export(
        rows,
        raw_by_page_id=raw_by_page_id,
        raw_by_key=raw_by_key,
        collected_at=str(data.get("collected_at") or ""),
        include_findings=bool(data.get("include_findings")) if include_findings is None else include_findings,
    )


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
    parser = argparse.ArgumentParser(description="FIND-1 M1 raw_signals/findings dry-run exporter")
    parser.add_argument("--input", required=True, help="JSON fixture/export containing rows and raw payload maps")
    parser.add_argument("--output", help="Optional dry-run JSON output path")
    parser.add_argument("--include-findings", action="store_true", help="Also include grm-finding/v1 dry-run records")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        result = export_from_input(load_export_input(args.input), include_findings=args.include_findings)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"findings_exporter: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
