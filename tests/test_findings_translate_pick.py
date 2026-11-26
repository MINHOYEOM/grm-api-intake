"""[적체 예방 2026-11-26] findings_translate_pick — 긴 행이 매일 밀려 영원히 안 뽑히는 일(11-25·26 미번역 319)을 막는다."""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import findings_translate_pick as tp  # noqa: E402


def item(fid, date, n):
    return {"finding_id": fid, "published_date": date, "finding_text": "x" * n}


class PickTest(unittest.TestCase):
    def test_long_old_rows_are_not_starved_by_daily_short_inflow(self):
        """매일 짧은 새 행이 들어와도 오래된 긴 행이 며칠 안에 모두 뽑힌다."""
        backlog = [item(f"long{i}", f"2024-01-{i + 1:02d}", 8000) for i in range(10)]
        queue = list(backlog)
        for day in range(30):
            queue += [item(f"new{day}-{k}", f"2026-11-{day % 28 + 1:02d}", 300) for k in range(40)]
            got = tp.pick(queue)
            ids = {x["finding_id"] for x in got["items"]}
            queue = [x for x in queue if x["finding_id"] not in ids]
        self.assertFalse([x for x in queue if x["finding_id"].startswith("long")])

    def test_lane_never_skips_a_row(self):
        """예산을 넘는 행 뒤의 짧은 행을 대신 고르지 않는다 — 줄은 거기서 멈춘다."""
        items = [item("a", "2026-11-03", 30000), item("b", "2026-11-02", 20000), item("c", "2026-11-01", 100)]
        got = tp.pick(items, max_chars=40000)
        self.assertEqual([x["finding_id"] for x in got["items"]], ["a", "c"])  # A줄 a, B줄 c, A줄 b 는 예산 초과 → 멈춤
        # 두 줄 모두 긴 행 앞에서 멈춰야 한다 — 가운데 짧은 s 를 대신 집으면 긴 행이 매일 밀린다(뮤테이션 확인)
        items = [item("n1", "2026-11-05", 100), item("L1", "2026-11-04", 39950), item("s", "2026-11-03", 10),
                 item("L2", "2026-11-02", 39950), item("o1", "2026-11-01", 100)]
        got = tp.pick(items, max_chars=40000)
        self.assertEqual([x["finding_id"] for x in got["items"]], ["n1", "o1"])

    def test_single_row_larger_than_budget_is_still_picked(self):
        got = tp.pick([item("huge", "2026-01-01", 90000)], max_chars=40000)
        self.assertEqual([x["finding_id"] for x in got["items"]], ["huge"])

    def test_row_cap_and_backlog_alert(self):
        items = [item(f"r{i}", f"2026-10-{i % 28 + 1:02d}", 10) for i in range(150)]
        got = tp.pick(items, max_rows=100)
        self.assertEqual(len(got["items"]), 100)
        self.assertFalse(got["backlog_alert"])
        big = [item(f"w{i}", "2025-01-01", 7500) for i in range(263)]
        self.assertTrue(tp.pick(big)["backlog_alert"])

    def test_no_duplicates_when_lanes_meet(self):
        items = [item(f"r{i}", f"2026-10-{i + 1:02d}", 100) for i in range(5)]
        got = tp.pick(items)
        ids = [x["finding_id"] for x in got["items"]]
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertEqual(len(ids), 5)
        self.assertEqual(got["remaining_rows"], 0)


class DailyInstructionTest(unittest.TestCase):
    """일일 번역 지시문이 고르기를 이 도구에 맡기고, 적체 신호를 보고하게 한다(지시문의 규칙 = 코드의 규칙)."""

    def test_daily_prompt_uses_picker_and_reports_backlog(self):
        text = (ROOT / "company" / "translation" / "findings-daily.md").read_text(encoding="utf-8")
        self.assertIn("findings_translate_pick.py", text)
        self.assertIn("backlog_alert", text)
        self.assertIsNone(re.search(r"최신순 최대 100건을 골라", text), "옛 고르기 규칙이 남아 있다")
        self.assertIsNone(re.search(r"건수를 줄인다", text), "번역 세션이 긴 행을 건너뛰게 하는 문구가 남아 있다")


if __name__ == "__main__":
    unittest.main()
