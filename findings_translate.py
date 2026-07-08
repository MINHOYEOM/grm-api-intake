#!/usr/bin/env python3
"""FIND-1 M6b findings translation export/apply tool.

This module never generates translations itself -- an LLM-driven session does
that offline against the plan JSON this tool exports. This tool only performs
deterministic, guarded work:

  --export  read-only extraction of untranslated findings (finding_text_ko ==
            '') into a translation-plan JSON that a translator/LLM session
            fills in (finding_text_ko + translation_method per item).

  --apply   guarded, all-or-nothing validation and application of a completed
            plan back onto the SQLite sidecar, plus an optional one-shot
            Postgres UPDATE SQL file for the live database.

Validation is intentionally strict and all-or-nothing: if any item in the
plan fails validation, nothing is written -- not to the sidecar, and not to
--sql-output -- and the process exits 3. Sidecar writes additionally require
the explicit --write-file guard (mirroring the other guarded writers in this
repo); without it, --apply is always a dry-run that still fully validates and
still reports what would change.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import findings_views as views
import grm_findings as gf
from findings_supabase import pg_quote_text


TRANSLATION_PLAN_SCHEMA_VERSION = "grm-findings-translation-plan/v1"
TRANSLATION_APPLY_SCHEMA_VERSION = "grm-findings-translation-apply/v1"

# gf.TRANSLATION_METHODS includes '' (the "not yet translated" sentinel); a
# submitted translation must pick one of the two real methods.
_ALLOWED_TRANSLATION_METHODS = tuple(m for m in gf.TRANSLATION_METHODS if m)

_HANGUL_RE = re.compile(r"[가-힣]")

_EXPORT_COLUMNS = (
    "finding_id",
    "source",
    "agency",
    "category_code",
    "category_label_ko",
    "published_date",
    "firm_name",
    "finding_text",
)


# ---------------------------------------------------------------------------
# Mode A: --export (read-only)
# ---------------------------------------------------------------------------


def _fetch_untranslated(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    columns_sql = ", ".join(_EXPORT_COLUMNS)
    sql = (
        f"SELECT {columns_sql} FROM findings WHERE finding_text_ko = '' "
        "ORDER BY published_date DESC, finding_id ASC"
    )
    rows = conn.execute(sql).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["finding_text_ko"] = ""
        record["translation_method"] = ""
        items.append(record)
    return items


def build_translation_plan(db_path: str | Path) -> dict[str, Any]:
    """Extract untranslated findings into a translation-plan JSON (read-only)."""
    conn = views.open_findings_db_readonly(db_path)
    try:
        findings_total = int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0])
        items = _fetch_untranslated(conn)
    finally:
        conn.close()

    return {
        "schema_version": TRANSLATION_PLAN_SCHEMA_VERSION,
        "source_db": {
            "file_name": Path(db_path).name,
            "findings_total": findings_total,
            "untranslated": len(items),
        },
        "items": items,
    }


# ---------------------------------------------------------------------------
# Mode B: --apply (guarded)
# ---------------------------------------------------------------------------


def _validate_item(item: dict[str, Any], db_rows: dict[str, dict[str, Any]]) -> list[str]:
    """Validate one plan item against the live DB snapshot. Returns error strings."""
    finding_id = str(item.get("finding_id") or "")
    if not finding_id:
        return ["item missing finding_id"]

    db_row = db_rows.get(finding_id)
    if db_row is None:
        return [f"{finding_id}: finding_id not found in database"]

    errors: list[str] = []

    finding_text = str(item.get("finding_text") or "")
    db_finding_text = str(db_row.get("finding_text") or "")
    if finding_text != db_finding_text:
        errors.append(
            f"{finding_id}: finding_text does not byte-match the database "
            "(source text was altered)"
        )

    finding_text_ko = str(item.get("finding_text_ko") or "")
    translation_method = str(item.get("translation_method") or "")

    if not finding_text_ko.strip():
        errors.append(f"{finding_id}: finding_text_ko is empty")
    if translation_method not in _ALLOWED_TRANSLATION_METHODS:
        errors.append(
            f"{finding_id}: translation_method must be one of "
            f"{_ALLOWED_TRANSLATION_METHODS} (got {translation_method!r})"
        )
    if finding_text_ko.strip() and not _HANGUL_RE.search(finding_text_ko):
        errors.append(f"{finding_id}: finding_text_ko contains no Hangul characters")
    if finding_text_ko and finding_text_ko == finding_text:
        errors.append(
            f"{finding_id}: finding_text_ko is identical to finding_text (not translated)"
        )

    return errors


def _build_sql_text(items: list[dict[str, Any]]) -> str:
    lines = [
        "-- FIND-1 M6b live Postgres UPDATE plan for public.findings translation columns.",
        "-- Idempotent: each UPDATE's WHERE clause pins both finding_id and the original",
        "-- finding_text, so re-running this file after it has already applied matches zero",
        "-- rows per statement (safe no-op). Apply through the Supabase MCP or psql against",
        "-- the live database; this file makes no network connection on its own.",
    ]
    for item in items:
        lines.append(
            "update public.findings set finding_text_ko = {ko}, translation_method = {method} "
            "where finding_id = {fid} and finding_text = {text};".format(
                ko=pg_quote_text(str(item["finding_text_ko"])),
                method=pg_quote_text(str(item["translation_method"])),
                fid=pg_quote_text(str(item["finding_id"])),
                text=pg_quote_text(str(item["finding_text"])),
            )
        )
    lines.append("")
    lines.append("-- Verification (run after apply):")
    lines.append(
        "-- select count(*) as translated_count from public.findings "
        "where finding_text_ko != '';"
    )
    return "\n".join(lines) + "\n"


def _write_sql_file(path: str | Path, items: list[dict[str, Any]]) -> None:
    Path(path).write_text(_build_sql_text(items), encoding="utf-8")


def _write_updates(db_path: Path, items: list[dict[str, Any]]) -> int:
    """Commit UPDATEs to the live sidecar file inside one transaction (all or rollback)."""
    if not items:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        updated = 0
        for item in items:
            cursor = conn.execute(
                "UPDATE findings SET finding_text_ko = ?, translation_method = ? "
                "WHERE finding_id = ? AND finding_text = ?",
                (
                    str(item["finding_text_ko"]),
                    str(item["translation_method"]),
                    str(item["finding_id"]),
                    str(item["finding_text"]),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"{item.get('finding_id')}: update matched {cursor.rowcount} row(s) "
                    "(expected 1); rolling back all updates"
                )
            updated += 1
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_translations(
    plan: dict[str, Any],
    db_path: str | Path,
    *,
    write_file: bool = False,
    overwrite: bool = False,
    sql_output: str | Path | None = None,
) -> dict[str, Any]:
    """Validate then (optionally) apply a completed translation plan to the sidecar.

    Validation is all-or-nothing across every item in the plan: if any item
    fails, nothing is written anywhere (not the sidecar, not --sql-output) and
    the returned report has ready=False. Rows that already carry a non-empty
    finding_text_ko are skipped unless overwrite=True (translation overwrite
    is opt-in). Without write_file=True the sidecar is never touched -- the
    report still reflects what a real apply would do (validated/updated/
    skipped counts), and --sql-output (if given) is still written, since it
    never touches the sidecar file either.
    """
    path = Path(db_path)
    if not path.is_file():
        raise ValueError(f"findings_translate: database file not found: {path}")

    items = plan.get("items")
    if not isinstance(items, list):
        raise ValueError("plan.items must be a list")

    errors: list[str] = []
    schema_version = plan.get("schema_version")
    if schema_version != TRANSLATION_PLAN_SCHEMA_VERSION:
        errors.append(
            "schema_version mismatch: expected "
            f"{TRANSLATION_PLAN_SCHEMA_VERSION!r}, got {schema_version!r}"
        )

    conn = views.open_findings_db_readonly(path)
    try:
        db_rows = {
            str(row["finding_id"]): dict(row)
            for row in conn.execute(
                "SELECT finding_id, finding_text, finding_text_ko FROM findings"
            ).fetchall()
        }
    finally:
        conn.close()

    seen_ids: set[str] = set()
    valid_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            errors.append("item is not an object")
            continue
        finding_id = str(item.get("finding_id") or "")
        item_errors = _validate_item(item, db_rows)
        if finding_id and finding_id in seen_ids:
            item_errors.append(f"{finding_id}: duplicate finding_id in plan")
        if finding_id:
            seen_ids.add(finding_id)
        if item_errors:
            errors.extend(item_errors)
        else:
            valid_items.append(item)

    validated = len(items)

    if errors:
        return {
            "schema_version": TRANSLATION_APPLY_SCHEMA_VERSION,
            "mode": "dry_run",
            "validated": validated,
            "updated": 0,
            "skipped_already_translated": 0,
            "errors": errors,
            "sql_output_path": "",
            "ready": False,
        }

    to_update: list[dict[str, Any]] = []
    skipped = 0
    for item in valid_items:
        finding_id = str(item["finding_id"])
        db_row = db_rows[finding_id]
        already_translated = bool(str(db_row.get("finding_text_ko") or "").strip())
        if already_translated and not overwrite:
            skipped += 1
            continue
        to_update.append(item)

    sql_output_path = ""
    if sql_output:
        sql_output_path = str(Path(sql_output))
        _write_sql_file(sql_output_path, to_update)

    if not write_file:
        return {
            "schema_version": TRANSLATION_APPLY_SCHEMA_VERSION,
            "mode": "dry_run",
            "validated": validated,
            "updated": len(to_update),
            "skipped_already_translated": skipped,
            "errors": [],
            "sql_output_path": sql_output_path,
            "ready": True,
        }

    updated = _write_updates(path, to_update)

    return {
        "schema_version": TRANSLATION_APPLY_SCHEMA_VERSION,
        "mode": "file_write",
        "validated": validated,
        "updated": updated,
        "skipped_already_translated": skipped,
        "errors": [],
        "sql_output_path": sql_output_path,
        "ready": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return data


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
        description="FIND-1 M6b findings translation export/apply tool"
    )
    parser.add_argument("--db-path", required=True, help="Path to the findings SQLite sidecar")
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export untranslated findings to a translation plan JSON (read-only)",
    )
    parser.add_argument(
        "--apply",
        metavar="TRANSLATIONS_JSON",
        help="Apply a completed translation plan JSON to the sidecar (guarded)",
    )
    parser.add_argument("--output", help="Plan/report JSON output path")
    parser.add_argument(
        "--write-file",
        action="store_true",
        help="Guard: commit UPDATEs to --db-path (omit for a dry-run; --apply only)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow re-translating rows that already have a non-empty finding_text_ko",
    )
    parser.add_argument(
        "--sql-output",
        help="Optional path to write a one-shot live Postgres UPDATE SQL file (--apply only)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    if args.export and args.apply:
        print("findings_translate: use exactly one of --export or --apply", file=sys.stderr)
        return 2
    if not args.export and not args.apply:
        print("findings_translate: one of --export or --apply is required", file=sys.stderr)
        return 2

    if args.export:
        try:
            result = build_translation_plan(args.db_path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"findings_translate: {exc}", file=sys.stderr)
            return 2

        if args.output:
            _write_json(args.output, result, pretty=args.pretty)
        else:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
        return 0

    try:
        plan = _load_json(args.apply)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"findings_translate: {exc}", file=sys.stderr)
        return 2

    try:
        result = apply_translations(
            plan,
            args.db_path,
            write_file=args.write_file,
            overwrite=args.overwrite,
            sql_output=args.sql_output,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"findings_translate: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))

    if not result["ready"]:
        return 3
    return 0


__all__ = [
    "TRANSLATION_PLAN_SCHEMA_VERSION",
    "TRANSLATION_APPLY_SCHEMA_VERSION",
    "build_translation_plan",
    "apply_translations",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
