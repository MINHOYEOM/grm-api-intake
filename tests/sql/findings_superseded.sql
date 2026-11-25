\set ON_ERROR_STOP on
-- 별도 빈 테스트 DB에서만 실행한다. 실서비스 SQL Editor에 붙이지 않는다.
-- 166_findings_superseded.sql — '대체됨' 표식 계약 시험(2026-11-24).
begin;
create role anon;
create role authenticated;
create role service_role bypassrls;
-- 002 findings 모양(이 시험에 필요한 칸만) + 010 제약
create table public.findings (
  finding_id text primary key, raw_signal_id text not null, source text not null default 'FDA Warning Letter',
  finding_text text not null, finding_language text, review_status text not null,
  finding_text_ko text not null default '', translation_method text not null default '',
  scope_status text not null default 'ok',
  constraint findings_scope_status_chk check (scope_status in ('ok', 'non_pharma', 'fragment'))
);
grant usage on schema public to service_role;
grant select, insert, update on public.findings to service_role;

create function public.t_assert(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;

-- 문서 r1: 옛 덩어리 old1(공개·사람 번역)·old2(반려) / 새 글 new1(공개)·new2(번역 대기)
-- 문서 r2: 옛 덩어리 old3 / 새 글 new3(번역 대기 — 공개된 대체 글 없음)
-- 문서 r3: 옛 행 old4 는 이미 범위 밖(non_pharma)
insert into public.findings(finding_id, raw_signal_id, finding_text, finding_language, review_status, finding_text_ko, translation_method, scope_status) values
  ('old1','r1','Specifically, blob','EN','accepted','사람 번역','manual','ok'),
  ('old2','r1','Specifically, other','EN','rejected','반려된 행','llm_assisted','ok'),
  ('new1','r1','1. Your firm failed','EN','accepted','새 번역','llm_assisted','ok'),
  ('new2','r1','2. Your firm failed','EN','needs_review','','','ok'),
  ('old3','r2','Specifically, blob 3','EN','accepted','옛 번역','llm_assisted','ok'),
  ('new3','r2','1. New untranslated','EN','needs_review','','','ok'),
  ('old4','r3','Food blob','EN','accepted','식품','llm_assisted','non_pharma'),
  ('new4','r3','1. Public','EN','accepted','공개','llm_assisted','ok');

\ir ../../web/migrations/166_findings_superseded.sql

-- 권한
select public.t_assert((select relrowsecurity from pg_class where oid='public.findings_supersessions'::regclass), 'RLS enabled');
select public.t_assert(not has_table_privilege('anon','public.findings_supersessions','SELECT'), 'anon cannot read supersessions');
select public.t_assert(not has_table_privilege('authenticated','public.findings_supersessions','SELECT'), 'authenticated cannot read supersessions');
select public.t_assert(not has_function_privilege('anon','public.grm_findings_supersede(jsonb,text)','EXECUTE'), 'anon cannot supersede');
select public.t_assert(not has_function_privilege('authenticated','public.grm_findings_supersede(jsonb,text)','EXECUTE'), 'authenticated cannot supersede');
select public.t_assert(not has_function_privilege('anon','public.grm_findings_unsupersede(text[])','EXECUTE'), 'anon cannot unsupersede');
select public.t_assert(has_function_privilege('service_role','public.grm_findings_supersede(jsonb,text)','EXECUTE'), 'service_role can supersede');
-- 제약: superseded 허용, 임의 값 거부
select public.t_assert(pg_get_constraintdef((select oid from pg_constraint where conname='findings_scope_status_chk')) like '%superseded%', 'constraint allows superseded');

set local role service_role;

-- 1) 공개된 대체 글이 있으면 숨긴다 — 사람 결정 칸은 그대로, 이전 값 기록
select public.t_assert(
  (select (public.grm_findings_supersede(
     '[{"finding_id":"old1","raw_signal_id":"r1","superseded_by":["new1","new2"],"reason":"body_recollected"}]'::jsonb, 'test')->>'superseded')::int) = 1,
  'old1 superseded');
select public.t_assert((select scope_status from public.findings where finding_id='old1') = 'superseded', 'old1 hidden');
select public.t_assert((select review_status || '|' || finding_text_ko || '|' || translation_method from public.findings where finding_id='old1')
                       = 'accepted|사람 번역|manual', 'human decisions kept');
select public.t_assert((select prev_scope_status from public.findings_supersessions where finding_id='old1') = 'ok', 'prev recorded');
select public.t_assert((select superseded_by from public.findings_supersessions where finding_id='old1') = '["new1","new2"]'::jsonb, 'replacements recorded');

-- 2) 공개된 대체 글이 없으면 숨기지 않는다(번역 대기)
select public.t_assert(
  (select public.grm_findings_supersede(
     '[{"finding_id":"old3","raw_signal_id":"r2","superseded_by":["new3"]}]'::jsonb, 'test')->'skipped'->0->>'why') = 'replacement_not_public',
  'not public replacement skipped');
select public.t_assert((select scope_status from public.findings where finding_id='old3') = 'ok', 'old3 still visible');

-- 3) 이미 범위 밖인 행은 건드리지 않는다
select public.t_assert(
  (select public.grm_findings_supersede(
     '[{"finding_id":"old4","raw_signal_id":"r3","superseded_by":["new4"]}]'::jsonb, 'test')->'skipped'->0->>'why') = 'not_ok:non_pharma',
  'non_pharma untouched');

-- 4) 다른 문서의 글·없는 글·자기 자신을 대체 글로 쓰면 거부
select public.t_assert(
  (select public.grm_findings_supersede(
     '[{"finding_id":"old3","raw_signal_id":"r2","superseded_by":["new1"]}]'::jsonb, 'test')->'skipped'->0->>'why') = 'superseded_by_missing',
  'other document replacement rejected');
select public.t_assert(
  (select public.grm_findings_supersede(
     '[{"finding_id":"old3","raw_signal_id":"r1","superseded_by":["new1"]}]'::jsonb, 'test')->'skipped'->0->>'why') = 'raw_signal_mismatch',
  'raw_signal mismatch rejected');
select public.t_assert(
  (select public.grm_findings_supersede(
     '[{"finding_id":"new1","raw_signal_id":"r1","superseded_by":["new1"]}]'::jsonb, 'test')->'skipped'->0->>'why') = 'bad_superseded_by',
  'self replacement rejected');

-- 5) 다시 불러도 멱등(이미 superseded → not_ok)
select public.t_assert(
  (select public.grm_findings_supersede(
     '[{"finding_id":"old1","raw_signal_id":"r1","superseded_by":["new1"]}]'::jsonb, 'test')->'skipped'->0->>'why') = 'not_ok:superseded',
  'idempotent');

-- 6) 되돌리기 — 이전 값으로, 기록 삭제
select public.t_assert((select (public.grm_findings_unsupersede(array['old1'])->>'restored')::int) = 1, 'restored one');
select public.t_assert((select scope_status from public.findings where finding_id='old1') = 'ok', 'old1 visible again');
select public.t_assert((select count(*) from public.findings_supersessions) = 0, 'record removed');

rollback;
