-- 지식그래프 — 483 FEI 채우기 함수(101 grm_backfill_findings_fei_since)의 호출당 비용을 줄인다.
-- 함수 본문만 교체한다(시그니처·반환형·결과 동일). 테이블·다른 함수 불변. 이 파일은 행을 쓰지 않는다.
--
-- 문제: 101 의 본문은 p_since 와 무관하게 raw_signals 의 FDA 483 원문(JSON)을 전부 jsonb 로 풀어
-- fei_number 를 뽑은 뒤에야 findings 와 조인한다. 일일 호출(7일 창)에도 전체 483 원문을 읽어
-- 캐시가 식은 첫 호출은 약 2.3초 이상이고, 나머지 단계와 합쳐 PostgREST 타임아웃(약 8초)에 근접한다
-- (11-06 일일 실행이 grm_link_new_findings HTTP 500 으로 한 번 실패).
-- 해법(112 의 WL 함수와 같은 방식): 창 안에서 fei 가 비어 있는 지적의 raw_signal_id 를 먼저 좁힌 뒤
-- 그 원문만 파싱한다. 조건(원문 source·JSON 형태·7~10자리 숫자)과 갱신 대상(fei is null · ingested_at >= p_since)은
-- 101 과 같으므로 결과는 같다.

create or replace function public.grm_backfill_findings_fei_since(p_since timestamptz)
returns integer
language plpgsql
set search_path to 'public'
as $$
declare
  v_rows integer;
begin
  with t as materialized (
    select distinct f0.raw_signal_id
      from public.findings f0
     where f0.fei is null
       and f0.ingested_at >= p_since
  )
  update public.findings f
     set fei = x.fei
    from (
      select r.raw_signal_id,
             (r.raw_json::jsonb ->> 'fei_number') as fei
        from t
        join public.raw_signals r on r.raw_signal_id = t.raw_signal_id
       where r.source = 'FDA 483'
         and r.raw_json like '{%'
         and (r.raw_json::jsonb ->> 'fei_number') ~ '^[0-9]{7,10}$'
    ) x
   where f.raw_signal_id = x.raw_signal_id
     and f.fei is null
     and f.ingested_at >= p_since;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_findings_fei_since(timestamptz) from public, anon, authenticated;
