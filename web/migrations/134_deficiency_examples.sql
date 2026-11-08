-- ============================================================================
-- 134_deficiency_examples.sql — 업체명 없는 실사 지적 사례 모음(PMDA 연차보고서 별첨 · MHRA GMP 지적 원자료)
--
-- 무엇: collect_deficiency_examples.py 가 원천 파일을 행(지적 1건 / 조항 인용 1건 / 예시 문장 1개) 단위로 넣는 표 2개.
--   deficiency_example_sources  원천 파일 장부. 파일 하나 = 1행(sha256·Last-Modified·행 수). 연 1회 갱신 감지용.
--   deficiency_examples         행. (source_key, row_no) 가 멱등 키다. 같은 파일을 다시 넣으면 변화 0.
--     agency           PMDA | MHRA
--     year             PMDA = 일본 회계연도(FY2025 = 2025-04~2026-03, year_basis 'jp_fiscal') / MHRA = 역년
--     lang             ja | en — 원문 언어. 번역은 하지 않는다(한국어는 다음 단계).
--     statement        지적 원문. MHRA 2018·2019 xlsx 는 문장이 없어 빈칸이다(조항·등급만 있다).
--     context          PMDA '指摘に至った背景' / MHRA PDF 예시 묶음의 머리 문장
--     agency_category  기관 분류 원문(PMDA 指摘分類 / MHRA 장·부속서)
--     grm_category     우리 공통 분류(grm_findings.FINDING_TAXONOMY code) + grm_category_basis(매핑 근거)
--     clause_keys      정규화 조항 키 — 지식 그래프(106 finding_clause)와 같은 형식:
--                      'JP GMP Ordinance Article 11-2'(第11条の2) · 'EU GMP Chapter 1' · 'EU GMP Annex 15' · '21 CFR 211.22'
--     clauses          [{key, para, raw, basis, confidence}] — raw 는 원문 글자 그대로.
--                      ★영문 PMDA 별첨의 조항 표기는 일본어 원문과 어긋나는 일이 있어 confidence 0.6 으로 둔다.
--     site_group       같은 제조소(PMDA 각주 '同一の製造所') / 같은 실사(MHRA Blinded ID)
-- 업체별 findings 표에 넣지 않는 이유: 업체가 없다(익명 사례 모음). 업체 축 검색·워치리스트와 섞이면 안 된다.
--
-- 가산만: 새 표 2개 + 함수 2개. 기존 표·함수·RPC 는 하나도 건드리지 않는다.
-- 권한: public·anon·authenticated 를 명시 회수(10-28 교훈: `revoke … from public` 만으로는 Supabase 가
--   anon·authenticated 에 직접 준 grant 가 안 지워진다). RLS 켜고 정책은 만들지 않는다 = 클라이언트 경로 없음.
--   읽기·쓰기는 service_role 만. 공개 노출(웹·검색·트렌드 페이지)은 비서가 따로 정한다(별도 migration).
--   함수는 security invoker · search_path 고정 · service_role 에만 EXECUTE.
--
-- 적용: 비서가 SQL 검토 후 SQL 에디터에서 이 한 파일. 적용 후에야 repo 변수 ENABLE_DEFICIENCY_EXAMPLES=true.
-- 적용 후 확인:
--   select table_name, grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name like 'deficiency\_example%' order by 1,2,3;
--   → service_role(+postgres) 만. anon·authenticated·PUBLIC 행이 없어야 한다.
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name like 'deficiency\_examples\_%' order by 1,2;   → service_role(+postgres) 만.
--   select relname, relrowsecurity from pg_class where relname like 'deficiency\_example%';   → 전부 true
--
-- 되돌리기(이 표·함수만 · 기존 불변):
--   drop function if exists public.deficiency_examples_finalize(jsonb, integer[]);
--   drop function if exists public.deficiency_examples_upsert(text, text, integer, text, jsonb);
--   drop table if exists public.deficiency_examples;
--   drop table if exists public.deficiency_example_sources;
-- ============================================================================

create table if not exists public.deficiency_example_sources (
  source_key     text primary key check (source_key ~ '^(pmda|mhra):[a-z0-9_]{3,80}$'),
  agency         text not null check (agency in ('PMDA','MHRA')),
  year           integer not null check (year between 2000 and 2100),
  lang           text not null check (lang in ('ja','en')),
  kind           text not null check (kind in ('pmda_attachment','mhra_xlsx','mhra_trend_pdf')),
  url            text not null check (url ~ '^https://'),
  index_url      text not null default '',
  sha256         text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  bytes          integer not null check (bytes > 0),
  last_modified  text not null default '' check (char_length(last_modified) <= 80),
  rows_parsed    integer not null check (rows_parsed >= 0),
  rows_loaded    integer not null default 0 check (rows_loaded >= 0),
  parser_version text not null check (char_length(parser_version) between 1 and 60),
  first_loaded   timestamptz not null default now(),
  last_loaded    timestamptz not null default now()
);

