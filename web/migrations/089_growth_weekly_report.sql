-- 089: 주간 성장 리포트 데이터층 — 마케팅 계획 M-04 (2026-09-23).
--
-- 매주 월요일 아침 "지난주(월~일, KST)에 무엇이 늘었나"를 JSON 한 방으로 낸다: 방문·구독자·
-- 구독 신청·채널별 신청(087)·구역별 전환(076)·경로별 신청(084)·검색(GSC)·회원. 077
-- growth_daily_report 의 주간판이되 통째 복제하지 않고 필요한 축만 다시 잰다(078 이
-- gsc_report 를, 084 가 funnel_paths_report 를 가른 것과 같은 이유 — 200줄 사본을 만들지 않는다).
--
-- ★유입 축(087)·경로 축(084)은 **누적 카운터**라 "이번 주 몇 건"을 스스로 모른다. 일자 스냅샷
--   배관을 하나 더 두는 대신 이 함수가 매주 자기 산출을 `growth_weekly_reports` 에 남기고
--   **직전 주 스냅샷과의 차분**으로 주간값을 만든다. 첫 주는 직전이 없어 주간값이 null 이다
--   (0 이 아니다 — 부재를 0 으로 적지 않는다).
-- ★쓰기가 있으므로 volatile 이고 실행은 service_role 뿐(운영 도구·Actions 전용, 077 과 같은
--   규칙). 읽기는 authenticated(/admin 이 나중에 읽을 수 있게).
-- ★뉴스레터 발송 여부는 Brevo 에 있어 SQL 이 모른다 — 파이썬(web/growth_weekly.py)이 붙인다.
-- ★같은 week_end 로 재실행하면 payload 를 덮어쓴다(멱등). 단 차분의 기준인 직전 주 행은
--   건드리지 않는다.
-- ★GSC 확정 데이터는 2~3일 늦다 — 주간 합계에 `days_with_data` 와 `latest_date` 를 같이 내고
--   읽는 쪽이 "아직 덜 왔다"를 구분한다("검색 0" 으로 읽지 않는다).

