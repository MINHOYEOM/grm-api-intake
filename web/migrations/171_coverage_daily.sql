begin;

create table public.coverage_daily (
  day date primary key,
  mfds_admin jsonb not null check (jsonb_typeof(mfds_admin) = 'object'),
  mfds_recall jsonb not null check (jsonb_typeof(mfds_recall) = 'object')
);

alter table public.coverage_daily enable row level security;
revoke all on table public.coverage_daily from public, anon, authenticated;
grant select, insert, update on table public.coverage_daily to service_role;

comment on table public.coverage_daily is
  'KST 날짜당 한 행. 각 소스의 측정 상태, 비교 기간, 원천·DB·제외·중복 건수를 기록한다.';

commit;
