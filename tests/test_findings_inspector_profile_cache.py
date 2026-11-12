#!/usr/bin/env python3
"""152_findings_inspector_profile_cache.sql — 실사관 프로파일 결과 캐시의 정적 계약(DB 접촉 없음).

지키려는 것(어느 하나가 조용히 뒤집히면 캐시가 틀리거나 새 RPC 표면이 열리는 자리):
  · 원 계산은 rename 으로 보존(070 본문 복제 금지)하고 클라이언트 실행 권한을 거둔다
  · 공개 RPC 이름·1인자 시그니처·반환형·security definer 불변, 캐시는 정규화 키·24시간 안 행만, 아니면 폴백
  · 새 표·새 함수는 anon·authenticated 권한을 명시해 revoke(revoke from public 만으로는 anon 이 남는다)
  · 갱신은 예산 안에서만, 대상은 085 스냅샷 index 의 키, 상태 함수는 payload 를 내보내지 않는다
  · cron 분이 085(*/20)·086(:05/:25/:45)·092(:10/:30/:50)와 겹치지 않는다
  · 머리 주석에 되돌림 SQL·md5 동치 검증법이 있다
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "web" / "migrations"
_PATH = _MIGRATIONS / "152_findings_inspector_profile_cache.sql"
_PREV_DEF = _MIGRATIONS / "070_findings_inspector_document_id.sql"

_NEW_FUNCS = (
    "public.findings_inspector_profile_compute(text)",
    "public.findings_inspector_profile_cache_refresh(integer)",
    "public.findings_inspector_profile_cache_status()",
)


def _strip(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _header(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if not line.strip().startswith("--"):
            break
        lines.append(line)
    return "\n".join(lines)


def _slice_function(code: str, signature: str) -> str:
    start = code.index(signature)
    end = code.index("$$;", start) + len("$$;")
    return code[start:end]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


class InspectorProfileCacheMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PATH.is_file(), f"missing {_PATH}")
        self.sql = _PATH.read_text(encoding="utf-8")
        self.code = _strip(self.sql)
        self.head = _header(self.sql)
        self.public_fn = _slice_function(self.code, "create function public.findings_inspector_profile(")
        self.refresh_fn = _slice_function(
            self.code, "create or replace function public.findings_inspector_profile_cache_refresh(")
        self.status_fn = _slice_function(
            self.code, "create or replace function public.findings_inspector_profile_cache_status(")

    def test_no_crlf_and_documented(self) -> None:
        self.assertNotIn(b"\r\n", _PATH.read_bytes())
        self.assertGreaterEqual(self.sql.count("--"), 40)

    # ── 원 계산 보존 ─────────────────────────────────────────────────────────
    def test_compute_renamed_not_copied(self) -> None:
        self.assertIn("alter function public.findings_inspector_profile(text) "
                      "rename to findings_inspector_profile_compute;", self.code)
        # 070 본문의 특징 식이 이 파일 코드에 없어야 한다(복제 금지).
        for marker in ("findings_inspector_pairs()", "rows_out", "'by_category'", "'repeats'"):
            self.assertNotIn(marker, self.code, marker)

    def test_rename_happens_before_new_public_function(self) -> None:
        self.assertLess(self.code.index("rename to findings_inspector_profile_compute;"),
                        self.code.index("create function public.findings_inspector_profile("))

    def test_previous_definition_shape_matches(self) -> None:
        # 래퍼가 복제하는 시그니처·반환형·보안 속성이 직전 정의(070)와 같아야 한다.
        prev = _norm(_strip(_PREV_DEF.read_text(encoding="utf-8")))
        # (070 은 `set search_path = public`, 이 파일은 pg_get_functiondef 표기 `to 'public'` — 같은 설정.)
        self.assertRegex(prev, r"create or replace function public\.findings_inspector_profile\(p_inspector_key text\) "
                               r"returns jsonb language sql stable security definer "
                               r"set search_path (= public|to 'public') as \$\$")
        # 070 이 프로파일을 정의한 마지막 파일이어야 한다(그 뒤 재정의가 있으면 이 래퍼 계약을 다시 볼 것).
        later = [p.name for p in sorted(_MIGRATIONS.glob("*.sql"))
                 if p.name > _PREV_DEF.name and p != _PATH
                 and re.search(r"function\s+public\.findings_inspector_profile\s*\(",
                               _strip(p.read_text(encoding="utf-8")))]
        self.assertEqual(later, [])

    # ── 공개 RPC 계약 ────────────────────────────────────────────────────────
    def test_public_signature_and_return_unchanged(self) -> None:
        fn = _norm(self.public_fn)
        self.assertIn("create function public.findings_inspector_profile(p_inspector_key text) "
                      "returns jsonb language sql stable security definer set search_path to 'public' as $$", fn)

    def test_public_reads_normalized_fresh_cache_then_falls_back(self) -> None:
        fn = _norm(self.public_fn)
        self.assertIn("c.inspector_key = public.findings_inspector_key(coalesce(p_inspector_key, ''))", fn)
        self.assertIn("c.payload is not null", fn)
        self.assertIn("interval '24 hours'", fn)
        self.assertIn("public.findings_inspector_profile_compute(p_inspector_key)", fn)
        self.assertTrue(fn.startswith("create function") and "select coalesce(" in fn)

    def test_public_has_no_writes(self) -> None:
        low = self.public_fn.lower()
        for verb in ("insert ", "update ", "delete "):
            self.assertNotIn(verb, low)

    def test_public_grants(self) -> None:
        self.assertIn("revoke all on function public.findings_inspector_profile(text) from public;", self.code)
        self.assertIn("grant execute on function public.findings_inspector_profile(text) "
                      "to anon, authenticated, service_role;", self.code)

    # ── 새 표·새 함수: anon·authenticated 명시 revoke ───────────────────────
    def test_cache_table_locked(self) -> None:
        self.assertIn("alter table public.findings_inspector_profile_cache enable row level security;", self.code)
        self.assertIn("revoke all on table public.findings_inspector_profile_cache "
                      "from public, anon, authenticated;", self.code)
        self.assertNotRegex(self.code, r"create\s+policy[^;]*findings_inspector_profile_cache")
        self.assertNotRegex(self.code, r"grant[^;]*on\s+table\s+public\.findings_inspector_profile_cache")

    def test_new_functions_revoke_anon_and_authenticated(self) -> None:
        for sig in _NEW_FUNCS:
            self.assertIn(f"revoke execute on function {sig} from public, anon, authenticated;", self.code, sig)

    def test_no_new_function_granted_to_clients(self) -> None:
        for m in re.finditer(r"grant\s+execute\s+on\s+function\s+([^\s]+)\s+to\s+([^;]+);", self.code):
            fn, roles = m.group(1), m.group(2)
            if fn == "public.findings_inspector_profile(text)":
                continue
            self.assertNotRegex(roles, r"\b(anon|authenticated|public)\b", f"{fn} -> {roles}")

    def test_status_is_service_role_only_and_has_no_payload(self) -> None:
        self.assertIn("grant execute on function public.findings_inspector_profile_cache_status() "
                      "to service_role;", self.code)
        self.assertNotIn("payload,", self.status_fn.replace("payload is not null", ""))
        self.assertNotIn("'payload'", self.status_fn)

    # ── 갱신 ─────────────────────────────────────────────────────────────────
    def test_refresh_is_invoker_budgeted_and_row_isolated(self) -> None:
        fn = _norm(self.refresh_fn)
        self.assertIn("security invoker", fn)
        self.assertIn("exit when clock_timestamp() - t_start > budget;", fn)
        self.assertIn("least(coalesce(p_budget_ms, 20000), 600000)", fn)
        self.assertIn("exception when others then", fn)
        self.assertIn("public.findings_inspector_profile_compute(r.inspector_key)", fn)
        self.assertIn("order by c.refreshed_at asc nulls first", fn)

    def test_refresh_targets_come_from_085_index_and_refuse_empty(self) -> None:
        fn = _norm(self.refresh_fn)
        self.assertIn("public.findings_inspector_index()", fn)
        self.assertIn("raise exception", fn)
        self.assertIn("cardinality(v_keys) = 0", fn)

    def test_no_set_role_anywhere(self) -> None:
        # 원 계산은 security definer(공개 술어를 본문에 건다) — 역할 전환이 필요 없고, definer 안에선 42501.
        self.assertNotRegex(self.code.lower(), r"\bset\s+(local\s+)?role\b")

    # ── cron ─────────────────────────────────────────────────────────────────
    def test_cron_name_and_minutes_do_not_collide(self) -> None:
        m = re.search(r"cron\.schedule\('grm-findings-inspector-profile-cache',\s*'([^']+)'", self.code)
        self.assertIsNotNone(m)
        minutes = {int(x) for x in m.group(1).split()[0].split(",")}
        self.assertEqual(minutes, {15, 35, 55})
        taken = {0, 20, 40, 5, 25, 45, 10, 30, 50}
        self.assertFalse(minutes & taken)
        self.assertIn("findings_inspector_profile_cache_refresh(20000)", self.code)

    def test_initial_fill_not_bundled(self) -> None:
        calls = [line for line in self.code.splitlines()
                 if line.strip().lower().startswith("select public.findings_inspector_profile_cache_refresh(")]
        self.assertEqual(calls, [])

    def test_reloads_postgrest(self) -> None:
        self.assertIn("notify pgrst, 'reload schema';", self.code)

    # ── 머리 주석: 되돌림·검증 ───────────────────────────────────────────────
    def test_header_has_rollback_sql(self) -> None:
        h = self.head
        for s in ("select cron.unschedule('grm-findings-inspector-profile-cache');",
                  "drop function public.findings_inspector_profile(text);",
                  "alter function public.findings_inspector_profile_compute(text) rename to findings_inspector_profile;",
                  "grant execute on function public.findings_inspector_profile(text) to",
                  "drop function public.findings_inspector_profile_cache_refresh(integer);",
                  "drop function public.findings_inspector_profile_cache_status();",
                  "drop table public.findings_inspector_profile_cache;"):
            self.assertIn(s, h, s)

    def test_header_has_md5_equivalence_check(self) -> None:
        h = self.head
        # 적용 전 캡처와 적용 후 재대조 — 둘 다 md5 로(같은 파이프라인).
        pre = h[h.index("적용 **전**"):h.index("1) 이 파일 적용")]
        post = h[h.index("적용 **후**"):h.index("5) 권한")]
        self.assertIn("md5", pre)
        self.assertIn("md5", post)
        self.assertIn("113", h)
        self.assertIn("is distinct from public.findings_inspector_profile_compute(c.inspector_key)", h)
        self.assertIn("information_schema.routine_privileges", h)


if __name__ == "__main__":
    unittest.main()
