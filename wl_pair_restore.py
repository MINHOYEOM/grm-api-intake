#!/usr/bin/env python3
"""Preview a raw_body_followup restore plan, or apply its source-text changes."""

import argparse
import json
import os
from pathlib import Path
import re
import sys


PLAN_SCHEMA = "grm-wl-truncation-restore-plan/v1"
TEXT_COLUMN = "finding_text"
RESTORE_STATUSES = {"applied", "already_applied", "no_match", "stale", "concurrent_change"}


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


def apply_change(change, base_url, service_key):
    """Let the database check the planned hash and update the locked row."""
    import requests

    url = f"{base_url}/rest/v1/rpc/grm_wl_pair_restore"
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    payload = {
        "p_finding_id": change["finding_id"],
        "p_raw_signal_id": change["raw_signal_id"],
        "p_old_md5": change["before_md5"][TEXT_COLUMN],
        "p_new_text": change["after"][TEXT_COLUMN],
    }
    for attempt in range(2):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
        except requests.exceptions.Timeout:
            if attempt == 0:
                continue
            return "rpc_failed:timeout"
        except requests.exceptions.RequestException as exc:
            return f"rpc_failed:{type(exc).__name__}"
        if response.status_code < 500:
            break
    if not 200 <= response.status_code < 300:
        return f"rpc_failed:http_{response.status_code}"
    try:
        status = response.json()
    except ValueError:
        return "unexpected_rpc_result"
    if isinstance(status, str) and status in RESTORE_STATUSES:
        return status
    return "unexpected_rpc_result"


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
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
        results = []
        for change in changes:
            status = apply_change(change, base_url, service_key)
            results.append({"finding_id": change["finding_id"], "status": status})
            if status not in RESTORE_STATUSES:
                failed = True
        report["results"] = results

    print(json.dumps(report, indent=2))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
