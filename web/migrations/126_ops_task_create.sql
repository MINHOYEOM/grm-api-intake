-- ============================================================================
-- 126_ops_task_create.sql — 운영 센터 업무 지시 쓰기 (PR5/7, 설계서 2026-11-06 §4-1·§2-4)
--
-- 무엇(전부 새 이름, 가산만 — 122 표·123 ops_home·125 함수와 반환 shape 불변):
--   public.ops_task_create(team, title, detail, due, member)   대표 화면 '업무 지시' 시트가 부르는 유일한 함수.
--                                       함수 안에서 private.is_admin() 검사, 아니면 42501. security definer
--                                       (표 쓰기 권한은 service_role 뿐이라).
--   private.ops_task_create_at(..., actor, now)   본체(시각·행위자를 인자로 받는다 → 시험 가능). 클라이언트 실행 불가.
--
-- 지시 규칙(§4-1):
--   · 팀 필수 — 대상 없는 지시는 받지 않는다. 팀 slug 가 비었거나, 모르는 slug 이거나, 부문(division)·
--     쉬는 칸(active=false)이면 거부. 부문은 상태를 갖지 않는 묶음이라 실 또는 팀을 고른다.
--   · 제목 1~120자(앞뒤 공백 제거 후), 상세 2000자 이하(비면 null), 마감일은 없어도 되지만 KST 오늘보다 앞이면 거부.
--   · 직원(member)을 고르면 그 팀 소속(은퇴 아님)이어야 한다. 안 고르면 비서실이 읽고 담당을 정한다.
--   · 새 행: lane='inbox'(지시함), reason='directive', created_kind='ceo', created_by=auth.uid(),
--     secretary_seen_at=null(비서실 인박스 ops_secretary_inbox 에 뜬다).
--   · 더블 탭: 같은 제목이 10초 안에 다시 오면 거부(대표 지시끼리, 팀 무관). 동시에 두 번 와도 한 건만 들어가도록
--     트랜잭션 잠금(pg_advisory_xact_lock)으로 검사와 삽입을 한 줄로 세운다.
--   · 거부는 예외가 아니라 {ok:false, code, message} — 화면이 그 문장을 그대로 보여 준다. 성공만 감사 기록
--     (public.admin_audit_log 한 줄: 누가·무엇을·어느 행). 42501(관리자 아님)만 예외.
--   · 메일 없음(대표가 방금 보낸 것, §4-4).
--
-- 권한: 새 함수 둘 다 public·anon·authenticated 셋을 명시 회수(10-28: public 만 회수하면 anon 이 남는다).
--   ops_task_create 만 authenticated 에 EXECUTE(안에서 관리자 검사). search_path 는 public 고정.
--
-- 적용: 122·123·125 다음. 비서실이 SQL 검토 후 SQL 에디터에서.
-- 적용 후 확인(SQL 에디터):
--   select routine_schema, routine_name, grantee, privilege_type from information_schema.routine_privileges
--    where routine_name in ('ops_task_create','ops_task_create_at') order by 2,3;
--   → ops_task_create: authenticated(+postgres) · ops_task_create_at: postgres 만. anon·PUBLIC 행이 없어야 한다.
--   일반 회원 토큰으로 rpc('ops_task_create', …) → 42501 이어야 한다.
--
-- 되돌리기(함수만 · 표·데이터는 122 것이라 보존):
--   drop function if exists public.ops_task_create(text, text, text, date, text);
--   drop function if exists private.ops_task_create_at(text, text, text, date, text, uuid, timestamptz);
-- ============================================================================

create schema if not exists private;

-- ── 지시 본체(시각·행위자 고정 — 시험용) ────────────────────────────────────
create or replace function private.ops_task_create_at(
  p_team text, p_title text, p_detail text, p_due date, p_member text, p_actor uuid, p_now timestamptz)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_team public.ops_team%rowtype;
  v_member integer;
  v_title text := btrim(coalesce(p_title, ''));
  v_detail text := nullif(btrim(coalesce(p_detail, '')), '');
  v_member_slug text := nullif(btrim(coalesce(p_member, '')), '');
  v_id bigint;
