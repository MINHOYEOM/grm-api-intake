-- ============================================================================
-- 123_ops_home.sql — 운영 센터 상태 계산 + 홈 읽기 함수 (PR1/7, 설계서 2026-11-06 §2-2·§3·부록 B)
--
-- 무엇(전부 새 이름, 가산만 — 기존 표·함수·RPC 반환 shape 불변):
--   private.ops_goal_state(...)          목표 한 개의 상태. 순수 함수('지금'을 인자로 받는다 → 경계 시험 가능).
--   private.ops_goal_unknown_reason(...) '측정 필요'의 이유 코드(no_target | no_value | stale).
--   private.ops_state_rank(text)         나쁜 순서 단일 원천: 문제(4) > 주의(3) > 측정 필요(2) > 정상(1).
--   private.ops_home_at(p_now)           홈 전체 JSON 계산 본체(시각 고정 시험용). 클라이언트 실행 불가.
--   public.ops_home()                    화면이 부르는 유일한 함수. 함수 안에서 private.is_admin() 검사,
--                                        아니면 42501. security definer. 본체에 now() 를 넘긴다.
--
-- 상태 규칙(§2-2, 이 파일이 유일한 구현 — 화면 JS 는 다시 계산하지 않는다):
--   1. 목표값 없음(미정) / 현재값 없음 / 측정 시각 없음        → unknown(측정 필요)
--   2. now - measured_at > 주기 × 1.5 (24h→36h·168h→252h·744h→1116h, 정수 나눗셈 *3/2 로 딱 떨어짐)
--                                                             → unknown(낡음 · 값은 그대로 보여 준다)
--   3. 문제선 넘음(gte: 값 < bad_at / lte: 값 > bad_at)       → bad(문제)   · bad_at 비면 '주의'까지만
--   4. 주의선 넘음(gte: 값 < 주의선 / lte: 값 > 주의선)        → warn(주의)
--   5. 그 밖(달성 + 근접)                                      → ok(정상)   · 근접(목표선~주의선 사이)은 정상
--   ★주의선이 비어 있으면 목표선을 주의선으로 쓴다(coalesce(warn_at, target)). 부록 A 초안은 주의선이 비면
--     목표 미달도 '정상'으로 냈는데, 부록 C 의 기대 상태(예: tr-same-day 목표 1·값 0 → 주의)와 어긋난다.
--     주의선이 없다 = 근접 구간이 없다 → 목표 미달이 곧 주의. 경계 시험이 이 해석을 고정한다.
--   팀·실 상태 = 소속 목표 중 가장 나쁜 쪽(평균 안 씀) · 목표가 하나도 없으면 unknown('목표 없음').
--   부문 상태  = 소속 팀 중 가장 나쁜 쪽 · 팀이 하나도 없으면 unknown('팀 없음') — 빈 집합을 정상으로 덮지 않는다.
--   '측정 필요'가 '정상'보다 나쁜 이유: 안 잰 것을 초록으로 덮지 않기 위해.
--
-- 놀고 있음(팀원 단위, D6): 활동 중(active) · 호출형 아님(idle_exempt=false) · 그 팀원 앞 지시함·진행 할 일 0 ·
--   그 팀에 주의·문제 목표가 있음 · 마지막 활동이 3일 이상 전. 마지막 활동이 한 번도 없으면 '놀고 있음'이
--   아니라 '활동 기록 없음'(no_record)으로 따로 센다 — 지어낸 일수를 만들지 않는다.
-- 결정 대기에 보이는 조건: state='pending' 이거나 state='snoozed' and snooze_until <= now.
--   홈은 3건 + decisions_more. 월요일 발행 승인은 DB 행이 없으므로 publish_approval 은 늘 null 이고
--   화면이 기존 loadPublishPr 결과로 채운다(발행 PR 은 ops_decision 에 넣지 않는다 — 두 번 보임 방지).
-- 날짜 경계는 KST: 오늘 = (now at time zone 'Asia/Seoul')::date, 이번 주 = KST 월요일 00:00 부터.
--
-- 권한: 본체·보조 함수는 public·anon·authenticated 셋 다 명시 회수(10-28 교훈: public 만 회수하면 anon 이 남는다).
--   ops_home 만 authenticated 에 EXECUTE — 호출은 되지만 관리자가 아니면 함수 안에서 42501.
--   비서실이 SQL 에디터(postgres)에서 같은 결과를 보려면 `select private.ops_home_at(now());`
--   (ops_home() 은 JWT 가 없어 is_admin()=false → 42501 이 정상).
--
-- 적용: 122 다음(표가 있어야 함수가 만들어진다). 비서실이 SQL 검토 후 적용.
-- 적용 후 확인(SQL 에디터):
--   select routine_schema, routine_name, grantee, privilege_type from information_schema.routine_privileges
--    where routine_name in ('ops_home','ops_home_at','ops_goal_state','ops_goal_unknown_reason','ops_state_rank')
--    order by 1,2,3;
--   → ops_home 은 authenticated 에 EXECUTE, anon·PUBLIC 행 없음. private.* 4개는 anon·authenticated·PUBLIC 행 없음.
--   select jsonb_pretty(private.ops_home_at(now()));  -- 표가 비면 tree=[] · summary 전부 0
--   일반 회원 토큰으로 rpc('ops_home') → 42501(permission denied) 이어야 한다.
--
-- 되돌리기(함수만 · 122 의 표는 그대로):
--   drop function if exists public.ops_home();
--   drop function if exists private.ops_home_at(timestamptz);
--   drop function if exists private.ops_goal_unknown_reason(numeric, numeric, timestamptz, integer, timestamptz);
--   drop function if exists private.ops_goal_state(text, numeric, numeric, numeric, numeric, timestamptz, integer, timestamptz);
--   drop function if exists private.ops_state_rank(text);
-- ============================================================================

