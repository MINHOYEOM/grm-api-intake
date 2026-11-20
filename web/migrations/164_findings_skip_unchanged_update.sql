-- 164 findings 무변경 UPDATE 건너뛰기 — 값이 바이트까지 같은 UPDATE 는 행을 다시 쓰지 않는다(BEFORE UPDATE 트리거).
-- Max local web/migrations prefix was 161 on main(89c6dcbc). 162~165 는 같은 PR(DB 용량 3단계). Additive only —
-- 새 트리거 함수 하나 + findings 에 BEFORE UPDATE 트리거 하나. 표 열·인덱스·RLS·RPC·다른 트리거 무변경.
-- 설계: _ops/reports/GRM_DB용량_확보방안_2026-11-20.md §5-3. 156·158(캐시 skip-unchanged)과 같은 규칙을 원본 표에.
--
-- ★왜(2026-11-20 03:15 UTC 실측, 읽기 전용 SELECT):
--   pg_stat_user_tables.findings: n_tup_upd 362,277 · n_tup_hot_upd 207,108 · n_tup_ins 29,456 · n_live_tup 29,392.
--   ★정정: 362k 는 "하루"가 아니라 통계 리셋(2026-06-30) 뒤 누적이다 — 143일 평균 하루 ~2,500회.
--   pg_stat_statements(리셋 2026-07-03) 상위 UPDATE 는 전부 서비스 키 PostgREST PATCH 다:
--     · category_code·label·taxonomy_version  72,017 + 26,581 + 18,026 회 — findings_reclassify_service(바뀌는 행만 PATCH)
--     · finding_text_ko·translation_method    59,628 + 13,432 + 9,654 + … 회 — findings_translate_apply_service
--     · detail_text·detail_text_ko            55,744 회 · 196.5초 — findings_detail_apply(483 상세, TOAST 큰 칸)
--   무변경 재쓰기가 생기는 길(코드 대조):
--     · translate apply 는 outbox 의 **모든** 배치를 매번 다시 PATCH 한다(지금 5개 333항목 → 새 항목 50개 안팎,
--       나머지 ~280항목은 같은 값을 다시 쓴다. 하루 1회 꼴).
--     · detail apply 의 workflow_dispatch 는 폴더 전체(약 1만 항목)를 다시 쓴다 — 11-11 에 6회 실행(5회 취소),
--       detail_text(TOAST, 합 17.8MB)를 통째로 다시 써서 findings 빈자리(~20–25MB 추정)의 주원인이 됐다.
--     · push 로 배치 파일이 **수정**(AM)돼도 그 파일 전 항목을 다시 쓴다.
--   PostgreSQL 은 같은 값 UPDATE 도 새 행 판(+바뀐 칸의 TOAST)을 쓴다. 156·158 은 캐시만 막았고 원본 표는 열려 있다.
--   같은 PR 에서 두 apply 스크립트도 GET 단계에서 같은 값이면 PATCH 를 보내지 않게 고쳤다(1차 방어). 이 트리거는
--   어느 경로(수동 SQL·새 스크립트·재실행)로 와도 막는 2차 방어다.
--
-- ★무엇을: public.grm_skip_unchanged_update() — 범용 BEFORE UPDATE FOR EACH ROW 트리거 함수.
--   NEW 와 OLD 를 to_jsonb 로 바꾸고 **생성 열(attgenerated)을 뺀 뒤 ::text 로 비교**해 같으면 null 을 돌려준다
--   (= 그 행 UPDATE 건너뜀: 새 행 판·TOAST·WAL 0, n_tup_upd 도 안 오른다). 다르면 NEW 그대로(종전 동작).
--   · 생성 열을 빼는 이유: BEFORE 트리거 시점엔 생성 열(firm_key·country_key·original_is_english·search_tsv)이
--     아직 계산되지 않아 NEW 쪽 값을 믿을 수 없다(PG 문서: BEFORE 트리거에서 생성 열 접근 불가). 생성 열은 기반 열의
--     함수이므로 기반 열이 같으면 생성 열도 같다. 목록은 pg_attribute 에서 그때그때 읽는다(새 생성 열이 생겨도 자동).
--   · jsonb 의미 비교(=)가 아니라 ::text 비교: jsonb 숫자는 원래 표기를 지켜 '1.0' 과 '1.00' 을 text 로는 다르게 본다
--     → 표기만 바뀐 UPDATE 도 종전처럼 쓴다(156·158 과 같은 이유). double precision(confidence)은 왕복 정확 표기.
--   · 트리거 이름 zz_ 접두: 같은 시점 BEFORE 트리거는 이름 순으로 돈다 — 다른 BEFORE UPDATE 트리거가 NEW 를 고친
--     뒤 마지막에 비교하게 한다(지금 findings 의 다른 트리거는 BEFORE INSERT findings_scope_status_biu 하나뿐).
--
-- ★호출자 영향(코드 대조): 건너뛴 행은 RETURNING·PostgREST return=representation 에 나오지 않는다(0행).
--   findings 를 PATCH 하는 스크립트는 전부 0행을 "matched_zero = 멱등 no-op" 으로 센다(reclassify·translate apply·
--   detail apply·review_queue·review_promote·backfill_483_inspectors) — 오류로 보는 곳 없음.
--   DB 함수 중 findings 를 UPDATE 하는 곳은 grm_backfill_findings_fei_since·grm_backfill_wl_fei_since 뿐이고
--   둘 다 `fei is null` 행만 고쳐 무변경 갈래가 없다(row_count 불변). findings 에 AFTER 트리거는 없다.
--   INSERT … ON CONFLICT DO UPDATE 로 findings 를 쓰는 수집기는 없다(전부 ignore-duplicates).
--   비용: 행마다 to_jsonb 두 번 + pg_attribute 한 번 조회 — 단건 PATCH 기준 1ms 미만. 대량 SQL UPDATE(백필) 도
--   바뀌는 행만 고르는 것이 이 저장소의 관례라 영향이 작다.
--
-- ★검증법(운영자, 적용 뒤 — 읽기 전용 위주):
--   select tgname, pg_get_triggerdef(oid) from pg_trigger
--    where tgrelid = 'public.findings'::regclass and not tgisinternal;   -- zz_findings_skip_unchanged_bu 추가
--   -- 무변경 UPDATE 가 건너뛰어지는지(트랜잭션 안에서 되돌림):
--   begin;
--     select n_tup_upd from pg_stat_xact_user_tables where relid = 'public.findings'::regclass;   -- 기준
--     with x as (update public.findings set finding_text_ko = finding_text_ko
--                 where finding_id = (select finding_id from public.findings limit 1) returning 1)
--     select count(*) from x;                                                                  -- 0 이어야 한다
--     with y as (update public.findings set finding_text_ko = coalesce(finding_text_ko, '') || ''
--                 where finding_id = (select finding_id from public.findings where finding_text_ko is null limit 1)
--                returning 1) select count(*) from y;                                         -- null→'' 은 1(값이 다르다)
--   rollback;
--   -- 하루 뒤: pg_stat_user_tables.findings.n_tup_upd 증가분이 translate/detail apply 의 succeeded 합과 비슷해야 한다.
--
-- ★되돌리기:
--   drop trigger if exists zz_findings_skip_unchanged_bu on public.findings;
--   drop function if exists public.grm_skip_unchanged_update();
--   (데이터는 그대로다 — 이 migration 은 행을 쓰지 않는다.)

create or replace function public.grm_skip_unchanged_update()
returns trigger
language plpgsql
set search_path to 'pg_catalog'
as $$
declare
  v_gen text[];
begin
  select coalesce(array_agg(a.attname::text), '{}'::text[]) into v_gen
    from pg_catalog.pg_attribute a
   where a.attrelid = tg_relid and a.attnum > 0 and not a.attisdropped and a.attgenerated <> '';
  -- 164: 생성 열을 뺀 행 전체가 바이트까지 같으면 이 행의 UPDATE 를 건너뛴다(머리 주석).
  if (to_jsonb(new) - v_gen)::text = (to_jsonb(old) - v_gen)::text then
    return null;
  end if;
  return new;
end;
$$;
comment on function public.grm_skip_unchanged_update() is
  '164: BEFORE UPDATE 트리거 — 생성 열을 뺀 NEW 와 OLD 가 jsonb text 로 같으면 null(행 재작성 생략). DB 용량 3단계.';
revoke execute on function public.grm_skip_unchanged_update() from public, anon, authenticated;

drop trigger if exists zz_findings_skip_unchanged_bu on public.findings;
create trigger zz_findings_skip_unchanged_bu
  before update on public.findings
  for each row execute function public.grm_skip_unchanged_update();
