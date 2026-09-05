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
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
BASH = shutil.which("bash")


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
        fn = self.mig.split("create or replace function public.grm_zone_of", 1)[1].split("$$;", 1)[0]
        order = [m.group(1) for m in re.finditer(r"when base ~ '\^/([^']+)' then", fn)]
        general = order.index("findings/")
        for specific in ("findings/firm/", "findings/inspector/", "findings/docs?/", "findings/trends/", "findings/clause/"):
            self.assertLess(order.index(specific), general, specific)

    def test_referrer_rules_put_ai_before_google(self):
        """gemini.google.com 은 구글 규칙에도 걸린다 — AI 규칙이 먼저여야 한다."""
        fn = self.mig.split("create or replace function public.growth_daily_report", 1)[1]
        self.assertLess(fn.index("then 'ai'"), fn.index("then 'google'"))

    def test_daily_visits_are_cross_checked_across_three_tables(self):
        """★★★헤드라인 하나만 읽으면 3배 틀린 날이 있다.

        실측 2026-09-02: 총합표 10(간격 12.1) vs 국가 합 29(1.16)·기기 합 29(1.18).

        ★원인은 "차원이 다르면 다르게 센다"가 **아니다**(그렇게 적었다가 정정했다).
        표본 여부는 **실행 단위**로 정해지고 한 실행 안에서는 다섯 표가 같은 체제에
        있다 — 10배 차이는 한 실행 안에서 생길 수 없다. 갈렸다면 **서로 다른 실행에서
        굳은 것**이고, 그 일이 생기는 이유는 **정밀도 래칫이 표마다 독립**이기 때문이다
        (의도된 설계 — 방문은 전수인데 경로만 표본인 경우가 있다. 이건 그 부작용).

        확정 근거(00:56:57 UTC 실행 로그): 다섯 표 모두 간격 1.0~1.36 을 받았는데
        `rum_daily`·referrer·path 는 **건너뜀 7일**(당시 NULL 을 1.0 으로 읽던 버그가
        "저장값이 더 정확"으로 막았다), 신규 country·device 는 **8일 전부** 적재됐다.

        래칫이 표별 독립인 한 또 생길 수 있으므로, "어느 수를 믿을지"를 보고 작성자의
        판단에 맡기지 않고 함수가 결정론으로 고른다.
        """
        fn = self.mig.split("create or replace function public.growth_daily_report", 1)[1]
        block = fn.split("into v_day", 2)[1]
        # 세 출처를 모두 재고,
        for src in ("'totals'", "'countries'", "'devices'"):
            self.assertIn(src, block, src)
        for table in ("rum_daily", "rum_country_daily", "rum_device_daily"):
            self.assertIn(table, block, table)
        # 가장 정밀한 것을 고르며(작은 sample_interval 우선),
        self.assertIn("order by coalesce(si, 'infinity'::numeric), pref", block,
                      "정밀도 순 정렬이 없다 — 아무거나 고르게 된다")
        # ★NULL=미상은 무한히 부정확으로 읽는다(수집기 stored_precision 과 같은 규칙).
        # 1.0 으로 읽으면 정밀도를 모르는 값이 항상 이겨 가드가 뒤집힌다.
        self.assertNotIn("coalesce(si, 1", block)
        # 무엇을 골랐는지·다른 방법은 뭐라 했는지 함께 낸다(사람이 검증할 수 있게).
        for key in ("'best_visits'", "'best_visits_source'",
                    "'best_visits_sample_interval'", "'measurements'"):
            self.assertIn(key, block, key)
        # 그리고 보고가 헤드라인 대신 이걸 쓰도록 근거 문구를 함께 낸다.
        self.assertIn("'best_visits_basis'", fn)

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


