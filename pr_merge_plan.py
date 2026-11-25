#!/usr/bin/env python3
"""Print a read-only merge plan for open PRs in the current GitHub repository."""

import argparse
from collections import defaultdict
from html import escape
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys


PR_FIELDS = (
    "number,title,baseRefName,headRefName,isCrossRepository,isDraft,"
    "statusCheckRollup,changedFiles"
)
CI_FAILURES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
CI_PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "REQUESTED", "WAITING", "PENDING_APPROVAL"}
CI_SUCCESS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def gh_json(*args):
    result = subprocess.run(
        ["gh", *args], check=True, stdout=subprocess.PIPE, encoding="utf-8",
    )
    return json.loads(result.stdout)


def load_open_prs():
    limit = 100
    while True:
        prs = gh_json("pr", "list", "--state", "open", "--limit", str(limit), "--json", PR_FIELDS)
        if len(prs) < limit:
            break
        limit *= 2

    for pr in prs:
        if not pr["changedFiles"]:
            pr["files"] = []
            continue
        # The REST file list includes the previous filename for renames.
        pages = gh_json(
            "api", "--paginate", "--slurp",
            f"repos/{{owner}}/{{repo}}/pulls/{pr['number']}/files?per_page=100",
        )
        pr["files"] = []
        for page in pages:
            for file in page:
                entry = {"path": file["filename"]}
                if file.get("previous_filename"):
                    entry["previous_path"] = file["previous_filename"]
                pr["files"].append(entry)
    return prs


def ci_state(checks):
    states = {
        check.get("conclusion") or check.get("state") or check.get("status")
        for check in checks or []
    }
    if states & CI_FAILURES:
        return "FAIL"
    if states & CI_PENDING:
        return "PENDING"
    if states and states <= CI_SUCCESS:
        return "PASS"
    return "UNKNOWN" if states else "NONE"


def merge_plan(prs):
    if not isinstance(prs, list):
        raise ValueError("PR 목록은 JSON 배열이어야 합니다.")
    by_number = {pr["number"]: pr for pr in prs}
    if len(by_number) != len(prs):
        raise ValueError("PR 목록에 중복 번호가 있습니다.")

    by_file = defaultdict(list)
    by_head = defaultdict(set)
    for number, pr in sorted(by_number.items()):
        files = {file["path"] for file in pr["files"] or []}
        if len(files) != pr.get("changedFiles", len(files)):
            raise ValueError(f"#{number}의 변경 파일 목록이 완전하지 않습니다.")
        files.update(file["previous_path"] for file in pr["files"] or [] if file.get("previous_path"))
        for path in sorted(files):
            by_file[path].append(number)
        if not pr["isCrossRepository"]:
            by_head[pr["headRefName"]].add(number)

    overlaps = defaultdict(list)
    for path, numbers in by_file.items():
        for pair in combinations(numbers, 2):
            overlaps[pair].append(path)

    overlap_counts = dict.fromkeys(by_number, 0)
    for left, right in overlaps:
        overlap_counts[left] += 1
        overlap_counts[right] += 1

    parents = {}
    ambiguous = {}
    ci = {}
    for number, pr in by_number.items():
        candidates = by_head[pr["baseRefName"]] - {number}
        if len(candidates) == 1:
            parents[number] = next(iter(candidates))
        elif candidates:
            ambiguous[number] = sorted(candidates)
        ci[number] = ci_state(pr["statusCheckRollup"])

    pending = set(by_number)
    recommended = set()
    rows = []

    def priority(number):
        can_recommend = (
            not by_number[number]["isDraft"]
            and ci[number] == "PASS"
            and number not in ambiguous
            and (number not in parents or parents[number] in recommended)
        )
        return not can_recommend, overlap_counts[number], number

    while pending:
        available = [number for number in pending if parents.get(number) not in pending]
        if not available:
            for number in sorted(pending):
                rows.append((number, ci[number], parents[number], None, "스택 순환 확인"))
            break

        number = min(available, key=priority)
        parent = parents.get(number)
        rank = None
        if not priority(number)[0]:
            recommended.add(number)
            rank = len(recommended)
            action = f"#{parent} 머지 후 base·CI 재확인" if parent else "머지 검토"
        elif number in ambiguous:
            candidates = ", ".join(f"#{candidate}" for candidate in ambiguous[number])
            action = f"상위 PR 확인 ({candidates})"
        elif parent is not None and parent not in recommended:
            action = f"상위 PR #{parent} 보류"
        elif by_number[number]["isDraft"]:
            action = "초안 해제 대기"
        else:
            action = "CI 확인"
        rows.append((number, ci[number], parent, rank, action))
        pending.remove(number)
    return rows, overlaps


def cell(value):
    return escape(str(value), quote=False).replace("|", "&#124;").replace("\r", "\\r").replace("\n", "\\n")


def render(prs):
    rows, overlaps = merge_plan(prs)
    by_number = {pr["number"]: pr for pr in prs}
    lines = [
        "## 권장 머지 순서",
        "",
        "| 순서 | PR | 제목 | Base | 상위 PR | 초안 | CI | 조치 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, ci, parent, rank, action in rows:
        pr = by_number[number]
        values = (
            rank if rank is not None else "보류", f"#{number}", pr["title"], pr["baseRefName"],
            f"#{parent}" if parent is not None else "-", "예" if pr["isDraft"] else "아니요", ci, action,
        )
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    if not rows:
        lines.append("| - | - | - | - | - | - | - | 열린 PR 없음 |")

    lines.extend([
        "", "## 겹침 경고", "",
        "| PR 쌍 | 겹치는 파일 |", "| --- | --- |",
    ])
    if not overlaps:
        lines.append("| - | 겹침 없음 |")
    for (left, right), paths in sorted(overlaps.items()):
        lines.append(f"| #{left} / #{right} | {', '.join(map(cell, paths))} |")
    return "\n".join(lines)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", metavar="JSON", help="Read a saved gh PR list (use - for stdin).")
    args = parser.parse_args(argv)
    try:
        if args.input == "-":
            prs = json.load(sys.stdin)
        elif args.input:
            prs = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        else:
            prs = load_open_prs()
        print(render(prs))
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
