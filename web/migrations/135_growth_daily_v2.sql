-- 135: 성장 일보 v2 — 방문 → 검색 유입 → 구독 → 메일 클릭을 한 장으로 + 주간 검색 개선 판독 (2026-11-08).
--
-- 계기: 대표 지시 "MCP 연결됐으면 활용해야지". 수집은 이미 매일 돈다(grm-rum-analytics.yml —
-- RUM·Brevo 구독자·Brevo 캠페인·Search Console). 비어 있던 것은 **잇는 판독**이다. 그래서
-- 새 수집 경로는 만들지 않고, 기존 보고 함수 셋(077 growth_daily_report · 078 gsc_report ·
-- 090 캠페인 표)을 한 번에 돌려주는 합성 함수와, 주 1회 검색 개선용 판독 함수만 더한다.
-- 렌더링(마크다운)은 `web/growth_daily.py` 가 한다 — 판단 규칙을 SQL 이 아니라 테스트 가능한
-- 파이썬에 둔다.
--
-- ★가산만 한다(RPC 계약 변경 = 장애 창 교훈). 077·078·090 함수와 기존 열은 건드리지 않는다.
--
-- ★★메일 클릭 — Brevo 가 같은 캠페인에 클릭 수를 두 벌 준다(2026-11-08 MCP 실측, 11-02 호):
--     globalStats.uniqueClicks = 72 · clickers = 74   (수신 29명인데 72 — 사람 수일 수 없다)
--     campaignStats[목록 3].uniqueClicks = 6 · clickers = 8
--     linksStats(링크별 클릭) 합 = 8                  ← 목록 집계와 일치
--   영문 목록(수신 1명)도 globalStats 4 vs 목록 0 이다. 차이의 정체를 Brevo 가 밝히지 않으므로
--   단정하지 않는다(가설: 사내 메일 보안 스캐너의 링크 사전 방문). 090 은 globalStats 를 적재해
--   왔다 — 그 열은 그대로 두고(지금 읽는 소비자가 있다) **목록 기준 열을 가산**한다. 판독은
--   목록 기준 → 없으면 링크별 합으로 내려가고, globalStats 는 참고값으로만 싣는다.
--
-- 권한: 077·078 과 같다 — 보고 함수는 security definer(auth.users 를 세는 077 을 부른다)라
-- service_role 만 실행한다. 새 열은 090 표의 기존 RLS(authenticated 읽기)를 그대로 따른다.

alter table public.newsletter_campaign_stats
  add column if not exists list_delivered integer check (list_delivered >= 0),
  add column if not exists list_unique_views integer check (list_unique_views >= 0),
  add column if not exists list_unique_clicks integer check (list_unique_clicks >= 0),
  add column if not exists list_clickers integer check (list_clickers >= 0);

comment on column public.newsletter_campaign_stats.list_unique_clicks is
  'Brevo campaignStats(목록별) uniqueClicks 의 합. globalStats.uniqueClicks(unique_clicks 열)는 수신자 수보다 큰 값이 와서 사람 수로 읽을 수 없다(135 주석). 판독은 이 열을 쓴다.';
comment on column public.newsletter_campaign_stats.unique_clicks is
  'Brevo globalStats.uniqueClicks — 수신자 수보다 커질 수 있다(2026-11-08 실측 29명에 72). 사람 수가 아니다. 판독은 list_unique_clicks(135)를 쓴다.';

-- ---------------------------------------------------------------------------
-- 성장 일보 v2 — 한 번 호출로 일보 한 장의 재료 전부.
--   growth : 077 growth_daily_report(p_date) 그대로(방문·유입·구역·구독자·깔때기·회원)
--   search : 078 gsc_report() 그대로(검색 기준일은 2~3일 늦다 — 자체 report_date)
--   pages  : 기준일 상위 페이지(rum_path_daily — 착지가 아니라 열람 전체)
--   mail   : 최근 주간 메일의 링크별 클릭 상위 + 최근 캠페인 + 해지
-- ---------------------------------------------------------------------------
create or replace function public.growth_daily_v2(p_date date default null)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  g jsonb;
  d date;
