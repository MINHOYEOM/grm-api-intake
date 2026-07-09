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
under --outbox-dir and issues HTTPS PATCH requests. It never moves, renames,
or deletes outbox files, and never stages, commits, or pushes anything. PATCH
is idempotent by construction here (the WHERE-equivalent filter pins both
finding_id and the original finding_text), so leaving already-applied outbox
files in place is safe: re-running this script re-PATCHes them to the same
values (a harmless no-op write) or matches zero rows if finding_text no
longer matches live data (also harmless -- counted as matched_zero, not an
error). Outbox files therefore accumulate over time by design; that is a
deliberately out-of-scope, low-stakes piece of housekeeping (see the module
docstring companion notes in the M9b task writeup) rather than something this
script manages.

The service-role key is never included in any log line, exception message,
or report field -- only exception type names and HTTP status codes are
surfaced, mirroring findings_supabase_append.py's convention.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


DEFAULT_OUTBOX_DIR = "translations/outbox"
DEFAULT_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only

_REQUIRED_ITEM_KEYS = ("finding_id", "finding_text", "finding_text_ko", "translation_method")


def _normalize_base_url(base_url: str) -> str | None:
    text = str(base_url or "").strip()
    if not text.lower().startswith("https://"):
        return None
    return text.rstrip("/")


def _load_outbox_files(outbox_dir: str | Path) -> tuple[list[Path], list[str]]:
    """Return (sorted file paths, errors). Missing directory is not an error."""
    directory = Path(outbox_dir)
    if not directory.is_dir():
        return [], []
    paths = sorted(p for p in directory.glob("*.json") if p.is_file())
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


def _patch_finding(
    base_url: str,
    service_key: str,
    item: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """PATCH one findings row via PostgREST, filtered by finding_id + finding_text.

    Returns (status_code, returned_rows_or_None, error_summary). error_summary
    is "" on 2xx. On failure it is "timeout", an exception type name, or
    "http_{status}" -- never exception text, so the service-role key embedded
    in a lower-level transport error can never leak through it.

    Retries once (total 2 attempts) for 5xx responses or a request timeout.
    Any other exception or 4xx status fails immediately without retry.
    """
    finding_id = str(item.get("finding_id") or "")
    finding_text = str(item.get("finding_text") or "")
    url = f"{base_url}/rest/v1/findings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {
        "finding_id": f"eq.{finding_id}",
        "finding_text": f"eq.{finding_text}",
    }
    body = {
        "finding_text_ko": str(item.get("finding_text_ko") or ""),
        "translation_method": str(item.get("translation_method") or ""),
    }

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.patch(url, params=params, json=body, headers=headers, timeout=timeout)
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
            finding_id = str(item.get("finding_id") or "")

            if dry_run:
                report["items_succeeded"] += 1
                continue

            status, rows, err = _patch_finding(base, service_key, item)
            if err:
                report["items_errored"] += 1
                report["errors"].append(
                    f"{path.name}: finding_id={finding_id} PATCH failed ({err})"
                )
                continue

            matched = len(rows or [])
            if matched == 0:
                # 0 rows matched -- either already applied (finding_text_ko
                # already equals the desired value from a prior run) or the
                # live finding_text no longer byte-matches the outbox item
                # (source row changed/removed since the batch was built).
                # Both are TOCTOU-safe no-ops, not errors -- the outbox file
                # is left in place either way and this is not retried
                # specially; the next scheduled run naturally revisits it.
                report["items_matched_zero"] += 1
            elif matched == 1:
                report["items_succeeded"] += 1
            else:
                # >1 rows matched: finding_id should be unique -- this signals
                # a data-integrity anomaly, not the expected idempotent
                # no-op/success cases above. Counted as an error; the outbox
                # file is left in place unchanged either way.
                report["items_errored"] += 1
                report["errors"].append(
                    f"{path.name}: finding_id={finding_id} PATCH matched {matched} rows "
                    "(expected 0 or 1)"
                )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        return None
    return url, key


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
