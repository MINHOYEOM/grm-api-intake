\set ON_ERROR_STOP on
-- 운영 센터 131(경보 업무) 행동 시험. 별도 빈 테스트 DB 에서만 실행한다.
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

\ir ../../web/migrations/131_ops_alarm_tasks.sql

create function pg_temp.assert_true(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;
create function pg_temp.assert_eq(got text, want text, message text) returns void language plpgsql as $$
begin
  if got is distinct from want then
    raise exception 'assertion failed: % (got %, want %)', message, coalesce(got, 'NULL'), coalesce(want, 'NULL');
  end if;
end $$;

insert into public.ops_team (slug, name, kind, sort_order) values ('situation-room', '종합상황실', 'office', 1);
insert into public.ops_team (slug, name, kind, sort_order) values ('platform', '플랫폼', 'division', 4);
insert into public.ops_team (slug, name, kind, parent_id, sort_order)
  values ('infra', '수집·인프라팀', 'team', (select id from public.ops_team where slug = 'platform'), 4),
         ('dev', '개발팀', 'team', (select id from public.ops_team where slug = 'platform'), 2);
insert into public.ops_team (slug, name, kind, sort_order, active) values ('old', '없어진 실', 'office', 9, false);

-- ── 1. 권한: 표는 service_role 만 · 함수는 service_role 만 · ops_home 만 회원 호출 허용 ──────────────
select pg_temp.assert_true(not has_table_privilege('anon', 'public.ops_alarm', 'SELECT'), 'anon cannot read ops_alarm');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.ops_alarm', 'SELECT'), 'members cannot read ops_alarm');
select pg_temp.assert_true(has_table_privilege('service_role', 'public.ops_alarm', 'INSERT'), 'service_role writes ops_alarm');
select pg_temp.assert_true((select relrowsecurity from pg_class where oid = 'public.ops_alarm'::regclass), 'ops_alarm has RLS');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.ops_alarm_upsert(integer,text,text,text,text[],text,timestamptz)', 'EXECUTE'), 'anon cannot upsert');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'public.ops_alarm_upsert(integer,text,text,text,text[],text,timestamptz)', 'EXECUTE'), 'members cannot upsert');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'public.ops_alarm_neglect_scan(timestamptz)', 'EXECUTE'), 'members cannot scan');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'public.ops_alarm_neglected(timestamptz)', 'EXECUTE'), 'members cannot list neglected');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'private.ops_alarm_summary(timestamptz)', 'EXECUTE'), 'members cannot call summary');
select pg_temp.assert_true(has_function_privilege('service_role', 'public.ops_alarm_upsert(integer,text,text,text,text[],text,timestamptz)', 'EXECUTE'), 'service_role upserts');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.ops_home()', 'EXECUTE'), 'ops_home still callable by members');

-- ── 2. 새 이슈 -> 지시함 업무 · 멱등(같은 이슈 두 번) ────────────────────────────────────────────
do $$
declare r jsonb; t public.ops_task%rowtype; t0 timestamptz := '2026-11-01 09:00+09';
begin
  r := public.ops_alarm_upsert(1274, '[reconciliation] 원천 대비 수집량 낙차가 감지됐습니다', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274',
                               'open', array['reconciliation','reconcile-mfds'], 'infra', t0);
  perform pg_temp.assert_eq(r->>'action', 'created', 'first call creates');
  select * into t from public.ops_task where id = (r->>'task_id')::bigint;
  perform pg_temp.assert_eq(t.lane, 'inbox', 'lane inbox');
  perform pg_temp.assert_eq(t.reason, 'fix', 'reason fix');
  perform pg_temp.assert_eq(t.created_kind, 'secretary', 'created_kind secretary');
  perform pg_temp.assert_eq(t.title, '[reconciliation] 원천 대비 수집량 낙차가 감지됐습니다', 'title = issue title');
  perform pg_temp.assert_eq(t.result_url, 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'result_url = issue url');
  perform pg_temp.assert_eq(t.team_id::text, (select id::text from public.ops_team where slug = 'infra'), 'team infra');
  perform pg_temp.assert_true(t.secretary_seen_at is null and t.created_by is null, 'secretary will see it, system-created');
  r := public.ops_alarm_upsert(1274, '[reconciliation] 원천 대비 수집량 낙차가 감지됐습니다', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274',
                               'open', array['reconciliation','reconcile-mfds'], 'infra', t0 + interval '1 hour');
  perform pg_temp.assert_eq(r->>'action', 'unchanged', 'second call is a no-op');
  perform pg_temp.assert_eq((select count(*)::text from public.ops_task), '1', 'one task for one issue');
  perform pg_temp.assert_eq((select count(*)::text from public.ops_alarm), '1', 'one link for one issue');
  perform pg_temp.assert_true((select updated_at = t0 from public.ops_task), 'no-op does not reset the 3-day clock');
end $$;

-- 모르는 팀·부문·쉬는 칸·빈 값은 종합상황실로 (이슈 번호 2001~2004)
do $$
declare r jsonb; v text; i int := 2000;
begin
  foreach v in array array['nope', 'platform', 'old', ''] loop
    i := i + 1;
    r := public.ops_alarm_upsert(i, '모르는 팀 ' || v, 'https://github.com/MINHOYEOM/grm-api-intake/issues/' || i, 'open', array['x'], v, '2026-11-01 09:00+09');
    perform pg_temp.assert_eq(r->>'team', 'situation-room', 'fallback team for "' || v || '"');
  end loop;
end $$;

-- 입력 거부
do $$
begin
  begin perform public.ops_alarm_upsert(0, 't', 'https://github.com/a/b/issues/1', 'open', null, 'dev'); raise exception 'accepted issue 0';
  exception when invalid_parameter_value then null; end;
  begin perform public.ops_alarm_upsert(5, 't', 'https://github.com/a/b/issues/1', 'weird', null, 'dev'); raise exception 'accepted bad state';
  exception when invalid_parameter_value then null; end;
  begin perform public.ops_alarm_upsert(5, 't', 'http://evil.example/x', 'open', null, 'dev'); raise exception 'accepted non-github url';
  exception when invalid_parameter_value then null; end;
end $$;

-- 닫힌 채 처음 보는 이슈는 만들지 않는다
do $$
declare r jsonb;
begin
  r := public.ops_alarm_upsert(3000, 'old closed', 'https://github.com/MINHOYEOM/grm-api-intake/issues/3000', 'closed', null, 'dev');
  perform pg_temp.assert_eq(r->>'action', 'skipped_closed', 'closed unknown issue skipped');
  perform pg_temp.assert_true(not exists (select 1 from public.ops_alarm where issue_number = 3000), 'no link for it');
end $$;

-- ── 3. 닫힘 -> 완료 · 재오픈 -> 되살림 ────────────────────────────────────────────────────────
do $$
declare r jsonb; t public.ops_task%rowtype; id1 bigint := (select task_id from public.ops_alarm where issue_number = 1274);
begin
  update public.ops_task set secretary_seen_at = now() where id = id1;
  r := public.ops_alarm_upsert(1274, 'x', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'closed', array['reconciliation'], 'infra', '2026-11-02 09:00+09');
  perform pg_temp.assert_eq(r->>'action', 'closed', 'close');
  select * into t from public.ops_task where id = id1;
  perform pg_temp.assert_eq(t.lane, 'done', 'task done');
  perform pg_temp.assert_true(t.done_at = '2026-11-02 09:00+09', 'done_at set');
  r := public.ops_alarm_upsert(1274, 'x', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'closed', array['reconciliation'], 'infra', '2026-11-03 09:00+09');
  perform pg_temp.assert_eq(r->>'action', 'unchanged', 'closing twice is a no-op');
  r := public.ops_alarm_upsert(1274, '[reconciliation] 다시 열림', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'open', array['reconciliation'], 'infra', '2026-11-04 09:00+09');
  perform pg_temp.assert_eq(r->>'action', 'reopened', 'reopen');
  select * into t from public.ops_task where id = id1;
  perform pg_temp.assert_eq(t.lane, 'inbox', 'back to inbox');
  perform pg_temp.assert_true(t.done_at is null and t.started_at is null and t.secretary_seen_at is null, 'revived clean, secretary re-reads');
  perform pg_temp.assert_true(t.updated_at = '2026-11-04 09:00+09', 'clock restarts at reopen');
  perform pg_temp.assert_eq(t.title, '[reconciliation] 다시 열림', 'title synced');
  perform pg_temp.assert_eq((select count(*)::text from public.ops_task where result_url like '%/issues/1274'), '1', 'still one task');
end $$;

-- 사람이 먼저 업무를 완료로 옮겼는데 이슈는 열림 -> 되살리지 않는다 (이슈 2001)
do $$
declare r jsonb; id1 bigint := (select task_id from public.ops_alarm where issue_number = 2001);
begin
  update public.ops_task set lane = 'done', done_at = now() where id = id1;
  r := public.ops_alarm_upsert(2001, '모르는 팀 nope', 'https://github.com/MINHOYEOM/grm-api-intake/issues/2001', 'open', array['x'], 'nope', '2026-11-05 09:00+09');
  perform pg_temp.assert_eq((select lane from public.ops_task where id = id1), 'done', 'human done is respected while issue open');
end $$;

-- ── 4. 3일 방치 판정(경계) · 이슈당 1회 · 닫으면 방치 업무도 완료 ─────────────────────────────────
do $$
declare r jsonb; n int;
begin
  -- 1274 는 11-04 09:00 에 되살아났다. 정확히 72시간 = 방치 아님, 1초 넘으면 방치.
  select count(*) into n from public.ops_alarm_neglected('2026-11-07 09:00+09') where issue_number = 1274;
  perform pg_temp.assert_eq(n::text, '0', 'exactly 3 days is not neglected');
  select count(*) into n from public.ops_alarm_neglected('2026-11-07 09:00:01+09') where issue_number = 1274;
  perform pg_temp.assert_eq(n::text, '1', '3 days + 1s is neglected');
  -- 사람이 완료로 옮긴 업무는 방치 아님
  select count(*) into n from public.ops_alarm_neglected('2026-11-09 09:00+09') where issue_number = 2001;
  perform pg_temp.assert_eq(n::text, '0', 'done task is not neglected');

  r := public.ops_alarm_neglect_scan('2026-11-08 09:00+09');
  perform pg_temp.assert_eq(jsonb_array_length(r)::text, (select count(*)::text from public.ops_alarm_neglected('2026-11-08 09:00+09')), 'scan flags every neglected issue');
  perform pg_temp.assert_true(exists (select 1 from jsonb_array_elements(r) x where (x->>'issue')::int = 1274 and (x->>'days')::int = 4), '1274 flagged at day 4');
  perform pg_temp.assert_true(exists (select 1 from public.ops_task t join public.ops_team tm on tm.id = t.team_id
                                      where tm.slug = 'situation-room' and t.title like '방치 경보: #1274 %' and t.lane = 'inbox'
                                        and t.result_url like '%/issues/1274'), 'neglect task in situation-room');
  r := public.ops_alarm_neglect_scan('2026-11-09 09:00+09');
  perform pg_temp.assert_eq(jsonb_array_length(r)::text, '0', 'second scan flags nothing (once per issue)');
  perform pg_temp.assert_eq((select count(*)::text from public.ops_task where title like '방치 경보: #1274 %'), '1', 'one neglect task');

  -- 이슈가 닫히면 방치 업무도 완료
  perform public.ops_alarm_upsert(1274, 'x', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'closed', array['reconciliation'], 'infra', '2026-11-10 09:00+09');
  perform pg_temp.assert_eq((select lane from public.ops_task where title like '방치 경보: #1274 %'), 'done', 'neglect task closed with the issue');
  -- 닫힌 뒤 다시 열리면 방치 표시가 초기화되어 다시 올릴 수 있다
  perform public.ops_alarm_upsert(1274, 'x', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'open', array['reconciliation'], 'infra', '2026-11-11 09:00+09');
  perform pg_temp.assert_true((select neglect_flagged_at is null and neglect_task_id is null from public.ops_alarm where issue_number = 1274), 'neglect mark reset on reopen');
end $$;

-- 라벨이 바뀌면 지시함에 있는 업무만 옮긴다
do $$
declare r jsonb; id1 bigint := (select task_id from public.ops_alarm where issue_number = 1274);
begin
  r := public.ops_alarm_upsert(1274, 'x', 'https://github.com/MINHOYEOM/grm-api-intake/issues/1274', 'open', array['reconcile-monitor'], 'dev', '2026-11-12 09:00+09');
  perform pg_temp.assert_eq(r->>'action', 'updated', 'team re-routed');
  perform pg_temp.assert_eq((select tm.slug from public.ops_task t join public.ops_team tm on tm.id = t.team_id where t.id = id1), 'dev', 'now dev');
  perform pg_temp.assert_true((select updated_at = '2026-11-11 09:00+09' from public.ops_task where id = id1), 're-route keeps the clock');
end $$;

-- ── 5. ops_home: 기존 키는 그대로 + alarms 만 가산 ────────────────────────────────────────────
set local role authenticated;
select set_config('test.is_admin', 'on', true);
select set_config('test.home', public.ops_home()::text, true);
reset role;
create temp table h as select current_setting('test.home')::jsonb as j;
select pg_temp.assert_eq(
  (select string_agg(k, ',' order by k) from h, jsonb_object_keys(j) k),
  'alarms,anomalies,decisions,decisions_more,flow,generated_at,publish_approval,summary,teams,tree,upcoming',
  'ops_home keys = old keys + alarms');
select pg_temp.assert_eq(
  (select string_agg(k, ',' order by k) from h, jsonb_object_keys(j->'alarms') k), 'items,neglect_days,neglected,open',
  'alarms shape');
select pg_temp.assert_eq((select (j->'alarms'->>'neglect_days') from h), '3', 'neglect_days 3');
-- 원래 본체 반환은 그대로(123 키 10개)
select pg_temp.assert_eq(
  (select string_agg(k, ',' order by k) from jsonb_object_keys(private.ops_home_at(now())) k),
  'anomalies,decisions,decisions_more,flow,generated_at,publish_approval,summary,teams,tree,upcoming', 'ops_home_at unchanged');

-- 요약: 방치 수와 항목
do $$
declare s jsonb;
begin
  s := private.ops_alarm_summary('2026-11-20 09:00+09');
  perform pg_temp.assert_true((s->>'neglected')::int >= 1, 'summary counts neglected');
  perform pg_temp.assert_true(jsonb_array_length(s->'items') <= 5, 'items capped at 5');
  perform pg_temp.assert_true((s->'items'->0->>'days')::int >= 8, 'item carries days');
  perform pg_temp.assert_eq(private.ops_alarm_summary('2026-11-01 10:00+09')->>'neglected', '0', 'nothing neglected right after creation');
end $$;

-- 관리자 아님 -> 42501
set local role authenticated;
select set_config('test.is_admin', 'off', true);
do $$
begin
  begin perform public.ops_home(); raise exception 'non-admin read ops_home';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

rollback;
\echo 'ops_center_alarm.sql: all assertions passed'