create table if not exists public.deficiency_examples (
  id                 bigint generated always as identity primary key,
  source_key         text not null,
  row_no             integer not null check (row_no > 0),
  agency             text not null check (agency in ('PMDA','MHRA')),
  year               integer not null check (year between 2000 and 2100),
  year_basis         text not null default 'calendar' check (year_basis in ('calendar','jp_fiscal')),
  lang               text not null check (lang in ('ja','en')),
  locator            text not null check (char_length(locator) between 1 and 120),
  item_no            text not null default '' check (char_length(item_no) <= 40),
  statement          text not null default '' check (char_length(statement) <= 20000),
  context            text not null default '' check (char_length(context) <= 20000),
  agency_system      text not null default '' check (char_length(agency_system) <= 200),
  agency_category    text not null default '' check (char_length(agency_category) <= 300),
  product_scope      text not null default '' check (char_length(product_scope) <= 200),
  grade              text not null default '' check (grade in ('','critical','major','other')),
  deficiency_label   text not null default '' check (char_length(deficiency_label) <= 40),
  site_group         text not null default '' check (char_length(site_group) <= 60),
  country            text not null default '' check (char_length(country) <= 80),
  clause_keys        text[] not null default '{}',
  clauses            jsonb not null default '[]'::jsonb check (jsonb_typeof(clauses) = 'array'),
  grm_category       text not null default '' check (char_length(grm_category) <= 60),
  grm_category_basis text not null default '' check (grm_category_basis in
                       ('','agency_category_map','eu_gmp_part_map','text_classifier')),
  attrs              jsonb not null default '{}'::jsonb check (jsonb_typeof(attrs) = 'object'),
  content_hash       text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  -- 멱등 키: 같은 원천의 같은 자리 = 같은 행.
  constraint deficiency_examples_source_row unique (source_key, row_no),
  constraint deficiency_examples_source_fk foreign key (source_key)
    references public.deficiency_example_sources (source_key) on delete cascade deferrable initially deferred
);
create index if not exists deficiency_examples_agency_year_idx
  on public.deficiency_examples (agency, year, lang);
create index if not exists deficiency_examples_category_idx
  on public.deficiency_examples (grm_category, agency);
create index if not exists deficiency_examples_clause_keys_idx
  on public.deficiency_examples using gin (clause_keys);

alter table public.deficiency_example_sources enable row level security;
alter table public.deficiency_examples        enable row level security;

revoke all on public.deficiency_example_sources from public, anon, authenticated;
revoke all on public.deficiency_examples        from public, anon, authenticated;
grant all on public.deficiency_example_sources to service_role;
grant all on public.deficiency_examples        to service_role;

-- ── 적재 1: 행 묶음 upsert ───────────────────────────────────────────────────────────────
-- 수집기가 원천 하나를 800행씩 나눠 부른다. content_hash 가 같으면 건드리지 않는다(unchanged).
-- 원천 장부 행은 finalize 가 만든다 — FK 는 deferrable 이지만 PostgREST 호출은 호출마다 커밋이므로,
-- 첫 upsert 전에 장부 행이 없으면 자리표시 행을 먼저 만든다(rows_loaded=0, finalize 가 덮는다).
create or replace function public.deficiency_examples_upsert(
  p_source_key text, p_agency text, p_year integer, p_lang text, p_rows jsonb)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  n_ins integer := 0;
  n_upd integer := 0;
  n_all integer := 0;