create schema if not exists private;

-- ── 나쁜 순서(단일 원천) ────────────────────────────────────────────────────
create or replace function private.ops_state_rank(p_state text)
returns integer
language sql
immutable
set search_path = public
as $$
  select case p_state
    when 'bad'     then 4
    when 'warn'    then 3
    when 'unknown' then 2
    when 'ok'      then 1
    else 2          -- 모르는 값은 정상으로 덮지 않고 측정 필요 취급
  end
$$;

-- ── 목표 한 개의 상태(순수 함수) ────────────────────────────────────────────
create or replace function private.ops_goal_state(
  p_dir text, p_target numeric, p_warn numeric, p_bad numeric,
  p_value numeric, p_measured timestamptz, p_period_hours integer, p_now timestamptz)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    when p_dir is null or p_dir not in ('gte','lte') then 'unknown'
    when p_target is null or p_value is null or p_measured is null then 'unknown'
    when p_period_hours is null or p_now is null then 'unknown'
    when p_now - p_measured > make_interval(hours => (p_period_hours * 3 / 2)) then 'unknown'
    when p_bad is not null
         and ((p_dir = 'gte' and p_value < p_bad) or (p_dir = 'lte' and p_value > p_bad)) then 'bad'
    when (p_dir = 'gte' and p_value < coalesce(p_warn, p_target))
      or (p_dir = 'lte' and p_value > coalesce(p_warn, p_target)) then 'warn'
    else 'ok'
  end
$$;

-- ── '측정 필요'의 이유 ──────────────────────────────────────────────────────
create or replace function private.ops_goal_unknown_reason(
  p_target numeric, p_value numeric, p_measured timestamptz, p_period_hours integer, p_now timestamptz)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    when p_target is null then 'no_target'
    when p_value is null or p_measured is null then 'no_value'
    when p_now - p_measured > make_interval(hours => (p_period_hours * 3 / 2)) then 'stale'
    else null
  end
$$;

