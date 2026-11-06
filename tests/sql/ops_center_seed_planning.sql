\set ON_ERROR_STOP on
-- 운영 센터 127 시드 추가 행동 시험. 별도 빈 테스트 DB 에서만 실행한다(실서비스 SQL 에디터에 붙이지 않는다).
-- 122·123 위에 124 → 127 을 적용하고, 127 을 한 번 더(멱등), 그다음 124 → 127 을 다시 돌려(124 가 되돌린 이름을
-- 127 이 다시 고치는지) 본다. CI: .github/workflows/grm-ops-center-sql-check.yml
begin;

-- ── Supabase 흉내(tests/sql/ops_center_seed.sql 과 같음) ─────────────────────
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

-- ── 1. 124 → 127 ────────────────────────────────────────────────────────────
\ir ../../web/migrations/124_ops_center_seed.sql
\ir ../../web/migrations/127_ops_center_seed_planning.sql
create temp table c1 as
select (select count(*) from public.ops_team)   as teams,
       (select count(*) from public.ops_member) as members,
       (select count(*) from public.ops_goal)   as goals;
select pg_temp.assert_eq((select teams::text from c1), '16', 'teams 14 + 2');
select pg_temp.assert_eq((select members::text from c1), '18', 'members 15 + 3');
select pg_temp.assert_eq((select goals::text from c1), '26', 'goals 23 + 3');
select pg_temp.assert_eq((select string_agg(kind || '=' || n, ',' order by kind)
                          from (select kind, count(*) n from public.ops_team group by kind) x),
                         'division=3,office=3,team=10', 'office 3 · division 3 · team 10');
select pg_temp.assert_eq((select name || '/' || is_pilot from public.ops_team where slug = 'strategy'), '제품전략팀/false', 'strategy is a regular team');
select pg_temp.assert_eq((select label from public.ops_goal where slug = 'strat-adopted'), '채택한 아이디어 수', 'strat-adopted label');
select pg_temp.assert_eq((select trim_scale(current_value)::text from public.ops_goal where slug = 'strat-adopted'), '1', 'strat-adopted value kept');
select pg_temp.assert_eq((select p.slug from public.ops_team c join public.ops_team p on p.id = c.parent_id where c.slug = 'support'),
                         'customer', 'support under customer');
select pg_temp.assert_eq((select string_agg(c.slug, ',' order by c.sort_order) from public.ops_team c
                          join public.ops_team p on p.id = c.parent_id where p.slug = 'customer'),
                         'outreach,growth,support', 'support is third in customer');
select pg_temp.assert_eq((select kind || '/' || coalesce(parent_id::text, 'top') || '/' || sort_order from public.ops_team where slug = 'planning'),
                         'office/top/0', 'planning is a top office, first');
select pg_temp.assert_eq((select string_agg(m.slug || ':' || t.slug || ':' || m.kind || ':' || coalesce(m.model, '-'), ',' order by m.slug)
                          from public.ops_member m join public.ops_team t on t.id = m.team_id
                          where m.slug in ('plan-review','plan-policy','support-desk')),
                         'plan-policy:planning:session:opus,plan-review:planning:session:opus,support-desk:support:session:sonnet',
                         'new members');
select pg_temp.assert_eq((select count(*)::text from public.ops_member
                          where slug in ('plan-review','plan-policy','support-desk') and last_active_at is null), '3', 'no invented activity');
select pg_temp.assert_eq((select string_agg(slug || ':' || direction || ':' || target || '/' || coalesce(warn_at::text, '-') || '/' || bad_at
                                            || ':' || period_hours || ':' || measure_kind || ':' || sort_order, ',' order by slug)
                          from public.ops_goal where slug in ('plan-review-gap','plan-risks-open','support-reply')),
                         'plan-review-gap:lte:7/10/14:168:manual:1,plan-risks-open:lte:0/-/1:168:manual:2,support-reply:lte:48/72/168:168:manual:1',
                         'new goal lines');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal
                          where slug in ('plan-review-gap','plan-risks-open','support-reply') and current_value is null and measured_at is null),
                         '3', 'new goals unmeasured');
select pg_temp.assert_eq((select count(*)::text from public.ops_task), '0', 'no tasks seeded');
select pg_temp.assert_eq((select count(*)::text from public.ops_decision), '0', 'no decisions seeded');

