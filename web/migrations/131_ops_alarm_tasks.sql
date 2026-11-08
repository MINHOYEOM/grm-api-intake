-- ============================================================================
-- 131_ops_alarm_tasks.sql — 경보에 주인 붙이기 (운영 센터 후속, 설계서 2026-11-06 §4-1 '업무' 확장)
--
-- 왜: 자동 감시가 GitHub 이슈로 경보를 열어도 아무도 처리하지 않았다(11-07 기준 4건 댓글 0개 방치).
--   경보 이슈 1건 = 담당 팀의 업무 1건(지시함)으로 만들고, 이슈가 닫히면 완료, 3일 넘게 지시함에서
--   안 움직이면 종합상황실에 '방치 경보' 업무를 하나 더 만들어 Admin 운영 센터에 빨간 표시를 띄운다.
--
-- 무엇(전부 새 이름, 가산만 — 122 표·125·126 함수와 123 ops_home_at 반환 shape 불변):
--   public.ops_alarm                   경보 이슈 ↔ 업무 연결 표(이슈 번호 = 멱등 키). 쓰기·읽기 service_role 전용.
--   public.ops_alarm_upsert(...)       이슈 상태를 받아 업무를 만들고/완료하고/되살린다(서비스 함수).
--   public.ops_alarm_neglected(now)    방치 경보 목록(읽기 전용 · 드라이런과 화면이 같은 정의를 쓴다).
--   public.ops_alarm_neglect_scan(now) 방치 경보를 종합상황실 업무로 올리고(이슈당 1회) 새로 올린 목록을 돌려준다.
--   private.ops_alarm_summary(now)     ops_home 에 붙는 'alarms' 한 덩어리(빨간 표시의 원천).
--   public.ops_home()                  ★재정의: 반환 = ops_home_at(now()) 그대로 + 'alarms' 키 하나. 기존 키는 하나도
--                                      안 바뀐다(가산만). 관리자 검사(42501)·security definer·search_path 는 123 과 같다.
--                                      123 을 다시 적용하면 이 정의가 123 판으로 되돌아간다 → 123 다음 131 순서 고정.
--
-- 규칙:
--   · 새 이슈(열림) → 업무: lane='inbox'(지시함) · reason='fix' · created_kind='secretary' · created_by=null ·
--     title=이슈 제목(120자) · result_url=이슈 URL · secretary_seen_at=null(비서실 인박스 ops_secretary_inbox 에 뜬다).
--     같은 이슈 번호로 두 번 와도 업무는 하나(ops_alarm.issue_number 기본키 + 이슈별 advisory lock).
--   · 닫힘이면 업무 lane='done'+done_at, 방치 업무가 열려 있으면 그것도 완료. 닫힌 채 처음 보는 이슈는 만들지 않는다.
--   · 다시 열리면 업무를 되살린다(lane='inbox', done_at·started_at 비움, 비서실이 다시 읽게 secretary_seen_at 비움,
--     방치 표시 초기화). 3일 시계는 ops_task.updated_at 으로 센다 — 만들 때·닫을 때·되살릴 때·칸을 옮길 때만 갱신한다
--     (제목·팀 동기화는 시계를 건드리지 않는다).
--   · 팀 slug 는 부문(division)·쉬는 칸·모르는 slug 이면 종합상황실(situation-room)로 떨어진다(경보를 버리지 않는다).
--     팀이 라벨 때문에 바뀌면 아직 지시함에 있는 업무만 옮긴다(맡은 직원은 비운다).
--   · 방치 = 이슈 열림 + 업무 지시함(inbox) + updated_at 이 3일(72시간) '초과' 전. 한 곳(ops_alarm_neglected)에서만 정의.
--   · 메일·외부 발송 없음. 이 파일은 cron 도 만들지 않는다(GitHub Actions 가 부른다).
--
-- 권한: 새 표·함수 전부 public·anon·authenticated 를 명시 회수(10-28: public 만 회수하면 anon 이 남는다).
--   표는 RLS 켜고 정책 없음 + service_role 에만 grant → 일반 회원·관리자 토큰으로도 직접 못 읽는다(화면은 ops_home).
--   서비스 함수는 service_role 에만 EXECUTE, security invoker(125 관례). search_path 는 public 고정.
--   ops_home 만 authenticated 에 EXECUTE(안에서 관리자 검사).
--
-- 적용: 122·123·125·126 다음(123 → 131 순서 필수). 비서실이 SQL 검토 후 SQL 에디터에서.
-- 적용 후 확인(SQL 에디터):
--   select table_name, grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name='ops_alarm' order by 2,3;       -- service_role 만(anon·authenticated 행 없음)
--   select relname, relrowsecurity from pg_class where relname='ops_alarm';       -- true
--   select routine_schema, routine_name, grantee, privilege_type from information_schema.routine_privileges
--    where routine_name in ('ops_alarm_upsert','ops_alarm_neglected','ops_alarm_neglect_scan','ops_alarm_summary','ops_home')
--    order by 2,3;
--   → ops_alarm_* 공개 3개: service_role(+postgres) · ops_alarm_summary: postgres 만 · ops_home: authenticated(+postgres).
--     anon·PUBLIC 행이 하나도 없어야 한다.
--   select jsonb_object_keys(private.ops_home_at(now())) except … 는 필요 없다 — 일반 홈 호출은 관리자 토큰으로:
--   rpc('ops_home') 응답 키 = 기존 10개 + alarms.
--
-- 되돌리기(이 파일 것만 · 123 판 ops_home 으로 복귀하려면 123 의 ops_home 정의를 다시 실행):
--   drop function if exists public.ops_alarm_neglect_scan(timestamptz);
--   drop function if exists public.ops_alarm_neglected(timestamptz);
--   drop function if exists public.ops_alarm_upsert(integer, text, text, text, text[], text, timestamptz);
--   (ops_home 을 123 판으로 되돌린 뒤)  drop function if exists private.ops_alarm_summary(timestamptz);
--   drop table if exists public.ops_alarm;      -- 만들어진 업무(ops_task)는 남는다
-- ============================================================================

