-- 110 의 grm_backfill_wl_fei_since 최적화 — 함수 본문만 교체(시그니처·반환형·결과 동일).
--
-- 110 은 p_since 와 무관하게 모든 경고서한 raw_signals 의 raw_json 을 정규식으로 훑은 뒤 findings 와 조인했다
-- (일일 호출마다 약 1.9초). 여기서는 findings 를 먼저 좁힌다: source='FDA Warning Letter' · fei is null ·
-- ingested_at >= p_since 인 행의 raw_signal_id 만 대상으로 raw_signals 를 조인해 파싱한다.
-- 같은 편지의 여러 지적은 distinct raw_signal_id 로 한 번만 파싱한다. 규칙(파서·멱등·가산)은 110 과 같다.
-- 전체 백필(grm_backfill_wl_fei, '-infinity')의 결과도 같다.

create or replace function public.grm_backfill_wl_fei_since(p_since timestamptz)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_rows integer;
begin
  with t as materialized (
    select distinct f0.raw_signal_id
      from public.findings f0
     where f0.source = 'FDA Warning Letter'
       and f0.fei is null
       and f0.ingested_at >= p_since
  )
  update public.findings f
     set fei = x.fei
    from (
      select r.raw_signal_id,
             public.grm_wl_fei_from_text(r.raw_json) as fei
        from t
        join public.raw_signals r on r.raw_signal_id = t.raw_signal_id
       where r.source = 'FDA Warning Letter'
         and r.raw_json ~* 'FEI'
    ) x
   where f.raw_signal_id = x.raw_signal_id
     and x.fei is not null
     and f.source = 'FDA Warning Letter'
     and f.fei is null
     and f.ingested_at >= p_since;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_wl_fei_since(timestamptz) from public, anon, authenticated;
