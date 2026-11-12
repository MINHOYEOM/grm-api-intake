-- ============================================================================
-- 147_raw_signals_published_date_nullable.sql — 게시일 없는 원천을 정본에 받는다
--
-- 무엇: raw_signals.published_date 의 NOT NULL 을 푼다. 그 한 가지만 한다.
--
-- 왜(2026-11-12): 게시일을 단언하지 않는 원천은 raw_signals 에 들어올 수 없었다. ICH 토픽 스냅샷 31건과
--   실사일이 확인되지 않은 WHOPIR 3건(who-whopir-3579a9363c65·389995566672·7f12fc0ea517)이 그렇다.
--   그래서 이 행들은 Notion 에만 있었다. 노션 대기열 이전(대표 결정 11-10)을 하려면 원천을 빠짐없이
--   정본에 받아야 한다. 날짜를 지어 넣지는 않는다(수집일로 채우면 틀린 게시일이 된다) — 빈 값은 null 이다.
--
-- 바꾸지 않는 것:
--   · 형식 check 를 새로 걸지 않는다. 새 거부 경로는 조용한 유실이 된다. 11-12 실측으로 기존 9,036행은
--     모두 YYYY-MM-DD 형식이다.
--   · findings.published_date 는 그대로 NOT NULL 이다(게시일 없는 원천은 지적사항을 만들지 않는다).
--   · 표·함수·RPC 반환 형태는 그대로다. 가산 변경이다(값이 null 일 수 있게 될 뿐).
--
-- 읽는 쪽 점검(11-12, 운영 DB 의 함수 본문 전수):
--   · regulatory_news: `rs.published_date >= …` 비교라 null 행은 자연히 빠진다(ICH 는 대상 소스도 아니다).
--   · mfds_inspection_documents: `coalesce(r.published_date,'')` — MFDS 는 날짜가 늘 있다.
--   · findings_stats_compute: findings 표의 날짜를 쓴다(raw_signals 는 건수만).
--   · Python REST 독자(483·MFDS 수리 도구·감사)는 날짜가 늘 있는 FDA·MFDS 소스로 거른다.
--
-- 적용 순서: 이 migration 을 **먼저** 적용하고 나서 코드 PR 을 머지한다. 코드가 먼저 나가면
--   게시일 없는 항목 POST 가 NOT NULL 로 거부되어 supabase-append-failed 경고가 난다(유실은 아님).
-- 적용 후 확인:
--   select is_nullable from information_schema.columns
--    where table_schema='public' and table_name='raw_signals' and column_name='published_date';  → YES
-- 되돌리기(게시일 없는 행이 아직 없을 때만):
--   alter table public.raw_signals alter column published_date set not null;
-- ============================================================================

alter table public.raw_signals alter column published_date drop not null;

comment on column public.raw_signals.published_date is
  '원천 게시일(YYYY-MM-DD, text). 원천이 게시일을 단언하지 않으면 null(ICH 토픽 스냅샷 등). 날짜를 지어 넣지 않는다. (147, 2026-11-12)';
