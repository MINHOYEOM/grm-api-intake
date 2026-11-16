-- 158 findings_similar_cache 갱신 — 새 payload 가 기존과 바이트까지 같으면 payload 를 다시 쓰지 않는다.
-- 156(검색 캐시)과 같은 규칙을 유사 사례 캐시(092)에 넓힌다. Max local web/migrations prefix was 156 on main
-- (157 은 다른 세션 몫으로 비워 둔다). Additive only — 함수 시그니처·반환(jsonb: target·computed·error·pruned·
-- elapsed_ms)·security invoker·search_path·권한 불변. 공개 RPC(findings_similar_to)·status·targets 는 손대지 않는다.
-- 설계: GRM_DB용량축소_설계_2026-11-13.md 의 A안 부기("findings_similar_cache 의 upsert 도 같은 방식").
--
-- ★왜(2026-11-13 05:00 UTC 전후 실측, 읽기 전용 SELECT):
--   findings_similar_cache 2,604행 표 전체 11,460,608 B(10.9 MiB)
--     = heap 3,686,400 B + TOAST 7,127,040 B(TOAST heap 6,832,128 + TOAST 인덱스 270,336) + 인덱스 614,400 B.
--   살아 있는 payload 합 sum(pg_column_size(payload)) = 7,598,479 B — 그중 TOAST 로 나간 것(>2,000 B) 946행
--   5,485,866 B, heap 안 1,659행 2,120,182 B. → TOAST heap 의 약 1.3 MiB, heap 의 약 1.3 MiB 가 죽은 판·빈자리.
--   pg_stat_user_tables: n_tup_upd 32,213(HOT 0 — refreshed_at 에 인덱스가 있어 HOT 불가) · n_tup_del 982 ·
--   autovacuum 61회. TOAST 관계: n_tup_ins 31,105 · n_tup_del 27,809.
--   쓰는 지점은 `findings_similar_cache_refresh(integer, integer)`(092) 의 insert … on conflict do update 한 곳뿐
--   (라이브 pg_get_functiondef 대조: 본문이 092 파일과 같다. targets·status·findings_similar_to 는 읽기만).
--   cron `grm-findings-similar-cache` 10,30,50 * * * * (하루 72회), 대상 2,197건을 3일 주기로 다시 계산.
--   표본 대조(대상 중 2일 넘은 15행, findings_similar_to_compute(id,5) 를 다시 불러 비교): text 동일 10/15.
--   → 갱신의 약 2/3 는 같은 답을 TOAST 에 다시 쓰고 옛 조각을 지우는 일이다.
--
-- ★무엇을: refresh 루프의 upsert 를 두 갈래로 나눈다(가산).
--   1) 행이 있고 `payload::text is not distinct from v::text` 이면 computed_ms·refreshed_at 만 갱신한다.
--      SET 에 payload 가 없으면 PostgreSQL 은 TOAST 포인터를 그대로 옮긴다 — TOAST 쓰기 0.
--   2) 아니면(행이 없거나 값이 다르면) 종전 insert … on conflict do update 그대로.
--   n_ok·n_err 집계, 14일 prune, 반환 jsonb 는 종전과 같다(같은 답 갈래도 computed 로 센다).
--
-- ★왜 refreshed_at 은 계속 갱신하나(완전 무쓰기 안 기각): 읽는 쪽 전부가 refreshed_at 으로 신선도를 본다.
--   · findings_similar_to(092): `refreshed_at > now() - interval '8 days'` 가 아니면 캐시를 버리고 원 계산 폴백
--     → 안 바꾸면 같은 답인 행이 8일 뒤 낡은 것으로 판정돼 anon 3초 벽 앞의 원 계산으로 떨어진다.
--   · findings_similar_cache_status(092) 의 fresh_target·oldest_age_s.
--   · 이 refresh 의 `refreshed_at < now() - interval '3 days'` 선택·정렬과 14일 prune — 안 바꾸면 같은 행만
--     계속 집고, 14일째 멀쩡한 행을 지운다.
--
-- ★왜 jsonb 비교가 아니라 text 비교인가: jsonb 의 `=`/`is not distinct from` 은 의미 비교다
--   (`'{"a":1.0}'::jsonb = '{"a":1.00}'::jsonb` 는 true, ::text 는 다르다). 공개 RPC 응답은 jsonb::text 라
--   jsonb 비교로 건너뛰면 바이트가 다른 옛 값을 계속 내보낼 수 있다(byte-identical 계약 위반). 156 과 같은 이유.
--   비용: 행당 payload 평균 2,918 B(text 평균 약 6.7 KB) 직렬화 두 번 — 원 계산 평균 2,563 ms 대비 무시할 만하다.
--
-- ★이 migration 이 하지 않는 것: 이미 생긴 죽은 판(약 2.5 MiB)은 회수하지 않는다(파일 크기는 표 재작성으로만
--   준다). 재작성(VACUUM FULL)은 이 migration 적용 **뒤** 별도 점검 창에서 사람이 한다(PR 본문 '적용 절차').
--   finding_similar·_text·_member·_done 은 갱신·삭제가 거의 없어(n_tup_upd 0~13) 같은 낭비가 없다 — 범위 밖.
--
-- ★검증법(운영자, 적용 전후):
--   (a) 값 동치 — 적용 전에 캡처, 적용·다음 갱신 뒤 같은 질의로 다시 받아 비교:
--       select finding_id, md5(payload::text) from public.findings_similar_cache order by finding_id;
--       begin; set local role anon;
--         select finding_id, md5(public.findings_similar_to(finding_id)::text)
--           from (select finding_id from public.findings_similar_cache order by finding_id limit 10) s;
--       rollback;
--       → 그 사이 다시 계산된 행 중 같은 답 갈래는 md5 가 같고, 다른 답 갈래는 원 계산과 같아야 한다.
--   (b) 쓰기 감소 — cron 한 회차(:10/:30/:50) 전후로 두 줄을 읽어 차이를 본다:
--       select n_tup_upd, n_tup_hot_upd, n_dead_tup from pg_stat_user_tables
--        where relid = 'public.findings_similar_cache'::regclass;
--       select n_tup_ins, n_tup_del, n_dead_tup from pg_stat_all_tables
--        where relid = (select reltoastrelid from pg_class where oid = 'public.findings_similar_cache'::regclass);
--       → 적용 뒤 n_tup_upd 증가분 대비 TOAST n_tup_ins 증가분이 적용 전보다 뚜렷이 작다(같은 답 행은 0 조각).
--         refreshed_at 은 계속 새로워야 한다:
--       select public.findings_similar_cache_status();   -- fresh_target 이 target 근처를 유지
--   (c) 권한 불변:
--       select routine_name, grantee from information_schema.routine_privileges
--        where routine_name = 'findings_similar_cache_refresh' order by 2;
--       → postgres·service_role 만(anon·authenticated·PUBLIC 행 없음 — 적용 전과 같다).
--
-- ★적용 순서: (a) 캡처 → 이 파일 적용(함수 하나 create or replace, 잠금 순간) → 다음 cron 회차 뒤 (b)·(c)
--   → 하루 뒤 (a) 재대조 → 그 뒤 별도 점검 창에서 VACUUM FULL public.findings_similar_cache(PR 본문).
--
-- ★되돌리기: 092 의 본문으로 다시 정의한다(아래 전문 그대로 실행).
--   create or replace function public.findings_similar_cache_refresh(
--     p_budget_ms integer default 20000,
--     p_max_rows  integer default 200
--   ) returns jsonb
--   language plpgsql security invoker set search_path to 'public', 'extensions' as $$
--   declare
--     r        record;
--     t_start  timestamptz := clock_timestamp();
--     t0       timestamptz;
--     v        jsonb;
--     n_target integer;
--     n_ok     integer := 0;
--     n_err    integer := 0;
--     n_pruned integer;
--     budget   interval := make_interval(secs => greatest(1000, least(coalesce(p_budget_ms, 20000), 600000)) / 1000.0);
--   begin
--     select count(*) into n_target from public.findings_similar_cache_targets();
--
--     for r in
--       select t.finding_id
--         from public.findings_similar_cache_targets() as t(finding_id)
--         left join public.findings_similar_cache s using (finding_id)
--        where s.finding_id is null or s.refreshed_at < now() - interval '3 days'
--        order by s.refreshed_at nulls first, t.finding_id
--        limit greatest(1, least(coalesce(p_max_rows, 200), 2000))
--     loop
--       exit when clock_timestamp() - t_start > budget;
--       begin
--         t0 := clock_timestamp();
--         v := public.findings_similar_to_compute(r.finding_id, 5);
--         insert into public.findings_similar_cache (finding_id, payload, computed_ms, refreshed_at)
--         values (r.finding_id, v, (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer, clock_timestamp())
--         on conflict (finding_id) do update
--           set payload = excluded.payload, computed_ms = excluded.computed_ms, refreshed_at = excluded.refreshed_at;
--         n_ok := n_ok + 1;
--       exception when others then
--         n_err := n_err + 1;
--       end;
--     end loop;
--
--     with del as (
--       delete from public.findings_similar_cache s
--        where s.refreshed_at < now() - interval '14 days'
--       returning 1
--     )
--     select count(*) into n_pruned from del;
--
--     return jsonb_build_object(
--       'target', n_target, 'computed', n_ok, 'error', n_err, 'pruned', n_pruned,
--       'elapsed_ms', (extract(epoch from (clock_timestamp() - t_start)) * 1000)::integer);
--   end;
--   $$;
--   revoke execute on function public.findings_similar_cache_refresh(integer, integer) from public, anon, authenticated;
--   (되돌려도 데이터는 그대로다 — 이 migration 은 표·행을 바꾸지 않는다.)

-- ── 1. 갱신 — 092 본문 + "같은 답이면 payload 를 다시 쓰지 않는다" ─────────────────────────
-- 호출자는 postgres(pg_cron)뿐. 행마다 서브트랜잭션이라 한 행 실패가 나머지를 막지 않는다(092 그대로).
create or replace function public.findings_similar_cache_refresh(
  p_budget_ms integer default 20000,
  p_max_rows  integer default 200
) returns jsonb
language plpgsql security invoker set search_path to 'public', 'extensions' as $$
declare
  r        record;
  t_start  timestamptz := clock_timestamp();
  t0       timestamptz;
  v        jsonb;
  n_target integer;
  n_ok     integer := 0;
  n_err    integer := 0;
  n_pruned integer;
  budget   interval := make_interval(secs => greatest(1000, least(coalesce(p_budget_ms, 20000), 600000)) / 1000.0);
begin
  select count(*) into n_target from public.findings_similar_cache_targets();

  for r in
    select t.finding_id
      from public.findings_similar_cache_targets() as t(finding_id)
      left join public.findings_similar_cache s using (finding_id)
     where s.finding_id is null or s.refreshed_at < now() - interval '3 days'
     order by s.refreshed_at nulls first, t.finding_id
     limit greatest(1, least(coalesce(p_max_rows, 200), 2000))
  loop
    exit when clock_timestamp() - t_start > budget;
    begin
      t0 := clock_timestamp();
      v := public.findings_similar_to_compute(r.finding_id, 5);
      -- 158: 바이트까지 같은 답이면 payload 는 SET 에서 뺀다(TOAST 를 다시 쓰지 않는다). 신선도는 종전대로 갱신.
      -- jsonb 비교가 아니라 text 비교 — jsonb 는 1.0 과 1.00 을 같다고 본다(머리 주석).
      update public.findings_similar_cache
         set computed_ms = (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer, refreshed_at = clock_timestamp()
       where finding_id = r.finding_id
         and payload::text is not distinct from v::text;
      if not found then
        insert into public.findings_similar_cache (finding_id, payload, computed_ms, refreshed_at)
        values (r.finding_id, v, (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer, clock_timestamp())
        on conflict (finding_id) do update
          set payload = excluded.payload, computed_ms = excluded.computed_ms, refreshed_at = excluded.refreshed_at;
      end if;
      n_ok := n_ok + 1;
    exception when others then
      n_err := n_err + 1;
    end;
  end loop;

  with del as (
    delete from public.findings_similar_cache s
     where s.refreshed_at < now() - interval '14 days'
    returning 1
  )
  select count(*) into n_pruned from del;

  return jsonb_build_object(
    'target', n_target, 'computed', n_ok, 'error', n_err, 'pruned', n_pruned,
    'elapsed_ms', (extract(epoch from (clock_timestamp() - t_start)) * 1000)::integer);
end;
$$;
revoke execute on function public.findings_similar_cache_refresh(integer, integer) from public, anon, authenticated;
