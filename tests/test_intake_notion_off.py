#!/usr/bin/env python3
"""[노션 끄기 2026-11-13] GRM_INTAKE_NOTION=off — 수집기가 노션 Intake DB 를 쓰지도 읽지도 않는다. 기본 on.

지키는 것:
- 기본(on)이면 종전과 같다 — 노션 행을 만들고, 노션 handoff 를 만들고, health 에 새 키가 없다.
- off 면 **노션 API 호출이 0건**이다(행 생성·handoff·중복 조회·preflight·무음 감시·창 밖 미소비 수·483 선조회).
  NOTION_TOKEN 이 없어도 돈다.
- off 면 중복 판정·handoff 는 다른 두 변수 값과 무관하게 DB 기준이다.
- off 에서 "들어갔다" = 정본에 있다는 것이 확인됨. 정본 저장 실패는 failure 가 아니라 경고다(매일 같은
  문서가 거부돼도 수집이 서지 않는다).
- off 에서 정본·대기열 조회가 실패하면 적재 전에 멈춘다(되돌아갈 노션 기준이 없다).
- off 인데 Supabase 저장이 꺼져 있으면 실행을 거부한다(exit 2).
- off 에서도 무음 감시·창 밖 미소비 수·'발행 요일 handoff 가 발행 기록 없이 닫힘' 신호가 DB 기준으로 남는다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import collect_intake as ci  # noqa: E402
import grm_handoff as gh  # noqa: E402
import grm_notion  # noqa: E402
import grm_queue as gq  # noqa: E402
import requests  # noqa: E402
from test_grm_queue import FakeStore, _record  # noqa: E402
from test_intake_db_dedup import _AT, _FakeQueueStore, _item, _ok, _NOW, _RUN, _SUPABASE, SRC  # noqa: E402

_NOTION_FUNCS = (
    "notion_create_page", "notion_query_existing_keys", "notion_query_existing_doc_ids",
    "notion_verify_modality_property", "notion_verify_handoff_ref_property",
    "notion_count_aged_unconsumed_new", "emit_routine_handoff",
)
CANON_OK = {"ok": True, "rows": 4, "frozen": "", "aged_open": 3, "web_brief": "x/brief.json",
            "error": "", "staled_dates": ["2026-11-16", "2026-11-19"]}


class SwitchTest(unittest.TestCase):
    def _env(self, **env):
        clean = {k: v for k, v in env.items() if v is not None}
        patcher = mock.patch.dict(os.environ, clean)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("GRM_INTAKE_NOTION", "GRM_INTAKE_DEDUP_SOURCE", "GRM_HANDOFF_SOURCE"):
            if name not in clean:
                os.environ.pop(name, None)

    def test_only_the_word_off_turns_notion_off(self):
        for value, want in ((None, False), ("", False), ("on", False), ("false", False),
                            ("0", False), ("no", False), ("off", True), (" OFF ", True)):
            self._env(GRM_INTAKE_NOTION=value)
            self.assertIs(gq.notion_intake_off(), want, value)

    def test_off_implies_db_dedup_and_db_handoff(self):
        self._env()
        self.assertEqual((gq.dedup_source(), gq.handoff_source()), ("notion", "notion"))
        self._env(GRM_INTAKE_NOTION="off")
        self.assertEqual((gq.dedup_source(), gq.handoff_source()), ("supabase", "db"))
        self._env(GRM_INTAKE_NOTION="off", GRM_INTAKE_DEDUP_SOURCE="notion", GRM_HANDOFF_SOURCE="notion")
        self.assertEqual((gq.dedup_source(), gq.handoff_source()), ("supabase", "db"))

    def test_workflow_and_env_example_default_on(self):
        wf = (ROOT / ".github" / "workflows" / "grm-intake.yml").read_text(encoding="utf-8")
        self.assertIn("GRM_INTAKE_NOTION: ${{ vars.GRM_INTAKE_NOTION || 'on' }}", wf)
        self.assertIn("\nGRM_INTAKE_NOTION=on\n", (ROOT / ".env.example").read_text(encoding="utf-8"))


class InsertItemsTest(unittest.TestCase):
    def _insert(self, items, **kw):
        with mock.patch.object(ci, "log"):
            return ci.insert_items("", "", items, _RUN, _AT, set(), False,
                                   findings_supabase=_SUPABASE, **kw)

    def test_off_never_creates_a_notion_row_and_counts_by_the_store(self):
        queued, counters = [], ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep") as sleep, \
                mock.patch.object(ci, "append_intake_item_to_supabase",
                                  side_effect=[_ok("inserted"), _ok("duplicate"), _ok("invalid")]), \
                mock.patch.object(ci, "notion_create_page",
                                  side_effect=AssertionError("노션 행을 만들면 안 된다")):
            got = self._insert([_item("saved"), _item("dup"), _item("rejected")],
                               queue_new_items=queued, queue_on_store=True, store_counters=counters,
                               notion_write=False)
        self.assertEqual(got, (2, 0, 1))                 # 들어감 2 · 정본 저장 실패 1
        self.assertEqual([it.document_id for it in queued], ["saved", "dup"])
        sleep.assert_not_called()                        # 노션 속도 제한 대기도 없다
        self.assertEqual(counters["notion_failed"], 0)   # 노션 실패로 세지 않는다
        self.assertEqual(counters["supabase_failed"], 1)

    def test_off_remembers_the_key_so_a_batch_duplicate_is_skipped(self):
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok()) as supa, \
                mock.patch.object(ci, "notion_create_page", side_effect=AssertionError):
            got = self._insert([_item("same"), _item("same")], notion_write=False)
        self.assertEqual(got, (1, 1, 0))
        self.assertEqual(supa.call_count, 1)

    def test_default_still_creates_the_notion_row(self):
        with mock.patch.object(ci.time, "sleep") as sleep, \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok("invalid")), \
                mock.patch.object(ci, "notion_create_page", return_value=True) as create:
            got = self._insert([_item("a")])
        self.assertEqual(got, (1, 0, 0))                 # 종전: 노션 행이 생기면 들어간 것
        create.assert_called_once()
        sleep.assert_called_once_with(0.34)


class _NotionCalled(AssertionError):
    pass


class MainNotionOffTest(unittest.TestCase):
    DOCS = ("wl-new", "wl-have", "wl-heal", "wl-old")

    def setUp(self):
        _FakeQueueStore.made, _FakeQueueStore.fail, _FakeQueueStore.url_fail = 0, False, False

    def _run_main(self, extra_env, *, canonical=CANON_OK, supa_status="inserted", guard_notion=True,
                  drop_notion_creds=False, silence=False, create_ok=True):
        stats = ci.CollectionStats()
        groups = [[] for _ in range(22)]
        groups[7] = [_item(d) for d in self.DOCS]
        collected = (stats,) + tuple(groups)
        env = {"NOTION_TOKEN": "tok", "NOTION_DATABASE_ID": "db",
               "ENABLE_FINDINGS_SUPABASE_APPEND": "true", "RAW_SIGNALS_HEAL": "false",
               "RAW_UPDATE_APPLY": "false", "RAW_DRIFT_MEASURE": "false",
               "ENABLE_SOURCE_SILENCE_WATCHDOG": "true" if silence else "false",
               "ENABLE_WEB_BRIEF_EMIT": "true", "GRM_WEB_BRIEF_DIR": "canon-dir",
               "ENABLE_MODALITY_TAG": "true", "ENABLE_FDA_483": "true",
               "SUPABASE_URL": _SUPABASE[0], "SUPABASE_SERVICE_ROLE_KEY": _SUPABASE[1]}
        env.update(extra_env)
        urls: list[str] = []

        def http(self_, method, url, **kw):          # 모든 requests 호출이 여기로 온다
            urls.append(str(url))
            if "notion.com" in str(url):
                raise _NotionCalled(url)
            resp = mock.MagicMock()
            resp.status_code = 200
            resp.json.return_value = [{"first_seen_run_date": "2026-07-01"}]
            return resp
        notion_side = {"side_effect": _NotionCalled("노션 함수 호출")} if guard_notion else {}
        keys = grm_notion.ExistingKeys(doc_ids=set(), url_keys=set())
        defaults = {"notion_create_page": create_ok, "notion_query_existing_keys": keys,
                    "notion_query_existing_doc_ids": set(), "notion_verify_modality_property": True,
                    "notion_verify_handoff_ref_property": True, "notion_count_aged_unconsumed_new": 9,
                    "emit_routine_handoff": (7, "https://notion.so/handoff")}
        report = {} if canonical is None else {"canonical": dict(canonical)}
        with tempfile.TemporaryDirectory() as d:
            env["GRM_HEALTH_JSON"] = os.path.join(d, "grm-health.json")
            with mock.patch.dict(os.environ, env), mock.patch.object(
                    sys, "argv", ["collect_intake.py", "--emit-routine-handoff"]), \
                    mock.patch.object(ci, "now_kst", return_value=_NOW), \
                    mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(requests.sessions.Session, "request", http), \
                    mock.patch.object(ci, "probe_kr_egress_proxy", return_value=("unconfigured", "")), \
                    mock.patch.object(ci, "_configure_exclusion_ledger"), \
                    mock.patch.object(ci, "_run_collection", return_value=collected) as coll, \
                    mock.patch.object(ci, "existing_raw_signal_ids", return_value=set()), \
                    mock.patch.object(gq, "QueueStore", _FakeQueueStore), \
                    mock.patch.object(gq, "run_shadow", return_value=report) as shadow, \
                    mock.patch.object(gq, "health_summary", return_value={}), \
                    mock.patch.object(ci, "append_intake_item_to_supabase",
                                      return_value=_ok(supa_status)) as supa:
                mocks = {}
                for name in _NOTION_FUNCS:
                    kw = notion_side or {"return_value": defaults[name]}
                    patcher = mock.patch.object(ci, name, **kw)
                    mocks[name] = patcher.start()
                    self.addCleanup(patcher.stop)
                for name in ("GRM_INTAKE_NOTION", "GRM_HANDOFF_SOURCE", "GRM_INTAKE_DEDUP_SOURCE",
                             "ENABLE_INTAKE_DEDUP_SHADOW", "ENABLE_INTAKE_QUEUE_SHADOW",
                             "ENABLE_SILENCE_SUPABASE_SHADOW", "GITHUB_STEP_SUMMARY"):
                    if name not in extra_env:
                        os.environ.pop(name, None)
                if drop_notion_creds:
                    os.environ.pop("NOTION_TOKEN", None)
                    os.environ.pop("NOTION_DATABASE_ID", None)
                code = ci.main()
            payload = None
            if os.path.exists(env["GRM_HEALTH_JSON"]):
                with open(env["GRM_HEALTH_JSON"], encoding="utf-8") as f:
                    payload = json.load(f)
        return {"code": code, "payload": payload, "shadow": shadow, "supa": supa, "mocks": mocks,
                "urls": urls, "collect": coll}

    @staticmethod
    def _codes(payload, level):
        return [f["code"] for f in payload["health"][level]]

    # ── 기본(on) ──
    def test_default_is_unchanged(self):
        got = self._run_main({}, guard_notion=False)
        self.assertEqual(got["code"], 0)
        st = got["payload"]["store"]
        self.assertNotIn("notion", st)
        self.assertNotIn("handoff_source", st)
        self.assertNotIn("monitor", st)
        self.assertEqual(st["dedup_mode"], "notion")
        self.assertEqual(got["mocks"]["notion_create_page"].call_count, 4)
        got["mocks"]["emit_routine_handoff"].assert_called_once()
        got["mocks"]["notion_query_existing_keys"].assert_called_once()
        got["mocks"]["notion_count_aged_unconsumed_new"].assert_called_once()
        self.assertEqual(got["payload"]["handoff"]["aged_unconsumed_new"], 9)
        self.assertEqual(got["payload"]["handoff"]["url"], "https://notion.so/handoff")
        got["shadow"].assert_not_called()

    # ── off ──
    def test_off_makes_zero_notion_calls_and_runs_on_the_database(self):
        got = self._run_main({"GRM_INTAKE_NOTION": "off"}, silence=True)
        self.assertEqual(got["code"], 0)
        for name, m in got["mocks"].items():
            self.assertEqual(m.call_count, 0, name)
        self.assertEqual([u for u in got["urls"] if "notion" in u], [])
        st = got["payload"]["store"]
        self.assertEqual(st["notion"], "off")
        self.assertEqual(st["dedup_mode"], "supabase")
        self.assertEqual(st["dedup_shadow"]["db"], {"new": 1, "have": 1, "heal": 1, "old": 1})
        self.assertIsNone(st["dedup_shadow"]["same"])
        self.assertEqual([c[0][2].document_id for c in got["supa"].call_args_list], ["wl-new"])
        kw = got["shadow"].call_args.kwargs
        self.assertEqual(sorted(it.document_id for it in kw["new_items"]), ["wl-heal", "wl-new"])
        self.assertIsNone(kw["notion_payload"])               # 비교할 노션 handoff 가 없다
        self.assertIsNone(kw["notion_status_lookup"])
        self.assertIs(kw["canonical"], True)
        self.assertEqual(kw["canonical_web_brief_dir"], "canon-dir")
        # 수집 건수: 새 문서 1건이 들어갔다(정본 기준).
        wl = next(s for s in got["payload"]["sources"] if s.get("inserted") == 1)
        self.assertEqual(wl["failed"], 0)

    def test_off_handoff_and_watch_values_come_from_the_database(self):
        got = self._run_main({"GRM_INTAKE_NOTION": "off"})
        h = got["payload"]["handoff"]
        self.assertEqual((h["emitted"], h["failed"], h["row_count"], h["url"]), (True, False, 4, ""))
        self.assertEqual(h["aged_unconsumed_new"], 3)                       # DB 의 창 밖 미소비 수
        self.assertIn("aged-unconsumed-new", self._codes(got["payload"], "warnings"))
        hs = got["payload"]["store"]["handoff_source"]
        self.assertEqual((hs["source"], hs["notion_mirror"]), ("db", "off"))
        # 11-16 은 월요일 — 발행 기록 없이 닫힌 발행 요일 handoff 만 경고가 된다(11-19 는 목요일).
        warn = self._codes(got["payload"], "warnings")
        self.assertIn("handoff-not-consumed:2026-11-16", warn)
        self.assertNotIn("handoff-not-consumed:2026-11-19", warn)

    def test_off_ignores_the_other_two_switches(self):
        got = self._run_main({"GRM_INTAKE_NOTION": "off", "GRM_INTAKE_DEDUP_SOURCE": "notion",
                              "GRM_HANDOFF_SOURCE": "notion"})
        self.assertEqual(got["code"], 0)
        self.assertEqual(got["payload"]["store"]["dedup_mode"], "supabase")
        self.assertEqual(got["payload"]["store"]["handoff_source"]["source"], "db")

    def test_off_runs_without_notion_credentials(self):
        got = self._run_main({"GRM_INTAKE_NOTION": "off"}, drop_notion_creds=True)
        self.assertEqual(got["code"], 0)
        self.assertEqual(got["payload"]["store"]["notion"], "off")

    def test_default_still_requires_notion_credentials(self):
        got = self._run_main({}, drop_notion_creds=True, guard_notion=False)
        self.assertEqual(got["code"], 2)
        got["collect"].assert_not_called()

    def test_off_refuses_to_run_without_the_store(self):
        for env in ({"ENABLE_FINDINGS_SUPABASE_APPEND": "false"}, {"SUPABASE_SERVICE_ROLE_KEY": ""},
                    {"SUPABASE_URL": ""}):
            with self.subTest(env=env):
                got = self._run_main(dict({"GRM_INTAKE_NOTION": "off"}, **env))
                self.assertEqual(got["code"], 2)
                got["collect"].assert_not_called()             # 수집 전에 멈춘다
                self.assertEqual(got["supa"].call_count, 0)

    def test_off_stops_before_loading_when_the_database_lookup_fails(self):
        for attr in ("fail", "url_fail"):
            with self.subTest(attr=attr):
                self.setUp()
                setattr(_FakeQueueStore, attr, True)
                got = self._run_main({"GRM_INTAKE_NOTION": "off"})
                self.assertEqual(got["code"], 1)
                self.assertEqual(got["supa"].call_count, 0)        # 아무것도 적재하지 않았다
                got["shadow"].assert_not_called()
                self.assertEqual(self._codes(got["payload"], "failures"), ["intake-dedupe-db-failed"])
                for m in got["mocks"].values():
                    self.assertEqual(m.call_count, 0)              # 노션으로 되돌아가지도 않는다

    def test_off_store_rejection_is_a_warning_not_a_failure(self):
        got = self._run_main({"GRM_INTAKE_NOTION": "off"}, supa_status="invalid")
        self.assertEqual(got["code"], 0)
        self.assertNotIn("notion-insert-failed", self._codes(got["payload"], "failures"))
        self.assertIn("supabase-append-failed", self._codes(got["payload"], "warnings"))
        self.assertEqual(got["payload"]["store"]["notion_failed"], 0)
        failed = [s["failed"] for s in got["payload"]["sources"] if s.get("failed")]
        self.assertEqual(failed, [1])                              # 소스별 실패 수에는 남는다
        self.assertEqual(list(got["shadow"].call_args.kwargs["new_items"])[0].document_id, "wl-heal")

    def test_default_notion_failure_is_still_a_failure(self):
        got = self._run_main({}, guard_notion=False, create_ok=False)
        self.assertEqual(got["code"], 1)
        self.assertIn("notion-insert-failed", self._codes(got["payload"], "failures"))
        self.assertEqual(got["payload"]["store"]["notion_failed"], 4)

    def test_off_silence_watch_reads_the_queue(self):
        with mock.patch("source_silence.query_last_seen",
                        side_effect=_NotionCalled("무음 감시가 노션을 읽었다")), \
                mock.patch("source_silence.query_last_seen_supabase",
                           return_value=({SRC: date(2026, 11, 19)}, ["PIC/S: http_500"],
                                         date(2026, 7, 16))) as db:
            got = self._run_main({"GRM_INTAKE_NOTION": "off"}, silence=True)
        self.assertEqual(got["code"], 0)
        db.assert_called_once()
        self.assertEqual(db.call_args[0][:2], _SUPABASE)
        self.assertEqual(got["payload"]["store"]["monitor"]["silence"],
                         {"basis": "intake_queue", "history_start": "2026-07-16", "judged": 1, "errors": 1})

    def test_default_silence_watch_still_reads_notion(self):
        with mock.patch("source_silence.query_last_seen", return_value=({}, [])) as notion, \
                mock.patch("source_silence.query_last_seen_supabase",
                           side_effect=AssertionError("그림자도 꺼져 있다")):
            got = self._run_main({}, guard_notion=False, silence=True)
        self.assertEqual(got["code"], 0)
        notion.assert_called_once()
        self.assertNotIn("monitor", got["payload"]["store"])


class StaledDatesTest(unittest.TestCase):
    """정본 handoff 가 오늘 기록으로 닫는(stale) 지난 open handoff 의 날짜를 돌려준다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "web", "data", "deltas"))
        os.makedirs(os.path.join(self.root, "web", "data", "briefs"))
        self.store = FakeStore()
        self.store.add_raw(_record("Federal Register", "2026-1"))
        self.store.enqueue([{"source": "Federal Register", "document_id": "2026-1",
                             "first_seen_run_date": "2026-11-13",
                             "first_seen_at": "2026-11-13T03:30:00+09:00"}])

    def _shadow(self, run, **kw):
        gen = datetime(run.year, run.month, run.day, 3, 40, tzinfo=gq.KST)
        with mock.patch.object(gq, "log"), mock.patch.object(gh, "log"), \
                mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            os.environ.pop("GRM_QUEUE_HANDOFF_REFRESH", None)
            return gq.run_shadow(("u", "k"), run_date=run, generated_at=gen, window_days=30,
                                 display_window_days=7, store=self.store, out_dir=self.root,
                                 repo_root=self.root, **kw)

    def test_open_monday_handoff_closed_by_tuesday_is_reported(self):
        self._shadow(date(2026, 11, 16), canonical=True)                 # 월요일 — 발행 기록 없음
        report = self._shadow(date(2026, 11, 17), canonical=True)
        self.assertEqual(report["canonical"]["staled_dates"], ["2026-11-16"])
        self.assertEqual(gh.unconsumed_publish_handoffs(report["canonical"]["staled_dates"]),
                         ["2026-11-16"])

    def test_consumed_monday_is_not_reported(self):
        self._shadow(date(2026, 11, 16), canonical=True)
        with open(os.path.join(self.root, "web", "data", "deltas", "delta_2026_11_16.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"publish_date": "2026-11-16", "cards": {"2026-1": {}}}, f)
        report = self._shadow(date(2026, 11, 17), canonical=True)
        self.assertEqual(report["canonical"]["staled_dates"], [])

    def test_frozen_rerun_reports_nothing(self):
        self._shadow(date(2026, 11, 15), canonical=True)
        self._shadow(date(2026, 11, 16), canonical=True)
        # 동결된 재실행은 아무것도 닫지 않는다 — 열린 기록이 남아 있어도 '닫았다'고 보고하지 않는다.
        self.store.upsert_handoff({"handoff_date": "2026-11-09", "status": gq.HANDOFF_OPEN,
                                   "manifest": [], "consumed_evidence": ""})
        report = self._shadow(date(2026, 11, 16), canonical=True)        # 월요일 재실행 = 동결
        self.assertEqual(self.store.handoffs["2026-11-09"]["status"], gq.HANDOFF_OPEN)
        self.assertEqual(report["canonical"]["frozen"], gq.FROZEN_PUBLISH_DAY)
        self.assertEqual(report["canonical"]["staled_dates"], [])


if __name__ == "__main__":
    unittest.main()
