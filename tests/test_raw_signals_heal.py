"""Supabase raw_signals 사본 누락 수리 (2026-11-08 수집 완전성 ③-3).

- 수집기: Notion 중복으로 건너뛴 항목 중 사본에 없는 것만 채운다(heal_supabase_copies).
- 1회성 백필: backfill_raw_signals_from_notion — 기본 dry-run, --apply 일 때만 쓴다.
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

import backfill_raw_signals_from_notion as bf  # noqa: E402
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
        ins, sk, _f = ci.insert_items("t", "db", [a, b], date(2026, 11, 8), ci.now_kst(),
                                      set(existing), True, heal_candidates=cands)
        self.assertEqual((ins, sk), (1, 1))
        self.assertEqual([i.document_id for i in cands], ["a"])
        # 미지정이면 종전과 같다(예외 없음·반환 동일)
        self.assertEqual(ci.insert_items("t", "db", [a, b], date(2026, 11, 8), ci.now_kst(),
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


def _snap(doc_id: str, source: str = "WHO", page_id: str = "") -> dict:
    return {"page_id": page_id or f"pg-{doc_id}", "source": source, "document_id": doc_id,
            "date": "2026-06-01", "headline": f"Report {doc_id}", "official_url": "https://x",
            "type_or_class": "WHOPIR", "collected_at": "2026-06-02T03:17:00+09:00"}


class BackfillFromNotionTest(unittest.TestCase):
    def _run(self, *, apply, snaps, have, raws, post=None, **kw):
        post = post or MagicMock(return_value=fsa.SupabaseRawSignalAppendResult("inserted"))
        with patch.object(bf, "notion_rows_for_source", return_value=snaps), \
                patch.object(bf, "supabase_doc_ids", return_value=have), \
                patch.object(bf.fsa, "_append_raw_signal", post):
            rep = bf.run(["WHO"], notion_token="t", notion_db="d", supabase_url=URL,
                         service_key=KEY, apply=apply, fetch_raw=lambda _t, pid: raws.get(pid),
                         sleep_s=0, **kw)
        return rep, post

    def test_dry_run_writes_nothing(self):
        rep, post = self._run(apply=False, snaps=[_snap("a"), _snap("b")], have={"a"},
                              raws={"pg-b": {"id": "b"}})
        post.assert_not_called()
        self.assertEqual(rep.missing, {"WHO": 1})
        self.assertEqual(rep.inserted, 0)

    def test_apply_writes_only_missing_and_dedups_notion_twins(self):
        snaps = [_snap("a"), _snap("b"), _snap("b", page_id="pg-b2"), _snap("c")]
        rep, post = self._run(apply=True, snaps=snaps, have={"a"},
                              raws={"pg-b": {"id": "b"}, "pg-c": {"id": "c"}})
        self.assertEqual(post.call_count, 2)
        self.assertEqual({c.args[2]["document_id"] for c in post.call_args_list}, {"b", "c"})
        self.assertEqual(rep.inserted, 2)

    def test_raw_unavailable_skipped_by_default(self):
        rep, post = self._run(apply=True, snaps=[_snap("b")], have=set(), raws={})
        post.assert_not_called()
        self.assertEqual(rep.raw_unavailable, ["WHO::b"])
        rep, post = self._run(apply=True, snaps=[_snap("b")], have=set(), raws={},
                              allow_empty_raw=True)
        self.assertEqual(post.call_args.args[2]["extraction_status"],
                         "notion_backfill_raw_unavailable")

    def test_record_identity_matches_collector(self):
        """같은 source·document_id·raw 면 수집기 레코드와 raw_signal_id·raw_sha256 이 같다."""
        item = ci.IntakeItem(source="WHO", document_id="b", date_iso="2026-06-01",
                             headline="Report b", official_url="https://x",
                             type_or_class="WHOPIR", raw_payload={"id": "b"})
        coll = findings_store.raw_signal_from_intake_item(item)
        rec = bf.record_from_snapshot(_snap("b"), {"id": "b"})
        self.assertEqual(rec["raw_signal_id"], coll["raw_signal_id"])
        self.assertEqual(rec["raw_sha256"], coll["raw_sha256"])
        self.assertEqual(list(json.loads(rec["row_json"])), list(json.loads(coll["row_json"])))

    def test_brave_empty_source_kind_is_filled_and_valid(self):
        """11-12 대표 결정 '채워서 옮기기' — Brave 행은 source_kind 만 search-result 로 채운다."""
        snap = dict(_snap("s1", source="Brave Search"), type_or_class="")
        rec = bf.record_from_snapshot(snap, {"title": "x"})
        self.assertEqual(rec["source_kind"], "search-result")
        self.assertEqual(bf.gf.validate_raw_signal(rec), [])
        self.assertEqual(json.loads(rec["row_json"])["type_or_class"], "")   # Notion 원값은 그대로

    def test_other_sources_empty_source_kind_still_invalid(self):
        snap = dict(_snap("w1"), type_or_class="")
        rec = bf.record_from_snapshot(snap, {"id": "w1"})
        self.assertEqual(rec["source_kind"], "")
        self.assertIn("raw_signals.source_kind required", bf.gf.validate_raw_signal(rec))

    def test_brave_existing_source_kind_is_kept(self):
        snap = dict(_snap("s2", source="Brave Search"), type_or_class="Notice")
        self.assertEqual(bf.record_from_snapshot(snap, {})["source_kind"], "Notice")

    def test_failed_post_gives_exit_1(self):
        bad = MagicMock(return_value=fsa.SupabaseRawSignalAppendResult("error", errors=("x",)))
        rep, _ = self._run(apply=True, snaps=[_snap("b")], have=set(),
                           raws={"pg-b": {"id": "b"}}, post=bad)
        self.assertEqual(rep.failed, 1)

    def test_main_requires_credentials(self):
        with patch.dict("os.environ", {"NOTION_TOKEN": "", "NOTION_DATABASE_ID": "",
                                       "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": ""}):
            self.assertEqual(bf.main(["--source", "WHO"]), 2)



# ── WHOPIR 실사일 결손: 원천 목록에서 읽어 채움 ──────────────────────────────────
import hashlib  # noqa: E402
from urllib.parse import urljoin  # noqa: E402

import collect_who as cw  # noqa: E402

_PDF_A = "/prequal/sites/default/files/whopir_files/A-WHOPIR.pdf"
_PDF_B = "/prequal/sites/default/files/whopir_files/B-WHOPIR.pdf"


def _wid(href: str) -> str:
    return "who-whopir-" + hashlib.sha1(urljoin(cw.WHOPIR_MED_URL, href).encode()).hexdigest()[:12]


def _row(href: str, iso: str) -> str:
    return (f'<article><a href="{href}"><span>Firm</span></a>'
            '<div class="field field--name-field-whopir-inspection-dates field__item">'
            f'<time datetime="{iso}T12:00:00Z">x</time></div></article>')


_LIST_P0 = "<html>" + _row(_PDF_A, "2024-03-18") + _row(_PDF_B, "2023-05-02") + "</html>"


def _fake_html(pages: dict[int, str], calls: list[str] | None = None):
    def get(url: str) -> str:
        if calls is not None:
            calls.append(url)
        page = int(url.rsplit("page=", 1)[1]) if "page=" in url else 0
        if page not in pages:
            raise RuntimeError("down")
        return pages[page]
    return get


def _wsnap(href: str | None, doc_id: str, date_: str = "", source: str = "WHO") -> dict:
    s = _snap(doc_id, source=source)
    s["date"] = date_
    s["official_url"] = urljoin(cw.WHOPIR_MED_URL, href) if href else ""
    return s


class WhopirDateFillTest(unittest.TestCase):
    def test_fills_by_official_url_and_by_document_id(self):
        a = _wsnap(_PDF_A, _wid(_PDF_A))
        b = _wsnap(None, _wid(_PDF_B))               # official_url 없음 → document_id 로 역조회
        filled, unresolved = bf.fill_whopir_dates([a, b], _fake_html({0: _LIST_P0, 1: ""}))
        self.assertEqual((filled, unresolved), (2, []))
        self.assertEqual((a["date"], b["date"]), ("2024-03-18", "2023-05-02"))

    def test_unknown_row_stays_blank_and_is_reported(self):
        c = _wsnap("/prequal/x/C-WHOPIR.pdf", "who-whopir-ffffffffffff")
        filled, unresolved = bf.fill_whopir_dates([c], _fake_html({0: _LIST_P0}))
        self.assertEqual((filled, unresolved, c["date"]), (0, ["who-whopir-ffffffffffff"], ""))

    def test_other_source_and_already_dated_rows_untouched_and_no_fetch(self):
        calls: list[str] = []
        ich = _wsnap(_PDF_A, _wid(_PDF_A), source="ICH")
        dated = _wsnap(_PDF_B, _wid(_PDF_B), date_="2020-01-01")
        other = _wsnap(None, "WHO-RSS-1")             # who-whopir- 접두 아님
        out = bf.fill_whopir_dates([ich, dated, other], _fake_html({0: _LIST_P0}, calls))
        self.assertEqual(out, (0, []))
        self.assertEqual((ich["date"], dated["date"], other["date"]), ("", "2020-01-01", ""))
        self.assertEqual(calls, [])

    def test_pagination_stops_when_no_new_rows(self):
        calls: list[str] = []
        bf.fill_whopir_dates([_wsnap(_PDF_A, _wid(_PDF_A))],
                             _fake_html({0: _LIST_P0, 1: _LIST_P0}, calls))
        self.assertEqual(len(calls), 2)               # page=1 이 새 행 없음 → 멈춤

    def test_network_failure_inserts_with_null_date_not_invented(self):
        """원천 목록을 못 읽어 실사일을 못 찾으면 — migration 147 이후 — 날짜를 지어 넣지 않고 null 로 넣는다."""
        snap = _wsnap(_PDF_A, _wid(_PDF_A))
        snap["type_or_class"] = "who-inspection"
        post = MagicMock(return_value=fsa.SupabaseRawSignalAppendResult("inserted"))
        with patch.object(bf, "notion_rows_for_source", return_value=[snap]), \
                patch.object(bf, "supabase_doc_ids", return_value=set()), \
                patch.object(bf.fsa, "_append_raw_signal", post):
            rep = bf.run(["WHO"], notion_token="t", notion_db="d", supabase_url=URL,
                         service_key=KEY, apply=True, fetch_raw=lambda _t, _p: {"id": 1},
                         sleep_s=0, get_html=_fake_html({}))
        self.assertEqual((rep.date_filled_from_source, rep.date_unresolved),
                         (0, [snap["document_id"]]))
        self.assertEqual(rep.invalid, [])
        post.assert_called_once()
        self.assertIsNone(post.call_args.args[2]["published_date"])

    def test_run_fills_then_inserts(self):
        snap = _wsnap(_PDF_A, _wid(_PDF_A))
        post = MagicMock(return_value=fsa.SupabaseRawSignalAppendResult("inserted"))
        with patch.object(bf, "notion_rows_for_source", return_value=[snap]), \
                patch.object(bf, "supabase_doc_ids", return_value=set()), \
                patch.object(bf.fsa, "_append_raw_signal", post):
            rep = bf.run(["WHO"], notion_token="t", notion_db="d", supabase_url=URL,
                         service_key=KEY, apply=True, fetch_raw=lambda _t, _p: {"id": 1},
                         sleep_s=0, get_html=_fake_html({0: _LIST_P0}))
        self.assertEqual((rep.date_filled_from_source, rep.inserted, rep.invalid), (1, 1, []))
        self.assertEqual(post.call_args.args[2]["published_date"], "2024-03-18")


if __name__ == "__main__":
    unittest.main()
