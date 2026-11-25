#!/usr/bin/env python3
"""Mark reviewed FDA 483s as out-of-scope. Preview by default."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import findings_supabase_backfill as fsb
import raw_versioning as rv
from backfill_483_ocr_recovery import OUT_OF_SCOPE_STATUS
from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
from grm_common import SOURCE_FDA_483


_DOC_ID = re.compile(r"fda483-[0-9]+")
_CATEGORY = re.compile(r"[a-z][a-z0-9_-]*")
_FDA_NAME = re.compile(r"\bFood\s+(?:and|&)\s+Drug\s+Administration\b", re.IGNORECASE)
_DRUG_CLUE = re.compile(
    r"\b(?:otc\s+drugs?|drugs?|pharmaceuticals?|biologic\s+products?|vaccines?)\b"
    r"|\b(?:21\s*C\.?\s*F\.?\s*R\.?\s*(?:parts?\s*|§+\s*)?"
    r"(?:210|211)\b|(?:210|211)\.\d+\b)",
    re.IGNORECASE,
)
_STATUS_KEY = "fda483_text_status"
_BODY_KEY = "fda483_body_full"


def _fetch_rows(base: str, key: str, doc: str) -> list[dict]:
    return fsb._fetch_all_pages(
        base, key, "raw_signals",
        select="raw_signal_id,document_id,source,raw_json,row_json,raw_sha256",
        page_size=2, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "document_id": f"eq.{doc}"},
    )


def _text_for_comparison(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _review_row(entry: dict, base: str, key: str, *, apply: bool) -> tuple[str, str, str, str]:
    """Return before, after, outcome, detail for a single reviewed entry."""
    doc = entry["doc"]
    evidence = entry.get("evidence")
    category = entry.get("category")
    if not isinstance(evidence, str) or not evidence.strip():
        return "", "", "rejected", "missing evidence"
    if not isinstance(category, str) or not _CATEGORY.fullmatch(category):
        return "", "", "rejected", "invalid category"

    try:
        rows = _fetch_rows(base, key, doc)
    except RuntimeError:
        return "", "", "error", "raw_signals lookup failed"
    if len(rows) != 1:
        return "", "", "rejected", f"expected one FDA 483 row, found {len(rows)}"
    row = rows[0]
    if row.get("document_id") != doc or row.get("source") != SOURCE_FDA_483:
        return "", "", "rejected", "raw_signals row identity mismatch"
    try:
        raw = json.loads(row.get("raw_json"))
    except (TypeError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        return "", "", "rejected", "invalid raw_json"

    before = str(raw.get(_STATUS_KEY) or "")
    if before == OUT_OF_SCOPE_STATUS:
        return before, before, "skipped", "already out-of-scope"

    quote = _text_for_comparison(evidence)
    body = raw.get(_BODY_KEY)
    full_text = _text_for_comparison(body) if isinstance(body, str) else ""
    for label, text in (("evidence", quote), ("full text", full_text)):
        # The FDA's name appears on the form for every establishment type.
        clue = _DRUG_CLUE.search(_FDA_NAME.sub(" ", text))
        if clue:
            return before, before, "rejected", f"drug clue in {label}: {clue.group()}"
    if not full_text:
        return before, before, "rejected", f"missing full text ({_BODY_KEY})"
    if not quote or quote not in full_text:
        return before, before, "rejected", "evidence quote not found in full text"
    if not all(isinstance(row.get(field), str) and row[field].strip()
               for field in ("raw_signal_id", "raw_sha256", "row_json")):
        return before, before, "rejected", "missing raw_signal_id, raw_sha256 or row_json"

    if not apply:
        return before, OUT_OF_SCOPE_STATUS, "would_apply", f"{category}: {evidence}"
    try:
        result = rv.replace_raw(
            base, key, row, {**raw, _STATUS_KEY: OUT_OF_SCOPE_STATUS},
            change_kind="repair", changed_by=rv.run_label("mark_483_out_of_scope"),
            diff_summary={"reason": "483_status_marker", "category": category, "evidence": evidence},
        )
    except rv.ApplyVersionError as exc:
        return before, "", "error", f"version write: {exc.status}"
    return before, OUT_OF_SCOPE_STATUS, result["status"], f"{category}: {evidence}"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 JSON array with doc, evidence and category")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Preview changes (default)")
    modes.add_argument("--apply", action="store_true", help="Write versions after human approval")
    parser.add_argument("--supabase-url")
    parser.add_argument("--service-role-key")
    args = parser.parse_args(argv)

    try:
        entries = json.loads(args.input.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        parser.error(f"could not read input JSON: {exc}")
    if not isinstance(entries, list):
        parser.error("input must be a JSON array")

    docs = [entry.get("doc") if isinstance(entry, dict) else None for entry in entries]
    counts = Counter(doc for doc in docs if isinstance(doc, str) and _DOC_ID.fullmatch(doc))
    base = key = ""
    if any(count == 1 for count in counts.values()):
        credentials = resolve_supabase_service_credentials(args)
        if not credentials:
            parser.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        base = normalize_supabase_url(credentials[0])
        if not base:
            parser.error("SUPABASE_URL must start with https://")
        key = credentials[1]

    table = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    table.writerow(("line", "doc", "before", "after", "outcome", "detail"))
    failed = False
    for number, (entry, doc) in enumerate(zip(entries, docs), start=1):
        if not isinstance(doc, str) or not _DOC_ID.fullmatch(doc):
            result = "", "", "rejected", "expected doc in fda483-<number> format"
        elif counts[doc] > 1:
            result = "", "", "rejected", "duplicate document in input"
        else:
            result = _review_row(entry, base, key, apply=args.apply)
        table.writerow((number, doc, *result))
        failed |= result[2] in ("rejected", "error")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