-- 홈: 경영기획실이 맨 앞, 독자지원팀은 고객 방 셋째, 새 칸은 '측정 필요'(값 없음)
create temp table h as select private.ops_home_at(timestamptz '2026-11-06 18:00:00+09') as j;
select pg_temp.assert_eq((select string_agg(t->>'slug', ',' order by o) from h, jsonb_array_elements(j->'tree') with ordinality x(t, o)),
                         'planning,situation-room,verification,research,platform,customer', 'tree order');
select pg_temp.assert_eq((select string_agg(c->>'slug', ',' order by o) from h, jsonb_array_elements(j->'tree') t,
                          jsonb_array_elements(t->'children') with ordinality y(c, o) where t->>'slug' = 'customer'),
                         'outreach,growth,support', 'customer room order');
select pg_temp.assert_eq((select j->'teams'->'planning'->>'state' from h), 'unknown', 'planning unmeasured');
select pg_temp.assert_eq((select j->'teams'->'support'->>'state' from h), 'unknown', 'support unmeasured');
select pg_temp.assert_eq((select j->'teams'->'strategy'->>'name' from h), '제품전략팀', 'home shows the new name');
select pg_temp.assert_eq((select (j->'teams'->'strategy'->>'state') from h), 'ok', 'strategy still ok');
select pg_temp.assert_eq((select count(*)::text from h, jsonb_object_keys(j->'teams')), '13', 'stateful cells 11 + 2');

-- ── 2. 127 을 한 번 더: 행 수 같음 ──────────────────────────────────────────
\ir ../../web/migrations/127_ops_center_seed_planning.sql
select pg_temp.assert_eq((select count(*)::text from public.ops_team),   (select teams::text from c1),   'rerun teams same');
select pg_temp.assert_eq((select count(*)::text from public.ops_member), (select members::text from c1), 'rerun members same');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal),   (select goals::text from c1),   'rerun goals same');

-- ── 3. 운영 DB 처럼 값이 생긴 뒤 124 → 127 다시: 이름은 127 값, 새로 잰 값은 그대로 ─────────
update public.ops_goal set current_value = 3, measured_at = timestamptz '2026-11-08 09:00+09', note = '11-08 경영 점검'
 where slug = 'plan-review-gap';
update public.ops_goal set current_value = 2, measured_at = timestamptz '2026-11-08 09:00+09', note = '11-08 대표 승인 2건'
 where slug = 'strat-adopted';
update public.ops_member set last_active_at = timestamptz '2026-11-08 09:00+09', last_active_note = '11-08 첫 점검'
 where slug = 'plan-review';
\ir ../../web/migrations/124_ops_center_seed.sql
select pg_temp.assert_eq((select name from public.ops_team where slug = 'strategy'), '제품전략팀(시범)', '124 alone reverts the name (why 127 must follow)');
\ir ../../web/migrations/127_ops_center_seed_planning.sql
select pg_temp.assert_eq((select name || '/' || is_pilot from public.ops_team where slug = 'strategy'), '제품전략팀/false', '124 → 127 ends as 제품전략팀');
select pg_temp.assert_eq((select label from public.ops_goal where slug = 'strat-adopted'), '채택한 아이디어 수', '124 → 127 label');
select pg_temp.assert_eq((select trim_scale(current_value)::text || '/' || note from public.ops_goal where slug = 'strat-adopted'),
                         '2/11-08 대표 승인 2건', 'newer strat-adopted value kept through 124 and 127');
select pg_temp.assert_eq((select trim_scale(current_value)::text || '/' || note from public.ops_goal where slug = 'plan-review-gap'),
                         '3/11-08 경영 점검', 'newer plan value kept');
select pg_temp.assert_eq((select last_active_note from public.ops_member where slug = 'plan-review'), '11-08 첫 점검', 'newer activity kept');
select pg_temp.assert_eq((select count(*)::text from public.ops_team),   (select teams::text from c1),   'after 124→127 teams same');
select pg_temp.assert_eq((select count(*)::text from public.ops_member), (select members::text from c1), 'after 124→127 members same');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal),   (select goals::text from c1),   'after 124→127 goals same');

rollback;
\echo 'ops_center_seed_planning.sql: all assertions passed'
