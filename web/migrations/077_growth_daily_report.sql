-- 077 — 일일 성장 보고의 데이터층 (2026-09-05)
--
-- 운영자가 매일 아침 "어제 몇 명이, 어디서(국가·경로·기기), 어느 페이지로 들어왔고,
-- 구독자는 몇 명 늘었나"를 Cloudflare/Brevo 화면을 열지 않고 한국어 보고로 받기 위한 층.
-- 값의 출처는 셋이다:
--   · Cloudflare Web Analytics(RUM, bot:0) — 072/073 의 방문·리퍼러·경로에 더해 이 파일이
--     국가(rum_country_daily)·기기(rum_device_daily)를 추가한다. 수집기는 그룹마다 별도
--     요청(f923f0c 의 ABR 교훈)이라 차원이 늘어도 방문 수의 정밀도는 딸려 내려가지 않는다.
--   · Brevo 리스트 구독자 수 — newsletter_subscribers_daily. 지금까지는 사람이 Brevo 화면을
--     캡처해 세었다(09-01 "6→8"). 스냅샷은 하루 1회, 같은 날 재실행은 덮어쓴다.
--   · 기존 funnel_counts_daily(071)·auth.users — 함수가 읽기만 한다.
--
-- ★읽기 권한은 072 와 같다: authenticated 만(운영 지표). 쓰기는 service_role(워크플로).
-- ★보고 함수 growth_daily_report 는 auth.users 를 세므로 security definer 다. 그래서
--   anon·authenticated 에게는 실행 권한을 주지 않는다 — 호출자는 운영 도구(postgres /
--   service_role)뿐이다. /admin 이 이 함수를 쓰게 되면 그때 회원 수를 뺀 뷰를 따로 판다.
-- ★"국가"는 Cloudflare 가 주는 값 그대로 저장한다(ISO 2자리 코드 또는 이름). 한국어
--   이름은 함수가 붙인다 — 저장값을 번역해 두면 새 코드가 나올 때 과거를 못 가른다.

create table if not exists public.rum_country_daily (
  snap_date date not null,
  country text not null,
  visits integer not null check (visits >= 0),
  sample_interval numeric check (sample_interval >= 1),
  primary key (snap_date, country)
);

create table if not exists public.rum_device_daily (
  snap_date date not null,
  device_type text not null,
  visits integer not null check (visits >= 0),
  sample_interval numeric check (sample_interval >= 1),
  primary key (snap_date, device_type)
);

create table if not exists public.newsletter_subscribers_daily (
  snap_date date not null,
  list_id integer not null,
  total_subscribers integer not null check (total_subscribers >= 0),
  total_blacklisted integer not null default 0 check (total_blacklisted >= 0),
  unique_subscribers integer check (unique_subscribers >= 0),
  captured_at timestamptz not null default now(),
  primary key (snap_date, list_id)
);

comment on column public.rum_country_daily.sample_interval is
  'Cloudflare 표본 간격(1=전수 · 10=10배 추정). NULL=미상.';
comment on column public.rum_device_daily.sample_interval is
  'Cloudflare 표본 간격(1=전수 · 10=10배 추정). NULL=미상.';
comment on column public.newsletter_subscribers_daily.total_subscribers is
  'Brevo 리스트의 구독자 수(uniqueSubscribers 우선, 없으면 totalSubscribers, 그것도 없으면 contacts count). 수신거부(blacklisted)는 별도 열.';
comment on column public.newsletter_subscribers_daily.snap_date is
  '캡처 시각의 KST 날짜(01:30 KST 실행이면 "그 날 아침 기준" 수치).';

alter table public.rum_country_daily enable row level security;
alter table public.rum_device_daily enable row level security;
alter table public.newsletter_subscribers_daily enable row level security;

revoke all on public.rum_country_daily from public, anon, authenticated;
revoke all on public.rum_device_daily from public, anon, authenticated;
revoke all on public.newsletter_subscribers_daily from public, anon, authenticated;

grant select on public.rum_country_daily to authenticated;
grant select on public.rum_device_daily to authenticated;
grant select on public.newsletter_subscribers_daily to authenticated;

drop policy if exists "signed-in can read rum countries" on public.rum_country_daily;
create policy "signed-in can read rum countries"
on public.rum_country_daily for select to authenticated using (true);

