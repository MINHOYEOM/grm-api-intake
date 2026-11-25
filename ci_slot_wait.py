"""Wait for a GRM CI slot before pushing a branch."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, time as wall_time, timedelta, timezone


GH_RUN_LIST = (
    "gh", "run", "list", "--workflow", "GRM CI (compile + tests)",
    "--event", "pull_request", "--json", "databaseId,headBranch,status",
    "--limit", "100",
)
ACTIVE_STATUSES = {"queued", "in_progress"}
SLOT_LIMIT = 3
POLL_SECONDS = 60
KST = timezone(timedelta(hours=9), "KST")
COLLECT_START = wall_time(2, 17, tzinfo=KST)
COLLECT_END = wall_time(4, 17, tzinfo=KST)


def active_branch_count(run_list_json, excluded_branch=None):
    runs = json.loads(run_list_json)
    if not isinstance(runs, list):
        raise ValueError("gh 실행 목록이 JSON 배열이 아닙니다.")
    return len({
        run["headBranch"]
        for run in runs
        if run["status"] in ACTIVE_STATUSES and run["headBranch"] != excluded_branch
    })


def collection_wait_seconds(now):
    local_now = now.astimezone(KST)
    start = datetime.combine(local_now.date(), COLLECT_START)
    end = datetime.combine(local_now.date(), COLLECT_END)
    if start <= local_now <= end:
        # The closing instant is part of the window.
        return (end + datetime.resolution - local_now).total_seconds()
    return 0


def wait_for_slot(args, *, read_runs, now, sleep, monotonic, excluded_branch=None):
    deadline = monotonic() + args.max_wait_min * 60
    while True:
        count = active_branch_count(read_runs(), excluded_branch)
        window_wait = collection_wait_seconds(now()) if args.collect_window else 0
        if window_wait:
            decision = "수집 시간 창 종료 대기"
        elif count >= SLOT_LIMIT:
            decision = "CI 자리 대기"
        else:
            decision = "push 가능"
        print(f"실행 중이거나 대기 중인 CI 브랜치 {count}개 · {decision}", flush=True)

        if args.dry_run or (not window_wait and count < SLOT_LIMIT):
            return 0

        interval = window_wait or POLL_SECONDS
        if count >= SLOT_LIMIT:
            interval = min(interval, POLL_SECONDS)
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(interval, remaining))
        if monotonic() >= deadline:
            print("CI 자리 대기 시간 초과", file=sys.stderr)
            return 1


def main(
    argv=None, *, read_runs=None, now=None, sleep=time.sleep,
    monotonic=time.monotonic, current_branch=None,
):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="push 전에 GRM CI 자리를 확인합니다.")
    parser.add_argument("--max-wait-min", type=int, default=90, help="최대 대기 시간(분)")
    parser.add_argument(
        "--collect-window", action=argparse.BooleanOptionalAction, default=True,
        help="수집 시간 창(02:17~04:17 KST)이 끝날 때까지 대기",
    )
    parser.add_argument("--dry-run", action="store_true", help="브랜치 수와 판정만 출력")
    parser.add_argument("--exclude-branch", action="store_true", help="자기 브랜치를 집계에서 제외")
    parser.add_argument("--self-branch", help="제외할 자기 브랜치 이름(기본값은 현재 git 브랜치)")
    args = parser.parse_args(argv)
    if args.max_wait_min < 0:
        parser.error("--max-wait-min은 0 이상이어야 합니다.")

    try:
        excluded_branch = None
        if args.exclude_branch:
            excluded_branch = args.self_branch
            if excluded_branch is None:
                if current_branch is None:
                    excluded_branch = subprocess.check_output(
                        ("git", "branch", "--show-current"), encoding="utf-8",
                    ).strip()
                else:
                    excluded_branch = current_branch()
            if not excluded_branch:
                parser.error("현재 git 브랜치가 없습니다. --self-branch로 지정하세요.")

        return wait_for_slot(
            args,
            read_runs=read_runs or (lambda: subprocess.check_output(GH_RUN_LIST, encoding="utf-8")),
            now=now or (lambda: datetime.now(KST)),
            sleep=sleep,
            monotonic=monotonic,
            excluded_branch=excluded_branch,
        )
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as exc:
        print(f"CI 상태 확인 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