begin
  if p_source_key is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'deficiency_examples_upsert: source_key/rows 필수' using errcode = '22023';
  end if;

  insert into public.deficiency_example_sources
    (source_key, agency, year, lang, kind, url, sha256, bytes, rows_parsed, parser_version)
  values (p_source_key, p_agency, p_year, p_lang,
          case when p_agency = 'PMDA' then 'pmda_attachment' else 'mhra_xlsx' end,
          'https://pending.invalid/', repeat('0', 64), 1, 0, 'pending')
  on conflict (source_key) do nothing;

  select count(*) into n_all from jsonb_array_elements(p_rows);

  with src as (
    select distinct on (r.row_no) r.*
      from jsonb_to_recordset(p_rows) as r(
             row_no integer, locator text, item_no text, statement text, context text,
             agency_system text, agency_category text, product_scope text, grade text,
             deficiency_label text, site_group text, country text, clause_keys text[],
             clauses jsonb, grm_category text, grm_category_basis text, attrs jsonb,
             content_hash text)
     order by r.row_no
  ), up as (
    insert into public.deficiency_examples as t
      (source_key, row_no, agency, year, year_basis, lang, locator, item_no, statement, context,
       agency_system, agency_category, product_scope, grade, deficiency_label, site_group, country,
       clause_keys, clauses, grm_category, grm_category_basis, attrs, content_hash)
    select p_source_key, row_no, p_agency, p_year,
           case when p_agency = 'PMDA' then 'jp_fiscal' else 'calendar' end, p_lang,
           locator, coalesce(item_no, ''), coalesce(statement, ''), coalesce(context, ''),
           coalesce(agency_system, ''), coalesce(agency_category, ''), coalesce(product_scope, ''),
           coalesce(grade, ''), coalesce(deficiency_label, ''), coalesce(site_group, ''),
           coalesce(country, ''), coalesce(clause_keys, '{}'), coalesce(clauses, '[]'::jsonb),
           coalesce(grm_category, ''), coalesce(grm_category_basis, ''), coalesce(attrs, '{}'::jsonb),
           content_hash
      from src
    on conflict (source_key, row_no) do update
      set agency = excluded.agency, year = excluded.year, year_basis = excluded.year_basis,
          lang = excluded.lang, locator = excluded.locator, item_no = excluded.item_no,
          statement = excluded.statement, context = excluded.context,
          agency_system = excluded.agency_system, agency_category = excluded.agency_category,
          product_scope = excluded.product_scope, grade = excluded.grade,
          deficiency_label = excluded.deficiency_label, site_group = excluded.site_group,
          country = excluded.country, clause_keys = excluded.clause_keys, clauses = excluded.clauses,
          grm_category = excluded.grm_category, grm_category_basis = excluded.grm_category_basis,
          attrs = excluded.attrs, content_hash = excluded.content_hash, updated_at = now()
      where t.content_hash is distinct from excluded.content_hash
    returning (xmax = 0) as inserted
  )
  select count(*) filter (where inserted), count(*) filter (where not inserted)
    into n_ins, n_upd from up;

  return jsonb_build_object('inserted', n_ins, 'updated', n_upd,
                            'unchanged', greatest(n_all - n_ins - n_upd, 0));
end;
$$;

-- ── 적재 2: 마무리 — 이번 파싱에 없는 행 삭제 + 원천 장부 갱신 ─────────────────────────────
-- p_source = 원천 장부 한 행(수집기 source_payload) · p_keep = 이번에 파싱된 row_no 전부.
create or replace function public.deficiency_examples_finalize(p_source jsonb, p_keep integer[])
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_key   text := p_source->>'source_key';
  n_del   integer;
  n_rows  integer;
begin
  if v_key is null or p_keep is null then
    raise exception 'deficiency_examples_finalize: source_key/keep 필수' using errcode = '22023';
  end if;

  with d as (
    delete from public.deficiency_examples
     where source_key = v_key and not (row_no = any (p_keep))
    returning 1
  )
  select count(*) into n_del from d;

  select count(*) into n_rows from public.deficiency_examples where source_key = v_key;

  insert into public.deficiency_example_sources as s
    (source_key, agency, year, lang, kind, url, index_url, sha256, bytes, last_modified,
     rows_parsed, rows_loaded, parser_version)
  values (v_key, p_source->>'agency', (p_source->>'year')::int, p_source->>'lang', p_source->>'kind',
          p_source->>'url', coalesce(p_source->>'index_url', ''), p_source->>'sha256',
          (p_source->>'bytes')::int, left(coalesce(p_source->>'last_modified', ''), 80),
          (p_source->>'rows_parsed')::int, n_rows, p_source->>'parser_version')
  on conflict (source_key) do update
    set agency = excluded.agency, year = excluded.year, lang = excluded.lang, kind = excluded.kind,
        url = excluded.url, index_url = excluded.index_url, sha256 = excluded.sha256,
        bytes = excluded.bytes, last_modified = excluded.last_modified,
        rows_parsed = excluded.rows_parsed, rows_loaded = excluded.rows_loaded,
        parser_version = excluded.parser_version, last_loaded = now();

  return jsonb_build_object('deleted', n_del, 'rows_loaded', n_rows);
end;
$$;

revoke all on function public.deficiency_examples_upsert(text, text, integer, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.deficiency_examples_finalize(jsonb, integer[])
  from public, anon, authenticated;
grant execute on function public.deficiency_examples_upsert(text, text, integer, text, jsonb) to service_role;
grant execute on function public.deficiency_examples_finalize(jsonb, integer[]) to service_role;
