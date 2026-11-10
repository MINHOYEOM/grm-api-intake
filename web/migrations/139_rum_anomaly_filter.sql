-- 139: 방문 통계(RUM)에서 자동 접속 급증을 걸러 낸다 — 날짜·경로 단위 판정 + "_clean" 보기 + 보고 함수 연결.
--
-- 계기(2026-11-10): 11-01 하루 방문 4,085건(평소 28일 중앙값 57)이 걸러지지 않아 "독자 1위는
-- 업체 페이지(35%)"라는 잘못된 결론이 나왔다. 그날 /findings/firm/ 877건 + /en/findings/firm/ 417건,
-- 전부 직접 유입·데스크톱 100%·한국 0건·나라 20곳(BD·PK·SG·AR …) — 주거용 프록시를 도는 수집 봇의
-- 전형이다. Cloudflare 의 bot:0 필터(072·077)는 이것을 사람으로 셌다.
--
-- ★왜 DB(집계 단계)인가: Cloudflare 는 방문자 한 명씩이 아니라 날짜×차원 합계만 준다(072~083).
--   방문자 단위 신호(UA·IP)는 우리 손에 없다. 그래서 "평소 대비 몇 배"라는 합계 수준 판정을 DB 에
--   두고, 모든 소비자가 같은 판정을 보게 한다. 수집기에서 거르면 지난 데이터(11-01)를 다시 볼 수
--   없고, 보고 스크립트마다 거르면 기준이 갈라진다(growth_daily.py 의 SPIKE 규칙이 그 예 — 렌더
--   에서만 걸러 경로·구역·전환율은 오염된 채였다).
--
-- ★원본 표는 건드리지 않는다. 판정은 보기(view)로 매번 계산한다 — 기준을 바꾸면 지난 날짜도 즉시
--   같은 기준으로 다시 보인다. 사람이 판정을 뒤집을 자리는 rum_anomaly_overrides 한 곳이다.
--
-- ■ 날짜 규칙(rum_anomaly_days): 그날 방문 ≥ 200 이고 ≥ 직전 28일 중앙값의 8배(직전 데이터 ≥ 7일).
--   실측(08-10~11-09, 92일): 11-01 = 72.3배. 그 밖의 최대는 11-03 3.2배(뉴스레터 발송 다음 날 —
--   한국 81%·브리프 경로, 진짜 독자). 8배는 정상 최대의 2.5배 여유, 11-01 의 9분의 1 이라 양쪽
--   모두 넉넉하다. 판정된 날은 _clean 보기에서 **하루 통째로 빠진다**(그날의 진짜 방문 수십 건도
--   함께 빠진다 — 봇과 사람을 합계에서 가를 수 없어서다. 보고는 "제외일"로 표시한다).
-- ■ 경로 규칙(rum_anomaly_paths): 날짜 규칙에 안 걸린 날, 한 경로가 ≥ 100 방문이고 ≥ 그 경로의
--   직전 28일 중앙값(최소 1)의 20배. 하루 전체는 평범한데 한 쪽만 두들긴 경우를 잡는다. 그 경로는
--   평소 값(중앙값 올림)으로 깎고, 깎은 만큼을 그날 방문 합계에서도 뺀다. 실측 92일 중 이 규칙에
--   걸리는 날은 0(정상 최대는 09-23 /findings/ 40).
--
-- ■ 보고 함수 연결 — 077 growth_daily_report · 089 growth_weekly_report · 135 growth_daily_v2 ·
--   087 funnel_zone_report 의 원본 표 참조를 _clean 보기로 바꾼다. 운영 DB 의 **현재 정의**를
--   읽어 표 이름만 바꿔 다시 만든다(저장소 파일과 운영 정의가 이미 갈라져 있다 — 실측 087
--   funnel_zone_report 에 visits_latest_date 가 운영에만 있다). 반환 모양은 그대로다.
--   예외 — 077 의 data_quality 블록(rum_present_on_date·rum_latest_date 등)은 원본을 계속 본다.
--   "적재됐나"는 걸러 낸 뒤가 아니라 적재 사실이어야 하므로.
--   가산 — 135 growth_daily_v2 에 'rum_anomalies' 키 하나(직전 14일 제외 판정 목록)를 더한다.
--
-- 권한: 보기는 security_invoker — 원본 표의 RLS·권한을 그대로 따른다(authenticated 읽기,
-- rum_referrer_path_daily 는 service_role 만). overrides 는 authenticated 읽기·service_role 쓰기.
-- 비용 0: 보기 계산은 하루 1행·경로 ≥100 행만 상관 질의를 돈다(92일 기준 수 ms).

-- ---------------------------------------------------------------------------
-- 1. 사람이 판정을 뒤집는 자리
-- ---------------------------------------------------------------------------
create table if not exists public.rum_anomaly_overrides (
  snap_date date primary key,
  excluded boolean not null,
  note text not null check (length(note) > 0),
  created_at timestamptz not null default now()
);
comment on table public.rum_anomaly_overrides is
  '139 — RUM 제외일 수동 판정. excluded=true 면 규칙과 무관하게 제외, false 면 규칙이 걸어도 포함. note 에 이유.';

