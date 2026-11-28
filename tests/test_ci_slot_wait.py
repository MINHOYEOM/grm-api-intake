import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

from ci_slot_wait import KST, active_branch_count, collection_wait_seconds, main


def run_list(*branches):
    return json.dumps([
        {"databaseId": run_id, "headBranch": branch, "status": status}
        for run_id, (branch, status) in enumerate(branches, start=1)
    ])


BUSY = run_list(("alpha", "queued"), ("beta", "in_progress"), ("gamma", "queued"))
AVAILABLE = run_list(("alpha", "queued"), ("beta", "in_progress"))


class CiSlotWaitTests(unittest.TestCase):
    def setUp(self):
        self.current_time = datetime(2026, 11, 25, 12, tzinfo=KST)
        self.elapsed = 0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds
        self.current_time += timedelta(seconds=seconds)

    def run_cli(self, argv, *snapshots, current_branch=None):
        snapshots = iter(snapshots)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                argv, read_runs=lambda: next(snapshots), now=lambda: self.current_time,
                sleep=self.sleep, monotonic=lambda: self.elapsed,
                current_branch=current_branch or (lambda: ""),  # 실제 git 브랜치에 시험이 흔들리지 않게
            )
        self.assertEqual(list(snapshots), [])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_counts_distinct_active_branches(self):
        runs = run_list(
            ("alpha", "queued"), ("alpha", "in_progress"), ("beta", "in_progress"),
            ("gamma", "completed"), ("delta", "waiting"),
        )
        self.assertEqual(active_branch_count(runs), 2)

    def test_polls_until_a_slot_opens(self):
        code, stdout, _ = self.run_cli([], BUSY, BUSY, AVAILABLE)
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [60, 60])
        self.assertIn("3개 · CI 자리 대기", stdout)
        self.assertIn("2개 · push 가능", stdout)

    def test_times_out_when_all_slots_stay_busy(self):
        code, _, stderr = self.run_cli(["--max-wait-min", "1"], BUSY)
        self.assertEqual(code, 1)
        self.assertEqual(self.sleeps, [60])
        self.assertIn("시간 초과", stderr)

    def test_collection_window_includes_both_boundaries(self):
        start = self.current_time.replace(hour=2, minute=17).astimezone(timezone.utc)
        end = start + timedelta(hours=2)
        tick = datetime.resolution
        self.assertEqual(collection_wait_seconds(start - tick), 0)
        self.assertGreater(collection_wait_seconds(start), 0)
        self.assertGreater(collection_wait_seconds(end), 0)
        self.assertEqual(collection_wait_seconds(end + tick), 0)

    def test_waits_until_collection_window_ends_then_checks_ci_again(self):
        self.current_time = self.current_time.replace(hour=4, minute=16)
        code, stdout, _ = self.run_cli([], AVAILABLE, BUSY, AVAILABLE)
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [60.000001, 60])
        self.assertIn("2개 · 수집 시간 창 종료 대기", stdout)

    def test_collection_window_wait_is_capped_by_deadline(self):
        self.current_time = self.current_time.replace(hour=2, minute=17)
        code, _, stderr = self.run_cli(["--max-wait-min", "1"], AVAILABLE)
        self.assertEqual(code, 1)
        self.assertEqual(self.sleeps, [60])
        self.assertIn("시간 초과", stderr)

    def test_polls_busy_ci_during_the_collection_window(self):
        self.current_time = self.current_time.replace(hour=4, minute=15)
        code, _, _ = self.run_cli([], BUSY, AVAILABLE, AVAILABLE)
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [60, 60.000001])

    def test_can_disable_collection_window(self):
        self.current_time = self.current_time.replace(hour=3)
        code, stdout, _ = self.run_cli(["--no-collect-window"], AVAILABLE)
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [])
        self.assertIn("2개 · push 가능", stdout)

    def test_dry_run_reports_the_decision_without_sleeping(self):
        code, stdout, _ = self.run_cli(["--dry-run"], BUSY)
        self.assertEqual(code, 0)
        self.assertEqual(self.sleeps, [])
        self.assertIn("3개 · CI 자리 대기", stdout)

    def test_can_exclude_an_explicit_or_current_branch(self):
        options = (
            (["--self-branch", "alpha"], None),
            ([], lambda: "beta"),
        )
        for extra_args, current_branch in options:
            with self.subTest(extra_args=extra_args):
                code, stdout, _ = self.run_cli(
                    ["--exclude-branch", *extra_args], BUSY, current_branch=current_branch,
                )
                self.assertEqual(code, 0)
                self.assertEqual(self.sleeps, [])
                self.assertIn("2개 · push 가능", stdout)

    def test_company_branch_skips_the_wait_even_when_ci_is_full(self):
        # 편집팀 push 는 PR CI 를 안 띄운다. 기다리다 백그라운드로 가면 push 가 사라진다(11-28).
        for branch in ("claude/company-2026-11-30", "claude/company-backup-1130"):
            with self.subTest(branch=branch):
                self.sleeps.clear()
                code, stdout, _ = self.run_cli([], current_branch=lambda b=branch: b)
                self.assertEqual(code, 0)
                self.assertEqual(self.sleeps, [])
                self.assertIn("push 가능", stdout)

    def test_non_company_branch_still_waits(self):
        code, _, _ = self.run_cli(["--max-wait-min", "0"], BUSY, current_branch=lambda: "claude/glossary-weekly")
        self.assertNotEqual(code, 0)
