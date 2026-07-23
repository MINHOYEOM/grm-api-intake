#!/usr/bin/env python3
"""FIND-1 M9b -- unattended CI apply service for queued translation batches.

This script is the second half of the FIND-1 translation automation pipeline
(see findings_translate.py's module docstring for the first half):

  M9c (not this file): a weekly, subscription-usage Claude Code session
      exports untranslated findings, translates them, validates the result
      with findings_translate.py --apply --source supabase --outbox-output,
      and commits the resulting outbox batch JSON under translations/outbox/
      via a PR that gets auto-merged into main.

  M9b (this file): a GitHub Actions workflow triggered by that merge runs
      this script, which is pure, deterministic Python -- no LLM, no
      judgment calls -- to PATCH each outbox item onto the live
      public.findings table using the Supabase service-role key (the same
      RLS-bypass mechanism the nightly M4 ingestion already uses safely).

This module performs no git operations whatsoever -- it only reads files
under --outbox-dir and issues HTTPS GET/PATCH requests. It never moves,
renames, or deletes outbox files, and never stages, commits, or pushes
anything. Each item is applied read-before-write, keyed on finding_id (the
findings primary key, web/migrations/002_findings.sql): a short GET fetches
the live finding_text, it is compared byte-for-byte against the outbox item
in-process, and only on a match is a PATCH issued -- also keyed on finding_id
only. This keeps the apply idempotent (re-running re-PATCHes an already-
applied row to the same values, a harmless no-op) and TOCTOU-safe (if the
source finding_text changed since the batch was built the comparison fails
and nothing is written -- counted as matched_zero, not an error), exactly as
the previous single-PATCH design did.

The reason the finding_text equality is a client-side comparison rather than
a query-string filter (as it was before 2026-07-23): finding_text can be up
to 30,000 characters (FDA warning-letter full text). Carrying it in the PATCH
URL as `finding_text=eq.<30k chars>` produced a request URI of ~35-45 KB,
past the Supabase edge's ~32 KB URL limit, which returned a bare-text HTTP
400 "Bad Request" before the request ever reached PostgREST. Under the M13b
policy that made the run go red -- and because the request was rejected the
Korean text was never written, so the four longest findings in a batch stayed
untranslated and re-failed on every run. Keying every URL on finding_id (a
~32-char PK) removes finding_text from the wire path entirely.

Leaving already-applied outbox files in place is *correct* but not *free*:
re-running re-issues the GET/PATCH pair per item. The cost is wall-clock: the
CI workflow has a 10-minute timeout, and on 2026-07-13 dozens of accumulated
already-applied batches pushed every run past it, starving the newest batch.
Two mitigations exist: (1) this script processes outbox files newest-first
(see _load_outbox_files), and (2) the M9c translation session archives
already-applied batches from translations/outbox/ to translations/applied/
in the same PR that adds a new batch, so the outbox normally holds only the
batch(es) not yet confirmed applied.

The service-role key is never included in any log line, exception message,
or report field -- only exception type names and HTTP status codes are
surfaced, mirroring findings_supabase_append.py's convention.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


DEFAULT_OUTBOX_DIR = "translations/outbox"
DEFAULT_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only

_REQUIRED_ITEM_KEYS = ("finding_id", "finding_text", "finding_text_ko", "translation_method")


def _load_outbox_files(outbox_dir: str | Path) -> tuple[list[Path], list[str]]:
    """Return (newest-first file paths, errors). Missing directory is not an error.

    Outbox file names are date-prefixed (e.g. 2026-07-15-batch.json), so reverse
    lexicographic order processes the newest batch first. This is a starvation
    defense: the workflow has a hard 10-minute timeout, and if applied batches
    ever accumulate in the outbox again (2026-07-13 incident: 42 stale files
    re-PATCHed on every run pushed the newest batch past the timeout, so merged
    translations never reached Supabase), the newest — i.e. the only not-yet-
    applied — batch still lands before the deadline.
    """
    directory = Path(outbox_dir)
    if not directory.is_dir():
        return [], []
    paths = sorted((p for p in directory.glob("*.json") if p.is_file()), reverse=True)
    return paths, []


def _parse_outbox_file(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Parse one outbox file into a list of item dicts. Returns (items, error)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], type(exc).__name__

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], "invalid_json"

    if not isinstance(data, list):
        return [], "root_not_a_list"

    items: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not all(key in entry for key in _REQUIRED_ITEM_KEYS):
            continue
        items.append(entry)
    return items, ""


def _send_findings_request(
    verb: str,
    base_url: str,
    service_key: str,
    *,
    params: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """Issue one GET or PATCH to /rest/v1/findings and return
    (status_code, rows_or_None, error_summary).

    error_summary is "" on 2xx. On failure it is "timeout", an exception type
    name, or "http_{status}" -- never exception text, so the service-role key
    embedded in a lower-level transport error can never leak through it. On
    2xx, rows is the decoded JSON array (or [] if the body is absent/not a
    list).

    Retries once (total 2 attempts) for 5xx responses or a request timeout.
    Any other exception or 4xx status fails immediately without retry. `verb`
    is resolved to requests.get/requests.patch at call time so test doubles
    patched onto the module's `requests` are honoured.
    """
    request_fn = requests.get if verb == "get" else requests.patch
    url = f"{base_url}/rest/v1/findings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = request_fn(
                url, params=params, json=json_body, headers=headers, timeout=timeout
            )
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return resp.status_code, [], ""
        return resp.status_code, (data if isinstance(data, list) else []), ""

    return 0, None, "retry_exhausted"  # unreachable safety net


def _apply_one_finding(
    base_url: str,
    service_key: str,
    item: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Read-before-write apply of one outbox item, keyed on finding_id only.

    Returns (outcome, error_detail) where outcome is one of "succeeded",
    "matched_zero", or "errored" -- the same three-way accounting the previous
    single-PATCH design produced. No request URL ever carries finding_text
    (see the module docstring: it can be 30k chars and blew past the ~32 KB
    edge URL limit). Instead a short GET fetches the live finding_text by
    primary key, it is compared in-process, and only a still-matching row is
    PATCHed by primary key. error_detail carries no service-role key -- only
    finding_id, HTTP codes, exception type names, and row counts.
    """
    finding_id = str(item.get("finding_id") or "")
    finding_text = str(item.get("finding_text") or "")

    # 1) Fetch the live row by primary key (short URL). finding_id is the
    #    findings PK, so this returns 0 or 1 rows.
    _status, rows, err = _send_findings_request(
        "get",
        base_url,
        service_key,
        params={"finding_id": f"eq.{finding_id}", "select": "finding_text"},
        timeout=timeout,
    )
    if err:
        return "errored", f"finding_id={finding_id} GET failed ({err})"

    # Only a row whose live finding_text still byte-matches the outbox item is
    # eligible -- this reproduces the original finding_id+finding_text eq
    # filter as an in-process comparison instead of a URL predicate.
    live_matches = [
        r for r in (rows or []) if str(r.get("finding_text") or "") == finding_text
    ]
    if not live_matches:
        # finding_id absent, or the source finding_text changed since the batch
        # was built -- a TOCTOU-safe no-op, counted as matched_zero exactly as
        # the previous eq-filter-matches-zero case.
        return "matched_zero", ""
    if len(live_matches) > 1:
        # Impossible under the finding_id primary key, but keep the anomaly
        # guard rather than silently over-writing.
        return (
            "errored",
            f"finding_id={finding_id} live rows matched {len(live_matches)} (expected 0 or 1)",
        )

    # 2) Write by primary key only (short URL). return=representation lets us
    #    confirm exactly one row was updated.
    _status, prows, perr = _send_findings_request(
        "patch",
        base_url,
        service_key,
        params={"finding_id": f"eq.{finding_id}"},
        json_body={
            "finding_text_ko": str(item.get("finding_text_ko") or ""),
            "translation_method": str(item.get("translation_method") or ""),
        },
        timeout=timeout,
    )
    if perr:
        return "errored", f"finding_id={finding_id} PATCH failed ({perr})"

    matched = len(prows or [])
    if matched == 0:
        # Row changed/removed between the GET and the PATCH -- a TOCTOU-safe
        # no-op, not an error.
        return "matched_zero", ""
    if matched == 1:
        return "succeeded", ""
    return (
        "errored",
        f"finding_id={finding_id} PATCH matched {matched} rows (expected 0 or 1)",
    )