drop policy if exists "signed-in can read rum devices" on public.rum_device_daily;
create policy "signed-in can read rum devices"
on public.rum_device_daily for select to authenticated using (true);

drop policy if exists "signed-in can read newsletter subscriber counts" on public.newsletter_subscribers_daily;
create policy "signed-in can read newsletter subscriber counts"
on public.newsletter_subscribers_daily for select to authenticated using (true);

-- ---------------------------------------------------------------------------
-- 보고 함수. 하루치 JSON 하나로 "어제"를 설명하는 데 필요한 모든 수를 낸다.
-- 숫자는 여기서 정하고, 말은 보고 작성자가 한다(계산이 대화 속에서 흔들리지 않게).
--
-- p_date 기본값 = KST 어제. 아침 08:30 KST 에 호출하면 어제(KST)가 된다.
-- RUM 날짜는 UTC 다(072 주석) — KST 09:00~24:00 이 같은 번호의 날짜에 들어가므로
-- 한국 사이트에서는 "KST 어제"의 좋은 근사고, 어긋나는 구간은 새벽 0~9시뿐이다.
--
-- ★표본 간격(sample_interval)을 값마다 같이 낸다. 1 이면 정확, >1 이면 그 배수의 표본
--   추정(10 이면 10단위 반올림), NULL 이면 정밀도 미상(075 이전 적재분 = 사실상 추정).
--   보고는 이 값을 반드시 밝힌다 — 추정값을 정확값처럼 읽으면 판단이 갈린다(#876/#879).
-- ★리퍼러·경로·국가 묶음은 수집기가 아니라 여기서 정한다(admin.js RUM_REFERRER_GROUPS ·
--   RUM_ZONES 와 같은 규칙·같은 순서 — 구체 규칙이 일반 규칙보다 앞).
-- ★깔때기 view 계열은 봇 필터가 없어 부풀어 있다(09-01 실측: view 일 170 vs 실방문 21).
--   참고값으로만 낸다. 전환율 분모는 RUM 방문이다.
create or replace function public.growth_daily_report(p_date date default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  d date := coalesce(p_date, (now() at time zone 'Asia/Seoul')::date - 1);
  d7_start date;
  p7_start date;
  p7_end date;
  v_day jsonb;
  v_trend jsonb;
  v_week jsonb;
  v_refs jsonb;
  v_countries jsonb;
  v_devices jsonb;
  v_paths jsonb;
  v_funnel jsonb;
  v_news jsonb;
  v_members jsonb;
  v_quality jsonb;
begin
  d7_start := d - 6;
  p7_start := d - 13;
  p7_end := d - 7;

  -- 어제 하루: 방문·페이지뷰
  select jsonb_build_object(
           'visits', max(value) filter (where metric = 'visits'),
           'page_views', max(value) filter (where metric = 'page_views'),
           'sample_interval',
             case when count(*) = 0 then null
                  when bool_or(sample_interval is null) then null
                  else max(sample_interval) end)
    into v_day
    from public.rum_daily where snap_date = d;

  -- 최근 14일 흐름(요일 포함) — "어제가 평소보다 높은가"는 이 줄로 본다
  select coalesce(jsonb_agg(jsonb_build_object(
           'date', t.snap_date,
           'weekday', (array['월','화','수','목','금','토','일'])[extract(isodow from t.snap_date)::int],
           'visits', t.visits,
           'page_views', t.page_views,
           'sample_interval', t.si) order by t.snap_date), '[]'::jsonb)
    into v_trend
    from (select snap_date,
                 max(value) filter (where metric = 'visits') as visits,
                 max(value) filter (where metric = 'page_views') as page_views,
                 case when bool_or(sample_interval is null) then null
                      else max(sample_interval) end as si
            from public.rum_daily
           where snap_date between d - 13 and d
           group by snap_date) t;

  -- 이번 7일 vs 직전 7일
  select jsonb_build_object(
           'this_week', jsonb_build_object(
             'start', d7_start, 'end', d,
             'visits', coalesce(sum(value) filter (where metric = 'visits' and snap_date between d7_start and d), 0),
             'page_views', coalesce(sum(value) filter (where metric = 'page_views' and snap_date between d7_start and d), 0),
             'days_with_data', count(distinct snap_date) filter (where snap_date between d7_start and d)),
           'prev_week', jsonb_build_object(
             'start', p7_start, 'end', p7_end,
             'visits', coalesce(sum(value) filter (where metric = 'visits' and snap_date between p7_start and p7_end), 0),
             'page_views', coalesce(sum(value) filter (where metric = 'page_views' and snap_date between p7_start and p7_end), 0),
             'days_with_data', count(distinct snap_date) filter (where snap_date between p7_start and p7_end)))
    into v_week
    from public.rum_daily where snap_date between p7_start and d;

  -- 유입 경로(리퍼러) — 묶음 규칙은 admin.js 와 동일·같은 순서(AI 가 구글보다 앞:
  -- gemini.google.com 이 구글 규칙에도 걸린다)
  with r as (
    select snap_date, referer_host, visits, sample_interval,
           case
             when referer_host = '' or referer_host = '(direct)' then 'direct'
             when referer_host ~* '(^|\.)(chatgpt\.com|openai\.com|perplexity\.ai|gemini\.google\.com|claude\.ai|copilot\.microsoft\.com)$' then 'ai'
             when referer_host ~* '(^|\.)google\.' then 'google'
             when referer_host ~* '(^|\.)naver\.com$' then 'naver'
             when referer_host ~* '(^|\.)(bing\.com|duckduckgo\.com|yahoo\.com|daum\.net)$' then 'other_search'
             when referer_host ~* 'sendibm|brevo' then 'newsletter'
             when referer_host ~* '(^|\.)(teams\.microsoft\.com|onecdn\.static\.microsoft|sharepoint\.com|office\.com|office\.net)$' then 'teams'
             when referer_host ~* '(^|\.)grm-solutions\.com$' then 'internal'
             else 'other'
           end as grp
      from public.rum_referrer_daily
     where snap_date between p7_start and d)
  select jsonb_build_object(
           'day', (select coalesce(jsonb_agg(jsonb_build_object('group', g.grp, 'visits', g.v) order by g.v desc, g.grp), '[]'::jsonb)
                     from (select grp, sum(visits) as v from r where snap_date = d group by grp) g),
           'day_hosts', (select coalesce(jsonb_agg(jsonb_build_object('host', referer_host, 'group', grp, 'visits', visits) order by visits desc, referer_host), '[]'::jsonb)
                           from r where snap_date = d),
           'day_sum', (select coalesce(sum(visits), 0) from r where snap_date = d),
           'day_sample_interval', (select case when count(*) = 0 then null when bool_or(sample_interval is null) then null else max(sample_interval) end from r where snap_date = d),
           'this_week', (select coalesce(jsonb_agg(jsonb_build_object('group', g.grp, 'visits', g.v) order by g.v desc, g.grp), '[]'::jsonb)
                           from (select grp, sum(visits) as v from r where snap_date between d7_start and d group by grp) g),
           'prev_week', (select coalesce(jsonb_agg(jsonb_build_object('group', g.grp, 'visits', g.v) order by g.v desc, g.grp), '[]'::jsonb)
                           from (select grp, sum(visits) as v from r where snap_date between p7_start and p7_end group by grp) g),
           'this_week_hosts', (select coalesce(jsonb_agg(jsonb_build_object('host', h.referer_host, 'group', h.grp, 'visits', h.v) order by h.v desc, h.referer_host), '[]'::jsonb)
                                 from (select referer_host, grp, sum(visits) as v from r where snap_date between d7_start and d group by referer_host, grp order by sum(visits) desc, referer_host limit 12) h))
    into v_refs;

  -- 국가 — 저장값은 Cloudflare 원문(코드/이름), 한국어 이름은 여기서만 붙인다
  with c as (
    select snap_date, country, visits, sample_interval,
           case country
             when 'KR' then '한국' when 'US' then '미국' when 'JP' then '일본' when 'CN' then '중국'
             when 'IN' then '인도' when 'DE' then '독일' when 'GB' then '영국' when 'SG' then '싱가포르'
             when 'TW' then '대만' when 'VN' then '베트남' when 'FR' then '프랑스' when 'CH' then '스위스'
             when 'IE' then '아일랜드' when 'CA' then '캐나다' when 'AU' then '호주' when 'HK' then '홍콩'
             when 'NL' then '네덜란드' when 'IT' then '이탈리아' when 'ES' then '스페인' when 'TH' then '태국'
             when 'ID' then '인도네시아' when 'MY' then '말레이시아' when 'PH' then '필리핀' when 'BR' then '브라질'
             when 'MX' then '멕시코' when 'SE' then '스웨덴' when 'DK' then '덴마크' when 'BE' then '벨기에'
             when 'AT' then '오스트리아' when 'PL' then '폴란드' when 'IL' then '이스라엘' when 'AE' then '아랍에미리트'
             when 'SA' then '사우디아라비아' when 'TR' then '튀르키예' when 'RU' then '러시아' when 'BD' then '방글라데시'
             when 'PK' then '파키스탄' when 'NZ' then '뉴질랜드' when 'ZA' then '남아프리카공화국' when 'EG' then '이집트'
             when '' then '(미상)' when '(unknown)' then '(미상)'
             else country
           end as label
      from public.rum_country_daily
     where snap_date between d7_start and d)
  select jsonb_build_object(
           'day', (select coalesce(jsonb_agg(jsonb_build_object('country', country, 'label', label, 'visits', visits) order by visits desc, country), '[]'::jsonb)
                     from c where snap_date = d),
           'day_sample_interval', (select case when count(*) = 0 then null when bool_or(sample_interval is null) then null else max(sample_interval) end from c where snap_date = d),
           'this_week', (select coalesce(jsonb_agg(jsonb_build_object('country', g.country, 'label', g.label, 'visits', g.v) order by g.v desc, g.country), '[]'::jsonb)
                           from (select country, label, sum(visits) as v from c group by country, label) g))
    into v_countries;

  -- 기기(PC/모바일/태블릿)
  with dv as (
    select snap_date, device_type, visits, sample_interval,
           case lower(device_type)
             when 'desktop' then 'PC' when 'mobile' then '모바일' when 'tablet' then '태블릿'
             when '' then '(미상)' when '(unknown)' then '(미상)'
             else device_type
           end as label
      from public.rum_device_daily
     where snap_date between d7_start and d)
  select jsonb_build_object(
           'day', (select coalesce(jsonb_agg(jsonb_build_object('device', device_type, 'label', label, 'visits', visits) order by visits desc, device_type), '[]'::jsonb)
                     from dv where snap_date = d),
           'day_sample_interval', (select case when count(*) = 0 then null when bool_or(sample_interval is null) then null else max(sample_interval) end from dv where snap_date = d),
           'this_week', (select coalesce(jsonb_agg(jsonb_build_object('device', g.device_type, 'label', g.label, 'visits', g.v) order by g.v desc, g.device_type), '[]'::jsonb)
                           from (select device_type, label, sum(visits) as v from dv group by device_type, label) g))
    into v_devices;

  -- 착지 경로 — 구역 이름은 admin.js RUM_ZONES 와 같은 규칙·같은 순서(구체 규칙이 앞).
  -- /en/ 트리는 접두를 떼고 같은 규칙으로 구역을 정하되 lang 으로 표시한다.
  with p as (
    select snap_date, request_path, visits, sample_interval,
           (request_path ~ '^/en(/|$)') as is_en,
           regexp_replace(request_path, '^/en(?=/|$)', '') as base_path
      from public.rum_path_daily
     where snap_date between d7_start and d),
  z as (
    select *,
           case
             when base_path ~ '^/library/' then '자료실'
             when base_path ~ '^/glossary/' then '용어사전'
             when base_path ~ '^/findings/firm/' then '업체 프로파일'
             when base_path ~ '^/findings/inspector/' then '실사관 프로파일'
             when base_path ~ '^/findings/docs?/' then '지적사항 문서'
             when base_path ~ '^/findings/trends/' then '트렌드'
             when base_path ~ '^/findings/clause/' then '조항별 사례'
             when base_path ~ '^/findings/' then '지적사항 검색'
             when base_path ~ '^/briefs/' then '주간 브리프'
             when base_path ~ '^/archive/' then '아카이브'
             when base_path ~ '^/quiz/' then '퀴즈'
             when base_path ~ '^/guide/' then '이용안내'
             when base_path ~ '^/admin/' then '운영 콘솔'
             when base_path = '/' or base_path = '' then '홈'
             else '기타'
           end as zone
      from p)
  select jsonb_build_object(
           'day', (select coalesce(jsonb_agg(jsonb_build_object('path', request_path, 'zone', zone, 'en', is_en, 'visits', visits) order by visits desc, request_path), '[]'::jsonb)
                     from (select * from z where snap_date = d order by visits desc, request_path limit 12) x),
           'day_sum', (select coalesce(sum(visits), 0) from z where snap_date = d),
           'day_sample_interval', (select case when count(*) = 0 then null when bool_or(sample_interval is null) then null else max(sample_interval) end from z where snap_date = d),
           'day_zones', (select coalesce(jsonb_agg(jsonb_build_object('zone', g.zone, 'visits', g.v) order by g.v desc, g.zone), '[]'::jsonb)
                           from (select zone, sum(visits) as v from z where snap_date = d group by zone) g),
           'this_week', (select coalesce(jsonb_agg(jsonb_build_object('path', h.request_path, 'zone', h.zone, 'en', h.is_en, 'visits', h.v) order by h.v desc, h.request_path), '[]'::jsonb)
                           from (select request_path, zone, is_en, sum(visits) as v from z group by request_path, zone, is_en order by sum(visits) desc, request_path limit 15) h),
           'this_week_zones', (select coalesce(jsonb_agg(jsonb_build_object('zone', g.zone, 'visits', g.v) order by g.v desc, g.zone), '[]'::jsonb)
                                 from (select zone, sum(visits) as v from z group by zone) g),
           'this_week_en_visits', (select coalesce(sum(visits), 0) from z where is_en))
    into v_paths;

  -- 구독 깔때기(071 스냅샷은 23:55 KST 누적값) — 하루치 = 그 날 스냅샷 − 전날 스냅샷
  with f as (
    select snap_date,
           max(total) filter (where key = 'band_submit') as band_submit,
           max(total) filter (where key = 'cta_submit') as cta_submit,
           max(total) filter (where key = 'band_view') as band_view,
           max(total) filter (where key = 'cta_view') as cta_view,
           max(total) filter (where key = 'cta_dismiss') as cta_dismiss
      from public.funnel_counts_daily
     where snap_date in (d, d - 1, d - 7, d - 14)
     group by snap_date)
  select jsonb_build_object(
           'day', (select jsonb_build_object(
                     'band_submit', fd.band_submit - fp.band_submit,
                     'cta_submit', fd.cta_submit - fp.cta_submit,
                     'submits', (fd.band_submit - fp.band_submit) + (fd.cta_submit - fp.cta_submit),
                     'band_view_ref', fd.band_view - fp.band_view,
                     'cta_view_ref', fd.cta_view - fp.cta_view,
                     'cta_dismiss', fd.cta_dismiss - fp.cta_dismiss)
                     from f fd join f fp on fp.snap_date = d - 1 where fd.snap_date = d),
           'this_week_submits', (select (fd.band_submit - fp.band_submit) + (fd.cta_submit - fp.cta_submit)
                                   from f fd join f fp on fp.snap_date = d - 7 where fd.snap_date = d),
           'prev_week_submits', (select (fd.band_submit - fp.band_submit) + (fd.cta_submit - fp.cta_submit)
                                   from f fd join f fp on fp.snap_date = d - 14 where fd.snap_date = d - 7),
           'cumulative', (select jsonb_build_object('band_submit', band_submit, 'cta_submit', cta_submit) from f where snap_date = d),
           'snapshot_present', exists (select 1 from f where snap_date = d),
           'prev_snapshot_present', exists (select 1 from f where snap_date = d - 1))
    into v_funnel;

  -- 뉴스레터 확정 구독자(Brevo 더블옵트인 완료 수) — 아침 스냅샷 기준
  with s as (
    select snap_date, list_id, total_subscribers, total_blacklisted, unique_subscribers, captured_at
      from public.newsletter_subscribers_daily
     where snap_date <= d + 1),
  latest as (select * from s order by snap_date desc limit 1),
  prev as (select s.* from s, latest where s.snap_date < latest.snap_date order by s.snap_date desc limit 1),
  week_ago as (select s.* from s, latest where s.snap_date <= latest.snap_date - 7 order by s.snap_date desc limit 1)
  select jsonb_build_object(
           'latest', (select jsonb_build_object('snap_date', snap_date, 'total', total_subscribers, 'blacklisted', total_blacklisted, 'captured_at_kst', captured_at at time zone 'Asia/Seoul') from latest),
           'previous', (select jsonb_build_object('snap_date', snap_date, 'total', total_subscribers, 'blacklisted', total_blacklisted) from prev),
           'week_ago', (select jsonb_build_object('snap_date', snap_date, 'total', total_subscribers, 'blacklisted', total_blacklisted) from week_ago),
           'delta_since_previous', (select l.total_subscribers - p.total_subscribers from latest l, prev p),
           'delta_7d', (select l.total_subscribers - w.total_subscribers from latest l, week_ago w),
           'history_14d', (select coalesce(jsonb_agg(jsonb_build_object('snap_date', snap_date, 'total', total_subscribers) order by snap_date), '[]'::jsonb)
                             from s where snap_date >= d - 13))
    into v_news;

  -- 회원(로그인 계정) — 관심 업체 알림 등 회원 기능의 모집단
  select jsonb_build_object(
           'total', count(*),
           'new_on_date', count(*) filter (where (created_at at time zone 'Asia/Seoul')::date = d),
           'new_this_week', count(*) filter (where (created_at at time zone 'Asia/Seoul')::date between d7_start and d),
           'new_prev_week', count(*) filter (where (created_at at time zone 'Asia/Seoul')::date between p7_start and p7_end),
           'signed_in_this_week', count(*) filter (where (last_sign_in_at at time zone 'Asia/Seoul')::date between d7_start and d))
    into v_members
    from auth.users;

  -- 데이터 상태 — "0" 과 "안 왔다"를 가른다(부재 어휘). 보고는 이 블록을 먼저 본다.
  select jsonb_build_object(
           'rum_present_on_date', exists (select 1 from public.rum_daily where snap_date = d),
           'rum_latest_date', (select max(snap_date) from public.rum_daily),
           'rum_referrer_present', exists (select 1 from public.rum_referrer_daily where snap_date = d),
           'rum_path_present', exists (select 1 from public.rum_path_daily where snap_date = d),
           'rum_country_present', exists (select 1 from public.rum_country_daily where snap_date = d),
           'rum_device_present', exists (select 1 from public.rum_device_daily where snap_date = d),
           'rum_country_first_date', (select min(snap_date) from public.rum_country_daily),
           'funnel_snapshot_present', exists (select 1 from public.funnel_counts_daily where snap_date = d),
           'funnel_prev_snapshot_present', exists (select 1 from public.funnel_counts_daily where snap_date = d - 1),
           'newsletter_latest_snapshot', (select max(snap_date) from public.newsletter_subscribers_daily),
           'date_basis', 'RUM 날짜는 UTC 기준 — KST 09:00~24:00 방문이 같은 날짜에 들어가고, KST 새벽 0~9시 방문은 전날로 잡힌다',
           'bot_basis', 'RUM 은 Cloudflare bot:0 필터(대시보드 Exclude bots=Yes 와 같은 모집단) + 운영자 제외 게이트(grm-op) 적용. 존(zone) 지표가 아니다',
           'precision_basis', 'sample_interval 1=정확, N>1=약 N배 표본 추정(10이면 10단위 반올림), null=정밀도 미상(추정으로 취급)')
    into v_quality;

  return jsonb_build_object(
    'report_date', d,
    'generated_at_kst', to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
    'day', v_day,
    'trend_14d', v_trend,
    'week_compare', v_week,
    'referrers', v_refs,
    'countries', v_countries,
    'devices', v_devices,
    'landing_paths', v_paths,
    'funnel', v_funnel,
    'newsletter', v_news,
    'members', v_members,
    'data_quality', v_quality);
end;
$$;

revoke all on function public.growth_daily_report(date) from public, anon, authenticated;
grant execute on function public.growth_daily_report(date) to service_role;

comment on function public.growth_daily_report(date) is
  '일일 성장 보고용 JSON(어제 방문·유입경로·국가·기기·착지·구독·회원). 운영 도구 전용 — anon/authenticated 실행 불가.';
