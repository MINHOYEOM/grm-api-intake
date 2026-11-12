-- 153 ops_db_size_daily — DB 크기를 하루 한 번 기록해, 무료 플랜 한도(500MB)에 언제 닿을지 미리 본다.
-- Max local web/migrations prefix was 152 on main. Additive only — 기존 표·함수 무변경.
--
-- ★왜(2026-11-12 실측): DB 366MB / 무료 한도 500MB(73%). 한도를 넘으면 DB 가 읽기 전용이 돼 수집이 멈춘다.
--   지금은 크기를 기록하는 곳이 없어 "한 달에 얼마나 느는지"를 모른다 → 유료 전환 시점을 판단할 근거가 없다.
--
-- ★무엇을:
--   1) 표 ops_db_size_daily(day, db_bytes, top_tables) — 하루 한 행. 클라이언트 권한 0(RLS on·정책 없음).
--   2) 기록 함수 grm_db_size_record() — pg_cron 매일 00:05 UTC. 같은 날 다시 부르면 덮어쓴다(멱등).
--   3) 상태 함수 grm_db_size_status() — service_role 만. 한도·현재·7일/28일 하루 증가량·한도까지 남은 일수.
--      db_size_health.py(GitHub Actions 주 1회)가 읽고 80% 이상이거나 한도까지 60일 미만이면 이슈를 연다.
--
-- 적용 후 확인:
--   select public.grm_db_size_record();                     -- 첫 행
--   select jsonb_pretty(public.grm_db_size_status());
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name like 'grm_db_size%' order by 1, 2;  -- anon·authenticated 행 없음
--   select grantee from information_schema.role_table_grants where table_name = 'ops_db_size_daily'
--     and grantee in ('anon', 'authenticated');              -- 0행
-- 되돌리기:
--   select cron.unschedule('grm-db-size-daily');
--   drop function public.grm_db_size_status();
--   drop function public.grm_db_size_record();
--   drop table public.ops_db_size_daily;

create table if not exists public.ops_db_size_daily (
  day         date        primary key,
  db_bytes    bigint      not null check (db_bytes >= 0),
  top_tables  jsonb       not null default '[]'::jsonb,   -- 큰 표 10개 {name, bytes}
  recorded_at timestamptz not null default now()
);
comment on table public.ops_db_size_daily is
  '153: DB 크기 일일 기록(무료 한도 500MB 대비 증가 추세). grm_db_size_record()(pg_cron) 가 쓰고 grm_db_size_status()(service_role) 가 읽는다.';
alter table public.ops_db_size_daily enable row level security;
revoke all on table public.ops_db_size_daily from public, anon, authenticated;

create or replace function public.grm_db_size_record()
returns jsonb
language plpgsql security invoker set search_path to 'public', 'pg_catalog' as $$
declare
  v_bytes bigint := pg_database_size(current_database());
  v_top   jsonb;
begin
  select coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'bytes', t.bytes) order by t.bytes desc), '[]'::jsonb)
    into v_top
    from (select n.nspname || '.' || c.relname as name, pg_total_relation_size(c.oid) as bytes
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where c.relkind = 'r' and n.nspname not in ('pg_catalog', 'information_schema')
           order by pg_total_relation_size(c.oid) desc
           limit 10) t;
  insert into public.ops_db_size_daily (day, db_bytes, top_tables, recorded_at)
  values ((now() at time zone 'utc')::date, v_bytes, v_top, now())
  on conflict (day) do update set db_bytes = excluded.db_bytes, top_tables = excluded.top_tables,
                                  recorded_at = excluded.recorded_at;
  return jsonb_build_object('day', (now() at time zone 'utc')::date, 'db_bytes', v_bytes);
end;
$$;
revoke execute on function public.grm_db_size_record() from public, anon, authenticated;

-- 하루 증가량은 기간 양 끝 행의 차이 ÷ 일수(기록이 하루 이상 빠져도 맞다). 기록이 2행 미만이면 null.
create or replace function public.grm_db_size_status()
returns jsonb
language sql stable security definer set search_path to 'public' as $$
  with cur as (select day, db_bytes from public.ops_db_size_daily order by day desc limit 1),
  g7 as (select (select db_bytes from cur) - o.db_bytes as d, (select day from cur) - o.day as n
           from public.ops_db_size_daily o
          where o.day <= (select day from cur) - 7 order by o.day desc limit 1),
  g28 as (select (select db_bytes from cur) - o.db_bytes as d, (select day from cur) - o.day as n
            from public.ops_db_size_daily o
           where o.day <= (select day from cur) - 28 order by o.day desc limit 1),
  first_row as (select day, db_bytes from public.ops_db_size_daily order by day asc limit 1),
  gall as (select (select db_bytes from cur) - f.db_bytes as d, (select day from cur) - f.day as n from first_row f)
  select jsonb_build_object(
    'limit_bytes',       524288000,
    'day',               (select day from cur),
    'db_bytes',          (select db_bytes from cur),
    'used_ratio',        round((select db_bytes from cur)::numeric / 524288000, 4),
    'rows',              (select count(*) from public.ops_db_size_daily),
    'growth_7d_per_day', (select case when n > 0 then round(d::numeric / n) end from g7),
    'growth_28d_per_day',(select case when n > 0 then round(d::numeric / n) end from g28),
    'growth_all_per_day',(select case when n > 0 then round(d::numeric / n) end from gall),
    'recorded_at',       (select max(recorded_at) from public.ops_db_size_daily),
    'top_tables',        (select top_tables from public.ops_db_size_daily order by day desc limit 1));
$$;
revoke execute on function public.grm_db_size_status() from public, anon, authenticated;
grant execute on function public.grm_db_size_status() to service_role;

create extension if not exists pg_cron;
select cron.schedule('grm-db-size-daily', '5 0 * * *', $c$select public.grm_db_size_record()$c$);
