\set ON_ERROR_STOP on
-- 운영 센터 122·123 행동 시험. 별도 빈 테스트 DB 에서만 실행한다(실서비스 SQL 에디터에 붙이지 않는다).
-- CI: .github/workflows/grm-ops-center-sql-check.yml · 뮤테이션: tests/run_ops_center_sql_mutations.py
begin;

-- ── Supabase 흉내 ───────────────────────────────────────────────────────────
create role anon;
create role authenticated;
create role service_role bypassrls;
create schema auth;
create table auth.users (id uuid primary key);
create schema private;
grant usage on schema private to authenticated;          -- 001 과 같음
grant usage on schema public to anon, authenticated, service_role;
-- Supabase 는 새 객체에 anon·authenticated 권한을 기본으로 붙인다 — 그래서 명시 회수가 필요하다(10-28).
-- 시험에서도 같은 기본 권한을 걸어 두어야 '회수 누락'이 빨갛게 드러난다.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
alter default privileges in schema private grant execute on functions to anon, authenticated;
-- 관리자 판정 대역: test.is_admin = on | off | null
create function private.is_admin() returns boolean language sql stable as $$
  select case current_setting('test.is_admin', true) when 'on' then true when 'null' then null else false end
$$;
grant execute on function private.is_admin() to authenticated;

\ir ../../web/migrations/122_ops_center_tables.sql
\ir ../../web/migrations/123_ops_home.sql