create schema if not exists private;

-- ── 연결 표 ────────────────────────────────────────────────────────────────
create table if not exists public.ops_alarm (
  issue_number integer primary key check (issue_number > 0),
  task_id bigint not null unique references public.ops_task(id),
  issue_url text not null check (char_length(issue_url) <= 300),
  labels text[] not null default '{}',
  issue_state text not null default 'open' check (issue_state in ('open','closed')),
  -- 방치 경보: 종합상황실에 올린 업무와 올린 시각. null = 아직 안 올림(이슈당 1회).
  neglect_task_id bigint references public.ops_task(id),
  neglect_flagged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists ops_alarm_open_idx on public.ops_alarm (issue_state) where issue_state = 'open';

alter table public.ops_alarm enable row level security;
revoke all on public.ops_alarm from public, anon, authenticated;
grant all on public.ops_alarm to service_role;

-- ── 이슈 → 업무 ─────────────────────────────────────────────────────────────
create or replace function public.ops_alarm_upsert(
  p_issue integer, p_title text, p_url text, p_state text, p_labels text[], p_team text,
  p_now timestamptz default now())
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_team public.ops_team%rowtype;
  v_link public.ops_alarm%rowtype;
  v_task public.ops_task%rowtype;
  v_title text := left(btrim(coalesce(p_title, '')), 120);
  v_labels text[] := coalesce(p_labels, '{}');
  v_id bigint;
  v_changed boolean := false;
begin
  if p_issue is null or p_issue <= 0 then
    raise exception 'ops_alarm_upsert: bad issue number' using errcode = '22023';
  end if;
  if p_state is null or p_state not in ('open','closed') then
    raise exception 'ops_alarm_upsert: state must be open or closed' using errcode = '22023';
  end if;
  if coalesce(p_url, '') !~ '^https://github\.com/[^ ]+$' or char_length(p_url) > 300 then
    raise exception 'ops_alarm_upsert: bad issue url' using errcode = '22023';
  end if;
  if v_title = '' then
    v_title := '경보 이슈 #' || p_issue;
  end if;

  -- 팀: 모르는 slug·부문·쉬는 칸이면 종합상황실로(경보를 버리지 않는다)
  select * into v_team from public.ops_team
   where slug = btrim(coalesce(p_team, '')) and active and kind <> 'division';
  if not found then
    select * into v_team from public.ops_team where slug = 'situation-room' and active;
    if not found then
      raise exception 'ops_alarm_upsert: situation-room team missing' using errcode = '22023';
    end if;
  end if;

  -- 같은 이슈의 동시 호출(issues 이벤트 + 일일 동기화)을 한 줄로 세운다(트랜잭션 끝에 풀림)
  perform pg_advisory_xact_lock(hashtext('ops_alarm'), p_issue);

  select * into v_link from public.ops_alarm where issue_number = p_issue for update;
  if not found then
    if p_state = 'closed' then
      return jsonb_build_object('ok', true, 'action', 'skipped_closed', 'issue', p_issue);
    end if;
    insert into public.ops_task (team_id, member_id, title, detail, lane, reason, created_by, created_kind,
                                 result_url, secretary_seen_at, created_at, updated_at)
    values (v_team.id, null, v_title,
            left('GitHub 경보 이슈 #' || p_issue || ' (라벨: ' || array_to_string(v_labels, ', ') ||
                 '). 처리한 뒤 이슈를 닫으면 이 업무가 자동으로 완료됩니다.', 2000),
            'inbox', 'fix', null, 'secretary', p_url, null, p_now, p_now)
    returning id into v_id;
    insert into public.ops_alarm (issue_number, task_id, issue_url, labels, issue_state, created_at, updated_at)
    values (p_issue, v_id, p_url, v_labels, 'open', p_now, p_now);
    return jsonb_build_object('ok', true, 'action', 'created', 'issue', p_issue, 'task_id', v_id, 'team', v_team.slug);
  end if;

  select * into v_task from public.ops_task where id = v_link.task_id for update;

  if p_state = 'closed' then
    if v_task.lane = 'done' and v_link.issue_state = 'closed' then
      return jsonb_build_object('ok', true, 'action', 'unchanged', 'issue', p_issue, 'task_id', v_task.id);
    end if;
    update public.ops_task
       set lane = 'done', done_at = coalesce(done_at, p_now), updated_at = p_now
     where id = v_task.id and lane <> 'done';
    -- 방치 경보 업무가 열려 있으면 같이 끝낸다(원인이 풀렸다)
    update public.ops_task
       set lane = 'done', done_at = coalesce(done_at, p_now), updated_at = p_now
     where id = v_link.neglect_task_id and lane <> 'done';
    update public.ops_alarm set issue_state = 'closed', labels = v_labels, updated_at = p_now
     where issue_number = p_issue;
    return jsonb_build_object('ok', true, 'action', 'closed', 'issue', p_issue, 'task_id', v_task.id);
  end if;

  -- 열림
  -- 되살리기는 '닫혔던 이슈가 다시 열린' 경우만. 이슈가 열려 있는데 사람이 업무를 먼저 완료로 옮긴 것은 건드리지 않는다.
  if v_link.issue_state = 'closed' then
    update public.ops_task
       set lane = 'inbox', done_at = null, started_at = null, secretary_seen_at = null,
           team_id = v_team.id, member_id = case when team_id = v_team.id then member_id else null end,
           title = v_title, updated_at = p_now
     where id = v_task.id;
    update public.ops_alarm
       set issue_state = 'open', labels = v_labels, neglect_task_id = null, neglect_flagged_at = null, updated_at = p_now
     where issue_number = p_issue;
    return jsonb_build_object('ok', true, 'action', 'reopened', 'issue', p_issue, 'task_id', v_task.id, 'team', v_team.slug);
  end if;

  -- 이미 열려 있음: 제목·팀·라벨만 맞춘다. updated_at(3일 시계)은 건드리지 않는다.
  if v_task.title is distinct from v_title then
    update public.ops_task set title = v_title where id = v_task.id;
    v_changed := true;
  end if;
  if v_task.lane = 'inbox' and v_task.team_id is distinct from v_team.id then
    update public.ops_task set team_id = v_team.id, member_id = null where id = v_task.id;
    v_changed := true;
  end if;
  if v_link.labels is distinct from v_labels then
    update public.ops_alarm set labels = v_labels where issue_number = p_issue;
  end if;
  return jsonb_build_object('ok', true, 'action', case when v_changed then 'updated' else 'unchanged' end,
                            'issue', p_issue, 'task_id', v_task.id);
end
$$;

-- ── 방치 목록(정의는 여기 한 곳) ─────────────────────────────────────────────
-- 열린 경보 이슈의 업무가 지시함에서 3일(72시간) 넘게 안 움직였다. flagged = 이미 종합상황실에 올렸는가.
create or replace function public.ops_alarm_neglected(p_now timestamptz default now())
returns table (issue_number integer, issue_url text, task_id bigint, title text, team text,
               since timestamptz, days integer, flagged boolean)
language sql
stable
set search_path = public
as $$
  select a.issue_number, a.issue_url, t.id, t.title, tm.slug, t.updated_at,
         floor(extract(epoch from (p_now - t.updated_at)) / 86400)::int,
         (a.neglect_flagged_at is not null)
    from public.ops_alarm a
    join public.ops_task t on t.id = a.task_id
    join public.ops_team tm on tm.id = t.team_id
   where a.issue_state = 'open'
     and t.lane = 'inbox'
     and p_now - t.updated_at > interval '3 days'
$$;

-- ── 방치 경보 올리기(이슈당 1회) ─────────────────────────────────────────────
create or replace function public.ops_alarm_neglect_scan(p_now timestamptz default now())
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_sr smallint;
  v_id bigint;
  r record;
  v_out jsonb := '[]'::jsonb;
begin
  select id into v_sr from public.ops_team where slug = 'situation-room' and active;
  if v_sr is null then
    raise exception 'ops_alarm_neglect_scan: situation-room team missing' using errcode = '22023';
  end if;
  for r in select * from public.ops_alarm_neglected(p_now) n where not n.flagged order by n.issue_number loop
    perform pg_advisory_xact_lock(hashtext('ops_alarm'), r.issue_number);
    -- 잠근 뒤 다시 확인: 그 사이 다른 호출이 올렸으면 건너뛴다
    if exists (select 1 from public.ops_alarm where issue_number = r.issue_number and neglect_flagged_at is null) then
      insert into public.ops_task (team_id, title, detail, lane, reason, created_kind, result_url, created_at, updated_at)
      values (v_sr, left('방치 경보: #' || r.issue_number || ' ' || r.title, 120),
              left('경보 이슈 #' || r.issue_number || ' 업무가 ' || r.days || '일째 지시함에 머물러 있습니다(' ||
                   r.team || '). 담당을 정하거나 이슈를 처리해 주세요. 이슈가 닫히면 이 업무도 자동으로 완료됩니다.', 2000),
              'inbox', 'fix', 'secretary', r.issue_url, p_now, p_now)
      returning id into v_id;
      update public.ops_alarm set neglect_task_id = v_id, neglect_flagged_at = p_now, updated_at = p_now
       where issue_number = r.issue_number;
      v_out := v_out || jsonb_build_array(jsonb_build_object(
        'issue', r.issue_number, 'url', r.issue_url, 'team', r.team, 'days', r.days, 'task_id', v_id));
    end if;
  end loop;
  return v_out;
end
$$;

-- ── 홈에 붙는 요약(빨간 표시의 원천) ─────────────────────────────────────────
create or replace function private.ops_alarm_summary(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  select jsonb_build_object(
    'open', (select count(*) from public.ops_alarm a join public.ops_task t on t.id = a.task_id
              where a.issue_state = 'open' and t.lane <> 'done'),
    'neglected', (select count(*) from public.ops_alarm_neglected(p_now)),
    'neglect_days', 3,
    'items', coalesce((
      select jsonb_agg(jsonb_build_object('issue', n.issue_number, 'url', n.issue_url, 'title', n.title,
                                          'team', n.team, 'days', n.days) order by n.since, n.issue_number)
      from (select * from public.ops_alarm_neglected(p_now) order by since, issue_number limit 5) n), '[]'::jsonb))
$$;

-- ── ops_home: 기존 반환에 'alarms' 한 키만 더한다 ────────────────────────────
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
  return private.ops_home_at(now()) || jsonb_build_object('alarms', private.ops_alarm_summary(now()));
end
$$;

-- ── 권한 ────────────────────────────────────────────────────────────────────
revoke all on function public.ops_alarm_upsert(integer, text, text, text, text[], text, timestamptz) from public, anon, authenticated;
revoke all on function public.ops_alarm_neglected(timestamptz) from public, anon, authenticated;
revoke all on function public.ops_alarm_neglect_scan(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_alarm_summary(timestamptz) from public, anon, authenticated;
revoke all on function public.ops_home() from public, anon, authenticated;
grant execute on function public.ops_alarm_upsert(integer, text, text, text, text[], text, timestamptz) to service_role;
grant execute on function public.ops_alarm_neglected(timestamptz) to service_role;
grant execute on function public.ops_alarm_neglect_scan(timestamptz) to service_role;
grant execute on function public.ops_home() to authenticated;   -- 호출 허용, 안에서 관리자 검사