-- ── 홈 전체 JSON 본체(시각 고정) ────────────────────────────────────────────
create or replace function private.ops_home_at(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
with
d0 as (
  select (p_now at time zone 'Asia/Seoul')::date as today,
         (date_trunc('week', p_now at time zone 'Asia/Seoul') at time zone 'Asia/Seoul') as week_start
),
teams as (
  select * from public.ops_team where active
),
g as (
  select gl.*,
         private.ops_goal_state(gl.direction, gl.target, gl.warn_at, gl.bad_at,
                                gl.current_value, gl.measured_at, gl.period_hours, p_now) as st
  from public.ops_goal gl
  join teams t on t.id = gl.team_id
  where gl.active
),
g2 as (
  select g.*,
         case when g.st = 'unknown'
              then private.ops_goal_unknown_reason(g.target, g.current_value, g.measured_at, g.period_hours, p_now)
         end as ureason,
         case when g.measured_at is null then null
              else floor(extract(epoch from (p_now - g.measured_at)) / 86400)::int
         end as mdays
  from g
),
g3 as (
  select g2.*,
         case
           when g2.st in ('bad','warn') then
             g2.label || ' ' || coalesce(trim_scale(g2.current_value)::text, '—') || g2.unit
               || ' · 목표 ' || trim_scale(g2.target)::text || g2.unit
           when g2.ureason = 'no_target' then g2.label || ' · 목표 미정'
           when g2.ureason = 'no_value'  then g2.label || ' · 값 없음'
           when g2.ureason = 'stale'     then g2.label || ' · ' || g2.mdays || '일 전 측정'
           when g2.st = 'unknown'        then g2.label || ' · 측정 필요'
           else ''
         end as reason_text
  from g2
),
dvis as (   -- 홈에 보이는 결정(대기 + 보류 시각이 지난 것). 팀이 비활성이어도 결정은 숨기지 않는다.
  select d.*, t.slug as team_slug
  from public.ops_decision d
  join public.ops_team t on t.id = d.team_id
  where d.state = 'pending' or (d.state = 'snoozed' and d.snooze_until <= p_now)
),
dcount as (
  select team_id, count(*)::int as n from dvis group by team_id
),
nodes as (   -- 상태를 갖는 칸: 실(office) + 팀(team)
  select t.id, t.slug, t.name, t.kind, t.parent_id, t.sort_order, t.is_pilot, t.blurb,
         coalesce(w.st, 'unknown') as state,
         case when w.id is null then 'no_goals'
              when w.st in ('bad','warn') then 'goal_' || w.st
              when w.st = 'unknown' then coalesce(w.ureason, 'unknown')
              else '' end as reason_code,
         case when w.id is null then '목표 없음'
              when w.st = 'ok' then ''
              else w.reason_text end as reason,
         case when w.st is distinct from 'ok' then w.slug end as worst_goal,
         coalesce(dc.n, 0) as decisions
  from teams t
  left join lateral (
    select g3.id, g3.slug, g3.st, g3.ureason, g3.reason_text
    from g3
    where g3.team_id = t.id
    order by private.ops_state_rank(g3.st) desc, g3.sort_order, g3.id
    limit 1
  ) w on true
  left join dcount dc on dc.team_id = t.id
  where t.kind in ('office','team')
),
divs as (    -- 부문: 소속 팀 중 가장 나쁜 쪽
  select dv.id, dv.slug, dv.name, dv.sort_order, dv.blurb,
         coalesce(wc.state, 'unknown') as state,
         case when wc.id is null then 'no_children'
              when wc.state = 'ok' then ''
              else 'child_' || wc.state end as reason_code,
         case when wc.id is null then '팀 없음'
              when wc.state = 'ok' then ''
              else wc.name || ' · ' || wc.reason end as reason,
         case when wc.state is distinct from 'ok' then wc.slug end as worst_team,
         coalesce(dc.n, 0)
           + coalesce((select sum(n.decisions) from nodes n where n.parent_id = dv.id), 0)::int as decisions
  from teams dv
  left join lateral (
    select n.id, n.slug, n.name, n.state, n.reason
    from nodes n
    where n.parent_id = dv.id and n.kind = 'team'
    order by private.ops_state_rank(n.state) desc, n.sort_order, n.id
    limit 1
  ) wc on true
  left join dcount dc on dc.team_id = dv.id
  where dv.kind = 'division'
),
alert_team as (   -- 주의·문제 목표가 있는 팀(놀고 있음 판정의 전제)
  select distinct team_id from g3 where st in ('bad','warn')
),
mem as (
  select mb.*, t.slug as team_slug, t.sort_order as team_sort,
         (select count(*)::int from public.ops_task tk
           where tk.member_id = mb.id and tk.lane in ('inbox','doing')) as open_tasks,
         case when mb.last_active_at is null then null
              else floor(extract(epoch from (p_now - mb.last_active_at)) / 86400)::int
         end as last_days,
         exists (select 1 from alert_team a where a.team_id = mb.team_id) as team_alert
  from public.ops_member mb
  join teams t on t.id = mb.team_id
  where mb.state <> 'retired'
),
mem2 as (
  select mem.*,
         (state = 'active' and not idle_exempt and open_tasks = 0 and team_alert
          and last_active_at is not null and p_now - last_active_at >= interval '3 days') as idle,
         (state = 'active' and not idle_exempt and open_tasks = 0 and team_alert
          and last_active_at is null) as no_record
  from mem
),
tk as (
  select x.*, t.slug as team_slug, mb.slug as member_slug,
         (x.lane <> 'done' and x.due_on is not null and x.due_on < (select today from d0)) as overdue
  from public.ops_task x
  join public.ops_team t on t.id = x.team_id
  left join public.ops_member mb on mb.id = x.member_id
),
upc as (
  select tk.due_on as on_date, 'task' as kind, tk.title as text, tk.team_slug as team, tk.id
  from tk, d0
  where tk.lane <> 'done' and tk.due_on between d0.today and d0.today + 7
  union all
  select (d.due_at at time zone 'Asia/Seoul')::date, 'decision', d.title, t.slug, d.id
  from public.ops_decision d
  join public.ops_team t on t.id = d.team_id, d0
  where d.state in ('pending','snoozed') and d.due_at is not null
    and (d.due_at at time zone 'Asia/Seoul')::date between d0.today and d0.today + 7
),
anom as (   -- 순서: 문제 목표 → 주의 목표 → 낡음 → 놀고 있음 → 기록 없음
  select case g3.st when 'bad' then 1 else 2 end as ord, g3.team_id, g3.sort_order::int as sub,
         jsonb_build_object('kind','goal','team',t.slug,'goal',g3.slug,'state',g3.st,
                            'text',g3.reason_text,'note',g3.note) as obj
  from g3 join teams t on t.id = g3.team_id
  where g3.st in ('bad','warn')
  union all
  select 3, g3.team_id, g3.sort_order::int,
         jsonb_build_object('kind','stale','team',t.slug,'goal',g3.slug,'state','unknown',
                            'text',g3.reason_text,'note',g3.note,'measured_days',g3.mdays)
  from g3 join teams t on t.id = g3.team_id
  where g3.ureason = 'stale'
  union all
  select 4, m.team_id, m.id,
         jsonb_build_object('kind','idle','team',m.team_slug,'member',m.slug,'name',m.name,'days',m.last_days)
  from mem2 m where m.idle
  union all
  select 5, m.team_id, m.id,
         jsonb_build_object('kind','no_record','team',m.team_slug,'member',m.slug,'name',m.name)
  from mem2 m where m.no_record
)
select jsonb_build_object(
  'generated_at', to_char(p_now at time zone 'Asia/Seoul', 'YYYY-MM-DD"T"HH24:MI:SS"+09:00"'),
  'summary', jsonb_build_object(
    'bad',           (select count(*) from nodes where state = 'bad'),
    'warn',          (select count(*) from nodes where state = 'warn'),
    'unknown_teams', (select count(*) from nodes where state = 'unknown'),
    'ok_teams',      (select count(*) from nodes where state = 'ok'),
    'decisions',     (select count(*) from dvis),
    'idle',          (select count(*) from mem2 where idle),
    'no_record',     (select count(*) from mem2 where no_record),
    'deadlines',     (select count(*) from tk, d0
                       where tk.lane <> 'done' and tk.due_on between d0.today and d0.today + 7),
    'overdue',       (select count(*) from tk where tk.overdue)
  ),
  'decisions', coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', x.id, 'team', x.team_slug, 'reason', x.reason, 'title', x.title, 'why', x.why,
             'if_approved', x.if_approved, 'recommendation', x.recommendation, 'due_at', x.due_at,
             'overdue', (x.due_at is not null and x.due_at < p_now), 'state', x.state,
             'snooze_count', x.snooze_count, 'ref_url', x.ref_url) order by x.rn)
    from (select dvis.*, row_number() over (order by due_at nulls last, created_at, id) as rn from dvis) x
    where x.rn <= 3), '[]'::jsonb),
  'decisions_more', greatest((select count(*) from dvis) - 3, 0),
  'publish_approval', null::jsonb,
  'anomalies', coalesce((
    select jsonb_agg(a.obj order by a.ord, t.sort_order, a.sub)
    from anom a join public.ops_team t on t.id = a.team_id), '[]'::jsonb),
  'tree', coalesce((
    select jsonb_agg(z.obj order by z.sort_order, z.id)
    from (
      select n.sort_order, n.id,
             jsonb_build_object('slug',n.slug,'name',n.name,'kind',n.kind,'state',n.state,
                                'reason_code',n.reason_code,'reason',n.reason,'worst_goal',n.worst_goal,
                                'is_pilot',n.is_pilot,'decisions',n.decisions) as obj
      from nodes n where n.kind = 'office'
      union all
      select dv.sort_order, dv.id,
             jsonb_build_object('slug',dv.slug,'name',dv.name,'kind','division','state',dv.state,
                                'reason_code',dv.reason_code,'reason',dv.reason,'worst_team',dv.worst_team,
                                'decisions',dv.decisions,
                                'children', coalesce((
                                  select jsonb_agg(jsonb_build_object(
                                           'slug',n.slug,'name',n.name,'kind',n.kind,'state',n.state,
                                           'reason_code',n.reason_code,'reason',n.reason,
                                           'worst_goal',n.worst_goal,'is_pilot',n.is_pilot,
                                           'decisions',n.decisions) order by n.sort_order, n.id)
                                  from nodes n where n.parent_id = dv.id and n.kind = 'team'), '[]'::jsonb))
      from divs dv
    ) z), '[]'::jsonb),
  'teams', coalesce((
    select jsonb_object_agg(n.slug, jsonb_build_object(
             'name', n.name, 'kind', n.kind, 'state', n.state, 'reason_code', n.reason_code,
             'reason', n.reason, 'worst_goal', n.worst_goal, 'blurb', n.blurb,
             'members', coalesce((
               select jsonb_agg(jsonb_build_object(
                        'slug', m.slug, 'name', m.name, 'kind', m.kind, 'model', m.model, 'state', m.state,
                        'idle_exempt', m.idle_exempt, 'last_active_at', m.last_active_at,
                        'last_active_days', m.last_days, 'last_active_note', m.last_active_note,
                        'open_tasks', m.open_tasks, 'idle', m.idle, 'no_record', m.no_record)
                      order by m.id)
               from mem2 m where m.team_id = n.id), '[]'::jsonb),
             'goals', coalesce((
               select jsonb_agg(jsonb_build_object(
                        'slug', x.slug, 'label', x.label, 'unit', x.unit, 'direction', x.direction,
                        'current', x.current_value, 'target', x.target, 'warn_at', x.warn_at, 'bad_at', x.bad_at,
                        'state', x.st, 'unknown_reason', x.ureason, 'measured_at', x.measured_at,
                        'measured_days', x.mdays, 'stale', (x.ureason = 'stale'),
                        'measure_kind', x.measure_kind, 'note', x.note)
                      order by x.sort_order, x.id)
               from g3 x where x.team_id = n.id), '[]'::jsonb)))
    from nodes n), '{}'::jsonb),
  'flow', jsonb_build_object(
    'todo', coalesce((
      select jsonb_agg(f.obj order by f.due_on nulls last, f.created_at, f.id) from (
        select tk.id, tk.due_on, tk.created_at,
               jsonb_build_object('id',tk.id,'team',tk.team_slug,'member',tk.member_slug,'title',tk.title,
                                  'lane',tk.lane,'due_on',tk.due_on,'overdue',tk.overdue,
                                  'created_kind',tk.created_kind,
                                  'secretary_seen',(tk.secretary_seen_at is not null)) as obj
        from tk where tk.lane = 'inbox'
        order by tk.due_on nulls last, tk.created_at, tk.id limit 30) f), '[]'::jsonb),
    'doing', coalesce((
      select jsonb_agg(f.obj order by f.due_on nulls last, f.created_at, f.id) from (
        select tk.id, tk.due_on, tk.created_at,
               jsonb_build_object('id',tk.id,'team',tk.team_slug,'member',tk.member_slug,'title',tk.title,
                                  'lane',tk.lane,'due_on',tk.due_on,'overdue',tk.overdue,
                                  'created_kind',tk.created_kind,
                                  'secretary_seen',(tk.secretary_seen_at is not null)) as obj
        from tk where tk.lane in ('doing','review')
        order by tk.due_on nulls last, tk.created_at, tk.id limit 30) f), '[]'::jsonb),
    'done', coalesce((
      select jsonb_agg(f.obj order by f.done_at desc, f.id desc) from (
        select tk.id, tk.done_at,
               jsonb_build_object('id',tk.id,'team',tk.team_slug,'member',tk.member_slug,'title',tk.title,
                                  'lane',tk.lane,'done_at',tk.done_at,'result_url',tk.result_url) as obj
        from tk, d0
        where tk.lane = 'done' and tk.done_at >= d0.week_start
        order by tk.done_at desc, tk.id desc limit 5) f), '[]'::jsonb)
  ),
  'upcoming', coalesce((
    select jsonb_agg(jsonb_build_object('on',u.on_date,'kind',u.kind,'text',u.text,'team',u.team)
                     order by u.on_date, u.kind desc, u.id)
    from (select * from upc order by on_date, kind desc, id limit 5) u), '[]'::jsonb)
)
$$;

-- ── 화면이 부르는 함수: 관리자 검사 → 본체 ──────────────────────────────────
create or replace function public.ops_home()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  -- 회원 가입이 열려 있어 로그인됨 ≠ 관리자. null 도 거부(coalesce) — 검사 함수가 null 을 내도 열리지 않게.
  if not coalesce(private.is_admin(), false) then
    raise exception 'ops_home: admin only' using errcode = '42501';
  end if;
  return private.ops_home_at(now());
end
$$;

-- ── 권한: 셋 다 명시 회수 후 ops_home 만 authenticated 에 ─────────────────────
revoke all on function private.ops_state_rank(text) from public, anon, authenticated;
revoke all on function private.ops_goal_state(text, numeric, numeric, numeric, numeric, timestamptz, integer, timestamptz)
  from public, anon, authenticated;
revoke all on function private.ops_goal_unknown_reason(numeric, numeric, timestamptz, integer, timestamptz)
  from public, anon, authenticated;
revoke all on function private.ops_home_at(timestamptz) from public, anon, authenticated;
revoke all on function public.ops_home() from public, anon, authenticated;
grant execute on function public.ops_home() to authenticated;   -- 호출 허용, 안에서 관리자 검사
