\set ON_ERROR_STOP on
-- 운영 센터 125(결정 쓰기·서비스 함수) 행동 시험. 별도 빈 테스트 DB 에서만 실행한다.
-- CI: .github/workflows/grm-ops-center-sql-check.yml · 뮤테이션: tests/run_ops_center_sql_mutations.py
begin;

-- ── Supabase 흉내(ops_center.sql 과 같은 바탕 + auth.uid·admin_audit_log) ─────────
create role anon;
create role authenticated;
create role service_role bypassrls;
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('test.uid', true), '')::uuid
$$;
grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
create schema private;
grant usage on schema private to authenticated;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
alter default privileges in schema private grant execute on functions to anon, authenticated;
create function private.is_admin() returns boolean language sql stable as $$
  select case current_setting('test.is_admin', true) when 'on' then true when 'null' then null else false end
$$;
grant execute on function private.is_admin() to authenticated;
-- 기존 감사 표(supabase/migrations/202607050001_admin_ops.sql)와 같은 열
create table public.admin_audit_log (
  id bigint generated always as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null, target_type text, target_id text,
  details jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());
revoke all on public.admin_audit_log from anon, authenticated;

\ir ../../web/migrations/122_ops_center_tables.sql
\ir ../../web/migrations/123_ops_home.sql
\ir ../../web/migrations/125_ops_decision_write.sql

create function pg_temp.assert_true(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;
create function pg_temp.assert_eq(got text, want text, message text) returns void language plpgsql as $$
begin
  if got is distinct from want then
    raise exception 'assertion failed: % (got %, want %)', message, coalesce(got, 'NULL'), coalesce(want, 'NULL');
  end if;
end $$;

insert into auth.users values ('00000000-0000-0000-0000-0000000000a1');
insert into public.ops_team (slug, name, kind, sort_order) values ('sr', '종합상황실', 'office', 1);
insert into public.ops_member (team_id, slug, name, kind)
  values ((select id from public.ops_team where slug = 'sr'), 'sr-prep', '발행 준비 담당', 'routine');
insert into public.ops_goal (team_id, slug, label, unit, direction, target, period_hours, measure_kind)
  values ((select id from public.ops_team where slug = 'sr'), 'sr-ontime', '제때 돈 비율', '%', 'gte', 95, 24, 'github');
insert into public.ops_decision (team_id, title, reason, state, snooze_until) values
  ((select id from public.ops_team where slug = 'sr'), '승인할 결정', 'approve', 'pending', null),
  ((select id from public.ops_team where slug = 'sr'), '보류할 결정', 'ask', 'pending', null),
  ((select id from public.ops_team where slug = 'sr'), '철회된 결정', 'approve', 'withdrawn', null),
  ((select id from public.ops_team where slug = 'sr'), '보류 중 결정', 'approve', 'snoozed', '2026-11-05 08:00+09');

-- ── 1. 권한 ─────────────────────────────────────────────────────────────────
select pg_temp.assert_true(not has_function_privilege('anon', 'public.ops_decide(bigint,text,text)', 'EXECUTE'), 'anon cannot call ops_decide');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.ops_decide(bigint,text,text)', 'EXECUTE'), 'authenticated may call ops_decide');
select pg_temp.assert_true((select prosecdef from pg_proc where oid = 'public.ops_decide(bigint,text,text)'::regprocedure), 'ops_decide is security definer');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'private.ops_decide_at(bigint,text,text,uuid,timestamptz)', 'EXECUTE'), 'members cannot call ops_decide_at');
select pg_temp.assert_true(not has_function_privilege('anon', 'private.ops_snooze_until(timestamptz,text)', 'EXECUTE'), 'anon cannot call ops_snooze_until');
do $$
declare f text;
begin
  foreach f in array array[
    'public.ops_decision_open(text,text,text,text,text,text,timestamptz,text)',
    'public.ops_secretary_inbox()',
    'public.ops_member_touch(text,text,timestamptz)',
    'public.ops_goal_set(text,numeric,text,timestamptz)',
    'public.ops_task_move(bigint,text,text,text)'] loop
    perform pg_temp.assert_true(not has_function_privilege('anon', f, 'EXECUTE'), 'anon cannot call ' || f);
    perform pg_temp.assert_true(not has_function_privilege('authenticated', f, 'EXECUTE'), 'authenticated cannot call ' || f);
    perform pg_temp.assert_true(has_function_privilege('service_role', f, 'EXECUTE'), 'service_role may call ' || f);
    perform pg_temp.assert_true(not (select prosecdef from pg_proc where oid = f::regprocedure), f || ' is security invoker');
    perform pg_temp.assert_true((select proconfig::text like '%search_path=public%' from pg_proc where oid = f::regprocedure), f || ' fixes search_path');
  end loop;
