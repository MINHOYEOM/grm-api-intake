-- ============================================================================
-- 169_extraction_gap_snapshot.sql — [운영 감시 복구 2] extraction_gap_by_source() 를 "부를 때 계산"
--   에서 "pg_cron 이 6시간마다 계산해 두고 RPC 는 읽기만"으로 바꾼다(085 rpc_snapshot 과 같은 길).
--
-- ★왜(2026-11-26, 168 적용 당일): 168 로 계산이 6배 가벼워졌는데도(유휴 웜 ~1.0s) 모니터 재실행
--   run 45764051867 이 PostgREST 왕복 8,426ms(rpc-near-timeout 적색)였다. 같은 함수를 DB 안에서
--   연달아 4번 재면 **15.1s · 19.2s · 9.7s · 1.1s** — 다른 활성 세션 0개인데도 이만큼 흔들린다
--   (공유 인스턴스 CPU 버스트 고갈). 즉 raw_signals 전 행(raw_json 79MB)을 훑는 계산은 얼마나
--   가볍게 고치든 PostgREST 8s(authenticator statement_timeout) 안에 든다는 보장이 없다.
--   계산을 요청 경로에서 빼는 것만이 구조적 해법이다.
--
-- ★무엇을:
--   · 168 의 계산 본문은 rename 으로 보존한다(`extraction_gap_by_source_compute`) — 본문 복제 금지
--     (085 와 같은 원칙. 판정식·계약 시험은 168 정의를 그대로 본다).
--   · 공개 RPC `extraction_gap_by_source()` = rpc_snapshot 의 동결 결과. 스냅샷이 아예 없을 때만
--     원 계산으로 폴백한다. **낡았다고 폴백하지 않는다** — 낡은 스냅샷 대신 다시 8s 벽에 부딪히는
--     것보다, 페이로드의 `generated_at`(계산 시각, 095 부터 있던 키)으로 모니터가 신선도를 판정해
--     적색을 띄우는 편이 낫다(findings_backlog_monitor.py 의 extraction-gap-snapshot-stale).
--   · 갱신 `extraction_gap_snapshot_refresh()` — pg_cron 잡 소유자(postgres)가 6시간마다 부른다.
--     cron 경로는 PostgREST 의 8s 를 타지 않는다. 대신 cron 명령에서 statement_timeout 120s 로
--     상한을 건다(PostgreSQL 은 문장 시작 때 타이머를 건다 — 함수 안 set 은 안 먹는다, 128 머리말).
--     실패하면 이전 스냅샷이 남고, generated_at 이 낡아 모니터가 알린다.
--
-- ★계약 불변: RPC 이름·시그니처·반환 JSON 그대로. 모니터 무수정으로도 동작한다.
-- 적용 후 확인(SQL 에디터):
--   select rpc, computed_ms, refreshed_at from public.rpc_snapshot where rpc = 'extraction_gap_by_source';
--   select (public.extraction_gap_by_source() - 'generated_at')
--        = (public.extraction_gap_by_source_compute() - 'generated_at');   -- true
--   select jobname, schedule, command from cron.job where jobname = 'grm-extraction-gap-snapshot';
-- ============================================================================

-- ── 1. 원 계산(168) 은 이름만 바꾼다(본문·security definer·search_path·소유자·ACL 보존) ──
alter function public.extraction_gap_by_source() rename to extraction_gap_by_source_compute;
revoke execute on function public.extraction_gap_by_source_compute() from public, anon, authenticated;

-- ── 2. 갱신 — 계산해서 rpc_snapshot 에 upsert. 호출자는 pg_cron 잡 소유자(postgres)뿐 ──
create or replace function public.extraction_gap_snapshot_refresh() returns jsonb
language plpgsql security definer set search_path to 'public' as $$
declare
  t0 timestamptz := clock_timestamp();
  v  jsonb;
  ms integer;
begin
  v := public.extraction_gap_by_source_compute();
  ms := (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer;
  insert into public.rpc_snapshot (rpc, args, payload, computed_ms, refreshed_at)
  values ('extraction_gap_by_source', '{}'::jsonb, v, ms, clock_timestamp())
  on conflict (rpc, args) do update
    set payload = excluded.payload, computed_ms = excluded.computed_ms, refreshed_at = excluded.refreshed_at;
  return jsonb_build_object('rpc', 'extraction_gap_by_source', 'ms', ms);
end;
$$;
revoke execute on function public.extraction_gap_snapshot_refresh() from public, anon, authenticated;

-- ── 3. 공개 RPC — 같은 이름·같은 시그니처·같은 JSON. 스냅샷 → 없을 때만 원 계산 ──
create function public.extraction_gap_by_source() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(
    (select s.payload from public.rpc_snapshot s
      where s.rpc = 'extraction_gap_by_source' and s.args = '{}'::jsonb),
    public.extraction_gap_by_source_compute());
$$;
comment on function public.extraction_gap_by_source() is
  '169: extraction_gap_by_source_compute()(168 판정) 의 6시간 스냅샷을 읽는다. 신선도는 payload.generated_at '
  '— 모니터가 판정한다. 스냅샷이 없을 때만 원 계산으로 폴백.';
revoke execute on function public.extraction_gap_by_source() from public;
revoke execute on function public.extraction_gap_by_source() from anon, authenticated;
grant execute on function public.extraction_gap_by_source() to service_role;

-- ── 4. 6시간마다 갱신(UTC 00:40·06:40·12:40·18:40). 같은 잡 이름 재호출은 스케줄 갱신(멱등) ──
create extension if not exists pg_cron;
select cron.schedule(
  'grm-extraction-gap-snapshot', '40 */6 * * *',
  $cmd$set statement_timeout = '120s'; select public.extraction_gap_snapshot_refresh()$cmd$);

-- ── 5. 첫 스냅샷을 지금 채운다 + PostgREST 스키마 캐시 갱신 ──
set statement_timeout = '120s';
select public.extraction_gap_snapshot_refresh();
notify pgrst, 'reload schema';
