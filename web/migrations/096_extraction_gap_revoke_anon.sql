-- ============================================================================
-- 096_extraction_gap_revoke_anon.sql — [권한 정정] extraction_gap_by_source() 를 service_role 전용으로.
--
-- ★왜(2026-10-28 095 적용 후 실측): 046/048/095 는 모두 `revoke all ... from public` 후
--   service_role 에만 grant 했다. 그러나 Supabase 는 public 스키마 함수에 default privileges 로
--   anon·authenticated 에 EXECUTE 를 **직접** 준다 — PUBLIC 회수로는 지워지지 않는다.
--   실측 routine_privileges = anon·authenticated·postgres·service_role. 046 부터 열려 있었다.
--   create or replace 는 기존 ACL 을 유지하므로 함수를 다시 올려도 바뀌지 않는다.
--
--   카운트 전용(원문 무반환)이라 유출은 없지만 의도(운영 감시 전용)와 다르다. 호출자는
--   findings_backlog_monitor.py(service_role) 하나뿐이라 회수해도 깨지는 화면이 없다.
--
-- 함수 본문·응답 shape 불변 — 권한만 바꾼다.
-- ============================================================================

revoke execute on function public.extraction_gap_by_source() from anon, authenticated;
grant execute on function public.extraction_gap_by_source() to service_role;
