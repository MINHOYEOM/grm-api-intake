-- ============================================================================
-- 125_ops_decision_write.sql — 운영 센터 결정 쓰기 + 비서실 서비스 함수 (PR4/7, 설계서 2026-11-06 §2-5·§4)
--
-- 무엇(전부 새 이름, 가산만 — 122 표·123 ops_home 반환 shape 불변):
--   public.ops_decide(id, action, snooze)   대표 화면이 부르는 유일한 쓰기 함수. 함수 안에서 private.is_admin()
--                                           검사, 아니면 42501. security definer(표 쓰기 권한은 service_role 뿐이라).
--   private.ops_decide_at(...)              본체(시각·행위자를 인자로 받는다 → 시험 가능). 클라이언트 실행 불가.
--   private.ops_snooze_until(now, kind)     보류 다시 올라오는 시각 = KST 08:00(내일 | 3일 뒤). 순수 함수.
--   서비스 함수(service_role 만 — 로컬 비서실 세션·코드가 부른다, 화면은 못 부른다):
--     public.ops_decision_open(...)        결정 올리기(발행 PR 은 거부 — 홈에 두 번 보이지 않게)
--     public.ops_secretary_inbox()         새 지시·처리된 결정을 돌려주고 읽음 표시
--     public.ops_member_touch(slug, note, at)   팀원 마지막 활동 기록
--     public.ops_goal_set(slug, value, note, at) 목표 현재값·측정 시각·메모
--     public.ops_task_move(id, lane, member, result_url)  할 일 칸 이동(완료는 결과 링크 필수)
--
-- 결정 규칙(§4-2):
--   · 바뀌는 행은 state in ('pending','snoozed') 뿐 — 이미 승인·철회된 결정은 다시 바꾸지 않는다.
--     한 문장 update … where state in (…) 이라 폰·PC 에서 동시에 눌러도 한 번만 반영된다.
--     이미 처리됐으면 예외가 아니라 {ok:false, code:'already_decided', message:'이미 처리된 결정입니다'}.
--   · 승인 = '비서실이 권장대로 진행해도 됨'. 외부 발송·머지·돈이 드는 일은 자동 실행되지 않는다.
--     decided_at·decided_by 를 남기고 secretary_seen_at 을 비워 비서실 인박스에 올린다.
--   · 보류 = snooze_until(KST 08:00) 까지 홈에서 사라졌다가 같은 행이 다시 올라온다(메일 재발송 없음).
--     snooze_count + 1. decided_* 는 승인 때만 쓴다(보류는 결정이 아니다).
--   · 반려 없음(D5). 모든 처리는 public.admin_audit_log 에 한 줄(누가·무엇을·어느 행).
--
-- 권한: 새 함수 전부 public·anon·authenticated 셋을 명시 회수(10-28: public 만 회수하면 anon 이 남는다).
--   ops_decide 만 authenticated 에 EXECUTE(안에서 관리자 검사). 서비스 함수는 service_role 에만 EXECUTE,
--   security invoker(service_role 은 122 에서 표 전체 권한) — 쓸데없이 definer 를 늘리지 않는다.
--   search_path 는 전부 public 고정.
--
-- 적용: 122·123 다음. 비서실이 SQL 검토 후 SQL 에디터에서.
-- 적용 후 확인(SQL 에디터):
--   select routine_schema, routine_name, grantee, privilege_type from information_schema.routine_privileges
--    where routine_name in ('ops_decide','ops_decide_at','ops_snooze_until','ops_decision_open',
--                           'ops_secretary_inbox','ops_member_touch','ops_goal_set','ops_task_move')
--    order by 2,3;
--   → ops_decide: authenticated(+postgres) · 서비스 함수 5개: service_role(+postgres) · private 2개: postgres 만.
--     anon·PUBLIC 행이 하나도 없어야 한다.
--   일반 회원 토큰으로 rpc('ops_decide', …) → 42501 이어야 한다.
--
-- 되돌리기(함수만 · 표·데이터는 122 것이라 보존):
--   drop function if exists public.ops_decide(bigint, text, text);
--   drop function if exists private.ops_decide_at(bigint, text, text, uuid, timestamptz);
--   drop function if exists private.ops_snooze_until(timestamptz, text);
--   drop function if exists public.ops_decision_open(text, text, text, text, text, text, timestamptz, text);
--   drop function if exists public.ops_secretary_inbox();
--   drop function if exists public.ops_member_touch(text, text, timestamptz);
--   drop function if exists public.ops_goal_set(text, numeric, text, timestamptz);
--   drop function if exists public.ops_task_move(bigint, text, text, text);
-- ============================================================================

