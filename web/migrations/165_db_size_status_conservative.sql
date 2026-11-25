-- 165 DB 크기 감시 보정 — 한도를 보수 기준(500,000,000 B · DB 전체 합)으로 세고, 7일 하루 증가의 중앙값을 더 낸다.
-- Max local web/migrations prefix was 161 on main(89c6dcbc). 162~165 는 같은 PR(DB 용량 3단계). Additive only —
-- ops_db_size_daily 에 null 허용 열 하나(used_bytes) 추가, grm_db_size_record()·grm_db_size_status() 본문 교체.
-- 두 함수의 시그니처·반환형(jsonb)·security·search_path·권한 그대로. status 의 종전 키는 전부 남기고 키만 더한다.
-- 설계: _ops/reports/GRM_DB용량_확보방안_2026-11-20.md §1·§5-4.
--
-- ★왜(2026-11-20 03:20 UTC 실측, 읽기 전용 SELECT):
--   Supabase 문서("Understanding Database and Disk Size"): Free 는 database size 500MB 를 넘으면 read-only.
--   문서가 주는 SQL 은 sum(pg_database_size(datname)) from pg_database — template0·template1 도 들어간다.
--   지금 postgres 374,213,779 B + template1 7,752,851 + template0 7,520,783 = 389,487,413 B.
--   154 의 status 는 limit 524,288,000(=500MiB)·postgres DB 만 셌다 → 낙관 쪽으로 약 40MB 어긋난다
--   (1단계 전 기준: 낙관 79% vs 보수 85.9%). 문서에 MB 단위가 없으므로 보수 쪽(10^6, DB 전체 합)으로 센다.
--   또 7일 평균 증가가 일회성 적재(11-16 임베딩 +21MB)에 끌려가 한도 도달일을 왜곡했다 → 중앙값을 함께 낸다.
--
-- ★무엇을:
--   1) ops_db_size_daily.used_bytes bigint null — 그날 DB 전체 합(문서 SQL). 옛 행은 null(소급 불가).
--   2) grm_db_size_record(): used_bytes 도 기록(반환 jsonb 에 'used_bytes' 가산). db_bytes(postgres DB)는 그대로 —
--      추세 비교의 연속성을 지킨다.
--   3) grm_db_size_status():
--      · limit_bytes 524,288,000 → 500,000,000 (값만 바뀜, 키·형 그대로)
--      · used_ratio = used_bytes / limit_bytes (종전 db_bytes / 524,288,000)
--      · 가산 키: used_bytes(지금 시점 DB 전체 합, 실시간), db_bytes_live(지금 postgres DB),
--        limit_basis('sum(pg_database_size) over pg_database / 10^6 bytes'), warn_ratio(0.85),
--        growth_7d_median_per_day(최근 7일 연속 기록 사이 하루 증가량의 중앙값 — 기록 간격이 하루 넘으면 일수로 나눔)
--      · 종전 키 day·db_bytes·rows·growth_7d/28d/all_per_day·recorded_at·top_tables 는 그대로.
--   db_size_health.py 가 used_bytes·growth_7d_median_per_day 를 우선 읽고(없으면 종전 키), 경보 기준을 85% 로 맞춘다
--   (같은 PR). 85% = 425MB.
--
-- ★검증법(적용 뒤):
--   select jsonb_pretty(public.grm_db_size_status());
--     → limit_bytes 500000000, used_bytes ≈ select sum(pg_database_size(datname)) from pg_database,
--       used_ratio = used_bytes/5e8, growth_7d_median_per_day 숫자(기록 2행 이상)
--   select public.grm_db_size_record();  -- 오늘 행 덮어쓰기(멱등), 반환에 used_bytes
--   select day, db_bytes, used_bytes from public.ops_db_size_daily order by day desc limit 2;
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name like 'grm_db_size%' order by 1, 2;  -- anon·authenticated 행 없음(154 와 같음)
--
-- ★되돌리기: 154 파일의 두 함수 정의(create or replace … $$; + revoke/grant)를 다시 실행한다.
--   열은 남겨도 무해하다(지우려면 alter table public.ops_db_size_daily drop column if exists used_bytes;).

