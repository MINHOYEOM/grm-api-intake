#!/usr/bin/env python3
"""[원문 갱신 규칙 R0 2026-11-12] raw_drift — 오늘 판 vs 정본 비교는 보고만 하고 쓰지 않는다."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

import collect_intake as ci
import findings_store
import raw_drift as rd

_AT = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _item(doc, raw, source="FDA 483"):
    return ci.IntakeItem(source=source, document_id=doc, date_iso="2026-11-01",
                         headline=f"h {doc}", official_url="https://x", type_or_class="483",
                         raw_payload=raw)


def _rid(item):
    return findings_store.raw_signal_from_intake_item(item, collected_at=_AT)["raw_signal_id"]


class CompareRawTest(unittest.TestCase):

    def test_same_ignores_key_order(self):
        d = rd.compare_raw({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1})
        self.assertTrue(d["same"])

    def test_changed_keys_and_shrink(self):
        old = {"obs": [{"n": 1, "detail": "x" * 500}, {"n": 2, "detail": "y" * 500}], "t": 1}
        new = {"obs": [{"n": 1, "detail": "x" * 500}], "t": 2}
        d = rd.compare_raw(old, new)
        self.assertFalse(d["same"])
        self.assertEqual(d["changed_keys"], ["obs", "t"])
        self.assertTrue(d["shrunk"])
        self.assertEqual(d["list_shrunk_keys"], ["obs"])

    def test_added_and_dropped_keys(self):
        d = rd.compare_raw({"a": 1, "gone": 2}, {"a": 1, "new": 3})
        self.assertEqual(d["added_keys"], ["new"])
        self.assertEqual(d["dropped_keys"], ["gone"])


class MeasureDriftTest(unittest.TestCase):

    def test_counts_per_source_and_never_writes(self):
        same = _item("s", {"v": 1})
        grew = _item("g", {"v": 1, "detail": "longer text"})
        shrunk = _item("k", {"v": 1})
        absent = _item("a", {"v": 1})
        stored = {_rid(same): {"v": 1}, _rid(grew): {"v": 1},
                  _rid(shrunk): {"v": 1, "detail": "x" * 200}}
        fetch = mock.Mock(return_value=stored)
        with mock.patch.object(rd.requests, "post") as post, \
                mock.patch.object(rd.requests, "patch") as patch_:
            s = rd.measure_drift("https://e.supabase.co", "k", [same, grew, shrunk, absent],
                                 collected_at=_AT, fetch=fetch)
        post.assert_not_called()
        patch_.assert_not_called()
        src = s["by_source"]["FDA 483"]
        self.assertEqual((src["compared"], src["same"], src["differ"]), (3, 1, 2))
        self.assertEqual((src["shrunk"], src["grew"]), (1, 1))
        self.assertEqual(s["missing_in_db"], 1)
        self.assertEqual(src["keys"], {"detail": 2})
        self.assertNotIn("x" * 50, json.dumps(s))    # 값은 싣지 않는다(칸 이름·크기만)

    def test_lookup_failure_reports_and_skips(self):
        s = rd.measure_drift("https://e.supabase.co", "k", [_item("s", {"v": 1})],
                             collected_at=_AT, fetch=lambda *a: None)
        self.assertEqual(s["lookup_failed"], 1)
        self.assertEqual(s["by_source"], {})

    def test_cap_limits_lookups(self):
        items = [_item(f"d{i}", {"v": i}) for i in range(5)]
        fetch = mock.Mock(return_value={})
        s = rd.measure_drift("https://e.supabase.co", "k", items, collected_at=_AT,
                             max_items=2, fetch=fetch)
        self.assertEqual(s["capped"], 3)
        self.assertEqual(len(fetch.call_args.args[2]), 2)


class FetchStoredRawTest(unittest.TestCase):

    def test_chunks_and_parses(self):
        ids = [f"rawsig-{i:03d}" for i in range(45)]
        calls = []

        def get(url, params, headers, timeout):
            calls.append(params["raw_signal_id"])
            r = mock.Mock(status_code=200)
            chunk = params["raw_signal_id"][4:-1].split(",")
            r.json.return_value = [{"raw_signal_id": c, "raw_json": json.dumps({"id": c})}
                                   for c in chunk]
            return r

        out = rd.fetch_stored_raw("https://e.supabase.co", "k", ids, get=get)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(out), 45)
        self.assertEqual(out["rawsig-000"], {"id": "rawsig-000"})

    def test_any_error_returns_none(self):
        r = mock.Mock(status_code=500)
        self.assertIsNone(rd.fetch_stored_raw("https://e.supabase.co", "k", ["rawsig-1"],
                                              get=lambda *a, **k: r))

    def test_non_https_refused(self):
        self.assertIsNone(rd.fetch_stored_raw("http://e", "k", ["rawsig-1"], get=mock.Mock()))


if __name__ == "__main__":
    unittest.main()
