"""findings_detail_stale_monitor — 483 상세 재번역(stale) 대기 정체 경보(2026-11-26).

재번역 대기열(`findings_detail_export --mode stale`)은 예약 실행에 연결돼 있지 않다. 소급으로 원문이 바뀐 뒤
아무도 대기열을 만들지 않으면 재번역이 조용히 멈춘다. 이 감시는 매일 대기 행 수를 세고, 0 보다 큰 상태가
3일 넘게 줄지 않으면 경보한다. 경보 규칙을 여기서 고정한다.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import findings_detail_stale_monitor as m  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(REPO, ".github", "workflows", "grm-findings-detail-stale-monitor.yml")
D = date(2026, 12, 10)


def _hist(*pairs):
    return [{"date": d, "stale": n} for d, n in pairs]


class TrendTest(unittest.TestCase):
    def test_not_decreasing_for_three_days_alarms(self):
        h = _hist(("2026-12-06", 700), ("2026-12-07", 700), ("2026-12-08", 700), ("2026-12-09", 700),
                  ("2026-12-10", 700))
        r = m.evaluate_trend(h, D)
        self.assertEqual(r["status"], "failure")
        self.assertEqual(r["breaches"][0]["code"], "detail-stale-not-decreasing")
        self.assertEqual(r["baseline"], {"date": "2026-12-07", "stale": 700})

    def test_growing_also_alarms(self):
        r = m.evaluate_trend(_hist(("2026-12-07", 10), ("2026-12-10", 40)), D)
        self.assertEqual(r["status"], "failure")

    def test_decreasing_is_ok(self):
        r = m.evaluate_trend(_hist(("2026-12-07", 700), ("2026-12-10", 650)), D)
        self.assertEqual((r["status"], r["breaches"]), ("ok", []))

    def test_zero_today_is_ok(self):
        r = m.evaluate_trend(_hist(("2026-12-07", 700), ("2026-12-10", 0)), D)
        self.assertEqual(r["status"], "ok")

    def test_short_history_is_warming_up_not_silent_ok(self):
        r = m.evaluate_trend(_hist(("2026-12-08", 700), ("2026-12-09", 700), ("2026-12-10", 700)), D)
        self.assertEqual(r["status"], "warming_up")
        self.assertEqual(r["breaches"], [])

    def test_baseline_is_latest_entry_at_least_three_days_old(self):
        # 3일 전 기록이 비어도(크론 지각·실패) 그보다 이전 기록을 기준으로 본다
        r = m.evaluate_trend(_hist(("2026-12-01", 900), ("2026-12-05", 500), ("2026-12-09", 300),
                                   ("2026-12-10", 500)), D)
        self.assertEqual(r["baseline"]["date"], "2026-12-05")
        self.assertEqual(r["status"], "failure")

    def test_missing_today_entry_is_error(self):
        self.assertEqual(m.evaluate_trend(_hist(("2026-12-07", 1)), D)["status"], "error")


class HistoryTest(unittest.TestCase):
    def test_same_day_rerun_replaces_entry_and_keeps_order(self):
        h = m.merge_history(_hist(("2026-12-09", 5), ("2026-12-10", 9)), D, {"stale": 7, "junk_only_skipped": 2})
        self.assertEqual([(x["date"], x["stale"]) for x in h], [("2026-12-09", 5), ("2026-12-10", 7)])
        self.assertEqual(h[-1]["junk_only_skipped"], 2)

    def test_history_is_capped(self):
        h = [{"date": f"2026-10-{d:02d}", "stale": 1} for d in range(1, 32)]
        self.assertEqual(len(m.merge_history(h, D, {"stale": 1})), m.HISTORY_KEEP)


class SameQueueFunctionTest(unittest.TestCase):
    """감시는 export 의 stale 대기열과 같은 함수로 센다 — 산문 증가 조건까지 같다."""

    TEXT = "Procedures designed to prevent microbiological contamination are not established."
    OLD = "Specifically, A. The operator handled unprotected sterile parts with gloved hands during setup."

    def test_counts_match_export_queue(self):
        import findings_detail_export as fde
        grown = self.OLD + " B. Containers with foreign matter are not classified as critical defects at all."
        findings = [
            {"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2025-11-05",
             "finding_text": self.TEXT, "detail_text": self.OLD},
            {"finding_id": "f2", "raw_signal_id": "r2", "published_date": "2025-11-05",
             "finding_text": self.TEXT, "detail_text": self.OLD + " EMPlOVEE{S) SEE REVERSE"}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
                    {"number": "1", "deficiency": self.TEXT, "detail": grown}]}},
                {"raw_signal_id": "r2", "raw_json": {"fda_483_observations": [
                    {"number": "1", "deficiency": self.TEXT, "detail": self.OLD}]}}]
        counts = m.count_stale(findings, raws)
        queue, stats = fde.build_queue(findings, raws, stale_only=True)
        self.assertEqual((counts["stale"], counts["junk_only_skipped"]), (len(queue), stats["junk_only_skipped"]))
        self.assertEqual((counts["stale"], counts["junk_only_skipped"]), (1, 1))


class WorkflowContractTest(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as fh:
            self.wf = fh.read()

    def test_runs_daily_and_calls_monitor_with_history(self):
        self.assertRegex(self.wf, r"cron:\s*'\d+ \d+ \* \* \*'")
        self.assertIn("python findings_detail_stale_monitor.py --history detail_stale_history.json", self.wf)
        self.assertIn("path: detail_stale_history.json", self.wf)
        self.assertIn("restore-keys: detail-stale-history-", self.wf)

    def test_breach_opens_issue_and_never_turns_job_red(self):
        # cache 는 job 성공 시에만 저장된다 — red 로 끝내면 그날 기록이 사라져 추세를 못 본다
        self.assertIn("continue-on-error: true", self.wf)
        self.assertIn("if: steps.monitor.outcome == 'failure'", self.wf)
        self.assertIn("if: steps.monitor.outcome == 'success'", self.wf)
        self.assertIsNone(re.search(r"^\s*exit 1\s*$", self.wf, re.M))

    def test_read_only_permissions(self):
        self.assertIn("contents: read", self.wf)
        self.assertNotIn("contents: write", self.wf)


if __name__ == "__main__":
    unittest.main()