alter table public.rum_anomaly_overrides enable row level security;
revoke all on public.rum_anomaly_overrides from public, anon, authenticated;
grant select on public.rum_anomaly_overrides to authenticated;
grant all on public.rum_anomaly_overrides to service_role;
drop policy if exists "signed-in can read rum anomaly overrides" on public.rum_anomaly_overrides;
create policy "signed-in can read rum anomaly overrides"
on public.rum_anomaly_overrides for select to authenticated using (true);

-- ---------------------------------------------------------------------------
-- 2. 판정 보기
-- ---------------------------------------------------------------------------
create or replace view public.rum_anomaly_days with (security_invoker = true) as
with d as (
  select snap_date, value as visits from public.rum_daily where metric = 'visits'),
b as (
  select d.snap_date, d.visits,
         (select percentile_cont(0.5) within group (order by d2.visits)
            from d d2 where d2.snap_date between d.snap_date - 28 and d.snap_date - 1)::numeric as baseline,
         (select count(*) from d d2 where d2.snap_date between d.snap_date - 28 and d.snap_date - 1)::int as baseline_days
    from d),
a as (
  select b.*, (b.visits >= 200 and b.baseline_days >= 7 and b.visits >= 8 * b.baseline) as hit from b)
select coalesce(a.snap_date, o.snap_date) as snap_date,
       a.visits,
       a.baseline,
       a.baseline_days,
       round(a.visits / nullif(a.baseline, 0), 1) as ratio,
       case when o.snap_date is not null then 'manual' else 'auto' end as source,
       coalesce(o.note, '방문이 직전 28일 중앙값의 8배 이상이고 200건 이상(139 날짜 규칙)') as reason
  from a full join public.rum_anomaly_overrides o on o.snap_date = a.snap_date
 where coalesce(o.excluded, a.hit);

comment on view public.rum_anomaly_days is
  '139 — 자동 접속 급증으로 통계에서 하루 통째로 빼는 날. 규칙: 방문 ≥200 이고 ≥ 직전 28일 중앙값×8. rum_anomaly_overrides 가 우선.';

create or replace view public.rum_anomaly_paths with (security_invoker = true) as
with p as (
  select snap_date, request_path, visits from public.rum_path_daily),
c as (
  select p.snap_date, p.request_path, p.visits,
         greatest(ceil(coalesce((select percentile_cont(0.5) within group (order by p2.visits)
                                   from p p2
                                  where p2.request_path = p.request_path
                                    and p2.snap_date between p.snap_date - 28 and p.snap_date - 1), 0)), 1)::int as baseline
    from p
   where p.visits >= 100)
select c.snap_date, c.request_path, c.visits, c.baseline, (c.visits - c.baseline) as excess
  from c
 where c.visits >= 20 * c.baseline
   and not exists (select 1 from public.rum_anomaly_days a where a.snap_date = c.snap_date);

comment on view public.rum_anomaly_paths is
  '139 — 제외일이 아닌 날, 한 경로만 튄 경우(≥100 이고 ≥ 그 경로 직전 28일 중앙값×20). _clean 보기에서 baseline 으로 깎인다.';

-- ---------------------------------------------------------------------------
-- 3. 걸러 낸 보기 — 원본과 같은 열·같은 순서(소비자는 표 이름만 바꾸면 된다)
-- ---------------------------------------------------------------------------
create or replace view public.rum_daily_clean with (security_invoker = true) as
select r.snap_date, r.metric,
       greatest(r.value - coalesce(x.excess, 0), 0)::integer as value,
       r.sample_interval
  from public.rum_daily r
  left join (select snap_date, sum(excess)::integer as excess
               from public.rum_anomaly_paths group by snap_date) x on x.snap_date = r.snap_date
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

create or replace view public.rum_path_daily_clean with (security_invoker = true) as
select r.snap_date, r.request_path,
       coalesce(x.baseline, r.visits)::integer as visits,
       r.sample_interval
  from public.rum_path_daily r
  left join public.rum_anomaly_paths x on x.snap_date = r.snap_date and x.request_path = r.request_path
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

create or replace view public.rum_referrer_daily_clean with (security_invoker = true) as
select r.* from public.rum_referrer_daily r
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

create or replace view public.rum_country_daily_clean with (security_invoker = true) as
select r.* from public.rum_country_daily r
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

create or replace view public.rum_device_daily_clean with (security_invoker = true) as
select r.* from public.rum_device_daily r
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

create or replace view public.rum_referrer_path_daily_clean with (security_invoker = true) as
select r.* from public.rum_referrer_path_daily r
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

comment on view public.rum_daily_clean is
  '139 — rum_daily 에서 제외일을 빼고, 경로 급증분(rum_anomaly_paths.excess)을 뺀 값. 분석·보고는 이 보기를 쓴다.';
comment on view public.rum_path_daily_clean is
  '139 — rum_path_daily 에서 제외일을 빼고, 급증 경로는 평소 값으로 깎은 값. 분석·보고는 이 보기를 쓴다.';

