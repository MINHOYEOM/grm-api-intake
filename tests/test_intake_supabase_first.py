#!/usr/bin/env python3
"""[대기열 0단계 2026-11-12] 저장 순서 뒤집기 + Notion 중복 조회 실패 시 Supabase 대체 판정.

지키는 것:
- Supabase(정본)가 Notion(주간 대기열)보다 먼저 쓰이고, Notion 실패가 Supabase 저장을 막지 않는다.
- Supabase 실패가 Notion 적재를 막지 않는다(반환 통계는 종전과 같다).
- 게시일 없는 항목은 Supabase 에 시도하지 않는다(날짜를 지어 넣지 않는다).
- Notion 조회가 죽으면 Supabase 키로 판정해 적재가 0건이 되지 않는다(09-27 사고). 판정할 수
  없는 항목(게시일 없음·URL 2차 dedup 소스·물어보지 않은 키)은 쓰지 않고 미룬다.
- Supabase 대체 판정도 안 되면 종전처럼 중단한다(fail-closed).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import collect_intake as ci
import findings_store as store
import grm_notion
from grm_health import _evaluate_health

_SUPABASE = ("https://example.supabase.co", "service-role-secret-token")
_RUN = datetime(2026, 11, 12, tzinfo=timezone.utc).date()
_AT = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)


def _item(**overrides) -> ci.IntakeItem:
    base = dict(
        source="FDA Warning Letter",
        document_id="wl-0001",
        date_iso="2026-11-10",
        headline="[WL] Example Pharma",
        official_url="https://www.fda.gov/wl/0001",
        type_or_class="Warning Letter",
        firm="Example Pharma",
        body="CGMP violations.",
        qa_relevance="Likely",
        osd_relevance="Direct",
        signal_tier="Tier 3",
        raw_payload={"firm": "Example Pharma"},
    )
    base.update(overrides)
    return ci.IntakeItem(**base)


def _ok(status="inserted"):
    return store.RawSignalAppendResult(status, raw_signal_id="rawsig-x")


def _insert(items, *, existing=None, **kw):
    return ci.insert_items("tok", "db", items, _RUN, _AT, set(existing or ()), False,
                           modality_enabled=False, **kw)


class SupabaseFirstOrderTest(unittest.TestCase):

    def test_supabase_is_written_before_notion(self):
        calls: list[str] = []
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase",
                                  side_effect=lambda *a, **k: (calls.append("supabase"), _ok())[1]), \
                mock.patch.object(ci, "notion_create_page",
                                  side_effect=lambda *a, **k: (calls.append("notion"), True)[1]):
            got = _insert([_item()], findings_supabase=_SUPABASE)
        self.assertEqual(got, (1, 0, 0))
        self.assertEqual(calls, ["supabase", "notion"])

    def test_notion_failure_keeps_supabase_copy_and_is_counted(self):
        counters = ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok()) as supa, \
                mock.patch.object(ci, "notion_create_page", return_value=False):
            got = _insert([_item()], findings_supabase=_SUPABASE, store_counters=counters)
        self.assertEqual(got, (0, 0, 1))          # failed 는 종전처럼 Notion 실패 건수
        supa.assert_called_once()                 # 종전엔 Notion 실패면 Supabase 를 부르지도 않았다
        self.assertEqual(counters["notion_failed"], 1)
        self.assertEqual(counters["notion_failed_supabase_kept"], 1)
        self.assertEqual(counters["supabase_saved"], 1)

    def test_notion_failure_after_supabase_duplicate_counts_as_kept(self):
        counters = ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok("duplicate")), \
                mock.patch.object(ci, "notion_create_page", return_value=False):
            _insert([_item()], findings_supabase=_SUPABASE, store_counters=counters)
        self.assertEqual(counters["notion_failed_supabase_kept"], 1)
        self.assertEqual(counters["supabase_duplicate"], 1)

    def test_supabase_failure_does_not_block_notion(self):
        counters = ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", side_effect=OSError("down")), \
                mock.patch.object(ci, "notion_create_page", return_value=True) as create:
            got = _insert([_item()], findings_supabase=_SUPABASE, store_counters=counters)
        self.assertEqual(got, (1, 0, 0))
        create.assert_called_once()
        self.assertEqual(counters["supabase_failed"], 1)
        self.assertEqual(counters["notion_failed"], 0)

    def test_undated_item_skips_supabase_but_reaches_notion(self):
        counters = ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase") as supa, \
                mock.patch.object(ci, "notion_create_page", return_value=True):
            got = _insert([_item(source="ICH", document_id="ich-q1", date_iso="")],
                          findings_supabase=_SUPABASE, store_counters=counters)
        self.assertEqual(got, (1, 0, 0))
        supa.assert_not_called()
        self.assertEqual(counters["supabase_skipped_undated"], 1)

    def test_findings_mode_raw_saved_but_findings_error_counts_as_saved(self):
        counters = ci._new_store_counters()
        import findings_supabase_append as fsa
        partial = fsa.SupabaseRawSignalWithFindingsAppendResult(
            "error", raw_signal_id="rawsig-x", raw_signal_status="inserted",
            errors=("findings batch POST failed",))
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_with_findings_to_supabase",
                                  return_value=partial), \
                mock.patch.object(ci, "notion_create_page", return_value=True):
            _insert([_item()], findings_supabase=_SUPABASE,
                    findings_supabase_include_findings=True, store_counters=counters)
        self.assertEqual(counters["supabase_saved"], 1)
        self.assertEqual(counters["supabase_failed"], 0)

    def test_existing_key_still_skips_both_writes(self):
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase") as supa, \
                mock.patch.object(ci, "notion_create_page") as create:
            got = _insert([_item()], existing={"FDA Warning Letter::wl-0001"},
                          findings_supabase=_SUPABASE)
        self.assertEqual(got, (0, 1, 0))
        supa.assert_not_called()
        create.assert_not_called()


class FallbackDedupInsertTest(unittest.TestCase):

    def test_unchecked_key_is_deferred_without_writes(self):
        counters = ci._new_store_counters()
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase") as supa, \
                mock.patch.object(ci, "notion_create_page") as create:
            got = _insert([_item()], findings_supabase=_SUPABASE, store_counters=counters,
                          fallback_checked=set())
        self.assertEqual(got, (0, 1, 0))
        supa.assert_not_called()
        create.assert_not_called()
        self.assertEqual(counters["fallback_deferred"], 1)

    def test_url_dedup_source_is_deferred_even_if_checked(self):
        eca = _item(source=ci.SOURCE_ECA, document_id="eca-1")
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page") as create:
            got = _insert([eca], fallback_checked={f"{ci.SOURCE_ECA}::eca-1"})
        self.assertEqual(got, (0, 1, 0))
        create.assert_not_called()

    def test_supabase_not_saved_defers_notion_in_fallback(self):
        # 재검토 0-2: 대체 모드에서는 Supabase 가 "새로 저장됨"이라고 답한 항목만 Notion 에 쓴다.
        cases = [("failed", mock.Mock(side_effect=OSError("down"))),
                 ("duplicate", mock.Mock(return_value=_ok("duplicate")))]
        for name, append in cases:
            with self.subTest(name), mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(ci, "append_intake_item_to_supabase", append), \
                    mock.patch.object(ci, "notion_create_page") as create:
                counters = ci._new_store_counters()
                got = _insert([_item()], findings_supabase=_SUPABASE, store_counters=counters,
                              fallback_checked={"FDA Warning Letter::wl-0001"})
                self.assertEqual(got, (0, 1, 0))
                create.assert_not_called()
                self.assertEqual(counters["fallback_deferred"], 1)

    def test_fallback_without_supabase_config_defers(self):
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page") as create:
            got = _insert([_item()], fallback_checked={"FDA Warning Letter::wl-0001"})
        self.assertEqual(got, (0, 1, 0))
        create.assert_not_called()

    def test_normal_mode_supabase_failure_still_writes_notion(self):
        # 평소 모드는 Supabase 실패와 무관하게 Notion 에 쓴다(대기열 우선 — 합집합 금지와 짝).
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok("duplicate")), \
                mock.patch.object(ci, "notion_create_page", return_value=True) as create:
            got = _insert([_item()], findings_supabase=_SUPABASE)
        self.assertEqual(got, (1, 0, 0))
        create.assert_called_once()

    def test_checked_new_key_is_inserted(self):
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok()), \
                mock.patch.object(ci, "notion_create_page", return_value=True) as create:
            got = _insert([_item()], findings_supabase=_SUPABASE,
                          fallback_checked={"FDA Warning Letter::wl-0001"})
        self.assertEqual(got, (1, 0, 0))
        create.assert_called_once()


class SupabaseFallbackKeysTest(unittest.TestCase):

    def test_returns_checked_and_existing_keys_and_skips_undated(self):
        dated_old = _item(document_id="wl-old")
        dated_new = _item(document_id="wl-new")
        undated = _item(source="ICH", document_id="ich-q1", date_iso="")
        rid_old = ci.raw_signal_from_intake_item(dated_old, collected_at=_AT)["raw_signal_id"]
        asked: list[list[str]] = []

        def fake_existing(url, key, ids):
            asked.append(list(ids))
            return {rid_old}

        with mock.patch.object(ci, "existing_raw_signal_ids", side_effect=fake_existing):
            got = ci.supabase_fallback_dedup_keys(*_SUPABASE, [dated_old, dated_new, undated], _AT)
        checked, existing = got
        self.assertEqual(checked, {"FDA Warning Letter::wl-old", "FDA Warning Letter::wl-new"})
        self.assertEqual(existing, {"FDA Warning Letter::wl-old"})
        self.assertEqual(len(asked[0]), 2)       # 게시일 없는 항목은 묻지 않는다

    def test_lookup_failure_returns_none(self):
        with mock.patch.object(ci, "existing_raw_signal_ids", return_value=None):
            self.assertIsNone(ci.supabase_fallback_dedup_keys(*_SUPABASE, [_item()], _AT))


class MainFallbackTest(unittest.TestCase):
    """main(): Notion 중복 조회 실패 → Supabase 대체 판정으로 적재 계속(09-27 재발 방지)."""

    def _run_main(self, health_path, *, supabase_lookup):
        stats = ci.CollectionStats()
        groups = [[] for _ in range(22)]
        groups[7] = [_item(document_id="wl-new"), _item(document_id="wl-old")]   # wl_items
        collected = (stats,) + tuple(groups)
        env = {"NOTION_TOKEN": "tok", "NOTION_DATABASE_ID": "db",
               "GRM_HEALTH_JSON": health_path,
               "ENABLE_FINDINGS_SUPABASE_APPEND": "true",
               "SUPABASE_URL": _SUPABASE[0], "SUPABASE_SERVICE_ROLE_KEY": _SUPABASE[1],
               "RAW_SIGNALS_HEAL": "false"}
        fixed_now = datetime(2026, 11, 12, 3, 17, tzinfo=ci.KST)
        err = grm_notion.NotionDedupeQueryError("Read timed out. (read timeout=30)")
        rid_old = ci.raw_signal_from_intake_item(
            _item(document_id="wl-old"), collected_at=fixed_now)["raw_signal_id"]
        lookup = (lambda url, key, ids: {rid_old}) if supabase_lookup else (lambda *a: None)
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["collect_intake.py"]), \
                mock.patch.object(ci, "now_kst", return_value=fixed_now), \
                mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_verify_modality_property", return_value=True), \
                mock.patch.object(ci, "notion_verify_handoff_ref_property", return_value=True), \
                mock.patch.object(ci, "probe_kr_egress_proxy", return_value=("unconfigured", "")), \
                mock.patch.object(ci, "_fda483_known_document_ids", return_value=None), \
                mock.patch.object(ci, "_configure_exclusion_ledger"), \
                mock.patch.object(ci, "_run_collection", return_value=collected), \
                mock.patch.object(ci, "notion_query_existing_keys", side_effect=err), \
                mock.patch.object(ci, "existing_raw_signal_ids", side_effect=lookup), \
                mock.patch.object(ci, "append_intake_item_to_supabase", return_value=_ok()), \
                mock.patch.object(ci, "notion_create_page", return_value=True) as create:
            code = ci.main()
        return code, create

    def test_fallback_inserts_only_new_and_warns(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "grm-health.json")
            code, create = self._run_main(path, supabase_lookup=True)
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        self.assertEqual(create.call_count, 1)            # wl-new 만. wl-old 는 Supabase 에 있음
        self.assertEqual(create.call_args[0][2].document_id, "wl-new")
        warn_codes = [x["code"] for x in payload["health"]["warnings"]]
        self.assertIn("notion-dedupe-fallback-supabase", warn_codes)
        fail_codes = [x["code"] for x in payload["health"]["failures"]]
        self.assertNotIn("notion-dedupe-query-failed", fail_codes)
        self.assertEqual(payload["store"]["dedup_mode"], "supabase-fallback")
        self.assertEqual(payload["store"]["supabase_saved"], 1)

    def test_fallback_lookup_failure_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "grm-health.json")
            code, create = self._run_main(path, supabase_lookup=False)
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        self.assertEqual(code, 1)
        create.assert_not_called()
        codes = [x["code"] for x in payload["health"]["failures"]]
        self.assertEqual(codes, ["notion-dedupe-query-failed"])


class HealthStoreSignalsTest(unittest.TestCase):

    def _health(self, stats):
        return _evaluate_health(
            stats=stats, active=set(), enable_search=False, enable_mfds=False,
            enable_mfds_law=False, enable_mfds_recall=False, enable_mfds_admin=False,
            enable_mfds_gmp_cert=False, enable_mfds_safety_letter=False,
            enable_mfds_gmp_inspection=False, enable_ich=False, enable_who=False,
            enable_hc=False, enable_fda483=False, enable_moleg_api=False, enable_scrape=False,
            event_name="schedule", emit_routine_handoff=False, handoff_emitted=False,
            handoff_failed=False, handoff_error_msg="")

    def test_notion_failure_detail_mentions_supabase_kept(self):
        stats = ci.CollectionStats()
        stats.wl_insert_failed = 2
        stats.store_summary = dict(ci._new_store_counters(), notion_failed=2,
                                   notion_failed_supabase_kept=2)
        h = self._health(stats).to_dict()
        fail = [x for x in h["failures"] if x["code"] == "notion-insert-failed"][0]
        self.assertIn("Supabase", fail["detail"])

    def test_supabase_failure_is_a_warning(self):
        stats = ci.CollectionStats()
        stats.store_summary = dict(ci._new_store_counters(), supabase_failed=3)
        h = self._health(stats).to_dict()
        self.assertIn("supabase-append-failed", [x["code"] for x in h["warnings"]])

    def test_default_store_summary_adds_nothing(self):
        stats = ci.CollectionStats()
        h = self._health(stats).to_dict()
        codes = [x["code"] for x in h["warnings"] + h["failures"]]
        self.assertNotIn("supabase-append-failed", codes)
        self.assertNotIn("notion-dedupe-fallback-supabase", codes)


if __name__ == "__main__":
    unittest.main()
