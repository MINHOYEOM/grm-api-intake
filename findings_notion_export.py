#!/usr/bin/env python3
"""FIND-1 M1k read-only Notion Intake export for live backfill.

This is the live-data bridge into the M1 offline backfill pipeline. It queries
Notion Intake pages, restores each page's raw API payload from children, and
writes the exporter input shape consumed by findings_backfill.py. It never
writes Notion, SQLite, Supabase, or Status/handoff state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from grm_cli import write_json as _write_json
from grm_common import SOURCE_HANDOFF
from grm_handoff import TYPE_ROUTINE_HANDOFF, _intake_page_snapshot, fetch_intake_raw_payload
from grm_notion import (
    NOTION_DB_QUERY_URL_TPL,
    PROP_RUN_DATE,
    PROP_STATUS,
    PROP_TYPE_CLASS,
    notion_api_request,
)


NOTION_EXPORT_SCHEMA_VERSION = "grm-findings-notion-export/v1"
TYPE_WEB_DELTA = "web-delta"
TYPE_WEB_DEEP_DELTA = "web-deep-delta"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 200


def _row_key(row: dict[str, Any]) -> str:
    return f"{str(row.get('source') or '').strip()}::{str(row.get('document_id') or '').strip()}"


def _clamp_page_size(page_size: int) -> int:
    return max(1, min(100, int(page_size or DEFAULT_PAGE_SIZE)))


def _filter_clause(
    *,
    status_names: list[str] | None = None,
    run_date_from: str = "",
    run_date_to: str = "",
) -> dict[str, Any] | None:
    filters: list[dict[str, Any]] = []
    statuses = [s.strip() for s in status_names or [] if s.strip()]
    if statuses:
        status_filters = [
            {"property": PROP_STATUS, "select": {"equals": status}}
            for status in statuses
        ]
        filters.append(status_filters[0] if len(status_filters) == 1 else {"or": status_filters})
    if run_date_from:
        filters.append({"property": PROP_RUN_DATE, "date": {"on_or_after": run_date_from}})
    if run_date_to:
        filters.append({"property": PROP_RUN_DATE, "date": {"on_or_before": run_date_to}})
    if not filters:
        return None
    return filters[0] if len(filters) == 1 else {"and": filters}


def _is_non_signal_page(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "").strip()
    type_or_class = str(row.get("type_or_class") or "").strip()
    if source == SOURCE_HANDOFF or type_or_class == TYPE_ROUTINE_HANDOFF:
        return "routine_handoff"
    if type_or_class in {TYPE_WEB_DELTA, TYPE_WEB_DEEP_DELTA}:
        return "web_delta"
    return ""


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "").strip() or "(blank)"
        counts[value] = counts.get(value, 0) + 1
    return {name: counts[name] for name in sorted(counts)}


def export_notion_intake(
    *,
    token: str,
    database_id: str,
    limit: int | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    sleep_s: float = 0.34,
    status_names: list[str] | None = None,
    run_date_from: str = "",
    run_date_to: str = "",
) -> dict[str, Any]:
    """Build an offline backfill input JSON from live Notion Intake pages."""
    if not str(token or "").strip():
        raise ValueError("NOTION_TOKEN is required")
    if not str(database_id or "").strip():
        raise ValueError("NOTION_DATABASE_ID is required")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")

    rows: list[dict[str, Any]] = []
    raw_by_page_id: dict[str, dict[str, Any]] = {}
    raw_by_key: dict[str, dict[str, Any]] = {}
    missing_raw: list[dict[str, str]] = []
    skipped_pages: list[dict[str, str]] = []
    page_count = 0
    pages_seen = 0
    raw_fetch_attempted = 0
    start_cursor = ""

    body: dict[str, Any] = {
        "page_size": _clamp_page_size(page_size),
        "sorts": [{"property": PROP_RUN_DATE, "direction": "ascending"}],
    }
    query_filter = _filter_clause(
        status_names=status_names,
        run_date_from=run_date_from,
        run_date_to=run_date_to,
    )
    if query_filter:
        body["filter"] = query_filter

    url = NOTION_DB_QUERY_URL_TPL.format(db_id=database_id)
    while True:
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]

        data = notion_api_request("POST", url, token, body=body)
        page_count += 1
        for page in data.get("results", []):
            pages_seen += 1
            row = _intake_page_snapshot(page)
            skip_reason = _is_non_signal_page(row)
            if skip_reason:
                skipped_pages.append({
                    "page_id": str(row.get("page_id") or ""),
                    "row_key": _row_key(row),
                    "reason": skip_reason,
                })
                continue

            rows.append(row)
            raw_fetch_attempted += 1
            raw = fetch_intake_raw_payload(token, str(row.get("page_id") or ""))
            if raw is None:
                missing_raw.append({
                    "page_id": str(row.get("page_id") or ""),
                    "row_key": _row_key(row),
                    "reason": "missing_raw_payload",
                })
            else:
                page_id = str(row.get("page_id") or "")
                key = _row_key(row)
                if page_id:
                    raw_by_page_id[page_id] = raw
                if key != "::":
                    raw_by_key[key] = raw

            if sleep_s > 0:
                time.sleep(sleep_s)

            if limit is not None and len(rows) >= limit:
                break

        if limit is not None and len(rows) >= limit:
            break
        if not data.get("has_more"):
            break
        start_cursor = str(data.get("next_cursor") or "")
        if not start_cursor:
            break
        if page_count >= max_pages:
            raise ValueError(f"Notion query exceeded max_pages={max_pages}")

    blocking_errors = len(missing_raw)
    return {
        "schema_version": NOTION_EXPORT_SCHEMA_VERSION,
        "rows": rows,
        "raw_by_page_id": raw_by_page_id,
        "raw_by_key": raw_by_key,
        "report": {
            "mode": "notion_read_only_export",
            "database_id": database_id,
            "query_pages": page_count,
            "pages_seen": pages_seen,
            "signal_rows_exported": len(rows),
            "skipped_pages": skipped_pages,
            "raw_fetch_attempted": raw_fetch_attempted,
            "raw_fetch_ok": len(raw_by_page_id),
            "raw_fetch_missing": len(missing_raw),
            "missing_raw": missing_raw,
            "rows_by_source": _count_by(rows, "source"),
            "rows_by_status": _count_by(rows, "status"),
            "rows_by_type_or_class": _count_by(rows, "type_or_class"),
            "query": {
                "limit": limit,
                "page_size": _clamp_page_size(page_size),
                "max_pages": max_pages,
                "status_names": [s.strip() for s in status_names or [] if s.strip()],
                "run_date_from": run_date_from,
                "run_date_to": run_date_to,
            },
            "preflight": {
                "notion_api": "read_only",
                "sqlite_write": "not_used",
                "supabase_write": "not_used",
                "status_handoff": "not_used",
                "blocking_errors": blocking_errors,
                "ready_for_backfill_plan": blocking_errors == 0,
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="FIND-1 M1 read-only Notion Intake export")
    parser.add_argument("--notion-token", default=os.environ.get("NOTION_TOKEN", ""), help="Defaults to NOTION_TOKEN")
    parser.add_argument("--database-id", default=os.environ.get("NOTION_DATABASE_ID", ""), help="Defaults to NOTION_DATABASE_ID")
    parser.add_argument("--output", help="Output JSON path; stdout when omitted")
    parser.add_argument("--limit", type=int, help="Maximum exported signal rows")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Notion query page size, 1..100")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Safety cap for Notion query pages")
    parser.add_argument("--sleep", type=float, default=0.34, help="Sleep between raw payload fetches")
    parser.add_argument("--status", action="append", default=[], help="Optional Notion Status select filter; repeatable")
    parser.add_argument("--run-date-from", default="", help="Optional Run Date (KST) lower bound, YYYY-MM-DD")
    parser.add_argument("--run-date-to", default="", help="Optional Run Date (KST) upper bound, YYYY-MM-DD")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        result = export_notion_intake(
            token=args.notion_token,
            database_id=args.database_id,
            limit=args.limit,
            page_size=args.page_size,
            max_pages=args.max_pages,
            sleep_s=args.sleep,
            status_names=args.status,
            run_date_from=args.run_date_from,
            run_date_to=args.run_date_to,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"findings_notion_export: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