revoke all on public.rum_anomaly_days, public.rum_anomaly_paths, public.rum_daily_clean,
              public.rum_path_daily_clean, public.rum_referrer_daily_clean,
              public.rum_country_daily_clean, public.rum_device_daily_clean,
              public.rum_referrer_path_daily_clean
  from public, anon, authenticated;
grant select on public.rum_anomaly_days, public.rum_anomaly_paths, public.rum_daily_clean,
                public.rum_path_daily_clean, public.rum_referrer_daily_clean,
                public.rum_country_daily_clean, public.rum_device_daily_clean
  to authenticated;
grant select on public.rum_anomaly_days, public.rum_anomaly_paths, public.rum_daily_clean,
                public.rum_path_daily_clean, public.rum_referrer_daily_clean,
                public.rum_country_daily_clean, public.rum_device_daily_clean,
                public.rum_referrer_path_daily_clean
  to service_role;

-- ---------------------------------------------------------------------------
-- 4. 보고용 요약 — 기간 안의 제외일·급증 경로 목록
-- ---------------------------------------------------------------------------
create or replace function public.rum_anomaly_summary(p_from date, p_to date)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select jsonb_build_object(
    'from', p_from, 'to', p_to,
    'excluded_days', coalesce((
      select jsonb_agg(jsonb_build_object('date', a.snap_date, 'visits', a.visits,
                                          'baseline', a.baseline, 'ratio', a.ratio,
                                          'source', a.source, 'reason', a.reason)
                       order by a.snap_date)
        from public.rum_anomaly_days a where a.snap_date between p_from and p_to), '[]'::jsonb),
    'capped_paths', coalesce((
      select jsonb_agg(jsonb_build_object('date', x.snap_date, 'path', x.request_path,
                                          'visits', x.visits, 'baseline', x.baseline, 'excess', x.excess)
                       order by x.snap_date, x.request_path)
        from public.rum_anomaly_paths x where x.snap_date between p_from and p_to), '[]'::jsonb),
    'basis', '방문 통계는 자동 접속 급증을 뺀 값이다(139). 제외일 = 방문 ≥200 이고 직전 28일 중앙값의 8배 이상 → 하루 통째로 뺀다. 급증 경로 = 한 쪽이 ≥100 이고 평소의 20배 이상 → 평소 값으로 깎는다. 원본은 rum_* 표에 그대로 있다.');
$$;
revoke all on function public.rum_anomaly_summary(date, date) from public, anon, authenticated;
grant execute on function public.rum_anomaly_summary(date, date) to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 5. 보고 함수 4개를 _clean 보기에 연결 — 운영 정의를 읽어 표 이름만 바꾼다.
--    각 함수에서 바뀐 참조 수를 검사하고, 바꾼 뒤 원본 참조가 남으면(077 data_quality 제외) 중단한다.
-- ---------------------------------------------------------------------------
do $mig$
declare
  re constant text := '\mpublic\.rum_(daily|path_daily|referrer_daily|country_daily|device_daily|referrer_path_daily)\M';
  f regprocedure;
  src text;
  head text;
  tail text;
  out_src text;
  cut int;
  expected int;
  got int;
begin
  foreach f in array array[
      'public.funnel_zone_report()'::regprocedure,
      'public.growth_weekly_report(date, boolean)'::regprocedure,
      'public.growth_daily_v2(date)'::regprocedure,
      'public.growth_daily_report(date)'::regprocedure] loop
    src := pg_get_functiondef(f);
    if f = 'public.growth_daily_report(date)'::regprocedure then
      -- data_quality 블록은 적재 사실을 말하므로 원본을 계속 본다.
      cut := strpos(src, '''rum_present_on_date''');
      if cut = 0 then
        raise exception '139: growth_daily_report 에서 data_quality 기준점을 찾지 못함';
      end if;
      head := left(src, cut - 1);
      tail := substr(src, cut);
    else
      head := src;
      tail := '';
    end if;
    expected := (select count(*) from regexp_matches(head, re, 'g'));
    if expected = 0 then
      raise exception '139: % 에 바꿀 RUM 참조가 없음(이미 적용됐거나 정의가 바뀜)', f;
    end if;
    out_src := regexp_replace(head, re, 'public.rum_\1_clean', 'g') || tail;
    got := (select count(*) from regexp_matches(out_src, '\mpublic\.rum_[a-z_]+_clean\M', 'g'));
    if got <> expected then
      raise exception '139: % 치환 수 불일치 (기대 % · 결과 %)', f, expected, got;
    end if;
    if f = 'public.growth_daily_v2(date)'::regprocedure then
      if (select count(*) from regexp_matches(out_src, '''version'', 2,', 'g')) <> 1 then
        raise exception '139: growth_daily_v2 의 version 기준점이 1개가 아님';
      end if;
      out_src := replace(out_src, '''version'', 2,',
                         '''version'', 2, ''rum_anomalies'', public.rum_anomaly_summary(d - 13, d),');
    end if;
    execute out_src;
    raise notice '139: % — RUM 참조 %곳을 _clean 으로', f, expected;
  end loop;
end
$mig$;
