-- 071: 깔때기 일별 스냅샷 — funnel_counts(060, 누적 카운터)만으로는 "어제 몇 건"을
-- 알 수 없어, 매일 23:55 KST(14:55 UTC, pg_cron)에 누적치를 날짜별로 동결한다.
-- 일별 활동량은 인접 스냅샷의 차분으로 읽는다(판독은 /admin 성장·유입 패널).
-- 무PII 계약은 060 과 동일: key 문자열과 정수 합계만 저장.

create extension if not exists pg_cron;

create table if not exists public.funnel_counts_daily (
  snap_date date not null,
  key text not null
    check (key in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss')),
  total integer not null check (total >= 0),
  primary key (snap_date, key)
);

alter table public.funnel_counts_daily enable row level security;

revoke all on public.funnel_counts_daily from public;
revoke all on public.funnel_counts_daily from anon;
revoke all on public.funnel_counts_daily from authenticated;
grant select on public.funnel_counts_daily to anon, authenticated;

drop policy if exists "public can read funnel snapshots" on public.funnel_counts_daily;
create policy "public can read funnel snapshots"
on public.funnel_counts_daily
for select
to anon, authenticated
using (true);

-- 같은 날 재실행은 최신 누적치로 갱신(멱등) — 스냅샷은 그 날의 "마지막 관측"이다.
-- security definer 는 funnel_counts_daily 의 쓰기 RLS 를 우회하지만, 아래에서
-- 클라이언트 실행 권한을 전부 회수하므로 클라이언트가 닿는 우회 경로는 없다
-- (호출자는 pg_cron 잡 소유자뿐).
create or replace function public.funnel_snapshot()
returns void
language sql
security definer
set search_path = public
as $$
  insert into public.funnel_counts_daily (snap_date, key, total)
  select (now() at time zone 'Asia/Seoul')::date, key, total
  from public.funnel_counts
  on conflict (snap_date, key) do update set total = excluded.total;
$$;

revoke all on function public.funnel_snapshot() from public;
revoke all on function public.funnel_snapshot() from anon;
revoke all on function public.funnel_snapshot() from authenticated;

-- 23:55 KST = 14:55 UTC (pg_cron 은 UTC 기준). 같은 잡 이름 재호출은 스케줄 갱신(멱등).
select cron.schedule('grm-funnel-snapshot-daily', '55 14 * * *', 'select public.funnel_snapshot()');

-- 시계열 시작점: 적용 즉시 1회 동결(오늘 행은 23:55 에 그 날 마감치로 갱신된다).
select public.funnel_snapshot();
