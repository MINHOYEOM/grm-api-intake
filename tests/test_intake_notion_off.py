#!/usr/bin/env python3
"""Regression tests for the collector database path."""

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
import grm_queue as gq  # noqa: E402
import requests  # noqa: E402
from test_grm_queue import FakeStore, _record  # noqa: E402
from test_intake_db_dedup import _AT, _FakeQueueStore, _item, _ok, _NOW, _RUN, _SUPABASE, SRC  # noqa: E402
CANON_OK = {"ok": True, "rows": 4, "frozen": "", "aged_open": 3, "web_brief": "x/brief.json",
            "error": "", "staled_dates": ["2026-11-16", "2026-11-19"]}


class _NotionCalled(AssertionError):
    pass


class MainNotionOffTest(unittest.TestCase):
    DOCS = ("wl-new", "wl-have", "wl-heal", "wl-old")

    def setUp(self):
        _FakeQueueStore.made, _FakeQueueStore.fail, _FakeQueueStore.url_fail = 0, False, False

    def _run_main(self, extra_env, *, canonical=CANON_OK, supa_status="inserted", silence=False):
        stats = ci.CollectionStats()
        groups = [[] for _ in range(22)]
        groups[7] = [_item(d) for d in self.DOCS]
        collected = (stats,) + tuple(groups)
        env = {"ENABLE_FINDINGS_SUPABASE_APPEND": "true", "RAW_SIGNALS_HEAL": "false",
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
        report = {} if canonical is None else {"canonical": dict(canonical)}
        with tempfile.TemporaryDirectory() as d:
            env["GRM_HEALTH_JSON"] = os.path.join(d, "grm-health.json")
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
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
                code = ci.main()
            payload = None
            if os.path.exists(env["GRM_HEALTH_JSON"]):
                with open(env["GRM_HEALTH_JSON"], encoding="utf-8") as f:
                    payload = json.load(f)
        return {"code": code, "payload": payload, "shadow": shadow, "supa": supa,
                "urls": urls, "collect": coll}

    @staticmethod
    def _codes(payload, level):
        return [f["code"] for f in payload["health"][level]]

    # ── 기본(on) ──

    # ── off ──
    def test_off_makes_zero_notion_calls_and_runs_on_the_database(self):
        got = self._run_main({}, silence=True)
        self.assertEqual(got["code"], 0)
        self.assertEqual([u for u in got["urls"] if "notion" in u], [])
        st = got["payload"]["store"]
        self.assertEqual(st["notion"], "off")
        self.assertEqual(st["dedup_mode"], "supabase")
        self.assertEqual(st["dedup_shadow"]["db"], {"new": 1, "have": 1, "heal": 1, "old": 1})
        self.assertIsNone(st["dedup_shadow"]["same"])
        self.assertEqual([c[0][2].document_id for c in got["supa"].call_args_list], ["wl-new"])
        kw = got["shadow"].call_args.kwargs
        self.assertEqual(sorted(it.document_id for it in kw["new_items"]), ["wl-heal", "wl-new"])
        self.assertIs(kw["canonical"], True)
        self.assertEqual(kw["canonical_web_brief_dir"], "canon-dir")
        # 수집 건수: 새 문서 1건이 들어갔다(정본 기준).
        wl = next(s for s in got["payload"]["sources"] if s.get("inserted") == 1)
        self.assertEqual(wl["failed"], 0)

    def test_off_handoff_and_watch_values_come_from_the_database(self):
        got = self._run_main({})
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


    def test_off_refuses_to_run_without_the_store(self):
        for env in ({"ENABLE_FINDINGS_SUPABASE_APPEND": "false"}, {"SUPABASE_SERVICE_ROLE_KEY": ""},
                    {"SUPABASE_URL": ""}):
            with self.subTest(env=env):
                got = self._run_main(dict({}, **env))
                self.assertEqual(got["code"], 2)
                got["collect"].assert_not_called()             # 수집 전에 멈춘다
                self.assertEqual(got["supa"].call_count, 0)

    def test_off_stops_before_loading_when_the_database_lookup_fails(self):
        for attr in ("fail", "url_fail"):
            with self.subTest(attr=attr):
                self.setUp()
                setattr(_FakeQueueStore, attr, True)
                got = self._run_main({})
                self.assertEqual(got["code"], 1)
                self.assertEqual(got["supa"].call_count, 0)        # 아무것도 적재하지 않았다
                got["shadow"].assert_not_called()
                self.assertEqual(self._codes(got["payload"], "failures"), ["intake-dedupe-db-failed"])

    def test_off_store_rejection_is_a_warning_not_a_failure(self):
        got = self._run_main({}, supa_status="invalid")
        self.assertEqual(got["code"], 0)
        self.assertNotIn("notion-insert-failed", self._codes(got["payload"], "failures"))
        self.assertIn("supabase-append-failed", self._codes(got["payload"], "warnings"))
        self.assertEqual(got["payload"]["store"]["notion_failed"], 0)
        failed = [s["failed"] for s in got["payload"]["sources"] if s.get("failed")]
        self.assertEqual(failed, [1])                              # 소스별 실패 수에는 남는다
        self.assertEqual(list(got["shadow"].call_args.kwargs["new_items"])[0].document_id, "wl-heal")


    def test_off_silence_watch_reads_the_queue(self):
        with mock.patch("source_silence.query_last_seen_supabase",
                           return_value=({SRC: date(2026, 11, 19)}, ["PIC/S: http_500"],
                                         date(2026, 7, 16))) as db:
            got = self._run_main({}, silence=True)
        self.assertEqual(got["code"], 0)
        db.assert_called_once()
        self.assertEqual(db.call_args[0][:2], _SUPABASE)
        self.assertEqual(got["payload"]["store"]["monitor"]["silence"],
                         {"basis": "intake_queue", "history_start": "2026-07-16", "judged": 1, "errors": 1})


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

    def _shadow(self, run, hour=3, **kw):
        gen = datetime(run.year, run.month, run.day, hour, 40, tzinfo=gq.KST)
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
        report = self._shadow(date(2026, 11, 16), hour=8, canonical=True)   # 월요일 07:00 뒤 재실행 = 동결
        self.assertEqual(self.store.handoffs["2026-11-09"]["status"], gq.HANDOFF_OPEN)
        self.assertEqual(report["canonical"]["frozen"], gq.FROZEN_PUBLISH_DAY)
        self.assertEqual(report["canonical"]["staled_dates"], [])


if __name__ == "__main__":
    unittest.main()
