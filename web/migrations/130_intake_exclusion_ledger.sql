-- ============================================================================
-- 130_intake_exclusion_ledger.sql — 수집기 제외 장부 (원천 대조 감시의 선행 장치)
--
-- 무엇: 수집기가 원천에서 받았지만 규칙으로 **의도적으로 버린** 문서의 기록. 행 하나 = 문서 하나.
--   (source, document_id) 가 키 — 기존 dedup 키 관례 `source::document_id` 와 같다(dedup_key 생성 열).
--   원천 URL·제목·원천 게시일·제외 이유 코드·판정 규칙 버전·처음 본 날·마지막 본 날.
--
-- ★규칙 버전: rule_version 은 판정 규칙(관련성 키워드·범위 함수)의 지문이다(수집기가 코드에서 계산).
--   수집기는 **같은 규칙 버전일 때만** 같은 문서를 다시 받지 않는다. 규칙이 바뀌면 지문이 달라져 다시
--   판정되고, 이번엔 들어오면 intake_exclusion_release 로 장부에서 풀린다 → 영구 블랙리스트가 아니다.
--   basis: metadata(목록 메타만으로 확정) / text(본문까지 읽고 판정) / text_missing(본문을 못 얻은 채
--   메타로만 판정 — 수집기는 이 행을 '판정 끝'으로 보지 않고 다시 본다).
--
-- 가산만: 새 표 1개 + 함수 2개. 기존 표·함수·RPC 반환 shape 은 하나도 건드리지 않는다.
-- 권한: public·anon·authenticated 를 명시 회수(10-28 교훈: `revoke … from public` 만으로는 Supabase 가
--   anon·authenticated 에 직접 준 grant 가 안 지워진다). RLS 켜고 정책은 만들지 않는다 = 클라이언트
--   읽기·쓰기 경로 없음. 쓰기·읽기는 수집기가 쓰는 service_role 만(service_role 은 RLS 를 우회).
--   함수도 같은 이유로 셋을 명시 회수하고 service_role 에만 EXECUTE. security invoker(service_role 이
--   표 권한을 직접 가지므로 definer 불필요), search_path 고정.
--
-- 적용: 비서가 SQL 검토 후 SQL 에디터에서 130 한 파일. 적용 후에야 repo 변수
--   ENABLE_INTAKE_EXCLUSION_LEDGER=true 를 켠다(켜기 전에는 수집기가 장부를 건드리지 않는다).
-- 적용 후 확인(SQL 에디터):
--   select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name='intake_exclusion' order by 1,2;
--   → service_role 만(+postgres). anon·authenticated·PUBLIC 행이 없어야 한다.
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name like 'intake\_exclusion\_%' order by 1,2;   → service_role(+postgres) 만.
--   select relrowsecurity from pg_class where relname='intake_exclusion';   → true
--
-- 되돌리기(이 표·함수만 · 기존 불변 · 장부는 재생성 가능한 관측 데이터라 손실 허용):
--   drop function if exists public.intake_exclusion_record(jsonb);
--   drop function if exists public.intake_exclusion_release(jsonb);
--   drop table if exists public.intake_exclusion;
-- ============================================================================

create table if not exists public.intake_exclusion (
  source        text not null check (char_length(source) between 1 and 60),
  document_id   text not null check (char_length(document_id) between 1 and 300),
  dedup_key     text generated always as (source || '::' || document_id) stored,
  official_url  text check (official_url is null or char_length(official_url) <= 500),
  title         text check (title is null or char_length(title) <= 300),
  published_date date,
  reason_code   text not null check (reason_code ~ '^[a-z0-9_]{2,60}$'),
  rule_version  text not null check (char_length(rule_version) between 4 and 64),
  basis         text not null default 'text' check (basis in ('metadata','text','text_missing')),
  first_seen    date not null default current_date,
  last_seen     date not null default current_date,
  primary key (source, document_id),
  constraint intake_exclusion_seen_order check (last_seen >= first_seen)
);
create index if not exists intake_exclusion_reason_idx
  on public.intake_exclusion (source, reason_code, last_seen desc);

alter table public.intake_exclusion enable row level security;

revoke all on public.intake_exclusion from public, anon, authenticated;
grant all on public.intake_exclusion to service_role;

-- ── 기록: 없으면 넣고, 있으면 최신 판정으로 갱신(first_seen 은 보존, last_seen 은 앞으로만) ──────────
create or replace function public.intake_exclusion_record(p_rows jsonb)
returns integer
language plpgsql
set search_path = public
as $$
declare
  n integer;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    return 0;
  end if;
  with src as (
    -- 같은 키가 한 배치에 두 번 있으면 on conflict 가 같은 행을 두 번 못 건드린다 → 마지막 것만.
    select distinct on (r.source, r.document_id)
           r.source, r.document_id, r.official_url, r.title, r.published_date,
           r.reason_code, r.rule_version, coalesce(r.basis, 'text') as basis,
           coalesce(r.seen_on, current_date) as seen_on
      from jsonb_to_recordset(p_rows) as r(
             source text, document_id text, official_url text, title text,
             published_date date, reason_code text, rule_version text,
             basis text, seen_on date)
     order by r.source, r.document_id
  ), up as (
    insert into public.intake_exclusion as t
      (source, document_id, official_url, title, published_date,
       reason_code, rule_version, basis, first_seen, last_seen)
    select source, document_id, official_url, title, published_date,
           reason_code, rule_version, basis, seen_on, seen_on
      from src
    on conflict (source, document_id) do update
      set official_url   = excluded.official_url,
          title          = excluded.title,
          published_date = excluded.published_date,
          reason_code    = excluded.reason_code,
          rule_version   = excluded.rule_version,
          basis          = excluded.basis,
          last_seen      = greatest(t.last_seen, excluded.last_seen)
    returning 1
  )
  select count(*) into n from up;
  return n;
end;
$$;

-- ── 해제: 규칙이 바뀌어 이번에 수집 대상이 된 문서는 장부에서 푼다 ─────────────────────────────
create or replace function public.intake_exclusion_release(p_keys jsonb)
returns integer
language plpgsql
set search_path = public
as $$
declare
  n integer;
begin
  if p_keys is null or jsonb_typeof(p_keys) <> 'array' then
    return 0;
  end if;
  with del as (
    delete from public.intake_exclusion t
     using jsonb_to_recordset(p_keys) as k(source text, document_id text)
     where t.source = k.source and t.document_id = k.document_id
    returning 1
  )
  select count(*) into n from del;
  return n;
end;
$$;

revoke all on function public.intake_exclusion_record(jsonb) from public, anon, authenticated;
revoke all on function public.intake_exclusion_release(jsonb) from public, anon, authenticated;
grant execute on function public.intake_exclusion_record(jsonb) to service_role;
grant execute on function public.intake_exclusion_release(jsonb) to service_role;
