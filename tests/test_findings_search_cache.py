#!/usr/bin/env python3
"""086_findings_search_cache.sql — 검색 RPC 결과 캐시의 정적 계약(DB 접촉 없음).

지키려는 것(어느 하나가 조용히 뒤집히면 캐시가 틀리거나 공개 게이트가 새는 자리):
  · 공개 RPC `findings_search` 는 여전히 **security invoker** 다 — definer 로 뒤집히면 RLS 를 우회해
    비공개 행이 캐시에도 화면에도 실린다(030 계약).
  · 캐시는 anon/authenticated 에게만 — service_role 등은 종전 경로.
  · 원 계산은 rename 으로 보존(복제 금지) · 공개 RPC 에 쓰기 없음.
  · 키 정규화가 082 의 `p` CTE 와 같은 표현식 — 한쪽만 바뀌면 같은 인자가 다른 키로 흩어지거나
    (캐시 무용) 다른 인자가 같은 키로 접힌다(캐시 오답).
  · 갱신 함수는 invoker + `set local role anon`/`reset role` — definer 안에서는 set role 이
    금지된다(실측 42501).
  · 시드가 용어사전 사례 링크 전량(glossary_cases.json 의 q)을 덮는다.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "web" / "migrations"
_PATH = _MIGRATIONS / "086_findings_search_cache.sql"
_SEARCH_082 = _MIGRATIONS / "082_findings_search_text_only.sql"
_GLOSSARY_CASES = _ROOT / "web" / "data" / "glossary_cases.json"


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _slice_function(code: str, signature: str) -> str:
    start = code.index(signature)
    end = code.index("$$;", start) + len("$$;")
    return code[start:end]


def _param_block(code: str) -> str:
    """`public.findings_search(` 부터 `returns jsonb` 까지, 공백 정규화."""
    # `alter function public.findings_search(text, …) rename` 의 타입 목록이 아니라 인자 선언부.
    start = code.index("public.findings_search(\n  p_q text")
    end = code.index("returns jsonb", start)
    return re.sub(r"\s+", " ", code[start:end]).strip()


class CacheMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PATH.is_file(), f"missing {_PATH}")
        self.sql = _PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_previous_migration_exists(self) -> None:
        self.assertTrue((_MIGRATIONS / "085_rpc_snapshot.sql").is_file())

    def test_compute_is_renamed_not_copied(self) -> None:
        self.assertIn("rename to findings_search_compute", self.code)
        # 082 본문의 CTE 가 이 파일에 없어야 한다 — 본문 복제(정본 표류)를 막는다.
        self.assertNotIn("searched as (", self.code)
        self.assertNotIn("page_docs_full", self.code)

    def test_public_rpc_is_security_invoker(self) -> None:
        fn = _slice_function(self.code, "create function public.findings_search(")
        self.assertIn("security invoker", fn)
        self.assertNotIn("security definer", fn)

    def test_public_rpc_caches_only_for_anon_or_authenticated(self) -> None:
        fn = _slice_function(self.code, "create function public.findings_search(")
        self.assertIn("current_user in ('anon', 'authenticated')", fn)

    def test_public_rpc_falls_back_to_compute_with_all_13_args(self) -> None:
        fn = _slice_function(self.code, "create function public.findings_search(")
        self.assertIn("public.findings_search_compute(", fn)
        self.assertIn("public.findings_search_cache_read(public.findings_search_cache_args(", fn)
        for name in ("p_q", "p_source", "p_category", "p_month", "p_evidence", "p_review_status",
                     "p_agency", "p_sort", "p_page", "p_docs_per_page", "p_country",
                     "p_orig_lang", "p_text_only"):
            # 키 함수와 원 계산 양쪽에 같은 인자가 전부 전달돼야 한다(하나라도 빠지면 키가 접힌다).
            self.assertGreaterEqual(fn.count(name), 3, name)

    def test_public_rpc_has_no_writes(self) -> None:
        fn = _slice_function(self.code, "create function public.findings_search(")
        for verb in ("insert ", "update ", "delete "):
            self.assertNotIn(verb, fn.lower())

    def test_signature_identical_to_082(self) -> None:
        self.assertEqual(_param_block(self.code),
                         _param_block(_strip_sql_comments(_SEARCH_082.read_text(encoding="utf-8"))))

    def test_grants_match_085_convention(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_search(\n"
            "  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)\n"
            "  to anon, authenticated, service_role;", self.code)

    def test_cache_table_locked_from_clients(self) -> None:
        self.assertIn("alter table public.findings_search_cache enable row level security;", self.code)
        self.assertIn("revoke all on table public.findings_search_cache from public, anon, authenticated;",
                      self.code)

    def test_read_helper_serves_fresh_payload_only(self) -> None:
        fn = _slice_function(self.code, "create or replace function public.findings_search_cache_read(")
        self.assertIn("security definer", fn)
        self.assertIn("payload is not null", fn)
        self.assertIn("interval '75 minutes'", fn)
        self.assertIn("interval '30 hours'", fn)

    def test_refresh_is_invoker_and_toggles_role_both_paths(self) -> None:
        fn = _slice_function(self.code, "create or replace function public.findings_search_cache_refresh(")
        self.assertIn("security invoker", fn)
        self.assertNotIn("security definer", fn)
        self.assertEqual(fn.count("set local role anon"), 1)
        # 정상 경로 + 예외 경로 둘 다 역할을 되돌린다.
        self.assertEqual(fn.count("reset role"), 2)
        self.assertIn("exception when others then", fn)
        self.assertIn("args not normalized", fn)

    def test_refresh_not_executable_by_clients(self) -> None:
        self.assertIn(
            "revoke execute on function public.findings_search_cache_refresh(text) "
            "from public, anon, authenticated;", self.code)

    def test_status_exposes_no_payload(self) -> None:
        fn = _slice_function(self.code, "create or replace function public.findings_search_cache_status(")
        self.assertNotIn("'payload'", fn)
        self.assertIn("'table_bytes'", fn)

    def test_args_normalization_mirrors_082_p_cte(self) -> None:
        code_082 = _strip_sql_comments(_SEARCH_082.read_text(encoding="utf-8"))
        key_fn = _slice_function(self.code, "create or replace function public.findings_search_cache_args(")
        self.assertIn("immutable", key_fn)
        for expr in (
            "coalesce(btrim(p_q), '')",
            "p_sort in ('date_desc', 'date_asc', 'firm_asc')",
            "least(greatest(coalesce(p_page, 1), 1), 400000)",
            "least(greatest(coalesce(p_docs_per_page, 24), 1), 100)",
            "upper(coalesce(btrim(p_country), ''))",
            "lower(coalesce(btrim(p_orig_lang), '')) = 'en'",
            "coalesce(p_text_only, false)",
        ):
            self.assertIn(expr, code_082, f"082 에서 사라짐: {expr}")
            self.assertIn(expr, key_fn, f"086 키 함수에 없음: {expr}")

    def test_seed_has_two_hot_defaults(self) -> None:
        self.assertIn("(public.findings_search_cache_args(), 'hot')", self.code)
        self.assertIn("(public.findings_search_cache_args(p_orig_lang := 'en'), 'hot')", self.code)

    def test_seed_covers_every_glossary_case_query(self) -> None:
        data = json.loads(_GLOSSARY_CASES.read_text(encoding="utf-8"))
        items = data["items"] if isinstance(data["items"], list) else list(data["items"].values())
        qs = sorted({it["q"] for it in items if isinstance(it, dict) and it.get("q")})
        self.assertGreaterEqual(len(qs), 100)
        missing = [q for q in qs
                   if f"(public.findings_search_cache_args(p_q := '{q}', p_text_only := true), 'daily')"
                   not in self.code]
        self.assertEqual(missing, [], f"시드에 없는 용어 사례 질의 {len(missing)}건 — 086 시드를 다시 생성하라")
        for q in qs:
            self.assertNotIn("'", q)
            self.assertNotIn("\\", q)

    def test_seed_is_idempotent(self) -> None:
        self.assertIn("on conflict (args) do nothing;", self.code)

    def test_cron_jobs_offset_from_085(self) -> None:
        self.assertIn("cron.schedule('grm-findings-search-cache-hot',   '5,25,45 * * * *'", self.code)
        self.assertIn("cron.schedule('grm-findings-search-cache-daily', '10 3 * * *'", self.code)
        self.assertIn("findings_search_cache_refresh('hot')", self.code)
        self.assertIn("findings_search_cache_refresh('daily')", self.code)

    def test_reloads_postgrest_schema(self) -> None:
        self.assertIn("notify pgrst, 'reload schema';", self.code)


if __name__ == "__main__":
    unittest.main()
