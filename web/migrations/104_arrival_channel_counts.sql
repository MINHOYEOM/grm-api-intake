-- 104: 방문 **도착 채널** 집계 — 유입 표식(utm)·리퍼러 호스트별 하루 정수 카운터.
--
-- 계기(2026-11-04 성장분석팀 「구독자는 어디서 들어와 가입했나」): Cloudflare RUM 은 쿼리스트링을
-- 저장하지 않아(073 clean_path) 메일 앱·링크드인 앱·메신저가 전부 '직접'으로 섞이고, 087 은 구독
-- **제출** 때만 채널을 읽어 가입 23건(표본 작음)으로만 방향을 본다. 같은 first-touch 를 가입과 무관한
-- **방문 도착** 시점에 한 번 세면(7일에 한 번 — 087 의 first touch 가 새로 저장되는 순간) 채널별
-- 도착 규모를 볼 수 있다.
--
-- ★무PII — 저장하는 것은 (KST 일자, utm_source, utm_medium, 리퍼러 호스트) 조합의 정수 합계뿐이다.
--   IP·브라우저/세션 식별자·이메일·URL·경로·쿼리 원문·campaign 값은 보내지도 저장하지도 않는다.
--   방문자 단위 행이 없어 "한 사람이 몇 번"은 원리상 알 수 없다(의도한 한계). 값은 087 과 같은 형식
--   제약(소문자·숫자·점·밑줄·하이픈)이라 '@' 가 든 값은 'other' 로 접힌다.
-- ★센 단위 = "이 브라우저에서 7일 만에 새로 시작된 first touch" — 사람 수가 아니라 근사 신규·복귀 도착
--   수다. 시크릿 모드·저장소 삭제는 매번 새로 센다. 클라이언트가 자동화 브라우저는 걸러 내지만
--   JS 를 실행하는 크롤러는 섞일 수 있다(RUM 의 bot 구분은 여기 없다) — 규모 방향만 읽는다.
-- ★가산만 — 기존 표·RPC(060/076/084/087/103, rum_*)는 건드리지 않는다. 반환 shape 변경 없음.
-- ★열린 문자열은 anon RPC 로 받으므로 087 과 같은 3중 제약 — 형식·길이·행 수 상한 3000. 상한에 닿으면
--   새 조합을 ('other','other','other') 로 접는다(카운트는 잃지 않는다).
-- ★값이 없을 때의 낱말(087 과 같다): utm 부재 = 'none' · 리퍼러 없음 = 'direct' · 형식 위반 = 'other'.
-- 읽기는 authenticated 뿐, 쓰기는 anon 이 RPC 로만.
-- ★적용은 사람이 한다 — 이 파일은 PR 에만 있다.

create table if not exists public.arrival_channel_counts (
  day date not null,
  source text not null check (source ~ '^[a-z0-9._-]{1,40}$'),
  medium text not null check (medium ~ '^[a-z0-9._-]{1,40}$'),
  ref_host text not null check (ref_host ~ '^[a-z0-9.-]{1,80}$'),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (day, source, medium, ref_host)
);

alter table public.arrival_channel_counts enable row level security;

revoke all on public.arrival_channel_counts from public, anon, authenticated;
grant select on public.arrival_channel_counts to authenticated;

drop policy if exists "signed-in can read arrival channels" on public.arrival_channel_counts;
create policy "signed-in can read arrival channels"
on public.arrival_channel_counts for select to authenticated using (true);

create or replace function public.arrival_bump(p_source text, p_medium text, p_ref_host text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  s text; m text; r text;
  cap constant integer := 3000;
  today constant date := (now() at time zone 'Asia/Seoul')::date;
begin
  s := lower(coalesce(p_source, ''));    if s !~ '^[a-z0-9._-]{1,40}$' then s := 'other'; end if;
  m := lower(coalesce(p_medium, ''));    if m !~ '^[a-z0-9._-]{1,40}$' then m := 'other'; end if;
  r := lower(coalesce(p_ref_host, ''));  if r !~ '^[a-z0-9.-]{1,80}$'  then r := 'other'; end if;
  if not exists (select 1 from public.arrival_channel_counts a
                  where a.day = today and a.source = s and a.medium = m and a.ref_host = r)
     and (select count(*) from public.arrival_channel_counts) >= cap then
    s := 'other'; m := 'other'; r := 'other';
  end if;
  insert into public.arrival_channel_counts as ac (day, source, medium, ref_host, total, updated_at)
  values (today, s, m, r, 1, now())
  on conflict (day, source, medium, ref_host) do update
  set total = ac.total + 1, updated_at = now();
end;
$$;

revoke all on function public.arrival_bump(text, text, text) from public, anon, authenticated;
grant execute on function public.arrival_bump(text, text, text) to anon, authenticated;

-- 판독 — 채널별 합계(전체 기간) + 최근 28일 일별 채널 합계. 채널 묶음 규칙은 087 funnel_touch_report 와
-- 같다(utm_source 가 있으면 'utm:{source}', 없으면 리퍼러 호스트로 묶는다). 저장값은 원문 그대로이고
-- 묶음은 읽을 때 붙인다 — 규칙이 낡아도 과거 행이 사라지지 않는다.
create or replace function public.arrival_channel_report()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with t as (
    select day, source, medium, ref_host, total,
           case
             when source not in ('none', 'other') then 'utm:' || source
             when ref_host = 'direct' then 'direct'
             when ref_host = 'internal' then 'internal'
             when ref_host ~* '(^|\.)(chatgpt\.com|openai\.com|perplexity\.ai|gemini\.google\.com|claude\.ai|copilot\.microsoft\.com)$' then 'ai'
             when ref_host ~* '(^|\.)google\.' then 'google'
             when ref_host ~* '(^|\.)naver\.com$' then 'naver'
             when ref_host ~* '(^|\.)(bing\.com|duckduckgo\.com|yahoo\.com|daum\.net)$' then 'other_search'
             when ref_host ~* 'sendibm|brevo' then 'newsletter'
             when ref_host ~* '(^|\.)(linkedin\.com|linkedin\.android|lnkd\.in)$' then 'linkedin'
             when ref_host ~* '(^|\.)(teams\.microsoft\.com|onecdn\.static\.microsoft|sharepoint\.com|office\.com|office\.net)$' then 'teams'
             else 'other'
           end as channel
      from public.arrival_channel_counts)
  select jsonb_build_object(
    'by_channel', coalesce(
      (select jsonb_agg(jsonb_build_object('channel', g.channel, 'arrivals', g.s) order by g.s desc, g.channel)
         from (select channel, sum(total) as s from t group by channel) g), '[]'::jsonb),
    'by_day', coalesce(
      (select jsonb_agg(jsonb_build_object('day', g.day, 'channel', g.channel, 'arrivals', g.s)
                        order by g.day desc, g.s desc, g.channel)
         from (select day, channel, sum(total) as s from t
                where day >= (now() at time zone 'Asia/Seoul')::date - 27
                group by day, channel) g), '[]'::jsonb),
    'total_arrivals', coalesce((select sum(total) from t), 0),
    -- 배선 이전 도착은 이 표에 없다(소급 불가) — 첫 기록일을 같이 낸다.
    'first_day', (select min(day) from t),
    'last_day', (select max(day) from t)
  );
$$;

revoke all on function public.arrival_channel_report() from public, anon, authenticated;
grant execute on function public.arrival_channel_report() to authenticated;
