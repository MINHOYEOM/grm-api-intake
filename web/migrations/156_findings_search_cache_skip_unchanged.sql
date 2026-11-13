-- 156 findings_search_cache 갱신 — 새 payload 가 기존과 바이트까지 같으면 payload 를 다시 쓰지 않는다.
-- Max local web/migrations prefix was 155 on main. Additive only — 함수 시그니처·반환(text)·security
-- invoker·search_path·권한 불변. 공개 RPC(findings_search)·읽기(findings_search_cache_read)는 손대지 않는다.
-- 설계: _ops/reports/GRM_DB용량축소_설계_2026-11-13.md 의 A안 (2)(갱신 시 동일값 건너뛰기).
--
-- ★왜(2026-11-13 01:10 UTC 실측, 읽기 전용 SELECT):
--   findings_search_cache 196행(hot 2 · daily 194) 표 전체 34,234,368 B(32.6 MiB)
--     = heap 147,456 B + TOAST 33,849,344 B(32.3 MiB) + 인덱스 204,800 B.
--   살아 있는 payload 합 sum(pg_column_size(payload)) = 18,444,878 B(17.6 MiB)
--     → TOAST 의 약 14.7 MiB 가 죽은 판(이전 세대 payload 가 남긴 빈자리).
--   pg_stat_user_tables: n_tup_upd 17,516(HOT 9,996) · autovacuum 53회.
--   TOAST 관계(pg_stat_all_tables): n_tup_ins 671,285 · n_tup_del 655,817 — 갱신 때마다 payload 전체를
--   새 TOAST 조각으로 다시 쓰고 옛 조각을 지운다.
--   쓰기 양: hot 2행(저장 평균 74,685 B) × 하루 72회(:05/:25/:45) ≈ 10.8 MB/일,
--            daily 194행(저장 평균 94,307 B) × 하루 1회 ≈ 18.3 MB/일.
--   그런데 원 계산의 입력(findings)은 하루 한두 번 바뀐다 — hot 은 20분마다 같은 답을 다시 쓰는 일이 대부분.
--   쓰는 지점은 `findings_search_cache_refresh_row(jsonb)`(151) 의 update 한 곳뿐이다(라이브
--   pg_get_functiondef 대조: refresh·refresh_batch 는 이 함수를 부르고 update 가 없다. sync(088)는 행 추가·삭제만 하고
--   payload 는 쓰지 않는다). 라이브 본문은 151 파일과 같다(주석만 빠짐).
--
-- ★무엇을: refresh_row 의 update 를 두 갈래로 나눈다(가산).
--   1) `payload::text is not distinct from v::text` 이면 computed_ms·refreshed_at·last_error 만 갱신한다.
--      SET 에 payload 가 없으면 PostgreSQL 은 TOAST 포인터를 그대로 옮긴다 — TOAST 쓰기 0. 바뀌는 열에
--      인덱스가 없어(pkey 는 args) HOT update 가 되어 죽은 heap 판도 페이지 안에서 정리된다.
--   2) 아니면(값이 다르거나, 기존 payload 가 null 이거나) 종전 update 그대로 payload 까지 쓴다.
--   행이 없으면(갱신 중 sync 가 지운 경우) 두 update 모두 0행 — 종전과 같다.
--
-- ★왜 refreshed_at 은 계속 갱신하나(완전 무쓰기 안 기각): 읽는 쪽 전부가 refreshed_at 으로 신선도를 본다.
--   · findings_search_cache_read(086): hot 75분 · daily 30시간 넘으면 캐시를 버리고 원 계산 폴백
--     → 갱신하지 않으면 같은 답인 행이 낡은 것으로 판정돼 anon 3초 벽 앞의 원 계산으로 떨어진다.
--   · findings_search_cache_status(086)·job_status(151)·findings_search_cache_health.py 의 신선 행 수.
--   · refresh_batch(151) 의 `order by c.refreshed_at asc nulls first` — 안 바꾸면 같은 행만 계속 집는다.
--   그래서 신선도 계약은 종전과 같고, 바뀌는 것은 "같은 답일 때 payload 를 다시 쓰지 않는다" 하나다.
--
-- ★왜 jsonb 비교가 아니라 text 비교인가: jsonb 의 `=`/`is not distinct from` 은 의미 비교다.
--   실측: `'{"a":1.0}'::jsonb is not distinct from '{"a":1.00}'::jsonb` = true 인데 ::text 는
--   `{"a": 1.0}` vs `{"a": 1.00}` 로 다르다. 공개 RPC 응답은 jsonb::text(PostgREST 본문)라, jsonb 비교로
--   건너뛰면 바이트가 다른 옛 값을 계속 내보낼 수 있다(byte-identical 계약 위반). text 비교는 정확히 그 바이트다.
--   비용 실측(196행 자기 대조, EXPLAIN ANALYZE): jsonb 비교 0.21s · text 비교 1.49s(행당 ~7.6ms) ·
--   md5(text) 비교 0.89s. 행당 원 계산 평균 606ms(daily)·3,293ms(hot) 대비 1% 안팎이라 정확한 쪽을 고른다.
--   md5 는 저장된 해시가 없어 양쪽을 직렬화한 뒤 해시까지 더 하는 셈이고, 충돌 가능성만 보탠다.
--
-- ★이 migration 이 하지 않는 것: 이미 생긴 죽은 TOAST(약 14.7 MiB)는 회수하지 않는다(파일 크기는
--   표 재작성으로만 준다). 재작성(VACUUM FULL)은 이 migration 적용 **뒤** 별도 점검 창에서 사람이 한다
--   (PR 본문 '적용 절차'). 순서가 반대면 재작성 직후부터 다시 부푼다. findings_similar_cache(092) 의
--   같은 구조는 범위 밖(설계서 A안 부기 — 별도 PR).
--
-- ★검증법(운영자, 적용 전후):
--   (a) 값 동치 — 적용 전에 캡처, 적용·다음 갱신 뒤 같은 질의로 다시 받아 비교:
--       select args, md5(payload::text) from public.findings_search_cache order by args;   -- 196행
--       begin; set local role anon;
--         select md5(public.findings_search()::text),
--                md5(public.findings_search(p_orig_lang := 'en')::text),
--                md5(public.findings_search(p_q := 'CAPA', p_text_only := true)::text),
--                md5(public.findings_search(p_q := '변경관리', p_text_only := true)::text);
--       rollback;
--       → 그 사이 findings 가 안 바뀐 행은 md5 가 전부 같아야 한다(갱신이 건너뛴 행도, 쓴 행도).
--   (b) 쓰기 감소 — hot 잡(:05/:25/:45) 한 번 전후로 두 줄을 읽어 차이를 본다:
--       select n_tup_upd, n_tup_hot_upd, n_dead_tup from pg_stat_user_tables
--        where relid = 'public.findings_search_cache'::regclass;
--       select n_tup_ins, n_tup_del, n_dead_tup from pg_stat_all_tables
--        where relid = (select reltoastrelid from pg_class where oid = 'public.findings_search_cache'::regclass);
--       → 적용 뒤 payload 가 같은 회차: n_tup_upd +2 · n_tup_hot_upd +2 · TOAST n_tup_ins +0 · n_tup_del +0.
--         (적용 전에는 같은 회차에 TOAST n_tup_ins 가 행당 수십 조각씩 늘었다.) refreshed_at 은 계속 새로워야 한다:
--       select tier, min(refreshed_at), max(refreshed_at), count(*) filter (where last_error is not null)
--         from public.findings_search_cache group by tier;
--   (c) 권한 불변:
--       select routine_name, grantee from information_schema.routine_privileges
--        where routine_name = 'findings_search_cache_refresh_row' order by 2;
--       → postgres·service_role 만(anon·authenticated·PUBLIC 행 없음 — 적용 전과 같다).
--
-- ★적용 순서: (a) 캡처 → 이 파일 적용(함수 하나 create or replace, 잠금 순간) → 다음 hot 회차 뒤 (b)·(c)
--   → 다음 daily 배치(03:10~03:40 UTC) 뒤 (a) 재대조 → 그 뒤 별도 점검 창에서 VACUUM FULL(PR 본문).
--
-- ★되돌리기: 151 의 본문으로 다시 정의한다(아래 전문 그대로 실행).
--   create or replace function public.findings_search_cache_refresh_row(p_args jsonb)
--   returns text
--   language plpgsql security invoker set search_path to 'public', 'extensions' as $$
--   declare
--     t0  timestamptz;
--     v   jsonb;
--     ms  integer;
--   begin
--     if public.findings_search_cache_args(
--          p_args->>'p_q', p_args->>'p_source', p_args->>'p_category', p_args->>'p_month',
--          p_args->>'p_evidence', p_args->>'p_review_status', p_args->>'p_agency', p_args->>'p_sort',
--          (p_args->>'p_page')::integer, (p_args->>'p_docs_per_page')::integer,
--          p_args->>'p_country', p_args->>'p_orig_lang', (p_args->>'p_text_only')::boolean) <> p_args
--     then
--       raise exception 'args not normalized';
--     end if;
--     t0 := clock_timestamp();
--     execute 'set local role anon';
--     v := public.findings_search_compute(
--            p_args->>'p_q', p_args->>'p_source', p_args->>'p_category', p_args->>'p_month',
--            p_args->>'p_evidence', p_args->>'p_review_status', p_args->>'p_agency', p_args->>'p_sort',
--            (p_args->>'p_page')::integer, (p_args->>'p_docs_per_page')::integer,
--            p_args->>'p_country', p_args->>'p_orig_lang', (p_args->>'p_text_only')::boolean);
--     execute 'reset role';
--     ms := (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer;
--     update public.findings_search_cache
--        set payload = v, computed_ms = ms, refreshed_at = clock_timestamp(), last_error = null
--      where args = p_args;
--     return null;
--   exception when others then
--     execute 'reset role';
--     update public.findings_search_cache set last_error = sqlerrm where args = p_args;
--     return sqlerrm;
--   end;
--   $$;
--   revoke execute on function public.findings_search_cache_refresh_row(jsonb) from public, anon, authenticated;
--   (되돌려도 데이터는 그대로다 — 이 migration 은 표·행을 바꾸지 않는다.)

-- ── 1. 한 행 갱신 — 151 본문 + "같은 답이면 payload 를 다시 쓰지 않는다" ───────────────────
-- 반환: null = 성공, 그 밖 = 오류 메시지(그 행의 last_error 에도 남는다). 예외를 밖으로 던지지 않는다.
-- security invoker 인 이유는 086 머리(definer 안에서는 set role 불가). 호출자는 postgres(pg_cron)뿐.
create or replace function public.findings_search_cache_refresh_row(p_args jsonb)
returns text
language plpgsql security invoker set search_path to 'public', 'extensions' as $$
declare
  t0  timestamptz;
  v   jsonb;
  ms  integer;
begin
  -- 손으로 넣은 비정규 행은 계산하지 않는다 — 키가 다르면 캐시가 영영 안 맞는다.
  if public.findings_search_cache_args(
       p_args->>'p_q', p_args->>'p_source', p_args->>'p_category', p_args->>'p_month',
       p_args->>'p_evidence', p_args->>'p_review_status', p_args->>'p_agency', p_args->>'p_sort',
       (p_args->>'p_page')::integer, (p_args->>'p_docs_per_page')::integer,
       p_args->>'p_country', p_args->>'p_orig_lang', (p_args->>'p_text_only')::boolean) <> p_args
  then
    raise exception 'args not normalized';
  end if;
  t0 := clock_timestamp();
  execute 'set local role anon';
  v := public.findings_search_compute(
         p_args->>'p_q', p_args->>'p_source', p_args->>'p_category', p_args->>'p_month',
         p_args->>'p_evidence', p_args->>'p_review_status', p_args->>'p_agency', p_args->>'p_sort',
         (p_args->>'p_page')::integer, (p_args->>'p_docs_per_page')::integer,
         p_args->>'p_country', p_args->>'p_orig_lang', (p_args->>'p_text_only')::boolean);
  execute 'reset role';
  ms := (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer;
  -- 156: 바이트까지 같은 답이면 payload 는 SET 에서 뺀다(TOAST 를 다시 쓰지 않는다). 신선도는 종전대로 갱신.
  -- jsonb 비교가 아니라 text 비교 — jsonb 는 1.0 과 1.00 을 같다고 본다(머리 주석).
  update public.findings_search_cache
     set computed_ms = ms, refreshed_at = clock_timestamp(), last_error = null
   where args = p_args
     and payload::text is not distinct from v::text;
  if not found then
    update public.findings_search_cache
       set payload = v, computed_ms = ms, refreshed_at = clock_timestamp(), last_error = null
     where args = p_args;
  end if;
  return null;
exception when others then
  execute 'reset role';
  update public.findings_search_cache set last_error = sqlerrm where args = p_args;
  return sqlerrm;
end;
$$;
revoke execute on function public.findings_search_cache_refresh_row(jsonb) from public, anon, authenticated;
