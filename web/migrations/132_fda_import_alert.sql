-- ============================================================================
-- 132_fda_import_alert.sql — FDA 수입경보(Import Alert 66-40·66-41) Red List 스냅샷 + 등재·해제 이력
--
-- 무엇: collect_fda_import_alert.py 가 매일 한 번 원천 HTML 을 파싱해 넣는 경량 표 3개.
--   import_alert_listing  현재(및 과거) 등재 업체. 행 하나 = (경보 번호, 정규화 업체 키).
--                         active=false 는 직전 스냅샷에서 사라진 업체(해제 추정).
--   import_alert_event    등재·해제 이력. seed(이력 시작점) / listed / relisted / delisted / products_changed.
--   import_alert_snapshot 실행 한 번 = 경보 하나당 1행(원문 sha256·Last-Modified·업체 수) — 갱신 주기 실측용.
-- findings 스키마에 넣지 않는 이유: 위반 사유 서술이 없다(타당성 문서 §2.5). 목적이 다른 리스트형 데이터다.
-- ★해제일 필드가 원천에 없다 — delisted 는 "그날 스냅샷에 없었다"는 관측일 뿐이다. 수집을 건너뛴 날의
--   해제·재등재는 복원할 수 없다.
-- ★첫 실행(그 경보의 snapshot 행이 0개)은 전원을 'seed' 로만 남긴다. 'listed' 가 쏟아지지 않는다.
--
-- 가산만: 새 표 3개 + 함수 1개. 기존 표·함수·RPC 는 하나도 건드리지 않는다.
-- 권한: public·anon·authenticated 를 명시 회수(10-28 교훈: `revoke … from public` 만으로는 Supabase 가
--   anon·authenticated 에 직접 준 grant 가 안 지워진다). RLS 켜고 정책은 만들지 않는다 = 클라이언트 경로 없음.
--   읽기·쓰기는 service_role 만(RLS 우회). 공개 노출(웹·검색)은 비서가 따로 정한다(별도 migration).
--   함수는 security invoker(service_role 이 표 권한을 직접 가진다)·search_path 고정·service_role 에만 EXECUTE.
--
-- 적용: 비서가 SQL 검토 후 SQL 에디터에서 이 한 파일. 적용 후에야 repo 변수 ENABLE_FDA_IMPORT_ALERT=true.
-- 적용 후 확인:
--   select table_name, grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name like 'import\_alert\_%' order by 1,2,3;
--   → service_role(+postgres) 만. anon·authenticated·PUBLIC 행이 없어야 한다.
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name = 'import_alert_apply' order by 2;   → service_role(+postgres) 만.
--   select relname, relrowsecurity from pg_class where relname like 'import\_alert\_%';   → 전부 true
--
-- 되돌리기(이 표·함수만 · 기존 불변):
--   drop function if exists public.import_alert_apply(jsonb);
--   drop table if exists public.import_alert_event;
--   drop table if exists public.import_alert_listing;
--   drop table if exists public.import_alert_snapshot;
-- ============================================================================

create table if not exists public.import_alert_snapshot (
  id             bigint generated always as identity primary key,
  alert_no       text not null check (alert_no ~ '^[0-9]{2}-[0-9]{2}$'),
  run_date       date not null,
  sha256         text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  last_modified  text check (last_modified is null or char_length(last_modified) <= 80),
  page_published date,
  raw_blocks     integer not null check (raw_blocks >= 0),
  firms_unique   integer not null check (firms_unique >= 0),
  merged_blocks  integer not null default 0 check (merged_blocks >= 0),
  events         jsonb not null default '{}'::jsonb,
  schema_version text not null check (char_length(schema_version) between 1 and 60),
  created_at     timestamptz not null default now(),
  unique (alert_no, run_date)
);

create table if not exists public.import_alert_listing (
  alert_no       text not null check (alert_no ~ '^[0-9]{2}-[0-9]{2}$'),
  firm_key       text not null check (char_length(firm_key) between 7 and 400),
  firm_name      text not null check (char_length(firm_name) between 1 and 300),
  name_norm      text not null check (char_length(name_norm) between 1 and 300),
  country        text not null check (char_length(country) between 1 and 80),
  is_korea       boolean not null default false,
  address        text check (address is null or char_length(address) <= 600),
  addresses      jsonb not null default '[]'::jsonb,
  city           text check (city is null or char_length(city) <= 120),
  firm_published date,
  product_codes  text[] not null default '{}',
  products       jsonb not null default '[]'::jsonb,
  active         boolean not null default true,
  first_seen     date not null,
  last_seen      date not null,
  delisted_on    date,
  primary key (alert_no, firm_key),
  constraint import_alert_listing_seen_order check (last_seen >= first_seen),
  constraint import_alert_listing_delist check (active = (delisted_on is null))
);
create index if not exists import_alert_listing_country_idx
  on public.import_alert_listing (country, active);
create index if not exists import_alert_listing_published_idx
  on public.import_alert_listing (firm_published desc) where active;

create table if not exists public.import_alert_event (
  id             bigint generated always as identity primary key,
  alert_no       text not null check (alert_no ~ '^[0-9]{2}-[0-9]{2}$'),
  firm_key       text not null check (char_length(firm_key) between 7 and 400),
  event          text not null check (event in ('seed','listed','relisted','delisted','products_changed')),
  event_date     date not null,
  firm_name      text not null default '',
  country        text not null default '',
  firm_published date,
  detail         jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  -- 같은 날 다시 돌려도 이벤트가 두 번 생기지 않는다(멱등).
  unique (alert_no, firm_key, event, event_date)
);
create index if not exists import_alert_event_date_idx
  on public.import_alert_event (event_date desc, event);

