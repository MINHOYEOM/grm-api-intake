import json
import os
import re
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

from company.editorial import stage_timing


SCRIPT = Path(stage_timing.__file__).resolve()
RUN_DATE = "2026-11-30"
RUN_FOLDER = f"company/run/{RUN_DATE}"
BRANCH = f"claude/company-{RUN_DATE}"


def git(repo, *args, stdin=None):
    return subprocess.run(
        ["git", *args], cwd=repo,
        input=stdin.encode("utf-8") if stdin is not None else None,
        capture_output=True, check=True,
    ).stdout.decode("utf-8").strip()


def make_history(repo, records):
    git(repo, "init", "--template=")
    parent = None
    for epoch, files in records:
        entries = []
        for path, content in files.items():
            blob = git(repo, "hash-object", "-w", "--stdin", stdin=content)
            entries.append(f"100644 {blob}\t{RUN_FOLDER}/{path}\n")
        git(repo, "update-index", "--index-info", stdin="".join(entries))
        tree = git(repo, "write-tree")
        identity = f"Editorial Test <test@example.invalid> {epoch} +0000"
        headers = [f"tree {tree}"]
        if parent:
            headers.append(f"parent {parent}")
        headers += [f"author {identity}", f"committer {identity}"]
        parent = git(repo, "hash-object", "-t", "commit", "-w", "--stdin",
                     stdin="\n".join(headers) + "\n\nTest fixture\n")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", parent)


def epoch(hhmmss):
    timestamp = f"{RUN_DATE}T{hhmmss}+09:00"
    return int(datetime.fromisoformat(timestamp).timestamp())


def run_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), RUN_DATE, *args], cwd=repo, check=True,
        capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ).stdout


def test_rework_and_deadlines_from_local_ref(tmp_path):
    make_history(tmp_path, [(epoch(clock), files) for clock, files in [
        ("07:21:00", {"claims/desk.claim": "claim"}),
        ("07:40:00", {"desk.json": "{}"}),
        ("08:02:00", {"claims/delta.A.claim": "claim"}),
        ("08:03:00", {"claims/deep.A.claim": "claim", "claims/deep.B.claim": "claim"}),
        ("08:25:00", {"delta.A.json": "{}"}),
        ("08:27:00", {"deep.A.json": "{}"}),
        ("08:45:00", {"delta.json": "{}"}),
        ("09:00:00", {"copyedit.json": '{"status":"재작업"}'}),
        ("09:02:00", {"rework.json": '{"status":"진행"}'}),
        ("09:11:00", {"rework.json": '{"status":"완료"}', "delta.A.json": '{"revision":1}'}),
        ("09:12:00", {"delta.json": '{"revision":1}'}),
        ("09:19:00", {"copyedit.json": '{"round":2}', "copyedit.r1.json": '{"round":1}'}),
        ("09:21:00", {"deep.B.json": "{}"}),
        ("09:25:01", {"copyedit.json": '{"status":"완료"}', "rework2.json": "{}", "delta.A.json": '{"revision":2}'}),
        ("09:30:00", {"backup/claim": "claim"}),
        ("09:45:00", {"backup/copyedit.json": "{}"}),
    ]])

    report = json.loads(run_cli(tmp_path, "--json"))
    assert report["branch"] == BRANCH
    rows = {row["stage"]: row for row in report["stages"]}

    def measurement(stage):
        row = rows[stage]
        assert row["start_kst"].endswith("+09:00")
        return (row["start_kst"][11:19], row["end_kst"][11:19], row["duration_seconds"], row["overdue"])

    assert measurement("desk") == ("07:21:00", "07:40:00", 1140, None)
    assert measurement("delta") == ("08:02:00", "08:25:00", 1380, False)
    assert measurement("deep") == ("08:03:00", "09:21:00", 4680, True)
    assert measurement("copyedit_r1") == ("08:45:00", "09:00:00", 900, False)
    assert measurement("rework") == ("09:02:00", "09:11:00", 540, False)
    assert measurement("copyedit_r2") == ("09:12:00", "09:25:01", 781, True)
    assert rows["copyedit_r2"]["slot_elapsed_seconds"] == 781
    assert measurement("rework2") == ("09:25:01", "09:25:01", 0, True)
    assert measurement("backup") == ("09:30:00", "09:45:00", 900, None)
    table = run_cli(tmp_path, "--branch", f"refs/heads/{BRANCH}")
    assert "단계 | 시작(KST)" in table
    assert table.splitlines()[-1] == (
        "단계 실측: 교열 2차 09:25:01 KST 결과 (09:25:00 시한 초과)"
        " · 09:12 예약 시각부터 13분 01초"
    )


def test_direct_review_uses_final_deadline_and_missing_results_are_unknown(tmp_path):
    make_history(tmp_path, [
        (epoch("08:00:00"), {"claims/deep.A.claim": "claim"}),
        (epoch("09:25:00"), {"copyedit.json": '{"status":"완료"}'}),
    ])
    rows = {row["stage"]: row for row in json.loads(run_cli(tmp_path, "--json"))["stages"]}
    assert rows["copyedit_r1"]["overdue"] is False
    assert rows["copyedit_r1"]["deadline_kst"].endswith("09:25:00+09:00")
    assert rows["copyedit_r2"]["start_kst"] is None
    assert rows["deep"]["end_kst"] is None
    assert rows["deep"]["overdue"] is None


def test_first_review_waiting_for_rework_uses_first_deadline(tmp_path):
    make_history(tmp_path, [(epoch("09:00:01"), {"copyedit.json": '{"status":"재작업"}'})])
    rows = {row["stage"]: row for row in json.loads(run_cli(tmp_path, "--json"))["stages"]}
    assert rows["copyedit_r1"]["overdue"] is True
    assert rows["rework"]["start_kst"] is None


def test_deadlines_match_run_schedule():
    schedule_lines = []
    in_schedule = False
    with SCRIPT.with_name("RUN.md").open(encoding="utf-8") as source:
        for line in source:
            if line.startswith("## "):
                if in_schedule:
                    break
                in_schedule = line.startswith("## 5.")
            if in_schedule:
                schedule_lines.append(line)
    schedule = "".join(schedule_lines)
    checks = [
        (stage_timing.SHARD_DEADLINE, r"미완 샤드가 있으면 (\d\d:\d\d) 까지"),
        (stage_timing.FIRST_REVIEW_REWORK_DEADLINE, r"늦어도 (\d\d:\d\d) 까지 push"),
        (stage_timing.REWORK_DEADLINE, r"시한\((\d\d:\d\d) 재작업 대기"),
        (stage_timing.FINAL_REVIEW_DEADLINE, r"교열의 마감[^\n]*? (\d\d:\d\d) 가 되도록"),
        (stage_timing.FINAL_REVIEW_DEADLINE, r"(\d\d:\d\d) 시한은 그대로다"),
        (stage_timing.SECOND_REVIEW_SLOT, r"\| (\d\d:\d\d) \(재작업 직후\) \| 교열 \(2차\)"),
    ]
    for cutoff, pattern in checks:
        match = re.search(pattern, schedule)
        assert match, pattern
        assert cutoff == time.fromisoformat(match[1]), pattern