end $$;

-- ── 2. 보류 시각 = KST 08:00 ────────────────────────────────────────────────
select pg_temp.assert_eq(private.ops_snooze_until('2026-11-06 09:10+09', 'tomorrow')::text,
                         ('2026-11-07 08:00+09'::timestamptz)::text, 'tomorrow 08:00 KST');
select pg_temp.assert_eq(private.ops_snooze_until('2026-11-06 09:10+09', '3d')::text,
                         ('2026-11-09 08:00+09'::timestamptz)::text, '3 days later 08:00 KST');
select pg_temp.assert_eq(private.ops_snooze_until('2026-11-06 23:59+09', 'tomorrow')::text,
                         ('2026-11-07 08:00+09'::timestamptz)::text, 'late night still next KST day');
-- KST 00:30 = UTC 전날 15:30. UTC 날짜로 계산하면 '내일'이 오늘 아침 08:00 이 된다.
select pg_temp.assert_eq(private.ops_snooze_until('2026-11-06 00:30+09', 'tomorrow')::text,
                         ('2026-11-07 08:00+09'::timestamptz)::text, 'KST date, not UTC date');
select pg_temp.assert_true(private.ops_snooze_until('2026-11-06 09:10+09', 'forever') is null, 'unknown kind → null');

-- ── 3. 관리자 아님 → 42501 ─────────────────────────────────────────────────
set local role authenticated;
select set_config('test.uid', '00000000-0000-0000-0000-0000000000a1', true);
do $$
declare v text;
begin
  foreach v in array array['off', 'null'] loop
    perform set_config('test.is_admin', v, true);
    begin
      perform public.ops_decide((select id from public.ops_decision where title = '승인할 결정'), 'approve');
      raise exception 'non-admin (%) decided', v;
    exception when insufficient_privilege then null; end;
  end loop;
end $$;
-- 회원은 표에 직접 못 쓴다(쓰기는 함수만)
select set_config('test.is_admin', 'on', true);
do $$
begin
  begin
    update public.ops_decision set state = 'approved';
    raise exception 'admin updated ops_decision directly';
  exception when insufficient_privilege then null; end;
  begin
    perform public.ops_decision_open('sr', 't', null, null, null, 'approve', null, null);
    raise exception 'admin called a service function';
  exception when insufficient_privilege then null; end;
end $$;

