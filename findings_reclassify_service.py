#!/usr/bin/env python3
"""grm-finding-taxonomy -- unattended CI reclassification service for the live
findings table.

Background: the 2026-07-12 classification audit (archive/findings_classification_
audit_2026-07-12.md) found `classify_finding_category()` (v2) had a real-world
accuracy of 71% (25/100 wrong in a stratified sample). grm_findings.py v3 fixed
the identified structural bugs, and the v3 sochu 재감사(archive/findings_
classification_audit_v3_2026-07-12.md, 실질 정확도 89%) drove a v4 revision
addressing the remaining wrong 9 cases (see grm_findings.TAXONOMY_VERSION change
log for both). This module is intentionally version-agnostic -- it always calls
whatever `gf.classify_finding_category()`/`gf.TAXONOMY_VERSION` currently is, so
the v3->v4 upgrade required no code change here, only a re-dispatch of the
existing workflow.

Rows already live in Supabase were classified under an older taxonomy version and
never get revisited by the normal ingestion path (findings are written once, at
collection time) -- something has to walk the live table and re-run the current
classifier against each row's already-stored `finding_text` to bring
`category_code`/`category_label_ko`/`taxonomy_version` up to date.

This module is that "something". It follows the M9/M12 security model this
repo already established for unattended reclassification-style jobs
(findings_translate_apply_service.py, findings_supabase_backfill.py):

  - No LLM, no judgment calls -- classify_finding_category() is pure,
    deterministic Python. This script is pure transport + composition around
    it.
  - Reads findings via PostgREST with the service-role key (RLS bypass, same
    mechanism the nightly M4 ingestion and M12 backfill already use safely),
    paginated via findings_supabase_backfill's Range-header helper.
  - PATCHes only rows whose category_code would change OR whose
    taxonomy_version is not yet the current gf.TAXONOMY_VERSION -- a rerun
    with nothing pending issues zero PATCH calls (idempotent by construction,
    not by chance).
  - finding_text, finding_text_ko, and scope_status are never read for
    mutation and never appear in a PATCH body -- only category_code,
    category_label_ko, and taxonomy_version are ever written.
  - This module performs no git operations whatsoever.

The service-role key is never included in any log line, exception message, or
report field -- only exception type names and HTTP status codes are surfaced,
mirroring findings_supabase_append.py's convention.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

import requests

import findings_supabase_backfill as fsb
import grm_findings as gf
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_DEFAULT_PAGE_SIZE = 1000

_SELECT_COLUMNS = "finding_id,finding_text,category_code,category_label_ko,taxonomy_version"


def _normalize_base_url(base_url: str) -> str | None:
    return fsb._normalize_base_url(base_url)


def fetch_findings_for_reclassification(
    base_url: str,
    service_key: str,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Fetch every findings row's finding_id/finding_text/category_code/
    category_label_ko/taxonomy_version, fully paginated. finding_text is read
    only to feed classify_finding_category() -- it is never included in a
    PATCH body and never mutated.
    """
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_reclassify_service: SUPABASE_URL must start with https://")
    return fsb._fetch_all_pages(
        base, service_key, "findings",
        select=_SELECT_COLUMNS, page_size=page_size, order="finding_id.asc",
    )


