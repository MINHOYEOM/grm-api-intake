"""예약 지각 보완기(schedule_catchup.py) — 대상 목록·cron 정합 + 판정 단위 시험 (2026-11-24)."""
from __future__ import annotations

import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import schedule_catchup as sc  # noqa: E402

WF_DIR = os.path.join(ROOT, ".github", "workflows")
UTC = timezone.utc


def _read(name: str) -> str:
    with open(os.path.join(WF_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def workflow_crons(name: str) -> tuple[str, ...]:
    """on.schedule 의 cron 목록(PyYAML 없이 — CI 러너 의존성을 늘리지 않는다)."""
    out, in_sched = [], False
    for line in _read(name).splitlines():
        if re.match(r"^  schedule:\s*(#.*)?$", line):
            in_sched = True
            continue
        if in_sched:
            m = re.match(r"""^\s+-\s*cron:\s*['"]([^'"]+)['"]""", line)
            if m:
                out.append(m.group(1).strip())
            elif re.match(r"^  \S", line) or re.match(r"^\S", line):
                break
    return tuple(out)


def workflow_name(name: str) -> str:
    m = re.search(r"^name:\s*(.+?)\s*$", _read(name), re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def run(event: str, created: datetime, status: str = "completed", conclusion: str = "success",
        actor: str = "someone") -> dict:
    return {"event": event, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status,
            "conclusion": conclusion, "triggering_actor": {"login": actor}, "html_url": "u"}


BODY = next(t for t in sc.TARGETS if t.workflow == "grm-fda483-body-full-backfill.yml")


class TargetsMatchWorkflows(unittest.TestCase):
    def test_every_target_cron_equals_workflow_schedule(self):
        for t in sc.TARGETS:
            with self.subTest(t.workflow):
                self.assertTrue(os.path.exists(os.path.join(WF_DIR, t.workflow)), "워크플로 파일 없음")
                self.assertEqual(tuple(sorted(t.crons)), tuple(sorted(workflow_crons(t.workflow))),
                                 "TARGETS 의 cron 과 워크플로 on.schedule 이 어긋났다 — 둘을 같이 고쳐라")

    def test_every_target_accepts_dispatch_with_its_inputs(self):
        for t in sc.TARGETS:
            with self.subTest(t.workflow):
                text = _read(t.workflow)
                self.assertIn("workflow_dispatch", text)
                for key in t.inputs:
                    self.assertRegex(text, rf"(?m)^      {re.escape(key)}:\s*$", f"dispatch 입력 {key} 없음")

    def test_targets_are_unique_and_parse(self):
        names = [t.workflow for t in sc.TARGETS]
        self.assertEqual(len(names), len(set(names)))
        for t in sc.TARGETS:
            for c in t.crons:
                sc.Cron.parse(c)
            self.assertGreater(t.grace_min, 0)
            self.assertGreater(t.max_late_min, t.grace_min)

    def test_outbound_send_is_never_a_target(self):
        names = {t.workflow for t in sc.TARGETS}
        self.assertNotIn("grm-newsletter-send.yml", names)   # 외부 발송은 자동 재기동 금지

    def test_wakeup_paths_point_at_real_workflows(self):
        text = _read("grm-schedule-catchup.yml")
        block = text.split("workflow_run:", 1)[1].split("types:", 1)[0]
        listed = re.findall(r'-\s*"([^"]+)"', block)
        real = {workflow_name(n) for n in os.listdir(WF_DIR) if n.endswith(".yml")}
        self.assertGreaterEqual(len(listed), 2)
        for n in listed:
            self.assertIn(n, real, f"workflow_run 이 가리키는 워크플로 이름이 없다: {n}")
        # 예약에만 기대지 않는다 — 이벤트로 도는 CI 를 반드시 포함
        self.assertIn(workflow_name("grm-ci.yml"), listed)
        self.assertTrue(workflow_crons("grm-schedule-catchup.yml"))

    def test_catchup_itself_is_not_a_target(self):
        self.assertNotIn("grm-schedule-catchup.yml", {t.workflow for t in sc.TARGETS})


class CronParse(unittest.TestCase):
    def test_lists_ranges_steps(self):
        c = sc.Cron.parse("7,37 0-4 * * 1")
        self.assertTrue(c.matches(datetime(2026, 11, 23, 4, 37, tzinfo=UTC)))     # 월
        self.assertFalse(c.matches(datetime(2026, 11, 24, 4, 37, tzinfo=UTC)))    # 화
        s = sc.Cron.parse("41 */6 * * *")
        self.assertEqual(sorted(s.hours), [0, 6, 12, 18])

    def test_sunday_zero_and_seven(self):
        sun = datetime(2026, 11, 22, 22, 7, tzinfo=UTC)
        self.assertTrue(sc.Cron.parse("7 22 * * 0").matches(sun))
        self.assertTrue(sc.Cron.parse("7 22 * * 7").matches(sun))

    def test_latest_slot(self):
        now = datetime(2026, 11, 24, 1, 30, tzinfo=UTC)
        self.assertEqual(sc.latest_slot(["7 13 * * *"], now, 24 * 60), datetime(2026, 11, 23, 13, 7, tzinfo=UTC))
        self.assertIsNone(sc.latest_slot(["7 13 * * *"], now, 60))

    def test_bad_cron(self):
        with self.assertRaises(ValueError):
            sc.Cron.parse("7 13 * *")
        with self.assertRaises(ValueError):
            sc.Cron.parse("61 13 * * *")


class Decide(unittest.TestCase):
    slot = datetime(2026, 11, 23, 13, 7, tzinfo=UTC)

    def test_11_23_missing_run_dispatches_with_schedule_inputs(self):
        now = datetime(2026, 11, 24, 1, 20, tzinfo=UTC)          # 실제 사고: 이튿날까지 기록 없음
        d = sc.decide(BODY, now, [run("schedule", datetime(2026, 11, 22, 20, 17, tzinfo=UTC))])
        self.assertEqual(d.action, "dispatch")
        self.assertEqual(d.slot, self.slot)
        self.assertEqual(d.inputs, {"apply": "true", "since": "2026-10-09", "ocr_page_budget": "1500"})

    def test_within_grace_does_nothing(self):
        d = sc.decide(BODY, self.slot + timedelta(minutes=60), [])
        self.assertEqual(d.action, "not-due")       # 허용 지각 안 — 전날 슬롯은 보완 기한 밖

    def test_late_schedule_run_counts(self):
        now = self.slot + timedelta(hours=8)
        d = sc.decide(BODY, now, [run("schedule", self.slot + timedelta(hours=7), status="in_progress")])
        self.assertEqual(d.action, "ok")

    def test_own_dispatch_makes_it_idempotent(self):
        now = self.slot + timedelta(hours=3)
        d = sc.decide(BODY, now, [run("workflow_dispatch", self.slot + timedelta(hours=2), status="queued",
                                      conclusion=None, actor=sc.BOT_LOGIN)])
        self.assertEqual(d.action, "ok")

    def test_failed_catchup_run_alarms_but_does_not_redispatch(self):
        now = self.slot + timedelta(hours=3)
        d = sc.decide(BODY, now, [run("workflow_dispatch", self.slot + timedelta(hours=2),
                                      conclusion="failure", actor=sc.BOT_LOGIN)])
        self.assertEqual(d.action, "alarm-failed")

    def test_failed_schedule_run_is_left_to_its_own_alarm(self):
        now = self.slot + timedelta(hours=3)
        d = sc.decide(BODY, now, [run("schedule", self.slot + timedelta(minutes=5), conclusion="failure")])
        self.assertEqual(d.action, "ok")

    def test_run_before_slot_does_not_cover(self):
        now = self.slot + timedelta(hours=2)
        d = sc.decide(BODY, now, [run("workflow_dispatch", self.slot - timedelta(minutes=1))])
        self.assertEqual(d.action, "dispatch")

    def test_other_events_do_not_cover(self):
        now = self.slot + timedelta(hours=2)
        d = sc.decide(BODY, now, [run("push", self.slot + timedelta(minutes=10))])
        self.assertEqual(d.action, "dispatch")

    def test_too_old_slot_is_left_alone(self):
        weekly = sc.Target("x.yml", ("0 0 * * 1",), 30, 120)
        self.assertEqual(sc.decide(weekly, datetime(2026, 11, 23, 2, 0, tzinfo=UTC), []).action, "dispatch")
        self.assertEqual(sc.decide(weekly, datetime(2026, 11, 23, 2, 31, tzinfo=UTC), []).action, "not-due")

    def test_disabled_workflow_is_not_dispatched(self):
        d = sc.decide(BODY, self.slot + timedelta(hours=2), [], state="disabled_manually")
        self.assertEqual(d.action, "disabled")

    def test_monday_stranded_pr_slot(self):
        stranded = next(t for t in sc.TARGETS if t.workflow == "grm-stranded-batch-pr.yml")
        now = datetime(2026, 11, 23, 9, 50, tzinfo=UTC)          # 월 08:41Z 슬롯 + 69분
        d = sc.decide(stranded, now, [run("schedule", datetime(2026, 11, 23, 7, 40, tzinfo=UTC))])
        self.assertEqual((d.action, d.slot), ("dispatch", datetime(2026, 11, 23, 8, 41, tzinfo=UTC)))
        self.assertEqual(d.inputs, {"dry_run": "false"})


class FakeGH:
    def __init__(self, runs=None, state="active", fail_dispatch=False):
        self.runs, self.state, self.fail_dispatch = list(runs or []), state, fail_dispatch
        self.dispatched, self.alarms = [], []

    def workflow_state(self, wf):
        return self.state

    def runs_since(self, wf, since):
        return [r for r in self.runs if sc._ts(r["created_at"]) >= since]

    def dispatch(self, wf, ref, inputs):
        if self.fail_dispatch:
            raise RuntimeError("403")
        self.dispatched.append((wf, ref, inputs))
        self.runs.append(run("workflow_dispatch", datetime.now(UTC), status="queued", actor=sc.BOT_LOGIN))

    def alarm(self, marker, text):
        if any(m == marker for m, _ in self.alarms):
            return "이슈 #1 이미 표기됨"
        self.alarms.append((marker, text))
        return "이슈 #1 댓글"


class RunAll(unittest.TestCase):
    now = datetime(2026, 11, 24, 1, 20, tzinfo=UTC)

    def test_dispatch_once_then_idempotent(self):
        gh = FakeGH()
        sc.run_all(gh, self.now, ref="main", dry_run=False, targets=[BODY], sleeper=lambda s: None, log=lambda m: None)
        self.assertEqual(len(gh.dispatched), 1)
        # 다음 보완기 실행 — 방금 만든 실행이 슬롯을 덮는다
        gh.runs[-1]["created_at"] = (self.now + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sc.run_all(gh, self.now + timedelta(minutes=30), ref="main", dry_run=False, targets=[BODY],
                   sleeper=lambda s: None, log=lambda m: None)
        self.assertEqual(len(gh.dispatched), 1)

    def test_dry_run_never_writes(self):
        gh = FakeGH(fail_dispatch=True)
        ds = sc.run_all(gh, self.now, ref="main", dry_run=True, targets=[BODY], sleeper=lambda s: None, log=lambda m: None)
        self.assertEqual((ds[0].action, gh.dispatched, gh.alarms), ("dispatch", [], []))

    def test_dispatch_failure_alarms_once_per_slot(self):
        gh = FakeGH(fail_dispatch=True)
        a = sc.run_all(gh, self.now, ref="main", dry_run=False, targets=[BODY], sleeper=lambda s: None, log=lambda m: None)
        b = sc.run_all(gh, self.now + timedelta(minutes=30), ref="main", dry_run=False, targets=[BODY],
                       sleeper=lambda s: None, log=lambda m: None)
        self.assertEqual(a[0].action, "alarm-dispatch-failed")
        self.assertTrue(a[0].alerted)
        self.assertFalse(b[0].alerted)
        self.assertEqual(len(gh.alarms), 1)
        self.assertIn("grm-fda483-body-full-backfill.yml:2026-11-23T13:07Z", gh.alarms[0][0])


if __name__ == "__main__":
    unittest.main()
