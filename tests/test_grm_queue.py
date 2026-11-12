"""주간 대기열(DB) — 상태 규칙·handoff 재료·그림자 비교 (grm_queue.py, migration 149)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import grm_handoff as gh  # noqa: E402
import grm_queue as gq  # noqa: E402

RUN = date(2026, 11, 16)
GEN = datetime(2026, 11, 16, 3, 40, tzinfo=gq.KST)


def _record(source: str, doc: str, *, raw: dict | None = None, modality: str = "",
            collected_at: str = "2026-11-12T03:30:00+09:00", **row) -> dict:
    base = {"source": source, "document_id": doc, "date": "2026-11-10",
            "headline": f"headline {doc}", "official_url": f"https://example.org/{doc}",
            "type_or_class": "Rule", "signal_tier": "Tier 2", "body": "body text"}
    base.update(row)
    return {"raw_signal_id": gq.raw_signal_id_for(source, doc), "source": source,
            "document_id": doc, "collected_at": collected_at, "modality": modality,
            "row_json": json.dumps(base, ensure_ascii=False),
            "raw_json": json.dumps(raw if raw is not None else {"k": doc})}


class FakeStore:
    """표 세 개를 메모리로 흉내 낸다(QueueStore 와 같은 메서드)."""

    def __init__(self) -> None:
        self.queue: dict[str, dict] = {}
        self.raw: dict[str, dict] = {}
        self.handoffs: dict[str, dict] = {}
        self.fail_batch = False
        self.writes = 0

    def add_raw(self, rec: dict) -> None:
        self.raw[gq.row_key(rec["source"], rec["document_id"])] = rec

    def enqueue(self, rows):
        if self.fail_batch and len(rows) > 1:
            raise gq.QueueStoreError("intake_queue POST: http_409")
        n = 0
        for r in rows:
            key = gq.row_key(r["source"], r["document_id"])
            if key not in self.raw:
                raise gq.QueueStoreError("intake_queue POST: http_409")   # FK
            if key not in self.queue:
                self.queue[key] = dict(r, state="new", state_reason="", handoff_date=None,
                                       brief_publish_date=None)
                n += 1
        return n

    def open_candidates(self, start, end):
        return [q for _, q in sorted(self.queue.items())
                if q["state"] in gq.OPEN_STATES and start <= q["first_seen_run_date"] <= end]

    def aged_open_count(self, before):
        return sum(1 for q in self.queue.values()
                   if q["state"] in gq.OPEN_STATES and q["first_seen_run_date"] < before)

    def queue_rows(self, keys):
        return {gq.row_key(s, d): self.queue[gq.row_key(s, d)]
                for s, d in keys if gq.row_key(s, d) in self.queue}

    def set_state(self, source, document_id, fields):
        self.writes += 1
        self.queue[gq.row_key(source, document_id)].update(fields)

    def raw_signals(self, keys):
        return {gq.row_key(s, d): self.raw[gq.row_key(s, d)]
                for s, d in keys if gq.row_key(s, d) in self.raw}

    def handoffs_since(self, start):
        return [h for d, h in sorted(self.handoffs.items()) if d >= start]

    def get_handoff(self, handoff_date):
        return self.handoffs.get(handoff_date)

    def upsert_handoff(self, row):
        self.handoffs[row["handoff_date"]] = dict(row)

    def patch_handoff(self, handoff_date, fields):
        self.writes += 1
        self.handoffs[handoff_date].update(fields)

    def stale_prior_open(self, keep_date):
        n = 0
        for d, h in self.handoffs.items():
            if d != keep_date and h["status"] == gq.HANDOFF_OPEN:
                h.update(status=gq.HANDOFF_STALE, superseded_by=keep_date)
                n += 1
        return n


class _Item:
    def __init__(self, source, document_id):
        self.source, self.document_id = source, document_id


def _manifest(*rows):
    return [dict({"web_card_id": r.get("document_id", ""), "merged_into": "", "status_hint": "",
                  "signal_tier": "Tier 2", "section": "news"}, **r) for r in rows]


class ComputeStatesTest(unittest.TestCase):
    def setUp(self):
        self.manifest = _manifest(
            {"source": "FR", "document_id": "pub"},
            {"source": "FR", "document_id": "dropped"},
            {"source": "FR", "document_id": "t1", "signal_tier": "Tier 1"},
            {"source": "MFDS", "document_id": "watch", "section": "watch"},
            {"source": "FR", "document_id": "plain"},
            {"source": "FR", "document_id": "held"},
            {"source": "Recall", "document_id": "rep"},
            {"source": "Recall", "document_id": "member", "merged_into": "Recall::rep",
             "web_card_id": ""},
            {"source": "FR", "document_id": "err", "status_hint": "Error"},
        )
        self.delta = {"publish_date": "2026-11-16",
                      "cards": {"pub": {}, "dropped": {}, "rep": {}, "err": {}},
                      "deferred": ["FR::held"]}
        self.brief = {"cards": [{"id": "pub"}, {"id": "rep"}, {"id": "err"}]}

    def test_every_row_ends_in_exactly_one_state(self):
        got = gq.compute_states(self.manifest, self.delta, self.brief, "2026-11-16")
        self.assertEqual(len(got), len(self.manifest))
        self.assertEqual(got["FR::pub"], {"state": "processed", "reason": "published",
                                          "brief_publish_date": "2026-11-16"})
        self.assertEqual(got["FR::dropped"]["reason"], "carded_not_published")
        self.assertEqual(got["FR::dropped"]["state"], "skipped")
        self.assertEqual(got["FR::t1"]["reason"], "tier1")
        self.assertEqual(got["MFDS::watch"]["reason"], "watch")
        self.assertEqual(got["FR::plain"]["reason"], "not_carded")
        self.assertEqual(got["FR::held"]["state"], "deferred")
        self.assertEqual(got["Recall::member"]["state"], "processed")       # 대표를 따른다
        self.assertEqual(got["Recall::member"]["reason"], "merged:published")
        self.assertEqual(got["FR::err"]["state"], "error")                  # 실렸어도 Error 우선
        self.assertEqual(got["FR::held"]["brief_publish_date"], "")

    def test_brief_not_yet_published_is_provisional(self):
        got = gq.compute_states(self.manifest, self.delta, None, "2026-11-16")
        self.assertEqual(got["FR::dropped"], {"state": "processed",
                                              "reason": "carded_pending_publish",
                                              "brief_publish_date": "2026-11-16"})

    def test_deferred_but_carded_follows_the_card(self):
        self.delta["deferred"] = ["pub"]
        got = gq.compute_states(self.manifest, self.delta, self.brief, "2026-11-16")
        self.assertEqual(got["FR::pub"]["state"], "processed")

    def test_member_without_representative_is_not_consumed_as_published(self):
        m = _manifest({"source": "Recall", "document_id": "m", "merged_into": "Recall::gone"})
        got = gq.compute_states(m, {"cards": {}}, {"cards": []}, "2026-11-16")
        self.assertEqual(got["Recall::m"]["reason"], "merged_representative_missing")

    def test_pure(self):
        a = gq.compute_states(self.manifest, self.delta, self.brief, "2026-11-16")
        b = gq.compute_states(list(reversed(self.manifest)), self.delta, self.brief, "2026-11-16")
        self.assertEqual(a, b)


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "web", "data", "deltas"))
        os.makedirs(os.path.join(self.root, "web", "data", "briefs"))
        self.store = FakeStore()
        for doc in ("a", "b", "c"):
            self.store.add_raw(_record("FR", doc))
        self.store.enqueue([{"source": "FR", "document_id": d,
                             "first_seen_run_date": "2026-11-05",
                             "first_seen_at": "2026-11-05T03:30:00+09:00"} for d in "abc"])

    def _publish(self, day, cards, brief_ids=None, deferred=()):
        tag = day.replace("-", "_")
        with open(os.path.join(self.root, "web", "data", "deltas", f"delta_{tag}.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"publish_date": day, "cards": {c: {} for c in cards},
                       "deferred": list(deferred)}, f)
        if brief_ids is not None:
            with open(os.path.join(self.root, "web", "data", "briefs", f"brief_web_{tag}.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"cards": [{"id": c} for c in brief_ids]}, f)

    def _handoff(self, day, docs, status=gq.HANDOFF_OPEN):
        self.store.upsert_handoff({"handoff_date": day, "status": status, "consumed_evidence": "",
                                   "manifest": _manifest(*[{"source": "FR", "document_id": d}
                                                           for d in docs])})

    def test_no_delta_means_nothing_is_consumed(self):
        self._handoff("2026-11-09", "abc")
        out = gq.reconcile(self.store, self.root, RUN)
        self.assertEqual(out["handoffs"], 0)
        self.assertTrue(all(q["state"] == "new" for q in self.store.queue.values()))
        self.assertEqual(self.store.handoffs["2026-11-09"]["status"], gq.HANDOFF_OPEN)

    def test_consumes_and_is_idempotent(self):
        self._handoff("2026-11-09", "abc", status=gq.HANDOFF_STALE)   # 화요일 수집이 먼저 닫았어도
        self._publish("2026-11-09", ["a"], brief_ids=["a"], deferred=["FR::c"])
        gq.reconcile(self.store, self.root, RUN)
        self.assertEqual(self.store.queue["FR::a"]["state"], "processed")
        self.assertEqual(self.store.queue["FR::a"]["brief_publish_date"], "2026-11-09")
        self.assertEqual(self.store.queue["FR::b"]["state"], "skipped")
        self.assertEqual(self.store.queue["FR::c"]["state"], "deferred")
        self.assertEqual(self.store.handoffs["2026-11-09"]["status"], gq.HANDOFF_CONSUMED)
        self.assertEqual(self.store.handoffs["2026-11-09"]["consumed_evidence"],
                         "web/data/deltas/delta_2026_11_09.json")
        before = self.store.writes
        again = gq.reconcile(self.store, self.root, RUN)
        self.assertEqual(self.store.writes, before)           # 두 번째는 아무것도 쓰지 않는다
        self.assertEqual(again["changed"], 0)

    def test_deferred_row_reenters_and_older_handoff_does_not_pull_it_back(self):
        self._handoff("2026-11-09", "abc")
        self._publish("2026-11-09", ["a"], brief_ids=["a"], deferred=["FR::c"])
        gq.reconcile(self.store, self.root, RUN)
        rows, _, _ = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual([r["document_id"] for r in rows], ["c"])            # 보류분만 다시 후보
        self._handoff("2026-11-16", "c")
        self._publish("2026-11-16", ["c"], brief_ids=["c"])
        gq.reconcile(self.store, self.root, RUN)
        self.assertEqual(self.store.queue["FR::c"]["state"], "processed")
        self.assertEqual(self.store.queue["FR::c"]["handoff_date"], "2026-11-16")
        before = self.store.writes
        gq.reconcile(self.store, self.root, RUN)                              # 옛 호를 다시 계산해도
        self.assertEqual(self.store.queue["FR::c"]["state"], "processed")
        # 옛 호의 '보류'로 잠깐 되돌렸다가 다시 쓰지 않는다(중간에 끊기면 틀린 상태가 남는다).
        self.assertEqual(self.store.writes, before)

    def test_late_brief_corrects_provisional_state(self):
        self._handoff("2026-11-09", "ab")
        self._publish("2026-11-09", ["a", "b"])
        gq.reconcile(self.store, self.root, RUN)
        self.assertEqual(self.store.queue["FR::b"]["state_reason"], "carded_pending_publish")
        self._publish("2026-11-09", ["a", "b"], brief_ids=["a"])
        gq.reconcile(self.store, self.root, RUN)
        self.assertEqual(self.store.queue["FR::b"]["state"], "skipped")
        self.assertEqual(self.store.queue["FR::b"]["brief_publish_date"], None)

    def test_dry_run_writes_nothing(self):
        self._handoff("2026-11-09", "abc")
        self._publish("2026-11-09", ["a"], brief_ids=["a"])
        out = gq.reconcile(self.store, self.root, RUN, apply=False)
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(out["states"], {"processed": 1, "skipped": 2})


class CandidatesTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()

    def _enq(self, source, doc, run_date, **rec):
        self.store.add_raw(_record(source, doc, **rec))
        self.store.enqueue([{"source": source, "document_id": doc,
                             "first_seen_run_date": run_date,
                             "first_seen_at": f"{run_date}T03:30:00+09:00"}])

    def test_window_matches_notion_v1_filter(self):
        self._enq("FR", "edge-in", "2026-10-17")       # D-30 — 포함
        self._enq("FR", "edge-out", "2026-10-16")      # D-31 — 창 밖
        self._enq("FR", "today", "2026-11-16")
        rows, _, stats = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual(sorted(r["document_id"] for r in rows), ["edge-in", "today"])
        self.assertEqual(self.store.aged_open_count("2026-10-17"), 1)
        self.assertEqual(stats["handoff"], 2)

    def test_only_enqueued_documents_enter(self):
        self._enq("FR", "queued", "2026-11-12")
        self.store.add_raw(_record("FDA 483", "backfilled"))      # 정본에만 있음(과거분 수집기)
        rows, _, _ = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual([r["document_id"] for r in rows], ["queued"])

    def test_out_of_scope_483_is_closed_before_handoff(self):
        self._enq("FDA 483", "oos", "2026-11-12", raw={"fda483_text_status": "out-of-scope"})
        self._enq("FDA 483", "ok", "2026-11-12", raw={"fda483_text_status": "ok"})
        rows, _, stats = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual([r["document_id"] for r in rows], ["ok"])
        self.assertEqual(stats["pre_skipped"], 1)
        self.assertEqual(self.store.queue["FDA 483::oos"]["state"], "skipped")
        self.assertEqual(self.store.queue["FDA 483::oos"]["state_reason"], "out_of_scope_483")

    def test_snapshot_uses_queue_run_date_and_db_modality(self):
        self._enq("FR", "m", "2026-11-12", modality="Biologic",
                  collected_at="2026-11-01T10:00:00+00:00")
        rows, raws, _ = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual(rows[0]["run_date"], "2026-11-12")
        self.assertEqual(rows[0]["modality"], "Biologic")
        self.assertEqual(rows[0]["status"], "New")
        self.assertEqual(set(rows[0]), set(gh._HANDOFF_V2_ROW_KEEP))
        self.assertEqual(raws["FR::m"], {"k": "m"})

    def test_enqueue_reports_documents_missing_from_raw_signals(self):
        self.store.add_raw(_record("FR", "ok"))
        self.store.fail_batch = True
        with patch.object(gq, "log"):
            out = gq.enqueue_items(self.store, [_Item("FR", "ok"), _Item("FR", "no-raw"),
                                                _Item("FR", "ok")], RUN, GEN)
        self.assertEqual(out, {"requested": 2, "inserted": 1, "failed": 1})
        self.assertEqual(self.store.queue["FR::ok"]["first_seen_run_date"], "2026-11-16")


class DbHandoffTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.rows = []
        for doc in ("2026-20001", "2026-20002"):
            rec = _record("Federal Register", doc, type_or_class="Rule", qa_relevance="High")
            self.store.add_raw(rec)
            self.store.enqueue([{"source": "Federal Register", "document_id": doc,
                                 "first_seen_run_date": "2026-11-12",
                                 "first_seen_at": "2026-11-12T03:30:00+09:00"}])

    def test_same_material_gives_same_cards_as_notion_path(self):
        with patch.object(gh, "log"):
            db_payload, stats = gq.build_db_handoff(self.store, RUN, 30, 7, GEN)
            # 노션 경로 흉내: 같은 행·같은 원문을 노션 스냅샷 모양으로 주고 같은 빌더를 돈다.
            notion_rows, inmem = [], {}
            for key, rec in self.store.raw.items():
                snap, raw = gq.snapshot_from_raw_signal(rec, self.store.queue[key],
                                                        gh._HANDOFF_V2_ROW_KEEP)
                snap.update(page_id=f"page-{rec['document_id']}", title="t", status="New")
                notion_rows.append(snap)
                inmem[key] = raw
            enriched, _ = gh.enrich_rows_with_raw("", notion_rows, inmemory_raw=inmem, sleep_s=0)
            notion_payload = gh.build_routine_handoff_payload_v2(enriched, RUN, 7, GEN)
        self.assertEqual(stats["handoff"], 2)
        self.assertEqual(db_payload["handoff_id"], "routine-handoff::2026-11-16")
        cmp_ = gq.compare_payloads(notion_payload, db_payload)
        self.assertEqual((cmp_["common"], cmp_["identical"]), (2, 2))
        self.assertEqual(cmp_["only_notion"] + cmp_["only_db"], [])
        self.assertTrue(cmp_["coverage_same"])

    def test_record_handoff_keeps_one_open_and_a_bodyless_manifest(self):
        with patch.object(gh, "log"):
            payload, _ = gq.build_db_handoff(self.store, RUN, 30, 7, GEN)
        self.store.upsert_handoff({"handoff_date": "2026-11-15", "status": gq.HANDOFF_OPEN})
        recorded = gq.record_handoff(self.store, payload, 30, GEN)
        self.assertEqual(recorded, {"staled": 1, "frozen": ""})
        self.assertEqual(self.store.handoffs["2026-11-15"]["status"], gq.HANDOFF_STALE)
        today = self.store.handoffs["2026-11-16"]
        self.assertEqual(today["status"], gq.HANDOFF_OPEN)
        self.assertEqual(len(today["manifest"]), 2)
        self.assertNotIn("card_scaffold", today["manifest"][0])
        self.assertEqual(today["payload_sha256"], gq.payload_sha256(payload))

    def test_payload_sha_ignores_generation_time(self):
        with patch.object(gh, "log"):
            a, _ = gq.build_db_handoff(self.store, RUN, 30, 7, GEN)
            b, _ = gq.build_db_handoff(self.store, RUN, 30, 7, GEN.replace(hour=9))
        self.assertEqual(gq.payload_sha256(a), gq.payload_sha256(b))


class FreezeTest(unittest.TestCase):
    """월요일 발행 뒤 같은 날 수집을 다시 돌려도 그 호의 행 목록이 바뀌지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "web", "data", "deltas"))
        os.makedirs(os.path.join(self.root, "web", "data", "briefs"))
        self.store = FakeStore()
        for doc in ("2026-1", "2026-2"):
            self.store.add_raw(_record("Federal Register", doc))
        self.store.enqueue([{"source": "Federal Register", "document_id": d,
                             "first_seen_run_date": "2026-11-13",
                             "first_seen_at": "2026-11-13T03:30:00+09:00"}
                            for d in ("2026-1", "2026-2")])

    def _shadow(self, **kw):
        with patch.object(gq, "log"), patch.object(gh, "log"), \
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            return gq.run_shadow(("u", "k"), run_date=RUN, generated_at=GEN, window_days=30,
                                 display_window_days=7, store=self.store, out_dir=self.root,
                                 repo_root=self.root, **kw)

    def _write(self, kind, name, payload):
        with open(os.path.join(self.root, "web", "data", kind, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_rerun_after_publish_keeps_the_issue_manifest(self):
        self._shadow()                                             # 월요일 새벽 수집
        first = dict(self.store.handoffs["2026-11-16"])
        self.assertEqual(len(first["manifest"]), 2)
        # 발행: 델타만 먼저 main 에 들어온다(발행본은 아직).
        self._write("deltas", "delta_2026_11_16.json",
                    {"publish_date": "2026-11-16", "cards": {"2026-1": {}, "2026-2": {}}})
        # 같은 날 재실행 — 새 문서 1건이 더 들어왔다.
        self.store.add_raw(_record("Federal Register", "2026-3"))
        report = self._shadow(new_items=[_Item("Federal Register", "2026-3")])
        after = self.store.handoffs["2026-11-16"]
        self.assertEqual(after["status"], gq.HANDOFF_CONSUMED)
        self.assertEqual(after["manifest"], first["manifest"])      # 행 목록 그대로
        self.assertEqual(after["payload_sha256"], first["payload_sha256"])
        self.assertEqual(report["handoff_frozen"], gq.HANDOFF_CONSUMED)
        self.assertEqual(self.store.queue["Federal Register::2026-3"]["state"], "new")  # 다음 날 몫
        # 재실행분 파일은 다른 이름으로 나간다 — 브랜치 사본이 그 호의 파일을 덮어쓰지 않게.
        self.assertTrue(os.path.exists(os.path.join(self.root, "handoff_db_2026-11-16.rerun.json")))
        with open(os.path.join(self.root, "handoff_db_2026-11-16.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["row_count"], 2)             # 첫 실행분 그대로
        # 발행본이 늦게 들어와도 임시 상태가 확정된다(행 목록이 남아 있으므로).
        self.assertEqual(self.store.queue["Federal Register::2026-2"]["state_reason"],
                         "carded_pending_publish")
        self._write("briefs", "brief_web_2026_11_16.json", {"cards": [{"id": "2026-1"}]})
        self._shadow()
        self.assertEqual(self.store.queue["Federal Register::2026-2"]["state"], "skipped")
        self.assertEqual(self.store.queue["Federal Register::2026-2"]["state_reason"],
                         "carded_not_published")
        self.assertEqual(self.store.queue["Federal Register::2026-1"]["state_reason"], "published")

    def test_same_day_rerun_before_publish_still_refreshes(self):
        self._shadow()
        self.store.add_raw(_record("Federal Register", "2026-3"))
        report = self._shadow(new_items=[_Item("Federal Register", "2026-3")])
        self.assertEqual(report["handoff_frozen"], "")
        self.assertEqual(len(self.store.handoffs["2026-11-16"]["manifest"]), 3)
        self.assertEqual(self.store.handoffs["2026-11-16"]["status"], gq.HANDOFF_OPEN)


class HealTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        for doc in ("in-queue", "lost", "done"):
            self.store.add_raw(_record("FR", doc))
        self.store.enqueue([{"source": "FR", "document_id": d,
                             "first_seen_run_date": "2026-11-10",
                             "first_seen_at": "2026-11-10T03:30:00+09:00"}
                            for d in ("in-queue", "done")])
        self.store.queue["FR::done"]["state"] = "processed"
        self.payload = {"rows": [
            {"source": "FR", "document_id": "in-queue", "run_date": "2026-11-10"},
            {"source": "FR", "document_id": "lost", "run_date": "2026-11-11",
             "collected_at": "2026-11-11T03:31:00.000+09:00"},
            {"source": "FR", "document_id": "done", "run_date": "2026-11-10"},
            {"source": "FR", "document_id": "no-raw", "run_date": "2026-11-11",
             "collected_at": "2026-11-11T03:31:00.000+09:00"},
        ]}

    def test_reenqueues_only_missing_rows_with_notion_run_date(self):
        with patch.object(gq, "log"):
            out = gq.heal_from_notion(self.store, self.payload, GEN)
        self.assertEqual(out, {"checked": 4, "missing": 2, "inserted": 1, "failed": 1})
        lost = self.store.queue["FR::lost"]
        self.assertEqual((lost["first_seen_run_date"], lost["enqueued_by"], lost["state"]),
                         ("2026-11-11", "heal", "new"))
        self.assertEqual(self.store.queue["FR::done"]["state"], "processed")   # 있던 행은 그대로
        with patch.object(gq, "log"):
            again = gq.heal_from_notion(self.store, self.payload, GEN)
        self.assertEqual((again["missing"], again["inserted"]), (1, 0))        # no-raw 만 남는다

    def test_shadow_reports_heal_and_health_summary(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(gq, "log"), \
                patch.object(gh, "log"), patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            report = gq.run_shadow(("u", "k"), run_date=RUN, generated_at=GEN, window_days=30,
                                   display_window_days=7, store=self.store, out_dir=tmp,
                                   repo_root=tmp, notion_payload=self.payload)
            with open(os.path.join(tmp, "queue_shadow_2026-11-16.md"), encoding="utf-8") as f:
                md = f.read()
        self.assertEqual(report["heal"]["inserted"], 1)
        self.assertIn("대기열 보충", md)
        health = gq.health_summary(report)
        self.assertEqual((health["healed"], health["heal_missing"], health["heal_failed"]), (1, 2, 1))
        self.assertEqual(health["handoff_frozen"], "")
        self.assertTrue(all(not isinstance(v, (list, dict)) for v in health.values()))


class BuilderConditionsTest(unittest.TestCase):
    """재생 대조 10절에서 나온 빌더 조건 — 공백 · 483 범위."""

    def setUp(self):
        self.store = FakeStore()

    def _enq(self, source, doc, **rec):
        self.store.add_raw(_record(source, doc, **rec))
        self.store.enqueue([{"source": source, "document_id": doc,
                             "first_seen_run_date": "2026-11-12",
                             "first_seen_at": "2026-11-12T03:30:00+09:00"}])

    def test_edge_whitespace_is_trimmed_like_the_notion_reader(self):
        self._enq("MFDS", "recall-1", headline="회수 공지\r", firm="  가나제약\r\n",
                  body="첫 줄\r\n둘째 줄")
        rows, _, _ = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual(rows[0]["headline"], "회수 공지")
        self.assertEqual(rows[0]["firm"], "가나제약")
        self.assertEqual(rows[0]["body"], "첫 줄\r\n둘째 줄")          # 글 안쪽은 그대로

    def test_notion_snapshot_reader_trims_the_same_way(self):
        # 정규화 기준이 노션 읽기 규칙이라는 것을 잠근다 — 그쪽이 바뀌면 여기도 다시 봐야 한다.
        page = {"properties": {"Headline": {"rich_text": [{"plain_text": "회수 공지\r"}]}}}
        import grm_notion
        snap = gh._intake_page_snapshot(
            {"properties": {grm_notion.PROP_HEADLINE: page["properties"]["Headline"]}})
        self.assertEqual(snap["headline"], "회수 공지")

    def test_483_out_of_scope_by_current_rule_is_closed_before_handoff(self):
        self._enq("FDA 483", "blood", raw={"establishment_type": "Blood Bank",
                                           "fda_483_observations": []})
        self._enq("FDA 483", "drug", raw={"establishment_type": "Drug Manufacturer",
                                          "fda_483_observations": [
                                              {"deficiency": "Batch records", "detail": "x"}]})
        rows, _, stats = gq.load_candidates(self.store, RUN, 30, gh._HANDOFF_V2_ROW_KEEP)
        self.assertEqual([r["document_id"] for r in rows], ["drug"])
        self.assertEqual(stats["pre_skipped"], 1)
        self.assertEqual(self.store.queue["FDA 483::blood"]["state_reason"], "out_of_scope_483_rule")

    def test_scope_verdict_is_the_taxonomy_function_not_a_copy(self):
        import grm_taxonomy
        raw = {"establishment_type": "Whatever", "fda_483_observations": [
            {"deficiency": "d", "detail": "t"}]}
        with patch.object(gq, "is_out_of_scope_483", return_value=True) as fn:
            self.assertEqual(gq.pre_handoff_skip_reason("FDA 483", raw), "out_of_scope_483_rule")
        fn.assert_called_once_with("Whatever", "d t")
        self.assertIs(gq.is_out_of_scope_483, grm_taxonomy.is_out_of_scope_483)
        with patch.object(gq, "is_out_of_scope_483", return_value=True):
            self.assertEqual(gq.pre_handoff_skip_reason("FDA Warning Letter", raw), "")   # 483 만

    def test_sponsor_and_cro_stay_in_scope(self):
        # 11-12 대표 결정(#1602): 의약품 임상 스폰서·CRO 는 범위 안.
        import grm_taxonomy
        for est in ("Sponsor", "Contract Research Organization"):
            want = "out_of_scope_483_rule" if grm_taxonomy.is_out_of_scope_483(est, "") else ""
            self.assertEqual(gq.pre_handoff_skip_reason(
                "FDA 483", {"establishment_type": est, "fda_483_observations": []}), want)

    def test_only_notion_rows_closed_as_out_of_scope_are_reported_as_explained(self):
        self._enq("FDA 483", "blood", raw={"establishment_type": "Blood Bank"})
        self._enq("FDA 483", "drug", raw={"establishment_type": "Drug Manufacturer"})
        notion = {"rows": [{"source": "FDA 483", "document_id": d, "run_date": "2026-11-12"}
                           for d in ("blood", "drug", "missing")]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(gq, "log"), \
                patch.object(gh, "log"), patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            report = gq.run_shadow(("u", "k"), run_date=RUN, generated_at=GEN, window_days=30,
                                   display_window_days=7, store=self.store, out_dir=tmp,
                                   repo_root=tmp, notion_payload=notion)
        cmp_ = report["compare"]
        self.assertEqual(cmp_["only_notion_out_of_scope"], ["FDA 483::blood"])
        self.assertEqual(cmp_["only_notion"], ["FDA 483::missing"])
        self.assertIn("설명됨", gq.render_report_md(report))


class HandoffMirrorWorkflowTest(unittest.TestCase):
    """브랜치 사본 워크플로와 수집기의 파일 이름 계약."""

    def setUp(self):
        self.wf = (ROOT / ".github" / "workflows" / "grm-queue-handoff-mirror.yml").read_text(
            encoding="utf-8")

    def test_mirror_glob_takes_the_normal_file_and_not_the_rerun_file(self):
        import fnmatch
        glob = "handoff_db_????-??-??.json"
        self.assertIn(glob, self.wf)
        self.assertTrue(fnmatch.fnmatch(gq.handoff_file_name(RUN), glob))
        self.assertFalse(fnmatch.fnmatch(gq.handoff_file_name(RUN, "consumed"), glob))
        self.assertFalse(fnmatch.fnmatch(gq.handoff_file_name(RUN, "stale"), glob))

    def test_mirror_reads_the_artifact_the_intake_uploads(self):
        intake = (ROOT / ".github" / "workflows" / "grm-intake.yml").read_text(encoding="utf-8")
        self.assertIn("name: grm-queue-shadow", intake)
        self.assertIn("-n grm-queue-shadow", self.wf)
        self.assertIn(f"path: {gq.DEFAULT_SHADOW_DIR}/", intake)
        self.assertIn('workflows: ["GRM API Intake (Daily)"]', self.wf)
        self.assertRegex(intake, r"(?m)^name: GRM API Intake \(Daily\)")

    def test_branch_is_rewritten_as_a_single_parentless_commit(self):
        self.assertIn("git checkout -q --orphan mirror", self.wf)
        self.assertIn("git checkout -q FETCH_HEAD -- .", self.wf)
        self.assertNotIn("checkout -q -b mirror FETCH_HEAD", self.wf)     # 기록을 잇지 않는다
        self.assertIn("contents: write", self.wf)
        self.assertNotIn("secrets.", self.wf)


class OrderTest(unittest.TestCase):
    def test_shadow_runs_after_the_daily_raw_update(self):
        src = (ROOT / "collect_intake.py").read_text(encoding="utf-8")
        update = src.index("raw_update.apply_updates(")
        notion_emit = src.index("handoff_row_count, handoff_url = emit_routine_handoff(")
        shadow = src.index("grm_queue.run_shadow(")
        self.assertLess(update, notion_emit)      # 노션 handoff 도 R4 뒤
        self.assertLess(notion_emit, shadow)      # DB handoff 는 그 뒤(비교 대상이 있어야 한다)
        self.assertEqual(src.count("grm_queue.run_shadow("), 1)


class CompareTest(unittest.TestCase):
    def test_classifies_who_is_richer(self):
        n = {"rows": [{"source": "FR", "document_id": "1", "body": "abc…", "modality": "Bio",
                       "page_id": "p"},
                      {"source": "FR", "document_id": "only-n"}]}
        d = {"rows": [{"source": "FR", "document_id": "1", "body": "abcdef", "modality": "",
                       "page_id": ""},
                      {"source": "FR", "document_id": "only-d"}]}
        out = gq.compare_payloads(n, d)
        self.assertEqual(out["only_notion"], ["FR::only-n"])
        self.assertEqual(out["only_db"], ["FR::only-d"])
        self.assertEqual(out["identical"], 0)
        self.assertEqual(out["field_diff"], {"FR|body|db_richer": 1,
                                             "FR|modality|notion_only_value": 1})
        self.assertIn("db_richer", gq.render_report_md({"run_date": "x", "compare": out}))

    def test_state_compare_treats_deferred_as_notion_new(self):
        computed = {"FR::a": {"state": "processed", "reason": "published"},
                    "FR::b": {"state": "deferred", "reason": "deferred"},
                    "FR::c": {"state": "skipped", "reason": "not_carded"},
                    "FR::d": {"state": "skipped", "reason": "tier1"},
                    "FR::e": {"state": "skipped", "reason": "carded_not_published"}}
        out = gq.compare_states(computed, {"FR::a": "Processed", "FR::b": "New",
                                           "FR::c": "Processed", "FR::e": "Processed"})
        self.assertEqual((out["match"], out["explained"], out["mismatch"]), (2, 1, 1))
        self.assertEqual(out["not_in_notion"], ["FR::d"])
        self.assertEqual(out["mismatched"][0]["key"], "FR::c")


class ShadowTest(unittest.TestCase):
    def test_store_failure_never_raises_and_is_reported(self):
        class Broken(FakeStore):
            def open_candidates(self, start, end):
                raise gq.QueueStoreError("intake_queue GET: http_404")   # migration 미적용

        with tempfile.TemporaryDirectory() as tmp, patch.object(gq, "log"), \
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            report = gq.run_shadow(("u", "k"), run_date=RUN, generated_at=GEN, window_days=30,
                                   display_window_days=7, store=Broken(), out_dir=tmp,
                                   repo_root=tmp, notion_payload={"rows": []})
            self.assertTrue(any("DB handoff 생성 실패" in w for w in report["warnings"]))
            self.assertNotIn("compare", report)
            self.assertTrue(os.path.exists(os.path.join(tmp, "queue_shadow_2026-11-16.md")))

    def test_full_round_writes_files_and_compares(self):
        store = FakeStore()
        store.add_raw(_record("Federal Register", "2026-1"))
        with tempfile.TemporaryDirectory() as tmp, patch.object(gq, "log"), \
                patch.object(gh, "log"), patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            report = gq.run_shadow(("u", "k"), run_date=RUN, generated_at=GEN, window_days=30,
                                   display_window_days=7, store=store, out_dir=tmp, repo_root=tmp,
                                   new_items=[_Item("Federal Register", "2026-1")],
                                   notion_payload={"rows": []})
            self.assertEqual(report["enqueue"]["inserted"], 1)
            self.assertEqual(report["compare"]["only_db"], ["Federal Register::2026-1"])
            self.assertEqual(report["warnings"], [])
            for name in ("handoff_db_2026-11-16.json", "brief_web_2026_11_16.json",
                         "queue_shadow_2026-11-16.json"):
                self.assertTrue(os.path.exists(os.path.join(tmp, name)), name)

    def test_flag_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_INTAKE_QUEUE_SHADOW", None)
            self.assertFalse(gq.shadow_enabled())


class SeedPlanTest(unittest.TestCase):
    def test_rules(self):
        since = "2026-11-09T03:40:00+09:00"
        recs = [
            {"source": "FR", "document_id": "old", "collected_at": "2026-11-09T03:40:00+09:00"},
            {"source": "FR", "document_id": "new", "collected_at": "2026-11-10T03:30:00+09:00"},
            {"source": "Health Canada Inspection", "document_id": "x1",
             "collected_at": "2026-11-10T03:30:00+09:00"},
            {"source": "FDA 483", "document_id": "x2", "collected_at": "2026-11-10T03:30:00+09:00",
             "published_date": "2026-11-08"},
            {"source": "FDA 483", "document_id": "x4", "collected_at": "2026-11-10T03:30:00+09:00",
             "published_date": "2025-01-01"},
            {"source": "FDA 483", "document_id": "keep", "collected_at": "2026-11-10T18:30:00+00:00",
             "published_date": "2026-11-08"},
            {"source": "ICH", "document_id": "nodate", "collected_at": ""},
        ]
        plan, stats = gq.plan_seed(recs, {"FDA 483::x2"}, since)
        self.assertEqual([p["document_id"] for p in plan], ["new", "keep"])
        self.assertEqual(plan[1]["first_seen_run_date"], "2026-11-11")       # UTC 18:30 = KST 다음 날
        self.assertEqual(stats, {"before_since": 1, "X1": 1, "X2": 1, "X4": 1, "undated": 1,
                                 "planned": 2})


class MigrationTest(unittest.TestCase):
    def test_tables_are_closed_to_anon(self):
        sql = (ROOT / "web" / "migrations" / "149_intake_queue.sql").read_text(encoding="utf-8")
        for table in ("intake_queue", "intake_handoffs"):
            self.assertIn(f"alter table public.{table}", sql)
            self.assertRegex(sql, rf"revoke all on public\.{table}\s+from public, anon, authenticated")
        self.assertNotIn("create policy", sql.lower())
        self.assertNotIn("drop ", sql.lower())            # 가산만
        self.assertIn("references public.raw_signals (source, document_id)", sql)


class HandoffPayloadOutTest(unittest.TestCase):
    def test_payload_out_is_optional_and_additive(self):
        import inspect
        sig = inspect.signature(gh.emit_routine_handoff)
        self.assertIsNone(sig.parameters["payload_out"].default)


if __name__ == "__main__":
    unittest.main()
