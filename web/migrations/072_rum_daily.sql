-- 072: Cloudflare Web Analytics(RUM) 일별 적재 — 운영자가 Cloudflare 대시보드를 읽지
-- 않고도 /admin 성장·유입 탭에서 방문·유입 경로를 한국어로 보게 하는 저장소.
--
-- 왜 여기(Supabase)인가: 같은 탭이 이미 funnel_counts(060)·funnel_counts_daily(071)를
-- 직접 읽는다. 같은 성격(일별 시계열)을 같은 곳에 두면 화면 코드가 한 줄로 끝나고,
-- 저장소 JSON 커밋(매일 전체 사이트 재빌드)·Cloudflare KV(Worker 바인딩 = 정적 사이트
-- 원칙 파기)를 피한다.
--
-- ★날짜 축은 KST 다. Cloudflare GraphQL 의 date 차원은 UTC 라 그대로 담으면 09:00 KST
-- 에서 하루가 갈려 funnel_counts_daily(23:55 KST 스냅샷)와 어긋난다. 수집기가 시간
-- 단위로 받아 KST 로 재버킷한 뒤 넣는다(collect_rum_analytics.py 참조).
--
-- ★읽기 권한은 authenticated 뿐이다 — funnel_counts(060)가 anon 공개인 것과 의도적으로
-- 다르다. 방문·유입 규모는 사이트 콘텐츠가 아니라 운영 지표이고, 읽는 화면(/admin)은
-- 어차피 로그인 뒤에만 조회한다(admin.js refreshAll 은 세션 확인 후 호출).
-- 쓰기는 service_role 뿐(RLS 우회) — 클라이언트 경로 없음.

create table if not exists public.rum_daily (
  snap_date date not null,
  metric text not null check (metric in ('visits', 'page_views')),
  value integer not null check (value >= 0),
  primary key (snap_date, metric)
);

create table if not exists public.rum_referrer_daily (
  snap_date date not null,
  referer_host text not null,
  visits integer not null check (visits >= 0),
  primary key (snap_date, referer_host)
);

alter table public.rum_daily enable row level security;
alter table public.rum_referrer_daily enable row level security;

revoke all on public.rum_daily from public, anon, authenticated;
revoke all on public.rum_referrer_daily from public, anon, authenticated;
grant select on public.rum_daily to authenticated;
grant select on public.rum_referrer_daily to authenticated;

drop policy if exists "signed-in can read rum daily" on public.rum_daily;
create policy "signed-in can read rum daily"
on public.rum_daily for select to authenticated using (true);

drop policy if exists "signed-in can read rum referrers" on public.rum_referrer_daily;
create policy "signed-in can read rum referrers"
on public.rum_referrer_daily for select to authenticated using (true);
