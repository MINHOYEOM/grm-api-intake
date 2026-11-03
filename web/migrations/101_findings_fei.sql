-- 지식그래프 1순위 — findings 에 FEI(사이트 번호) 열을 가산하고 483 을 raw_signals 에서 백필.
--
-- 배경: findings.entity_id 는 전 행 공란이고 FEI 는 raw_signals.raw_json(483 원본)에만 있었다.
-- 그래서 483 → FDA 실사 판정(fda_inspections.fei_number) 연결이 업체명 우회(32%)·
-- FEI 우회(65%)뿐이었다. 이 파일은 findings.fei 를 만들어 지적 단위로 사이트를 잇는다.
--
-- 가산만 한다: 열 1개 + 부분 인덱스 1개 + 백필 함수 1개. 기존 함수/RPC/뷰는 건드리지 않는다
-- (반환 shape 불변). 열 추가는 nullable·default 없음이라 메타데이터 변경만이고 테이블 재작성이 없다.
--
-- 유효성 규칙: FEI 는 숫자 7~10자리. 그 밖(빈값 22건, 11자리 4건, 6자리 이하 3건, 공백 포함 1건 —
-- 483 원본 기준)은 NULL 로 둔다. 잘린 값을 억지로 맞추면 다른 사이트에 연결되는 오염이 되므로
-- "모르면 비운다". 10자리 앞 0 이 사라진 7~9자리 값도 있어 원문 숫자를 그대로 보존한다(0 채움 없음).
--
-- 적용 순서: (1) 이 파일 적용 → (2) 아래 grm_backfill_findings_fei() 를 select 로 1회 호출.
-- 함수는 fei is null 인 483 행만 채우며 멱등이다(재실행해도 이미 채운 행은 건드리지 않는다).
-- 신규 483 행은 findings_supabase_append 가 fei 를 함께 쓰도록 코드 쪽에서 채운다(별도 PR 필요 시
-- 이 함수를 주 1회 재호출해도 된다).

alter table public.findings
  add column if not exists fei text;

alter table public.findings
  drop constraint if exists findings_fei_format_chk;
alter table public.findings
  add constraint findings_fei_format_chk
  check (fei is null or fei ~ '^[0-9]{7,10}$');

create index if not exists idx_findings_fei
  on public.findings (fei)
  where fei is not null;

create or replace function public.grm_backfill_findings_fei()
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_rows integer;
begin
  update public.findings f
     set fei = x.fei
    from (
      select r.raw_signal_id,
             (r.raw_json::jsonb ->> 'fei_number') as fei
        from public.raw_signals r
       where r.source = 'FDA 483'
         and r.raw_json like '{%'
         and (r.raw_json::jsonb ->> 'fei_number') ~ '^[0-9]{7,10}$'
    ) x
   where f.raw_signal_id = x.raw_signal_id
     and f.fei is null;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;

revoke all on function public.grm_backfill_findings_fei() from public, anon, authenticated;