create schema if not exists private;

-- ── 보류 시각: KST 날짜 + n일의 08:00 ────────────────────────────────────────
create or replace function private.ops_snooze_until(p_now timestamptz, p_kind text)
returns timestamptz
language sql
immutable
set search_path = public
as $$
  select case p_kind
    when 'tomorrow' then (((p_now at time zone 'Asia/Seoul')::date + 1) + time '08:00') at time zone 'Asia/Seoul'
    when '3d'       then (((p_now at time zone 'Asia/Seoul')::date + 3) + time '08:00') at time zone 'Asia/Seoul'
  end
$$;

-- ── 결정 처리 본체(시각·행위자 고정 — 시험용) ───────────────────────────────
create or replace function private.ops_decide_at(
  p_id bigint, p_action text, p_snooze text, p_actor uuid, p_now timestamptz)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_row public.ops_decision%rowtype;
  v_until timestamptz;
  v_state text;
begin
  if p_action is null or p_action not in ('approve','snooze') then
    raise exception 'ops_decide: action must be approve or snooze' using errcode = '22023';
  end if;
  if p_action = 'snooze' then
    if p_snooze is null or p_snooze not in ('tomorrow','3d') then
      raise exception 'ops_decide: snooze must be tomorrow or 3d' using errcode = '22023';
    end if;
    v_until := private.ops_snooze_until(p_now, p_snooze);
  end if;

  if p_action = 'approve' then
    update public.ops_decision
       set state = 'approved', decided_at = p_now, decided_by = p_actor,
           snooze_until = null, secretary_seen_at = null
     where id = p_id and state in ('pending','snoozed')
     returning * into v_row;
  else
    update public.ops_decision
       set state = 'snoozed', snooze_until = v_until, snooze_count = snooze_count + 1,
           secretary_seen_at = null
     where id = p_id and state in ('pending','snoozed')
     returning * into v_row;
  end if;

  if not found then
    select state into v_state from public.ops_decision where id = p_id;
    if v_state is null then
      return jsonb_build_object('ok', false, 'code', 'not_found', 'id', p_id,
                                'message', '결정을 찾지 못했습니다');
    end if;
    return jsonb_build_object('ok', false, 'code', 'already_decided', 'id', p_id, 'state', v_state,
                              'message', '이미 처리된 결정입니다');
  end if;

  insert into public.admin_audit_log (actor_user_id, action, target_type, target_id, details)
  values (p_actor, 'ops_decide', 'ops_decision', p_id::text,
          jsonb_build_object('action', p_action, 'snooze', p_snooze, 'snooze_until', v_until, 'title', v_row.title));

  return jsonb_build_object(
    'ok', true, 'id', v_row.id, 'state', v_row.state, 'snooze_until', v_row.snooze_until,
    'message', case when p_action = 'approve' then '승인됨 · 비서실이 이어서 진행합니다'
                    else '보류 · ' || to_char(v_until at time zone 'Asia/Seoul', 'MM-DD HH24:MI') || ' 에 다시 올라옵니다' end);
end
$$;

