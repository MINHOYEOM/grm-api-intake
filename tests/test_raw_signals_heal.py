"""Supabase raw_signals 사본 누락 수리 (2026-11-08 수집 완전성 ③-3).

- 수집기: Notion 중복으로 건너뛴 항목 중 사본에 없는 것만 채운다(heal_supabase_copies).
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import collect_intake as ci  # noqa: E402
import findings_store  # noqa: E402
import findings_supabase_append as fsa  # noqa: E402

URL = "https://example.supabase.co"
KEY = "service-key"


def _item(doc_id: str, source: str = ci.SOURCE_ICH) -> ci.IntakeItem:
    return ci.IntakeItem(source=source, document_id=doc_id, date_iso="2026-11-01",
                         headline=f"Report {doc_id}", official_url=f"https://who.example/{doc_id}",
                         type_or_class="WHOPIR", raw_payload={"id": doc_id})


def _rid(item: ci.IntakeItem) -> str:
    return findings_store.raw_signal_from_intake_item(item)["raw_signal_id"]


def _resp(status: int, data=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data if data is not None else []
    return r


class ExistingRawSignalIdsTest(unittest.TestCase):
    def test_chunks_and_collects(self):
        ids = [f"rawsig-{i:024d}" for i in range(170)]
        calls = []

        def _get(url, params, headers, timeout):
            calls.append(params)
            return _resp(200, [{"raw_signal_id": ids[0]}] if len(calls) == 1 else [])

        with patch.object(fsa.requests, "get", side_effect=_get):
            got = fsa.existing_raw_signal_ids(URL, KEY, ids)
        self.assertEqual(got, {ids[0]})
        self.assertEqual(len(calls), 3)   # 80 + 80 + 10
        self.assertTrue(calls[0]["raw_signal_id"].startswith("in.("))

    def test_any_failure_returns_none_not_partial(self):
        with patch.object(fsa.requests, "get", side_effect=[_resp(200, []), _resp(401)]):
            self.assertIsNone(fsa.existing_raw_signal_ids(URL, KEY, [f"r{i}" for i in range(100)]))
        with patch.object(fsa.requests, "get", side_effect=requests.exceptions.ConnectionError()):
            self.assertIsNone(fsa.existing_raw_signal_ids(URL, KEY, ["r1"]))

    def test_5xx_retried_once(self):
        with patch.object(fsa.requests, "get", side_effect=[_resp(503), _resp(200, [])]) as g:
            self.assertEqual(fsa.existing_raw_signal_ids(URL, KEY, ["r1"]), set())
        self.assertEqual(g.call_count, 2)


class InsertItemsHealCandidatesTest(unittest.TestCase):
    def test_skipped_duplicates_are_collected_only_when_asked(self):
        a, b = _item("a"), _item("b")
        existing = {f"{ci.SOURCE_ICH}::a"}
        cands: list = []
        ins, sk, _f = ci.insert_items([a, b], ci.now_kst(),
                                      set(existing), True, heal_candidates=cands)
        self.assertEqual((ins, sk), (1, 1))
        self.assertEqual([i.document_id for i in cands], ["a"])
        # 미지정이면 종전과 같다(예외 없음·반환 동일)
        self.assertEqual(ci.insert_items([a, b], ci.now_kst(),
                                         set(existing), True)[:2], (1, 1))


class HealSupabaseCopiesTest(unittest.TestCase):
    def test_only_missing_rows_are_appended(self):
        a, b, c = _item("a"), _item("b"), _item("c")
        ok = fsa.SupabaseRawSignalAppendResult("inserted")
        with patch.object(ci, "existing_raw_signal_ids", return_value={_rid(a)}), \
                patch.object(ci, "append_intake_item_to_supabase", return_value=ok) as app:
            s = ci.heal_supabase_copies(URL, KEY, [a, b, c, a], ci.now_kst())
        self.assertEqual({x.args[2].document_id for x in app.call_args_list}, {"b", "c"})
        self.assertEqual((s["candidates"], s["present"], s["missing"], s["healed"]), (3, 1, 2, 2))

    def test_lookup_failure_writes_nothing(self):
        with patch.object(ci, "existing_raw_signal_ids", return_value=None), \
                patch.object(ci, "append_intake_item_to_supabase") as app:
            s = ci.heal_supabase_copies(URL, KEY, [_item("a")], ci.now_kst())
        app.assert_not_called()
        self.assertEqual(s["lookup_failed"], 1)

    def test_cap_defers_the_rest(self):
        items = [_item(str(i)) for i in range(5)]
        ok = fsa.SupabaseRawSignalAppendResult("inserted")
        with patch.object(ci, "existing_raw_signal_ids", return_value=set()), \
                patch.object(ci, "append_intake_item_to_supabase", return_value=ok) as app:
            s = ci.heal_supabase_copies(URL, KEY, items, ci.now_kst(), max_heal=2)
        self.assertEqual(app.call_count, 2)
        self.assertEqual(s["deferred"], 3)

    def test_findings_mode_uses_findings_append_and_failures_counted(self):
        bad = fsa.SupabaseRawSignalWithFindingsAppendResult("error", errors=("http_500",))
        with patch.object(ci, "existing_raw_signal_ids", return_value=set()), \
                patch.object(ci, "append_intake_item_with_findings_to_supabase",
                             return_value=bad) as app:
            s = ci.heal_supabase_copies(URL, KEY, [_item("a")], ci.now_kst(),
                                        include_findings=True)
        app.assert_called_once()
        self.assertEqual((s["healed"], s["failed"]), (0, 1))

    def test_record_matches_collector_record_for_same_item(self):
        """보충 경로는 수집기 append 와 같은 레코드 빌더를 쓴다 — raw_signal_id 가 같아야 중복이 없다."""
        a = _item("a")
        with patch.object(ci, "existing_raw_signal_ids", return_value=set()) as ex, \
                patch.object(ci, "append_intake_item_to_supabase",
                             return_value=fsa.SupabaseRawSignalAppendResult("inserted")):
            ci.heal_supabase_copies(URL, KEY, [a], ci.now_kst())
        self.assertEqual(ex.call_args.args[2], [_rid(a)])


    def test_bare_whopir_is_left_to_backfill(self):
        """WHO 재수집분(PDF 보강 전 bare raw)은 사본에 넣지 않는다 — Notion 보강본을 가리지 않게."""
        who = _item("w", source=ci.SOURCE_WHO)
        with patch("collect_who.whopir_raw_is_bare", return_value=True),                 patch.object(ci, "existing_raw_signal_ids", return_value=set()) as ex,                 patch.object(ci, "append_intake_item_to_supabase") as app:
            s = ci.heal_supabase_copies(URL, KEY, [who], ci.now_kst())
        ex.assert_not_called()
        app.assert_not_called()
        self.assertEqual(s["skipped_bare"], 1)


class HealUndatedTest(unittest.TestCase):
    def test_undated_item_is_healed_too(self):
        """migration 147(2026-11-12) 이후 게시일 없는 항목(ICH 토픽 스냅샷)도 채운다(published_date null)."""
        undated = _item("u")
        undated.date_iso = ""
        dated = _item("d")
        ok = fsa.SupabaseRawSignalAppendResult("inserted")
        with patch.object(ci, "existing_raw_signal_ids", return_value=set()), \
                patch.object(ci, "append_intake_item_to_supabase", return_value=ok) as app:
            s = ci.heal_supabase_copies(URL, KEY, [undated, dated], ci.now_kst())
        self.assertEqual(sorted(x.args[2].document_id for x in app.call_args_list), ["d", "u"])
        self.assertEqual((s["candidates"], s.get("skipped_undated", 0)), (2, 0))


if __name__ == "__main__":
    unittest.main()