alter table public.ops_db_size_daily add column if not exists used_bytes bigint check (used_bytes is null or used_bytes >= 0);
comment on column public.ops_db_size_daily.used_bytes is
  '165: 그날 sum(pg_database_size) over pg_database(template 포함) — Supabase 문서의 database size 기준(보수).';

create or replace function public.grm_db_size_record()
returns jsonb
language plpgsql security invoker set search_path to 'public', 'pg_catalog' as $$
declare
  v_bytes bigint := pg_database_size(current_database());
  v_used  bigint := (select sum(pg_database_size(d.datname))::bigint from pg_database d);
  v_top   jsonb;
begin
  select coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'bytes', t.bytes) order by t.bytes desc), '[]'::jsonb)
    into v_top
    from (select n.nspname || '.' || c.relname as name, pg_total_relation_size(c.oid) as bytes
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where c.relkind = 'r' and n.nspname not in ('pg_catalog', 'information_schema')
           order by pg_total_relation_size(c.oid) desc
           limit 10) t;
  insert into public.ops_db_size_daily (day, db_bytes, used_bytes, top_tables, recorded_at)
  values ((now() at time zone 'utc')::date, v_bytes, v_used, v_top, now())
  on conflict (day) do update set db_bytes = excluded.db_bytes, used_bytes = excluded.used_bytes,
                                  top_tables = excluded.top_tables, recorded_at = excluded.recorded_at;
  return jsonb_build_object('day', (now() at time zone 'utc')::date, 'db_bytes', v_bytes, 'used_bytes', v_used);
end;
$$;
revoke execute on function public.grm_db_size_record() from public, anon, authenticated;

-- 하루 증가량(평균)은 기간 양 끝 행의 차이 ÷ 일수(154 그대로). 중앙값은 최근 7일 안 연속 기록 쌍의 하루 증가량.
create or replace function public.grm_db_size_status()
returns jsonb
language sql stable security definer set search_path to 'public' as $$
  with cur as (select day, db_bytes from public.ops_db_size_daily order by day desc limit 1),
  lim as (select 500000000::bigint as b),
  used as (select sum(pg_database_size(d.datname))::bigint as b from pg_database d),
  g7 as (select (select db_bytes from cur) - o.db_bytes as d, (select day from cur) - o.day as n
           from public.ops_db_size_daily o
          where o.day <= (select day from cur) - 7 order by o.day desc limit 1),
  g28 as (select (select db_bytes from cur) - o.db_bytes as d, (select day from cur) - o.day as n
            from public.ops_db_size_daily o
           where o.day <= (select day from cur) - 28 order by o.day desc limit 1),
  first_row as (select day, db_bytes from public.ops_db_size_daily order by day asc limit 1),
  gall as (select (select db_bytes from cur) - f.db_bytes as d, (select day from cur) - f.day as n from first_row f),
  steps as (select (o.db_bytes - lag(o.db_bytes) over w)::numeric / nullif(o.day - lag(o.day) over w, 0) as per_day
              from public.ops_db_size_daily o
             where o.day >= (select day from cur) - 7
            window w as (order by o.day)),
  med as (select percentile_cont(0.5) within group (order by per_day) as m from steps where per_day is not null)
  select jsonb_build_object(
    'limit_bytes',       (select b from lim),
    'limit_basis',       'sum(pg_database_size) over pg_database / 10^6 bytes',
    'warn_ratio',        0.85,
    'day',               (select day from cur),
    'db_bytes',          (select db_bytes from cur),
    'db_bytes_live',     pg_database_size(current_database()),
    'used_bytes',        (select b from used),
    'used_ratio',        round((select b from used)::numeric / (select b from lim), 4),
    'rows',              (select count(*) from public.ops_db_size_daily),
    'growth_7d_per_day', (select case when n > 0 then round(d::numeric / n) end from g7),
    'growth_7d_median_per_day', (select round(m::numeric) from med),
    'growth_28d_per_day',(select case when n > 0 then round(d::numeric / n) end from g28),
    'growth_all_per_day',(select case when n > 0 then round(d::numeric / n) end from gall),
    'recorded_at',       (select max(recorded_at) from public.ops_db_size_daily),
    'top_tables',        (select top_tables from public.ops_db_size_daily order by day desc limit 1));
$$;
revoke execute on function public.grm_db_size_status() from public, anon, authenticated;
grant execute on function public.grm_db_size_status() to service_role;
