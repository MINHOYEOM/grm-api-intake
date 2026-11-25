"""Warn when a dispatch follows three FDA-fetching dispatches on the same KST date."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

WORKFLOWS = (
    "grm-fda-citations",
    "grm-fda-import-alert",
    "grm-fda-inspections-backfill",
    "grm-fda483-body-full-backfill",
    "grm-fda483-detail-refresh",
    "grm-fda483-inspector-backfill",
    "grm-fda483-ocr-backfill",
    "grm-fda483-ocr-recovery",
    "grm-findings-backfill-fetch",
    "grm-intake",
    "grm-source-reconcile",
    "grm-source-verification",
)
KST = timezone(timedelta(hours=9))
THRESHOLD = 3


def _gh_json(*args):
    result = subprocess.run(
        ["gh", "run", *args, "--repo", os.environ["GITHUB_REPOSITORY"]],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    return json.loads(result.stdout)


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def warn_if_needed(run_id, summary):
    current = _gh_json("view", str(run_id), "--json", "createdAt")
    created = _time(current["createdAt"])
    start = created.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    utc_range = "..".join(
        instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for instant in (start, created)
    )
    count = 0
    for workflow in WORKFLOWS:
        runs = _gh_json(
            "list", "--workflow", workflow + ".yml", "--event", "workflow_dispatch", "--all",
            "--created", utc_range, "--limit", "100", "--json", "databaseId,createdAt",
        )
        for run in runs:
            timestamp = _time(run["createdAt"])
            if start <= timestamp and (timestamp, run["databaseId"]) < (created, run_id):
                count += 1
        if count >= THRESHOLD:
            summary.write("- ⚠️ 오늘(KST) 앞선 FDA 수동 실행이 3건 이상입니다. 추가 실행 시 FDA 차단 위험이 있습니다.\n")
            return


if __name__ == "__main__":
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        warn_if_needed(int(os.environ["GITHUB_RUN_ID"]), summary)
