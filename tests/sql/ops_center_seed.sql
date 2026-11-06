\set ON_ERROR_STOP on
-- 운영 센터 124 시드 행동 시험. 별도 빈 테스트 DB 에서만 실행한다(실서비스 SQL 에디터에 붙이지 않는다).
-- 122·123 위에 124 를 두 번 적용해 행 수가 같은지, ops_home 결과가 설계서 부록 C 의 예상과 같은지,
-- 적용 뒤 새로 잰 값을 세 번째 적용이 되돌리지 않는지 본다. CI: .github/workflows/grm-ops-center-sql-check.yml
begin;

-- ── Supabase 흉내(tests/sql/ops_center.sql 과 같음) ─────────────────────────
create role anon;
create role authenticated;
create role service_role bypassrls;
create schema auth;
create table auth.users (id uuid primary key);
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

\ir ../../web/migrations/122_ops_center_tables.sql
\ir ../../web/migrations/123_ops_home.sql

create function pg_temp.assert_eq(got text, want text, message text) returns void language plpgsql as $$
begin
  if got is distinct from want then
    raise exception 'assertion failed: % (got %, want %)', message, coalesce(got, 'NULL'), coalesce(want, 'NULL');
  end if;
end $$;

-- ── 1. 첫 적용 ──────────────────────────────────────────────────────────────
\ir ../../web/migrations/124_ops_center_seed.sql
create temp table c1 as
select (select count(*) from public.ops_team)   as teams,
       (select count(*) from public.ops_member) as members,
       (select count(*) from public.ops_goal)   as goals;
select pg_temp.assert_eq((select teams::text from c1), '14', 'teams 14');
select pg_temp.assert_eq((select members::text from c1), '15', 'members 15');
select pg_temp.assert_eq((select goals::text from c1), '23', 'goals 23');
select pg_temp.assert_eq((select string_agg(kind || '=' || n, ',' order by kind)
                          from (select kind, count(*) n from public.ops_team group by kind) x),
                         'division=3,office=2,team=9', 'office 2 · division 3 · team 9');
select pg_temp.assert_eq((select count(*)::text from public.ops_team c join public.ops_team p on p.id = c.parent_id
                          where c.kind = 'team' and p.kind = 'division'), '9', 'every team under a division');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal where current_value is null), '10', 'unmeasured goals stay null');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal where target is null), '2', 'undecided targets');
select pg_temp.assert_eq((select string_agg(slug, ',' order by slug) from public.ops_member where idle_exempt),
                         'dev-session,strat-card-history', 'idle_exempt only on-call (D6)');

-- ── 2. 두 번째 적용: 행 수 같음 ─────────────────────────────────────────────
\ir ../../web/migrations/124_ops_center_seed.sql
select pg_temp.assert_eq((select count(*)::text from public.ops_team),   (select teams::text from c1),   'rerun teams same');
select pg_temp.assert_eq((select count(*)::text from public.ops_member), (select members::text from c1), 'rerun members same');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal),   (select goals::text from c1),   'rerun goals same');

-- ── 3. 시드 직후 홈(부록 C 의 '오늘' = 11-06 저녁) ──────────────────────────
create temp table h as select private.ops_home_at(timestamptz '2026-11-06 18:00:00+09') as j;
select pg_temp.assert_eq((select string_agg(k || '=' || (j->'teams'->k->>'state'), ',' order by k)
                          from h, jsonb_object_keys(j->'teams') k),
  'brief=ok,dev=warn,education=unknown,growth=unknown,infra=unknown,outreach=unknown,quality=warn,'
  || 'situation-room=unknown,strategy=ok,translation=warn,verification=ok', 'team states = 부록 C');
select pg_temp.assert_eq((select string_agg(t->>'slug' || '=' || (t->>'state'), ',' order by o)
                          from h, jsonb_array_elements(j->'tree') with ordinality x(t, o)),
  'situation-room=unknown,verification=ok,research=warn,platform=warn,customer=unknown', 'tree order + division states');