def plan_reclassification(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of rows whose category_code would change under the
    current classifier, or whose taxonomy_version is not yet current --
    each entry carries the (finding_id, old/new category, old taxonomy_version)
    needed to build the PATCH request and the report's migration matrix.

    Rows already at (current category_code, gf.TAXONOMY_VERSION) are excluded
    entirely -- this is what makes a rerun with nothing pending a true no-op
    (0 PATCH calls issued), not merely a report that says "nothing changed".
    """
    plan: list[dict[str, Any]] = []
    for row in rows:
        finding_id = str(row.get("finding_id") or "")
        text = str(row.get("finding_text") or "")
        old_category = str(row.get("category_code") or "")
        old_taxonomy_version = str(row.get("taxonomy_version") or "")
        new_category = gf.classify_finding_category(text)

        if new_category == old_category and old_taxonomy_version == gf.TAXONOMY_VERSION:
            continue

        plan.append({
            "finding_id": finding_id,
            "old_category": old_category,
            "new_category": new_category,
            "old_taxonomy_version": old_taxonomy_version,
        })
    return plan


def _category_migration_matrix(plan: list[dict[str, Any]]) -> dict[str, int]:
    """old_category -> new_category counts, only for rows whose category
    actually changes (excludes pure taxonomy_version version-stamp bumps where
    the category itself was already correct)."""
    counter: Counter[str] = Counter()
    for item in plan:
        if item["old_category"] != item["new_category"]:
            counter[f"{item['old_category']}->{item['new_category']}"] += 1
    return dict(sorted(counter.items()))


def _patch_finding(
    base_url: str,
    service_key: str,
    item: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """PATCH one findings row's category_code/category_label_ko/taxonomy_version,
    filtered by finding_id + the category_code we just read (race-safety: if a
    concurrent process already reclassified this row, the filter matches zero
    rows rather than clobbering an already-current value).

    Returns (status_code, returned_rows_or_None, error_summary), mirroring
    findings_translate_apply_service._patch_finding's contract exactly:
    error_summary is "" on 2xx, else "timeout", an exception type name, or
    "http_{status}" -- never exception text, so the service-role key embedded
    in a lower-level transport error can never leak through it. Retries once
    (total 2 attempts) for 5xx responses or a request timeout.
    """
    finding_id = str(item.get("finding_id") or "")
    old_category = str(item.get("old_category") or "")
    new_category = str(item.get("new_category") or "")
    label = gf.CATEGORY_BY_CODE.get(new_category, gf.CATEGORY_BY_CODE["other_quality_system"]).label_ko

    url = f"{base_url}/rest/v1/findings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {
        "finding_id": f"eq.{finding_id}",
        "category_code": f"eq.{old_category}",
    }
    body = {
        "category_code": new_category,
        "category_label_ko": label,
        "taxonomy_version": gf.TAXONOMY_VERSION,
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


def run_reclassify(
    base_url: str,
    service_key: str,
    *,
    dry_run: bool,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch every findings row, plan the reclassification, and (unless
    dry_run) PATCH each changed row. Idempotent: a second run against an
    already-reclassified table plans (and PATCHes) 0 rows.
    """
    base = _normalize_base_url(base_url)
    report: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "apply",
        "taxonomy_version": gf.TAXONOMY_VERSION,
        "rows_scanned": 0,
        "changes_planned": 0,
        "category_changes": 0,
        "version_only_stamps": 0,
        "category_migration_matrix": {},
        "patched": 0,
        "matched_zero": 0,
        "errors": [],
    }
    if base is None:
        report["errors"].append("SUPABASE_URL must start with https://")
        return report

    try:
        rows = fetch_findings_for_reclassification(base, service_key)
    except (RuntimeError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report

    report["rows_scanned"] = len(rows)

    plan = plan_reclassification(rows)
    if limit is not None and limit >= 0:
        plan = plan[:limit]

    report["changes_planned"] = len(plan)
    report["category_changes"] = sum(1 for item in plan if item["old_category"] != item["new_category"])
    report["version_only_stamps"] = report["changes_planned"] - report["category_changes"]
    report["category_migration_matrix"] = _category_migration_matrix(plan)

    if dry_run:
        return report

    for item in plan:
        status, patched_rows, err = _patch_finding(base, service_key, item)
        finding_id = item["finding_id"]
        if err:
            report["errors"].append(f"finding_id={finding_id} PATCH failed ({err})")
            continue

        matched = len(patched_rows or [])
        if matched == 0:
            # 0 rows matched -- either already reclassified by a prior/concurrent
            # run (category_code no longer equals old_category), or the row was
            # deleted since the scan. Both are TOCTOU-safe no-ops, not errors.
            report["matched_zero"] += 1
        elif matched == 1:
            report["patched"] += 1
        else:
            # >1 rows matched: finding_id should be unique -- a data-integrity
            # anomaly, not the expected idempotent no-op/success cases above.
            report["errors"].append(
                f"finding_id={finding_id} PATCH matched {matched} rows (expected 0 or 1)"
            )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    """Write the report JSON to `path` if given, and always print it to stdout
    too -- CI step summaries can't be queried via `gh` CLI, so the run log
    (stdout) must carry the planned/patched counts and category migration
    matrix even when --output is also set. The service-role key is never a
    key or value in `report` (see run_reclassify/_patch_finding contracts),
    so this print can never leak it."""
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="grm-finding-taxonomy/v3 -- unattended CI reclassification service "
        "for the live findings table (no LLM, no git writes; READ + findings category "
        "PATCH only)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read and plan only -- report the change count and category migration "
        "matrix, but never PATCH anything.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only PATCH the first N planned changes (default: all).",
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
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_reclassify_service: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = run_reclassify(base_url, service_key, dry_run=args.dry_run, limit=args.limit)
    _write_report(args.output, report)

    if report["errors"]:
        return 1
    return 0


__all__ = [
    "fetch_findings_for_reclassification",
    "plan_reclassification",
    "run_reclassify",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