alter table public.import_alert_snapshot enable row level security;
alter table public.import_alert_listing  enable row level security;
alter table public.import_alert_event    enable row level security;

revoke all on public.import_alert_snapshot from public, anon, authenticated;
revoke all on public.import_alert_listing  from public, anon, authenticated;
revoke all on public.import_alert_event    from public, anon, authenticated;
grant all on public.import_alert_snapshot to service_role;
grant all on public.import_alert_listing  to service_role;
grant all on public.import_alert_event    to service_role;

-- ── 적용: 수집기가 계산한 스냅샷·diff 를 한 트랜잭션으로 반영 ──────────────────────────────────
-- p_payload = {alert_no, run_date, snapshot{...}, listings[...], events[...]}
-- listings 는 그날 원천에 있는 전원이다. 거기 없는 active 행은 delisted 로 내린다(수집기 diff 와 같은 규칙).
-- 수집기가 구조 검증(하한·급감 가드)을 통과한 경우에만 부른다.
create or replace function public.import_alert_apply(p_payload jsonb)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_alert  text := p_payload->>'alert_no';
  v_date   date := (p_payload->>'run_date')::date;
  v_snap   jsonb := coalesce(p_payload->'snapshot', '{}'::jsonb);
  n_up     integer;
  n_down   integer;
  n_ev     integer;
begin
  if v_alert is null or v_date is null or jsonb_typeof(p_payload->'listings') <> 'array' then
    raise exception 'import_alert_apply: alert_no/run_date/listings 필수' using errcode = '22023';
  end if;

  with src as (
    select distinct on (r.firm_key) r.*
      from jsonb_to_recordset(p_payload->'listings') as r(
             firm_key text, firm_name text, name_norm text, country text, is_korea boolean,
             address text, addresses jsonb, city text, firm_published date,
             product_codes text[], products jsonb)
     order by r.firm_key
  ), up as (
    insert into public.import_alert_listing as t
      (alert_no, firm_key, firm_name, name_norm, country, is_korea, address, addresses, city,
       firm_published, product_codes, products, active, first_seen, last_seen, delisted_on)
    select v_alert, firm_key, firm_name, name_norm, country, coalesce(is_korea, false), address,
           coalesce(addresses, '[]'::jsonb), city, firm_published, coalesce(product_codes, '{}'),
           coalesce(products, '[]'::jsonb), true, v_date, v_date, null
      from src
    on conflict (alert_no, firm_key) do update
      set firm_name      = excluded.firm_name,
          name_norm      = excluded.name_norm,
          country        = excluded.country,
          is_korea       = excluded.is_korea,
          address        = excluded.address,
          addresses      = excluded.addresses,
          city           = excluded.city,
          firm_published = excluded.firm_published,
          product_codes  = excluded.product_codes,
          products       = excluded.products,
          active         = true,
          delisted_on    = null,
          last_seen      = greatest(t.last_seen, excluded.last_seen)
    returning 1
  )
  select count(*) into n_up from up;

  with down as (
    update public.import_alert_listing t
       set active = false, delisted_on = v_date
     where t.alert_no = v_alert and t.active
       and t.last_seen < v_date
       and not exists (
         select 1 from jsonb_to_recordset(p_payload->'listings') as r(firm_key text)
          where r.firm_key = t.firm_key)
    returning 1
  )
  select count(*) into n_down from down;

  with ev as (
    insert into public.import_alert_event
      (alert_no, firm_key, event, event_date, firm_name, country, firm_published, detail)
    select v_alert, e.firm_key, e.event, coalesce(e.event_date, v_date), coalesce(e.firm_name, ''),
           coalesce(e.country, ''), e.firm_published, coalesce(e.detail, '{}'::jsonb)
      from jsonb_to_recordset(coalesce(p_payload->'events', '[]'::jsonb)) as e(
             firm_key text, event text, event_date date, firm_name text, country text,
             firm_published date, detail jsonb)
    on conflict (alert_no, firm_key, event, event_date) do nothing
    returning 1
  )
  select count(*) into n_ev from ev;

  insert into public.import_alert_snapshot as s
    (alert_no, run_date, sha256, last_modified, page_published, raw_blocks, firms_unique,
     merged_blocks, events, schema_version)
  values (v_alert, v_date, v_snap->>'sha256', nullif(v_snap->>'last_modified', ''),
          (v_snap->>'page_published')::date, coalesce((v_snap->>'raw_blocks')::int, 0),
          coalesce((v_snap->>'firms_unique')::int, 0), coalesce((v_snap->>'merged_blocks')::int, 0),
          jsonb_build_object('upserted', n_up, 'delisted', n_down, 'events', n_ev),
          coalesce(v_snap->>'schema_version', 'unknown'))
  on conflict (alert_no, run_date) do update
    set sha256 = excluded.sha256, last_modified = excluded.last_modified,
        page_published = excluded.page_published, raw_blocks = excluded.raw_blocks,
        firms_unique = excluded.firms_unique, merged_blocks = excluded.merged_blocks,
        events = excluded.events, schema_version = excluded.schema_version;

  return jsonb_build_object('upserted', n_up, 'delisted', n_down, 'events', n_ev);
end;
$$;

revoke all on function public.import_alert_apply(jsonb) from public, anon, authenticated;
grant execute on function public.import_alert_apply(jsonb) to service_role;
