-- 087: 구독 제출의 **유입 축**(first touch) + 구역별 전환 판독 — 076/084 의 다음 한 칸.
--
-- 계기(2026-09-23 마케팅 계획 M-01·M-03): 링크드인 게시·뉴스레터 전달·공유 링크처럼 우리가
-- 링크를 뿌리는 채널에서 온 구독이 몇 건인지 답할 자료가 없다. RUM 은 리퍼러 호스트까지만
-- 알고(앱 내장 브라우저는 리퍼러를 보내지 않아 '직접'에 섞인다 — 2026-09-23 실측 21일간
-- 직접 46%·링크드인 리퍼러 4회) 쿼리스트링은 저장하지 않는다(073 clean_path). 그래서 첫 방문
-- 때 utm 세 값·리퍼러 호스트·착지 구역을 브라우저에 보관했다가 **제출 때** 함께 보낸다.
-- 노출은 보내지 않는다(076 이 노출을 뺀 근거 그대로 — 크롤러 오염·요청 2배).
--
-- ★무PII — 이메일과 결합하지 않는 집계(키 문자열 + 정수 합계)다. utm 값은 우리가 만든 링크의
--   값이고, 남이 `?utm_source=<이메일>` 로 오염시키려 해도 형식 제약(소문자·숫자·점·밑줄·
--   하이픈)이 '@' 를 거부해 'other' 로 접힌다. 리퍼러는 호스트만 싣는다(RUM 072 가 이미 같은
--   값을 저장한다). 착지 구역은 076 과 같은 경로 첫 조각이라 쿼리·경로 뒷부분이 실리지 않는다.
-- ★076/084 를 고치지 않고 표 하나를 **가산**한다 — RPC 인자를 바꾸면 라이브 페이지의 호출이
--   깨진다(배포 창).
-- ★열린 문자열을 anon RPC 로 받으므로 076/084 와 같은 3중 제약 — 형식·길이·**행 수 상한 500**.
--   상한에 닿으면 튜플 전체를 'other' 로 접는다. 제출이 주 1~5 건이라 몇 해를 써도 닿지 않는다.
-- ★키는 제출 둘뿐이다(084 와 같다). 076 의 5키 어휘 대조(test_funnel_vocabulary_synced_three_ways)
--   에 이 표를 끼우지 말 것 — 일부러 좁힌 어휘다.
-- ★값이 없을 때의 낱말: utm 부재 = 'none' · 리퍼러 없음 = 'direct' · 사이트 내부 이동 = 'internal'.
--   형식 위반 = 'other'. 'none' 과 'other' 를 합치지 않는다 — 하나는 부재, 하나는 거부다.
--
-- 읽기는 authenticated 뿐, 쓰기는 anon 이 RPC 로만 — 076/084 와 같은 규칙.

create table if not exists public.funnel_touch_counts (
  key text not null
    check (key in ('band_submit','cta_submit')),
  source text not null check (source ~ '^[a-z0-9._-]{1,40}$'),
  medium text not null check (medium ~ '^[a-z0-9._-]{1,40}$'),
  campaign text not null check (campaign ~ '^[a-z0-9._-]{1,60}$'),
  ref_host text not null check (ref_host ~ '^[a-z0-9.-]{1,80}$'),
  landing_zone text not null check (landing_zone ~ '^[a-z0-9-]{1,24}$'),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (key, source, medium, campaign, ref_host, landing_zone)
);

alter table public.funnel_touch_counts enable row level security;

revoke all on public.funnel_touch_counts from public, anon, authenticated;
grant select on public.funnel_touch_counts to authenticated;

drop policy if exists "signed-in can read funnel touches" on public.funnel_touch_counts;
create policy "signed-in can read funnel touches"
on public.funnel_touch_counts for select to authenticated using (true);

