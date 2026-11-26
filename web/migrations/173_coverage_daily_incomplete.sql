-- 173_coverage_daily_incomplete.sql — 수집 완전성 N2(칸 대조) 결과를 매일 장부(171)에 함께 쌓는다.
--
-- 가산형: 칸 하나만 더한다(null 허용 — 171 장부만 쓴 날·이 칸이 생기기 전 날은 null).
-- 값 = field_contract.py 의 payload {"measured_at", "total_incomplete", "sources": {계약 키: 집계}, …}.
-- 권한은 171 그대로(service_role 만 select·insert·update).
--
-- 적용 후 확인(읽기 전용):
--   select column_name, data_type, is_nullable from information_schema.columns
--    where table_schema = 'public' and table_name = 'coverage_daily' and column_name = 'incomplete';
-- 적용 뒤 repo 변수 GRM_FIELD_CONTRACT_APPLY=true 로 저장을 켠다(grm-source-reconcile.yml).

begin;

alter table public.coverage_daily
  add column if not exists incomplete jsonb
  check (incomplete is null or jsonb_typeof(incomplete) = 'object');

comment on column public.coverage_daily.incomplete is
  '수집 완전성 N2 — 소스별 완전한 행 계약의 결손 집계(field_contract.py). 필수 칸이 비고 근거도 없는 행 수와 번호.';

commit;
