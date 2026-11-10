-- ============================================================================
-- 141_deficiency_examples_ko.sql — PMDA·MHRA 지적 사례(134)에 한국어 칸 3개를 더한다(가산만)
--
-- ★운영 적용 2026-11-11(작업 번호 140 으로 적용 — 140 은 #1493 이 먼저 머지해 141 로 바꿈. 칸 주석의 "140" 은 그때 값).
-- 계기(2026-11-11): 대표 결정 "한국어로 있어야 볼 수 있다". 원문(영어·일본어)은 그대로 두고
--   한국어를 옆에 붙인다. 화면은 deficiency_cases_refresh.py 스냅샷을 거쳐 한국어를 읽는다.
--
--   statement_ko        지적 문장의 한국어 번역. 문장이 없는 행(MHRA 2018·2019 xlsx)은 빈칸.
--   agency_category_ko  기관 분류(PMDA 指摘分類 / MHRA 장·부속서)의 한국어. xlsx 행도 채운다.
--   ko_source_hash      번역할 때의 content_hash. 원문이 바뀌어 content_hash 가 달라지면
--                       스냅샷이 그 번역을 싣지 않는다(낡은 번역이 새 원문 옆에 나가지 않게).
--
-- ★수집기 upsert(134 deficiency_examples_upsert)는 이 칸을 모른다 — 다시 수집해도 번역은 지워지지
--   않고, 원문이 바뀐 행만 content_hash 불일치로 화면에서 빠진다(재번역 대상).
-- 권한·RLS 는 134 그대로(service_role 전용). 새 함수·정책 없음.
--
-- 되돌리기:
--   alter table public.deficiency_examples
--     drop column if exists statement_ko, drop column if exists agency_category_ko,
--     drop column if exists ko_source_hash;
-- ============================================================================

alter table public.deficiency_examples
  add column if not exists statement_ko text not null default ''
    check (char_length(statement_ko) <= 20000),
  add column if not exists agency_category_ko text not null default ''
    check (char_length(agency_category_ko) <= 300),
  add column if not exists ko_source_hash text not null default ''
    check (ko_source_hash = '' or ko_source_hash ~ '^[0-9a-f]{64}$');

comment on column public.deficiency_examples.statement_ko is
  '지적 문장 한국어 번역(원문은 statement). 141';
comment on column public.deficiency_examples.agency_category_ko is
  '기관 분류 한국어(원문은 agency_category). 141';
comment on column public.deficiency_examples.ko_source_hash is
  '번역 당시 content_hash — 다르면 번역이 낡았다. 141';
