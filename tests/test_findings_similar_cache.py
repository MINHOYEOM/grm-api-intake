#!/usr/bin/env python3
"""092_findings_similar_cache.sql — 유사 사례 결과 캐시의 정적 계약(DB 접촉 없음).

지키려는 것(어느 하나가 조용히 뒤집히면 캐시가 틀리거나 방문자 RPC 를 느리게 만드는 자리):
  · 원 계산은 rename 으로 보존(079 성능 본문 복제 금지)
  · 공개 RPC 시그니처·기본값·권한 불변, 캐시는 정규화 limit=5 일 때만, 8일 안 행만, 아니면 폴백
  · 갱신은 **예산 안에서만** 계산(방문자 RPC 와 겹치는 긴 계산 금지)하고 대상은 086 payload 에서 뽑는다
  · 갱신·표는 클라이언트가 못 건드린다 · 상태 함수는 payload 를 내보내지 않는다
  · cron 분이 085(:00/:20/:40)·086(:05/:25/:45)과 겹치지 않는다
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "web" / "migrations"
_PATH = _MIGRATIONS / "092_findings_similar_cache.sql"


def _strip(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _slice_function(code: str, signature: str) -> str:
    start = code.index(signature)
    end = code.index("$$;", start) + len("$$;")
    return code[start:end]


class SimilarCacheMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PATH.is_file(), f"missing {_PATH}")
        self.sql = _PATH.read_text(encoding="utf-8")
        self.code = _strip(self.sql)
        self.public_fn = _slice_function(self.code, "create function public.findings_similar_to(")
        self.refresh_fn = _slice_function(
            self.code, "create or replace function public.findings_similar_cache_refresh(")
        self.status_fn = _slice_function(
            self.code, "create or replace function public.findings_similar_cache_status(")

    def test_no_crlf_and_documented(self) -> None:
        self.assertNotIn(b"\r\n", _PATH.read_bytes())
        self.assertGreaterEqual(self.sql.count("--"), 25)

    def test_depends_on_086_and_079(self) -> None:
        self.assertTrue((_MIGRATIONS / "086_findings_search_cache.sql").is_file())
        self.assertTrue((_MIGRATIONS / "079_findings_similar_perf.sql").is_file())

    def test_compute_renamed_not_copied(self) -> None:
        self.assertIn("alter function public.findings_similar_to(text, integer) "
                      "rename to findings_similar_to_compute;", self.code)
        # 079 본문의 특징 식이 이 파일에 없어야 한다(복제 금지).
        for marker in ("tsq as materialized", "ts_rank(", "window_reps", "similarity("):
            self.assertNotIn(marker, self.code, marker)

    def test_public_signature_and_defaults_unchanged(self) -> None:
        self.assertIn("create function public.findings_similar_to(p_finding_id text, "
                      "p_limit integer default 5)", self.public_fn)
        self.assertIn("security definer", self.public_fn)
        self.assertIn("set search_path to 'public', 'extensions'", self.public_fn)

    def test_public_caches_only_limit_5_and_fresh_rows(self) -> None:
        # 079 의 limit 정규화와 같은 식이어야 한다 — 다르면 limit 0/NULL 호출이 다른 키로 흩어진다.
        self.assertIn("greatest(1, least(coalesce(p_limit, 5), 50)) = 5", self.public_fn)
        self.assertIn("interval '8 days'", self.public_fn)
        self.assertIn("public.findings_similar_to_compute(p_finding_id, p_limit)", self.public_fn)
        compute_079 = _strip((_MIGRATIONS / "079_findings_similar_perf.sql").read_text(encoding="utf-8"))
        self.assertIn("greatest(1, least(coalesce(p_limit, 5), 50))", compute_079)

    def test_public_has_no_writes(self) -> None:
        low = self.public_fn.lower()
        for verb in ("insert ", "update ", "delete "):
            self.assertNotIn(verb, low)

    def test_public_grants_unchanged(self) -> None:
        self.assertIn("revoke all on function public.findings_similar_to(text, integer) from public;", self.code)
        self.assertIn("grant execute on function public.findings_similar_to(text, integer) "
                      "to anon, authenticated, service_role;", self.code)

    def test_cache_table_locked(self) -> None:
        self.assertIn("alter table public.findings_similar_cache enable row level security;", self.code)
        self.assertIn("revoke all on table public.findings_similar_cache from public, anon, authenticated;",
                      self.code)

    def test_refresh_is_budgeted_and_client_proof(self) -> None:
        self.assertIn("exit when clock_timestamp() - t_start > budget;", self.refresh_fn)
        self.assertRegex(self.refresh_fn, re.compile(r"least\(coalesce\(p_budget_ms, 20000\), 600000\)"))
        self.assertIn("exception when others then", self.refresh_fn)
        self.assertIn("public.findings_similar_to_compute(r.finding_id, 5)", self.refresh_fn)
        self.assertIn("revoke execute on function public.findings_similar_cache_refresh(integer, integer) "
                      "from public, anon, authenticated;", self.code)

    def test_refresh_targets_come_from_086_payload(self) -> None:
        targets = _slice_function(self.code, "create or replace function public.findings_similar_cache_targets(")
        self.assertIn("from public.findings_search_cache c", targets)
        self.assertIn("'$.documents[*].findings[*].finding_id'", targets)
        self.assertIn("revoke execute on function public.findings_similar_cache_targets() "
                      "from public, anon, authenticated;", self.code)
        # 갱신·상태가 같은 대상 정의를 쓴다(식 복제 금지).
        self.assertIn("public.findings_similar_cache_targets()", self.refresh_fn)
        self.assertIn("public.findings_similar_cache_targets()", self.status_fn)
        self.assertNotIn("jsonb_array_elements", self.code)
        self.assertIn("s.refreshed_at < now() - interval '3 days'", self.refresh_fn)
        self.assertIn("order by s.refreshed_at nulls first", self.refresh_fn)

    def test_refresh_prunes_old_rows_only(self) -> None:
        self.assertEqual(self.refresh_fn.count("delete from public.findings_similar_cache"), 1)
        self.assertIn("where s.refreshed_at < now() - interval '14 days'", self.refresh_fn)

    def test_status_exposes_no_payload(self) -> None:
        self.assertNotIn("'payload'", self.status_fn)
        self.assertIn("'fresh_target'", self.status_fn)
        self.assertIn("'target'", self.status_fn)

    def test_cron_minutes_do_not_overlap_085_086(self) -> None:
        m = re.search(r"cron\.schedule\('grm-findings-similar-cache', '([^']+)'", self.code)
        self.assertIsNotNone(m)
        minutes = {int(x) for x in m.group(1).split()[0].split(",")}
        self.assertEqual(minutes, {10, 30, 50})
        self.assertFalse(minutes & {0, 20, 40, 5, 25, 45})
        self.assertIn("findings_similar_cache_refresh(20000, 200)", self.code)

    def test_no_backfill_inside_migration(self) -> None:
        # 첫 채움(약 30분)은 마이그레이션에 묶지 않는다 — cron 등록 문장 밖에서 refresh 를 부르지 않는다.
        calls = [line for line in self.code.splitlines()
                 if line.strip().lower().startswith("select public.findings_similar_cache_refresh(")]
        self.assertEqual(calls, [])

    def test_reloads_postgrest(self) -> None:
        self.assertIn("notify pgrst, 'reload schema';", self.code)


if __name__ == "__main__":
    unittest.main()
