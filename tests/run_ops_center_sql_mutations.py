"""CI 전용: 격리 PostgreSQL 에서 운영 센터 122·123·125 를 일부러 깨 보고 tests/sql/ops_center.sql·
tests/sql/ops_center_decide.sql 이 빨개지는지(FAIL) 확인한 뒤, 원복해서 초록(PASS)인지 확인한다.
가드는 뮤테이션으로 검증한다.

로컬에서 돌리지 않는다(sudo postgres 필요). 워크플로: .github/workflows/grm-ops-center-sql-check.yml
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M122 = ROOT / "web/migrations/122_ops_center_tables.sql"
M123 = ROOT / "web/migrations/123_ops_home.sql"
M125 = ROOT / "web/migrations/125_ops_decision_write.sql"
DB = "ops_center_test"
SQL_HOME = "tests/sql/ops_center.sql"
SQL_DECIDE = "tests/sql/ops_center_decide.sql"

# (이름, 파일, 찾을 문자열, 바꿀 문자열) — 찾을 문자열이 없으면 뮤테이션 자체가 낡은 것이므로 실패시킨다.
MUTATIONS = [
    # 관리자 검사 우회
    ("admin check removed", M123,
     "if not coalesce(private.is_admin(), false) then", "if false then"),
    ("admin null guard removed", M123,
     "if not coalesce(private.is_admin(), false) then", "if not private.is_admin() then"),
    ("anon may call ops_home", M123,
     "grant execute on function public.ops_home() to authenticated;",
     "grant execute on function public.ops_home() to anon, authenticated;"),
    ("body callable by members", M123,
     "revoke all on function private.ops_home_at(timestamptz) from public, anon, authenticated;",
     "revoke all on function private.ops_home_at(timestamptz) from anon;"),
    ("RLS policy opened", M122,
     'create policy "admins read ops_team" on public.ops_team\n  for select to authenticated using (private.is_admin());',
     'create policy "admins read ops_team" on public.ops_team\n  for select to authenticated using (true);'),
    ("anon table revoke dropped", M122,
     "revoke all on public.ops_team, public.ops_member, public.ops_goal, public.ops_task, public.ops_decision\n  from public, anon, authenticated;",
     "revoke all on public.ops_team, public.ops_member, public.ops_goal, public.ops_task, public.ops_decision\n  from public, authenticated;"),
    ("sequence revoke dropped", M122,
     "execute format('revoke all on sequence %s from public, anon, authenticated', s);", "null;"),
    # 1.5배 강등
    ("stale factor 2x", M123, "(p_period_hours * 3 / 2)", "(p_period_hours * 2)"),
    ("stale on equal", M123,
     "when p_now - p_measured > make_interval(hours => (p_period_hours * 3 / 2)) then 'unknown'",
     "when p_now - p_measured >= make_interval(hours => (p_period_hours * 3 / 2)) then 'unknown'"),
    # 상태 선
    ("near counted as warn", M123,
     "when (p_dir = 'gte' and p_value < coalesce(p_warn, p_target))",
     "when (p_dir = 'gte' and p_value < p_target)"),
    ("missing warn line means ok", M123,
     "or (p_dir = 'lte' and p_value > coalesce(p_warn, p_target)) then 'warn'",
     "or (p_dir = 'lte' and p_value > p_warn) then 'warn'"),
    ("bad on equal line", M123,
     "((p_dir = 'gte' and p_value < p_bad) or (p_dir = 'lte' and p_value > p_bad))",
     "((p_dir = 'gte' and p_value <= p_bad) or (p_dir = 'lte' and p_value >= p_bad))"),
    # worst-of 순서
    ("unknown ranked above warn", M123,
     "    when 'warn'    then 3\n    when 'unknown' then 2\n",
     "    when 'warn'    then 2\n    when 'unknown' then 3\n"),
    ("ok ranked above unknown", M123,
     "    when 'unknown' then 2\n    when 'ok'      then 1\n",
     "    when 'unknown' then 1\n    when 'ok'      then 2\n"),
    ("rollup picks best not worst", M123,
     "order by private.ops_state_rank(g3.st) desc, g3.sort_order, g3.id",
     "order by private.ops_state_rank(g3.st) asc, g3.sort_order, g3.id"),
    ("division picks best child", M123,
     "order by private.ops_state_rank(n.state) desc, n.sort_order, n.id",
     "order by private.ops_state_rank(n.state) asc, n.sort_order, n.id"),
    # 분모 0(빈 집합)
    ("team without goals is ok", M123, "coalesce(w.st, 'unknown') as state", "coalesce(w.st, 'ok') as state"),
    ("division without teams is ok", M123, "coalesce(wc.state, 'unknown') as state", "coalesce(wc.state, 'ok') as state"),
    # 선 순서 제약
    ("gte bad<=warn dropped", M122,
     "(bad_at  is null or warn_at is null or bad_at  <= warn_at)", "true"),
    ("gte warn<=target dropped", M122,
     "(warn_at is null or target  is null or warn_at <= target)", "true"),
    ("lte warn<=bad dropped", M122,
     "(warn_at is null or bad_at  is null or warn_at <= bad_at)", "true"),
    ("lte target<=warn dropped", M122,
     "(target  is null or warn_at is null or target  <= warn_at)", "true"),
    # 놀고 있음(D6)·결정 보이는 조건
    ("idle_exempt ignored", M123,
     "(state = 'active' and not idle_exempt and open_tasks = 0 and team_alert\n          and last_active_at is not null",
     "(state = 'active' and open_tasks = 0 and team_alert\n          and last_active_at is not null"),
    ("idle without alert", M123,
     "open_tasks = 0 and team_alert\n          and last_active_at is not null",
     "open_tasks = 0\n          and last_active_at is not null"),
    ("never-active counted idle", M123,
     "and last_active_at is not null and p_now - last_active_at >= interval '3 days'",
     "and coalesce(p_now - last_active_at >= interval '3 days', true)"),
    ("snoozed shows early", M123,
     "(d.state = 'snoozed' and d.snooze_until <= p_now)", "d.state = 'snoozed'"),
    ("inbox cap 4", M123, "where x.rn <= 3", "where x.rn <= 4"),
]

# 125 결정 쓰기 — tests/sql/ops_center_decide.sql 이 잡아야 한다.
MUTATIONS_DECIDE = [
    ("decide admin check removed", M125,
     "if not coalesce(private.is_admin(), false) then\n    raise exception 'ops_decide: admin only'",
     "if false then\n    raise exception 'ops_decide: admin only'"),
    ("decide admin null guard removed", M125,
     "if not coalesce(private.is_admin(), false) then\n    raise exception 'ops_decide: admin only'",
     "if not private.is_admin() then\n    raise exception 'ops_decide: admin only'"),
    ("anon may call ops_decide", M125,
     "grant execute on function public.ops_decide(bigint, text, text) to authenticated;",
     "grant execute on function public.ops_decide(bigint, text, text) to anon, authenticated;"),
    ("approve ignores state", M125,
     "snooze_until = null, secretary_seen_at = null\n     where id = p_id and state in ('pending','snoozed')",
     "snooze_until = null, secretary_seen_at = null\n     where id = p_id"),
    ("snooze ignores state", M125,
     "secretary_seen_at = null\n     where id = p_id and state in ('pending','snoozed')\n     returning * into v_row;\n  end if;",
     "secretary_seen_at = null\n     where id = p_id\n     returning * into v_row;\n  end if;"),
    ("decided_by not recorded", M125, "decided_at = p_now, decided_by = p_actor,", "decided_at = p_now,"),
    ("audit row dropped", M125,
     "  insert into public.admin_audit_log (actor_user_id, action, target_type, target_id, details)\n"
     "  values (p_actor, 'ops_decide', 'ops_decision', p_id::text,\n"
     "          jsonb_build_object('action', p_action, 'snooze', p_snooze, 'snooze_until', v_until, 'title', v_row.title));",
     "  null;"),
    ("snooze uses UTC date", M125,
     "when 'tomorrow' then (((p_now at time zone 'Asia/Seoul')::date + 1)",
     "when 'tomorrow' then (((p_now at time zone 'UTC')::date + 1)"),
    ("snooze at 09:00", M125, "+ 1) + time '08:00'", "+ 1) + time '09:00'"),
    ("3d is 2d", M125, "+ 3) + time '08:00'", "+ 2) + time '08:00'"),
    ("service fn open to members", M125,
     "grant execute on function public.ops_secretary_inbox() to service_role;",
     "grant execute on function public.ops_secretary_inbox() to service_role, authenticated;"),
    ("service revoke dropped", M125,
     "revoke all on function public.ops_goal_set(text, numeric, text, timestamptz) from public, anon, authenticated;",
     "revoke all on function public.ops_goal_set(text, numeric, text, timestamptz) from public;"),
    ("publish PR guard removed", M125, "if coalesce(p_ref_url, '') ~ 'publish/brief-' then", "if false then"),
    ("own decision unseen", M125, "          now())   -- 비서실이 올린 행은", "          null)   -- 비서실이 올린 행은"),
    ("inbox shows unprocessed", M125, "x.secretary_seen_at is null and x.state in ('approved','snoozed')",
     "x.secretary_seen_at is null"),
    ("percent range unchecked", M125, "if v_unit = '%' and (p_value < 0 or p_value > 100) then", "if false then"),
    ("done without link", M125,
     "if p_lane = 'done' and coalesce(p_result_url, v_task.result_url) is null then", "if false then"),
    ("done reopened", M125, "if v_task.lane = 'done' then", "if false then"),
]


def run(sql: str) -> subprocess.CompletedProcess:
    subprocess.run(["sudo", "-u", "postgres", "dropdb", "--if-exists", DB], cwd=ROOT, check=True)
    subprocess.run(["sudo", "-u", "postgres", "createdb", DB], cwd=ROOT, check=True)
    return subprocess.run(["sudo", "-u", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", DB, "-f", sql],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def main() -> None:
    originals = {p: p.read_text(encoding="utf-8") for p in (M122, M123, M125)}
    plan = [(SQL_HOME, MUTATIONS), (SQL_DECIDE, MUTATIONS_DECIDE)]
    try:
        for sql, _ in plan:
            baseline = run(sql)
            if baseline.returncode:
                raise RuntimeError(f"Unmodified SQL failed ({sql}):\n" + baseline.stdout + baseline.stderr)
        for sql, mutations in plan:
            for name, path, old, new in mutations:
                text = originals[path]
                if old not in text:
                    raise RuntimeError(f"mutation target missing (stale mutation?): {name}")
                path.write_text(text.replace(old, new), encoding="utf-8")
                try:
                    result = run(sql)
                finally:
                    path.write_text(text, encoding="utf-8")
                if result.returncode == 0:
                    raise RuntimeError(f"mutation survived (guard is blind): {name}")
                print(f"killed: {name}")
        for sql, _ in plan:
            restored = run(sql)
            if restored.returncode:
                raise RuntimeError(f"Restored SQL failed ({sql}):\n" + restored.stdout + restored.stderr)
    finally:
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")
    total = len(MUTATIONS) + len(MUTATIONS_DECIDE)
    print(f"PASS: {total} mutations killed, restored SQL passes")


if __name__ == "__main__":
    main()
