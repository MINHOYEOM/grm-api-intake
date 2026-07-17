#!/usr/bin/env python3
"""FIND-1 M12 -- offline backfill of findings for raw_signals that already live
in Supabase but never got their findings extracted/inserted.

Background: FIND-1 M4's automatic findings append only runs inside
collect_intake's insert_items, for raw_signals that a given run newly
collects. A raw_signal that already exists in Supabase (document_id dedup
skips it on later runs) never gets revisited, so if the findings-append
feature flag was off, or the extractor code hadn't shipped yet, at the time a
raw_signal was first collected, its findings are permanently missing unless
something explicitly backfills them. This module is that "something" --
a reusable, deterministic (no LLM) backfill path, not a one-off SQL script.

This module is pure transport + composition of the existing FIND-1 layers:
  - findings_extractors.findings_from_raw_signal   (record -> findings, incl. M11 WL fan-out)
  - findings_supabase_append._append_findings_batch (findings -> POST, idempotent 409 fallback)
  - findings_supabase_append._finding_payload / _normalize_base_url / _post_rows / DEFAULT_TIMEOUT_SECONDS

It does not read/write SQLite, Notion, or collect_intake, and it never
constructs or re-POSTs raw_signals -- every raw_signal this module touches
already exists in Supabase (that's the whole premise of a backfill), so only
`findings` rows are ever inserted.

Translation is explicitly out of scope: backfilled findings are inserted
untranslated (finding_text_ko/translation_method left at their extractor
defaults), matching the M9 weekly translation loop's expectations -- the
public-facing RLS gate (M9) keeps untranslated rows out of the website until
the normal weekly translation pass fills in finding_text_ko later.

The service-role key is never included in any log line, exception message,
or report field -- only exception type names and HTTP status codes are
surfaced, mirroring findings_supabase_append.py's and
findings_translate_apply_service.py's convention.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

import findings_extractors
import findings_supabase_append as fsa
from grm_cli import header_ci as _header_ci
from grm_cli import parse_content_range as _parse_content_range
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


DEFAULT_TIMEOUT_SECONDS = fsa.DEFAULT_TIMEOUT_SECONDS
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_DEFAULT_PAGE_SIZE = 1000


@dataclass(frozen=True)
class BackfillReport:
    raw_scanned: int = 0
    unbackfilled: int = 0
    with_findings: int = 0
    findings_extracted: int = 0
    findings_inserted: int = 0
    findings_duplicate: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _normalize_base_url(base_url: str) -> str | None:
    return fsa._normalize_base_url(base_url)


def _get_page(
    base_url: str,
    service_key: str,
    table: str,
    *,
    select: str,
    offset: int,
    limit: int,
    order: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, dict[str, Any], str]:
    """GET one page of a PostgREST resource with the service-role key,
    paginated via the Range/Range-Unit headers (not limit/offset query
    params) so callers can read past PostgREST's default 1000-row cap.

    Returns (status_code, rows_or_None, response_headers, error_summary).
    error_summary is "" on 2xx, else "timeout", an exception type name, or
    "http_{status}" -- never exception text, so the service-role key
    embedded in a lower-level transport error can never leak through it.

    Retries once (total 2 attempts) for 5xx responses or a request timeout,
    mirroring findings_supabase_append._post_rows' retry contract.
    """
    url = f"{base_url}/rest/v1/{table}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Range-Unit": "items",
        "Range": f"{offset}-{offset + limit - 1}",
        "Prefer": "count=exact",
    }
    params: dict[str, str] = {"select": select}
    if order:
        params["order"] = order

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, {}, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, {}, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, {}, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, {}, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            data = []
        rows = data if isinstance(data, list) else []
        return resp.status_code, rows, dict(resp.headers), ""

    return 0, None, {}, "retry_exhausted"  # unreachable safety net


def _fetch_all_pages(
    base_url: str,
    service_key: str,
    table: str,
    *,
    select: str,
    page_size: int,
    order: str | None = None,
) -> list[dict[str, Any]]:
    """Page through a PostgREST resource to completion via Range headers.

    Raises RuntimeError (never the service key) if any page fails after
    retries. Stops when Content-Range reports the exact total has been
    reached, or -- if the total can't be determined -- when a page comes
    back shorter than page_size (the last-page signal).
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while True:
        _status, page_rows, headers, err = _get_page(
            base_url, service_key, table, select=select, offset=offset, limit=page_size, order=order,
        )
        if err:
            raise RuntimeError(f"findings_supabase_backfill: GET {table} failed ({err})")

        page_rows = page_rows or []
        rows.extend(page_rows)

        parsed_total = _parse_content_range(_header_ci(headers, "Content-Range"))
        if parsed_total is not None:
            total = parsed_total

        offset += page_size

        if not page_rows:
            break
        if total is not None:
            if offset >= total or len(rows) >= total:
                break
        elif len(page_rows) < page_size:
            break

    return rows


