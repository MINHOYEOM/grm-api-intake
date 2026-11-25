import sys
"""Measure editorial stages from the committer timestamps in a local Git ref.

Claims and intermediate artifacts can mark the first observed activity. For
rework, only a completed status marks a recorded result. The
interval between those records is not necessarily the worker's runtime or
the time between pushes.
"""

import argparse
import json
import os
import re
import subprocess
from datetime import date, datetime, time, timedelta, timezone


KST = timezone(timedelta(hours=9), "KST")
# RUN.md §5 and §5-1: result cutoffs, including input cutoffs for the next stage.
SHARD_DEADLINE = time(9, 20)
FIRST_REVIEW_REWORK_DEADLINE = time(9, 0)
REWORK_DEADLINE = time(9, 20)
FINAL_REVIEW_DEADLINE = time(9, 25)
SECOND_REVIEW_SLOT = time(9, 12)

STAGES = {
    "desk": "데스크",
    "delta": "델타",
    "deep": "심층",
    "copyedit_r1": "교열 1차",
    "rework": "재작업 1회차",
    "copyedit_r2": "교열 2차",
    "rework2": "재작업 2회차",
    "backup": "백업",
}


def git(*args, check=True):
    return subprocess.run(
        ["git", *args], check=check, capture_output=True, encoding="utf-8",
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )


def history(revision, folder):
    output = git(
        "log", "--full-history", "--reverse", "--topo-order", "--no-renames",
        "--format=%x1e%H %cI", "--name-only", "-z", revision, "--", folder,
    ).stdout
    for record in output.split("\x1e")[1:]:
        header, _, paths = record.partition("\0")
        commit, timestamp = header.split()
        files = {path.removeprefix(folder) for path in paths.strip("\n").split("\0") if path}
        yield commit, datetime.fromisoformat(timestamp).astimezone(KST), files


def rework_completed(commit, path):
    result = git("show", f"{commit}:{path}", check=False)
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "완료"


def stage_records(revision, folder):
    activity = {stage: [] for stage in STAGES}
    results = {stage: [] for stage in STAGES}

    def record(stage, timestamp, has_result):
        activity[stage].append(timestamp)
        if has_result:
            results[stage].append(timestamp)

    merged_outputs = {"delta.json", "deep_delta.json", "brief_check.json"}
    for commit, timestamp, files in history(revision, folder):
        if files & {"desk.json", "claims/desk.claim"}:
            record("desk", timestamp, "desk.json" in files)

        rework_files = files & {"rework.json", "rework2.json"}
        # Rework commits also change previously produced shards.
        if not rework_files:
            for stage in ("delta", "deep"):
                claim = any(re.fullmatch(rf"claims/{stage}\.[^/]+\.claim", path) for path in files)
                result = any(re.fullmatch(rf"{stage}\.[^/]+\.json", path) for path in files)
                if claim or result:
                    record(stage, timestamp, result)

        for path in rework_files:
            record(path.removesuffix(".json"), timestamp, rework_completed(commit, folder + path))
        if "rework2.json" in files:
            record("copyedit_r2", timestamp, False)

        review_stage = "copyedit_r2" if activity["rework"] or activity["rework2"] else "copyedit_r1"
        if "copyedit.json" in files:
            record(review_stage, timestamp, True)
        elif not rework_files and files & merged_outputs:
            record(review_stage, timestamp, False)
        if "copyedit.r1.json" in files and review_stage == "copyedit_r1" and not results["copyedit_r1"]:
            record("copyedit_r1", timestamp, True)

        if any(path.startswith("backup/") for path in files):
            record("backup", timestamp, "backup/copyedit.json" in files)

    return activity, results


