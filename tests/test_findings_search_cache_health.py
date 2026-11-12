"""[151 · 2026-11-12] 검색 캐시 갱신 나눠 커밋 + 전용 감시.

  · 151 migration: 공개 RPC·086 갱신 함수 시그니처 불변, 한 행 함수가 단일 원천, 배치는 오래된 행부터,
    새 함수 셋 전부 anon·authenticated·PUBLIC 회수(job_status 는 service_role 만), daily 잡 4배치 + 10분 상한.
  · findings_search_cache_health.evaluate: 11-12 실패 모양(최근 실패·성공 없음·신선 0)을 잡고 정상은 통과.
  · 워크플로 라벨이 운영 센터 경보 표(web/ops_alarm_sync.LABEL_TEAM)에 있다.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import findings_search_cache_health as h  # noqa: E402
import ops_alarm_sync as al  # noqa: E402

MIG = (ROOT / "web" / "migrations" / "151_findings_search_cache_batched_refresh.sql").read_text(encoding="utf-8")
CODE = re.sub(r"--.*$", "", MIG, flags=re.M)
WF = (ROOT / ".github" / "workflows" / "grm-findings-search-cache-health.yml").read_text(encoding="utf-8")


class MigrationContractTest(unittest.TestCase):
    def test_public_rpc_untouched(self):
        self.assertNotIn("function public.findings_search(", CODE)
        self.assertNotIn("findings_search_compute(\n  p_q", CODE)   # 원 계산 본문을 다시 정의하지 않는다

    def test_refresh_keeps_signature_and_delegates_to_row(self):
        self.assertIn("create or replace function public.findings_search_cache_refresh(p_tier text default 'daily')\nreturns jsonb", CODE)
        body = CODE[CODE.index("function public.findings_search_cache_refresh(p_tier"):]
        body = body[:body.index("$$;")]
        self.assertIn("public.findings_search_cache_refresh_row(r.args)", body)
        self.assertNotIn("findings_search_compute(", body, "몸통 복제 — 한 행 함수만 계산해야 한다")
        self.assertEqual(CODE.count("v := public.findings_search_compute("), 1, "원 계산 호출은 한 행 함수 한 곳뿐")

    def test_batch_takes_stalest_first_and_clamps(self):
        body = CODE[CODE.index("function public.findings_search_cache_refresh_batch("):]
        body = body[:body.index("$$;")]
        self.assertIn("order by c.refreshed_at asc nulls first, c.args", body)
        self.assertIn("limit lim", body)
        self.assertIn("greatest(1, least(coalesce(p_limit, 60), 500))", body)

    def test_new_functions_not_executable_by_anon(self):
        for sig in ("findings_search_cache_refresh_row(jsonb)",
                    "findings_search_cache_refresh_batch(text, integer)",
                    "findings_search_cache_job_status()",
                    "findings_search_cache_refresh(text)"):
            self.assertRegex(CODE, rf"revoke execute on function public\.{re.escape(sig)} from public, anon, authenticated;", sig)
        grants = re.findall(r"grant execute on function public\.(\S+) to ([^;]+);", CODE)
        self.assertEqual(grants, [("findings_search_cache_job_status()", "service_role")])

    def test_daily_job_batched_with_timeout(self):
        self.assertIn("schedule := '10,20,30,40 3 * * *'", CODE)
        self.assertIn("set local statement_timeout = '10min'; select public.findings_search_cache_refresh_batch('daily', 60)", CODE)
        # 4배치 × 60행이 daily 행(194)을 덮는다.
        self.assertGreaterEqual(4 * 60, 194)


def _status(**kw):
    base = {"now": "2026-11-12T08:00:00+00:00", "daily_rows": 194, "daily_fresh": 194,
            "daily_oldest_age_s": 18000,
            "runs": [{"job": "grm-findings-search-cache-daily", "start": "2026-11-12T03:40:00+00:00",
                      "status": "succeeded", "message": "1 row"}]}
    base.update(kw)
    return base


class HealthEvaluateTest(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(h.evaluate(_status()), [])

    def test_the_1112_shape_is_caught(self):
        """11-12 실측 모양: 이틀 연속 120초 실패 · 신선 0."""
        st = _status(daily_fresh=0, daily_oldest_age_s=190272, runs=[
            {"start": "2026-11-12T03:10:00+00:00", "status": "failed",
             "message": "ERROR:  canceling statement due to statement timeout"},
            {"start": "2026-11-11T03:10:00+00:00", "status": "failed", "message": "ERROR: timeout"},
            {"start": "2026-11-10T03:10:00+00:00", "status": "succeeded", "message": "1 row"}])
        problems = h.evaluate(st)
        self.assertEqual(len(problems), 3, problems)
        self.assertIn("statement timeout", problems[0])

    def test_running_latest_does_not_count_as_failure(self):
        st = _status(runs=[{"start": "2026-11-12T03:40:00+00:00", "status": "running"},
                           {"start": "2026-11-12T03:30:00+00:00", "status": "succeeded"}])
        self.assertEqual(h.evaluate(st), [])

    def test_one_failed_batch_with_enough_fresh_rows_still_flags_the_failure(self):
        st = _status(daily_fresh=150, runs=[
            {"start": "2026-11-12T03:40:00+00:00", "status": "failed", "message": "x"},
            {"start": "2026-11-12T03:30:00+00:00", "status": "succeeded"}])
        problems = h.evaluate(st)
        self.assertEqual(len(problems), 1)
        self.assertIn("실패", problems[0])

    def test_no_runs_at_all(self):
        problems = h.evaluate(_status(runs=[]))
        self.assertEqual(len(problems), 1)
        self.assertIn("성공한 daily 갱신 없음", problems[0])

    def test_fresh_threshold(self):
        self.assertEqual(h.evaluate(_status(daily_fresh=146)), [])       # 194*0.75=145.5 → 145
        self.assertEqual(len(h.evaluate(_status(daily_fresh=100))), 1)
        self.assertEqual(len(h.evaluate(_status(daily_rows=400, daily_fresh=149))), 1)  # 상한 150


class WiringTest(unittest.TestCase):
    def test_label_routed_to_ops_center(self):
        self.assertEqual(al.team_for(["findings-search-cache-health"]), "infra")
        self.assertIn("label = 'findings-search-cache-health'", WF)

    def test_workflow_runs_after_last_batch_and_uses_service_role_only_rpc(self):
        self.assertIn("cron: '55 3 * * *'", WF)
        self.assertIn("python findings_search_cache_health.py", WF)
        self.assertEqual(h.RPC_NAME, "findings_search_cache_job_status")


if __name__ == "__main__":
    unittest.main()
