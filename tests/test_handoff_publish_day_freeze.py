# -*- coding: utf-8 -*-
"""발행일(월) handoff 동결 — 07:00 KST 이후 같은 날 재실행은 handoff 를 다시 쓰지 않는다 (2026-11-16).

월요일 수집을 여러 번(자정 직후 추가 크론·PC dispatch) 돌리므로, 편집팀이 읽기 시작한 뒤 handoff 가
바뀌지 않는다는 것을 잠근다. 워크플로의 월요일 추가 크론도 여기서 확인한다.
"""
from __future__ import annotations

import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import grm_handoff

KST = timezone(timedelta(hours=9))
MON = date(2026, 11, 16)          # 월요일
TUE = date(2026, 11, 17)
HID = grm_handoff.handoff_id_for(MON)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kst(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


class FrozenRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.dict(os.environ, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_monday_before_seven_not_frozen(self) -> None:
        self.assertFalse(grm_handoff.publish_day_handoff_frozen(MON, _kst(MON, 6, 59)))
        self.assertFalse(grm_handoff.publish_day_handoff_frozen(MON, _kst(MON, 0, 7)))

    def test_monday_from_seven_frozen(self) -> None:
        self.assertTrue(grm_handoff.publish_day_handoff_frozen(MON, _kst(MON, 7, 0)))
        self.assertTrue(grm_handoff.publish_day_handoff_frozen(MON, _kst(MON, 8, 1)))
        self.assertTrue(grm_handoff.publish_day_handoff_frozen(MON, _kst(MON, 23, 59)))

    def test_monday_run_writing_after_midnight_frozen(self) -> None:
        self.assertTrue(grm_handoff.publish_day_handoff_frozen(MON, _kst(TUE, 0, 10)))

    def test_utc_now_converted_to_kst(self) -> None:
        # 22:01 UTC 일요일 = 07:01 KST 월요일
        utc = datetime(2026, 11, 15, 22, 1, tzinfo=timezone.utc)
        self.assertTrue(grm_handoff.publish_day_handoff_frozen(MON, utc))
        utc = datetime(2026, 11, 15, 21, 59, tzinfo=timezone.utc)
        self.assertFalse(grm_handoff.publish_day_handoff_frozen(MON, utc))

    def test_other_weekdays_never_frozen(self) -> None:
        self.assertFalse(grm_handoff.publish_day_handoff_frozen(TUE, _kst(TUE, 9, 0)))
        self.assertFalse(grm_handoff.publish_day_handoff_frozen(date(2026, 11, 15), _kst(MON, 8, 0)))

    def test_refresh_switch_unfreezes(self) -> None:
        with mock.patch.dict(os.environ, {"GRM_QUEUE_HANDOFF_REFRESH": "true"}):
            self.assertFalse(grm_handoff.publish_day_handoff_frozen(MON, _kst(MON, 7, 30)))

    def test_same_switch_and_hour_as_db_freeze(self) -> None:
        import grm_queue
        self.assertEqual(grm_handoff.PUBLISH_DAY_FREEZE_HOUR_KST, grm_queue.PUBLISH_DAY_FREEZE_HOUR_KST)
        with mock.patch.dict(os.environ, {grm_handoff.HANDOFF_REFRESH_ENV: "true"}):
            self.assertTrue(grm_queue.handoff_refresh_requested())


def _page(status: str = "New", rows: int = 139) -> dict:
    return {"id": "pid", "url": "https://notion.so/h",
            "properties": {
                "Status": {"select": {"name": status}},
                grm_handoff.PROP_HEADLINE: {"rich_text": [
                    {"plain_text": f"Routine New-only handoff ({rows} rows)",
                     "text": {"content": f"Routine New-only handoff ({rows} rows)"}}]},
            }}


class EmitFreezeTest(unittest.TestCase):
    """동결이면 STALE 봉인·reconcile·행 조회·upsert 를 하나도 부르지 않는다."""

    def _emit(self, now: datetime, existing: dict | None, env: dict | None = None):
        calls: list[str] = []

        def track(name, ret):
            def _inner(*a, **k):
                calls.append(name)
                return ret
            return _inner

        frozen: list[str] = []
        with mock.patch.dict(os.environ, env or {}, clear=True), \
             mock.patch.object(grm_handoff, "notion_find_handoff_page",
                               side_effect=track("find", existing)), \
             mock.patch.object(grm_handoff, "notion_stale_prior_open_handoffs",
                               side_effect=track("stale", 0)), \
             mock.patch.object(grm_handoff, "notion_reconcile_handoff_refs",
                               side_effect=track("reconcile", {})), \
             mock.patch.object(grm_handoff, "notion_query_new_intake_rows",
                               side_effect=track("query", [])), \
             mock.patch.object(grm_handoff, "notion_upsert_routine_handoff",
                               side_effect=track("upsert", ("pid", "new-url"))), \
             mock.patch.object(grm_handoff, "notion_mark_rows_handoff_ref",
                               side_effect=track("mark", (0, 0))):
            out = grm_handoff.emit_routine_handoff(
                "tok", "db", MON, 30, _kst(MON, 6, 40), display_window_days=7,
                freeze_now=now, frozen_out=frozen)
        return out, calls, frozen

    def test_frozen_existing_page_untouched(self) -> None:
        for status in ("New", "Processed", "Skipped"):
            out, calls, frozen = self._emit(_kst(MON, 7, 52), _page(status, 139))
            self.assertEqual(calls, ["find"], status)
            self.assertEqual(out, (139, "https://notion.so/h"))
            self.assertEqual(frozen, [status])

    def test_frozen_but_no_page_yet_creates_it(self) -> None:
        # 늦게 온 첫 수집 — 페이지가 없으면 평소대로 만든다.
        out, calls, frozen = self._emit(_kst(MON, 7, 52), None)
        self.assertIn("upsert", calls)
        self.assertEqual(frozen, [])
        self.assertEqual(out[1], "new-url")

    def test_before_seven_regenerates(self) -> None:
        # 07:00 전 재실행(483 회수 뒤 재생성)은 종전대로 갱신한다 — find 도 부르지 않는다(v1 경로).
        out, calls, frozen = self._emit(_kst(MON, 6, 30), _page())
        self.assertNotIn("find", calls)
        self.assertIn("upsert", calls)
        self.assertEqual(frozen, [])

    def test_refresh_switch_regenerates_after_seven(self) -> None:
        out, calls, frozen = self._emit(_kst(MON, 7, 10), _page(),
                                        env={"GRM_QUEUE_HANDOFF_REFRESH": "true"})
        self.assertIn("upsert", calls)
        self.assertEqual(frozen, [])

    def test_collect_intake_passes_frozen_out(self) -> None:
        with open(os.path.join(ROOT, "collect_intake.py"), encoding="utf-8") as f:
            src = f.read()
        call = src[src.index("handoff_row_count, handoff_url = emit_routine_handoff("):]
        call = call[:call.index(")\n")]
        self.assertIn("frozen_out=handoff_frozen", call)
        self.assertNotIn("freeze_now", call)   # 운영은 항상 쓰기 직전 실제 시각


class MondayCronTest(unittest.TestCase):
    """월요일 수집을 일찍 띄우는 추가 크론 — 전부 월요일 KST(일요일 15:00 UTC 이후)여야 한다."""

    def setUp(self) -> None:
        with open(os.path.join(ROOT, ".github", "workflows", "grm-intake.yml"), encoding="utf-8") as f:
            self.wf = f.read()
        self.crons = re.findall(r"^\s*- cron: '([^']+)'", self.wf, re.M)

    def test_daily_cron_kept(self) -> None:
        self.assertIn("17 18 * * *", self.crons)

    def test_monday_extra_crons_are_monday_kst_after_midnight(self) -> None:
        extra = [c for c in self.crons if c != "17 18 * * *"]
        self.assertGreaterEqual(len(extra), 2)
        for c in extra:
            minute, hour, dom, mon, dow = c.split()
            self.assertEqual((dom, mon, dow), ("*", "*", "0"), c)     # 일요일 UTC
            self.assertTrue(15 <= int(hour) <= 17, c)                 # 00:00~02:59 KST 월요일
            self.assertNotIn(int(minute), (0, 30), c)                 # 정각·30분 부하 피크 피함

    def test_concurrency_serializes_runs(self) -> None:
        block = self.wf[self.wf.index("concurrency:"):][:120]
        self.assertIn("group: grm-intake", block)
        self.assertIn("cancel-in-progress: false", block)


if __name__ == "__main__":
    unittest.main()