def apply_outbox(
    outbox_dir: str | Path,
    base_url: str,
    service_key: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Read every *.json batch under outbox_dir and PATCH each item to Supabase.

    Pure orchestration: no git operations, no file mutation of outbox_dir
    (files are read-only inputs and are never moved/deleted/rewritten). In
    dry_run mode files are still read and counted but no PATCH request is
    issued.
    """
    base = _normalize_base_url(base_url)
    report: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "apply",
        "files_scanned": 0,
        "items_total": 0,
        "items_succeeded": 0,
        "items_matched_zero": 0,
        "items_errored": 0,
        "errors": [],
    }
    if base is None:
        report["errors"].append("SUPABASE_URL must start with https://")
        return report

    paths, _load_errors = _load_outbox_files(outbox_dir)
    report["files_scanned"] = len(paths)

    for path in paths:
        items, parse_err = _parse_outbox_file(path)
        if parse_err:
            report["items_errored"] += 1
            report["errors"].append(f"{path.name}: failed to parse outbox file ({parse_err})")
            continue

        for item in items:
            report["items_total"] += 1

            if dry_run:
                report["items_succeeded"] += 1
                continue

            # Read-before-write apply keyed on finding_id only (see
            # _apply_one_finding). The three outcomes map 1:1 onto the report
            # counters the previous single-PATCH design produced:
            #   - matched_zero: already-applied no-op, or the live
            #     finding_text no longer matches the outbox item (source row
            #     changed/removed since the batch was built). Both are
            #     TOCTOU-safe -- the outbox file is left in place and the next
            #     scheduled run naturally revisits it.
            #   - errored: a transport/HTTP failure or an anomalous row count.
            #   - succeeded: exactly one row updated.
            outcome, detail = _apply_one_finding(base, service_key, item)
            if outcome == "succeeded":
                report["items_succeeded"] += 1
            elif outcome == "matched_zero":
                report["items_matched_zero"] += 1
            else:
                report["items_errored"] += 1
                report["errors"].append(f"{path.name}: {detail}")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M9b -- unattended CI apply service for queued translation "
        "outbox batches (no git operations; PATCH only)"
    )
    parser.add_argument(
        "--outbox-dir",
        default=DEFAULT_OUTBOX_DIR,
        help=f"Directory of translation outbox batch JSON files (default: {DEFAULT_OUTBOX_DIR})",
    )
    parser.add_argument(
        "--supabase-url",
        help="Supabase project URL (falls back to $SUPABASE_URL)",
    )
    parser.add_argument(
        "--service-role-key",
        help="Supabase service-role key (falls back to $SUPABASE_SERVICE_ROLE_KEY)",
    )
    parser.add_argument("--output", help="Report JSON output path (default: stdout)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and count outbox items without issuing any PATCH requests",
    )
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_translate_apply_service: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = apply_outbox(args.outbox_dir, base_url, service_key, dry_run=args.dry_run)
    # Report JSON is always printed/written before we decide the exit code --
    # even a red (exit 1) run leaves the report behind for the workflow's
    # step summary to surface.
    _write_report(args.output, report)

    # FIND-1 M13b: PATCH is still idempotent and outbox files are still never
    # removed, so a future scheduled run naturally retries anything that
    # failed this time -- that retry design is unchanged. What changes here
    # is *visibility*: retryability and CI gating are orthogonal, so errors
    # (items_errored, e.g. failed PATCHes/parses/anomalous row counts, or any
    # other entry landing in report["errors"], such as a malformed
    # SUPABASE_URL) now surface as exit 1 instead of a silently-green run
    # that only a human reading the report JSON would ever notice. An empty
    # outbox (0 items processed) is a normal steady state, not an error, and
    # keeps exiting 0.
    if report["errors"]:
        return 1
    return 0


__all__ = ["apply_outbox", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
