-- ============================================================================
-- 099_account_scrub_feedback.sql — 계정 삭제 시 문의(user_feedback)의 이메일만 지우는 RPC
--
-- user_feedback 은 auth.users 와 FK 가 없다(061) — 회신 동의가 있을 때 이메일 문자열만 저장.
-- 그래서 계정 삭제가 cascade 로 못 지운다. 결정(대표 2026-11-02): **본문은 보존**, 이메일을
-- NULL 로 비우고 contact_consent=false 로 되돌린다(061 CHECK: email 이 있으면 동의 필수 — 둘 다
-- 같이 내려야 제약을 지킨다).
--
-- security definer + service_role 전용 EXECUTE. Supabase 는 public 스키마 함수에 anon·authenticated
-- EXECUTE 를 직접 주므로(096) 둘을 명시적으로 회수한다. 반환은 지운 행 수(이메일 미반환).
--
-- 적용 후 확인(SQL 에디터):
--   select grantee from information_schema.routine_privileges
--    where routine_schema = 'public' and routine_name = 'account_scrub_feedback';
--   -- 기대: postgres·service_role 만(anon·authenticated 없음).
-- ============================================================================

create or replace function public.account_scrub_feedback(p_email text)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  n integer;
begin
  if p_email is null or length(btrim(p_email)) = 0 then
    return 0;
  end if;
  update public.user_feedback
     set email = null,
         contact_consent = false
   where email is not null
     and lower(email) = lower(btrim(p_email));
  get diagnostics n = row_count;
  return n;
end;
$$;

revoke all on function public.account_scrub_feedback(text) from public;
revoke execute on function public.account_scrub_feedback(text) from anon, authenticated;
grant execute on function public.account_scrub_feedback(text) to service_role;
