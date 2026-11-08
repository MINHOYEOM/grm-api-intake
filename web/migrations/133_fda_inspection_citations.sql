-- ============================================================================
-- 133_fda_inspection_citations.sql — FDA 실사 지적 조항(citations) 원장
--
-- 배경: FDA Data Dashboard API `inspections_citations` 엔드포인트가 주는 "어느 실사에서
--   어느 조항(Act/CFR 번호)을 지적했는가"를 싣는다. 058 `fda_inspections`(실사 등급
--   NAI/VAI/OAI)가 "몇 번 실사했고 결과가 어땠나"라면, 이 표는 "무엇을 지적했나"다.
--   수집기: collect_fda_citations.py / 워크플로: grm-fda-citations.yml.
--
-- ★findings 표에 넣지 않는다. citations 는 "21 CFR 211.192" + 짧은 정형 문구라서,
--   483 서술형 지적(findings)과 섞으면 검색·집계가 성격이 다른 두 모집단을 합친다
--   (052/053/054 에서 세 번 겪은 함정). 그래서 별도 평평한 표 하나에만 싣는다.
--
-- ★범위: ProgramArea 'Drugs'·'Biologics' 두 값만(CHECK 로 고정 — 다른 값이 섞이면
--   조용히 오염되지 않고 insert 가 실패한다, 058 의 project_area CHECK 와 같은 fail-closed).
--   citations 에는 ProductType·ProjectArea 가 없다(문서 api-definitions-citations.htm).
--   회계연도 창은 058 과 같다(수집기가 최근 7 회계연도로 필터).
--
-- ★058 과의 연결: inspection_id 로 조인한다(058 PK 와 같은 텍스트 표현). FK 는 걸지
--   않는다 — 058 은 GMP 의약품(Drug Quality Assurance)만 싣고, 생물의약품·임상(BIMO)
--   실사는 거기 없다. FK 를 걸면 그 행들의 insert 가 실패한다. "등급이 붙은 GMP 실사의
--   지적"은 `join public.fda_inspections using (inspection_id)` 로 고른다.
--
-- ★PK = (inspection_id, citation_id). CitationID 가 행마다 유일한지 아직 실측이 없어
--   (로컬에 키가 없다) 복합키로 둔다 — CitationID 가 전역 유일이어도 복합키는 유일하다.
--   중복이 생기면 수집기가 리포트(duplicate_keys)로 먼저 알리고 exit 1 로 끝난다.
--
-- ★country_key 는 055/057 의 public.grm_normalize_country 를 그대로 쓰는 GENERATED
--   컬럼이다(058 과 같은 정본 — 새 정규화 함수를 만들지 않는다). 수집기는 넣지 않는다.
--   전제: 057 적용(이미 운영 반영).
--
-- ★RLS: 058 과 같은 raw_signals 관례 — RLS 를 켜고 public·anon·authenticated 권한을
--   **명시적으로** 회수한다(`revoke ... from public` 만으로는 anon 이 남는다 — 10-28
--   extraction_gap_by_source 사고). 쓰기는 service_role 만 한다. 공개 노출(트렌드 페이지·
--   지적사항 검색 연결)은 다음 단계에서 집계 RPC 를 추가 전용 마이그레이션으로 얹는다.
-- ============================================================================
-- ★004/009 함정 해당 없음: plpgsql DO 블록·선언 변수·배열 슬라이스 없음(순수 DDL).
-- ============================================================================

create table if not exists public.fda_inspection_citations (
  inspection_id        text not null,
  citation_id          text not null,
  fei_number           text not null default '',
  legal_name           text not null default '',
  city                 text not null default '',
  state                text not null default '',
  state_code           text not null default '',
  zip_code             text not null default '',
  country_name         text not null default '',
  inspection_end_date  date,
  fiscal_year          int not null,
  program_area         text not null check (program_area in ('Drugs', 'Biologics')),
  act_cfr_number       text not null default '',
  short_description    text not null default '',
  long_description     text not null default '',
  firm_profile_url     text not null default '',
  ingested_at          timestamptz not null default now(),
  country_key          text generated always as (
                          public.grm_normalize_country(country_name)
                        ) stored,
  primary key (inspection_id, citation_id)
);

create index if not exists fda_inspection_citations_inspection_id_idx
  on public.fda_inspection_citations (inspection_id);
create index if not exists fda_inspection_citations_act_cfr_number_idx
  on public.fda_inspection_citations (act_cfr_number);
create index if not exists fda_inspection_citations_fei_number_idx
  on public.fda_inspection_citations (fei_number);
create index if not exists fda_inspection_citations_country_key_idx
  on public.fda_inspection_citations (country_key);
create index if not exists fda_inspection_citations_fiscal_year_idx
  on public.fda_inspection_citations (fiscal_year);

alter table public.fda_inspection_citations enable row level security;
revoke all on public.fda_inspection_citations from public;
revoke all on public.fda_inspection_citations from anon;
revoke all on public.fda_inspection_citations from authenticated;
grant select, insert, update, delete on public.fda_inspection_citations to service_role;
