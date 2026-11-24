#!/usr/bin/env python3
"""Preview a raw_body_followup restore plan, or apply its source-text changes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


PLAN_SCHEMA = "grm-wl-truncation-restore-plan/v1"
WL_SOURCE = "FDA Warning Letter"
TEXT_COLUMN = "finding_text"


def load_changes(path):
    """Validate the entire plan before previewing or applying any change."""
    plan = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict) or plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError("unexpected restore plan schema")
    items = plan.get("items")
    if not isinstance(items, list):
        raise ValueError("restore plan items must be a list")
    if items and (
        not isinstance(plan.get("report"), dict)
        or plan["report"].get("origin") != "raw_body_followup"
    ):
        raise ValueError("expected a raw_body_followup restore plan")

    changes = []
    seen = set()
    for index, item in enumerate(items, start=1):
        fields = ("finding_id", "raw_signal_id", "finding_text_old_md5", TEXT_COLUMN)
        if not isinstance(item, dict) or any(
            not isinstance(item.get(field), str) or not item[field].strip()
            for field in fields
        ):
            raise ValueError(f"item {index}: missing or invalid restore fields")
        if not re.fullmatch(r"[0-9a-f]{32}", item["finding_text_old_md5"]):
            raise ValueError(f"item {index}: invalid old text MD5")
        finding_id = item["finding_id"]
        if finding_id in seen:
            raise ValueError(f"item {index}: repeated finding_id")
        seen.add(finding_id)
        changes.append({
            "finding_id": finding_id,
            "raw_signal_id": item["raw_signal_id"],
            "before_md5": {TEXT_COLUMN: item["finding_text_old_md5"]},
            "after": {TEXT_COLUMN: item[TEXT_COLUMN]},
        })
    return changes


def apply_change(change, base_url, service_key, *, send):
    """Check the planned hash and condition the update on the exact live text."""
    filters = {
        "finding_id": f"eq.{change['finding_id']}",
        "raw_signal_id": f"eq.{change['raw_signal_id']}",
        "source": f"eq.{WL_SOURCE}",
    }
    _, rows, error = send(
        "get", base_url, service_key, params={**filters, "select": TEXT_COLUMN}
    )
    if error:
        return f"get_failed:{error}"
    if not rows:
        return "no_match"
    if len(rows) != 1 or not isinstance(rows[0].get(TEXT_COLUMN), str):
        return "unexpected_get_result"

    old_text = rows[0][TEXT_COLUMN]
    new_text = change["after"][TEXT_COLUMN]
    if old_text == new_text:
        return "already_applied"
    if hashlib.md5(old_text.encode("utf-8")).hexdigest() != change["before_md5"][TEXT_COLUMN]:
        return "stale"

    # Keep the predicate in the PATCH so a concurrent body change cannot be overwritten.
    _, rows, error = send(
        "patch", base_url, service_key,
        params={**filters, TEXT_COLUMN: f"eq.{old_text}", "select": "finding_id"},
        json_body={TEXT_COLUMN: new_text},
    )
    if error:
        return f"patch_failed:{error}"
    if not rows:
        return "concurrent_change"
    if rows != [{"finding_id": change["finding_id"]}]:
        return "unexpected_patch_result"
    return "applied"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="raw_body_followup plan JSON")
    parser.add_argument("--apply", action="store_true", help="Apply after operator approval")
    args = parser.parse_args(argv)
    try:
        changes = load_changes(args.plan)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    report = {"apply": args.apply, "changes": changes}
    failed = False
    if args.apply and changes:
        base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base_url.startswith("https://") or not service_key:
            parser.error("--apply requires HTTPS SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        from findings_translate_apply_service import _send_findings_request

        results = []
        for change in changes:
            status = apply_change(change, base_url, service_key, send=_send_findings_request)
            results.append({"finding_id": change["finding_id"], "status": status})
            if status not in {"applied", "already_applied", "no_match", "stale", "concurrent_change"}:
                failed = True
        report["results"] = results

    print(json.dumps(report, indent=2))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
