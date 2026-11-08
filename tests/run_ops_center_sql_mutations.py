"""CI 전용: 격리 PostgreSQL 에서 운영 센터 122·123·125·126·127·128 을 일부러 깨 보고 tests/sql/ops_center.sql·
ops_center_decide.sql·ops_center_task.sql·ops_center_seed_planning.sql·ops_center_measure.sql 이 빨개지는지(FAIL) 확인한 뒤,
원복해서 초록(PASS)인지 확인한다.
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
M126 = ROOT / "web/migrations/126_ops_task_create.sql"
M127 = ROOT / "web/migrations/127_ops_center_seed_planning.sql"
M128 = ROOT / "web/migrations/128_ops_measure.sql"
M131 = ROOT / "web/migrations/131_ops_alarm_tasks.sql"
DB = "ops_center_test"
SQL_HOME = "tests/sql/ops_center.sql"
SQL_DECIDE = "tests/sql/ops_center_decide.sql"
SQL_TASK = "tests/sql/ops_center_task.sql"
SQL_PLAN = "tests/sql/ops_center_seed_planning.sql"
SQL_MEASURE = "tests/sql/ops_center_measure.sql"
SQL_ALARM = "tests/sql/ops_center_alarm.sql"

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


# 126 업무 지시 — tests/sql/ops_center_task.sql 이 잡아야 한다.
MUTATIONS_TASK = [
    ("task admin check removed", M126,
     "if not coalesce(private.is_admin(), false) then\n    raise exception 'ops_task_create: admin only'",
     "if false then\n    raise exception 'ops_task_create: admin only'"),
    ("task admin null guard removed", M126,
     "if not coalesce(private.is_admin(), false) then\n    raise exception 'ops_task_create: admin only'",
     "if not private.is_admin() then\n    raise exception 'ops_task_create: admin only'"),
    ("anon may call ops_task_create", M126,
     "grant execute on function public.ops_task_create(text, text, text, date, text) to authenticated;",
     "grant execute on function public.ops_task_create(text, text, text, date, text) to anon, authenticated;"),
    ("task body callable by members", M126,
     "revoke all on function private.ops_task_create_at(text, text, text, date, text, uuid, timestamptz) from public, anon, authenticated;",
     "revoke all on function private.ops_task_create_at(text, text, text, date, text, uuid, timestamptz) from anon;"),
    ("task not definer", M126,
     "volatile\nsecurity definer\nset search_path = public\nas $$\nbegin\n  -- 회원 가입이",
     "volatile\nset search_path = public\nas $$\nbegin\n  -- 회원 가입이"),
    ("blank team accepted", M126, "if nullif(btrim(coalesce(p_team, '')), '') is null then", "if p_team is null then"),
    ("team required check dropped", M126, "if nullif(btrim(coalesce(p_team, '')), '') is null then", "if false then"),
    ("inactive team accepted", M126, "if not found or not v_team.active then", "if not found then"),
    ("division accepted", M126, "if v_team.kind = 'division' then", "if false then"),
    ("title 121 accepted", M126, "char_length(v_title) > 120", "char_length(v_title) > 121"),
    ("blank title accepted", M126, "if char_length(v_title) < 1 or", "if char_length(v_title) < 0 or"),
    ("title not trimmed", M126, "v_title text := btrim(coalesce(p_title, ''));", "v_title text := coalesce(p_title, '');"),
    ("detail length unchecked", M126, "if v_detail is not null and char_length(v_detail) > 2000 then", "if false then"),
    ("past due accepted", M126, "if p_due is not null and p_due < (p_now at time zone 'Asia/Seoul')::date then", "if false then"),
    ("due uses UTC date", M126, "p_due < (p_now at time zone 'Asia/Seoul')::date", "p_due < (p_now at time zone 'UTC')::date"),
    ("member team unchecked", M126, "where slug = v_member_slug and team_id = v_team.id and state <> 'retired';",
     "where slug = v_member_slug and state <> 'retired';"),
    ("retired member accepted", M126, "where slug = v_member_slug and team_id = v_team.id and state <> 'retired';",
     "where slug = v_member_slug and team_id = v_team.id;"),
    ("duplicate window 5s", M126, "created_at > p_now - interval '10 seconds'", "created_at > p_now - interval '5 seconds'"),
    ("duplicate check dropped", M126, "if exists (select 1 from public.ops_task", "if false and exists (select 1 from public.ops_task"),
    ("lane not inbox", M126, "values (v_team.id, v_member, v_title, v_detail, 'inbox', 'directive',",
     "values (v_team.id, v_member, v_title, v_detail, 'doing', 'directive',"),
    ("reason not directive", M126, "v_detail, 'inbox', 'directive', p_actor, 'ceo',", "v_detail, 'inbox', 'routine', p_actor, 'ceo',"),
    ("created_by not recorded", M126, "'directive', p_actor, 'ceo',", "'directive', null, 'ceo',"),
    ("created_kind secretary", M126, "'directive', p_actor, 'ceo',", "'directive', p_actor, 'secretary',"),
    ("task audit row dropped", M126,
     "  insert into public.admin_audit_log (actor_user_id, action, target_type, target_id, details)\n"
     "  values (p_actor, 'ops_task_create', 'ops_task', v_id::text,\n"
     "          jsonb_build_object('team', v_team.slug, 'member', v_member_slug, 'title', v_title, 'due_on', p_due));",
     "  null;"),
]

# 127 시드 추가 — tests/sql/ops_center_seed_planning.sql 이 잡아야 한다.
MUTATIONS_PLAN = [
    ("strategy row dropped", M127,
     "  ('strategy',    '제품전략팀',       'platform', 1, false, '새 기능을 시험해 보고 쓸지 정합니다'),\n", ""),
    ("strategy still pilot", M127,
     "('strategy',    '제품전략팀',       'platform', 1, false,", "('strategy',    '제품전략팀',       'platform', 1, true,"),
    ("support under research", M127, "('support',     '독자지원팀',       'customer', 3,",
     "('support',     '독자지원팀',       'research', 3,"),
    ("planning sorted last", M127, "('planning', '경영기획실', 'office', 0,", "('planning', '경영기획실', 'office', 9,"),
    ("strat-adopted label not applied", M127, "  ('strategy', 'strat-adopted',", "  ('strategy', 'strat-adopted-2',"),
    ("plan value overwritten", M127,
     "  current_value = case when ops_goal.measured_at is null\n"
     "                         or (excluded.measured_at is not null and ops_goal.measured_at <= excluded.measured_at)\n"
     "                       then excluded.current_value else ops_goal.current_value end,",
     "  current_value = excluded.current_value,"),
    ("activity overwritten", M127,
     "  last_active_note = case when excluded.last_active_at is not null\n"
     "                           and (ops_member.last_active_at is null or ops_member.last_active_at <= excluded.last_active_at)\n"
     "                          then excluded.last_active_note else ops_member.last_active_note end,",
     "  last_active_note = excluded.last_active_note,"),
    ("risk bad line 2", M127, "'plan-risks-open', '대응 안 된 높은 위험',      '건',   'lte', 0,  null, 1, ",
     "'plan-risks-open', '대응 안 된 높은 위험',      '건',   'lte', 0,  null, 2, "),
]


# 128 자동 측정 — tests/sql/ops_center_measure.sql 이 잡아야 한다.
MUTATIONS_MEASURE = [
    ("zero denominator writes 0", M128,
     "jsonb_build_object('value', null, 'note', '분모 0 · ' || p_basis)",
     "jsonb_build_object('value', 0, 'note', '분모 0 · ' || p_basis)"),
    ("timeout not caught", M128,
     "      when query_canceled then\n        v_status := 'timeout';\n        v_err := '시간 초과';\n", ""),
    ("failure stops the loop", M128,
     "        v_status := 'error';\n        v_err := sqlstate || ' ' || left(sqlerrm, 60);",
     "        raise;"),
    ("hold refreshes measured_at", M128,
     "update public.ops_goal set note = left('측정 보류 ' || v_day || ': ' || v_note, 120) where slug = g.slug;",
     "update public.ops_goal set note = left('측정 보류 ' || v_day || ': ' || v_note, 120), measured_at = p_now where slug = g.slug;"),
    ("failure refreshes measured_at", M128,
     "update public.ops_goal set note = left('측정 실패 ' || v_day || ': ' || v_err, 120) where slug = g.slug;",
     "update public.ops_goal set note = left('측정 실패 ' || v_day || ': ' || v_err, 120), measured_at = p_now where slug = g.slug;"),
    ("ref guard removed", M128,
     "if g.measure_ref !~ '^private\\.ops_m_[a-z0-9_]{1,40}$'\n         or to_regprocedure",
     "if false\n         and to_regprocedure"),
    ("anon may run ops_measure_all", M128,
     "grant execute on function public.ops_measure_all() to service_role;",
     "grant execute on function public.ops_measure_all() to service_role, anon;"),
    ("measure revoke dropped", M128,
     "revoke all on function public.ops_measure_all() from public, anon, authenticated;",
     "revoke all on function public.ops_measure_all() from public;"),
    ("measure not definer", M128,
     "volatile\nsecurity definer\nset search_path = public\nas $$\nbegin\n  return private.ops_measure_all_at(now());",
     "volatile\nset search_path = public\nas $$\nbegin\n  return private.ops_measure_all_at(now());"),
    ("clause count not distinct", M128,
     "count(*) filter (where exists (select 1 from public.finding_clause fc\n"
     "                                                 where fc.finding_id = f.finding_id)) as num\n"
     "            from public.findings f\n"
     "           where f.source = 'FDA Warning Letter') c",
     "(select count(*) from public.finding_clause fc join public.findings f2 on f2.finding_id = fc.finding_id\n"
     "                  where f2.source = 'FDA Warning Letter') as num\n"
     "            from public.findings f\n"
     "           where f.source = 'FDA Warning Letter') c"),
    ("tiny types counted", M128, "where t.den >= 20", "where t.den >= 1"),
    ("backlog window 0 days", M128, "f.ingested_at < p_now - interval '3 days'", "f.ingested_at < p_now - interval '0 days'"),
    ("backlog counts rejected", M128, "f.review_status <> 'rejected') c", "true) c"),
    ("stale snapshot not held", M128,
     "if a.snap_date < (p_now at time zone 'Asia/Seoul')::date - 2 then", "if false then"),
    ("seed note overwritten", M128,
     "note = case when ops_goal.measured_at is null then excluded.note else ops_goal.note end;",
     "note = excluded.note;"),
    ("db warn line 420", M128,
     "'lte', 400::numeric, 400::numeric, 450::numeric", "'lte', 400::numeric, 420::numeric, 450::numeric"),
    ("manual goal measured", M128,
     "    ('tr-backlog',        'sql',    'private.ops_m_tr_backlog'),\n",
     "    ('tr-backlog',        'sql',    'private.ops_m_tr_backlog'),\n    ('sr-report',         'sql',    'private.ops_m_ingest_age'),\n"),
]


# 131 경보 업무 — tests/sql/ops_center_alarm.sql 이 잡아야 한다.
MUTATIONS_ALARM = [
    ("neglect boundary inclusive", M131,
     "and p_now - t.updated_at > interval '3 days'", "and p_now - t.updated_at >= interval '3 days'"),
    ("neglect 2 days", M131,
     "and p_now - t.updated_at > interval '3 days'", "and p_now - t.updated_at > interval '2 days'"),
    ("neglect ignores lane", M131, "     and t.lane = 'inbox'\n", ""),
    ("neglect never marked flagged", M131,
     "update public.ops_alarm set neglect_task_id = v_id, neglect_flagged_at = p_now, updated_at = p_now",
     "update public.ops_alarm set neglect_task_id = v_id, updated_at = p_now"),
    ("closed issue not completed", M131,
     "set lane = 'done', done_at = coalesce(done_at, p_now), updated_at = p_now\n     where id = v_task.id and lane <> 'done';",
     "set updated_at = p_now\n     where id = v_task.id and lane <> 'done';"),
    ("reopen keeps done", M131, "set lane = 'inbox', done_at = null, started_at = null, secretary_seen_at = null,",
     "set lane = lane, done_at = done_at, started_at = null, secretary_seen_at = null,"),
    ("re-route resets clock", M131, "    update public.ops_task set team_id = v_team.id, member_id = null where id = v_task.id;",
     "    update public.ops_task set team_id = v_team.id, member_id = null, updated_at = p_now where id = v_task.id;"),
    ("closed unknown issue created", M131,
     "    if p_state = 'closed' then\n      return jsonb_build_object('ok', true, 'action', 'skipped_closed'",
     "    if false then\n      return jsonb_build_object('ok', true, 'action', 'skipped_closed'"),
    ("anon may upsert", M131,
     "grant execute on function public.ops_alarm_upsert(integer, text, text, text, text[], text, timestamptz) to service_role;",
     "grant execute on function public.ops_alarm_upsert(integer, text, text, text, text[], text, timestamptz) to service_role, anon;"),
    ("members read table", M131, "revoke all on public.ops_alarm from public, anon, authenticated;",
     "revoke all on public.ops_alarm from public;"),
    ("ops_home admin check removed", M131,
     "if not coalesce(private.is_admin(), false) then\n    raise exception 'ops_home: admin only'",
     "if false then\n    raise exception 'ops_home: admin only'"),
    ("ops_home drops alarms", M131,
     "return private.ops_home_at(now()) || jsonb_build_object('alarms', private.ops_alarm_summary(now()));",
     "return private.ops_home_at(now());"),
    ("url guard removed", M131, "if coalesce(p_url, '') !~ '^https://github\\.com/[^ ]+$' or char_length(p_url) > 300 then",
     "if false then"),
]


def run(sql: str) -> subprocess.CompletedProcess:
    subprocess.run(["sudo", "-u", "postgres", "dropdb", "--if-exists", DB], cwd=ROOT, check=True)
    subprocess.run(["sudo", "-u", "postgres", "createdb", DB], cwd=ROOT, check=True)
    return subprocess.run(["sudo", "-u", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", DB, "-f", sql],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def main() -> None:
    originals = {p: p.read_text(encoding="utf-8") for p in (M122, M123, M125, M126, M127)}
    originals[M128] = M128.read_text(encoding="utf-8")
    originals[M131] = M131.read_text(encoding="utf-8")
    plan = [(SQL_HOME, MUTATIONS), (SQL_DECIDE, MUTATIONS_DECIDE), (SQL_TASK, MUTATIONS_TASK), (SQL_PLAN, MUTATIONS_PLAN),
            (SQL_MEASURE, MUTATIONS_MEASURE), (SQL_ALARM, MUTATIONS_ALARM)]
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
    total = sum(len(m) for _, m in plan)
    print(f"PASS: {total} mutations killed, restored SQL passes")


if __name__ == "__main__":
    main()
