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
        return ci.insert_items(items, _AT, set(), False,
                               findings_supabase=_SUPABASE, **kw)

    def test_heal_documents_go_to_the_queue_only(self):
        queued, counters = [], ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase") as supa:
            got = self._insert([_item("heal")], queue_new_items=queued, queue_on_store=True,
                               queue_only_keys={f"{SRC}::heal"}, store_counters=counters)
        supa.assert_not_called()
        self.assertEqual(got, (0, 1, 0))
        self.assertEqual([it.document_id for it in queued], ["heal"])
        self.assertEqual(counters["queue_healed"], 1)

    def test_queue_follows_the_store(self):
        queued = []
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase",
                                  side_effect=[_ok("inserted"), _ok("duplicate"), _ok("invalid")]):
            got = self._insert([_item("saved"), _item("dup"), _item("store-fails")],
                               queue_new_items=queued, queue_on_store=True)
        # 정본에 있다고 확인된 두 건만 — 노션이 실패한 첫 건도 들어가고, 정본이 거부한 셋째는 안 들어간다.
        self.assertEqual([it.document_id for it in queued], ["saved", "dup"])
        self.assertEqual(got, (2, 0, 1))


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


class Fda483PrefetchDbTest(unittest.TestCase):
    """FDA 483 선조회(PDF·OCR 건너뛰기)를 DB 대기열 기준으로 — 중복 판정과 같은 변수를 따른다."""

    START = datetime(2026, 10, 21, tzinfo=timezone.utc).date()

    def _known(self, **kw):
        return ci._fda483_known_document_ids(_RUN, self.START, _RUN, enabled=True, **kw)


    def test_db_basis_reads_queued_483_within_the_window(self):
        seen = {}

        class Store:
            def __init__(self, url, key):
                seen["cred"] = (url, key)

            def queue_keys_for_source(self, source, since):
                seen["args"] = (source, since)
                return {"FDA 483::q1", "FDA 483::q2"}
        with mock.patch.object(gq, "QueueStore", Store), \
                mock.patch.object(ci, "log"):
            got = self._known(db=_SUPABASE)
        self.assertEqual(got, {"FDA 483::q1", "FDA 483::q2"})
        self.assertEqual(seen["cred"], _SUPABASE)
        self.assertEqual(seen["args"], ("FDA 483", "2026-10-20"))      # 창 = 집행 시작~오늘(31일)


    def test_store_query_is_scoped_to_source_and_window(self):
        calls = []

        class Http:
            def request(self, method, url, params=None, json=None, headers=None, timeout=None):
                calls.append(dict(params or {}))
                r = mock.MagicMock()
                r.status_code = 200
                r.json.return_value = [{"document_id": "fda483-1"}, {"document_id": ""}]
                return r
        st = gq.QueueStore(_SUPABASE[0], _SUPABASE[1], session=Http())
        self.assertEqual(st.queue_keys_for_source("FDA 483", "2026-10-20"), {"FDA 483::fda483-1"})
        self.assertEqual(calls[0]["source"], "eq.FDA 483")
        self.assertEqual(calls[0]["first_seen_run_date"], "gte.2026-10-20")


class HandoffStatusDbTest(unittest.TestCase):
    """coverage gate 의 handoff 상태 조회를 DB 대기열 기록으로 — 반환 낱말은 노션과 같다."""

    def test_status_words_match_the_notion_vocabulary(self):
        import fda_body_coverage as cov

        class Store:
            def __init__(self, row):
                self.row = row

            def get_handoff(self, day):
                return self.row
        for db_status, word in (("open", "New"), ("consumed", "Processed"), ("stale", "Skipped"),
                                ("weird", "missing")):
            self.assertEqual(cov.handoff_status_db("2026-11-16", base_url="u", service_key="k",
                                                   store=Store({"status": db_status})), word)
        self.assertEqual(cov.handoff_status_db("2026-11-16", base_url="u", service_key="k",
                                               store=Store(None)), "missing")


if __name__ == "__main__":
    unittest.main()