def measure(run_date, branch):
    folder = f"company/run/{run_date}/"
    revision = git("rev-parse", "--verify", "--end-of-options", f"{branch}^{{commit}}").stdout.strip()
    activity, results = stage_records(revision, folder)
    try:
        schedule_date = date.fromisoformat(run_date)
    except ValueError:
        first_activity = min((min(times) for times in activity.values() if times), default=None)
        schedule_date = first_activity.date() if first_activity else None

    needs_rework = bool(activity["rework"] or activity["copyedit_r2"])
    if results["copyedit_r1"] and not needs_rework:
        final = git("show", f"{revision}:{folder}copyedit.json", check=False)
        if final.returncode == 0:
            try:
                needs_rework = json.loads(final.stdout)["status"] == "재작업"
            except (ValueError, TypeError, KeyError):
                pass

    cutoffs = {
        "delta": SHARD_DEADLINE,
        "deep": SHARD_DEADLINE,
        "copyedit_r1": FIRST_REVIEW_REWORK_DEADLINE if needs_rework else FINAL_REVIEW_DEADLINE,
        "rework": REWORK_DEADLINE,
        "copyedit_r2": FINAL_REVIEW_DEADLINE,
        "rework2": FINAL_REVIEW_DEADLINE,
    }
    rows = []
    for stage, label in STAGES.items():
        start = min(activity[stage], default=None)
        end = max(results[stage], default=None)
        cutoff = cutoffs.get(stage)
        deadline = datetime.combine(schedule_date, cutoff, KST) if schedule_date and cutoff else None
        slot = datetime.combine(schedule_date, SECOND_REVIEW_SLOT, KST) if schedule_date and stage == "copyedit_r2" else None
        rows.append({
            "stage": stage,
            "label": label,
            "start_kst": start.isoformat() if start else None,
            "end_kst": end.isoformat() if end else None,
            "duration_seconds": int((end - start).total_seconds()) if end else None,
            "deadline_kst": deadline.isoformat() if deadline else None,
            "overdue": end > deadline if end and deadline else None,
            "scheduled_start_kst": slot.isoformat() if slot else None,
            "slot_elapsed_seconds": int((end - slot).total_seconds()) if end and slot and end >= slot else None,
        })
    return {"run_date": run_date, "branch": branch, "stages": rows}


def clock(iso_timestamp):
    return iso_timestamp[11:19] if iso_timestamp else "—"


def duration(seconds):
    if seconds is None:
        return "—"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}분 {seconds:02d}초"


def verdict(row):
    return {True: "초과", False: "시한 내", None: "—"}[row["overdue"]]


def print_report(report):
    print("단계 | 시작(KST) | 결과(KST) | 기록 간격 | 시한(KST) | 판정")
    print("--- | --- | --- | --- | --- | ---")
    stages = report["stages"]
    for row in stages:
        print(" | ".join((
            row["label"], clock(row["start_kst"]), clock(row["end_kst"]),
            duration(row["duration_seconds"]), clock(row["deadline_kst"]), verdict(row),
        )))
    reviews = [row for row in stages if row["stage"].startswith("copyedit_") and row["start_kst"]]
    if not reviews:
        print("단계 실측: 교열 기록 없음")
        return
    review = reviews[-1]
    if not review["end_kst"]:
        print(f"단계 실측: {review['label']} 결과 기록 없음")
        return
    slot_elapsed = review["slot_elapsed_seconds"]
    slot_note = ""
    if slot_elapsed is not None:
        slot_time = clock(review["scheduled_start_kst"])[:5]
        slot_note = f" · {slot_time} 예약 시각부터 {duration(slot_elapsed)}"
    print(f"단계 실측: {review['label']} {clock(review['end_kst'])} KST 결과 "
          f"({clock(review['deadline_kst'])} 시한 {verdict(review)}){slot_note}")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_date", help="YYYY-MM-DD or rehearsal run folder name")
    parser.add_argument("--branch", help="Local Git ref (default: claude/company-<run_date>)")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the table")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", args.run_date):
        parser.error("run_date must be a single run folder name")
    branch = args.branch or f"claude/company-{args.run_date}"
    try:
        report = measure(args.run_date, branch)
    except subprocess.CalledProcessError as exc:
        parser.exit(1, f"Git 기록을 읽지 못했습니다 ({branch}): {exc.stderr.strip()}\n")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
