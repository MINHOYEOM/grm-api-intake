#!/usr/bin/env python3
"""122_ops_center_tables.sql · 123_ops_home.sql — 오프라인 텍스트 계약 시험(운영 센터 PR1).

DB 없이 SQL 파일을 글자로 검사한다: 표 5개·RLS·권한(anon·authenticated 명시 회수, authenticated 는 SELECT 만,
쓰기는 service_role 만)·선 순서 제약·함수 안 관리자 검사(42501)·1.5배 강등·worst-of 순서·빈 집합 처리·가산만.
행동(경계표·롤업·놀고 있음·42501 실제 발생)은 격리 PostgreSQL 에서 tests/sql/ops_center.sql 이 보고,
뮤테이션은 tests/run_ops_center_sql_mutations.py 가 CI(grm-ops-center-sql-check.yml)에서 돌린다.
여기서는 ① 계약 검사 ② 검사기 자체가 깨진 SQL 을 잡는지(오프라인 뮤테이션) ③ CI 뮤테이션 목록이 낡지 않았는지.
"""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MIG = _ROOT / "web" / "migrations"
_P122 = _MIG / "122_ops_center_tables.sql"
_P123 = _MIG / "123_ops_home.sql"
_TABLES = ("ops_team", "ops_member", "ops_goal", "ops_task", "ops_decision")
_FUNCS = {
    "private.ops_state_rank": "(text)",
    "private.ops_goal_state": "(text, numeric, numeric, numeric, numeric, timestamptz, integer, timestamptz)",
    "private.ops_goal_unknown_reason": "(numeric, numeric, timestamptz, integer, timestamptz)",
    "private.ops_home_at": "(timestamptz)",
    "public.ops_home": "()",
}