-- ── 화면이 부르는 함수: 관리자 검사 → 본체 ──────────────────────────────────
create or replace function public.ops_decide(p_id bigint, p_action text, p_snooze text default null)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
begin
  -- 회원 가입이 열려 있어 로그인됨 ≠ 관리자. null 도 거부(coalesce).
  if not coalesce(private.is_admin(), false) then
    raise exception 'ops_decide: admin only' using errcode = '42501';
  end if;
  return private.ops_decide_at(p_id, p_action, p_snooze, auth.uid(), now());
end
$$;

-- ── 서비스 함수(service_role 전용) ──────────────────────────────────────────
-- 결정 올리기. 저장은 대기 건수와 무관하게 되고(홈은 3건 + 더 보기), 대기 건수를 같이 돌려준다.
create or replace function public.ops_decision_open(
  p_team text, p_title text, p_why text, p_if_approved text, p_recommendation text,
  p_reason text, p_due_at timestamptz, p_ref_url text)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_team smallint;
  v_id bigint;
  v_open int;
begin
  select id into v_team from public.ops_team where slug = p_team;
  if v_team is null then
    raise exception 'ops_decision_open: unknown team %', p_team using errcode = '22023';
  end if;
  -- 월요일 발행 PR 은 '발행·운영' 승인 흐름이 따로 있다 — 결정 대기에 넣으면 두 번 보이고 두 번 눌린다.
  if coalesce(p_ref_url, '') ~ 'publish/brief-' then
    raise exception 'ops_decision_open: publish PRs are approved in the publish tab' using errcode = '22023';
  end if;
  insert into public.ops_decision (team_id, title, why, if_approved, recommendation, reason, due_at, ref_url,
                                   secretary_seen_at)
  values (v_team, p_title, p_why, p_if_approved, p_recommendation, p_reason, p_due_at, p_ref_url,
          now())   -- 비서실이 올린 행은 비서실이 이미 안다 — 인박스에는 대표가 처리한 뒤에만 뜬다
  returning id into v_id;
  select count(*) into v_open from public.ops_decision
   where state = 'pending' or (state = 'snoozed' and snooze_until <= now());
  return jsonb_build_object('id', v_id, 'open', v_open, 'over_home_cap', v_open > 3);
end
$$;

-- 비서실 인박스: 새 지시(아직 안 읽은 할 일)·대표가 처리한 결정을 돌려주고 읽음 표시.
create or replace function public.ops_secretary_inbox()
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_tasks jsonb;
  v_decisions jsonb;
begin
  with t as (
    update public.ops_task x set secretary_seen_at = now()
      from public.ops_team tm
     where x.secretary_seen_at is null and tm.id = x.team_id
    returning x.id, tm.slug as team, x.title, x.detail, x.lane, x.due_on, x.created_kind, x.created_at
  )
  select coalesce(jsonb_agg(to_jsonb(t) order by t.created_at, t.id), '[]'::jsonb) into v_tasks from t;
  with d as (
    update public.ops_decision x set secretary_seen_at = now()
      from public.ops_team tm
     where x.secretary_seen_at is null and x.state in ('approved','snoozed') and tm.id = x.team_id
    returning x.id, tm.slug as team, x.title, x.state, x.decided_at, x.snooze_until, x.recommendation
  )
  select coalesce(jsonb_agg(to_jsonb(d) order by d.id), '[]'::jsonb) into v_decisions from d;
  return jsonb_build_object('tasks', v_tasks, 'decisions', v_decisions);
end
$$;

create or replace function public.ops_member_touch(p_slug text, p_note text default null, p_at timestamptz default null)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare v_id int;
begin
  update public.ops_member
     set last_active_at = coalesce(p_at, now()), last_active_note = p_note
   where slug = p_slug
   returning id into v_id;
  if v_id is null then
    raise exception 'ops_member_touch: unknown member %', p_slug using errcode = '22023';
  end if;
  return jsonb_build_object('slug', p_slug, 'last_active_at', coalesce(p_at, now()));
end
$$;

