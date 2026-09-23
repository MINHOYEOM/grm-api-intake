-- 085 rpc_snapshot — 무인자 집계 RPC 7종을 "매 방문마다 계산"에서 "20분마다 한 번 계산"으로.
-- Max local web/migrations prefix was 083 on main 6d7a673 (084 는 열린 PR #1055 가 쓴다). Additive only.
--
-- ★왜: 라이브 24시간 로그(2026-09-22)에서 `canceling statement due to statement timeout` 19건,
--   PostgREST 500 이 findings_inspector_index 4·findings_stats 3·findings_search 2·zone/matrix/
--   cfr/recent_window 각 1. anon 역할의 statement_timeout 은 3초인데 pg_stat_statements 실측
--   평균이 findings_stats 789ms(1,660회)·inspector_index 737ms(1,025회)·max 는 전부 2,98x ms
--   (= 3초 벽에 잘린 값). 원인은 함수 자체가 아니다 — 같은 함수를 postgres 로 단독 실행하면
--   79~288ms 다. 지적사항 허브 한 페이지가 RPC 3~7개를 **동시에** 쏘고, 공유 인스턴스에서
--   그 동시성이 각 호출을 3~5배로 늘려 3초를 넘긴다. 방문자는 "데이터를 불러오지 못했습니다"
--   를 본다(사용성 점검 2026-09-21 A3 — 재현 안 됐던 이유는 간헐이라서다).
--
-- ★무엇을: 이 7종은 파라미터가 없거나(p_months=12 고정) 결과가 하루 한 번 바뀐다(수집 배치).
--   결과 JSON 을 `rpc_snapshot` 표에 동결하고 공개 RPC 는 그 표를 읽는다. 표가 비었거나
--   24시간보다 낡으면 **원 계산으로 폴백**한다 — cron 이 죽어도 화면은 느려질 뿐 죽지 않고,
--   첫 적용 순간에도 빈 응답이 없다.
--
-- ★계약 불변: RPC 이름·시그니처·반환 JSON 이 그대로다(클라이언트 무수정). 원 계산 본문은
--   **rename 으로 보존**한다(`*_compute`) — 본문을 복제해 옮기지 않는다(037→039 가 경계한
--   정본 복제 표류 방지). `_compute` 는 클라이언트 실행 권한을 회수해 캐시 우회 경로를 닫는다.
--
-- ★검증(적용 전 dry-run): 7종 각각 `적용 전 payload` = `적용 후 공개 RPC 응답` 을 md5 로
--   대조했다(캐시가 계산 결과 그대로이므로 동일해야 한다).
--
-- 남는 한계: 신선도가 최대 20분 늦다(수집 배치는 하루 1회라 무의미한 지연). cfr_ranking/
-- recent_window 의 `as_of` 는 계산 시각의 날짜다 — 자정 직후 최대 20분간 전날 날짜가 나간다.
-- p_months≠12 호출은 캐시를 안 거치고 종전대로 계산한다(현재 클라이언트는 12 만 쓴다).

-- ── 1. 스냅샷 표 ────────────────────────────────────────────────────────────────
create table if not exists public.rpc_snapshot (
  rpc          text        not null,
  args         jsonb       not null default '{}'::jsonb,
  payload      jsonb       not null,
  computed_ms  integer     not null check (computed_ms >= 0),
  refreshed_at timestamptz not null default now(),
  primary key (rpc, args)
);
comment on table public.rpc_snapshot is
  '085: 무인자 집계 RPC 의 결과 동결. 공개 RPC 가 읽고 rpc_snapshot_refresh()(pg_cron 20분) 가 쓴다. 클라이언트 직접 접근 불가.';
alter table public.rpc_snapshot enable row level security;
revoke all on table public.rpc_snapshot from public, anon, authenticated;

-- ── 2. 원 계산 함수는 이름만 바꾼다(본문·security definer·search_path·소유자 보존) ──
alter function public.findings_stats()                 rename to findings_stats_compute;
alter function public.findings_inspector_index()       rename to findings_inspector_index_compute;
alter function public.findings_zone_category()         rename to findings_zone_category_compute;
alter function public.findings_category_matrix()       rename to findings_category_matrix_compute;
alter function public.fda_inspection_stats()           rename to fda_inspection_stats_compute;
alter function public.findings_cfr_ranking(integer)    rename to findings_cfr_ranking_compute;
alter function public.findings_recent_window(integer)  rename to findings_recent_window_compute;

revoke execute on function public.findings_stats_compute()                from public, anon, authenticated;
revoke execute on function public.findings_inspector_index_compute()      from public, anon, authenticated;
revoke execute on function public.findings_zone_category_compute()        from public, anon, authenticated;
revoke execute on function public.findings_category_matrix_compute()      from public, anon, authenticated;
revoke execute on function public.fda_inspection_stats_compute()          from public, anon, authenticated;
revoke execute on function public.findings_cfr_ranking_compute(integer)   from public, anon, authenticated;
revoke execute on function public.findings_recent_window_compute(integer) from public, anon, authenticated;

-- ── 3. 스냅샷 읽기(내부용) ───────────────────────────────────────────────────────
create or replace function public.rpc_snapshot_read(
  p_rpc text, p_args jsonb default '{}'::jsonb, p_max_age interval default interval '24 hours'
) returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select s.payload
  from public.rpc_snapshot s
  where s.rpc = p_rpc and s.args = p_args and s.refreshed_at > now() - p_max_age;
$$;
revoke execute on function public.rpc_snapshot_read(text, jsonb, interval) from public, anon, authenticated;

-- ── 4. 공개 RPC — 같은 이름·같은 시그니처·같은 JSON. 스냅샷 → 없으면 원 계산 ──
create function public.findings_stats() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(public.rpc_snapshot_read('findings_stats'), public.findings_stats_compute());
$$;
create function public.findings_inspector_index() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(public.rpc_snapshot_read('findings_inspector_index'), public.findings_inspector_index_compute());
$$;
create function public.findings_zone_category() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(public.rpc_snapshot_read('findings_zone_category'), public.findings_zone_category_compute());
$$;
create function public.findings_category_matrix() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(public.rpc_snapshot_read('findings_category_matrix'), public.findings_category_matrix_compute());
$$;
create function public.fda_inspection_stats() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(public.rpc_snapshot_read('fda_inspection_stats'), public.fda_inspection_stats_compute());
$$;
create function public.findings_cfr_ranking(p_months integer default 12) returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(
    public.rpc_snapshot_read('findings_cfr_ranking', jsonb_build_object('p_months', coalesce(p_months, 12))),
    public.findings_cfr_ranking_compute(p_months));
$$;
create function public.findings_recent_window(p_months integer default 12) returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(
    public.rpc_snapshot_read('findings_recent_window', jsonb_build_object('p_months', coalesce(p_months, 12))),
    public.findings_recent_window_compute(p_months));
$$;

-- 권한은 종전과 같다(anon·authenticated·service_role). PUBLIC 은 주지 않는다.
revoke execute on function public.findings_stats()                 from public;
revoke execute on function public.findings_inspector_index()       from public;
revoke execute on function public.findings_zone_category()         from public;
revoke execute on function public.findings_category_matrix()       from public;
revoke execute on function public.fda_inspection_stats()           from public;
revoke execute on function public.findings_cfr_ranking(integer)    from public;
revoke execute on function public.findings_recent_window(integer)  from public;
grant execute on function public.findings_stats()                 to anon, authenticated, service_role;
grant execute on function public.findings_inspector_index()       to anon, authenticated, service_role;
grant execute on function public.findings_zone_category()         to anon, authenticated, service_role;
grant execute on function public.findings_category_matrix()       to anon, authenticated, service_role;
grant execute on function public.fda_inspection_stats()           to anon, authenticated, service_role;
grant execute on function public.findings_cfr_ranking(integer)    to anon, authenticated, service_role;
grant execute on function public.findings_recent_window(integer)  to anon, authenticated, service_role;

-- ── 5. 갱신 — 7종을 순서대로 계산해 upsert. 호출자는 pg_cron 잡 소유자(postgres)뿐 ──
create or replace function public.rpc_snapshot_refresh() returns jsonb
language plpgsql security definer set search_path to 'public' as $$
declare
  r    record;
  t0   timestamptz;
  v    jsonb;
  ms   integer;
  done jsonb := '[]'::jsonb;
begin
  for r in
    select * from (values
      ('findings_stats',            '{}'::jsonb),
      ('findings_inspector_index',  '{}'::jsonb),
      ('findings_zone_category',    '{}'::jsonb),
      ('findings_category_matrix',  '{}'::jsonb),
      ('fda_inspection_stats',      '{}'::jsonb),
      ('findings_cfr_ranking',      '{"p_months": 12}'::jsonb),
      ('findings_recent_window',    '{"p_months": 12}'::jsonb)
    ) as t(rpc, args)
  loop
    t0 := clock_timestamp();
    v := case r.rpc
      when 'findings_stats'           then public.findings_stats_compute()
      when 'findings_inspector_index' then public.findings_inspector_index_compute()
      when 'findings_zone_category'   then public.findings_zone_category_compute()
      when 'findings_category_matrix' then public.findings_category_matrix_compute()
      when 'fda_inspection_stats'     then public.fda_inspection_stats_compute()
      when 'findings_cfr_ranking'     then public.findings_cfr_ranking_compute((r.args->>'p_months')::integer)
      when 'findings_recent_window'   then public.findings_recent_window_compute((r.args->>'p_months')::integer)
    end;
    ms := (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer;
    insert into public.rpc_snapshot (rpc, args, payload, computed_ms, refreshed_at)
    values (r.rpc, r.args, v, ms, clock_timestamp())
    on conflict (rpc, args) do update
      set payload = excluded.payload, computed_ms = excluded.computed_ms, refreshed_at = excluded.refreshed_at;
    done := done || jsonb_build_object('rpc', r.rpc, 'ms', ms);
  end loop;
  return done;
end;
$$;
revoke execute on function public.rpc_snapshot_refresh() from public, anon, authenticated;

-- 신선도 관측용(운영자·프로브): 언제·몇 ms 에 계산됐나. payload 는 내보내지 않는다.
create or replace function public.rpc_snapshot_status() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'rpc', rpc, 'args', args, 'computed_ms', computed_ms,
           'refreshed_at', refreshed_at, 'age_s', extract(epoch from (now() - refreshed_at))::integer)
         order by rpc, args), '[]'::jsonb)
  from public.rpc_snapshot;
$$;
revoke execute on function public.rpc_snapshot_status() from public;
grant execute on function public.rpc_snapshot_status() to anon, authenticated, service_role;

-- ── 6. 20분마다 갱신(pg_cron·UTC 무관). 같은 잡 이름 재호출은 스케줄 갱신(멱등, 071 관례) ──
create extension if not exists pg_cron;
select cron.schedule('grm-rpc-snapshot-refresh', '*/20 * * * *', 'select public.rpc_snapshot_refresh()');

-- ── 7. 첫 스냅샷을 지금 채운다(적용 직후부터 캐시 경로) + PostgREST 스키마 캐시 갱신 ──
select public.rpc_snapshot_refresh();
notify pgrst, 'reload schema';
