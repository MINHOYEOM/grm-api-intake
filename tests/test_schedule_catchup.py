"""예약 지각 보완기 — 예약 전수 분류·대상 정합·판정 시험."""
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


def has_workflow_schedule(name: str) -> bool:
    """on 블록에서 schedule 키를 찾는다. cron 값의 표기 방식과 무관하다."""
    for block in re.split(r"(?m)^(?=[^\s#])", _read(name)):
        trigger = re.match(r"""^(?:on|'on'|"on")[ \t]*:""", block)
        if trigger:
            return bool(re.search(r"""(?m)(?:^[ \t]+|[{,][ \t]*)(?:schedule|'schedule'|"schedule")[ \t]*:""",
                                  block[trigger.end():]))
    return False


def workflow_name(name: str) -> str:
    m = re.search(r"^name:\s*(.+?)\s*$", _read(name), re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def entry_scripts(name: str) -> set[str]:
    """워크플로가 `python X.py` / `python -m X` 로 실행하는 루트 모듈."""
    found = set(re.findall(r"python3?\s+(?:-m\s+)?([A-Za-z_]\w*)(?:\.py)?\b", _read(name)))
    return {m for m in found if os.path.exists(os.path.join(ROOT, m + ".py"))}


def direct_imports(mod: str) -> set[str]:
    with open(os.path.join(ROOT, mod + ".py"), encoding="utf-8") as fh:
        text = fh.read()
    out: set[str] = set()
    for a, b in re.findall(r"^\s*(?:from\s+(\w+)\s+import|import\s+([\w, ]+))", text, re.M):
        for n in (a or b).split(","):
            out.add(n.strip().split(" ")[0])
    return out


def hits_fda(name: str) -> bool:
    """진입 스크립트가 FDA 요청 모듈이거나 그것을 직접 import 하면 FDA 계열."""
    fetch = set(sc.FDA_FETCH_MODULES)
    return any(m in fetch or direct_imports(m) & fetch for m in entry_scripts(name))


def run(event: str, created: datetime, status: str = "completed", conclusion: str = "success",
        actor: str = "someone") -> dict:
    return {"event": event, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status,
            "conclusion": conclusion, "triggering_actor": {"login": actor}, "html_url": "u"}


BODY = next(t for t in sc.TARGETS if t.workflow == "grm-fda483-body-full-backfill.yml")
INTAKE = next(t for t in sc.TARGETS if t.workflow == "grm-intake.yml")
OCR = next(t for t in sc.TARGETS if t.workflow == "grm-fda483-ocr-recovery.yml")


class TargetsMatchWorkflows(unittest.TestCase):
    def test_every_scheduled_workflow_is_classified(self):
        scheduled = {name for name in os.listdir(WF_DIR)
                     if name.endswith((".yml", ".yaml")) and has_workflow_schedule(name)}
        self.assertSetEqual(scheduled, set(sc.CRON_AUDIT),
                            "새 예약은 CRON_AUDIT 에 분류·근거를 적고, 사라진 예약은 표에서 빼라")
        for name, (category, reason) in sc.CRON_AUDIT.items():
            with self.subTest(name):
                self.assertIn(category, {"A", "B", "C", "D"})
                self.assertTrue(reason.strip())

    def test_targets_match_data_gap_category(self):
        data_gaps = {name for name, (category, _) in sc.CRON_AUDIT.items() if category == "A"}
        self.assertSetEqual({t.workflow for t in sc.TARGETS}, data_gaps)

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
                for key in sc.APPLY_INPUTS.get(t.workflow, sc.CHECK_INPUTS.get(t.workflow, {})):
                    self.assertRegex(text, rf"(?m)^      {re.escape(key)}:\s*$", f"dispatch 입력 {key} 없음")

    def test_targets_are_unique_and_parse(self):
        names = [t.workflow for t in sc.TARGETS]
        self.assertEqual(len(names), len(set(names)))
        for t in sc.TARGETS:
            for c in t.crons:
                sc.Cron.parse(c)
            self.assertGreater(t.grace_min, 0)
            self.assertGreater(t.max_late_min, t.grace_min)

    def test_outbound_send_is_never_a_target_or_allowed(self):
        outbound = {"grm-newsletter-send.yml", "grm-ops-decision-mail.yml", "grm-watchlist-notify.yml"}
        names = {t.workflow for t in sc.TARGETS}
        self.assertEqual(outbound.intersection(names | sc.APPLY_INPUTS.keys() | sc.CHECK_INPUTS.keys()), set())

    def test_only_approved_targets_can_apply(self):
        self.assertEqual(sc.APPLY_INPUTS.keys(), {BODY.workflow, "grm-db-backup.yml", OCR.workflow})
        self.assertTrue(sc.APPLY_INPUTS.keys().isdisjoint(sc.CHECK_INPUTS))
        self.assertLessEqual(sc.APPLY_INPUTS.keys() | sc.CHECK_INPUTS.keys(), {t.workflow for t in sc.TARGETS})

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

    def test_fda_host_flag_matches_workflow_code(self):
        for t in sc.TARGETS:
            with self.subTest(t.workflow):
                self.assertEqual(t.fda_host, hits_fda(t.workflow),
                                 f"fda_host 분류가 워크플로 코드와 다르다(진입 스크립트 {sorted(entry_scripts(t.workflow))})")

    def test_fda_fetch_modules_really_call_www_fda_gov(self):
        for m in sc.FDA_FETCH_MODULES:
            with open(os.path.join(ROOT, m + ".py"), encoding="utf-8") as fh:
                self.assertIn("www.fda.gov", fh.read(), m)

    def test_known_fda_families(self):
        fda = {t.workflow for t in sc.TARGETS if t.fda_host}
        self.assertEqual(fda, {"grm-intake.yml", "grm-fda483-body-full-backfill.yml", "grm-fda483-ocr-recovery.yml"})

    def test_intake_is_a_capped_target(self):
        self.assertEqual((INTAKE.dispatch_cap, INTAKE.cap_window_min), (1, 12 * 60))
        self.assertEqual(sc.TARGETS[0].workflow, "grm-intake.yml")   # FDA 계열 우선순위: 수집 먼저

    def test_catchup_itself_is_not_a_target(self):
        self.assertNotIn("grm-schedule-catchup.yml", {t.workflow for t in sc.TARGETS})


class CronParse(unittest.TestCase):
    def test_lists_ranges_steps(self):
        c = sc.Cron.parse("7,37 0-5 * * 1")
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

    def test_11_24_ocr_gap_dispatches_approved_recovery(self):
        slot = datetime(2026, 11, 24, 23, 37, tzinfo=UTC)
        d = sc.decide(OCR, slot + timedelta(hours=2), [])
        self.assertEqual((d.action, d.slot), ("dispatch", slot))
        self.assertEqual(d.inputs, {"apply": "true", "mode": "recoverable", "target": "all", "ocr_page_budget": "600"})

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
        self.assertEqual(sc.decide(weekly, datetime(2026, 11, 23, 2, 0, tzinfo=UTC), []).action, "alarm-only")
        self.assertEqual(sc.decide(weekly, datetime(2026, 11, 23, 2, 31, tzinfo=UTC), []).action, "not-due")

    def test_disabled_workflow_is_not_dispatched(self):
        d = sc.decide(BODY, self.slot + timedelta(hours=2), [], state="disabled_manually")
        self.assertEqual(d.action, "disabled")

    def test_monday_stranded_pr_slot(self):
        stranded = next(t for t in sc.TARGETS if t.workflow == "grm-stranded-batch-pr.yml")
        now = datetime(2026, 11, 23, 9, 50, tzinfo=UTC)          # 월 08:41Z 슬롯 + 69분
        d = sc.decide(stranded, now, [run("schedule", datetime(2026, 11, 23, 7, 40, tzinfo=UTC))])
        self.assertEqual((d.action, d.slot, d.inputs), ("alarm-only", datetime(2026, 11, 23, 8, 41, tzinfo=UTC), {}))


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
        self.assertIn("슬롯 2026-11-23 13:07 UTC · 모드 apply", gh.alarms[0][1])
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

    def test_missing_slots_use_only_the_workflow_policy(self):
        cases = {
            "grm-intake.yml": ("2026-11-23T18:17Z", {"dry_run": "true"}, "점검"),
            "grm-delta-bridge.yml": ("2026-11-22T22:07Z", {"compare_only": "true", "publish_date": "2026-11-23"}, "점검"),
            "grm-mfds-gmp-cert-monthly.yml": ("2026-11-01T18:40Z", {"mode": "dry-run"}, "점검"),
            "grm-rum-analytics.yml": ("2026-11-23T04:30Z", {"probe": "true"}, "점검"),
            "grm-stranded-batch-pr.yml": ("2026-11-23T08:41Z", None, "경보"),
            "grm-publish-watchdog.yml": ("2026-11-23T00:13Z", None, "경보"),
            "grm-db-backup.yml": ("2026-11-22T20:37Z", {}, "apply"),
        }
        targets = {t.workflow: t for t in sc.TARGETS}
        self.assertEqual(cases.keys() - {"grm-db-backup.yml"}, targets.keys() - sc.APPLY_INPUTS.keys())
        for name, (stamp, inputs, mode) in cases.items():
            with self.subTest(name):
                target = targets[name]
                slot = sc._ts(stamp)
                now = slot + timedelta(minutes=target.grace_min)
                gh = FakeGH()
                sc.run_all(gh, now, ref="main", dry_run=False, targets=[target],
                           sleeper=lambda s: None, log=lambda m: None)
                self.assertEqual(gh.dispatched, [] if inputs is None else [(name, "main", inputs)])
                self.assertEqual(len(gh.alarms), 1)
                self.assertIn(f"슬롯 {slot:%Y-%m-%d %H:%M} UTC · 모드 {mode}", gh.alarms[0][1])
                if gh.runs:
                    gh.runs[-1]["created_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                again = sc.run_all(gh, now, ref="main", dry_run=False, targets=[target],
                                   sleeper=lambda s: None, log=lambda m: None)
                if mode == "점검":
                    self.assertEqual(again[0].action, "check-recorded")
                self.assertEqual(len(gh.alarms), 1)


class IntakeCap(unittest.TestCase):
    slot = datetime(2026, 11, 23, 18, 17, tzinfo=UTC)

    def test_11_23_intake_gap_dispatches(self):
        now = datetime(2026, 11, 23, 22, 30, tzinfo=UTC)      # 슬롯 + 4h13m, 실행 없음(실사고)
        d = sc.decide(INTAKE, now, [run("schedule", datetime(2026, 11, 22, 23, 37, tzinfo=UTC))])
        self.assertEqual((d.action, d.slot, d.inputs), ("dispatch", self.slot, {"dry_run": "true"}))

    def test_within_four_hours_waits_for_late_schedule(self):
        d = sc.decide(INTAKE, self.slot + timedelta(hours=3, minutes=50), [])
        self.assertNotEqual(d.action, "dispatch")

    def test_manual_dispatch_within_12h_caps(self):
        now = datetime(2026, 11, 23, 22, 30, tzinfo=UTC)
        manual = run("workflow_dispatch", self.slot - timedelta(hours=2))     # 슬롯 전 사람 수동 실행
        d = sc.decide(INTAKE, now, [manual])
        self.assertEqual(d.action, "capped")

    def test_old_manual_dispatch_does_not_cap(self):
        now = datetime(2026, 11, 23, 22, 30, tzinfo=UTC)
        d = sc.decide(INTAKE, now, [run("workflow_dispatch", now - timedelta(hours=13))])
        self.assertEqual(d.action, "dispatch")

    def test_sunday_monday_slots(self):
        now = datetime(2026, 11, 29, 20, 30, tzinfo=UTC)      # 일 16:19Z 슬롯 + 4h11m
        d = sc.decide(INTAKE, now, [])
        self.assertEqual((d.action, d.slot), ("dispatch", datetime(2026, 11, 29, 16, 19, tzinfo=UTC)))


class FdaGate(unittest.TestCase):
    now = datetime(2026, 11, 24, 3, 30, tzinfo=UTC)

    def _gh(self, extra=None):
        gh = FakeGH(runs=extra or [])
        return gh

    def test_only_one_fda_dispatch_per_tick(self):
        gh = self._gh()
        ds = sc.run_all(gh, self.now, ref="main", dry_run=False, targets=[BODY, OCR, INTAKE],
                        sleeper=lambda s: None, log=lambda m: None)
        acts = {d.workflow: d.action for d in ds}
        self.assertEqual(len(gh.dispatched), 1)
        self.assertEqual(list(acts.values()).count("dispatch"), 1)
        self.assertGreaterEqual(list(acts.values()).count("deferred"), 1)

    def test_priority_follows_targets_order(self):
        planned = [(t, sc.Decision(t.workflow, "dispatch")) for t in (INTAKE, BODY, OCR)]
        sc.gate_fda(planned, [])
        self.assertEqual([d.action for _, d in planned], ["dispatch", "deferred", "deferred"])

    def test_running_fda_job_defers_all(self):
        planned = [(t, sc.Decision(t.workflow, "dispatch")) for t in (BODY, OCR)]
        sc.gate_fda(planned, ["grm-intake.yml"])
        self.assertEqual([d.action for _, d in planned], ["deferred", "deferred"])

    def test_non_fda_targets_are_not_gated(self):
        stranded = next(t for t in sc.TARGETS if t.workflow == "grm-stranded-batch-pr.yml")
        planned = [(BODY, sc.Decision(BODY.workflow, "dispatch")), (stranded, sc.Decision(stranded.workflow, "dispatch"))]
        sc.gate_fda(planned, ["grm-intake.yml"])
        self.assertEqual([d.action for _, d in planned], ["deferred", "dispatch"])

    def test_in_progress_intake_run_blocks_body_full(self):
        busy = run("schedule", self.now - timedelta(minutes=5), status="in_progress")
        gh = FakeGH(runs=[busy])
        # 같은 FakeGH 가 모든 워크플로에 같은 runs 를 돌려주므로, BODY 슬롯(11-23 13:07)은 이 실행이 덮지 않게 이전 시각으로
        busy["created_at"] = "2026-11-23T12:00:00Z"
        ds = sc.run_all(gh, self.now, ref="main", dry_run=False, targets=[INTAKE, BODY],
                        sleeper=lambda s: None, log=lambda m: None)
        self.assertEqual(gh.dispatched, [])
        self.assertIn("deferred", [d.action for d in ds])


if __name__ == "__main__":
    unittest.main()
