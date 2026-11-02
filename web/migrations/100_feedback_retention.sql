-- ============================================================================
-- 100_feedback_retention.sql — 문의(user_feedback) '처리 완료 후 1년 지나면 삭제' 구현
--
-- 처리방침 문안: "문의 내용: 처리 완료 후 1년이 지나면 지웁니다."
-- 061 에는 처리 시각이 없어(status 만 있다) 1년을 셀 기준이 없었다. 가산만 한다:
--   1) 컬럼 resolved_at — status 가 done/dismissed 로 바뀐 시각(다시 열면 null)
--   2) BEFORE UPDATE 트리거 — 관리자 트리아지(status 만 갱신)가 그대로 시각을 남긴다
--   3) feedback_purge_resolved(p_days) — resolved_at 이 p_days(기본 365일) 지난 행을 **삭제**
--      (service_role 전용). 미처리(new/in_progress) 행은 절대 지우지 않는다.
-- 기존 테이블·함수·정책은 바꾸지 않는다(061 의 컬럼 한정 update(status) 권한도 그대로 —
-- 트리거가 NEW 를 채우는 것은 권한 대상이 아니다).
--
-- ★적용 시 기존 done/dismissed 행의 resolved_at 은 적용 시각(now())으로 채운다 — 처리 시각을
--   알 수 없으므로 '약속보다 일찍 지우는' 오류를 피하는 쪽(늦게 지움)으로 고른다.
--
-- 적용 후 확인(SQL 에디터):
--   select grantee from information_schema.routine_privileges
--    where routine_schema='public' and routine_name='feedback_purge_resolved';
--   -- 기대: postgres·service_role 만(anon·authenticated 없음).
--   select count(*) filter (where resolved_at is null and status in ('done','dismissed')) from public.user_feedback;
--   -- 기대: 0
-- ============================================================================

alter table public.user_feedback
  add column if not exists resolved_at timestamptz;

create index if not exists user_feedback_resolved_idx
  on public.user_feedback (resolved_at)
  where resolved_at is not null;

create or replace function public.user_feedback_set_resolved_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.status in ('done', 'dismissed') then
    if tg_op = 'INSERT' then
      new.resolved_at := now();
    elsif old.status is distinct from new.status or new.resolved_at is null then
      new.resolved_at := now();
    end if;
  else
    new.resolved_at := null;
  end if;
  return new;
end;
$$;

drop trigger if exists user_feedback_resolved_at on public.user_feedback;
create trigger user_feedback_resolved_at
  before insert or update of status on public.user_feedback
  for each row execute function public.user_feedback_set_resolved_at();

-- 기존 처리 완료 행 — 적용 시각으로 시계를 시작한다(위 ★ 참조).
update public.user_feedback
   set resolved_at = now()
 where status in ('done', 'dismissed') and resolved_at is null;

create or replace function public.feedback_purge_resolved(p_days integer default 365)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  n integer;
begin
  -- 실수로 짧은 기간을 넘겨 일찍 지우는 사고 방지(처리방침 약속은 1년).
  if p_days is null or p_days < 30 then
    raise exception 'feedback_purge_resolved: p_days must be >= 30';
  end if;
  delete from public.user_feedback
   where resolved_at is not null
     and status in ('done', 'dismissed')
     and resolved_at < now() - make_interval(days => p_days);
  get diagnostics n = row_count;
  return n;
end;
$$;

revoke all on function public.feedback_purge_resolved(integer) from public;
revoke execute on function public.feedback_purge_resolved(integer) from anon, authenticated;
grant execute on function public.feedback_purge_resolved(integer) to service_role;