begin
  g := public.growth_daily_report(p_date);
  d := (g ->> 'report_date')::date;

  return jsonb_build_object(
    'version', 2,
    'report_date', d,
    'generated_at_kst', to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
    'growth', g,
    'search', public.gsc_report(null),
    'pages', jsonb_build_object(
      'day', coalesce((
        select jsonb_agg(jsonb_build_object('path', p.request_path, 'zone', public.grm_zone_of(p.request_path),
                                            'visits', p.visits, 'sample_interval', p.sample_interval)
                         order by p.visits desc, p.request_path)
          from (select request_path, visits, sample_interval
                  from public.rum_path_daily
                 where snap_date = d
                 order by visits desc, request_path
                 limit 10) p), '[]'::jsonb),
      'week', coalesce((
        select jsonb_agg(jsonb_build_object('path', p.request_path, 'zone', public.grm_zone_of(p.request_path),
                                            'visits', p.visits)
                         order by p.visits desc, p.request_path)
          from (select request_path, sum(visits)::int as visits
                  from public.rum_path_daily
                 where snap_date between d - 6 and d
                 group by request_path
                 order by sum(visits) desc, request_path
                 limit 10) p), '[]'::jsonb)),
    'mail', (
      with c as (
        select s.*,
               coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                          where v.key !~ '#fb-(up|down)$'), 0) as link_clicks,
               coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                          where v.key ~ '#fb-up$'), 0) as fb_up,
               coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                          where v.key ~ '#fb-down$'), 0) as fb_down
          from public.newsletter_campaign_stats s
         where s.sent_at is not null),
      w as (select * from c where kind = 'weekly' order by sent_at desc limit 1)
      select jsonb_build_object(
        'latest_weekly', (select jsonb_build_object(
            'campaign_id', w.campaign_id, 'name', w.name, 'publish_date', w.publish_date,
            'sent_at_kst', to_char(w.sent_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
            'delivered', coalesce(w.list_delivered, w.delivered),
            'list_unique_clicks', w.list_unique_clicks,
            'list_clickers', w.list_clickers,
            'link_clicks', w.link_clicks,
            'global_unique_clicks', w.unique_clicks,
            'unsubscriptions', w.unsubscriptions,
            'fb_up', w.fb_up, 'fb_down', w.fb_down,
            'links_top', coalesce((
              select jsonb_agg(jsonb_build_object('url', l.url, 'clicks', l.clicks) order by l.clicks desc, l.url)
                from (select regexp_replace(v.key, '^https?://[^/]+', '') as url, (v.value)::numeric::int as clicks
                        from jsonb_each_text(w.links) v
                       where v.key !~ '#fb-(up|down)$'
                       order by (v.value)::numeric desc, v.key
                       limit 8) l), '[]'::jsonb),
            'captured_at_kst', to_char(w.captured_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
          from w),
        'recent', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'name', r.name, 'kind', r.kind,
                   'sent_at_kst', to_char(r.sent_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
                   'delivered', coalesce(r.list_delivered, r.delivered),
                   'list_unique_clicks', r.list_unique_clicks, 'link_clicks', r.link_clicks,
                   'global_unique_clicks', r.unique_clicks, 'unsubscriptions', r.unsubscriptions)
                 order by r.sent_at desc)
            from (select * from c order by sent_at desc limit 6) r), '[]'::jsonb),
        'unsubscriptions_14d', (select coalesce(sum(unsubscriptions), 0) from c
                                 where sent_at >= (d - 13)::timestamp at time zone 'Asia/Seoul'),
        'latest_capture_kst', (select to_char(max(captured_at) at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') from c),
        'basis', '클릭은 Brevo 목록 기준(list_unique_clicks) → 없으면 링크별 클릭 합(link_clicks). globalStats(global_unique_clicks)는 수신자 수보다 커질 수 있어 사람 수가 아니다. 열람은 Apple 메일 개인정보 보호가 부풀려 판정에 쓰지 않는다. 메일 링크는 브리프 페이지·구역 단위라 카드별 클릭은 아직 셀 수 없다.'))
  );
