#!/usr/bin/env python3
"""[원문 갱신 규칙 R4 2026-11-12] raw_update — 본문이 바뀌고 나빠지지 않을 때만 함수로 갱신한다. 네트워크 없음."""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone

import collect_intake as ci
import findings_store
import grm_common
import grm_findings as gf
import raw_update as ru

_AT = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _item(doc, raw, source="FDA Warning Letter", kind="warning-letter"):
    return ci.IntakeItem(source=source, document_id=doc, date_iso="2026-11-01",
                         headline=f"h {doc}", official_url="https://x", type_or_class=kind,
                         raw_payload=raw)


def _stored_row(item, raw):
    rec = findings_store.raw_signal_from_intake_item(item, collected_at=_AT)
    text = gf.canonical_json(raw)
    rec.update(raw_json=text, raw_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    return rec


class _Resp:
    def __init__(self, rows, status=200):
        self.status_code, self._rows = status, rows

    def json(self):
        return self._rows


def _get(stored_rows, findings_rows=(), fail=False):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if fail:
            return _Resp([], 500)
        ids = params[next(k for k in params if k != "select")][4:-1].split(",")
        rows = stored_rows if url.endswith("/raw_signals") else list(findings_rows)
        return _Resp([r for r in rows if r["raw_signal_id"] in ids])
    return get, calls


class SourceCoverageTest(unittest.TestCase):

    def test_every_source_has_content_fields(self):
        """원천은 빠짐없이 — grm_common 의 모든 SOURCE_* 와 HC 실사가 본문 칸 정의를 가진다."""
        sources = {v for k, v in vars(grm_common).items()
                   if k.startswith("SOURCE_") and isinstance(v, str)}
        sources.add(ru.SOURCE_HC_INSPECTION)
        self.assertEqual(sorted(sources - set(ru.CONTENT_FIELDS)), [])

    def test_unknown_source_is_never_updated(self):
        self.assertEqual(ru.decide("Nope", {"a": 1}, {"a": 2})["kind"], "unknown_source")


class DecideTest(unittest.TestCase):
    S = "FDA Warning Letter"

    def test_volatile_non_body_change_is_same(self):
        d = ru.decide(self.S, {"wl_body_full": "body", "fetched_at": "1"},
                      {"wl_body_full": "body  ", "fetched_at": "2"})
        self.assertEqual(d["kind"], "same")

    def test_today_without_body_is_not_comparable(self):
        self.assertEqual(ru.decide(self.S, {"wl_body_full": "body"}, {"url": "u"})["kind"],
                         "no_body_today")

    def test_grown_body_updates_and_keeps_enrichment(self):
        stored = {"wl_body_full": "Your firm failed", "wl_violations": ["a"], "enriched": "keep"}
        today = {"wl_body_full": "Your firm failed to validate.", "wl_violations": ["a"], "enriched": ""}
        d = ru.decide(self.S, stored, today)
        self.assertEqual(d["kind"], "update")
        self.assertEqual(d["merged"]["enriched"], "keep")       # 오늘 빈 칸은 좋은 값을 지우지 않는다
        self.assertEqual(d["diff_summary"]["changed_fields"], ["wl_body_full"])
        self.assertNotIn("Your firm", json.dumps(d["diff_summary"]))   # 값은 싣지 않는다

    def test_shrunk_body_is_rejected(self):
        d = ru.decide(self.S, {"wl_body_full": "x" * 1000}, {"wl_body_full": "x" * 500})
        self.assertEqual(d["kind"], "rejected_worse")
        self.assertIn("body_shrunk", d["reasons"])

    def test_list_shrink_is_rejected(self):
        d = ru.decide("FDA 483", {"fda_483_observations": [{"deficiency": "a"}, {"deficiency": "b"}]},
                      {"fda_483_observations": [{"deficiency": "a" * 50}]})
        self.assertIn("list_shrunk:fda_483_observations", d["reasons"])

    def test_new_failure_marker_is_rejected(self):
        d = ru.decide("FDA 483", {"fda483_excerpt": "obs text"},
                      {"fda483_excerpt": "obs text longer", "fda483_text_status": "scan-ocr-unavailable:x"})
        self.assertIn("marker:fda483_text_status", d["reasons"])

    def test_newly_truncated_observation_is_rejected(self):
        d = ru.decide("FDA 483", {"fda_483_observations": [{"deficiency": "a", "detail": "full stop."}]},
                      {"fda_483_observations": [{"deficiency": "a", "detail": "full stop and more …"}]})
        self.assertIn("truncated:fda_483_observations", d["reasons"])

    def test_case_and_punctuation_changes_count(self):
        d = ru.decide(self.S, {"wl_body_full": "cGMP."}, {"wl_body_full": "CGMP."})
        self.assertEqual(d["kind"], "update")


class PairFindingsTest(unittest.TestCase):

    def _f(self, fid, text):
        return {"finding_id": fid, "finding_text": text}

    def test_prefix_extension_becomes_restore_item(self):
        live = {"f1": {"finding_id": "f1", "raw_signal_id": "r", "finding_text": "Failure to …",
                       "published_date": "2026-11-01"}}
        out = ru.pair_findings([self._f("f1", "x")], [self._f("n1", "Failure to validate.")], live)
        self.assertEqual(len(out["restore"]), 1)
        it = out["restore"][0]
        self.assertEqual(it["finding_id"], "f1")                      # id 유지
        self.assertEqual(it["finding_text_old_md5"], hashlib.md5("Failure to …".encode()).hexdigest())
        self.assertEqual(set(it), {"finding_id", "raw_signal_id", "published_date", "finding_text_old_md5",
                                   "finding_text", "old_chars", "new_chars", "legacy_match"})

    def test_same_heading_detail_change_is_restore(self):
        live = {"f1": {"finding_id": "f1", "finding_text": "Head\nold detail"}}
        out = ru.pair_findings([self._f("f1", "")], [self._f("n", "Head\nnew detail")], live)
        self.assertEqual(len(out["restore"]), 1)

    def test_unrelated_text_is_ambiguous_not_written(self):
        live = {"f1": {"finding_id": "f1", "finding_text": "Alpha"}}
        out = ru.pair_findings([self._f("f1", "")], [self._f("n", "Beta")], live)
        self.assertEqual((out["restore"], out["ambiguous"]), ([], 1))

    def test_counts_new_only_old_only_not_live(self):
        live = {"f1": {"finding_id": "f1", "finding_text": "A"},
                "f2": {"finding_id": "f2", "finding_text": "B"}}
        out = ru.pair_findings([self._f("f1", ""), self._f("f2", ""), self._f("f3", "")],
                               [self._f("n1", "A")], live)
        self.assertEqual((out["same"], out["old_only"], out["not_live"]), (1, 1, 1))
        out = ru.pair_findings([self._f("f1", "")], [self._f("n1", "A"), self._f("n2", "C")], live)
        self.assertEqual(out["new_only"], 1)


class ApplyUpdatesTest(unittest.TestCase):

    def setUp(self):
        self.applied = []

    def _apply(self, status="applied"):
        def fn(base, key, **kw):
            self.applied.append(kw)
            return {"status": status}
        return fn

    def _wl(self, doc="wl-1", stored_body="Your firm failed", today_body="Your firm failed to validate."):
        stored_raw = {"wl_body_full": stored_body, "subject": "CGMP"}
        item = _item(doc, {"wl_body_full": today_body, "subject": "CGMP"})
        return item, _stored_row(item, stored_raw)

    def test_update_goes_through_function_with_read_fingerprint(self):
        item, row = self._wl()
        get, _ = _get([row])
        summary, plan = ru.apply_updates("https://x.supabase.co", "k", [item], collected_at=_AT,
                                         get=get, apply_fn=self._apply(), extract=lambda r: [],
                                         changed_by="t")
        self.assertEqual(summary["applied"], 1)
        kw = self.applied[0]
        self.assertEqual(kw["expected_raw_sha256"], row["raw_sha256"])
        self.assertEqual(kw["change_kind"], "source_update")
        self.assertEqual(json.loads(kw["record"]["raw_json"])["wl_body_full"], "Your firm failed to validate.")
        self.assertEqual(kw["record"]["row_json"], row["row_json"])     # 열 값은 그대로
        self.assertEqual(set(kw["record"]), {"raw_json", "raw_sha256", "row_json"})
        self.assertEqual(plan["schema_version"], "grm-wl-truncation-restore-plan/v1")

    def test_dry_run_and_lookup_failure_never_write(self):
        item, row = self._wl()
        get, _ = _get([row])
        summary, _ = ru.apply_updates("https://x.supabase.co", "k", [item], collected_at=_AT,
                                      get=get, apply_fn=self._apply(), extract=lambda r: [],
                                      dry_run=True)
        self.assertEqual((summary["would_apply"], self.applied), (1, []))
        get, _ = _get([row], fail=True)
        summary, _ = ru.apply_updates("https://x.supabase.co", "k", [item], collected_at=_AT,
                                      get=get, apply_fn=self._apply(), extract=lambda r: [])
        self.assertEqual((summary["lookup_failed"], self.applied), (1, []))

    def test_rejected_and_same_are_not_written(self):
        a, ra = self._wl("wl-a", stored_body="x" * 1000, today_body="x" * 100)
        b, rb = self._wl("wl-b", stored_body="same", today_body="same")
        get, _ = _get([ra, rb])
        summary, _ = ru.apply_updates("https://x.supabase.co", "k", [a, b], collected_at=_AT,
                                      get=get, apply_fn=self._apply(), extract=lambda r: [])
        self.assertEqual(self.applied, [])
        counts = summary["by_source"]["FDA Warning Letter"]
        self.assertEqual((counts["rejected_worse"], counts["same"]), (1, 1))

    def test_cap_and_conflict(self):
        items, rows = zip(*[self._wl(f"wl-{i}") for i in range(3)])
        get, _ = _get(list(rows))
        summary, _ = ru.apply_updates("https://x.supabase.co", "k", list(items), collected_at=_AT,
                                      get=get, apply_fn=self._apply("conflict"),
                                      extract=lambda r: [], max_apply=2)
        self.assertEqual((summary["capped"], summary["conflict"], summary["applied"]), (1, 2, 0))

    def test_findings_restore_plan_from_old_and_new_extraction(self):
        item, row = self._wl()
        live = [{"finding_id": "f-old", "raw_signal_id": row["raw_signal_id"],
                 "finding_text": "Your firm failed", "published_date": "2026-11-01"}]
        get, _ = _get([row], live)

        def extract(rec):
            body = json.loads(rec["raw_json"])["wl_body_full"]
            fid = "f-old" if body == "Your firm failed" else "f-new"
            return [{"finding_id": fid, "finding_text": body}]
        summary, plan = ru.apply_updates("https://x.supabase.co", "k", [item], collected_at=_AT,
                                         get=get, apply_fn=self._apply(), extract=extract)
        self.assertEqual([it["finding_id"] for it in plan["items"]], ["f-old"])
        self.assertEqual(plan["items"][0]["finding_text"], "Your firm failed to validate.")
        self.assertEqual(summary["findings"]["restore"], 1)

    def test_mfds_inspection_resyncs_attendees(self):
        item = _item("mfds-1", {"attachment_text": "평가 결과: 적합 — 추가 내용"},
                     source="MFDS", kind="gmp-inspection")
        row = _stored_row(item, {"attachment_text": "평가 결과: 적합"})
        get, _ = _get([row])
        synced = []
        ru.apply_updates("https://x.supabase.co", "k", [item], collected_at=_AT, get=get,
                         apply_fn=self._apply(), extract=lambda r: [],
                         sync_fn=lambda b, k, rec: synced.append(rec))
        self.assertEqual(len(synced), 1)
        self.assertEqual(synced[0]["raw_sha256"], self.applied[0]["record"]["raw_sha256"])


if __name__ == "__main__":
    unittest.main()
