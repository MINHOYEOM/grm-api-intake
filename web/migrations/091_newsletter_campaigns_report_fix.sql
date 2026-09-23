-- 091: 뉴스레터 캠페인 판독 함수 정정 — 090 `newsletter_campaigns_report` 의 클릭률이 100% 를 넘었다.
--
-- 계기(2026-09-23 첫 적재 실측): 13명에게 보낸 9/21 호의 Brevo globalStats 가 `uniqueClicks` 41·
-- `clickers` 42 였다. 둘 다 **사람 수가 아니다**(도달 13 보다 크다) — 090 이 `clickers ÷ delivered`
-- 로 낸 클릭률이 323% 가 됐다. 반면 링크별 클릭(linksStats)은 그 호에서 브리프 본 링크 6·나머지 0
-- 이었다. 그리고 9/7 호는 워치리스트 하나만 빼고 **모든 링크가 똑같이 3회**씩 눌렸다 — 회사 메일의
-- 보안 링크 검사기가 받은 메일의 링크를 전부 여는 전형이다. 이런 호에서는 1클릭 피드백의 👍·👎 도
-- 같은 수만큼 함께 눌린다.
--
-- 그래서 판독을 바꾼다(090 표·수집기는 그대로 — 원본 값은 계속 쌓는다):
--   · `click_rate_pct` 를 뺀다. 사람 수 분모가 없는 비율은 쓰지 않는다.
--   · `link_clicks` = 피드백 앵커를 뺀 링크별 클릭 합, `brief_link_clicks` = 그 호 브리프 본 링크
--     (`/briefs/<날짜>/` 로 끝나는 링크) 클릭 — 가장 읽을 만한 수다.
--   · `scanner_suspect` = 같은 0 아닌 클릭 수를 가진 링크가 4개 이상(9/7 호: 7개 중 6개가 3회씩)
--     — 참이면 그 호의 링크 클릭·피드백에 검사기 몫이 섞였다고 읽는다.
--   · `brevo_totals_inflated` = Brevo 고유 클릭이 도달보다 크다. **링크별 수와는 별개**다 — 9/21·9/14·
--     8/31 호는 총계만 부풀었고 링크별로는 브리프 본 링크 6·5·1 만 눌렸다. 둘을 한 표지로 합치면
--     깨끗한 링크 수까지 의심하게 된다.
--   · `fb_net` = 👍 − 👎. 검사기는 두 링크를 똑같이 누르므로 **차이**가 사람의 의견에 가깝다.
-- 아직 이 함수를 읽는 화면·리포트가 없어(090 과 같은 날 도입) 반환 모양을 바꿔도 깨지는 호출이 없다.
-- 시그니처(integer)는 그대로라 권한도 그대로 유지된다.

create or replace function public.newsletter_campaigns_report(p_limit integer default 8)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with c as (
    select s.*,
           coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                      where v.key ~ '#fb-up$'), 0) as fb_up,
           coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                      where v.key ~ '#fb-down$'), 0) as fb_down,
           exists (select 1 from jsonb_each_text(s.links) v where v.key ~ '#fb-(up|down)$') as fb_offered,
           coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                      where v.key !~ '#fb-(up|down)$'), 0) as link_clicks,
           coalesce((select sum((v.value)::numeric)::int from jsonb_each_text(s.links) v
                      where v.key ~ '/briefs/[0-9]{4}-[0-9]{2}-[0-9]{2}/$'), 0) as brief_link_clicks,
           coalesce((select max(g.cnt) >= 4
                       from (select v.value, count(*) as cnt from jsonb_each_text(s.links) v
                              where v.key !~ '#fb-(up|down)$' and (v.value)::numeric > 0
                              group by v.value) g), false) as uniform_links
      from public.newsletter_campaign_stats s
     order by s.sent_at desc nulls last, s.campaign_id desc
     limit least(greatest(coalesce(p_limit, 8), 1), 52))
  select jsonb_build_object(
    'campaigns', coalesce((select jsonb_agg(jsonb_build_object(
        'campaign_id', campaign_id, 'name', name, 'kind', kind, 'publish_date', publish_date,
        'sent_at_kst', to_char(sent_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
        'sent', sent, 'delivered', delivered, 'unique_views', unique_views,
        'brief_link_clicks', brief_link_clicks, 'link_clicks', link_clicks,
        'brevo_unique_clicks', unique_clicks, 'brevo_clicks', clickers,
        'scanner_suspect', uniform_links,
        'brevo_totals_inflated', coalesce(unique_clicks, 0) > coalesce(delivered, 0) and coalesce(delivered, 0) > 0,
        'unsubscriptions', unsubscriptions, 'hard_bounces', hard_bounces, 'soft_bounces', soft_bounces,
        'fb_offered', fb_offered, 'fb_up', fb_up, 'fb_down', fb_down, 'fb_net', fb_up - fb_down,
        'captured_at_kst', to_char(captured_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
        order by sent_at desc nulls last, campaign_id desc) from c), '[]'::jsonb),
    'latest_capture_kst', (select to_char(max(captured_at) at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI')
                             from public.newsletter_campaign_stats),
    'basis', 'Brevo 의 uniqueClicks·clickers 는 사람 수가 아니다(도달보다 클 수 있다) — 비율로 쓰지 않는다. 읽을 수는 brief_link_clicks(브리프 본 링크 클릭)다. scanner_suspect=true 인 호는 회사 메일 보안 검사기가 링크를 전부 연 흔적이 있어 링크 클릭이 부풀어 있다. brevo_totals_inflated 는 Brevo 총계만의 문제다. 1클릭 피드백은 검사기가 두 링크를 똑같이 누르므로 fb_net(👍−👎)으로 읽는다. 열람(unique_views)은 Apple 메일 개인정보 보호가 부풀린다.'
  );
$$;
