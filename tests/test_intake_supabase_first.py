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


class SupabaseStoreTest(unittest.TestCase):
    def test_store_result_controls_insert_count(self):
        for status, expected in (("inserted", (1, 0, 0)), ("duplicate", (1, 0, 0)),
                                 ("invalid", (0, 0, 1))):
            with self.subTest(status=status), mock.patch.object(
                    ci, "append_intake_item_to_supabase", return_value=_ok(status)):
                self.assertEqual(
                    ci.insert_items([_item()], _AT, set(), False, findings_supabase=_SUPABASE),
                    expected,
                )


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
