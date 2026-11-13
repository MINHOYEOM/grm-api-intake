#!/usr/bin/env python3
"""[대기열 5단계 2026-11-13] 중복 판정·대기열 넣기 기준을 DB 로 — 기본 꺼짐.

지키는 것:
- 변수가 둘 다 꺼져 있으면 DB 를 읽지 않고 종전과 같다.
- DB 기준(GRM_INTAKE_DEDUP_SOURCE=supabase): 정본에 없으면 새 문서, 정본·대기열 둘 다 있으면 이미 받음,
  정본에 있고 대기열에 없으면 저장 시각으로 보충(최근)·이미 받음(오래전)을 가른다.
- 보충 문서는 대기열에만 넣는다(정본·노션에 다시 쓰지 않는다).
- 넣기 기준은 정본 저장 확인이다 — 노션 행 생성이 실패해도 대기열에는 들어간다.
- DB 조회가 실패한 날은 노션 기준으로 판정한다. 노션 조회가 죽어도 DB 기준이면 수집이 계속된다.
- 그림자(ENABLE_INTAKE_DEDUP_SHADOW)는 판정을 바꾸지 않고 health 에 차이와 소스별 보충 표본을 남긴다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import collect_intake as ci
import findings_store as store
import grm_notion
import grm_queue as gq

_SUPABASE = ("https://example.supabase.co", "service-role-secret-token")
_RUN = datetime(2026, 11, 20, tzinfo=timezone.utc).date()
_AT = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
_NOW = datetime(2026, 11, 20, 3, 17, tzinfo=ci.KST)
SRC = "FDA Warning Letter"


def _item(doc: str, **over) -> ci.IntakeItem:
    base = dict(source=SRC, document_id=doc, date_iso="2026-11-18", headline=f"[WL] {doc}",
                official_url=f"https://www.fda.gov/wl/{doc}", type_or_class="Warning Letter",
                firm="Example Pharma", body="CGMP violations.", qa_relevance="Likely",
                osd_relevance="Direct", signal_tier="Tier 3", raw_payload={"firm": "Example Pharma"})
    base.update(over)
    return ci.IntakeItem(**base)


def _ok(status="inserted"):
    return store.RawSignalAppendResult(status, raw_signal_id="rawsig-x")


class ClassifyTest(unittest.TestCase):
    def test_four_rows_of_the_table(self):
        recent = (_NOW - timedelta(days=3)).isoformat()
        ancient = (_NOW - timedelta(days=200)).isoformat()
        got = gq.classify_dedup(
            ["S::new", "S::have", "S::heal", "S::old"],
            {"S::have": ancient, "S::heal": recent, "S::old": ancient}, {"S::have"}, _NOW)
        self.assertEqual(got, {"S::new": "new", "S::have": "have", "S::heal": "heal", "S::old": "old"})

    def test_heal_boundary_is_35_days_and_unknown_time_is_not_healed(self):
        self.assertEqual(gq.DEDUP_HEAL_DAYS, 35)
        inside = (_NOW - timedelta(days=35)).isoformat()
        outside = (_NOW - timedelta(days=35, minutes=1)).isoformat()
        got = gq.classify_dedup(["a", "b", "c"], {"a": inside, "b": outside, "c": ""}, set(), _NOW)
        self.assertEqual(got, {"a": "heal", "b": "old", "c": "old"})   # 저장 시각을 모르면 넣지 않는다

    def test_snapshot_source_old_documents_do_not_flood_back(self):
        # WHO·ICH 는 매일 목록 전체를 다시 준다. 대기열 기록 이전에 받은 문서는 정본에 오래전부터 있다.
        keys = [f"WHO::doc{i}" for i in range(170)]
        stored = {k: "2026-07-01T00:00:00+00:00" for k in keys}
        got = gq.classify_dedup(keys, stored, set(), _NOW)
        self.assertEqual(set(got.values()), {"old"})

    def test_summary_lists_heal_per_source_with_samples(self):
        classes = {"FR::n": "new", "FR::h": "have", "WHO::x1": "heal", "WHO::x2": "heal",
                   "ICH::o": "old", "FR::q": "have"}
        out = gq.dedup_shadow_summary(classes, notion_existing={"FR::h", "ICH::o", "WHO::x1"}, sample=1)
        self.assertEqual(out["db"], {"new": 1, "have": 2, "heal": 2, "old": 1})
        self.assertEqual(out["heal_by_source"], {"WHO": {"count": 2, "sample": ["x1"]}})
        self.assertEqual((out["notion_new"], out["db_new_or_heal"]), (3, 3))
        self.assertEqual(out["only_notion_new_sample"], ["FR::q"])     # 노션은 새 문서, DB 는 이미 받음
        self.assertEqual(out["only_db_new_sample"], ["WHO::x1"])       # 노션은 있음, DB 는 보충
        self.assertFalse(out["same"])
        same = gq.dedup_shadow_summary({"FR::n": "new", "FR::h": "have"}, {"FR::h"})
        self.assertTrue(same["same"])

    def test_lookup_returns_none_when_either_store_read_fails(self):
        class S:
            def __init__(self, raw, queue_error=False):
                self._raw, self._qe = raw, queue_error

            def raw_ingested_at(self, pairs):
                return self._raw

            def queue_rows(self, pairs):
                if self._qe:
                    raise gq.QueueStoreError("intake_queue GET: http_500")
                return {}
        self.assertIsNone(gq.lookup_dedup(S(None), [("S", "a")], _NOW))
        self.assertIsNone(gq.lookup_dedup(S({}, queue_error=True), [("S", "a")], _NOW))
        self.assertEqual(gq.lookup_dedup(S({}), [("S", "a"), ("S", "a"), ("", "x")], _NOW), {"S::a": "new"})
        self.assertEqual(gq.lookup_dedup(S({}), [], _NOW), {})

    def test_store_reads_ingested_time_and_url_keys(self):
        calls = []

        class Http:
            def request(self, method, url, params=None, json=None, headers=None, timeout=None):
                calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
                r = mock.MagicMock()
                r.status_code = 200
                if "raw_signal_id" in (params or {}):
                    rid = gq.raw_signal_id_for("S", "a")
                    r.json.return_value = [{"raw_signal_id": rid, "ingested_at": "2026-11-19T00:00:00+00:00"}]
                else:
                    r.json.return_value = [{"official_url": "https://EX.org/a/#frag"}, {"official_url": ""}]
                return r
        st = gq.QueueStore(_SUPABASE[0], _SUPABASE[1], session=Http())
        self.assertEqual(st.raw_ingested_at([("S", "a"), ("S", "b")]),
                         {"S::a": "2026-11-19T00:00:00+00:00"})
        self.assertEqual(st.source_url_keys(["ECA Academy"]),
                         {grm_notion.canonical_url_key("ECA Academy", "https://EX.org/a/#frag")})

        class Broken:
            def request(self, *a, **k):
                r = mock.MagicMock()
                r.status_code = 500
                return r
        bad = gq.QueueStore(_SUPABASE[0], _SUPABASE[1], session=Broken())
        self.assertIsNone(bad.raw_ingested_at([("S", "a")]))
        self.assertIsNone(bad.source_url_keys(["ECA Academy"]))


class InsertItemsQueueCriterionTest(unittest.TestCase):
    def _insert(self, items, **kw):
        return ci.insert_items("tok", "db", items, _RUN, _AT, set(), False, modality_enabled=False,
                               findings_supabase=_SUPABASE, **kw)

    def test_heal_documents_go_to_the_queue_only(self):
        queued, counters = [], ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase") as supa, \
                mock.patch.object(ci, "notion_create_page") as notion:
            got = self._insert([_item("heal")], queue_new_items=queued, queue_on_store=True,
                               queue_only_keys={f"{SRC}::heal"}, store_counters=counters)
        supa.assert_not_called()
        notion.assert_not_called()
        self.assertEqual(got, (0, 1, 0))
        self.assertEqual([it.document_id for it in queued], ["heal"])
        self.assertEqual(counters["queue_healed"], 1)

    def test_queue_follows_the_store_not_notion(self):
        queued = []
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase",
                                  side_effect=[_ok("inserted"), _ok("duplicate"), _ok("invalid")]), \
                mock.patch.object(ci, "notion_create_page", side_effect=[False, True, True]):
            got = self._insert([_item("saved-notion-fails"), _item("dup"), _item("store-fails")],
                               queue_new_items=queued, queue_on_store=True)
        # 정본에 있다고 확인된 두 건만 — 노션이 실패한 첫 건도 들어가고, 정본이 거부한 셋째는 안 들어간다.
        self.assertEqual([it.document_id for it in queued], ["saved-notion-fails", "dup"])
        self.assertEqual(got, (2, 0, 1))

    def test_notion_criterion_is_unchanged_when_the_switch_is_off(self):
        queued = []
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok()), \
                mock.patch.object(ci, "notion_create_page", side_effect=[False, True]):
            self._insert([_item("notion-fails"), _item("ok")], queue_new_items=queued)
        self.assertEqual([it.document_id for it in queued], ["ok"])     # 종전: 노션 행이 생긴 문서만


class _FakeQueueStore:
    made = 0
    fail = False
    url_fail = False
    recent = (_NOW - timedelta(days=2)).isoformat()
    ancient = (_NOW - timedelta(days=300)).isoformat()

    def __init__(self, url, key, session=None):
        type(self).made += 1

    def raw_ingested_at(self, pairs):
        if type(self).fail:
            return None
        return {f"{SRC}::wl-have": self.ancient, f"{SRC}::wl-heal": self.recent,
                f"{SRC}::wl-old": self.ancient}

    def queue_rows(self, pairs):
        return {f"{SRC}::wl-have": {}}

    def source_url_keys(self, sources):
        return None if type(self).url_fail else set()


class MainDbDedupTest(unittest.TestCase):
    DOCS = ("wl-new", "wl-have", "wl-heal", "wl-old")

    def setUp(self):
        _FakeQueueStore.made, _FakeQueueStore.fail, _FakeQueueStore.url_fail = 0, False, False

    def _run_main(self, extra_env, *, notion_keys=(), notion_error=False):
        stats = ci.CollectionStats()
        groups = [[] for _ in range(22)]
        groups[7] = [_item(d) for d in self.DOCS]                      # wl_items
        collected = (stats,) + tuple(groups)
        env = {"NOTION_TOKEN": "tok", "NOTION_DATABASE_ID": "db",
               "ENABLE_FINDINGS_SUPABASE_APPEND": "true", "RAW_SIGNALS_HEAL": "false",
               "RAW_UPDATE_APPLY": "false", "ENABLE_SOURCE_SILENCE_WATCHDOG": "false",
               "SUPABASE_URL": _SUPABASE[0], "SUPABASE_SERVICE_ROLE_KEY": _SUPABASE[1]}
        env.update(extra_env)
        keys = grm_notion.ExistingKeys(doc_ids={f"{SRC}::{d}" for d in notion_keys}, url_keys=set())
        nq = (mock.patch.object(ci, "notion_query_existing_keys",
                                side_effect=grm_notion.NotionDedupeQueryError("timeout"))
              if notion_error else mock.patch.object(ci, "notion_query_existing_keys", return_value=keys))
        with tempfile.TemporaryDirectory() as d:
            env["GRM_HEALTH_JSON"] = os.path.join(d, "grm-health.json")
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(sys, "argv", ["collect_intake.py"]), \
                    mock.patch.object(ci, "now_kst", return_value=_NOW), \
                    mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(ci, "notion_verify_modality_property", return_value=True), \
                    mock.patch.object(ci, "notion_verify_handoff_ref_property", return_value=True), \
                    mock.patch.object(ci, "probe_kr_egress_proxy", return_value=("unconfigured", "")), \
                    mock.patch.object(ci, "_fda483_known_document_ids", return_value=None), \
                    mock.patch.object(ci, "_configure_exclusion_ledger"), \
                    mock.patch.object(ci, "_run_collection", return_value=collected), nq, \
                    mock.patch.object(ci, "notion_count_aged_unconsumed_new", return_value=0), \
                    mock.patch.object(ci, "existing_raw_signal_ids", return_value=set()), \
                    mock.patch.object(gq, "QueueStore", _FakeQueueStore), \
                    mock.patch.object(gq, "run_shadow", return_value={}) as shadow, \
                    mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok()) as supa, \
                    mock.patch.object(ci, "notion_create_page", return_value=True) as create:
                for name in ("GRM_INTAKE_DEDUP_SOURCE", "ENABLE_INTAKE_DEDUP_SHADOW",
                             "ENABLE_INTAKE_QUEUE_SHADOW"):
                    if name not in extra_env:
                        os.environ.pop(name, None)
                code = ci.main()
            with open(env["GRM_HEALTH_JSON"], encoding="utf-8") as f:
                payload = json.load(f)
        created = [c[0][2].document_id for c in create.call_args_list]
        stored = [c[0][2].document_id for c in supa.call_args_list]
        queued = ([it.document_id for it in shadow.call_args.kwargs["new_items"]]
                  if shadow.call_args else None)
        return code, created, stored, queued, payload["store"]

    def test_default_never_reads_the_database_for_dedup(self):
        code, created, stored, queued, st = self._run_main({}, notion_keys=("wl-have",))
        self.assertEqual(code, 0)
        self.assertEqual(_FakeQueueStore.made, 0)
        self.assertEqual(created, ["wl-new", "wl-heal", "wl-old"])       # 노션 기준 그대로
        self.assertNotIn("dedup_shadow", st)
        self.assertEqual(st["dedup_mode"], "notion")
        self.assertIsNone(queued)

    def test_db_basis_decides_new_have_heal_old(self):
        # 노션 조회는 일부러 다른 답을 준다 — DB 기준이 이기는지 본다.
        code, created, stored, queued, st = self._run_main(
            {"GRM_INTAKE_DEDUP_SOURCE": "supabase"}, notion_keys=("wl-new",))
        self.assertEqual(code, 0)
        self.assertEqual(created, ["wl-new"])                 # 새 문서만 정본·노션에 쓴다
        self.assertEqual(stored, ["wl-new"])
        self.assertEqual(sorted(queued), ["wl-heal", "wl-new"])   # 보충은 대기열에만
        self.assertEqual(st["dedup_mode"], "supabase")
        self.assertEqual(st["queue_healed"], 1)
        self.assertEqual(st["dedup_shadow"]["db"], {"new": 1, "have": 1, "heal": 1, "old": 1})
        self.assertEqual(st["dedup_shadow"]["heal_by_source"], {SRC: {"count": 1, "sample": ["wl-heal"]}})

    def test_db_basis_keeps_collecting_when_notion_lookup_is_down(self):
        code, created, _stored, queued, st = self._run_main(
            {"GRM_INTAKE_DEDUP_SOURCE": "supabase"}, notion_error=True)
        self.assertEqual(code, 0)
        self.assertEqual(created, ["wl-new"])
        self.assertEqual(sorted(queued), ["wl-heal", "wl-new"])
        self.assertEqual(st["dedup_mode"], "supabase")
        self.assertIsNone(st["dedup_shadow"]["same"])

    def test_db_lookup_failure_falls_back_to_the_notion_basis(self):
        for attr in ("fail", "url_fail"):
            with self.subTest(attr=attr):
                self.setUp()
                setattr(_FakeQueueStore, attr, True)
                code, created, _stored, queued, st = self._run_main(
                    {"GRM_INTAKE_DEDUP_SOURCE": "supabase"}, notion_keys=("wl-have", "wl-old"))
                self.assertEqual(code, 0)
                self.assertEqual(created, ["wl-new", "wl-heal"])       # 노션 기준
                self.assertEqual(st["dedup_mode"], "notion")
                self.assertIsNone(queued)                              # 그림자도 꺼져 있다
                self.assertNotIn("queue_healed", st)

    def test_shadow_only_records_and_changes_nothing(self):
        code, created, stored, queued, st = self._run_main(
            {"ENABLE_INTAKE_DEDUP_SHADOW": "true"}, notion_keys=("wl-have", "wl-heal"))
        self.assertEqual(code, 0)
        self.assertEqual(created, ["wl-new", "wl-old"])       # 판정은 노션 기준
        self.assertEqual(stored, ["wl-new", "wl-old"])
        self.assertEqual(st["dedup_mode"], "notion")
        self.assertIsNone(queued)
        sh = st["dedup_shadow"]
        self.assertEqual(sh["db"], {"new": 1, "have": 1, "heal": 1, "old": 1})
        self.assertEqual(sh["only_notion_new_sample"], [f"{SRC}::wl-old"])
        self.assertEqual(sh["only_db_new_sample"], [f"{SRC}::wl-heal"])
        self.assertFalse(sh["same"])

    def test_workflow_passes_both_switches_default_off(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".github", "workflows", "grm-intake.yml"), encoding="utf-8") as f:
            wf = f.read()
        self.assertIn("GRM_INTAKE_DEDUP_SOURCE: ${{ vars.GRM_INTAKE_DEDUP_SOURCE || 'notion' }}", wf)
        self.assertIn("ENABLE_INTAKE_DEDUP_SHADOW: ${{ vars.ENABLE_INTAKE_DEDUP_SHADOW || 'false' }}", wf)
        self.assertEqual(gq.dedup_source(), "notion")


if __name__ == "__main__":
    unittest.main()
