#!/usr/bin/env python3
"""[077] 일일 성장 보고의 데이터층 — 수집기 국가·기기 그룹 · Brevo 구독자 스냅샷 · 마이그레이션 계약.

보고 자체는 대화(예약 태스크)가 쓰지만, 숫자의 출처는 여기서 고정한다:
① RUM 수집기가 국가·기기를 **별도 요청**으로 받는다(한 쿼리에 묶으면 방문 수까지 표본으로
   깎인다 — f923f0c 의 교훈). 그룹이 늘어도 기존 3개 그룹의 쿼리는 바뀌지 않는다.
② Brevo 구독자 수는 uniqueSubscribers → totalSubscribers → contacts.count 순으로 읽고,
   지원 중단으로 0 이 와도 "전원 이탈"로 저장하지 않는다.
③ 077 마이그레이션은 072 와 같은 읽기 권한(authenticated 만)이고, 보고 함수는
   auth.users 를 읽는 security definer 라 anon/authenticated 에 실행 권한이 없다.
④ 워크플로는 새 스크립트를 부르고, 시크릿 이름은 기존 NEWSLETTER_API_KEY 를 재사용한다.
"""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_newsletter_subscribers as subs  # noqa: E402
import collect_rum_analytics as rum  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# ★파일명을 번호로 참조하되 **변수 이름은 내용으로** 짓는다 — 마이그 번호는
# 병렬 세션끼리 충돌해 옮겨 다닌다(실제로 076 이 077 로 밀렸다).
MIG_GROWTH = ROOT / "web" / "migrations" / "077_growth_daily_report.sql"
WORKFLOW = ROOT / ".github" / "workflows" / "grm-rum-analytics.yml"


def _rum_payload(rows):
    return {"data": {"viewer": {"accounts": [{"rows": rows}]}}}


def _row(day, dim, value, visits, si=1.0):
    return {"sum": {"visits": visits}, "avg": {"sampleInterval": si},
            "dimensions": {"date": day, dim: value}}


class RumCountryDeviceGroupsTest(unittest.TestCase):
    def test_new_groups_are_separate_requests_and_do_not_touch_existing_queries(self):
        """국가·기기는 각자 요청이고, 기존 그룹의 쿼리 본문은 그대로다."""
        self.assertIn("countries", rum.GROUPS)
        self.assertIn("devices", rum.GROUPS)
        q_c = rum.build_query("countries")
        q_d = rum.build_query("devices")
        self.assertIn("countryName", q_c)
        self.assertNotIn("requestPath", q_c)
        self.assertNotIn("refererHost", q_c)
        self.assertIn("deviceType", q_d)
        self.assertNotIn("countryName", q_d)
        for legacy in ("totals", "referrers", "paths"):
            q = rum.build_query(legacy)
            self.assertNotIn("countryName", q, legacy)
            self.assertNotIn("deviceType", q, legacy)
        # 표본 간격은 모든 그룹이 같이 받는다 — 없으면 추정값을 정확값으로 오인한다.
        self.assertIn("sampleInterval", q_c)
        self.assertIn("sampleInterval", q_d)

    def test_parse_countries_keeps_raw_value_and_sums_duplicates(self):
        payload = _rum_payload([
            _row("2026-09-04", "countryName", "KR", 30),
            _row("2026-09-04", "countryName", "US", 4),
            _row("2026-09-04", "countryName", "KR", 2),   # 같은 키 두 행 → 합산
            _row("2026-09-04", "countryName", "", 1),     # 빈 값 → (unknown)
            _row("2026-09-04", "countryName", "JP", 0),   # 0 방문은 버린다
        ])
        keyed, si = rum.parse_countries(payload)
        self.assertEqual(keyed[("2026-09-04", "KR")], 32)
        self.assertEqual(keyed[("2026-09-04", "US")], 4)
        self.assertEqual(keyed[("2026-09-04", "(unknown)")], 1)
        self.assertNotIn(("2026-09-04", "JP"), keyed)
        self.assertEqual(si["2026-09-04"], 1.0)
        rows = rum.cap_countries(keyed, si)
        self.assertEqual([r["country"] for r in rows], ["KR", "US", "(unknown)"])
        self.assertTrue(all(r["sample_interval"] == 1.0 for r in rows))

    def test_parse_devices_lowercases_and_records_worst_precision(self):
        payload = _rum_payload([
            _row("2026-09-04", "deviceType", "Desktop", 20, si=1.0),
            _row("2026-09-04", "deviceType", "mobile", 10, si=10.0),
        ])
        keyed, si = rum.parse_devices(payload)
        self.assertEqual(keyed[("2026-09-04", "desktop")], 20)
        self.assertEqual(keyed[("2026-09-04", "mobile")], 10)
        # 그 날 행들 중 가장 나쁜 정밀도가 그 날의 정밀도다.
        self.assertEqual(si["2026-09-04"], 10.0)
        rows = rum.cap_devices(keyed, si)
        self.assertEqual({r["device_type"]: r["sample_interval"] for r in rows},
                         {"desktop": 10.0, "mobile": 10.0})

    def test_country_cap_is_deterministic(self):
        keyed = {("2026-09-04", f"C{i:02d}"): 1 for i in range(30)}
        rows = rum.cap_countries(keyed, {}, cap=5)
        self.assertEqual([r["country"] for r in rows], ["C00", "C01", "C02", "C03", "C04"])

    def test_main_plan_writes_the_new_tables(self):
        """main() 이 새 표 두 개를 적재 계획에 넣는다(코드 계약 — 잊으면 조용히 빈 표)."""
        src = (ROOT / "collect_rum_analytics.py").read_text(encoding="utf-8")
        plan = src.split("plan = [", 1)[1].split("]", 1)[0]
        for table in ("rum_daily", "rum_referrer_daily", "rum_path_daily",
                      "rum_country_daily", "rum_device_daily"):
            self.assertIn(f'("{table}"', plan, table)