create or replace function public.funnel_touch_bump(
  p_key text, p_source text, p_medium text, p_campaign text, p_ref_host text, p_landing_zone text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  s text; m text; c text; r text; z text;
  -- 행 수 상한 — 제출이 주 1~5 건이고 채널·캠페인 조합이 손에 꼽힌다. 남용은 여기서 멈춘다.
  cap constant integer := 500;
begin
  -- 모르는 키는 실패(폴백 금지) — 060/076/084 와 같은 규칙. 노출 키는 여기 오면 안 된다.
  if p_key not in ('band_submit','cta_submit') then
    raise exception 'funnel_touch_bump: unknown key %', p_key;
  end if;
  -- 형식에 안 맞으면 버리지 않고 'other' 로 접는다 — 제출은 일어난 사실이라 세야 한다.
  s := lower(coalesce(p_source, ''));       if s !~ '^[a-z0-9._-]{1,40}$' then s := 'other'; end if;
  m := lower(coalesce(p_medium, ''));       if m !~ '^[a-z0-9._-]{1,40}$' then m := 'other'; end if;
  c := lower(coalesce(p_campaign, ''));     if c !~ '^[a-z0-9._-]{1,60}$' then c := 'other'; end if;
  r := lower(coalesce(p_ref_host, ''));     if r !~ '^[a-z0-9.-]{1,80}$'  then r := 'other'; end if;
  z := lower(coalesce(p_landing_zone, '')); if z !~ '^[a-z0-9-]{1,24}$'   then z := 'other'; end if;
  -- 상한을 넘겨 **새** 행을 만들려는 호출은 튜플 전체를 'other' 로 접는다(기존 행은 계속 증가).
  if not exists (select 1 from public.funnel_touch_counts f
                  where f.key = p_key and f.source = s and f.medium = m and f.campaign = c
                    and f.ref_host = r and f.landing_zone = z)
     and (select count(*) from public.funnel_touch_counts) >= cap then
    s := 'other'; m := 'other'; c := 'other'; r := 'other'; z := 'other';
  end if;
  insert into public.funnel_touch_counts as ft
    (key, source, medium, campaign, ref_host, landing_zone, total, updated_at)
  values (p_key, s, m, c, r, z, 1, now())
  on conflict (key, source, medium, campaign, ref_host, landing_zone) do update
  set total = ft.total + 1, updated_at = now();
end;
$$;

revoke all on function public.funnel_touch_bump(text, text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.funnel_touch_bump(text, text, text, text, text, text) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- 판독 ① — 유입 축 표. 084 funnel_paths_report 와 같은 꼴이라 성장 일보가 한 번 더 부른다.
-- 채널은 여기서 정한다: utm_source 가 있으면 그것('utm:' 접두), 없으면 리퍼러 호스트를
-- 077 growth_daily_report 의 리퍼러 묶음과 같은 규칙으로 묶고 링크드인만 한 묶음 더 둔다
-- (이 축의 존재 이유가 링크드인이다). 저장값은 원문 그대로이고 묶음은 읽을 때 붙인다 —
-- 묶음 규칙이 낡아도 과거 행이 사라지지 않는다.
create or replace function public.funnel_touch_report()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with t as (
    select key, source, medium, campaign, ref_host, landing_zone, total, updated_at,
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
      from public.funnel_touch_counts)
  select jsonb_build_object(
    'by_channel', coalesce(
      (select jsonb_agg(jsonb_build_object('channel', g.channel, 'submits', g.s) order by g.s desc, g.channel)
         from (select channel, sum(total) as s from t group by channel) g), '[]'::jsonb),
    'rows', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'key', key, 'source', source, 'medium', medium, 'campaign', campaign,
                'ref_host', ref_host, 'landing_zone', landing_zone, 'channel', channel, 'total', total,
                'last_kst', to_char(updated_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
              order by updated_at desc)
         from t), '[]'::jsonb),
    'distinct_rows', (select count(*) from t),
    'total_submits', coalesce((select sum(total) from t), 0),
    -- 배선 이전 제출은 이 표에 없다(소급 불가) — 084 와 같은 이유로 첫 기록일을 같이 낸다.
    'first_kst', (select to_char(min(updated_at) at time zone 'Asia/Seoul', 'YYYY-MM-DD') from t)
  );
$$;

revoke all on function public.funnel_touch_report() from public, anon, authenticated;
grant execute on function public.funnel_touch_report() to authenticated;

-- ---------------------------------------------------------------------------
-- 판독 ② — 구역별 전환(마케팅 계획 M-03 "노출에도 zone 기록"의 대체). 노출을 구역별로 보내는
-- 대신 RUM 착지 방문(rum_path_daily, bot:0)을 분모로, 076 제출을 분자로 조인한다. 두 표의
-- 구역 규칙은 같다 — 경로 첫 조각(빈 조각 = 'home'). 076 카운터는 일자 스냅샷 없는 누적이라
-- 분모도 **076 적용일(2026-09-04) 이후**로 자른다. /en/ 트리는 구독 표면이 없으므로 분모에서
-- 뺀다(076 zone() 은 그 경로를 'en' 으로 남기지만 거기서는 제출이 일어날 수 없다).
-- ★경로 표는 표본 실행에서 10단위 반올림이라 작은 구역은 방문이 0 으로 보일 수 있다 —
--   구역마다 sample_interval 최댓값을 같이 내고, 방문 0 이면 전환율을 계산하지 않는다(null).
create or replace function public.funnel_zone_report()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with v as (
    select case when split_part(request_path, '/', 2) = '' then 'home'
                else lower(split_part(request_path, '/', 2)) end as zone,
           sum(visits) as visits,
           max(sample_interval) as si_max,
           bool_or(sample_interval is null) as si_unknown
      from public.rum_path_daily
     where snap_date >= date '2026-09-04'
       and request_path !~ '^/en(/|$)'
     group by 1),
  s as (
    select zone,
           coalesce(sum(total) filter (where key = 'band_submit'), 0) as band,
           coalesce(sum(total) filter (where key = 'cta_submit'), 0) as cta,
           coalesce(sum(total) filter (where key in ('band_submit', 'cta_submit')), 0) as submits,
           max(updated_at) as last_at
      from public.funnel_zone_counts
     group by 1),
  j as (
    select coalesce(v.zone, s.zone) as zone, v.visits, v.si_max, v.si_unknown,
           coalesce(s.band, 0) as band, coalesce(s.cta, 0) as cta,
           coalesce(s.submits, 0) as submits, s.last_at
      from v full outer join s on s.zone = v.zone)
  select jsonb_build_object(
    'since', '2026-09-04',
    'zones', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'zone', zone, 'visits', visits, 'submits', submits, 'band', band, 'cta', cta,
                'rate_pct', case when coalesce(visits, 0) > 0
                                 then round(100.0 * submits / visits, 2) end,
                'sample_interval_max', si_max, 'precision_unknown', si_unknown,
                'last_kst', to_char(last_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
              order by submits desc, coalesce(visits, 0) desc, zone)
         from j), '[]'::jsonb),
    'total_visits', (select coalesce(sum(visits), 0) from v),
    'total_submits', (select coalesce(sum(submits), 0) from s),
    'visits_latest_date', (select max(snap_date) from public.rum_path_daily),
    'path_precision_note', '경로 표는 표본 실행에서 거칠다(10단위) — 작은 구역의 방문은 0 으로 보일 수 있다. 전환율은 방향만 읽는다.'
  );
$$;

revoke all on function public.funnel_zone_report() from public, anon, authenticated;
grant execute on function public.funnel_zone_report() to authenticated;
