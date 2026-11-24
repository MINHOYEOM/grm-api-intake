#!/usr/bin/env python3
"""Report blank FDA 483 inspector reasons from stored full text as dry-run JSON.

Use --rows-json with a local export of raw_signals rows or with FDA 483 fixture
rows containing document_id, body, and optional insp and st. --read-db requires
SUPABASE_URL and a SELECT-only credential in SUPABASE_READ_ONLY_KEY.
"""
from __future__ import annotations

import sys

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from backfill_483_inspectors_from_body import BODY_KEY, SOURCE_FDA_483, is_target, row_body
import collect_fda_483 as fda483


_BLANK_REASONS = {
    fda483.INSPECTORS_NO_SIGNATURE: "no_signature_block",
    fda483.INSPECTORS_REDACTED: "redacted",
    fda483.INSPECTORS_PARSER_LIMIT: "ocr_unreadable",
}
_EXPECTED_BLANK_REASONS = {"no_signature_block", "redacted"}


def audit(rows: list[dict]) -> dict:
    results = []
    for row in rows:
        if row.get("source", SOURCE_FDA_483) != SOURCE_FDA_483 or not is_target(row):
            continue
        _, extraction_reason = fda483._extract_483_inspectors_detail(row_body(row))
        if extraction_reason == fda483.INSPECTORS_NO_TEXT:
            continue
        # Spelling conflicts and usable names still missing from storage require review.
        reason = _BLANK_REASONS.get(extraction_reason, "needs_human")
        results.append({
            "raw_signal_id": row.get("raw_signal_id"),
            "document_id": row.get("document_id"),
            "reason": reason,
            "requires_human": reason not in _EXPECTED_BLANK_REASONS,
        })
    return {
        "mode": "dry-run",
        "scanned": len(rows),
        "counts": dict(Counter(row["reason"] for row in results)),
        "rows": results,
    }


def _fetch_rows(url: str, read_only_key: str) -> list[dict]:
    from findings_supabase_backfill import _fetch_all_pages

    return _fetch_all_pages(
        url, read_only_key, "raw_signals", select="raw_signal_id,document_id,raw_json",
        page_size=200, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "raw_json": f"like.*{BODY_KEY}*"},
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rows-json", type=Path, help="Read a local JSON array of FDA 483 rows.")
    source.add_argument("--read-db", action="store_true", help="Read stored rows using a SELECT-only key.")
    parser.add_argument("--apply", action="store_true", help="Unavailable pending a reason column.")
    args = parser.parse_args(argv)
    if args.apply:
        parser.error("--apply is unavailable pending a database column for blank reasons")

    if args.read_db:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_READ_ONLY_KEY", "").strip()
        if not url or not key:
            parser.error("--read-db requires SUPABASE_URL and SUPABASE_READ_ONLY_KEY; use --rows-json locally")
        try:
            rows = _fetch_rows(url, key)
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
    else:
        try:
            rows = json.loads(args.rows_json.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            parser.error(f"could not read --rows-json: {exc}")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            parser.error("--rows-json must contain a JSON array of row objects")

    print(json.dumps(audit(rows), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
