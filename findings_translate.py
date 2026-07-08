#!/usr/bin/env python3
"""FIND-1 M6b/M8a findings translation export/apply tool.

Operational status (M8a): the SQLite sidecar is a July backfill snapshot plus
a local-dev convenience copy -- the system of record for newly ingested
findings is the live Supabase database (see --source below).

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

--source {sqlite,supabase} (default: sqlite) selects where findings are read
from. supabase mode talks to the live database anon-key, read-only (RLS) via
PostgREST: --export issues GET requests and --apply validates against a live
GET snapshot, but there is no live write path -- --apply --source supabase
requires --sql-output (the resulting SQL is applied by a human via the
Supabase SQL Editor) and rejects --write-file with exit 2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

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

# Supabase(PostgREST) export selects finding_text_ko/translation_method directly
# (the eq.'' filter guarantees both are empty strings on every returned row),
# instead of the SQLite path's local `_fetch_untranslated`, which stamps them
# onto each item in Python after a plain SELECT.
_EXPORT_COLUMNS_SUPABASE = _EXPORT_COLUMNS + ("finding_text_ko", "translation_method")

_SUPABASE_HTTP_TIMEOUT_SECONDS = 15
_SUPABASE_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_SUPABASE_EXPORT_LIMIT = 1000
_SUPABASE_VALIDATE_BATCH_SIZE = 20  # finding_id=in.(...) batch size (URL length defense)


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
# Mode A2: --source supabase (read-only, live PostgREST via anon key)
# ---------------------------------------------------------------------------


def _normalize_supabase_url(base_url: str) -> str | None:
    text = str(base_url or "").strip()
    if not text.lower().startswith("https://"):
        return None
    return text.rstrip("/")


def _header_ci(headers: dict[str, Any], name: str) -> str:
    """Case-insensitive header lookup (requests' CaseInsensitiveDict already is
    one, but callers/tests may pass a plain dict)."""
    name_lower = name.lower()
    for key, value in headers.items():
        if str(key).lower() == name_lower:
            return str(value)
    return ""


def _supabase_get(
    base_url: str,
    anon_key: str,
    path: str,
    params: dict[str, str],
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: int = _SUPABASE_HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any, dict[str, Any], str]:
    """GET one PostgREST resource with the (public) anon key.

    Returns (status_code, parsed_json_or_None, response_headers, error_summary).
    error_summary is "" on 2xx, else "timeout", an exception type name, or
    "http_{status}". Retries once for 5xx responses or a request timeout
    (mirrors findings_supabase_append._post_rows' retry contract). The anon
    key is a public value by design but is still never included in
    error_summary or any exception text, by convention with the rest of this
    module's Supabase transport code.
    """
    url = f"{base_url}/rest/v1/{path}"
    headers = {"apikey": anon_key, "Authorization": f"Bearer {anon_key}"}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(1, _SUPABASE_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _SUPABASE_MAX_ATTEMPTS:
                continue
            return 0, None, {}, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, {}, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _SUPABASE_MAX_ATTEMPTS:
                continue
            return resp.status_code, None, dict(resp.headers), f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, dict(resp.headers), f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            data = None
        return resp.status_code, data, dict(resp.headers), ""

    return 0, None, {}, "retry_exhausted"  # unreachable safety net


def _fetch_untranslated_supabase(base_url: str, anon_key: str) -> tuple[list[dict[str, Any]], str]:
    """Read-only PostgREST export of untranslated findings. Returns (items, error)."""
    status, data, _headers, err = _supabase_get(
        base_url,
        anon_key,
        "findings",
        params={
            "select": ",".join(_EXPORT_COLUMNS_SUPABASE),
            "finding_text_ko": "eq.",
            "order": "published_date.desc,finding_id.asc",
            "limit": str(_SUPABASE_EXPORT_LIMIT),
        },
    )
    if err:
        return [], err
    if not isinstance(data, list):
        return [], "invalid_response_shape"
    return [dict(row) for row in data], ""


def _fetch_findings_total_supabase(base_url: str, anon_key: str) -> int:
    """Total row count of public.findings via Content-Range (Prefer: count=exact).

    Returns -1 (never raises) if the count cannot be determined -- the report
    surfaces this explicitly rather than failing the whole export over a
    count-only signal.
    """
    _status, _data, headers, err = _supabase_get(
        base_url,
        anon_key,
        "findings",
        params={"select": "finding_id", "limit": "1"},
        extra_headers={"Prefer": "count=exact"},
    )
    if err:
        return -1
    content_range = _header_ci(headers, "Content-Range")
    if "/" not in content_range:
        return -1
    total_part = content_range.rsplit("/", 1)[-1]
    try:
        return int(total_part)
    except ValueError:
        return -1


def build_translation_plan_supabase(base_url: str, anon_key: str) -> dict[str, Any]:
    """Extract untranslated findings from the live Supabase database (read-only).

    Raises ValueError on a malformed base_url or a network/HTTP failure -- callers
    (the CLI) turn that into an exit 2 with the error summary, never a stack trace
    that could echo the anon key.
    """
    base = _normalize_supabase_url(base_url)
    if base is None:
        raise ValueError("findings_translate: --supabase-url must start with https://")

    items, err = _fetch_untranslated_supabase(base, anon_key)
    if err:
        raise ValueError(f"findings_translate: supabase export failed: {err}")

    findings_total = _fetch_findings_total_supabase(base, anon_key)
    host = urlsplit(base).netloc or base

    plan: dict[str, Any] = {
        "schema_version": TRANSLATION_PLAN_SCHEMA_VERSION,
        "source_db": {
            "file_name": f"supabase:{host}",
            "findings_total": findings_total,
            "untranslated": len(items),
        },
        "items": items,
    }
    if findings_total == -1:
        # Additive field: the exact-count probe failed; the export itself is intact.
        plan["count_unavailable"] = True
    if len(items) == _SUPABASE_EXPORT_LIMIT:
        plan["truncated_possible"] = True
    return plan


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


def _fetch_live_rows_for_ids_supabase(
    base_url: str, anon_key: str, finding_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Fetch finding_id/finding_text/finding_text_ko for a set of ids, batched.

    Batches at _SUPABASE_VALIDATE_BATCH_SIZE ids per `finding_id=in.(...)` GET as
    a URL-length defense. Returns (rows_by_finding_id, error_summary); on error
    the dict is always empty so a caller can't accidentally validate against a
    partial snapshot.
    """
    rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(finding_ids), _SUPABASE_VALIDATE_BATCH_SIZE):
        batch = finding_ids[start : start + _SUPABASE_VALIDATE_BATCH_SIZE]
        if not batch:
            continue
        status, data, _headers, err = _supabase_get(
            base_url,
            anon_key,
            "findings",
            params={
                "select": "finding_id,finding_text,finding_text_ko",
                "finding_id": "in.(" + ",".join(batch) + ")",
            },
        )
        if err:
            return {}, err
        if not isinstance(data, list):
            return {}, "invalid_response_shape"
        for row in data:
            rows[str(row.get("finding_id") or "")] = dict(row)
    return rows, ""