end;
$$;

revoke all on function public.growth_daily_v2(date) from public, anon, authenticated;
grant execute on function public.growth_daily_v2(date) to service_role;

-- ---------------------------------------------------------------------------
-- 주간 검색 개선 판독 — Search Console 최근 p_days 일(기준 = 확정 데이터의 마지막 날).
--   totals            : 창 합계와 직전 창(같은 길이) 합계
--   low_ctr_pages     : 1페이지(평균 순위 ≤10)에 노출되는데 클릭률이 낮은 페이지 — 제목·설명 문제
--   striking_pages    : 평균 순위 8~20 — 조금만 올리면 1페이지 상단
--   striking_queries  : 같은 기준의 검색어
--   duplicate_paths   : index.html·쿼리 꼬리가 붙은 경로 — 같은 페이지가 두 주소로 노출(정규 주소 문제)
--   topics            : 새 자료와 맞닿은 검색 수요(FDA 지적 조항·일본 사례·영국 사례)
-- 순위는 노출 가중 평균이다(일별 평균 순위의 단순 평균은 노출 적은 날을 과대 반영한다).
-- ★Postgres 정규식에서 단어 경계는 \y 다(\b 는 백스페이스). 대소문자 무시는 ~* 로.
-- ---------------------------------------------------------------------------
create or replace function public.gsc_search_opportunities(p_days integer default 28,
                                                           p_min_impressions integer default 3)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  n int := least(greatest(coalesce(p_days, 28), 7), 90);
  m int := greatest(coalesce(p_min_impressions, 3), 1);
  mx date;
