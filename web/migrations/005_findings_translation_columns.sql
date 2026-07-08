-- FIND-1 M6a 번역 컬럼 추가 — 라이브 public.findings 에 선택적 국문 해석 필드 2개를 얹는다.
-- finding_text(영문 원문 verbatim)는 절대 변경하지 않는다 -- 이 마이그레이션은 additive 컬럼만
-- 추가하며 기존 24건은 두 컬럼 모두 기본값 ''(빈 문자열=미번역)으로 채워진다.
-- 전제: 002_findings.sql(fresh-install 정본)이 먼저 적용되어 있어야 한다. 신규 fresh-install은
-- 002 자체가 이미 두 컬럼을 포함하므로(byte 단일 소스: findings_supabase.postgres_schema_ddl()),
-- 이 파일은 그 이전에 만들어진 이미 살아있는 테이블에만 필요하다. 재실행해도 멱등하다
-- (add column if not exists / drop constraint if exists 가드).

alter table public.findings
  add column if not exists finding_text_ko text not null default '';

alter table public.findings
  add column if not exists translation_method text not null default '';

alter table public.findings drop constraint if exists findings_translation_method_check;
alter table public.findings
  add constraint findings_translation_method_check
  check (translation_method in ('', 'llm_assisted', 'manual'));

-- 검증: 두 컬럼이 생성되고 기존 행이 모두 빈 문자열(미번역)로 채워졌는지 확인.
-- select finding_text_ko, translation_method from public.findings limit 5;
-- 검증: CHECK 제약이 존재하고 허용값 3개(빈 문자열 포함)를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_translation_method_check';
