-- ============================================================================
-- 122_ops_center_tables.sql — 운영 센터 표 5개 (PR1/7, 설계서 2026-11-06 §2·부록 A)
--
-- 무엇: Admin 홈 '운영 센터'(사무실 층 배치도: 방=팀, 자리=팀원, 대표실=결재함)의 원천 표.
--   ops_team      조직도 칸(실 2 · 부문 3 · 팀 9 = 14칸). 팀은 부문 아래, 실·부문은 최상위.
--   ops_member    팀원 = 정기 작업·세션·코드 하나. 상태·호출형 표시·마지막 활동 시각.
--   ops_goal      팀 목표. 목표/주의/문제 선·현재값·측정 시각·주기. 상태 계산은 123 의 함수 한 곳.
--   ops_task      할 일(지시함 → 진행 → 검사 → 완료). 담당 팀 필수(대상 없는 지시 금지).
--   ops_decision  결정 대기(대표가 정해야 비서실이 움직이는 일). 월요일 발행 PR 은 넣지 않는다.
--
-- 가산만: 새 표 5개 + 인덱스 + RLS 정책. 기존 표·함수·RPC 반환 shape 은 하나도 건드리지 않는다.
--   옛 097 ops_reports 는 그대로 둔다(이 표들과 무관, 삭제는 PR7 후보).
-- 쓰기: service_role 전용. 대표 화면의 쓰기는 PR4·PR5 의 '검사하는 함수'(ops_decide·ops_task_create)
--   를 거친다 — 이 파일은 클라이언트 쓰기 경로를 하나도 만들지 않는다.
-- 읽기: 관리자만. authenticated 에 SELECT 만 grant 하고 정책이 private.is_admin() 으로 거른다
--   (097 관례). 회원 가입이 열려 있어 '로그인됨 ≠ 관리자' 이므로 정책 없이는 회원이 다 본다.
-- 권한: `revoke … from public` 만으로는 Supabase 가 anon·authenticated 에 직접 준 grant 가 안 지워진다
--   (10-28 교훈, 096·097 관례) — public·anon·authenticated 셋을 명시해 회수한 뒤 필요한 것만 grant.
--   identity 열 시퀀스도 같은 이유로 셋 회수 후 service_role 에만 usage.
--
-- 선 순서 제약(ops_goal_lines_order): 있는 선끼리만 비교한다(빈 선은 비교에서 빠짐).
--   gte(클수록 좋음): bad_at ≤ warn_at ≤ target,  lte(작을수록 좋음): target ≤ warn_at ≤ bad_at.
--   ★부록 A 초안의 coalesce 사슬 대신 두 선씩 짝지은 명시 비교로 썼다 — coalesce 사슬은 선 하나만
--   있을 때 비교식이 NULL 이 되어 CHECK 를 '통과'시키는데, 그 통과가 의도인지 우연인지 읽히지 않는다.
--   짝 비교는 "둘 다 있을 때만 순서를 본다"가 글자로 드러나고 tests/sql/ops_center.sql 이 위반 6종을 막는다.
--
-- 적용: 비서실이 SQL 검토 후 SQL 에디터에서 122 → 123 순서로(PR 에는 파일만).
-- 적용 후 확인(SQL 에디터):
--   select table_name, grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name like 'ops\_%' and table_name <> 'ops_reports'
--    order by 1,2,3;
--   → 표마다 authenticated=SELECT 한 줄, service_role=전체, anon 행 없음이어야 한다.
--   select relname, relrowsecurity from pg_class
--    where relname in ('ops_team','ops_member','ops_goal','ops_task','ops_decision');  -- 전부 true
--   select sequence_name, grantee, privilege_type from information_schema.usage_privileges
--    where object_schema='public' and object_name like 'ops\_%seq' order by 1,2;  -- service_role 만
--
-- 되돌리기(데이터 쌓이기 전이라 손실 없음 · 이 표들만 · 기존 불변 · 123 을 먼저 되돌린다):
--   drop table if exists public.ops_decision, public.ops_task, public.ops_goal,
--                        public.ops_member, public.ops_team cascade;
-- ============================================================================

