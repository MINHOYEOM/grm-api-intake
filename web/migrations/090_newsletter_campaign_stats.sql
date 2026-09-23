-- 090: 뉴스레터 캠페인 지표 일별 적재 + 1클릭 피드백 판독 — 마케팅 계획 N-04·N-05 (2026-09-23).
--
-- 계기: 뉴스레터가 실제로 읽히는지(열람·클릭·해지)를 볼 곳이 Brevo 화면뿐이었다. 09-01 포맷
-- 실험(#869)도 "Brevo 캠페인 통계로 판정"하기로 해 두고 사람이 화면을 열어야만 판정할 수
-- 있었다. 매일 한 번 Brevo 캠페인 통계를 이 표로 옮긴다(`collect_newsletter_campaigns.py`,
-- grm-rum-analytics.yml 의 한 스텝 — 077 구독자 스냅샷과 같은 자리).
--
-- ★1클릭 피드백(N-04)은 **새 수집 경로를 만들지 않는다.** 메일 하단의 "유용했어요 / 아쉬웠어요"
--   는 같은 브리프 페이지의 서로 다른 앵커(`#fb-up` / `#fb-down`)로 가는 두 링크이고, Brevo 가
--   링크마다 클릭 수(linksStats)를 이미 센다. 그래서 여기서는 URL 끝의 앵커로 두 수를 골라내기만
--   한다. 쿼리 파라미터가 아니라 앵커라 발송 게이트(gate_provenance)도 그대로 통과한다.
-- ★무PII — 캠페인 단위 합계와 링크별 클릭 수만 저장한다. 수신자·클릭한 사람은 싣지 않는다.
-- ★같은 캠페인을 매일 다시 받는다(열람·클릭은 발송 후 며칠 동안 늘어난다) — upsert 로 덮고
--   captured_at 을 갱신한다. 값이 줄어드는 일은 없어야 정상이다(Brevo 가 과거 집계를 고치는
--   경우 그대로 따른다 — 여기서 래칫을 걸지 않는다).
-- ★오픈 수는 Apple 메일 개인정보 보호(MPP)가 부풀리므로 판정에는 클릭을 쓴다. 표에는 둘 다
--   남기되 판독 함수는 클릭을 앞세운다.
--
-- 읽기는 authenticated, 쓰기는 service_role(워크플로) — 072/077 과 같은 규칙.

create table if not exists public.newsletter_campaign_stats (
  campaign_id bigint primary key,
  name text not null,
  kind text not null check (kind in ('weekly', 'announce', 'other')),
  publish_date date,                         -- 주간 호의 발행일(캠페인명에서 파싱). 공지·기타는 null
  sent_at timestamptz,
  sent integer check (sent >= 0),
  delivered integer check (delivered >= 0),
  unique_views integer check (unique_views >= 0),
  unique_clicks integer check (unique_clicks >= 0),
  clickers integer check (clickers >= 0),
  unsubscriptions integer check (unsubscriptions >= 0),
  hard_bounces integer check (hard_bounces >= 0),
  soft_bounces integer check (soft_bounces >= 0),
  links jsonb not null default '{}'::jsonb,  -- {"<url>": <clicks>} — Brevo linksStats 그대로
  captured_at timestamptz not null default now()
);

comment on column public.newsletter_campaign_stats.links is
  'Brevo linksStats — URL 별 클릭 수. 1클릭 피드백은 URL 이 #fb-up / #fb-down 으로 끝나는 링크의 클릭 수다.';

alter table public.newsletter_campaign_stats enable row level security;
revoke all on public.newsletter_campaign_stats from public, anon, authenticated;
grant select on public.newsletter_campaign_stats to authenticated;
drop policy if exists "signed-in can read newsletter campaign stats" on public.newsletter_campaign_stats;
create policy "signed-in can read newsletter campaign stats"
on public.newsletter_campaign_stats for select to authenticated using (true);

-- 판독 — 최근 캠페인 N 개(발송 시각 역순)와 1클릭 피드백 수. 주간 성장 리포트(089 소비자)와
-- /admin 이 읽는다. 링크 URL 은 반환하지 않는다(피드백 두 수와 합계만) — 링크 목록은 표에서 직접.
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
           exists (select 1 from jsonb_each_text(s.links) v where v.key ~ '#fb-(up|down)$') as fb_offered
      from public.newsletter_campaign_stats s
     order by s.sent_at desc nulls last, s.campaign_id desc
     limit least(greatest(coalesce(p_limit, 8), 1), 52))
  select jsonb_build_object(
    'campaigns', coalesce((select jsonb_agg(jsonb_build_object(
        'campaign_id', campaign_id, 'name', name, 'kind', kind, 'publish_date', publish_date,
        'sent_at_kst', to_char(sent_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'),
        'sent', sent, 'delivered', delivered, 'unique_views', unique_views,
        'unique_clicks', unique_clicks, 'clickers', clickers,
        'click_rate_pct', case when coalesce(delivered, 0) > 0 then round(100.0 * clickers / delivered, 1) end,
        'unsubscriptions', unsubscriptions, 'hard_bounces', hard_bounces, 'soft_bounces', soft_bounces,
        'fb_offered', fb_offered, 'fb_up', fb_up, 'fb_down', fb_down,
        'captured_at_kst', to_char(captured_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
        order by sent_at desc nulls last, campaign_id desc) from c), '[]'::jsonb),
    'latest_capture_kst', (select to_char(max(captured_at) at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI')
                             from public.newsletter_campaign_stats),
    'basis', '열람(unique_views)은 Apple 메일 개인정보 보호가 부풀린다 — 판정은 클릭(clickers/delivered)으로. 1클릭 피드백은 #fb-up/#fb-down 링크의 클릭 수(fb_offered=false 인 호는 피드백 링크가 없던 호).'
  );
$$;

revoke all on function public.newsletter_campaigns_report(integer) from public, anon, authenticated;
grant execute on function public.newsletter_campaigns_report(integer) to authenticated, service_role;