class ResolveWindowStepTest(unittest.TestCase):
    """★워크플로의 창 계산을 **실행해서** 검증한다 — YAML 문자열 대조가 아니라.

    이 스텝의 위험은 문법이 아니라 **날짜 산술**이다. `--end` 를 열어 준 이유가
    "짧은 창을 과거로 밀어 표본 구간을 회수한다"인데, 끝에서 거꾸로 세는 계산이
    하루라도 어긋나면 회수 대상 날이 창 밖으로 나가고 **아무 오류 없이** 엉뚱한
    날을 다시 덮는다. 그래서 셸을 그대로 떼어 돌리고 산출값을 본다.

    PyYAML 은 쓰지 않는다 — CI 테스트 환경에 없다(test_workflow_flag_resolution.py
    가 같은 이유로 표준 라이브러리 파싱을 쓴다).
    """

    @staticmethod
    def _run_block(step_name: str) -> str:
        """지정한 스텝의 `run: |` 본문을 들여쓰기로 잘라 온다."""
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        i = next(n for n, ln in enumerate(lines)
                 if ln.strip() == f"- name: {step_name}")
        j = next(n for n in range(i + 1, len(lines))
                 if lines[n].strip() == "run: |")
        body_indent = len(lines[j]) - len(lines[j].lstrip()) + 2
        out = []
        for ln in lines[j + 1:]:
            if ln.strip() and len(ln) - len(ln.lstrip()) < body_indent:
                break
            out.append(ln[body_indent:] if ln.strip() else "")
        return "\n".join(out)

    def _resolve(self, days, end):
        """스텝을 돌려 (rc, {start, end}) 를 준다."""
        script = self._run_block("Resolve window")
        with tempfile.TemporaryDirectory() as tmp:
            sh = os.path.join(tmp, "step.sh")
            out = os.path.join(tmp, "gh_output")
            with open(sh, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(script)
            open(out, "w").close()
            env = dict(os.environ, IN_DAYS=days, IN_END=end, GITHUB_OUTPUT=out)
            # 한국어 오류문을 읽는다 — 로케일 기본(cp949)으로는 디코드가 터진다.
            proc = subprocess.run([BASH, sh], env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
            with open(out, encoding="utf-8") as fh:
                got = dict(ln.split("=", 1)
                           for ln in fh.read().splitlines() if "=" in ln)
        return proc.returncode, got

    def test_end_is_declared_as_an_optional_dispatch_input(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        block = wf.split("workflow_dispatch:", 1)[1].split("\npermissions:", 1)[0]
        lines = block.splitlines()
        i = next((n for n, ln in enumerate(lines) if ln.strip() == "end:"), None)
        self.assertIsNotNone(i, "창 끝을 옮길 입력이 없다 — 과거 구간에 영원히 못 닿는다")
        # 기본값이 비어 있어야 예약 실행(입력 없음)이 지금까지처럼 "지금"으로 끝난다.
        indent = len(lines[i]) - len(lines[i].lstrip())
        body = []
        for ln in lines[i + 1:]:
            if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
                break
            body.append(ln)
        decl = "\n".join(body)
        self.assertRegex(decl, r"default:\s*''")
        self.assertRegex(decl, r"required:\s*false")

    @unittest.skipIf(BASH is None, "bash 없음")
    def test_empty_end_still_means_now(self):
        """예약 실행 경로는 바뀌지 않는다 — 입력을 안 주면 오늘까지."""
        rc, got = self._resolve("8", "")
        self.assertEqual(rc, 0)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertTrue(got["end"].startswith(today), got)
        self.assertRegex(got["end"], r"T\d{2}:00:00Z$")

    @unittest.skipIf(BASH is None, "bash 없음")
    def test_start_is_counted_back_from_the_given_end(self):
        """★회수의 핵심 — 끝을 과거로 옮기면 시작도 같이 옮겨간다.

        8/24~8/28 이 표본으로 굳은 구간이다. 끝 8/29 자정 · 5일이면 그 다섯 날이
        정확히 들어오고, 창 길이는 전수 계층(7일 이하)에 남는다.
        """
        rc, got = self._resolve("5", "2026-08-29T00:00:00Z")
        self.assertEqual(rc, 0)
        self.assertEqual(got["start"], "2026-08-24T00:00:00Z")
        self.assertEqual(got["end"], "2026-08-29T00:00:00Z")

    @unittest.skipIf(BASH is None, "bash 없음")
    def test_month_boundary_does_not_shift_the_window(self):
        """달을 넘는 뺄셈을 손으로 하지 않는다는 증명(9/2 에서 5일 전 = 8/28)."""
        rc, got = self._resolve("5", "2026-09-02T00:00:00Z")
        self.assertEqual(rc, 0)
        self.assertEqual(got["start"], "2026-08-28T00:00:00Z")

    @unittest.skipIf(BASH is None, "bash 없음")
    def test_malformed_inputs_stop_the_run_instead_of_shifting_the_window(self):
        """★조용히 엉뚱한 창을 도는 것이 실패보다 나쁘다 — 그 실행이 정확한 날을
        표본으로 덮을 수 있다. 형식이 어긋나면 적재 전에 멈춘다."""
        for days, end in [("8", "2026-08-29"),        # 시각 없음
                          ("8", "8/29"),              # 다른 표기
                          ("8", "2026-08-29T00:00:00"),  # Z 없음
                          ("abc", ""),                # 일수가 수가 아님
                          ("0", "")]:                 # 빈 창
            with self.subTest(days=days, end=end):
                rc, got = self._resolve(days, end)
                self.assertNotEqual(rc, 0, f"통과하면 안 된다: days={days} end={end}")
                self.assertEqual(got, {}, "실패했는데 창을 뱉었다")

    def test_inputs_are_passed_through_env_not_expanded_into_the_shell(self):
        """dispatch 입력을 run 본문에 직접 펼치면 입력이 셸 코드가 된다."""
        script = self._run_block("Resolve window")
        self.assertNotIn("${{", script)
        self.assertIn("$IN_END", script)

if __name__ == "__main__":
    unittest.main()