def apply_translations_supabase(
    plan: dict[str, Any],
    base_url: str,
    anon_key: str,
    *,
    sql_output: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Validate a completed translation plan against the live Supabase database.

    There is no live write path in supabase mode -- RLS grants anon read-only.
    Validation reuses the exact same all-or-nothing rules as the sqlite --apply
    path (`_validate_item`), just sourced from a live GET snapshot instead of a
    local SQLite read. On success this only writes --sql-output (mode
    "sql_only"); a human applies that SQL through the Supabase SQL Editor.
    """
    base = _normalize_supabase_url(base_url)
    if base is None:
        raise ValueError("findings_translate: --supabase-url must start with https://")

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

    finding_ids = sorted({
        str(item.get("finding_id") or "")
        for item in items
        if isinstance(item, dict) and item.get("finding_id")
    })
    db_rows, fetch_err = _fetch_live_rows_for_ids_supabase(base, anon_key, finding_ids)
    if fetch_err:
        raise ValueError(f"findings_translate: supabase validation fetch failed: {fetch_err}")

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
            "mode": "sql_only",
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
        db_row = db_rows.get(finding_id, {})
        already_translated = bool(str(db_row.get("finding_text_ko") or "").strip())
        if already_translated and not overwrite:
            skipped += 1
            continue
        to_update.append(item)

    sql_output_path = str(Path(sql_output))
    _write_sql_file(sql_output_path, to_update)

    return {
        "schema_version": TRANSLATION_APPLY_SCHEMA_VERSION,
        "mode": "sql_only",
        "validated": validated,
        "updated": len(to_update),
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


def _resolve_supabase_credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.supabase_anon_key or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return url, key


def _emit(result: dict[str, Any], *, output: str | None, pretty: bool) -> None:
    if output:
        _write_json(output, result, pretty=pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M6b/M8a findings translation export/apply tool"
    )
    parser.add_argument(
        "--source",
        choices=("sqlite", "supabase"),
        default="sqlite",
        help="Findings source: local SQLite sidecar (default) or live Supabase via PostgREST "
        "(anon key, read-only)",
    )
    parser.add_argument(
        "--db-path", help="Path to the findings SQLite sidecar (--source sqlite only)"
    )
    parser.add_argument(
        "--supabase-url",
        help="Supabase project URL (--source supabase only; falls back to $SUPABASE_URL)",
    )
    parser.add_argument(
        "--supabase-anon-key",
        help="Supabase anon key (--source supabase only; falls back to $SUPABASE_ANON_KEY)",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export untranslated findings to a translation plan JSON (read-only)",
    )
    parser.add_argument(
        "--apply",
        metavar="TRANSLATIONS_JSON",
        help="Apply a completed translation plan JSON (guarded)",
    )
    parser.add_argument("--output", help="Plan/report JSON output path")
    parser.add_argument(
        "--write-file",
        action="store_true",
        help="Guard: commit UPDATEs to --db-path (omit for a dry-run; --apply --source sqlite "
        "only; always rejected for --source supabase)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow re-translating rows that already have a non-empty finding_text_ko",
    )
    parser.add_argument(
        "--sql-output",
        help="Path to write a one-shot live Postgres UPDATE SQL file (--apply only; required "
        "for --source supabase)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    if args.export and args.apply:
        print("findings_translate: use exactly one of --export or --apply", file=sys.stderr)
        return 2
    if not args.export and not args.apply:
        print("findings_translate: one of --export or --apply is required", file=sys.stderr)
        return 2

    if args.source == "supabase":
        creds = _resolve_supabase_credentials(args)
        if creds is None:
            print(
                "findings_translate: --source supabase requires --supabase-url/"
                "--supabase-anon-key or $SUPABASE_URL/$SUPABASE_ANON_KEY",
                file=sys.stderr,
            )
            return 2
        base_url, anon_key = creds

        if args.write_file:
            print(
                "findings_translate: --write-file is not supported for --source supabase "
                "(no live write path -- use --sql-output and apply it via the Supabase SQL "
                "Editor)",
                file=sys.stderr,
            )
            return 2

        if args.export:
            try:
                result = build_translation_plan_supabase(base_url, anon_key)
            except ValueError as exc:
                print(f"findings_translate: {exc}", file=sys.stderr)
                return 2
            _emit(result, output=args.output, pretty=args.pretty)
            return 0

        if not args.sql_output:
            print(
                "findings_translate: --sql-output is required for --apply --source supabase",
                file=sys.stderr,
            )
            return 2

        try:
            plan = _load_json(args.apply)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"findings_translate: {exc}", file=sys.stderr)
            return 2

        try:
            result = apply_translations_supabase(
                plan,
                base_url,
                anon_key,
                sql_output=args.sql_output,
                overwrite=args.overwrite,
            )
        except ValueError as exc:
            print(f"findings_translate: {exc}", file=sys.stderr)
            return 2

        _emit(result, output=args.output, pretty=args.pretty)
        if not result["ready"]:
            return 3
        return 0

    # --source sqlite (default) -- unchanged behavior.
    if not args.db_path:
        print("findings_translate: --db-path is required for --source sqlite", file=sys.stderr)
        return 2

    if args.export:
        try:
            result = build_translation_plan(args.db_path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"findings_translate: {exc}", file=sys.stderr)
            return 2

        _emit(result, output=args.output, pretty=args.pretty)
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

    _emit(result, output=args.output, pretty=args.pretty)

    if not result["ready"]:
        return 3
    return 0


__all__ = [
    "TRANSLATION_PLAN_SCHEMA_VERSION",
    "TRANSLATION_APPLY_SCHEMA_VERSION",
    "build_translation_plan",
    "apply_translations",
    "build_translation_plan_supabase",
    "apply_translations_supabase",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