create table if not exists public.ops_team (
  id smallint generated always as identity primary key,
  slug text not null unique check (slug ~ '^[a-z0-9-]{2,30}$'),
  name text not null check (char_length(name) between 1 and 30),
  kind text not null check (kind in ('office','division','team')),
  parent_id smallint references public.ops_team(id),
  sort_order smallint not null default 0,
  is_pilot boolean not null default false,
  blurb text check (blurb is null or char_length(blurb) <= 60),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  -- 팀은 부문 아래 필수, 실·부문은 최상위(상위 없음).
  constraint ops_team_parent_rule check (
    (kind = 'team' and parent_id is not null) or (kind <> 'team' and parent_id is null))
);
create index if not exists ops_team_parent_idx on public.ops_team (parent_id, sort_order);

create table if not exists public.ops_member (
  id integer generated always as identity primary key,
  team_id smallint not null references public.ops_team(id),
  slug text not null unique check (slug ~ '^[a-z0-9-]{2,40}$'),
  name text not null check (char_length(name) between 1 and 40),
  kind text not null check (kind in ('routine','session','code')),
  ref text check (ref is null or char_length(ref) <= 120),
  model text check (model is null or char_length(model) <= 20),
  state text not null default 'active' check (state in ('active','paused','retired')),
  -- 호출형(개발·독자지원 등 일이 없으면 쉬는 게 정상인 팀원) — '놀고 있음' 판정에서 제외(D6).
  idle_exempt boolean not null default false,
  -- null = 한 번도 기록 안 됨. 지어낸 일수를 만들지 않도록 '활동 기록 없음'으로 따로 센다(123).
  last_active_at timestamptz,
  last_active_note text check (last_active_note is null or char_length(last_active_note) <= 80),
  created_at timestamptz not null default now()
);
create index if not exists ops_member_team_idx on public.ops_member (team_id, state);

create table if not exists public.ops_goal (
  id integer generated always as identity primary key,
  team_id smallint not null references public.ops_team(id),
  slug text not null unique check (slug ~ '^[a-z0-9-]{2,40}$'),
  label text not null check (char_length(label) between 1 and 60),
  unit text not null default '' check (char_length(unit) <= 8),
  direction text not null check (direction in ('gte','lte')),
  -- 선은 전부 null 허용. target null = 목표 미정('측정 필요'). bad_at null = '주의'까지만.
  target numeric,
  warn_at numeric,
  bad_at numeric,
  current_value numeric,
  measured_at timestamptz,
  period_hours integer not null check (period_hours in (24,168,744)),
  measure_kind text not null check (measure_kind in ('sql','github','manual')),
  measure_ref text check (measure_ref is null or char_length(measure_ref) <= 80),
  note text check (note is null or char_length(note) <= 120),
  sort_order smallint not null default 0,
  active boolean not null default true,
  constraint ops_goal_lines_order check (
    case direction
      when 'gte' then
            (bad_at  is null or warn_at is null or bad_at  <= warn_at)
        and (warn_at is null or target  is null or warn_at <= target)
        and (bad_at  is null or target  is null or bad_at  <= target)
      when 'lte' then
            (target  is null or warn_at is null or target  <= warn_at)
        and (warn_at is null or bad_at  is null or warn_at <= bad_at)
        and (target  is null or bad_at  is null or target  <= bad_at)
      else false
    end)
);
create index if not exists ops_goal_team_idx on public.ops_goal (team_id, sort_order);

create table if not exists public.ops_task (
  id bigint generated always as identity primary key,
  team_id smallint not null references public.ops_team(id),   -- 대상 없는 지시 금지(not null)
  member_id integer references public.ops_member(id),
  title text not null check (char_length(title) between 1 and 120),
  detail text check (detail is null or char_length(detail) <= 2000),
  lane text not null default 'inbox' check (lane in ('inbox','doing','review','done')),
  reason text not null default 'directive' check (reason in ('directive','follow_up','routine','fix')),
  created_by uuid references auth.users(id) on delete set null,
  created_kind text not null check (created_kind in ('ceo','secretary')),
  due_on date,
  result_url text check (result_url is null or char_length(result_url) <= 300),
  secretary_seen_at timestamptz,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  done_at timestamptz,
  updated_at timestamptz not null default now()
);
create index if not exists ops_task_lane_due_idx on public.ops_task (lane, due_on);
create index if not exists ops_task_team_lane_idx on public.ops_task (team_id, lane);
create index if not exists ops_task_unseen_idx on public.ops_task (created_at) where secretary_seen_at is null;

