-- FIND-1 M9a 공개 게이트 — 국문 해석이 없는 finding 을 공개 API(anon/authenticated)에서
-- 원천 차단한다. M4 완전 가동(raw_signals+findings 직행 적재)으로 신규 유입이 매일
-- Supabase 에 쌓이는 지금, 미완성(미번역) 행이 클라이언트 필터 없이도 공개 웹에 노출되면
-- 안 된다는 요구를 DB 레벨(RLS)에서 강제한다 — 클라이언트(findings.js)가 필터링하는 게
-- 아니라, anon/authenticated 로는 애초에 조회 자체가 안 되게 한다.
-- 전제: 002_findings.sql(fresh-install 정본) + 003_findings_public_read.sql(공개 SELECT
-- 허용) 이 먼저 적용되어 있어야 한다. 이 파일은 003 이 만든 정책을 대체(drop 후 재생성)만
-- 한다 — grant 는 003 에서 이미 부여됐으므로 다시 손대지 않는다.

-- finding_language = 'KO' 인 행(MFDS 등 원문 자체가 한국어인 소스)은 번역이 필요 없으므로
-- finding_text_ko 가 비어 있어도 공개 대상이다. 그 외(FDA/WL/WHOPIR 등 원문이 영문인
-- 소스)는 finding_text_ko 가 채워진 뒤에만 공개된다.
drop policy if exists findings_public_read on public.findings;
create policy findings_public_read
on public.findings
for select
to anon, authenticated
using (finding_text_ko <> '' or finding_language = 'KO');

-- public.raw_signals 는 이 마이그레이션과 무관하다 — 002 의 전면 차단(정책 0개 + 권한
-- 회수)이 계속 유지된다.

-- service_role 은 RLS 를 우회하므로(Postgres 기본 동작) 이 정책은 collect_intake.py 의
-- M4 append 나 findings_translate.py --apply 의 향후 자동 write 경로에 아무 영향이 없다 —
-- 오직 anon/authenticated 로의 공개 SELECT 결과만 좁힌다.

-- 검증: 정책이 새 조건으로 교체됐는지 확인.
-- select polname, pg_get_expr(polqual, polrelid) from pg_policy
--   join pg_class on pg_class.oid = pg_policy.polrelid
--   where pg_class.relname = 'findings' and polname = 'findings_public_read';
-- 검증: 미번역·비KO 행이 anon 세션에서 보이지 않는지(핵심 회귀 케이스).
-- set role anon; select count(*) from public.findings
--   where finding_text_ko = '' and finding_language <> 'KO'; -- 항상 0 이어야 한다
-- reset role;
