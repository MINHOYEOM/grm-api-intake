"""수집 완전성 N6 — 사람이 보는 일보(completeness_daily.py).

합격 조건: 빠짐(놓침·결손·대조 안 돎·장부 차이)은 반드시 '사람 필요' 줄로 나오고, 같은 항목이 다음 날에도
남으면 제목에 'n일째'가 붙는다. 다 메워진 날은 한 줄("전 소스 정상")만 남는다.
"""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import completeness_daily as cd

DAY = date(2026, 11, 26)        # 목요일
MONDAY = date(2026, 11, 30)


def reconcile(*sources, day=DAY):
    return {"run_date": day.isoformat(), "sources": list(sources)}


def src(key, verdict="ok", missed=(), error=""):
    return {"key": key, "label": key.upper(), "verdict": verdict, "error": error,
            "missed": [{"document_id": d, "url": f"https://example.gov/{d}"} for d in missed]}


def ledger(admin_diff=0, incomplete=None):
    row = {"measurement_status": "measured", "source_count": 10, "db_count": 10 - admin_diff,
           "unexplained_difference": admin_diff}
    return {"mfds_admin": row, "mfds_recall": dict(row, unexplained_difference=0),
            "incomplete": incomplete}


def refill(*lines, day=DAY, apply=True):
    return {"day": day.isoformat(), "apply": apply, "lines": list(lines)}


REFILL_OK = refill()


def n2(**counts):
    return {"sources": {k: {"label": k, "incomplete": n, "by_field": {"f": n} if n else {},
                            "incomplete_ids": [f"{k}-{i}" for i in range(n)]} for k, n in counts.items()}}


class LinesTest(unittest.TestCase):
    def test_all_normal_is_one_line(self):
        lines = cd.build_lines(DAY, reconcile(src("fda483"), src("wl", "source_quiet")),
                               ledger(incomplete=n2(fda483=0)), None, REFILL_OK)
        result = cd.plan(DAY, lines, {})
        self.assertTrue(result["normal"])
        self.assertEqual(result["summary"], "2026-11-26 전 소스 정상")

    def test_injected_missed_doc_needs_human_with_id(self):
        """빠짐 주입: 대조가 놓친 문서 하나 → 사람 필요 줄에 번호와 원천 링크."""
        lines = cd.build_lines(DAY, reconcile(src("fda483", "missed", ["fda483-1"])),
                               ledger(incomplete=n2(fda483=0)), None)
        result = cd.plan(DAY, lines, {})
        self.assertFalse(result["normal"])
        self.assertIn("FDA483 — 사람 필요(1) · 놓침 · fda483-1 · 원천 https://example.gov/fda483-1", result["body"])

    def test_reconcile_missing_or_stale_is_not_silent(self):
        for report in (None, reconcile(src("fda483"), day=date(2026, 11, 25))):
            lines = cd.build_lines(DAY, report, ledger(incomplete=n2()), None)
            self.assertEqual([l.state for l in lines if l.key == "reconcile"], [cd.HUMAN])

    def test_unreachable_and_store_errors(self):
        lines = cd.reconcile_lines(reconcile(src("mfds_gmp", "source_unreachable", error="proxy down"),
                                             src("x", "disabled")), DAY)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].state, cd.HUMAN)
        self.assertIn("원천 불가 — proxy down", lines[0].reason)

    def test_ledger_difference_and_off(self):
        lines = cd.ledger_lines(ledger(admin_diff=2))
        self.assertEqual((lines[0].state, lines[0].count), (cd.HUMAN, 2))
        off = cd.ledger_lines(None)
        self.assertTrue(all(l.state == cd.OK for l in off))
        placeholder = cd.ledger_lines({"mfds_admin": {"measurement_status": "not_measured"},
                                       "mfds_recall": {"measurement_status": "not_measured"}})
        self.assertTrue(all(l.state == cd.OK for l in placeholder))

    def test_field_backlog_filling_only_when_shrinking(self):
        first = cd.field_lines(n2(fda483=5), None)[0]
        self.assertEqual(first.state, cd.FILLING)
        shrinking = cd.field_lines(n2(fda483=4), n2(fda483=5))[0]
        self.assertEqual(shrinking.state, cd.FILLING)
        self.assertIn("어제 5 → 4", shrinking.reason)
        stuck = cd.field_lines(n2(fda483=5), n2(fda483=5))[0]
        self.assertEqual(stuck.state, cd.HUMAN)
        self.assertEqual(stuck.items[:2], ["fda483-0", "fda483-1"])

    def test_field_result_absent_needs_human(self):
        self.assertEqual(cd.field_lines(None, None)[0].state, cd.HUMAN)

    def test_hc_line_on_monday_only(self):
        self.assertEqual(cd.hc_line(DAY), [])
        self.assertEqual(cd.hc_line(MONDAY)[0].state, cd.HUMAN)