def fetch_raw_signals(
    base_url: str,
    service_key: str,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Fetch every raw_signals row (select=*), fully paginated. raw_json/
    row_json come back as text (they're text columns) -- callers pass the
    row straight into findings_extractors.findings_from_raw_signal, which
    parses them internally via its own _json_object helper.
    """
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_supabase_backfill: SUPABASE_URL must start with https://")
    return _fetch_all_pages(
        base, service_key, "raw_signals", select="*", page_size=page_size, order="raw_signal_id.asc",
    )


def fetch_existing_finding_raw_ids(
    base_url: str,
    service_key: str,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> set[str]:
    """Fetch the set of raw_signal_id values that already have >=1 finding."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_supabase_backfill: SUPABASE_URL must start with https://")
    rows = _fetch_all_pages(
        base, service_key, "findings", select="raw_signal_id", page_size=page_size, order="raw_signal_id.asc",
    )
    return {str(row.get("raw_signal_id") or "") for row in rows if row.get("raw_signal_id")}


def select_unbackfilled(
    raw_signals: list[dict[str, Any]],
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    """raw_signals whose raw_signal_id has no finding yet."""
    return [
        rs for rs in raw_signals
        if str(rs.get("raw_signal_id") or "") not in existing_ids
    ]


def plan_backfill(
    raw_signals: list[dict[str, Any]],
    existing_ids: set[str],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Extract findings for every unbackfilled raw_signal; keep only the
    (raw_signal, findings) pairs where >=1 finding was extracted (sources
    with no extractor coverage naturally yield 0 findings and are dropped
    here rather than attempted as a no-op append).
    """
    pairs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for raw_signal in select_unbackfilled(raw_signals, existing_ids):
        findings = findings_extractors.findings_from_raw_signal(raw_signal)
        if findings:
            pairs.append((raw_signal, findings))
    return pairs


def run_backfill(
    base_url: str,
    service_key: str,
    *,
    dry_run: bool,
    limit: int | None = None,
) -> BackfillReport:
    """Fetch raw_signals + existing findings, plan the backfill, and (unless
    dry_run) append the missing findings via
    findings_supabase_append._append_findings_batch -- which is idempotent
    (a 409/duplicate row is counted, not an error).

    raw_signal rows themselves are never re-POSTed here -- they already
    exist in Supabase; only their derived findings are appended.
    """
    base = _normalize_base_url(base_url)
    if base is None:
        return BackfillReport(errors=("SUPABASE_URL must start with https://",))

    try:
        raw_signals = fetch_raw_signals(base, service_key)
        existing_ids = fetch_existing_finding_raw_ids(base, service_key)
    except (RuntimeError, ValueError) as exc:
        return BackfillReport(errors=(str(exc),))

    unbackfilled = select_unbackfilled(raw_signals, existing_ids)
    if limit is not None and limit >= 0:
        unbackfilled = unbackfilled[:limit]

    pairs = plan_backfill(unbackfilled, set())
    with_findings = len(pairs)
    findings_extracted = sum(len(findings) for _rs, findings in pairs)

    if dry_run:
        return BackfillReport(
            raw_scanned=len(raw_signals),
            unbackfilled=len(unbackfilled),
            with_findings=with_findings,
            findings_extracted=findings_extracted,
            findings_inserted=0,
            findings_duplicate=0,
            errors=(),
        )

    findings_inserted = 0
    findings_duplicate = 0
    errors: list[str] = []
    for _raw_signal, findings in pairs:
        inserted, duplicate, _had_error, batch_errors = fsa._append_findings_batch(
            base, service_key, findings,
        )
        findings_inserted += inserted
        findings_duplicate += duplicate
        errors.extend(batch_errors)

    return BackfillReport(
        raw_scanned=len(raw_signals),
        unbackfilled=len(unbackfilled),
        with_findings=with_findings,
        findings_extracted=findings_extracted,
        findings_inserted=findings_inserted,
        findings_duplicate=findings_duplicate,
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M12 -- backfill findings for raw_signals already in Supabase "
        "that never got findings extracted/inserted (no LLM, no git writes; READ + "
        "findings POST only)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read and plan only -- never POST any findings.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N unbackfilled raw_signals (default: all).",
    )
    parser.add_argument(
        "--supabase-url",
        help="Supabase project URL (falls back to $SUPABASE_URL)",
    )
    parser.add_argument(
        "--service-role-key",
        help="Supabase service-role key (falls back to $SUPABASE_SERVICE_ROLE_KEY)",
    )
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_supabase_backfill: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = run_backfill(base_url, service_key, dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2))

    # Findings-level errors are intentionally NOT a failure exit -- appends
    # are idempotent (a duplicate 409 is not an error) and nothing here is
    # destructive, so a follow-up run naturally retries anything transient.
    # The report is the source of truth for operators; CI is not gated on it.
    return 0


__all__ = [
    "BackfillReport",
    "fetch_raw_signals",
    "fetch_existing_finding_raw_ids",
    "select_unbackfilled",
    "plan_backfill",
    "run_backfill",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