def _code(sql: str) -> str:
    """주석(-- 이후) 제거. 문자열 안의 -- 는 이 두 파일에 없다."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _norm(sql: str) -> str:
    return " ".join(_code(sql).split())


def _fn_body(sql: str, name: str) -> str:
    m = re.search(r"create or replace function " + re.escape(name) + r"\(.*?\$\$(.*?)\$\$;", sql, re.S)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def _fn_header(sql: str, name: str) -> str:
    m = re.search(r"create or replace function " + re.escape(name) + r"\((.*?)\$\$", sql, re.S)
    if not m:
        raise AssertionError(f"function {name} not found")
    return " ".join(m.group(1).split())


# ── 검사기(오프라인 뮤테이션 시험이 같은 함수를 깨진 SQL 에 다시 건다) ───────────
def check_admin_guard(sql123: str) -> None:
    body = _code(_fn_body(sql123, "public.ops_home"))
    guard = "if not coalesce(private.is_admin(), false) then"
    assert guard in body, "관리자 검사 줄이 없다(null 도 거부하는 coalesce 포함)"
    i = body.index(guard)
    assert "raise exception" in body[i:] and "errcode = '42501'" in body[i:], "42501 로 거부하지 않는다"
    assert body.index("errcode = '42501'") < body.index("return private.ops_home_at(now())"), "검사보다 반환이 먼저다"
    head = _fn_header(sql123, "public.ops_home")
    for tok in ("language plpgsql", "security definer", "set search_path = public"):
        assert tok in head, tok


def check_stale_factor(sql123: str) -> None:
    for fn in ("private.ops_goal_state", "private.ops_goal_unknown_reason"):
        body = _norm(_fn_body(sql123, fn))
        assert "p_now - p_measured > make_interval(hours => (p_period_hours * 3 / 2))" in body, fn


def check_rank_order(sql123: str) -> None:
    body = _norm(_fn_body(sql123, "private.ops_state_rank"))
    got = dict((s, int(n)) for s, n in re.findall(r"when '(\w+)' then (\d)", body))
    assert got == {"bad": 4, "warn": 3, "unknown": 2, "ok": 1}, got
    assert "else 2" in body, "모르는 값은 측정 필요(2)"


def check_rollup(sql123: str) -> None:
    body = _norm(_fn_body(sql123, "private.ops_home_at"))
    assert "order by private.ops_state_rank(g3.st) desc" in body, "팀 롤업이 가장 나쁜 쪽을 고르지 않는다"
    assert "order by private.ops_state_rank(n.state) desc" in body, "부문 롤업이 가장 나쁜 쪽을 고르지 않는다"
    assert "coalesce(w.st, 'unknown') as state" in body, "목표 0개 팀이 측정 필요가 아니다"
    assert "coalesce(wc.state, 'unknown') as state" in body, "팀 0개 부문이 측정 필요가 아니다"
    assert "avg(" not in body, "평균을 쓰지 않는다"


def check_line_order(sql122: str) -> None:
    m = re.search(r"constraint ops_goal_lines_order check \((.*?)\n\);", sql122, re.S)
    assert m, "선 순서 제약이 없다"
    c = " ".join(m.group(1).split())
    gte, lte = c.split("when 'lte' then")
    for pair in ("(bad_at is null or warn_at is null or bad_at <= warn_at)",
                 "(warn_at is null or target is null or warn_at <= target)",
                 "(bad_at is null or target is null or bad_at <= target)"):
        assert pair in gte, pair
    for pair in ("(target is null or warn_at is null or target <= warn_at)",
                 "(warn_at is null or bad_at is null or warn_at <= bad_at)",
                 "(target is null or bad_at is null or target <= bad_at)"):
        assert pair in lte, pair
    assert "else false" in lte


def check_table_grants(sql122: str) -> None:
    code = _norm(sql122)
    lst = ", ".join("public." + t for t in _TABLES)
    assert f"revoke all on {lst} from public, anon, authenticated;" in code
    assert f"grant select on {lst} to authenticated;" in code
    assert f"grant all on {lst} to service_role;" in code
    for t in _TABLES:
        assert f"alter table public.{t} enable row level security;" in code, t
        assert (f'create policy "admins read {t}" on public.{t} for select to authenticated '
                f"using (private.is_admin());") in code, t
    grants = re.findall(r"grant ([a-z, ]+) on [^;]* to ([a-z_, ]+);", code)
    for privs, to in grants:
        assert "anon" not in to, (privs, to)
        if "authenticated" in to:
            assert privs.strip() == "select", (privs, to)
    assert "revoke all on sequence %s from public, anon, authenticated" in code
    assert "grant usage, select on sequence %s to service_role" in code


class FilesTest(unittest.TestCase):
    def test_files_exist_lf_and_commented(self):
        for p in (_P122, _P123):
            self.assertTrue(p.is_file(), p)
            self.assertNotIn(b"\r\n", p.read_bytes(), p.name)
            self.assertGreaterEqual(p.read_text(encoding="utf-8").count("--"), 30, p.name)

    def test_numbers_unique(self):
        for n, name in (("122", _P122.name), ("123", _P123.name)):
            self.assertEqual(sorted(p.name for p in _MIG.glob(f"{n}_*.sql")), [name])

    def test_header_has_rollback_and_post_apply_checks(self):
        s122, s123 = (p.read_text(encoding="utf-8") for p in (_P122, _P123))
        self.assertIn("drop table if exists public.ops_decision, public.ops_task, public.ops_goal,", s122)
        self.assertIn("information_schema.role_table_grants", s122)
        self.assertIn("information_schema.routine_privileges", s123)
        for fn, sig in _FUNCS.items():
            self.assertIn(f"drop function if exists {fn}{sig};", s123, fn)


class AdditiveOnlyTest(unittest.TestCase):
    """가산만: 기존 표·함수·RPC 를 건드리지 않는다."""

    def test_122_creates_only_ops_tables(self):
        code = _norm(_P122.read_text(encoding="utf-8"))
        made = re.findall(r"create table if not exists public\.(\w+)", code)
        self.assertEqual(made, list(_TABLES))
        self.assertEqual(sorted(set(re.findall(r"alter table public\.(\w+)", code))), sorted(_TABLES))
        for bad in ("drop table", "drop function", "create or replace function", "truncate", "ops_reports",
                    "insert into", "update public.", "delete from"):
            self.assertNotIn(bad, code, bad)

    def test_123_defines_only_new_functions_no_dml(self):
        code = _norm(_P123.read_text(encoding="utf-8"))
        made = re.findall(r"create or replace function ([a-z_]+\.[a-z_]+)\(", code)
        self.assertEqual(sorted(made), sorted(_FUNCS))
        for bad in ("insert into", "update public.", "delete from", "drop ", "alter table", "truncate", "ops_reports"):
            self.assertNotIn(bad, code, bad)

    def test_names_not_already_defined_elsewhere(self):
        mine = {_P122.name, _P123.name}
        for p in sorted(_MIG.glob("*.sql")):
            if p.name in mine:
                continue
            low = p.read_text(encoding="utf-8").lower()
            for t in _TABLES:
                self.assertIsNone(re.search(r"\b" + t + r"\b", low), f"{p.name} 가 {t} 를 이미 쓴다")
            for fn in _FUNCS:
                self.assertNotIn(fn.split(".")[1] + "(", low, f"{p.name} 가 {fn} 를 이미 정의")


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.s122 = _P122.read_text(encoding="utf-8")
        self.s123 = _P123.read_text(encoding="utf-8")

    def test_table_grants_and_rls(self):
        check_table_grants(self.s122)

    def test_line_order_constraint(self):
        check_line_order(self.s122)

    def test_required_targets_and_lengths(self):
        code = _norm(self.s122)
        self.assertIn("team_id smallint not null references public.ops_team(id), member_id", code)  # ops_task
        self.assertIn("check (state in ('pending','snoozed','approved','withdrawn'))", code)       # 반려 없음(D5)
        self.assertNotIn("rejected", code)
        self.assertIn("check (state <> 'snoozed' or snooze_until is not null)", code)
        self.assertIn("idle_exempt boolean not null default false", code)                          # D6
        self.assertIn("check (char_length(title) between 1 and 80)", code)                         # 결정 한 줄
        self.assertIn("(kind = 'team' and parent_id is not null) or (kind <> 'team' and parent_id is null)", code)

    def test_admin_guard(self):
        check_admin_guard(self.s123)

    def test_stale_factor(self):
        check_stale_factor(self.s123)

    def test_rank_order(self):
        check_rank_order(self.s123)

    def test_rollup_worst_of_and_empty_sets(self):
        check_rollup(self.s123)

    def test_pure_functions(self):
        for fn in ("private.ops_state_rank", "private.ops_goal_state", "private.ops_goal_unknown_reason"):
            self.assertIn("immutable", _fn_header(self.s123, fn), fn)
            self.assertNotIn("now()", _code(_fn_body(self.s123, fn)), fn)
        self.assertIn("p_now timestamptz", _fn_header(self.s123, "private.ops_goal_state"))
        body = _code(_fn_body(self.s123, "private.ops_home_at"))
        self.assertNotIn("now()", body, "본체는 p_now 만 쓴다(시각 고정 시험)")
        self.assertNotIn("current_date", body)
        self.assertNotIn("security definer", _fn_header(self.s123, "private.ops_home_at"))

    def test_warn_line_defaults_to_target(self):
        body = _norm(_fn_body(self.s123, "private.ops_goal_state"))
        self.assertEqual(body.count("coalesce(p_warn, p_target)"), 2)
        self.assertIn("p_bad is not null", body)  # 문제선 없으면 '주의'까지만

    def test_kst_boundaries_and_visible_decisions(self):
        body = _norm(_fn_body(self.s123, "private.ops_home_at"))
        self.assertIn("(p_now at time zone 'Asia/Seoul')::date as today", body)
        self.assertIn("d.state = 'pending' or (d.state = 'snoozed' and d.snooze_until <= p_now)", body)
        self.assertIn("where x.rn <= 3", body)
        self.assertIn("'publish_approval', null::jsonb", body)
        self.assertIn("p_now - last_active_at >= interval '3 days'", body)
        self.assertIn("not idle_exempt", body)

    def test_response_top_level_keys(self):
        body = _fn_body(self.s123, "private.ops_home_at")
        top = body[body.index("select jsonb_build_object(\n  'generated_at'"):]
        keys = re.findall(r"^  '([a-z_]+)',", top, re.M)
        self.assertEqual(keys, ["generated_at", "summary", "decisions", "decisions_more", "publish_approval",
                                "anomalies", "tree", "teams", "flow", "upcoming"])

    def test_function_grants(self):
        code = _norm(self.s123)
        for fn, sig in _FUNCS.items():
            self.assertIn(f"revoke all on function {fn}{sig} from public, anon, authenticated;", code, fn)
        grants = re.findall(r"grant execute on function ([^ ]+) to ([a-z_, ]+);", code)
        self.assertEqual(grants, [("public.ops_home()", "authenticated")])
        r = code.index("revoke all on function public.ops_home() from public, anon, authenticated;")
        self.assertLess(r, code.index("grant execute on function public.ops_home() to authenticated;"))


class OfflineMutationTest(unittest.TestCase):
    """검사기가 깨진 SQL 을 실제로 잡는지(가드는 뮤테이션으로 검증한다)."""

    def setUp(self):
        self.s122 = _P122.read_text(encoding="utf-8")
        self.s123 = _P123.read_text(encoding="utf-8")

    def _killed(self, check, text, old, new):
        self.assertIn(old, text, f"mutation target missing: {old!r}")
        with self.assertRaises(AssertionError, msg=f"survived: {old!r} -> {new!r}"):
            check(text.replace(old, new))

    def test_admin_bypass(self):
        g = "if not coalesce(private.is_admin(), false) then"
        self._killed(check_admin_guard, self.s123, g, "if false then")
        self._killed(check_admin_guard, self.s123, g, "if not private.is_admin() then")
        self._killed(check_admin_guard, self.s123, "errcode = '42501'", "errcode = 'P0001'")
        self._killed(check_admin_guard, self.s123, "security definer", "security invoker")

    def test_stale_factor(self):
        self._killed(check_stale_factor, self.s123, "(p_period_hours * 3 / 2)", "(p_period_hours * 2)")
        self._killed(check_stale_factor, self.s123, "p_now - p_measured > make_interval",
                     "p_now - p_measured >= make_interval")

    def test_worst_order(self):
        self._killed(check_rank_order, self.s123, "when 'warn'    then 3", "when 'warn'    then 1")
        self._killed(check_rank_order, self.s123, "when 'unknown' then 2", "when 'unknown' then 0")
        self._killed(check_rollup, self.s123, "private.ops_state_rank(g3.st) desc", "private.ops_state_rank(g3.st) asc")
        self._killed(check_rollup, self.s123, "private.ops_state_rank(n.state) desc", "private.ops_state_rank(n.state) asc")

    def test_empty_denominator(self):
        self._killed(check_rollup, self.s123, "coalesce(w.st, 'unknown') as state", "coalesce(w.st, 'ok') as state")
        self._killed(check_rollup, self.s123, "coalesce(wc.state, 'unknown') as state", "coalesce(wc.state, 'ok') as state")

    def test_line_order(self):
        self._killed(check_line_order, self.s122, "(bad_at  is null or warn_at is null or bad_at  <= warn_at)", "true")
        self._killed(check_line_order, self.s122, "(warn_at is null or bad_at  is null or warn_at <= bad_at)", "true")
        self._killed(check_line_order, self.s122, "else false", "else true")

    def test_grants(self):
        self._killed(check_table_grants, self.s122, "from public, anon, authenticated;\ngrant select",
                     "from public, authenticated;\ngrant select")
        self._killed(check_table_grants, self.s122, "  to authenticated;\ngrant all", "  to anon, authenticated;\ngrant all")
        self._killed(check_table_grants, self.s122,
                     "for select to authenticated using (private.is_admin());\ndrop policy if exists \"admins read ops_member\"",
                     "for select to authenticated using (true);\ndrop policy if exists \"admins read ops_member\"")


class CiMutationListTest(unittest.TestCase):
    """CI 뮤테이션 목록의 찾을 문자열이 지금 파일에 그대로 있는가(목록이 낡으면 CI 가 '돌았지만 아무것도 안 깬다')."""

    def test_every_ci_mutation_targets_existing_text(self):
        spec = importlib.util.spec_from_file_location("ops_mut", _ROOT / "tests" / "run_ops_center_sql_mutations.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertGreaterEqual(len(mod.MUTATIONS), 20)
        names = [m[0] for m in mod.MUTATIONS]
        self.assertEqual(len(names), len(set(names)))
        for name, path, old, new in mod.MUTATIONS:
            text = path.read_text(encoding="utf-8")
            self.assertIn(old, text, name)
            self.assertNotEqual(old, new, name)

    def test_workflow_runs_sql_and_mutations(self):
        wf = (_ROOT / ".github" / "workflows" / "grm-ops-center-sql-check.yml").read_text(encoding="utf-8")
        self.assertIn("-f tests/sql/ops_center.sql", wf)
        self.assertIn("python3 tests/run_ops_center_sql_mutations.py", wf)
        for p in ("web/migrations/*ops_center*", "web/migrations/*ops_home*", "tests/sql/ops_center.sql"):
            self.assertIn(p, wf)
        sql = (_ROOT / "tests" / "sql" / "ops_center.sql").read_text(encoding="utf-8")
        self.assertIn("\\ir ../../web/migrations/122_ops_center_tables.sql", sql)
        self.assertIn("\\ir ../../web/migrations/123_ops_home.sql", sql)
        self.assertIn("rollback;", sql)


if __name__ == "__main__":
    unittest.main()
