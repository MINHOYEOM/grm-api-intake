"""수집 완전성 N3 — 자동 메움 공통 고리(completeness_refill.py).

합격 조건(설계 §N3 수용 기준):
  1. 빠짐 주입 — 대조가 놓친 문서는 그 회차 결과에 반드시 남는다(경보 대상).
  2. 자동 메움 — 다음 실행이 사람 개입 없이 재수집하고, 재대조가 정상으로 닫힌다.
  3. 원천 불가 — '정상'으로 닫히지 않고 간격을 늘려 다시 보며, 복구 뒤 회차에서 메워진다.
  4. 가드 — 기계 재시도로 풀지 않고 사람 필요로 남긴다.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import completeness_refill as cr

NOW = datetime(2026, 11, 27, 9, 30, tzinfo=cr.KST)
DAY = NOW.date().isoformat()


def reconcile(*sources, day=DAY):
    return {"run_date": day, "sources": list(sources)}


def src(key, verdict="ok", missed=(), error="", published="2026-11-26"):
    return {"key": key, "verdict": verdict, "error": error,
            "missed": [{"document_id": d, "url": f"https://example.gov/{d}", "published": published} for d in missed]}


def fields(**ids):
    return {"sources": {k: {"incomplete_ids": list(v)} for k, v in ids.items()}}


class FakeWorld:
    """원천 = source 목록, DB = store. WL 재수집 도구와 재대조를 흉내 낸다."""

    def __init__(self, source_ids, store_ids=(), *, reachable=True, guard=False):
        self.source = set(source_ids)
        self.store = set(store_ids)
        self.reachable = reachable
        self.guard = guard
        self.runs: list[list[str]] = []
        self.dispatches: list[tuple[str, dict]] = []
        self.dispatch_outcome = "sent"
        self.reconciles: list[list[str]] = []

    def runner(self, argv):
        self.runs.append(argv)
        ids = argv[argv.index("--ids") + 1:argv.index("--report-path")]
        report_path = argv[argv.index("--report-path") + 1]
        apply = "--apply" in argv
        unresolved, reasons = [], {}
        for i in ids:
            if self.guard:
                unresolved.append(i)
                reasons["violations_shrunk"] = reasons.get("violations_shrunk", 0) + 1
            elif not self.reachable:
                unresolved.append(i)
            elif apply:
                self.store.add(i)
        Path(report_path).write_text(json.dumps({"unresolved_ids": unresolved, "failure_reasons": reasons}),
                                     encoding="utf-8")
        return 1 if unresolved else 0

    def reconcile(self, keys):
        self.reconciles.append(list(keys))
        out = []
        for k in keys:
            if not self.reachable:
                out.append(src(k, "source_unreachable", error="HTTP 503"))
            else:
                miss = sorted(self.source - self.store) if k == "wl" else []
                out.append(src(k, "missed" if miss else "ok", miss))
        return reconcile(*out)

    def dispatcher(self, workflow, inputs):
        self.dispatches.append((workflow, inputs))
        return self.dispatch_outcome


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def run_pass(self, world, now=NOW, *, reconcile_report=None, field_report=None, state=None, apply=True):
        return cr.run_pass(now, reconcile=reconcile_report, fields=field_report, prev_state=state, apply=apply,
                           workdir=self.dir, runner=world.runner, dispatcher=world.dispatcher,
                           reconciler=world.reconcile)

    @staticmethod
    def states(payload, key):
        return [l["state"] for l in payload["lines"] if l["key"] == key]

class PassTest(_Base):
    # 1·2 ---------------------------------------------------------------
    def test_injected_missed_doc_is_filled_and_rechecked_same_pass(self):
        world = FakeWorld({"wl-1", "wl-2"}, {"wl-2"})
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("wl", "missed", ["wl-1"])))
        self.assertEqual(world.runs[0][:3], ["backfill_wl_body_recovery.py", "--ids", "wl-1"])
        self.assertIn("--apply", world.runs[0])
        self.assertIn("wl-1", world.store)
        self.assertEqual(world.reconciles, [["wl"]])
        self.assertEqual(self.states(payload, "wl"), [cr.DONE])
        self.assertEqual(cr.pending_ids(state["sources"]["wl"]), [])
        self.assertEqual(payload["remaining"], {})

    def test_injected_missed_doc_is_never_silent_without_apply(self):
        world = FakeWorld({"wl-1"})
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("wl", "missed", ["wl-1"])), apply=False)
        self.assertNotIn("--apply", world.runs[0])
        self.assertEqual(world.reconciles, [])
        self.assertEqual(self.states(payload, "wl"), [cr.PENDING])
        self.assertEqual(payload["remaining"]["wl"], {"count": 1, "oldest": DAY})

    def test_unfilled_doc_carries_to_next_hourly_pass(self):
        world = FakeWorld({"wl-1"}, reachable=True)
        world.runner = lambda argv, w=world: (FakeWorld.runner(w, argv), w.store.discard("wl-1"))[0]
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("wl", "missed", ["wl-1"])))
        self.assertEqual(self.states(payload, "wl"), [cr.PENDING])
        self.assertEqual(state["sources"]["wl"]["first_seen"], {"wl-1": DAY})
        # 다음 회차: 대조 파일 없이 상태만으로 다시 대조하고 다시 받는다.
        world2 = FakeWorld({"wl-1"})
        payload2, state2 = self.run_pass(world2, NOW + timedelta(hours=1), state=state)
        self.assertEqual(world2.reconciles[0], ["wl"])
        self.assertEqual(len(world2.runs), 1)
        self.assertEqual(self.states(payload2, "wl"), [cr.DONE])

    # 3 -----------------------------------------------------------------
    def test_unreachable_is_not_closed_and_backs_off_then_fills(self):
        world = FakeWorld({"wl-1"}, reachable=False)
        state = cr.carry_state(None, DAY)
        cr.absorb(NOW, state, cr.observe(reconcile(src("wl", "missed", ["wl-1"])), None))
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("wl", "source_unreachable",
                                                                             error="HTTP 503")), state=state)
        self.assertEqual(world.runs, [])
        self.assertEqual(self.states(payload, "wl"), [cr.WAIT])
        self.assertEqual(payload["remaining"]["wl"]["count"], 1)       # 놓침이 지워지지 않았다
        self.assertEqual(state["sources"]["wl"]["next_try"], cr._iso(NOW + timedelta(hours=1)))
        # 간격 안의 회차는 원천을 두드리지 않는다.
        _, state = self.run_pass(world, NOW + timedelta(minutes=30), state=state)
        self.assertEqual(world.reconciles, [])
        # 간격이 지나면 다시 보고, 또 불가면 간격이 두 배.
        t2 = NOW + timedelta(hours=1)
        _, state = self.run_pass(world, t2, state=state)
        self.assertEqual(world.reconciles, [["wl"]])
        self.assertEqual(state["sources"]["wl"]["next_try"], cr._iso(t2 + timedelta(hours=2)))
        self.assertEqual(len(state["sources"]["wl"]["attempt_log"]), 2)
        # 복구 뒤 회차에서 메워진다.
        world.reachable = True
        payload, state = self.run_pass(world, t2 + timedelta(hours=2), state=state)
        self.assertIn("wl-1", world.store)
        self.assertEqual(self.states(payload, "wl"), [cr.DONE])

    # 4 -----------------------------------------------------------------
    def test_guard_hit_needs_human_and_is_not_retried_that_day(self):
        world = FakeWorld({"wl-1"}, guard=True)
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("wl", "missed", ["wl-1"])))
        self.assertIn(cr.HUMAN, self.states(payload, "wl"))
        self.assertEqual(state["sources"]["wl"]["guard_ids"], ["wl-1"])
        payload2, state2 = self.run_pass(world, NOW + timedelta(hours=1), state=state)
        self.assertEqual(len(world.runs), 1)                           # 다시 돌리지 않았다
        self.assertIn(cr.HUMAN, self.states(payload2, "wl"))
        # 다음 날엔 가드 기록을 풀고 첫 관측일은 잇는다.
        nxt = cr.carry_state(state2, "2026-11-28")
        self.assertNotIn("guard_ids", nxt["sources"]["wl"])
        self.assertEqual(nxt["sources"]["wl"]["first_seen"], {"wl-1": DAY})

    def test_missing_tool_report_needs_human(self):
        world = FakeWorld({"wl-1"})
        world.runner = lambda argv: 2
        payload, _ = self.run_pass(world, reconcile_report=reconcile(src("wl", "missed", ["wl-1"])))
        self.assertIn(cr.HUMAN, self.states(payload, "wl"))


class DispatchTest(_Base):
    def test_483_incomplete_dispatches_named_once_per_day(self):
        world = FakeWorld(set())
        ids = [f"fda483-{i}" for i in range(35)]
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("fda483")),
                                       field_report=fields(fda483=ids))
        wf, inputs = world.dispatches[0]
        self.assertEqual(wf, "grm-fda483-ocr-recovery.yml")
        self.assertEqual(inputs["mode"], "named")
        self.assertEqual(inputs["apply"], "true")
        self.assertEqual(inputs["doc_ids"].split(","), ids[:30])        # 상한 30
        self.assertEqual(self.states(payload, "fda483"), [cr.PENDING, cr.PENDING])
        _, state = self.run_pass(world, NOW + timedelta(hours=1), state=state)
        self.assertEqual(len(world.dispatches), 1)                     # 같은 날 다시 부르지 않는다
        self.assertEqual(len(cr.pending_ids(state["sources"]["fda483"])), 35)   # 칸 대조 없는 회차도 결손을 잇는다

    def test_483_missed_without_row_needs_human(self):
        world = FakeWorld(set())
        payload, _ = self.run_pass(world, reconcile_report=reconcile(src("fda483", "missed", ["fda483-x"])))
        self.assertEqual(world.dispatches, [])
        self.assertEqual(self.states(payload, "fda483"), [cr.HUMAN])

    def test_one_dispatch_per_concurrency_group_per_pass(self):
        world = FakeWorld(set())
        payload, state = self.run_pass(world, reconcile_report=reconcile(src("mfds_admin", "missed", ["a1"])),
                                       field_report=fields(mfds_gmp=["gmpinspect-1"]))
        self.assertEqual(len(world.dispatches), 1)
        later = NOW + timedelta(hours=1)
        _, _ = self.run_pass(world, later, state=state)
        self.assertEqual(len(world.dispatches), 2)
        workflows = sorted(w for w, _ in world.dispatches)
        self.assertEqual(workflows, ["grm-mfds-backfill.yml", "grm-mfds-recollect-attachment.yml"])
        admin = next(i for w, i in world.dispatches if w == "grm-mfds-backfill.yml")
        self.assertEqual(admin, {"source": "admin-action", "from_date": "2026-11-26", "to_date": "2026-11-26",
                                 "dry_run": "false"})

    def test_busy_workflow_is_retried_next_pass(self):
        world = FakeWorld(set())
        world.dispatch_outcome = "busy"
        _, state = self.run_pass(world, reconcile_report=reconcile(src("fda483")), field_report=fields(fda483=["d"]))
        self.assertNotIn("dispatched_day", state["sources"]["fda483"])
        world.dispatch_outcome = "sent"
        _, state = self.run_pass(world, NOW + timedelta(hours=1), state=state)
        self.assertEqual(len(world.dispatches), 2)
        self.assertEqual(state["sources"]["fda483"]["dispatched_day"], DAY)

    def test_failed_dispatch_needs_human(self):
        world = FakeWorld(set())
        world.dispatch_outcome = "failed"
        payload, _ = self.run_pass(world, reconcile_report=reconcile(src("fda483")), field_report=fields(fda483=["d"]))
        self.assertIn(cr.HUMAN, self.states(payload, "fda483"))

    def test_without_apply_nothing_is_dispatched(self):
        world = FakeWorld(set())
        payload, _ = self.run_pass(world, reconcile_report=reconcile(src("fda483")),
                                   field_report=fields(fda483=["d"]), apply=False)
        self.assertEqual(world.dispatches, [])
        self.assertEqual(payload["actions"][0]["outcome"], "planned")

    def test_source_without_path_needs_human(self):
        world = FakeWorld(set())
        payload, _ = self.run_pass(world, reconcile_report=reconcile(src("ema", "missed", ["ema-1"])))
        self.assertEqual(self.states(payload, "ema"), [cr.HUMAN])
        self.assertEqual(payload["remaining"]["ema"]["count"], 1)


class StateTest(unittest.TestCase):
    def test_stale_reconcile_is_ignored(self):
        payload, state = cr.run_pass(NOW, reconcile=reconcile(src("wl", "missed", ["wl-1"]), day="2026-11-26"),
                                     fields=None, prev_state=None, apply=False, workdir=Path("."),
                                     reconciler=lambda keys: None)
        self.assertEqual(state["sources"], {})
        self.assertEqual(payload["lines"], [])

    def test_resolved_ids_drop_first_seen(self):
        state = cr.carry_state(None, DAY)
        cr.absorb(NOW, state, cr.observe(reconcile(src("wl", "missed", ["a", "b"])), None))
        cr.absorb(NOW, state, cr.observe(reconcile(src("wl", "missed", ["b"])), None))
        self.assertEqual(state["sources"]["wl"]["first_seen"], {"b": DAY})

    def test_contract_keys_map_to_paths(self):
        seen = cr.observe(None, fields(fda_wl=["w"], mfds_gmp=["g"], ema=["e"]))
        self.assertEqual(set(seen), {"wl", "mfds_gmp"})

    def test_paths_point_to_existing_workflows(self):
        root = Path(cr.__file__).resolve().parent / ".github" / "workflows"
        for key, path in cr.PATHS.items():
            if path.kind == "dispatch":
                text = (root / path.workflow).read_text(encoding="utf-8")
                self.assertIn(f"group: {path.group}", text, key)
                for name in cr.dispatch_inputs(key, ["x"], {"missed": {"x": {"published": "2026-11-26"}}}, True):
                    self.assertRegex(text, rf"\n      {name}:\n", f"{key}: 워크플로에 입력 {name} 없음")


class MainTest(unittest.TestCase):
    def test_main_dry_run_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp, "r.json")
            rec.write_text(json.dumps(reconcile(src("ema", "missed", ["ema-1"]))), encoding="utf-8")
            outs = {n: str(Path(tmp, n)) for n in ("s.json", "p.json", "p.md")}
            with patch.object(cr, "now_kst", return_value=NOW), patch("sys.stdout"):
                code = cr.main(["--reconcile", str(rec), "--out-state", outs["s.json"],
                                "--json", outs["p.json"], "--report", outs["p.md"]])
            self.assertEqual(code, 0)
            payload = json.loads(Path(outs["p.json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["lines"][0]["state"], cr.HUMAN)
            self.assertIn("사람 필요", Path(outs["p.md"]).read_text(encoding="utf-8"))
            state = json.loads(Path(outs["s.json"]).read_text(encoding="utf-8"))
            self.assertEqual(state["schema"], cr.SCHEMA)


if __name__ == "__main__":
    unittest.main()