begin
  -- 팀 필수(대상 없는 지시 금지)
  if nullif(btrim(coalesce(p_team, '')), '') is null then
    return jsonb_build_object('ok', false, 'code', 'no_team', 'message', '팀을 골라 주세요. 맡을 팀이 없으면 보낼 수 없습니다');
  end if;
  select * into v_team from public.ops_team where slug = btrim(p_team);
  if not found or not v_team.active then
    return jsonb_build_object('ok', false, 'code', 'unknown_team', 'message', '없는 팀입니다. 화면을 새로 불러온 뒤 다시 골라 주세요');
  end if;
  if v_team.kind = 'division' then
    return jsonb_build_object('ok', false, 'code', 'not_a_team', 'message', '부문이 아니라 그 아래 팀을 골라 주세요');
  end if;
  -- 길이(122 의 CHECK 와 같은 선 — 예외 대신 문장으로 돌려준다)
  if char_length(v_title) < 1 or char_length(v_title) > 120 then
    return jsonb_build_object('ok', false, 'code', 'title_length', 'message', '제목은 1~120자로 적어 주세요');
  end if;
  if v_detail is not null and char_length(v_detail) > 2000 then
    return jsonb_build_object('ok', false, 'code', 'detail_length', 'message', '내용은 2000자까지 적을 수 있습니다');
  end if;
  if p_due is not null and p_due < (p_now at time zone 'Asia/Seoul')::date then
    return jsonb_build_object('ok', false, 'code', 'due_past', 'message', '마감일이 이미 지났습니다. 오늘 이후로 골라 주세요');
  end if;
  -- 직원은 고르지 않아도 된다. 고르면 그 팀 사람이어야 한다.
  if v_member_slug is not null then
    select id into v_member from public.ops_member
     where slug = v_member_slug and team_id = v_team.id and state <> 'retired';
    if v_member is null then
      return jsonb_build_object('ok', false, 'code', 'member_team', 'message', '고른 직원이 그 팀에 없습니다');
    end if;
  end if;

  -- 더블 탭: 검사와 삽입 사이에 다른 호출이 끼지 않게 잠근다(트랜잭션 끝에 풀림)
  perform pg_advisory_xact_lock(hashtext('ops_task_create'));
  if exists (select 1 from public.ops_task
              where created_kind = 'ceo' and title = v_title
                and created_at > p_now - interval '10 seconds' and created_at <= p_now) then
    return jsonb_build_object('ok', false, 'code', 'duplicate', 'message', '방금 같은 지시를 보냈습니다. 지시함을 확인해 주세요');
  end if;

  insert into public.ops_task (team_id, member_id, title, detail, lane, reason, created_by, created_kind,
                               due_on, secretary_seen_at, created_at, updated_at)
  values (v_team.id, v_member, v_title, v_detail, 'inbox', 'directive', p_actor, 'ceo',
          p_due, null, p_now, p_now)
  returning id into v_id;

  insert into public.admin_audit_log (actor_user_id, action, target_type, target_id, details)
  values (p_actor, 'ops_task_create', 'ops_task', v_id::text,
          jsonb_build_object('team', v_team.slug, 'member', v_member_slug, 'title', v_title, 'due_on', p_due));

  return jsonb_build_object('ok', true, 'id', v_id, 'message', '지시했습니다 · 비서실이 읽고 담당을 정합니다');
end
$$;

-- ── 화면이 부르는 함수: 관리자 검사 → 본체 ──────────────────────────────────
create or replace function public.ops_task_create(
  p_team text, p_title text, p_detail text, p_due date, p_member text default null)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
begin
  -- 회원 가입이 열려 있어 로그인됨 ≠ 관리자. null 도 거부(coalesce).
  if not coalesce(private.is_admin(), false) then
    raise exception 'ops_task_create: admin only' using errcode = '42501';
  end if;
  return private.ops_task_create_at(p_team, p_title, p_detail, p_due, p_member, auth.uid(), now());
end
$$;

-- ── 권한: 셋 다 명시 회수 후 화면 함수만 authenticated 에 ─────────────────────
revoke all on function private.ops_task_create_at(text, text, text, date, text, uuid, timestamptz) from public, anon, authenticated;
revoke all on function public.ops_task_create(text, text, text, date, text) from public, anon, authenticated;
grant execute on function public.ops_task_create(text, text, text, date, text) to authenticated;   -- 호출 허용, 안에서 관리자 검사
