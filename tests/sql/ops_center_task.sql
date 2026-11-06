\set ON_ERROR_STOP on
-- 운영 센터 126(업무 지시 쓰기) 행동 시험. 별도 빈 테스트 DB 에서만 실행한다.
-- CI: .github/workflows/grm-ops-center-sql-check.yml · 뮤테이션: tests/run_ops_center_sql_mutations.py
begin;

-- ── Supabase 흉내(ops_center_decide.sql 과 같은 바탕) ─────────────────────────
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
create table public.admin_audit_log (
  id bigint generated always as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null, target_type text, target_id text,
  details jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());
revoke all on public.admin_audit_log from anon, authenticated;

\ir ../../web/migrations/122_ops_center_tables.sql
\ir ../../web/migrations/123_ops_home.sql
\ir ../../web/migrations/125_ops_decision_write.sql
\ir ../../web/migrations/126_ops_task_create.sql

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
insert into public.ops_team (slug, name, kind, sort_order) values ('cust', '고객', 'division', 5);
insert into public.ops_team (slug, name, kind, parent_id, sort_order)
  values ('outreach', '대외홍보팀', 'team', (select id from public.ops_team where slug = 'cust'), 1);
insert into public.ops_team (slug, name, kind, sort_order, active) values ('old', '없어진 실', 'office', 9, false);
insert into public.ops_member (team_id, slug, name, kind)
  values ((select id from public.ops_team where slug = 'sr'), 'sr-prep', '발행 준비 담당', 'routine'),
         ((select id from public.ops_team where slug = 'outreach'), 'pr-cards', '링크드인 담당', 'session');
insert into public.ops_member (team_id, slug, name, kind, state)
  values ((select id from public.ops_team where slug = 'sr'), 'sr-gone', '은퇴한 담당', 'routine', 'retired');

-- ── 1. 권한 ─────────────────────────────────────────────────────────────────
select pg_temp.assert_true(not has_function_privilege('anon', 'public.ops_task_create(text,text,text,date,text)', 'EXECUTE'), 'anon cannot call ops_task_create');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.ops_task_create(text,text,text,date,text)', 'EXECUTE'), 'authenticated may call ops_task_create');
select pg_temp.assert_true(not has_function_privilege('service_role', 'public.ops_task_create(text,text,text,date,text)', 'EXECUTE'), 'service_role not granted (screen-only function)');
select pg_temp.assert_true((select prosecdef from pg_proc where oid = 'public.ops_task_create(text,text,text,date,text)'::regprocedure), 'ops_task_create is security definer');
select pg_temp.assert_true((select proconfig::text like '%search_path=public%' from pg_proc where oid = 'public.ops_task_create(text,text,text,date,text)'::regprocedure), 'ops_task_create fixes search_path');
select pg_temp.assert_true((select proconfig::text like '%search_path=public%' from pg_proc where oid = 'private.ops_task_create_at(text,text,text,date,text,uuid,timestamptz)'::regprocedure), 'body fixes search_path');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'private.ops_task_create_at(text,text,text,date,text,uuid,timestamptz)', 'EXECUTE'), 'members cannot call the body');
select pg_temp.assert_true(not has_function_privilege('anon', 'private.ops_task_create_at(text,text,text,date,text,uuid,timestamptz)', 'EXECUTE'), 'anon cannot call the body');

-- ── 2. 관리자 아님 → 42501, 행·감사 기록 없음 ──────────────────────────────────
set local role authenticated;
select set_config('test.uid', '00000000-0000-0000-0000-0000000000a1', true);
do $$
declare v text;
begin
  foreach v in array array['off', 'null'] loop
    perform set_config('test.is_admin', v, true);
    begin
      perform public.ops_task_create('sr', '관리자 아닌 지시', null, null, null);
      raise exception 'non-admin (%) created a task', v;
    exception when insufficient_privilege then null; end;
  end loop;
end $$;
-- 관리자도 표에 직접은 못 쓴다(쓰기는 함수만)
select set_config('test.is_admin', 'on', true);
do $$
begin
  begin
    insert into public.ops_task (team_id, title, created_kind) values ((select id from public.ops_team where slug = 'sr'), '직접', 'ceo');
    raise exception 'admin inserted ops_task directly';
  exception when insufficient_privilege then null; end;
end $$;

