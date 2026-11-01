-- ============================================================================
-- 097_ops_reports.sql — 종합상황실 일일 보고 표 (홈의 결재함·팀 상태 점의 원천)
--
-- 가산만: 새 테이블 하나. 기존 테이블·RPC·반환 shape 은 건드리지 않는다.
-- 쓰기: service_role 전용 — 종합상황실 예약작업이 부서마다 하루 1행(upsert on day,dept).
--       클라이언트 쓰기 경로 없음(authenticated 에도 insert/update/delete 미부여).
-- 읽기: 관리자만(private.is_admin()) — 요약에 운영 내부 사정이 실릴 수 있어 공개 읽기 금지.
-- 권한: `revoke from public` 만으로는 Supabase 의 anon·authenticated 직접 grant 가 안 지워진다
--       (096 참고) — 셋 다 명시적으로 회수한 뒤 필요한 것만 grant.
--
-- 적용 후 확인(SQL 에디터):
--   select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name='ops_reports' order by 1,2;
--   → authenticated=SELECT, service_role=전체, anon 행 없음이어야 한다.
--   select relrowsecurity from pg_class where oid='public.ops_reports'::regclass;  -- true
-- ============================================================================

create table if not exists public.ops_reports (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  day date not null,
  dept text not null check (char_length(dept) between 1 and 30),
  status text not null check (status in ('normal','notice','problem')),
  summary text check (summary is null or char_length(summary) <= 300),
  needs_decision boolean not null default false,
  constraint ops_reports_day_dept_key unique (day, dept)
);

create index if not exists ops_reports_day_idx on public.ops_reports (day desc);

alter table public.ops_reports enable row level security;

revoke all on public.ops_reports from public;
revoke all on public.ops_reports from anon;
revoke all on public.ops_reports from authenticated;
grant select on public.ops_reports to authenticated;
grant all on public.ops_reports to service_role;

drop policy if exists "admins read ops_reports" on public.ops_reports;
create policy "admins read ops_reports"
on public.ops_reports
for select
to authenticated
using (private.is_admin());