begin
  select max(snap_date) into mx from public.gsc_daily;
  if mx is null then
    return jsonb_build_object('connected', false, 'basis', 'gsc_daily 가 비어 있다 — Search Console 연동 대기');
  end if;

  return (
    with pg as (
      select page_path,
             sum(clicks)::int as clicks, sum(impressions)::int as impressions,
             round(sum(avg_position * impressions) / nullif(sum(impressions), 0), 1) as pos
        from public.gsc_page_daily
       where snap_date > mx - n and snap_date <= mx
       group by page_path),
    q as (
      select query,
             sum(clicks)::int as clicks, sum(impressions)::int as impressions,
             round(sum(avg_position * impressions) / nullif(sum(impressions), 0), 1) as pos
        from public.gsc_query_daily
       where snap_date > mx - n and snap_date <= mx
       group by query),
    topic(key, label, q_re, p_re) as (values
      ('fda_clause', 'FDA 지적 조항', '(21\s*cfr|cfr\s*21|\y211\.\d|§\s*211|\y483\y|warning letter|경고\s*서한|cgmp)', '(fda483-|/fda/|/agency/fda|/wl-)'),
      ('japan', '일본 사례', '(pmda|japan|japanese|일본|日本|厚生労働|gmp適合)', '(pmda|/japan|/jp/)'),
      ('uk', '영국 사례', '(mhra|united kingdom|\yuk\y|영국|britain)', '(mhra|/uk/)'))
    select jsonb_build_object(
      'connected', true,
      'window', jsonb_build_object('days', n, 'start', mx - n + 1, 'end', mx, 'min_impressions', m),
      'totals', (select jsonb_build_object(
          'clicks', coalesce(sum(clicks) filter (where snap_date > mx - n), 0),
          'impressions', coalesce(sum(impressions) filter (where snap_date > mx - n), 0),
          'pos', round(sum(avg_position * impressions) filter (where snap_date > mx - n)
                       / nullif(sum(impressions) filter (where snap_date > mx - n), 0), 1),
          'days_with_data', count(*) filter (where snap_date > mx - n),
          'prev_clicks', coalesce(sum(clicks) filter (where snap_date <= mx - n), 0),
          'prev_impressions', coalesce(sum(impressions) filter (where snap_date <= mx - n), 0),
          'prev_days_with_data', count(*) filter (where snap_date <= mx - n))
        from public.gsc_daily where snap_date > mx - 2 * n and snap_date <= mx),
      'low_ctr_pages', coalesce((select jsonb_agg(jsonb_build_object(
            'path', page_path, 'zone', public.grm_zone_of(page_path), 'clicks', clicks,
            'impressions', impressions, 'ctr_pct', round(100.0 * clicks / impressions, 1), 'pos', pos)
            order by impressions desc, page_path)
          from (select * from pg where impressions >= m and pos <= 10
                   and clicks::numeric / impressions < 0.05
                 order by impressions desc, page_path limit 15) x), '[]'::jsonb),
      'striking_pages', coalesce((select jsonb_agg(jsonb_build_object(
            'path', page_path, 'zone', public.grm_zone_of(page_path), 'clicks', clicks,
            'impressions', impressions, 'pos', pos) order by impressions desc, page_path)
          from (select * from pg where impressions >= m and pos between 8 and 20
                 order by impressions desc, page_path limit 15) x), '[]'::jsonb),
      'striking_queries', coalesce((select jsonb_agg(jsonb_build_object(
            'query', query, 'clicks', clicks, 'impressions', impressions, 'pos', pos)
            order by impressions desc, query)
          from (select * from q where impressions >= least(m, 2) and pos between 8 and 20
                 order by impressions desc, query limit 15) x), '[]'::jsonb),
      'duplicate_paths', coalesce((select jsonb_agg(jsonb_build_object(
            'path', page_path, 'impressions', impressions, 'clicks', clicks, 'pos', pos)
            order by impressions desc, page_path)
          from (select * from pg where page_path ~ '(/index\.html$|\?)'
                 order by impressions desc, page_path limit 10) x), '[]'::jsonb),
      'topics', (select jsonb_agg(jsonb_build_object(
            'key', t.key, 'label', t.label,
            'query_impressions', coalesce((select sum(impressions) from q where q.query ~* t.q_re), 0),
            'query_clicks', coalesce((select sum(clicks) from q where q.query ~* t.q_re), 0),
            'queries', coalesce((select jsonb_agg(jsonb_build_object('query', x.query, 'impressions', x.impressions,
                                                                     'clicks', x.clicks, 'pos', x.pos)
                                                  order by x.impressions desc, x.query)
                                   from (select * from q where q.query ~* t.q_re
                                          order by impressions desc, query limit 8) x), '[]'::jsonb),
            'page_impressions', coalesce((select sum(impressions) from pg where pg.page_path ~* t.p_re), 0),
            'page_clicks', coalesce((select sum(clicks) from pg where pg.page_path ~* t.p_re), 0),
            'pages', coalesce((select jsonb_agg(jsonb_build_object('path', x.page_path, 'impressions', x.impressions,
                                                                   'clicks', x.clicks, 'pos', x.pos)
                                                order by x.impressions desc, x.page_path)
                                 from (select * from pg where pg.page_path ~* t.p_re
                                        order by impressions desc, page_path limit 5) x), '[]'::jsonb))
            order by t.key)
          from topic t),
      'query_rows', (select count(*) from q),
      'page_rows', (select count(*) from pg),
      'generated_at_kst', to_char(now() at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
      'basis', 'Search Console 확정 데이터(구글만 — 네이버는 읽기 API 가 없다). 기준일은 2~3일 늦다. 검색어 표는 구글이 희소 검색어를 익명화로 빼서 합이 사이트 총계보다 작다. 페이지 표는 한 검색에 여러 페이지가 노출되면 각각 세어 합이 총계보다 클 수 있다. 순위는 노출 가중 평균.')
  );
end;
$$;

revoke all on function public.gsc_search_opportunities(integer, integer) from public, anon, authenticated;
grant execute on function public.gsc_search_opportunities(integer, integer) to service_role;