class NewsletterSubscriberSnapshotTest(unittest.TestCase):
    def test_prefers_unique_then_total(self):
        c = subs.parse_counts({"uniqueSubscribers": 8, "totalSubscribers": 9, "totalBlacklisted": 1})
        self.assertEqual((c["total_subscribers"], c["total_blacklisted"], c["source"]),
                         (8, 1, "uniqueSubscribers"))
        c = subs.parse_counts({"totalSubscribers": 9, "totalBlacklisted": 0})
        self.assertEqual((c["total_subscribers"], c["source"]), (9, "totalSubscribers"))

    def test_deprecated_zero_is_not_stored_as_zero_when_contacts_exist(self):
        """Brevo 가 지원 중단으로 0 을 주면 contacts.count 로 교차 확인한다."""
        c = subs.parse_counts({"uniqueSubscribers": 0, "totalSubscribers": 0}, contacts_count=8)
        self.assertEqual((c["total_subscribers"], c["source"]), (8, "contacts.count"))
        # 진짜 빈 리스트(교차 확인도 0)는 0 이다.
        c = subs.parse_counts({"uniqueSubscribers": 0, "totalSubscribers": 0}, contacts_count=0)
        self.assertEqual(c["total_subscribers"], 0)

    def test_missing_every_count_field_fails_loudly(self):
        with self.assertRaises(SystemExit):
            subs.parse_counts({"id": 3, "name": "GRM"})

    def test_row_shape_matches_migration_columns(self):
        row = subs.build_row(subs.parse_counts({"uniqueSubscribers": 8, "totalBlacklisted": 1}),
                             list_id=3, snap_date="2026-09-05")
        self.assertEqual(set(row), {"snap_date", "list_id", "total_subscribers",
                                    "total_blacklisted", "unique_subscribers"})
        mig = MIG_GROWTH.read_text(encoding="utf-8")
        table = mig.split("create table if not exists public.newsletter_subscribers_daily", 1)[1].split(");", 1)[0]
        for col in row:
            self.assertIn(col, table, col)

    def test_no_api_key_is_a_clean_skip(self):
        with patch.dict(os.environ, {"NEWSLETTER_API_KEY": ""}):
            self.assertEqual(subs.main(["--list-id", "3", "--dry-run"]), 0)

    def test_dry_run_reads_but_does_not_write(self):
        with patch.dict(os.environ, {"NEWSLETTER_API_KEY": "k"}), \
             patch.object(subs, "fetch_list", return_value={"uniqueSubscribers": 8, "totalBlacklisted": 1}), \
             patch.object(subs, "upsert_row", side_effect=AssertionError("dry-run 인데 적재했다")):
            self.assertEqual(subs.main(["--list-id", "3", "--dry-run", "--as-of", "2026-09-05"]), 0)