-- ── 4. 승인: 대기 → 승인, 두 번째는 '이미 처리' ─────────────────────────────
do $$
declare r jsonb; d public.ops_decision%rowtype; id1 bigint := (select id from public.ops_decision where title = '승인할 결정');
begin
  r := public.ops_decide(id1, 'approve');
  perform pg_temp.assert_eq(r->>'ok', 'true', 'approve ok');
  perform pg_temp.assert_eq(r->>'message', '승인됨 · 비서실이 이어서 진행합니다', 'approve message');
  select * into d from public.ops_decision where id = id1;
  perform pg_temp.assert_eq(d.state, 'approved', 'state approved');
  perform pg_temp.assert_eq(d.decided_by::text, '00000000-0000-0000-0000-0000000000a1', 'decided_by = caller');
  perform pg_temp.assert_true(d.decided_at is not null, 'decided_at set');
  perform pg_temp.assert_true(d.secretary_seen_at is null, 'goes to secretary inbox');
  r := public.ops_decide(id1, 'approve');
  perform pg_temp.assert_eq(r->>'ok', 'false', 'second approve refused');
  perform pg_temp.assert_eq(r->>'code', 'already_decided', 'already decided code');
  perform pg_temp.assert_eq(r->>'message', '이미 처리된 결정입니다', 'already decided message');
  r := public.ops_decide(id1, 'snooze', 'tomorrow');
  perform pg_temp.assert_eq(r->>'code', 'already_decided', 'cannot snooze an approved decision');
  r := public.ops_decide((select id from public.ops_decision where title = '철회된 결정'), 'approve');
  perform pg_temp.assert_eq(r->>'code', 'already_decided', 'withdrawn stays withdrawn');
  perform pg_temp.assert_eq((select state from public.ops_decision where title = '철회된 결정'), 'withdrawn', 'withdrawn unchanged');
  r := public.ops_decide(999999, 'approve');
  perform pg_temp.assert_eq(r->>'code', 'not_found', 'missing id');
  begin
    perform public.ops_decide(id1, 'reject');
    raise exception 'reject accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.ops_decide((select id from public.ops_decision where title = '보류할 결정'), 'snooze', 'forever');
    raise exception 'bad snooze accepted';
  exception when invalid_parameter_value then null; end;
  perform pg_temp.assert_eq((select state from public.ops_decision where title = '보류할 결정'), 'pending', 'bad snooze changed nothing');
end $$;
reset role;

-- ── 5. 보류(본체, 시각 고정): 대기 → 보류, 보류 중도 다시 보류 가능, 승인도 가능 ─────────
do $$
declare r jsonb; d public.ops_decision%rowtype; id2 bigint := (select id from public.ops_decision where title = '보류할 결정');
begin
  r := private.ops_decide_at(id2, 'snooze', 'tomorrow', '00000000-0000-0000-0000-0000000000a1', '2026-11-06 09:10+09');
  perform pg_temp.assert_eq(r->>'message', '보류 · 11-07 08:00 에 다시 올라옵니다', 'snooze message');
  select * into d from public.ops_decision where id = id2;
  perform pg_temp.assert_eq(d.state, 'snoozed', 'state snoozed');
  perform pg_temp.assert_eq(d.snooze_until::text, ('2026-11-07 08:00+09'::timestamptz)::text, 'snooze until KST 08:00');
  perform pg_temp.assert_eq(d.snooze_count::text, '1', 'snooze_count 1');
  perform pg_temp.assert_true(d.decided_at is null and d.decided_by is null, 'snooze is not a decision');
  r := private.ops_decide_at(id2, 'snooze', '3d', null, '2026-11-07 08:30+09');
  select * into d from public.ops_decision where id = id2;
  perform pg_temp.assert_eq(d.snooze_count::text, '2', 'snoozed again');
  perform pg_temp.assert_eq(d.snooze_until::text, ('2026-11-10 08:00+09'::timestamptz)::text, '3d from 11-07');
  r := private.ops_decide_at((select id from public.ops_decision where title = '보류 중 결정'), 'approve', null, null, '2026-11-06 09:10+09');
  perform pg_temp.assert_eq(r->>'state', 'approved', 'snoozed can be approved');
  perform pg_temp.assert_true((select snooze_until is null from public.ops_decision where title = '보류 중 결정'), 'approve clears snooze_until');
  -- 감사 기록: 성공한 처리만(승인 1 + 보류 2 + 승인 1 = 4), 거부된 시도는 남기지 않는다
  perform pg_temp.assert_eq((select count(*) from public.admin_audit_log where action = 'ops_decide')::text, '4', 'audit rows');