create table if not exists public.ops_decision (
  id bigint generated always as identity primary key,
  team_id smallint not null references public.ops_team(id),
  title text not null check (char_length(title) between 1 and 80),         -- 한 줄 결론
  why text check (why is null or char_length(why) <= 200),
  if_approved text check (if_approved is null or char_length(if_approved) <= 200),
  recommendation text check (recommendation is null or char_length(recommendation) <= 60),
  reason text not null check (reason in ('approve','ask')),                 -- 사유 라벨: 승인 요청 | 질문
  due_at timestamptz,
  -- 반려 상태 없음(D5): 거절은 보류 후 업무지시로 답한다.
  state text not null default 'pending' check (state in ('pending','snoozed','approved','withdrawn')),
  snooze_until timestamptz,
  snooze_count smallint not null default 0 check (snooze_count >= 0),
  decided_at timestamptz,
  decided_by uuid references auth.users(id) on delete set null,
  result_note text check (result_note is null or char_length(result_note) <= 200),
  ref_url text check (ref_url is null or char_length(ref_url) <= 300),
  mail_sent_at timestamptz,
  secretary_seen_at timestamptz,
  created_at timestamptz not null default now(),
  constraint ops_decision_snooze_has_time check (state <> 'snoozed' or snooze_until is not null)
);
create index if not exists ops_decision_open_idx on public.ops_decision (due_at)
  where state in ('pending','snoozed');

-- ── RLS·권한 (5개 표 공통) ──────────────────────────────────────────────────
-- 셋(public·anon·authenticated) 모두 명시 회수 → authenticated 에 SELECT 만 → 정책이 관리자만 통과.
-- 쓰기 grant 는 service_role 뿐(Supabase 의 service_role 은 RLS 를 우회한다).
alter table public.ops_team     enable row level security;
alter table public.ops_member   enable row level security;
alter table public.ops_goal     enable row level security;
alter table public.ops_task     enable row level security;
alter table public.ops_decision enable row level security;

revoke all on public.ops_team, public.ops_member, public.ops_goal, public.ops_task, public.ops_decision
  from public, anon, authenticated;
grant select on public.ops_team, public.ops_member, public.ops_goal, public.ops_task, public.ops_decision
  to authenticated;
grant all on public.ops_team, public.ops_member, public.ops_goal, public.ops_task, public.ops_decision
  to service_role;

drop policy if exists "admins read ops_team" on public.ops_team;
create policy "admins read ops_team" on public.ops_team
  for select to authenticated using (private.is_admin());
drop policy if exists "admins read ops_member" on public.ops_member;
create policy "admins read ops_member" on public.ops_member
  for select to authenticated using (private.is_admin());
drop policy if exists "admins read ops_goal" on public.ops_goal;
create policy "admins read ops_goal" on public.ops_goal
  for select to authenticated using (private.is_admin());
drop policy if exists "admins read ops_task" on public.ops_task;
create policy "admins read ops_task" on public.ops_task
  for select to authenticated using (private.is_admin());
drop policy if exists "admins read ops_decision" on public.ops_decision;
create policy "admins read ops_decision" on public.ops_decision
  for select to authenticated using (private.is_admin());

-- identity 열 시퀀스: Supabase 기본 권한이 anon·authenticated 에 시퀀스 사용권을 줄 수 있으므로 같이 회수.
do $$
declare
  t text;
  s text;
begin
  foreach t in array array['ops_team','ops_member','ops_goal','ops_task','ops_decision'] loop
    s := pg_get_serial_sequence('public.' || t, 'id');
    if s is null then
      raise exception '% 의 id 시퀀스를 찾지 못함', t;
    end if;
    execute format('revoke all on sequence %s from public, anon, authenticated', s);
    execute format('grant usage, select on sequence %s to service_role', s);
  end loop;
end $$;