-- ── 3. 화면 경로로 한 건: 지시함 · 대표 지시 · 만든 사람 = 호출자 ─────────────────
do $$
declare r jsonb; t public.ops_task%rowtype;
begin
  r := public.ops_task_create('outreach', '  구독 안내 명단 정리  ', '  지난달 신규만  ', null, 'pr-cards');
  perform pg_temp.assert_eq(r->>'ok', 'true', 'create ok');
  perform pg_temp.assert_eq(r->>'message', '지시했습니다 · 비서실이 읽고 담당을 정합니다', 'create message');
  select * into t from public.ops_task where id = (r->>'id')::bigint;
  perform pg_temp.assert_eq(t.title, '구독 안내 명단 정리', 'title trimmed');
  perform pg_temp.assert_eq(t.detail, '지난달 신규만', 'detail trimmed');
  perform pg_temp.assert_eq(t.lane, 'inbox', 'lane inbox');
  perform pg_temp.assert_eq(t.reason, 'directive', 'reason directive');
  perform pg_temp.assert_eq(t.created_kind, 'ceo', 'created_kind ceo');
  perform pg_temp.assert_eq(t.created_by::text, '00000000-0000-0000-0000-0000000000a1', 'created_by = caller');
  perform pg_temp.assert_eq(t.team_id::text, (select id::text from public.ops_team where slug = 'outreach'), 'team');
  perform pg_temp.assert_eq(t.member_id::text, (select id::text from public.ops_member where slug = 'pr-cards'), 'member');
  perform pg_temp.assert_true(t.secretary_seen_at is null, 'secretary has not seen it');
  perform pg_temp.assert_true(t.started_at is null and t.done_at is null, 'not started');
end $$;
reset role;

-- ── 4. 거부(본체, 시각 고정): 팀 필수·모르는 팀·부문·쉬는 칸·길이·마감·소속 ─────────
do $$
declare r jsonb; n0 bigint := (select count(*) from public.ops_task); a0 bigint := (select count(*) from public.admin_audit_log);
  uid uuid := '00000000-0000-0000-0000-0000000000a1'; t0 timestamptz := '2026-11-06 09:10:00+09';
begin
  r := private.ops_task_create_at(null, '팀 없는 지시', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'no_team', 'null team refused');
  perform pg_temp.assert_eq(r->>'ok', 'false', 'refusal is ok:false');
  r := private.ops_task_create_at('  ', '팀 없는 지시', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'no_team', 'blank team refused');
  r := private.ops_task_create_at('nope', '모르는 팀', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'unknown_team', 'unknown team refused');
  r := private.ops_task_create_at('old', '쉬는 칸', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'unknown_team', 'inactive team refused');
  r := private.ops_task_create_at('cust', '부문에 지시', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'not_a_team', 'division refused');
  r := private.ops_task_create_at('sr', '', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'title_length', 'empty title refused');
  r := private.ops_task_create_at('sr', '   ', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'title_length', 'blank title refused');
  r := private.ops_task_create_at('sr', null, null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'title_length', 'null title refused');
  r := private.ops_task_create_at('sr', repeat('가', 121), null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'title_length', '121 chars refused');
  r := private.ops_task_create_at('sr', '긴 내용', repeat('나', 2001), null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'detail_length', '2001-char detail refused');
  r := private.ops_task_create_at('sr', '지난 마감', null, date '2026-11-05', null, uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'due_past', 'past due refused');
  -- KST 11-07 00:30 = UTC 11-06 15:30 — UTC 날짜로 보면 11-06 마감이 '오늘'로 통과해 버린다
  r := private.ops_task_create_at('sr', '어제 마감', null, date '2026-11-06', null, uid, '2026-11-07 00:30:00+09');
  perform pg_temp.assert_eq(r->>'code', 'due_past', 'KST yesterday refused right after midnight');
  r := private.ops_task_create_at('sr', '남의 팀 직원', null, null, 'pr-cards', uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'member_team', 'member of another team refused');
  r := private.ops_task_create_at('sr', '없는 직원', null, null, 'ghost', uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'member_team', 'unknown member refused');
  r := private.ops_task_create_at('sr', '은퇴한 직원', null, null, 'sr-gone', uid, t0);
  perform pg_temp.assert_eq(r->>'code', 'member_team', 'retired member refused');
  perform pg_temp.assert_eq((select count(*) from public.ops_task)::text, n0::text, 'refusals insert nothing');
  perform pg_temp.assert_eq((select count(*) from public.admin_audit_log)::text, a0::text, 'refusals are not audited');

  -- 경계: 120자·2000자·KST 오늘 마감·실(office)·직원 없음은 받는다
  r := private.ops_task_create_at('sr', repeat('가', 120), repeat('나', 2000), date '2026-11-06', null, uid, t0);
  perform pg_temp.assert_eq(r->>'ok', 'true', '120/2000 chars, due today accepted');
  perform pg_temp.assert_true((select member_id is null from public.ops_task where id = (r->>'id')::bigint), 'no member = secretary assigns');
  -- KST 00:30 = UTC 전날 15:30. 마감 '오늘'은 KST 날짜로 본다
  r := private.ops_task_create_at('sr', '자정 직후 지시', null, date '2026-11-07', null, uid, '2026-11-07 00:30:00+09');
  perform pg_temp.assert_eq(r->>'ok', 'true', 'due today in KST right after midnight');
  r := private.ops_task_create_at('sr', '같은 팀 직원', null, null, 'sr-prep', uid, t0);
  perform pg_temp.assert_eq(r->>'ok', 'true', 'member of the same team accepted');
