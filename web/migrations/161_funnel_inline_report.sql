-- 161: 본문 속 줄형 구독 칸(160 inline_*) 판독 — 성장 일보가 따로 부르는 읽기 전용 함수.
-- [2026-11-17 구독 유입 대책] 160 이 inline_top/mid/end 키를 열었지만, 일보 숫자의 원천인
-- growth_daily_report(077)·growth_daily_v2(135)는 band_submit·cta_submit 두 키만 차분한다.
--
-- ★077·135 를 파일로 재정의하지 않는다 — 139 가 두 함수 본문을 DB 안에서 정규식으로 바꿔 썼다
--   (rum_* → rum_*_clean). 파일로 create or replace 하면 봇 거르기가 풀린다. 그래서 가산만 한다:
--   독립 함수 하나를 더하고 일보 렌더러(web/growth_daily.py)가 선택 입력으로 붙인다.
-- 읽기 전용(stable)이고 쓰기 없음. 무PII — 키·구역·경로·정수·시각만(060/076/084 와 같다).
-- 권한은 성장 일보 함수와 같은 대상(service_role)뿐.
--
-- 반환 jsonb:
--   report_date            기준일(기본 = 어제 KST, 077 과 같은 규칙)
--   snapshot_present       기준일 funnel_counts_daily(071) 스냅샷이 있나 — 없으면 일·주 값은 null
--   day / week / prev_week 줄형 칸 제출 합(071 일자 스냅샷 차분). 끝점 스냅샷이 없으면 null
--   slots                  자리별(top·mid·end) {day, week}
--   zones                  구역(076 경로 첫 조각) × 자리 누적 제출과 마지막 제출 시각(KST) — 076 은
--                          일자 스냅샷이 없어 '어제 어디서'는 마지막 제출 시각으로만 읽는다(084 와 같은 규칙)
--   paths                  경로(084) 상위 10 누적 제출과 마지막 제출 시각
-- 키가 아직 한 번도 쌓이지 않았으면 그 키는 0 으로 센다(스냅샷이 있는 날의 '없음' = 0).

create or replace function public.funnel_inline_report(p_date date default null)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with p as (
    select coalesce(p_date, (now() at time zone 'Asia/Seoul')::date - 1) as d
  ),
  k(key, slot) as (
    values ('inline_top_submit', 'top'), ('inline_mid_submit', 'mid'), ('inline_end_submit', 'end')
  ),
  -- 날짜별 스냅샷 존재(아무 키든 그날 행이 있으면 스냅샷이 돈 날이다)
  snap_days as (
    select distinct snap_date from public.funnel_counts_daily
  ),
  -- 날짜 × 자리 누적(스냅샷이 돈 날에 키 행이 없으면 0)
  cum as (
    select s.snap_date, k.slot, coalesce(f.total, 0) as total
      from snap_days s
      cross join k
      left join public.funnel_counts_daily f
        on f.snap_date = s.snap_date and f.key = k.key
  ),
  -- 끝점 누적 — 스냅샷이 없는 날이면 null
  ends as (
    select k.slot,
           (select c.total from cum c, p where c.slot = k.slot and c.snap_date = p.d)      as t0,
           (select c.total from cum c, p where c.slot = k.slot and c.snap_date = p.d - 1)  as t1,
           (select c.total from cum c, p where c.slot = k.slot and c.snap_date = p.d - 7)  as t7,
           (select c.total from cum c, p where c.slot = k.slot and c.snap_date = p.d - 14) as t14
      from k
  )
  select jsonb_build_object(
    'version', 1,
    'report_date', (select d from p),
    'snapshot_present', exists (select 1 from snap_days, p where snap_date = p.d),
    'day',       (select case when bool_and(t0 is not null and t1 is not null) then sum(t0 - t1)::int end from ends),
    'week',      (select case when bool_and(t0 is not null and t7 is not null) then sum(t0 - t7)::int end from ends),
    'prev_week', (select case when bool_and(t7 is not null and t14 is not null) then sum(t7 - t14)::int end from ends),
    'slots', (select jsonb_object_agg(slot, jsonb_build_object(
                       'day',  case when t0 is not null and t1 is not null then t0 - t1 end,
                       'week', case when t0 is not null and t7 is not null then t0 - t7 end))
                from ends),
    'zones', coalesce((
      select jsonb_agg(jsonb_build_object(
               'zone', z.zone, 'slot', k.slot, 'total', z.total,
               'last_at_kst', to_char(z.updated_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
             order by z.total desc, z.zone, k.slot)
        from public.funnel_zone_counts z
        join k on k.key = z.key), '[]'::jsonb),
    'paths', coalesce((
      select jsonb_agg(jsonb_build_object(
               'path', t.path, 'slot', t.slot, 'total', t.total, 'last_at_kst', t.last_at_kst)
             order by t.total desc, t.path)
        from (select fp.path, k.slot, fp.total,
                     to_char(fp.updated_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') as last_at_kst
                from public.funnel_path_counts fp
                join k on k.key = fp.key
               order by fp.total desc, fp.path
               limit 10) t), '[]'::jsonb)
  );
$$;

revoke all on function public.funnel_inline_report(date) from public, anon, authenticated;
grant execute on function public.funnel_inline_report(date) to service_role;

comment on function public.funnel_inline_report(date) is
  '161: 본문 속 줄형 구독 칸(inline_top/mid/end) 제출 판독 — 일·주(071 스냅샷 차분)·자리별·구역별·경로 상위. 읽기 전용, service_role 만.';