class Migration077ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mig = MIG_GROWTH.read_text(encoding="utf-8")

    def test_tables_follow_072_read_rules(self):
        for table in ("rum_country_daily", "rum_device_daily", "newsletter_subscribers_daily"):
            self.assertIn(f"create table if not exists public.{table}", self.mig, table)
            self.assertIn(f"alter table public.{table} enable row level security", self.mig, table)
            self.assertIn(f"grant select on public.{table} to authenticated", self.mig, table)
            grants = re.findall(rf"grant\s+\w+\s+on public\.{table} to ([a-z_]+)", self.mig)
            self.assertEqual(grants, ["authenticated"], f"{table} 에 다른 역할 grant: {grants}")

    def test_report_function_is_operator_only(self):
        """auth.users 를 세는 함수 — 익명·일반 회원이 실행할 수 있으면 회원 수가 샌다."""
        fn = self.mig.split("create or replace function public.growth_daily_report", 1)[1]
        self.assertIn("security definer", fn)
        self.assertIn("set search_path = public, pg_temp", fn)
        self.assertIn("revoke all on function public.growth_daily_report(date) from public, anon, authenticated", fn)
        self.assertIn("grant execute on function public.growth_daily_report(date) to service_role", fn)

    def test_zone_rules_are_specific_before_general(self):
        """admin.js RUM_ZONES 와 같은 원칙 — 일반 규칙 `^/findings/` 이 먼저 오면 하위 구역이 삼켜진다."""
        fn = self.mig.split("create or replace function public.growth_daily_report", 1)[1]
        order = [m.group(1) for m in re.finditer(r"when base_path ~ '\^/([^']+)' then", fn)]
        general = order.index("findings/")
        for specific in ("findings/firm/", "findings/inspector/", "findings/docs?/", "findings/trends/", "findings/clause/"):
            self.assertLess(order.index(specific), general, specific)

    def test_referrer_rules_put_ai_before_google(self):
        """gemini.google.com 은 구글 규칙에도 걸린다 — AI 규칙이 먼저여야 한다."""
        fn = self.mig.split("create or replace function public.growth_daily_report", 1)[1]
        self.assertLess(fn.index("then 'ai'"), fn.index("then 'google'"))

    def test_precision_is_carried_per_block(self):
        """표본 간격을 값마다 같이 낸다 — 추정값을 정확값처럼 읽지 않게."""
        fn = self.mig.split("create or replace function public.growth_daily_report", 1)[1]
        self.assertGreaterEqual(fn.count("sample_interval"), 8)
        self.assertIn("'precision_basis'", fn)


class WorkflowWiringTest(unittest.TestCase):
    def test_subscriber_snapshot_step_reuses_existing_secret(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("collect_newsletter_subscribers.py", wf)
        self.assertIn("secrets.NEWSLETTER_API_KEY", wf)
        self.assertIn("vars.GRM_NEWSLETTER_LIST_ID", wf)
        # probe(구조 확인) 실행에서는 적재하지 않는다.
        step = wf.split("collect_newsletter_subscribers.py", 1)[0].rsplit("- name:", 1)[1]
        self.assertIn("probe", step)
        # GHA 표현식에는 산술이 없다(기존 계약 유지).
        self.assertNotRegex(wf, r"\$\{\{[^}]*[0-9]\s*[-+*/]\s*[0-9][^}]*\}\}")


if __name__ == "__main__":
    unittest.main()
