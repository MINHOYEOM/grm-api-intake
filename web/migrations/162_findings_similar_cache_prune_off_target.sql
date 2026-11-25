-- 162 findings_similar_cache 갱신 — 대상(targets) 밖으로 나간 행을 14일이 아니라 하루 뒤에 지운다 + targets 를 한 번만 계산.
-- Max local web/migrations prefix was 161 on main(89c6dcbc). 162=이 파일·163=크론 기록 보존·164=findings 무변경
-- UPDATE 건너뛰기·165=DB 크기 감시 보정(같은 PR, DB 용량 3단계). Additive only — 함수 시그니처·security invoker·
-- search_path·권한 불변. 반환 jsonb 는 종전 키(target·computed·error·pruned·elapsed_ms) 그대로에 'pruned_off_target'
-- 하나만 더한다. 공개 RPC(findings_similar_to)·status·targets 는 손대지 않는다.
-- 설계: _ops/reports/GRM_DB용량_확보방안_2026-11-20.md §5-1(유사 사례 캐시 가지치기 상설화).
--
-- ★왜(2026-11-20 03:10 UTC 실측, 읽기 전용 SELECT):
--   findings_similar_cache 3,075행 / 표 전체 14,884,864 B. targets(findings_similar_cache_targets()) 2,308건.
--   대상 밖 행 773행, payload 합 1,827,340 B, 가장 오래된 refreshed_at = 2026-11-06 03:30(=14일 prune 경계).
--   대상 밖 행은 refresh 가 다시 계산하지 않으므로(루프는 targets 만 돈다) 14일 prune 까지 그대로 남는다.
--   표 크기 10.18MB(11-14) → 14.88MB(11-20): targets 증가(2,197→2,308)와 함께 대상 밖 행이 14일치까지 쌓인 결과.
--   대상 밖 행이 하루 ~55행씩 생기므로(773/14) 보존을 14일 → 1일로 줄이면 상주 행이 ~770 → ~55~110 으로 준다
--   (~2.5MB 를 표 안 빈자리로 돌려 이후 증가분이 재사용한다. 파일 크기 자체는 VACUUM FULL 로만 준다).
--   targets() 한 번 호출 = 약 2.3초(EXPLAIN ANALYZE 2,356 ms — 검색 캐시 200행 payload 를 jsonb_path 로 푼다).
--   092/158 본문은 count 와 루프에서 두 번 부른다 → 한 번만 계산해 배열로 재사용한다(회차당 ~2.3초 절감, 하루 72회).
--
-- ★무엇을(158 본문에서 바뀌는 곳 셋):
--   1) 시작에 targets 를 배열 v_targets 로 한 번 받는다. n_target = cardinality(v_targets). 루프는 unnest(v_targets).
--      (같은 STABLE 함수의 같은 트랜잭션 안 결과라 집합은 종전과 같다.)
--   2) 루프·158 의 같은 답 건너뛰기·14일 prune 은 그대로.
--   3) 대상 밖 prune(신규): refreshed_at 이 1일보다 오래됐고 v_targets 에 없는 행을 지운다.
--      - n_target = 0 이면 건너뛴다(검색 캐시가 비었거나 읽기 실패 — 캐시를 통째로 지우는 사고 방지).
--      - 1일 유예: 방금 계산한 행, 검색 캐시 재작성(03:10~03:40) 중 잠깐 빠졌다 돌아오는 행을 지키기 위해.
--
-- ★읽는 쪽 영향: findings_similar_to(공개) 는 캐시가 없으면 원 계산으로 폴백한다(092). 대상 밖 finding 은 검색
--   캐시 결과 화면에 더는 나오지 않는 행이라 조회가 드물다. 종전에도 8일 뒤엔 낡음 판정으로 폴백했다.
--
-- ★검증법(운영자, 적용 뒤 다음 cron 회차 :10/:30/:50 이후):
--   select (j->>'target')::int target, (j->>'pruned_off_target')::int off from
--     (select public.findings_similar_cache_refresh(20000, 1) j) s;          -- 수동 1행 회차(선택)
--   with t as (select finding_id from public.findings_similar_cache_targets() as t(finding_id))
--   select count(*) filter (where not exists (select 1 from t where t.finding_id = s.finding_id)) off_target,
--          count(*) total
--     from public.findings_similar_cache s;                                   -- off_target 이 ~100 이하로 준다
--   select public.findings_similar_cache_status();                            -- fresh_target 이 target 근처 유지
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name = 'findings_similar_cache_refresh' order by 2;        -- postgres·service_role 만
--
-- ★되돌리기: 158 파일의 함수 정의(create or replace … $$; + revoke)를 그대로 다시 실행한다. 지워진 대상 밖 행은
--   다시 대상에 들어오면 다음 회차가 계산해 채운다(데이터 손실 없음 — 파생 캐시).

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
  v_targets text[];
  n_target integer;
  n_ok     integer := 0;
  n_err    integer := 0;
  n_pruned integer;
  n_off    integer := 0;
  budget   interval := make_interval(secs => greatest(1000, least(coalesce(p_budget_ms, 20000), 600000)) / 1000.0);
begin
  -- 162: targets 는 한 번만 계산한다(호출당 ~2.3초). 종전 count + 루프 두 번 호출과 같은 집합.
  select coalesce(array_agg(t.finding_id), '{}'::text[]) into v_targets
    from public.findings_similar_cache_targets() as t(finding_id);
  n_target := cardinality(v_targets);

  for r in
    select t.finding_id
      from unnest(v_targets) as t(finding_id)
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

  -- 162: 대상 밖 행은 1일 유예 뒤 지운다. targets 가 비면(검색 캐시 비었음·읽기 실패) 건너뛴다.
  if n_target > 0 then
    with off_del as (
      delete from public.findings_similar_cache s
       where s.refreshed_at < now() - interval '1 day'
         and not exists (select 1 from unnest(v_targets) as u(finding_id) where u.finding_id = s.finding_id)
      returning 1
    )
    select count(*) into n_off from off_del;
  end if;

  return jsonb_build_object(
    'target', n_target, 'computed', n_ok, 'error', n_err, 'pruned', n_pruned,
    'pruned_off_target', n_off,
    'elapsed_ms', (extract(epoch from (clock_timestamp() - t_start)) * 1000)::integer);
end;
$$;
revoke execute on function public.findings_similar_cache_refresh(integer, integer) from public, anon, authenticated;
