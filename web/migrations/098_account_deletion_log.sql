-- ============================================================================
-- 098_account_deletion_log.sql — 계정 삭제 결과 기록 (Edge Function account-delete 전용)
--
-- 가산만: 새 테이블 하나. 기존 테이블·RPC 는 건드리지 않는다.
-- ★개인정보 0: 이메일·user_id·IP 를 **저장하지 않는다**. 일시·결과·단계별 성공 여부만 남긴다
--   (삭제 요청의 흔적이 또 하나의 개인정보가 되지 않게). 중복 제거용 해시도 두지 않는다.
-- 쓰기: service_role 전용(Edge Function). 읽기: 관리자만(private.is_admin()).
-- 권한: `revoke from public` 만으로는 anon·authenticated 직접 grant 가 안 지워진다(096) —
--       셋 다 명시적으로 회수한 뒤 관리자 select 만 부여.
--
-- 적용 후 확인(SQL 에디터):
--   select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema = 'public' and table_name = 'account_deletion_log';
--   -- 기대: authenticated=SELECT(정책이 관리자만 통과), postgres·service_role. anon 없음.
-- ============================================================================

create table if not exists public.account_deletion_log (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  ok boolean not null,
  -- 실패한 단계 이름(성공이면 null): admin_protected | brevo | feedback | auth
  failed_step text
    check (failed_step is null or failed_step in ('admin_protected','brevo','feedback','auth')),
  -- 단계별 결과 {"brevo":true,"feedback":true,"auth":true} — 불리언만(식별자 금지).
  steps jsonb not null default '{}'::jsonb
);

alter table public.account_deletion_log enable row level security;

revoke all on public.account_deletion_log from public;
revoke all on public.account_deletion_log from anon;
revoke all on public.account_deletion_log from authenticated;
grant select on public.account_deletion_log to authenticated;

drop policy if exists "admins read account deletion log" on public.account_deletion_log;
create policy "admins read account deletion log"
on public.account_deletion_log
for select
to authenticated
using (private.is_admin());