end $$;

-- ── 5. 더블 탭: 같은 제목 10초 안 거부, 10초 지나면 받음 ──────────────────────────
do $$
declare r jsonb; uid uuid := '00000000-0000-0000-0000-0000000000a1'; t0 timestamptz := '2026-11-06 10:00:00+09';
begin
  r := private.ops_task_create_at('sr', '주간 점검 정리', null, null, null, uid, t0);
  perform pg_temp.assert_eq(r->>'ok', 'true', 'first send');
  r := private.ops_task_create_at('sr', '주간 점검 정리', null, null, null, uid, t0 + interval '1 second');
  perform pg_temp.assert_eq(r->>'code', 'duplicate', 'same title 1s later refused');
  r := private.ops_task_create_at('outreach', ' 주간 점검 정리 ', null, null, null, uid, t0 + interval '9 seconds');
  perform pg_temp.assert_eq(r->>'code', 'duplicate', 'same title (trimmed) to another team 9s later refused');
  r := private.ops_task_create_at('sr', '다른 제목', null, null, null, uid, t0 + interval '2 seconds');
  perform pg_temp.assert_eq(r->>'ok', 'true', 'different title accepted');
  r := private.ops_task_create_at('sr', '주간 점검 정리', null, null, null, uid, t0 + interval '11 seconds');
  perform pg_temp.assert_eq(r->>'ok', 'true', 'same title after 10s accepted');
  perform pg_temp.assert_eq((select count(*) from public.ops_task where title = '주간 점검 정리')::text, '2', 'two rows, not three');
end $$;

-- ── 6. 감사 기록: 성공한 지시만, 한 건에 한 줄 ────────────────────────────────────
-- 성공: 화면 1 + 경계 3 + 더블 탭 3 = 7
select pg_temp.assert_eq((select count(*)::text from public.admin_audit_log where action = 'ops_task_create'), '7', 'audit rows = successful creates');
select pg_temp.assert_eq((select count(*)::text from public.ops_task), '7', 'task rows = successful creates');
select pg_temp.assert_eq((select count(*)::text from public.admin_audit_log a join public.ops_task t on t.id::text = a.target_id
                          where a.action = 'ops_task_create' and a.target_type = 'ops_task'
                            and a.actor_user_id = '00000000-0000-0000-0000-0000000000a1' and a.details->>'title' = t.title), '7',
                         'each audit row points at its task');
select pg_temp.assert_eq((select details->>'team' from public.admin_audit_log a join public.ops_task t on t.id::text = a.target_id
                          where t.title = '구독 안내 명단 정리'), 'outreach', 'audit details carry the team');

-- ── 7. 홈에 나타남(지시함) · 비서실 인박스에 뜸 · ops_home shape 불변 ────────────────
create temp table h as select private.ops_home_at('2026-11-06 12:00:00+09') as j;
select pg_temp.assert_true((select bool_or(x->>'title' = '구독 안내 명단 정리' and x->>'lane' = 'inbox' and x->>'created_kind' = 'ceo'
                                           and x->>'team' = 'outreach' and x->>'member' = 'pr-cards' and x->>'secretary_seen' = 'false')
                            from h, jsonb_array_elements(j->'flow'->'todo') x), 'directive shows in flow.todo');
select pg_temp.assert_eq((select jsonb_array_length(j->'flow'->'todo')::text from h), '7', 'all directives in the inbox lane');
select pg_temp.assert_eq(
  (select string_agg(k, ',' order by k) from h, jsonb_object_keys(j) k),
  'anomalies,decisions,decisions_more,flow,generated_at,publish_approval,summary,teams,tree,upcoming',
  'ops_home keys unchanged');
set local role service_role;
do $$
declare r jsonb;
begin
  r := public.ops_secretary_inbox();
  perform pg_temp.assert_eq(jsonb_array_length(r->'tasks')::text, '7', 'secretary inbox gets every new directive');
end $$;
reset role;
select pg_temp.assert_true((select bool_and(x->>'secretary_seen' = 'true')
                            from jsonb_array_elements(private.ops_home_at('2026-11-06 12:00:00+09')->'flow'->'todo') x),
                           'after inbox read the home says seen');

rollback;
\echo 'ops_center_task.sql: all assertions passed'