create or replace function public.ops_goal_set(p_slug text, p_value numeric, p_note text default null,
                                               p_at timestamptz default null)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare v_unit text;
begin
  select unit into v_unit from public.ops_goal where slug = p_slug;
  if not found then
    raise exception 'ops_goal_set: unknown goal %', p_slug using errcode = '22023';
  end if;
  if p_value is null or p_value = 'NaN'::numeric then
    raise exception 'ops_goal_set: value required' using errcode = '22023';
  end if;
  if v_unit = '%' and (p_value < 0 or p_value > 100) then
    raise exception 'ops_goal_set: percent out of range %', p_value using errcode = '22023';
  end if;
  update public.ops_goal
     set current_value = p_value, measured_at = coalesce(p_at, now()), note = coalesce(p_note, note)
   where slug = p_slug;
  return jsonb_build_object('slug', p_slug, 'value', p_value, 'measured_at', coalesce(p_at, now()));
end
$$;

create or replace function public.ops_task_move(p_id bigint, p_lane text, p_member text default null,
                                                p_result_url text default null)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_task public.ops_task%rowtype;
  v_member int;
begin
  if p_lane is null or p_lane not in ('inbox','doing','review','done') then
    raise exception 'ops_task_move: bad lane %', p_lane using errcode = '22023';
  end if;
  select * into v_task from public.ops_task where id = p_id for update;
  if not found then
    raise exception 'ops_task_move: unknown task %', p_id using errcode = '22023';
  end if;
  if v_task.lane = 'done' then
    raise exception 'ops_task_move: task % is already done', p_id using errcode = '22023';
  end if;
  if p_lane = 'done' and coalesce(p_result_url, v_task.result_url) is null then
    raise exception 'ops_task_move: done needs a result link' using errcode = '22023';
  end if;
  if p_member is not null then
    select id into v_member from public.ops_member where slug = p_member and team_id = v_task.team_id;
    if v_member is null then
      raise exception 'ops_task_move: member % is not in the task team', p_member using errcode = '22023';
    end if;
  end if;
  update public.ops_task
     set lane = p_lane,
         member_id = coalesce(v_member, member_id),
         result_url = coalesce(p_result_url, result_url),
         started_at = case when p_lane in ('doing','review','done') then coalesce(started_at, now()) else started_at end,
         done_at = case when p_lane = 'done' then now() else done_at end,
         updated_at = now()
   where id = p_id;
  return jsonb_build_object('id', p_id, 'lane', p_lane);
end
$$;

-- ── 권한: 셋 다 명시 회수 후 필요한 곳에만 ─────────────────────────────────────
revoke all on function private.ops_snooze_until(timestamptz, text) from public, anon, authenticated;
revoke all on function private.ops_decide_at(bigint, text, text, uuid, timestamptz) from public, anon, authenticated;
revoke all on function public.ops_decide(bigint, text, text) from public, anon, authenticated;
grant execute on function public.ops_decide(bigint, text, text) to authenticated;   -- 호출 허용, 안에서 관리자 검사

revoke all on function public.ops_decision_open(text, text, text, text, text, text, timestamptz, text) from public, anon, authenticated;
revoke all on function public.ops_secretary_inbox() from public, anon, authenticated;
revoke all on function public.ops_member_touch(text, text, timestamptz) from public, anon, authenticated;
revoke all on function public.ops_goal_set(text, numeric, text, timestamptz) from public, anon, authenticated;
revoke all on function public.ops_task_move(bigint, text, text, text) from public, anon, authenticated;
grant execute on function public.ops_decision_open(text, text, text, text, text, text, timestamptz, text) to service_role;
grant execute on function public.ops_secretary_inbox() to service_role;
grant execute on function public.ops_member_touch(text, text, timestamptz) to service_role;
grant execute on function public.ops_goal_set(text, numeric, text, timestamptz) to service_role;
grant execute on function public.ops_task_move(bigint, text, text, text) to service_role;