select pg_temp.assert_eq((select j->'summary'->>'bad' from h), '0', 'summary bad 0');
select pg_temp.assert_eq((select j->'summary'->>'warn' from h), '3', 'summary warn 3');
select pg_temp.assert_eq((select j->'summary'->>'ok_teams' from h), '3', 'summary ok 3');
select pg_temp.assert_eq((select j->'summary'->>'unknown_teams' from h), '5', 'summary unknown 5');
select pg_temp.assert_eq((select j->'summary'->>'idle' from h), '0', 'nobody idle');
select pg_temp.assert_eq((select j->'summary'->>'no_record' from h), '1', 'translator has no record');
select pg_temp.assert_eq((select a->>'member' from h, jsonb_array_elements(j->'anomalies') x(a) where a->>'kind' = 'no_record'),
                         'tr-daily', 'no_record member');
select pg_temp.assert_eq((select j->'teams'->'quality'->>'worst_goal' from h), 'dq-term-min', 'quality worst goal');
select pg_temp.assert_eq((select j->'teams'->'quality'->>'reason' from h), '용어 연결률(가장 낮은 유형 483) 50.2% · 목표 70%', 'quality reason');
select pg_temp.assert_eq((select j->'teams'->'dev'->>'worst_goal' from h), 'dev-link-streak', 'dev worst goal');
select pg_temp.assert_eq((select j->'teams'->'translation'->>'worst_goal' from h), 'tr-same-day', 'translation worst goal');
select pg_temp.assert_eq((select j->'teams'->'outreach'->'goals'->1->>'unknown_reason' from h), 'no_target', 'pr-subs no target');
select pg_temp.assert_eq((select j->'teams'->'strategy'->>'blurb' from h), '새 기능을 시험해 보고 쓸지 정합니다', 'blurb passes through');
select pg_temp.assert_eq((select jsonb_array_length(j->'teams'->'brief'->'members')::text from h), '3', 'brief members');
select pg_temp.assert_eq((select j->'summary'->>'decisions' from h), '0', 'no decisions seeded');
select pg_temp.assert_eq((select j->'summary'->>'deadlines' from h), '0', 'no tasks seeded');

-- ── 4. 시간이 지나면 시드 값은 전부 '측정 필요'(거짓 초록 없음) ────────────────
create temp table h2 as select private.ops_home_at(timestamptz '2027-01-01 09:00:00+09') as j;
select pg_temp.assert_eq((select j->'summary'->>'ok_teams' from h2), '0', 'aged seed: no ok');
select pg_temp.assert_eq((select j->'summary'->>'warn' from h2), '0', 'aged seed: no warn');
select pg_temp.assert_eq((select j->'summary'->>'unknown_teams' from h2), '11', 'aged seed: all unknown');

-- ── 5. 새로 잰 값은 시드 재실행이 되돌리지 않는다 ────────────────────────────
update public.ops_goal set current_value = 61, measured_at = timestamptz '2026-11-07 03:30+09', note = '11-07 자동 측정'
 where slug = 'dq-term-min';
update public.ops_goal set current_value = 5, measured_at = timestamptz '2026-11-07 03:30+09' where slug = 'tr-backlog';
update public.ops_member set last_active_at = timestamptz '2026-11-07 04:10+09', last_active_note = '11-07 번역 완료'
 where slug = 'tr-daily';
update public.ops_goal set label = '틀린 이름' where slug = 'dev-ci';
\ir ../../web/migrations/124_ops_center_seed.sql
select pg_temp.assert_eq((select trim_scale(current_value)::text from public.ops_goal where slug = 'dq-term-min'), '61', 'newer value kept');
select pg_temp.assert_eq((select note from public.ops_goal where slug = 'dq-term-min'), '11-07 자동 측정', 'newer note kept');
select pg_temp.assert_eq((select trim_scale(current_value)::text from public.ops_goal where slug = 'tr-backlog'), '5', 'value over seed null kept');
select pg_temp.assert_eq((select last_active_note from public.ops_member where slug = 'tr-daily'), '11-07 번역 완료', 'newer activity kept');
select pg_temp.assert_eq((select label from public.ops_goal where slug = 'dev-ci'), '전체 테스트 시간', 'definition restored by seed');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal), '23', 'third run goals same');

rollback;
\echo 'ops_center_seed.sql: all assertions passed'