class RefillOverlayTest(unittest.TestCase):
    """N3 자동 메움 결과 겹치기 — 아침 놓침을 그날 메웠으면 정상, 못 메웠으면 사람 필요(이유 붙임)."""

    def lines(self, *refill_lines, report=None, apply=True):
        return cd.build_lines(DAY, report or reconcile(src("wl", "missed", ["wl-1"])),
                              ledger(incomplete=n2()), None, refill(*refill_lines, apply=apply))

    def test_filled_same_day_turns_line_ok(self):
        lines = self.lines({"key": "wl", "state": "done", "reason": "재수집 → 재대조 정상"})
        wl = next(l for l in lines if l.key == "reconcile:wl")
        self.assertEqual(wl.state, cd.OK)
        self.assertTrue(cd.plan(DAY, lines, {})["normal"])

    def test_unfilled_stays_human_with_refill_reason(self):
        lines = self.lines({"key": "wl", "state": "wait", "reason": "원천 불가 2회째", "ids": ["wl-1"], "count": 1})
        wl = next(l for l in lines if l.key == "reconcile:wl")
        self.assertEqual(wl.state, cd.HUMAN)
        self.assertIn("자동 메움: 원천 대기 원천 불가 2회째", wl.reason)
        self.assertEqual(wl.items, ["wl-1"])

    def test_missing_or_stale_refill_needs_human(self):
        for rf in (None, refill(day=date(2026, 11, 25))):
            lines = cd.build_lines(DAY, reconcile(src("wl")), ledger(incomplete=n2()), None, rf)
            self.assertEqual([l.state for l in lines if l.key == "refill"], [cd.HUMAN])

    def test_refill_only_human_line_surfaces(self):
        lines = self.lines({"key": "fda483", "state": "human", "reason": "DB 에 행이 없는 놓침", "ids": ["d"], "count": 1},
                           {"key": "mfds_gmp", "state": "pending", "reason": "요청함", "ids": ["g"], "count": 1},
                           report=reconcile(src("wl")))
        extra = [(l.key, l.state) for l in lines if l.key.startswith("refill:")]
        self.assertEqual(extra, [("refill:fda483", cd.HUMAN)])

    def test_apply_off_is_noted_not_alarmed(self):
        lines = self.lines(report=reconcile(src("wl")), apply=False)
        note = [l for l in lines if l.key == "refill"]
        self.assertEqual([l.state for l in note], [cd.OK])
        self.assertIn("GRM_REFILL_APPLY", note[0].reason)


class PlanTest(unittest.TestCase):
    def test_day_count_carries_over(self):
        lines = cd.build_lines(DAY, reconcile(src("wl", "missed", ["wl-1"])), ledger(incomplete=n2()), None,
                               REFILL_OK)
        first = cd.plan(DAY, lines, {})
        self.assertNotIn("일째", first["title"])
        state = cd.parse_state(first["body"])
        self.assertEqual(state, {"reconcile:wl": "2026-11-26"})
        later = cd.plan(date(2026, 11, 28), lines, state)
        self.assertTrue(later["title"].endswith("(3일째)"))
        self.assertIn("**3일째**", later["body"])

    def test_resolved_item_drops_from_state(self):
        lines = cd.build_lines(DAY, reconcile(src("wl", "missed", ["wl-1"]), src("ema", "missed", ["e"])),
                               ledger(incomplete=n2()), None, REFILL_OK)
        state = cd.parse_state(cd.plan(DAY, lines, {})["body"])
        next_lines = cd.build_lines(DAY, reconcile(src("wl"), src("ema", "missed", ["e"])),
                                    ledger(incomplete=n2()), None, REFILL_OK)
        self.assertEqual(set(cd.plan(DAY, next_lines, state)["since"]), {"reconcile:ema"})


class MainTest(unittest.TestCase):
    def test_dry_run_without_store_flags_human(self):
        out = Path(self.id().split(".")[-1] + ".json")
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        with patch.dict("os.environ", {}, clear=True), patch("sys.stdout"):
            code = cd.main(["--reconcile", "nope.json", "--day", DAY.isoformat(), "--json", str(out)])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertFalse(data["normal"])
        self.assertIn("store", [l["key"] for l in data["lines"]])

    def test_publish_closes_open_issue_on_normal_day(self):
        calls = []
        with patch.object(cd, "_gh", side_effect=lambda a: calls.append(a) or ""):
            how = cd.publish({"normal": True, "summary": "ok"}, {"number": 7}, "")
        self.assertEqual(how, "closed #7")
        self.assertEqual(calls[-1], ["issue", "close", "7"])

    def test_publish_creates_with_labels(self):
        calls = []
        with patch.object(cd, "_gh", side_effect=lambda a: calls.append(a) or ""):
            cd.publish({"normal": False, "title": "t", "body": "b", "summary": "s"}, None, "")
        self.assertIn("completeness-daily,automation-warning", calls[-1])


if __name__ == "__main__":
    unittest.main()
