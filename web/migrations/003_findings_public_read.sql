-- FIND-1 M3c 공개 읽기 정책 — public.findings 만 anon/authenticated SELECT 재허용한다.
-- 전제: 002_findings.sql 이 먼저 적용되어 있어야 한다(테이블·RLS 활성화가 그 파일 소관).
-- 이 파일은 002 가 전면 차단한 두 테이블 중 findings 한 쪽의 grant/policy 만 되돌린다.

-- public.findings: anon/authenticated SELECT 만 허용(쓰기 권한은 부여하지 않는다 — 적재는
-- 계속 service_role 전용).
grant select on public.findings to anon, authenticated;

drop policy if exists findings_public_read on public.findings;
create policy findings_public_read
on public.findings
for select
to anon, authenticated
using (true);

-- public.raw_signals 는 계속 전면 차단한다 — 원본 보존층(raw_json/row_json) 비공개 계약.
-- 002 의 `revoke all on public.raw_signals, public.findings from anon, authenticated;` 로
-- 이미 권한이 회수돼 있고, 이 파일은 raw_signals 에 대해 어떤 grant/policy 도 추가하지 않는다.
-- (참고용 명시 — 실행 효과 없음: raw_signals 는 정책 0개 + 권한 0 상태를 그대로 유지한다.)