create table if not exists public.growth_weekly_reports (
  week_end date primary key,                 -- 그 주의 일요일(KST)
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.growth_weekly_reports enable row level security;
revoke all on public.growth_weekly_reports from public, anon, authenticated;
grant select on public.growth_weekly_reports to authenticated;
drop policy if exists "signed-in can read weekly growth reports" on public.growth_weekly_reports;
create policy "signed-in can read weekly growth reports"
on public.growth_weekly_reports for select to authenticated using (true);

-- p_persist=false 면 스냅샷을 남기지 않는다(파이썬 dry-run 용 — 리포트만 만들고 다음 주의
-- 차분 기준을 오염시키지 않는다).
create or replace function public.growth_weekly_report(p_week_end date default null, p_persist boolean default true)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  today_kst date := (now() at time zone 'Asia/Seoul')::date;
  -- 기본값 = 직전 일요일. 월요일 아침이면 어제, 일요일이면 지난주 일요일(오늘은 아직 안 끝났다).
  d date := coalesce(p_week_end, today_kst - extract(isodow from today_kst)::int);
  w_start date;   -- 이번 주 월요일
  p_start date;   -- 지난주 월요일
  p_end date;     -- 지난주 일요일
  v_prev jsonb;
  v_visits jsonb;
  v_subs jsonb;
  v_submits jsonb;
  v_gsc jsonb;
  v_members jsonb;
  v_zone jsonb;
  v_touch jsonb;
  v_touch_week jsonb;
  v_paths jsonb;
  v_paths_week jsonb;
  v_quality jsonb;
  payload jsonb;
begin
  w_start := d - 6;
  p_start := d - 13;
  p_end := d - 7;

  -- 직전 주 스냅샷(차분 기준). 없으면 null — 첫 주.
  select r.payload into v_prev from public.growth_weekly_reports r where r.week_end = p_end;

  -- 방문·페이지뷰: 이번 주 vs 지난주. 표본 간격 최댓값을 같이 낸다(1=정확).
  select jsonb_build_object(
           'this_week', jsonb_build_object(
             'start', w_start, 'end', d,
             'visits', coalesce(sum(value) filter (where metric = 'visits' and snap_date between w_start and d), 0),
             'page_views', coalesce(sum(value) filter (where metric = 'page_views' and snap_date between w_start and d), 0),
             'days_with_data', count(distinct snap_date) filter (where snap_date between w_start and d),
             'sample_interval_max', max(sample_interval) filter (where snap_date between w_start and d),
             'precision_unknown', bool_or(sample_interval is null) filter (where snap_date between w_start and d)),
           'prev_week', jsonb_build_object(
             'start', p_start, 'end', p_end,
             'visits', coalesce(sum(value) filter (where metric = 'visits' and snap_date between p_start and p_end), 0),
             'page_views', coalesce(sum(value) filter (where metric = 'page_views' and snap_date between p_start and p_end), 0),
             'days_with_data', count(distinct snap_date) filter (where snap_date between p_start and p_end),
             'sample_interval_max', max(sample_interval) filter (where snap_date between p_start and p_end),
             'precision_unknown', bool_or(sample_interval is null) filter (where snap_date between p_start and p_end)),
           'daily', coalesce((select jsonb_agg(jsonb_build_object('date', t.snap_date,
                       'weekday', (array['월','화','수','목','금','토','일'])[extract(isodow from t.snap_date)::int],
                       'visits', t.v, 'sample_interval', t.si) order by t.snap_date)
                      from (select snap_date, max(value) filter (where metric = 'visits') as v,
                                   max(sample_interval) as si
                              from public.rum_daily where snap_date between w_start and d group by snap_date) t), '[]'::jsonb))
    into v_visits
    from public.rum_daily where snap_date between p_start and d;

  -- 구독자(Brevo 스냅샷, 아침 기준): 주 시작(월요일 아침) 대비 주 마감(다음 월요일 아침).
  -- 그 날 스냅샷이 없으면 그 이전 가장 가까운 것을 쓴다.
  select jsonb_build_object(
           'now', (select jsonb_build_object('snap_date', snap_date, 'total', total_subscribers, 'blacklisted', total_blacklisted)
                     from public.newsletter_subscribers_daily where snap_date <= d + 1 order by snap_date desc limit 1),
           'week_start', (select jsonb_build_object('snap_date', snap_date, 'total', total_subscribers)
                            from public.newsletter_subscribers_daily where snap_date <= w_start order by snap_date desc limit 1),
           'prev_week_start', (select jsonb_build_object('snap_date', snap_date, 'total', total_subscribers)
                                 from public.newsletter_subscribers_daily where snap_date <= p_start order by snap_date desc limit 1))
    into v_subs;
  v_subs := v_subs || jsonb_build_object(
    'new_this_week', (v_subs->'now'->>'total')::int - (v_subs->'week_start'->>'total')::int,
    'new_prev_week', (v_subs->'week_start'->>'total')::int - (v_subs->'prev_week_start'->>'total')::int);

  -- 구독 신청(071 일자 스냅샷 23:55 KST 누적값 차분): 이번 주 = d 시점 − (d−7) 시점.
  with s as (
    select snap_date,
           coalesce(max(total) filter (where key = 'band_submit'), 0) + coalesce(max(total) filter (where key = 'cta_submit'), 0) as submits,
           coalesce(max(total) filter (where key = 'band_submit'), 0) as band,
           coalesce(max(total) filter (where key = 'cta_submit'), 0) as cta
      from public.funnel_counts_daily group by snap_date),
  pick as (
    select 'd' as at, s.* from s where snap_date <= d order by snap_date desc limit 1),
  pick7 as (
    select 'd7' as at, s.* from s where snap_date <= d - 7 order by snap_date desc limit 1),
  pick14 as (
    select 'd14' as at, s.* from s where snap_date <= d - 14 order by snap_date desc limit 1)
  select jsonb_build_object(
           'this_week', (select jsonb_build_object('submits', a.submits - b.submits, 'band', a.band - b.band, 'cta', a.cta - b.cta,
                                                   'snap_end', a.snap_date, 'snap_start', b.snap_date)
                           from pick a, pick7 b),
           'prev_week', (select jsonb_build_object('submits', b.submits - c.submits, 'snap_end', b.snap_date, 'snap_start', c.snap_date)
                           from pick7 b, pick14 c))
    into v_submits;

  -- 검색(GSC): 이번 주 vs 지난주 합계 + 용어사전 페이지 CTR(S-01 판정 재료). CTR 은 저장하지
  -- 않고 여기서 파생한다(078 과 같은 규칙 — 평균의 평균 방지). 순위는 노출 가중 평균.
  select jsonb_build_object(
           'latest_date', (select max(snap_date) from public.gsc_daily),
           'this_week', (select jsonb_build_object(
                           'clicks', coalesce(sum(clicks), 0), 'impressions', coalesce(sum(impressions), 0),
                           'ctr_pct', case when coalesce(sum(impressions), 0) > 0 then round(100.0 * sum(clicks) / sum(impressions), 2) end,
                           'avg_position', case when coalesce(sum(impressions), 0) > 0 then round((sum(avg_position * impressions) / sum(impressions))::numeric, 1) end,
                           'days_with_data', count(*))
                           from public.gsc_daily where snap_date between w_start and d),
           'prev_week', (select jsonb_build_object(
                           'clicks', coalesce(sum(clicks), 0), 'impressions', coalesce(sum(impressions), 0),
                           'ctr_pct', case when coalesce(sum(impressions), 0) > 0 then round(100.0 * sum(clicks) / sum(impressions), 2) end,
                           'avg_position', case when coalesce(sum(impressions), 0) > 0 then round((sum(avg_position * impressions) / sum(impressions))::numeric, 1) end,
                           'days_with_data', count(*))
                           from public.gsc_daily where snap_date between p_start and p_end),
           'glossary_this_week', (select jsonb_build_object(
                           'clicks', coalesce(sum(clicks), 0), 'impressions', coalesce(sum(impressions), 0),
                           'ctr_pct', case when coalesce(sum(impressions), 0) > 0 then round(100.0 * sum(clicks) / sum(impressions), 2) end)
                           from public.gsc_page_daily where snap_date between w_start and d and page_path like '/glossary/%'),
           'glossary_prev_week', (select jsonb_build_object(
                           'clicks', coalesce(sum(clicks), 0), 'impressions', coalesce(sum(impressions), 0),
                           'ctr_pct', case when coalesce(sum(impressions), 0) > 0 then round(100.0 * sum(clicks) / sum(impressions), 2) end)
                           from public.gsc_page_daily where snap_date between p_start and p_end and page_path like '/glossary/%'),
           'top_pages', coalesce((select jsonb_agg(jsonb_build_object('page', g.page_path, 'clicks', g.c, 'impressions', g.i,
                                   'ctr_pct', case when g.i > 0 then round(100.0 * g.c / g.i, 2) end,
                                   'avg_position', round(g.pos::numeric, 1)) order by g.i desc, g.page_path)
                          from (select page_path, sum(clicks) c, sum(impressions) i, sum(avg_position * impressions) / nullif(sum(impressions), 0) pos
                                  from public.gsc_page_daily where snap_date between w_start and d
                                 group by page_path order by sum(impressions) desc limit 10) g), '[]'::jsonb))
    into v_gsc;

  -- 회원(로그인 계정)
  select jsonb_build_object(
           'total', count(*),
           'new_this_week', count(*) filter (where (created_at at time zone 'Asia/Seoul')::date between w_start and d),
           'new_prev_week', count(*) filter (where (created_at at time zone 'Asia/Seoul')::date between p_start and p_end),
           'signed_in_this_week', count(*) filter (where (last_sign_in_at at time zone 'Asia/Seoul')::date between w_start and d))
    into v_members
    from auth.users;

  -- 구역별 전환(누적, 076 적용일 이후) — 087 의 판독 함수를 그대로 쓴다.
  v_zone := public.funnel_zone_report();

  -- 채널별 신청(087, 누적) + 직전 주 스냅샷과의 차분 = 이번 주.
  v_touch := public.funnel_touch_report();
  if v_prev is null or v_prev->'touch'->'rows' is null then
    v_touch_week := null;
  else
    with cur as (
      select * from jsonb_to_recordset(v_touch->'rows')
        as x(key text, source text, medium text, campaign text, ref_host text, landing_zone text, channel text, total int)),
    prev as (
      select * from jsonb_to_recordset(v_prev->'touch'->'rows')
        as x(key text, source text, medium text, campaign text, ref_host text, landing_zone text, total int)),
    diff as (
      select c.channel, c.source, c.medium, c.campaign, c.ref_host, c.landing_zone, c.key,
             c.total - coalesce(p.total, 0) as week
        from cur c left join prev p using (key, source, medium, campaign, ref_host, landing_zone))
    select jsonb_build_object(
             'by_channel', coalesce((select jsonb_agg(jsonb_build_object('channel', g.channel, 'submits', g.s) order by g.s desc, g.channel)
                                     from (select channel, sum(week) s from diff group by channel having sum(week) > 0) g), '[]'::jsonb),
             'rows', coalesce((select jsonb_agg(jsonb_build_object('key', key, 'channel', channel, 'source', source, 'medium', medium,
                               'campaign', campaign, 'ref_host', ref_host, 'landing_zone', landing_zone, 'submits', week)
                               order by week desc, channel) from diff where week > 0), '[]'::jsonb),
             'total', coalesce((select sum(week) from diff where week > 0), 0))
      into v_touch_week;
  end if;

  -- 경로별 신청(084, 누적) + 직전 주 차분.
  v_paths := public.funnel_paths_report();
  if v_prev is null or v_prev->'paths'->'paths' is null then
    v_paths_week := null;
  else
    with cur as (
      select * from jsonb_to_recordset(v_paths->'paths') as x(key text, path text, total int)),
    prev as (
      select * from jsonb_to_recordset(v_prev->'paths'->'paths') as x(key text, path text, total int)),
    diff as (
      select c.key, c.path, c.total - coalesce(p.total, 0) as week
        from cur c left join prev p using (key, path))
    select jsonb_build_object(
             'rows', coalesce((select jsonb_agg(jsonb_build_object('key', key, 'path', path, 'submits', week) order by week desc, path)
                               from diff where week > 0), '[]'::jsonb),
             'total', coalesce((select sum(week) from diff where week > 0), 0))
      into v_paths_week;
  end if;

  select jsonb_build_object(
           'rum_days_this_week', (v_visits->'this_week'->>'days_with_data')::int,
           'gsc_latest_date', v_gsc->>'latest_date',
           'gsc_lag_days', d - (v_gsc->>'latest_date')::date,
           'newsletter_snapshot_date', v_subs->'now'->>'snap_date',
           'funnel_snapshot_end', v_submits->'this_week'->>'snap_end',
           'prev_week_snapshot_present', v_prev is not null,
           'basis', '방문=Cloudflare RUM(bot:0·운영자 제외), 날짜는 UTC 기준. 구독자=Brevo 리스트 아침 스냅샷. 신청=깔때기 23:55 KST 스냅샷 차분. 채널·경로 주간값=직전 주 리포트 스냅샷과의 차분(첫 주는 null). 검색=GSC 확정 데이터(2~3일 지연). 노출(view) 카운터는 크롤러 오염이라 쓰지 않는다.')
    into v_quality;

  payload := jsonb_build_object(
    'week_end', d,
    'week_start', w_start,
    'generated_at_kst', to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
    'visits', v_visits,
    'subscribers', v_subs,
    'submits', v_submits,
    'touch', v_touch,
    'touch_week', v_touch_week,
    'zone', v_zone,
    'paths', v_paths,
    'paths_week', v_paths_week,
    'gsc', v_gsc,
    'members', v_members,
    'data_quality', v_quality);

  if p_persist then
    insert into public.growth_weekly_reports as g (week_end, payload)
    values (d, payload)
    on conflict (week_end) do update set payload = excluded.payload, updated_at = now();
  end if;

  return payload || jsonb_build_object('persisted', p_persist);
end;
$$;

revoke all on function public.growth_weekly_report(date, boolean) from public, anon, authenticated;
grant execute on function public.growth_weekly_report(date, boolean) to service_role;

comment on function public.growth_weekly_report(date, boolean) is
  '주간 성장 리포트 JSON(지난주 월~일). 자기 스냅샷을 growth_weekly_reports 에 남겨 채널·경로 주간값을 직전 주와의 차분으로 낸다. 운영 도구 전용(service_role).';
