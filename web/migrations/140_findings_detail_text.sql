-- 140: findings 에 483 관찰 "구체 내용"(상세 원문·국문) 두 칸을 가산한다.
--
-- 계기(2026-11-10 지식 그래프 방향 전환 1단계): 483 관찰의 상세 서술("Specifically, …")은 원본
-- 저장소(raw_signals.raw_json.fda_483_observations[].detail)에만 있고 공개 화면에 닿지 않았다
-- (도달률 0.5%). 같은 날 0단계에서 상세 수집 품질(쪽 넘김·서명란·가림 표시·상한)을 고쳤다.
--
-- ★가산만 한다. 기존 열·함수·RPC 반환 모양은 바꾸지 않는다(RPC 계약 변경 = 배포 창).
-- ★두 칸은 함께 채운다: translations/detail_outbox → findings_detail_apply.py 가 한 PATCH 로
--   detail_text(영문 원문)와 detail_text_ko(국문)를 쓴다. 어느 한쪽만 있는 행은 만들지 않는다.
-- ★공개 범위: findings 의 SELECT 권한은 표 단위(anon·authenticated)라 새 칸도 같은 RLS 게이트
--   (scope_status·국문 존재) 뒤에서 읽힌다. 상세는 FDA 가 공개한 483 원문 그대로다.
alter table public.findings
  add column if not exists detail_text text,
  add column if not exists detail_text_ko text;

comment on column public.findings.detail_text is
  '483 관찰 상세 원문(raw_signals fda_483_observations[].detail, 잘림 없음). detail_text_ko 와 함께 채운다.';
comment on column public.findings.detail_text_ko is
  '483 관찰 상세 국문(구독 경로 번역). detail_text 와 함께 채운다.';