create function pg_temp.assert_true(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;
create function pg_temp.assert_eq(got text, want text, message text) returns void language plpgsql as $$
begin
  if got is distinct from want then
    raise exception 'assertion failed: % (got %, want %)', message, coalesce(got, 'NULL'), coalesce(want, 'NULL');
  end if;
end $$;

-- ── 1. 권한(표·시퀀스·함수) ─────────────────────────────────────────────────
do $$
declare t text;
begin
  foreach t in array array['ops_team','ops_member','ops_goal','ops_task','ops_decision'] loop
    perform pg_temp.assert_true((select relrowsecurity from pg_class where oid = ('public.' || t)::regclass), t || ' RLS on');
    perform pg_temp.assert_true(not has_table_privilege('anon', 'public.' || t, 'SELECT'), 'anon cannot read ' || t);
    perform pg_temp.assert_true(has_table_privilege('authenticated', 'public.' || t, 'SELECT'), 'authenticated SELECT ' || t);
    perform pg_temp.assert_true(not has_table_privilege('authenticated', 'public.' || t, 'INSERT'), 'authenticated no INSERT ' || t);
    perform pg_temp.assert_true(not has_table_privilege('authenticated', 'public.' || t, 'UPDATE'), 'authenticated no UPDATE ' || t);
    perform pg_temp.assert_true(not has_table_privilege('authenticated', 'public.' || t, 'DELETE'), 'authenticated no DELETE ' || t);
    perform pg_temp.assert_true(has_table_privilege('service_role', 'public.' || t, 'INSERT'), 'service_role INSERT ' || t);
    perform pg_temp.assert_true(not has_sequence_privilege('authenticated', pg_get_serial_sequence('public.' || t, 'id'), 'USAGE'), 'authenticated no seq ' || t);
    perform pg_temp.assert_true(not has_sequence_privilege('anon', pg_get_serial_sequence('public.' || t, 'id'), 'USAGE'), 'anon no seq ' || t);
    perform pg_temp.assert_true(has_sequence_privilege('service_role', pg_get_serial_sequence('public.' || t, 'id'), 'USAGE'), 'service_role seq ' || t);
  end loop;
end $$;
select pg_temp.assert_true(not has_function_privilege('anon', 'public.ops_home()', 'EXECUTE'), 'anon cannot call ops_home');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.ops_home()', 'EXECUTE'), 'authenticated may call ops_home');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'private.ops_home_at(timestamptz)', 'EXECUTE'), 'authenticated cannot call ops_home_at');
select pg_temp.assert_true(not has_function_privilege('anon', 'private.ops_home_at(timestamptz)', 'EXECUTE'), 'anon cannot call ops_home_at');
select pg_temp.assert_true(not has_function_privilege('authenticated',
  'private.ops_goal_state(text,numeric,numeric,numeric,numeric,timestamptz,integer,timestamptz)', 'EXECUTE'), 'authenticated cannot call ops_goal_state');
select pg_temp.assert_true(not has_function_privilege('anon', 'private.ops_state_rank(text)', 'EXECUTE'), 'anon cannot call ops_state_rank');
select pg_temp.assert_true((select prosecdef from pg_proc where oid = 'public.ops_home()'::regprocedure), 'ops_home is security definer');

-- ── 2. 목표 상태 경계표 ─────────────────────────────────────────────────────
-- now = 2026-11-06 09:10 KST. 경계 ±1(값은 ±0.1, 시간은 ±1초).
create temp table goal_cases (dir text, target numeric, warn numeric, bad numeric, val numeric,
                              measured timestamptz, period int, want text, label text);
insert into goal_cases values
  -- gte 80/64/40 (근접 = 64~80 은 정상)
  ('gte',80,64,40, 80  ,'2026-11-06 09:00+09',24,'ok'  ,'gte at target'),
  ('gte',80,64,40, 79.9,'2026-11-06 09:00+09',24,'ok'  ,'gte near (근접=정상)'),
  ('gte',80,64,40, 64  ,'2026-11-06 09:00+09',24,'ok'  ,'gte at warn line'),
  ('gte',80,64,40, 63.9,'2026-11-06 09:00+09',24,'warn','gte below warn'),
  ('gte',80,64,40, 40  ,'2026-11-06 09:00+09',24,'warn','gte at bad line'),
  ('gte',80,64,40, 39.9,'2026-11-06 09:00+09',24,'bad' ,'gte below bad'),
  ('gte',80,64,40, 95  ,'2026-11-06 09:00+09',24,'ok'  ,'gte above target'),
  -- lte 31/38/60
  ('lte',31,38,60, 31  ,'2026-11-06 09:00+09',744,'ok'  ,'lte at target'),
  ('lte',31,38,60, 38  ,'2026-11-06 09:00+09',744,'ok'  ,'lte at warn line'),
  ('lte',31,38,60, 38.1,'2026-11-06 09:00+09',744,'warn','lte above warn'),
  ('lte',31,38,60, 60  ,'2026-11-06 09:00+09',744,'warn','lte at bad line'),
  ('lte',31,38,60, 60.1,'2026-11-06 09:00+09',744,'bad' ,'lte above bad'),
  -- 목표 0(곱셈 기본값이 안 되는 경우) lte 0/0/3
  ('lte',0,0,3, 0,'2026-11-06 09:00+09',24,'ok'  ,'zero target met'),
  ('lte',0,0,3, 1,'2026-11-06 09:00+09',24,'warn','zero target 1'),
  ('lte',0,0,3, 3,'2026-11-06 09:00+09',24,'warn','zero target at bad'),
  ('lte',0,0,3, 4,'2026-11-06 09:00+09',24,'bad' ,'zero target above bad'),
  -- 문제선 없음 → 주의까지만
  ('gte',30,7,null,    0,'2026-11-06 09:00+09',24,'warn','no bad line: never bad'),
  ('gte',30,7,null, -100,'2026-11-06 09:00+09',24,'warn','no bad line: far below'),
  ('gte',30,7,null,    7,'2026-11-06 09:00+09',24,'ok'  ,'no bad line: at warn'),
  -- 주의선 없음 → 목표선이 주의선(부록 C tr-same-day)
  ('gte',1,null,null, 0,'2026-11-06 09:00+09',168,'warn','no warn line gte below target'),
  ('gte',1,null,null, 1,'2026-11-06 09:00+09',168,'ok'  ,'no warn line gte at target'),
  ('lte',60,null,null,61,'2026-11-06 09:00+09',168,'warn','no warn line lte above target'),
  ('lte',60,180,null,1000,'2026-11-06 09:00+09',168,'warn','lte no bad line'),
  -- 측정 필요: 미정·값 없음·시각 없음·방향 이상
  ('gte',null,64,40, 50,'2026-11-06 09:00+09',24,'unknown','no target'),
  ('gte',80,64,40, null,'2026-11-06 09:00+09',24,'unknown','no value'),
  ('gte',80,64,40, 50, null                 ,24,'unknown','no measured_at'),
  ('xx' ,80,64,40, 50,'2026-11-06 09:00+09',24,'unknown','bad direction'),
  -- 낡음: 주기 × 1.5 를 '넘으면' 강등(같으면 아직 유효)
  ('gte',80,64,40, 90,'2026-11-04 21:10:00+09',24 ,'ok'     ,'daily exactly 36h'),
  ('gte',80,64,40, 90,'2026-11-04 21:09:59+09',24 ,'unknown','daily 36h+1s'),
  ('gte',80,64,40, 90,'2026-10-26 21:10:00+09',168,'ok'     ,'weekly exactly 252h'),
  ('gte',80,64,40, 90,'2026-10-26 21:09:59+09',168,'unknown','weekly 252h+1s'),
  ('gte',80,64,40, 90,'2026-09-20 21:10:00+09',744,'ok'     ,'monthly exactly 1116h'),
  ('gte',80,64,40, 90,'2026-09-20 21:09:59+09',744,'unknown','monthly 1116h+1s'),
  ('gte',80,64,40, 10,'2026-11-04 21:09:59+09',24 ,'unknown','stale beats bad');
do $$
declare r record; got text;
begin
  for r in select * from goal_cases loop
    got := private.ops_goal_state(r.dir, r.target, r.warn, r.bad, r.val, r.measured, r.period,
                                  timestamptz '2026-11-06 09:10:00+09');
    perform pg_temp.assert_eq(got, r.want, 'ops_goal_state ' || r.label);
  end loop;
end $$;

select pg_temp.assert_eq(private.ops_goal_unknown_reason(null, 1, now(), 24, now()), 'no_target', 'reason no_target');
select pg_temp.assert_eq(private.ops_goal_unknown_reason(1, null, now(), 24, now()), 'no_value', 'reason no_value');
select pg_temp.assert_eq(private.ops_goal_unknown_reason(1, 1, null, 24, now()), 'no_value', 'reason no measured');
select pg_temp.assert_eq(private.ops_goal_unknown_reason(1, 1, timestamptz '2026-11-04 21:09:59+09', 24,
                         timestamptz '2026-11-06 09:10:00+09'), 'stale', 'reason stale');
select pg_temp.assert_eq(private.ops_goal_unknown_reason(1, 1, timestamptz '2026-11-04 21:10:00+09', 24,
                         timestamptz '2026-11-06 09:10:00+09'), null, 'reason not stale at boundary');
-- 나쁜 순서: 문제 > 주의 > 측정 필요 > 정상
select pg_temp.assert_true(private.ops_state_rank('bad') > private.ops_state_rank('warn'), 'bad > warn');
select pg_temp.assert_true(private.ops_state_rank('warn') > private.ops_state_rank('unknown'), 'warn > unknown');
select pg_temp.assert_true(private.ops_state_rank('unknown') > private.ops_state_rank('ok'), 'unknown > ok');

-- ── 3. 선 순서 제약 ─────────────────────────────────────────────────────────
insert into public.ops_team (slug, name, kind) values ('lines', '선 시험', 'office');
create temp table line_cases (dir text, target numeric, warn numeric, bad numeric, ok boolean, label text);
insert into line_cases values
  ('gte',80,64,40, true ,'gte proper'),
  ('gte',80,80,80, true ,'gte all equal'),
  ('gte',null,null,null, true,'gte all null'),
  ('gte',null,64,40, true ,'gte no target'),
  ('gte',80,null,40, true ,'gte no warn'),
  ('gte',80,64,null, true ,'gte no bad'),
  ('gte',80,64,70, false,'gte bad above warn'),
  ('gte',80,90,40, false,'gte warn above target'),
  ('gte',80,null,90, false,'gte bad above target (no warn)'),
  ('lte',31,38,60, true ,'lte proper'),
  ('lte',0,0,3, true ,'lte zero target'),
  ('lte',60,180,null, true,'lte no bad'),
  ('lte',40,38,60, false,'lte target above warn'),
  ('lte',31,70,60, false,'lte warn above bad'),
  ('lte',70,null,60, false,'lte target above bad (no warn)');
do $$
declare r record; n int := 0; accepted boolean;
begin
  for r in select * from line_cases loop
    n := n + 1;
    begin
      insert into public.ops_goal (team_id, slug, label, direction, target, warn_at, bad_at, period_hours, measure_kind)
      values ((select id from public.ops_team where slug = 'lines'), 'line-' || n, r.label, r.dir,
              r.target, r.warn, r.bad, 24, 'manual');
      accepted := true;
    exception when check_violation then
      accepted := false;
    end;
    perform pg_temp.assert_true(accepted = r.ok, 'line order ' || r.label);
  end loop;
end $$;
-- 그 밖의 제약: 팀은 부모 필수 · 보류는 시각 필수 · 할 일은 팀 필수
do $$
begin
  begin
    insert into public.ops_team (slug, name, kind) values ('orphan', '고아 팀', 'team');
    raise exception 'accepted team without parent';
  exception when check_violation then null; end;
  begin
    insert into public.ops_decision (team_id, title, reason, state)
    values ((select id from public.ops_team where slug = 'lines'), '시각 없는 보류', 'ask', 'snoozed');
    raise exception 'accepted snooze without time';
  exception when check_violation then null; end;
  begin
    insert into public.ops_task (team_id, title, created_kind) values (null, '대상 없는 지시', 'ceo');
    raise exception 'accepted task without team';
  exception when not_null_violation then null; end;
end $$;
delete from public.ops_goal;
delete from public.ops_team;

-- ── 4. 빈 DB · 칸만 있는 DB(분모 0) ─────────────────────────────────────────
select pg_temp.assert_eq(private.ops_home_at(now())->>'tree', '[]', 'empty tree');
select pg_temp.assert_eq(private.ops_home_at(now())->'summary'->>'unknown_teams', '0', 'empty summary');
select pg_temp.assert_eq(private.ops_home_at(now())->>'teams', '{}', 'empty teams');
select pg_temp.assert_eq(private.ops_home_at(now())->>'decisions_more', '0', 'empty decisions_more');
-- 부록 C-1 의 14칸, 목표 0개 → 상태를 갖는 11칸이 전부 측정 필요
insert into public.ops_team (slug, name, kind, sort_order) values
  ('situation-room','종합상황실','office',1), ('verification','검증실','office',2),
  ('research','리서치','division',3), ('platform','플랫폼','division',4), ('customer','고객','division',5);
insert into public.ops_team (slug, name, kind, parent_id, sort_order)
select v.slug, v.name, 'team', (select id from public.ops_team where slug = v.parent), v.ord
from (values ('brief','브리프편집팀','research',1), ('translation','글로벌번역팀','research',2),
             ('education','용어·교육팀','research',3), ('strategy','제품전략팀(시범)','platform',1),
             ('dev','개발팀','platform',2), ('quality','데이터품질팀','platform',3),
             ('infra','수집·인프라팀','platform',4), ('outreach','대외홍보팀','customer',1),
             ('growth','성장분석팀','customer',2)) v(slug, name, parent, ord);
create temp table h0 as select private.ops_home_at(timestamptz '2026-11-06 09:10:00+09') as j;
select pg_temp.assert_eq((select j->'summary'->>'unknown_teams' from h0), '11', '14 cells no goals → 11 unknown');
select pg_temp.assert_eq((select j->'summary'->>'ok_teams' from h0), '0', 'no goals is never ok');
select pg_temp.assert_eq((select jsonb_array_length(j->'tree')::text from h0), '5', 'tree top level 2 offices + 3 divisions');
select pg_temp.assert_eq((select j->'tree'->2->>'state' from h0), 'unknown', 'division of no-goal teams unknown');
select pg_temp.assert_eq((select j->'teams'->'dev'->>'reason_code' from h0), 'no_goals', 'no_goals reason');
delete from public.ops_team where kind = 'team';
delete from public.ops_team;

-- ── 5. 시나리오: 롤업·놀고 있음·결정·흐름·날짜 ─────────────────────────────
insert into public.ops_team (slug, name, kind, sort_order) values
  ('sr','종합상황실','office',1), ('ver','검증실','office',2),
  ('research','리서치','division',3), ('platform','플랫폼','division',4), ('customer','고객','division',5);
insert into public.ops_team (slug, name, kind, parent_id, sort_order)
select v.slug, v.name, 'team', (select id from public.ops_team where slug = v.parent), v.ord
from (values ('brief','브리프편집팀','research',1), ('translation','글로벌번역팀','research',2),
             ('dev','개발팀','platform',1), ('infra','수집·인프라팀','platform',2)) v(slug, name, parent, ord);

insert into public.ops_goal (team_id, slug, label, unit, direction, target, warn_at, bad_at,
                             current_value, measured_at, period_hours, measure_kind, sort_order)
select (select id from public.ops_team where slug = v.team), v.slug, v.label, v.unit, v.dir, v.t, v.w, v.b,
       v.val, v.m::timestamptz, v.p, 'manual', v.ord
from (values
  ('ver','ver-gap','마지막 점검 후 경과','일','lte',31,38,60, 3,'2026-11-03 09:00+09',744,1),
  ('brief','brief-ontime','월요일 정시 발행','','gte',1,null,null, 1,'2026-11-05 09:00+09',168,1),
  ('brief','brief-month','월 검수','건','gte',80,64,40, 90,'2026-09-27 09:00+09',744,2),
  ('translation','tr-same-day','영문판 같은 날 발행','','gte',1,null,null, 0,'2026-11-05 09:00+09',168,1),
  ('translation','tr-backlog','미번역 잔량','건','lte',null,null,null, null,null,24,2),
  ('dev','dev-ci','전체 테스트 시간','분','lte',30,40,45, 50,'2026-11-06 08:00+09',24,1),
  ('dev','dev-streak','연속 성공','일','gte',30,7,null, 0,'2026-11-06 08:00+09',24,2),
  ('infra','infra-age','적재 후 경과','시간','lte',36,36,72, 10,'2026-11-04 20:10+09',24,1),
  ('infra','infra-fail','배포 실패','건','lte',0,0,2, 0,'2026-11-06 08:10+09',24,2)
) v(team, slug, label, unit, dir, t, w, b, val, m, p, ord);

insert into public.ops_member (team_id, slug, name, kind, state, idle_exempt, last_active_at)
select (select id from public.ops_team where slug = v.team), v.slug, v.slug, 'routine', v.st, v.ex, v.last::timestamptz
from (values
  ('translation','tr-idle'   ,'active' ,false,'2026-11-02 09:10+09'),   -- 4일, 일 0, 팀 주의 → 놀고 있음
  ('translation','tr-exempt' ,'active' ,true ,'2026-10-27 09:10+09'),   -- 호출형(D6) → 제외
  ('translation','tr-busy'   ,'active' ,false,'2026-10-27 09:10+09'),   -- 지시함에 일 있음 → 제외
  ('translation','tr-none'   ,'active' ,false,null),                    -- 기록 없음 → no_record
  ('translation','tr-recent' ,'active' ,false,'2026-11-04 09:10+09'),   -- 2일 → 제외
  ('translation','tr-paused' ,'paused' ,false,'2026-10-27 09:10+09'),   -- 일시정지 → 제외
  ('translation','tr-retired','retired',false,'2026-10-27 09:10+09'),   -- 퇴역 → 목록에서도 빠짐
  ('brief'      ,'brief-old' ,'active' ,false,'2026-10-27 09:10+09')    -- 팀이 정상 → 제외
) v(team, slug, st, ex, last);

insert into public.ops_task (team_id, member_id, title, lane, created_kind, due_on, done_at)
select (select id from public.ops_team where slug = v.team),
       (select id from public.ops_member where slug = v.member), v.title, v.lane, 'secretary', v.due::date, v.done::timestamptz
from (values
  ('translation','tr-busy','지시 A','inbox' ,'2026-11-08',null),
  ('dev'        ,null     ,'지연 B','doing' ,'2026-11-05',null),
  ('dev'        ,null     ,'검사 C','review','2026-11-20',null),
  ('sr'         ,null     ,'지시 D','inbox' ,'2026-11-13',null),
  ('sr'         ,null     ,'지시 E','inbox' ,'2026-11-14',null),
  ('sr'         ,null     ,'완료 F','done'  ,null        ,'2026-11-03 10:00+09'),
  ('sr'         ,null     ,'완료 G','done'  ,null        ,'2026-10-30 10:00+09')
) v(team, member, title, lane, due, done);

insert into public.ops_decision (team_id, title, reason, due_at, state, snooze_until)
select (select id from public.ops_team where slug = v.team), v.title, 'approve', v.due::timestamptz, v.st, v.snz::timestamptz
from (values
  ('sr' ,'d1','2026-11-09 09:00+09','pending'  ,null),
  ('sr' ,'d2','2026-11-13 09:00+09','pending'  ,null),
  ('sr' ,'d3',null                 ,'pending'  ,null),
  ('sr' ,'d4','2026-11-07 09:00+09','snoozed'  ,'2026-11-06 08:00+09'),   -- 보류 시각 지남 → 다시 보임
  ('sr' ,'d5','2026-11-10 09:00+09','snoozed'  ,'2026-11-07 08:00+09'),   -- 아직 보류 → 숨김
  ('sr' ,'d6','2026-11-08 09:00+09','approved' ,null),
  ('sr' ,'d7','2026-11-08 09:00+09','withdrawn',null),
  ('dev','d8','2026-11-05 09:00+09','pending'  ,null)                      -- 기한 지남
) v(team, title, due, st, snz);

create temp table h as select private.ops_home_at(timestamptz '2026-11-06 09:10:00+09') as j;

-- 상태 롤업(가장 나쁜 쪽)
select pg_temp.assert_eq((select j->'teams'->'sr'->>'state' from h), 'unknown', 'office without goals unknown');
select pg_temp.assert_eq((select j->'teams'->'sr'->>'reason' from h), '목표 없음', 'office without goals reason');
select pg_temp.assert_eq((select j->'teams'->'ver'->>'state' from h), 'ok', 'ver ok');
select pg_temp.assert_eq((select j->'teams'->'brief'->>'state' from h), 'ok', 'brief ok (monthly 40d not stale)');
select pg_temp.assert_eq((select j->'teams'->'translation'->>'state' from h), 'warn', 'warn beats unknown');
select pg_temp.assert_eq((select j->'teams'->'translation'->>'worst_goal' from h), 'tr-same-day', 'translation worst goal');
select pg_temp.assert_eq((select j->'teams'->'dev'->>'state' from h), 'bad', 'bad beats warn');
select pg_temp.assert_eq((select j->'teams'->'dev'->>'worst_goal' from h), 'dev-ci', 'dev worst goal');
select pg_temp.assert_eq((select j->'teams'->'dev'->>'reason' from h), '전체 테스트 시간 50분 · 목표 30분', 'dev reason text');
select pg_temp.assert_eq((select j->'teams'->'infra'->>'state' from h), 'unknown', 'stale unknown beats ok');
select pg_temp.assert_eq((select j->'teams'->'infra'->>'reason_code' from h), 'stale', 'infra stale reason');
select pg_temp.assert_eq((select j->'tree'->0->>'slug' from h), 'sr', 'tree order 1');
select pg_temp.assert_eq((select j->'tree'->2->>'state' from h), 'warn', 'research = worst(ok, warn)');
select pg_temp.assert_eq((select j->'tree'->2->>'worst_team' from h), 'translation', 'research worst team');
select pg_temp.assert_eq((select j->'tree'->3->>'state' from h), 'bad', 'platform = worst(bad, unknown)');
select pg_temp.assert_eq((select j->'tree'->4->>'state' from h), 'unknown', 'division without teams unknown');
select pg_temp.assert_eq((select j->'tree'->4->>'reason_code' from h), 'no_children', 'division without teams reason');
select pg_temp.assert_eq((select jsonb_array_length(j->'tree'->2->'children')::text from h), '2', 'research children');
select pg_temp.assert_eq((select j->'summary'->>'bad' from h), '1', 'summary bad');
select pg_temp.assert_eq((select j->'summary'->>'warn' from h), '1', 'summary warn');
select pg_temp.assert_eq((select j->'summary'->>'unknown_teams' from h), '2', 'summary unknown');
select pg_temp.assert_eq((select j->'summary'->>'ok_teams' from h), '2', 'summary ok');
-- 목표 상세
select pg_temp.assert_eq((select j->'teams'->'infra'->'goals'->0->>'stale' from h), 'true', 'goal stale flag');
select pg_temp.assert_eq((select j->'teams'->'infra'->'goals'->0->>'current' from h), '10', 'stale value kept');
select pg_temp.assert_eq((select j->'teams'->'translation'->'goals'->1->>'unknown_reason' from h), 'no_target', 'goal no_target');

-- 놀고 있음(D6)
select pg_temp.assert_eq((select j->'summary'->>'idle' from h), '1', 'exactly one idle');
select pg_temp.assert_eq((select j->'summary'->>'no_record' from h), '1', 'exactly one no_record');
select pg_temp.assert_eq((select a->>'member' from h, jsonb_array_elements(j->'anomalies') x(a) where a->>'kind' = 'idle'), 'tr-idle', 'idle member');
select pg_temp.assert_eq((select a->>'days' from h, jsonb_array_elements(j->'anomalies') x(a) where a->>'kind' = 'idle'), '4', 'idle days');
select pg_temp.assert_eq((select a->>'member' from h, jsonb_array_elements(j->'anomalies') x(a) where a->>'kind' = 'no_record'), 'tr-none', 'no_record member');
select pg_temp.assert_eq((select jsonb_array_length(j->'teams'->'translation'->'members')::text from h), '6', 'retired hidden');
select pg_temp.assert_eq((select m->>'idle' from h, jsonb_array_elements(j->'teams'->'translation'->'members') y(m) where m->>'slug' = 'tr-exempt'), 'false', 'exempt not idle');
select pg_temp.assert_eq((select m->>'open_tasks' from h, jsonb_array_elements(j->'teams'->'translation'->'members') y(m) where m->>'slug' = 'tr-busy'), '1', 'busy open tasks');
-- 이상 징후 순서: 문제 목표 → 주의 목표 → 낡음 → 놀고 있음 → 기록 없음
select pg_temp.assert_eq((select j->'anomalies'->0->>'state' from h), 'bad', 'anomalies start with bad');
select pg_temp.assert_eq((select string_agg(a->>'kind', ',' order by o) from h, jsonb_array_elements(j->'anomalies') with ordinality x(a, o)),
                         'goal,goal,goal,stale,idle,no_record', 'anomaly order');

-- 결정 대기: 보이는 5건 중 3건 + 더 보기 2
select pg_temp.assert_eq((select j->'summary'->>'decisions' from h), '5', 'visible decisions');
select pg_temp.assert_eq((select jsonb_array_length(j->'decisions')::text from h), '3', 'inbox shows 3');
select pg_temp.assert_eq((select j->>'decisions_more' from h), '2', 'decisions_more');
select pg_temp.assert_eq((select string_agg(d->>'title', ',' order by o) from h, jsonb_array_elements(j->'decisions') with ordinality x(d, o)),
                         'd8,d4,d1', 'decisions by due');
select pg_temp.assert_eq((select j->'decisions'->0->>'overdue' from h), 'true', 'overdue decision');
select pg_temp.assert_eq((select j->'decisions'->1->>'overdue' from h), 'false', 'not overdue decision');
select pg_temp.assert_eq((select j->>'publish_approval' from h), null, 'publish approval not in DB');
select pg_temp.assert_eq((select j->'tree'->0->>'decisions' from h), '4', 'sr decision chip');
select pg_temp.assert_eq((select j->'tree'->3->>'decisions' from h), '1', 'division chip sums children');

-- 흐름·마감
select pg_temp.assert_eq((select j->'summary'->>'deadlines' from h), '2', 'deadlines within 7 days (inclusive)');
select pg_temp.assert_eq((select j->'summary'->>'overdue' from h), '1', 'overdue tasks');
select pg_temp.assert_eq((select jsonb_array_length(j->'flow'->'todo')::text from h), '3', 'todo');
select pg_temp.assert_eq((select jsonb_array_length(j->'flow'->'doing')::text from h), '2', 'doing + review');
select pg_temp.assert_eq((select j->'flow'->'doing'->0->>'overdue' from h), 'true', 'late task flagged');
select pg_temp.assert_eq((select jsonb_array_length(j->'flow'->'done')::text from h), '1', 'done this KST week only');
select pg_temp.assert_eq((select jsonb_array_length(j->'upcoming')::text from h), '5', 'upcoming capped at 5');
select pg_temp.assert_eq((select j->'upcoming'->0->>'on' from h), '2026-11-07', 'upcoming first');
select pg_temp.assert_eq((select j->'upcoming'->4->>'kind' from h), 'task', 'upcoming same-day tie: task first');
select pg_temp.assert_eq((select j->>'generated_at' from h), '2026-11-06T09:10:00+09:00', 'generated_at KST');

-- ── 6. 관리자 검사(42501)와 RLS ────────────────────────────────────────────
set local role authenticated;
set local test.is_admin = 'off';
do $$
begin
  begin
    perform public.ops_home();
    raise exception 'non-admin got ops_home';
  exception when insufficient_privilege then null; end;
end $$;
set local test.is_admin = 'null';
do $$
begin
  begin
    perform public.ops_home();
    raise exception 'null is_admin got ops_home';
  exception when insufficient_privilege then null; end;
end $$;
set local test.is_admin = 'off';
select pg_temp.assert_eq((select count(*)::text from public.ops_team), '0', 'non-admin sees no teams');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal), '0', 'non-admin sees no goals');
select pg_temp.assert_eq((select count(*)::text from public.ops_decision), '0', 'non-admin sees no decisions');
select pg_temp.assert_eq((select count(*)::text from public.ops_task), '0', 'non-admin sees no tasks');
select pg_temp.assert_eq((select count(*)::text from public.ops_member), '0', 'non-admin sees no members');
do $$
begin
  begin
    perform private.ops_home_at(now());
    raise exception 'authenticated called ops_home_at';
  exception when insufficient_privilege then null; end;
end $$;
set local test.is_admin = 'on';
select pg_temp.assert_eq((select count(*)::text from public.ops_team), '9', 'admin sees teams');
select pg_temp.assert_eq((select jsonb_typeof(public.ops_home()->'summary')), 'object', 'admin gets ops_home');
do $$
begin
  begin
    insert into public.ops_task (team_id, title, created_kind)
    values ((select id from public.ops_team where slug = 'sr'), '직접 쓰기', 'ceo');
    raise exception 'admin wrote table directly';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
set local role anon;
do $$
begin
  begin
    perform public.ops_home();
    raise exception 'anon called ops_home';
  exception when insufficient_privilege then null; end;
  begin
    perform 1 from public.ops_team;
    raise exception 'anon read ops_team';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
set local role service_role;
insert into public.ops_task (team_id, title, created_kind)
values ((select id from public.ops_team where slug = 'sr'), '비서실 쓰기', 'secretary');
reset role;

rollback;
\echo 'ops_center.sql: all assertions passed'
