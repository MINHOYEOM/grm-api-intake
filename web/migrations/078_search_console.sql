-- 078 — Google Search Console 일별 적재 + 보고 함수 (2026-09-05)
--
-- Cloudflare RUM 은 "google.com 에서 왔다"까지만 안다. **무엇을 검색해서 왔는지**는
-- Search Console 만 안다 — 08-12 SEO 감사에서 이미 "남은 건 여기서 쿼리를 읽는 것"으로
-- 지목됐던 공백을 이 파일이 채운다.
--
-- ★읽기 권한은 072·077 과 같다: authenticated 만(운영 지표). 쓰기는 service_role.
-- ★CTR 은 저장하지 않는다 — 클릭÷노출로 언제든 나오고, 저장해 두면 합산할 때
--   "평균의 평균"이라는 틀린 수가 만들어진다. 순위도 합산은 반드시 노출 가중이다.
-- ★열 이름은 `avg_position` 이다. `position` 은 PostgreSQL 의 내장 함수 이름이라
--   열 이름으로 쓰면 문맥에 따라 파싱이 갈린다 — 굳이 그 위험을 살 이유가 없다.
-- ★검색어는 구글이 이미 익명화한 집계값이다(희소 검색어는 응답에서 통째로 빠진다).
--   그래서 검색어 행의 클릭 합 ≤ 사이트 총 클릭이고, 이 차이는 결함이 아니다.

create table if not exists public.gsc_daily (
  snap_date date not null primary key,
  clicks integer not null check (clicks >= 0),
  impressions integer not null check (impressions >= 0),
  avg_position numeric not null check (avg_position >= 0)
);

create table if not exists public.gsc_query_daily (
  snap_date date not null,
  query text not null,
  clicks integer not null check (clicks >= 0),
  impressions integer not null check (impressions >= 0),
  avg_position numeric not null check (avg_position >= 0),
  primary key (snap_date, query)
);

create table if not exists public.gsc_page_daily (
  snap_date date not null,
  page_path text not null,
  clicks integer not null check (clicks >= 0),
  impressions integer not null check (impressions >= 0),
  avg_position numeric not null check (avg_position >= 0),
  primary key (snap_date, page_path)
);

comment on table public.gsc_daily is
  'Search Console 사이트 총합(익명화된 희소 검색어 포함) — 검색어 표의 합보다 크거나 같다.';
comment on column public.gsc_daily.avg_position is
  '평균 게재순위(1=검색결과 첫 번째). 합산 시 노출 가중 평균이라야 뜻이 맞는다.';
comment on table public.gsc_query_daily is
  '검색어별 일별 지표. 구글이 희소 검색어를 익명화해 제외하므로 사이트 총합과 다르다.';
comment on column public.gsc_page_daily.page_path is
  'URL 경로만(스킴·호스트·쿼리스트링 제거) — rum_path_daily 와 같은 규칙.';

alter table public.gsc_daily enable row level security;
alter table public.gsc_query_daily enable row level security;
alter table public.gsc_page_daily enable row level security;

revoke all on public.gsc_daily from public, anon, authenticated;
revoke all on public.gsc_query_daily from public, anon, authenticated;
revoke all on public.gsc_page_daily from public, anon, authenticated;

grant select on public.gsc_daily to authenticated;
grant select on public.gsc_query_daily to authenticated;
grant select on public.gsc_page_daily to authenticated;

drop policy if exists "signed-in can read gsc daily" on public.gsc_daily;
create policy "signed-in can read gsc daily"
on public.gsc_daily for select to authenticated using (true);

drop policy if exists "signed-in can read gsc queries" on public.gsc_query_daily;
create policy "signed-in can read gsc queries"
on public.gsc_query_daily for select to authenticated using (true);

drop policy if exists "signed-in can read gsc pages" on public.gsc_page_daily;
create policy "signed-in can read gsc pages"
on public.gsc_page_daily for select to authenticated using (true);