end $$;

-- ── 6. ops_home 반환 shape 불변(123) ──────────────────────────────────────────
select pg_temp.assert_eq(
  (select string_agg(k, ',' order by k) from jsonb_object_keys(private.ops_home_at('2026-11-06 09:10+09')) k),
  'anomalies,decisions,decisions_more,flow,generated_at,publish_approval,summary,teams,tree,upcoming',
  'ops_home keys unchanged');

-- ── 7. 서비스 함수(service_role) ─────────────────────────────────────────────
set local role service_role;
do $$
declare r jsonb; tid bigint;
begin
  r := public.ops_decision_open('sr', '새 결정', '이유', '승인하면', '진행', 'approve', null, 'https://example.test/doc');
  perform pg_temp.assert_true((r->>'id') is not null, 'decision opened');
  perform pg_temp.assert_true((select secretary_seen_at is not null from public.ops_decision where id = (r->>'id')::bigint),
                              'own decision is already seen');
  begin
    perform public.ops_decision_open('sr', '발행', null, null, null, 'approve', null, 'https://github.com/x/y/pull/1?head=publish/brief-2026-11-09');
    raise exception 'publish PR opened as a decision';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.ops_decision_open('nope', 't', null, null, null, 'approve', null, null);
    raise exception 'unknown team accepted';
  exception when invalid_parameter_value then null; end;

  -- 인박스: 처리된 결정 2건(승인·승인) + 보류 1건이 한 번만 나오고, 다시 부르면 빈다
  r := public.ops_secretary_inbox();
  perform pg_temp.assert_eq(jsonb_array_length(r->'decisions')::text, '3', 'inbox decisions');
  r := public.ops_secretary_inbox();
  perform pg_temp.assert_eq(jsonb_array_length(r->'decisions')::text, '0', 'inbox marks read');

  perform public.ops_member_touch('sr-prep', '월 05:30 정상', '2026-11-06 05:30+09');
  perform pg_temp.assert_eq((select last_active_note from public.ops_member where slug = 'sr-prep'), '월 05:30 정상', 'member touched');
  begin
    perform public.ops_member_touch('nobody', null, null);
    raise exception 'unknown member accepted';
  exception when invalid_parameter_value then null; end;

  perform public.ops_goal_set('sr-ontime', 91.5, '11-06 측정', '2026-11-06 09:00+09');
  perform pg_temp.assert_eq((select current_value::text from public.ops_goal where slug = 'sr-ontime'), '91.5', 'goal set');
  begin
    perform public.ops_goal_set('sr-ontime', 101, null, null);
    raise exception 'percent over 100 accepted';
  exception when invalid_parameter_value then null; end;
  perform pg_temp.assert_eq((select current_value::text from public.ops_goal where slug = 'sr-ontime'), '91.5', 'bad value changed nothing');

  insert into public.ops_task (team_id, title, created_kind)
    values ((select id from public.ops_team where slug = 'sr'), '할 일', 'ceo') returning id into tid;
  r := public.ops_secretary_inbox();
  perform pg_temp.assert_eq(jsonb_array_length(r->'tasks')::text, '1', 'inbox new task');
  perform public.ops_task_move(tid, 'doing', 'sr-prep', null);
  perform pg_temp.assert_true((select started_at is not null and member_id is not null from public.ops_task where id = tid), 'task started');
  begin
    perform public.ops_task_move(tid, 'done', null, null);
    raise exception 'done without result link';
  exception when invalid_parameter_value then null; end;
  perform public.ops_task_move(tid, 'done', null, 'https://example.test/pr/1');
  perform pg_temp.assert_true((select done_at is not null from public.ops_task where id = tid), 'task done');
  begin
    perform public.ops_task_move(tid, 'doing', null, null);
    raise exception 'done task reopened';
  exception when invalid_parameter_value then null; end;
end $$;
reset role;

rollback;
\echo 'ops_center_decide.sql: all assertions passed'