-- ---------------------------------------------------------------------------
-- 구역 분류 함수 `grm_zone_of` 는 **077 에 정의돼 있다**(사본 없음 — 077 착지 표와
-- 여기 검색 페이지 표가 같은 것을 부른다). 마이그레이션은 번호 순으로 적용되므로
-- 여기서는 재정의하지 않고 그대로 쓴다.

-- ---------------------------------------------------------------------------
-- 보고 함수. **RUM 과 기준일이 다르다** — GSC 확정 데이터는 2~3일 늦게 오므로 기본
-- 기준일은 "어제"가 아니라 **GSC 가 실제로 준 최신 날짜**다. 이 날짜를 보고가 밝힌다.
--
-- 별도 함수인 이유: 077 의 growth_daily_report 를 통째로 다시 쓰면 200줄이 복제된다.
-- 호출자가 두 번 부르는 편이 싸고, 두 데이터의 기준일이 애초에 다르므로 분리가 자연스럽다.
create or replace function public.gsc_report(p_date date default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  latest date;
  d date;
  d7_start date;
  p7_start date;
  p7_end date;
  v_day jsonb;
  v_week jsonb;
  v_queries jsonb;
  v_rising jsonb;
  v_pages jsonb;
  v_zones jsonb;
  v_opportunity jsonb;
  v_trend jsonb;
  v_quality jsonb;
begin
  select max(snap_date) into latest from public.gsc_daily;
  if latest is null then
    -- ★"검색 유입 0" 이 아니라 "아직 연결되지 않았다" 다. 부재의 어휘를 지킨다.
    return jsonb_build_object(
      'connected', false,
      'reason', 'gsc_daily 에 행이 없다 — 서비스 계정 배선 전이거나 첫 수집 대기',
      'generated_at_kst', to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'));
  end if;

  d := least(coalesce(p_date, latest), latest);
  d7_start := d - 6;
  p7_start := d - 13;
  p7_end := d - 7;

  -- 기준일 하루
  select jsonb_build_object(
           'date', snap_date, 'clicks', clicks, 'impressions', impressions,
           'avg_position', avg_position,
           'ctr_pct', case when impressions > 0
                           then round(100.0 * clicks / impressions, 1) else null end)
    into v_day
    from public.gsc_daily where snap_date = d;

  -- 최근 7일 vs 직전 7일. 순위는 노출 가중 평균(단순 평균은 뜻이 없다).
  select jsonb_build_object(
           'this_week', jsonb_build_object(
             'start', d7_start, 'end', d,
             'clicks', coalesce(sum(clicks) filter (where snap_date between d7_start and d), 0),
             'impressions', coalesce(sum(impressions) filter (where snap_date between d7_start and d), 0),
             'days_with_data', count(*) filter (where snap_date between d7_start and d),
             'avg_position', round(coalesce(
               sum(avg_position * impressions) filter (where snap_date between d7_start and d)
               / nullif(sum(impressions) filter (where snap_date between d7_start and d), 0), 0), 1),
             'ctr_pct', round(100.0 * coalesce(sum(clicks) filter (where snap_date between d7_start and d), 0)
               / nullif(sum(impressions) filter (where snap_date between d7_start and d), 0), 1)),
           'prev_week', jsonb_build_object(
             'start', p7_start, 'end', p7_end,
             'clicks', coalesce(sum(clicks) filter (where snap_date between p7_start and p7_end), 0),
             'impressions', coalesce(sum(impressions) filter (where snap_date between p7_start and p7_end), 0),
             'days_with_data', count(*) filter (where snap_date between p7_start and p7_end),
             'avg_position', round(coalesce(
               sum(avg_position * impressions) filter (where snap_date between p7_start and p7_end)
               / nullif(sum(impressions) filter (where snap_date between p7_start and p7_end), 0), 0), 1),
             'ctr_pct', round(100.0 * coalesce(sum(clicks) filter (where snap_date between p7_start and p7_end), 0)
               / nullif(sum(impressions) filter (where snap_date between p7_start and p7_end), 0), 1)))
    into v_week
    from public.gsc_daily where snap_date between p7_start and d;

  -- 14일 흐름
  select coalesce(jsonb_agg(jsonb_build_object(
           'date', snap_date,
           'weekday', (array['월','화','수','목','금','토','일'])[extract(isodow from snap_date)::int],
           'clicks', clicks, 'impressions', impressions) order by snap_date), '[]'::jsonb)
    into v_trend
    from public.gsc_daily where snap_date between d - 13 and d;

  -- 최근 7일 검색어 상위 — "사람들이 무슨 말로 우리를 찾나"
  -- ★상한(limit)은 반드시 집계 **안쪽** 서브쿼리에 건다. 바깥에 걸면 결과가 한 행뿐이라
  --   아무것도 자르지 못하고 전량이 배열에 들어간다.
  select coalesce(jsonb_agg(jsonb_build_object(
           'query', q.query, 'clicks', q.clicks, 'impressions', q.impressions,
           'avg_position', q.avg_position,
           'ctr_pct', case when q.impressions > 0
                           then round(100.0 * q.clicks / q.impressions, 1) else null end)
           order by q.clicks desc, q.impressions desc, q.query), '[]'::jsonb)
    into v_queries
    from (select query,
                 sum(clicks) as clicks,
                 sum(impressions) as impressions,
                 round(sum(avg_position * impressions) / nullif(sum(impressions), 0), 1) as avg_position
            from public.gsc_query_daily
           where snap_date between d7_start and d
           group by query
           order by sum(clicks) desc, sum(impressions) desc, query
           limit 20) q;

  -- 떠오르는 검색어 — 이번 7일 노출이 직전 7일보다 늘어난 것(새로 등장한 것 포함).
  -- ★"검색 수요가 어디로 움직이나"는 총량보다 이 델타가 먼저 말해 준다.
  select coalesce(jsonb_agg(jsonb_build_object(
           'query', r.query, 'clicks', r.clicks, 'impressions', r.impressions,
           'prev_impressions', r.prev_impressions, 'delta', r.delta, 'is_new', r.is_new)
           order by r.delta desc, r.query), '[]'::jsonb)
    into v_rising
    from (with cur as (
            select query, sum(clicks) as clicks, sum(impressions) as impressions
              from public.gsc_query_daily
             where snap_date between d7_start and d group by query),
          prv as (
            select query, sum(impressions) as impressions
              from public.gsc_query_daily
             where snap_date between p7_start and p7_end group by query)
          select c.query, c.clicks, c.impressions,
                 coalesce(p.impressions, 0) as prev_impressions,
                 c.impressions - coalesce(p.impressions, 0) as delta,
                 (p.query is null) as is_new
            from cur c left join prv p on p.query = c.query
           where c.impressions - coalesce(p.impressions, 0) >= 5
           order by (c.impressions - coalesce(p.impressions, 0)) desc, c.query
           limit 12) r;

  -- 최근 7일 착지 페이지(검색 기준) + 구역
  select coalesce(jsonb_agg(jsonb_build_object(
           'page', t.page_path, 'zone', public.grm_zone_of(t.page_path),
           'clicks', t.clicks, 'impressions', t.impressions, 'avg_position', t.avg_position,
           'ctr_pct', case when t.impressions > 0
                           then round(100.0 * t.clicks / t.impressions, 1) else null end)
           order by t.clicks desc, t.impressions desc, t.page_path), '[]'::jsonb)
    into v_pages
    from (select page_path, sum(clicks) as clicks, sum(impressions) as impressions,
                 round(sum(avg_position * impressions) / nullif(sum(impressions), 0), 1) as avg_position
            from public.gsc_page_daily
           where snap_date between d7_start and d
           group by page_path
           order by sum(clicks) desc, sum(impressions) desc, page_path
           limit 15) t;

  select coalesce(jsonb_agg(jsonb_build_object(
           'zone', z.zone, 'clicks', z.clicks, 'impressions', z.impressions)
           order by z.clicks desc, z.impressions desc, z.zone), '[]'::jsonb)
    into v_zones
    from (select public.grm_zone_of(page_path) as zone,
                 sum(clicks) as clicks, sum(impressions) as impressions
            from public.gsc_page_daily
           where snap_date between d7_start and d
           group by 1) z;

  -- ★기회 페이지 — 노출은 쌓이는데 클릭이 거의 없다. 원인은 둘 중 하나이고 조치가 다르다:
  --   순위가 낮아 안 보이거나(avg_position 큼 → 내용·내부링크), 보이는데 안 눌리거나
  --   (avg_position 작음 → 제목·설명). 보고는 이 둘을 갈라 말해야 한다.
  select coalesce(jsonb_agg(jsonb_build_object(
           'page', t.page_path, 'zone', public.grm_zone_of(t.page_path),
           'clicks', t.clicks, 'impressions', t.impressions, 'avg_position', t.avg_position,
           'ctr_pct', round(100.0 * t.clicks / t.impressions, 1),
           'likely_cause', case when t.avg_position > 10
                                then '순위가 낮아 잘 안 보인다(2페이지 이후) — 내용·내부링크'
                                else '보이는데 안 눌린다 — 제목·설명 후보' end)
           order by t.impressions desc, t.page_path), '[]'::jsonb)
    into v_opportunity
    from (select page_path, sum(clicks) as clicks, sum(impressions) as impressions,
                 round(sum(avg_position * impressions) / nullif(sum(impressions), 0), 1) as avg_position
            from public.gsc_page_daily
           where snap_date between d7_start and d
           group by page_path
          having sum(impressions) >= 20
             and 100.0 * sum(clicks) / nullif(sum(impressions), 0) < 2.0
           order by sum(impressions) desc, page_path
           limit 8) t;

  -- 데이터 상태 — 지연·익명화 폭을 반드시 보고에 싣게 한다.
  select jsonb_build_object(
           'latest_date', latest,
           'lag_days', ((now() at time zone 'Asia/Seoul')::date - latest),
           'days_with_data_14', (select count(*) from public.gsc_daily where snap_date between d - 13 and d),
           'query_clicks_7d', (select coalesce(sum(clicks), 0) from public.gsc_query_daily where snap_date between d7_start and d),
           'total_clicks_7d', (select coalesce(sum(clicks), 0) from public.gsc_daily where snap_date between d7_start and d),
           'query_rows_capped_at', 60,
           'lag_basis', 'Search Console 확정 데이터는 보통 2~3일 늦게 도착한다 — 최근 날짜가 비어 있는 것은 유입 0 이 아니다',
           'anonymization_basis', '구글은 희소 검색어를 응답에서 통째로 제외한다(개인 식별 방지). 그래서 검색어 표의 클릭 합은 사이트 총 클릭보다 항상 작거나 같다',
           'metric_basis', '노출=검색결과에 보인 횟수 · 클릭=눌린 횟수 · CTR=클릭÷노출 · 평균순위=검색결과 몇 번째(1~10이 1페이지)')
    into v_quality;

  return jsonb_build_object(
    'connected', true,
    'report_date', d,
    'generated_at_kst', to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
    'day', v_day,
    'trend_14d', v_trend,
    'week_compare', v_week,
    'top_queries', v_queries,
    'rising_queries', v_rising,
    'top_pages', v_pages,
    'zones', v_zones,
    'opportunity_pages', v_opportunity,
    'data_quality', v_quality);
end;
$$;

revoke all on function public.gsc_report(date) from public, anon, authenticated;
grant execute on function public.gsc_report(date) to service_role;

comment on function public.gsc_report(date) is
  '검색(Search Console) 일일 보고용 JSON. 기준일 기본값은 어제가 아니라 GSC 최신 확정일(2~3일 지연). 운영 도구 전용.';
