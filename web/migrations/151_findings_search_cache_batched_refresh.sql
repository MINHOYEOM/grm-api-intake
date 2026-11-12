-- 151 findings_search_cache 갱신을 나눠 커밋 — 한 번의 시간 초과가 194행을 전부 되돌리지 않게.
-- Max local web/migrations prefix was 150 on main. Additive only — 086 의 공개 RPC·갱신 함수 시그니처·반환 JSON 불변.
--
-- ★왜(2026-11-12 실측, 읽기 전용): daily 갱신 잡(grm-findings-search-cache-daily, jobid 5)이 11-05~11-10 은
--   79~89초로 성공하다가 **11-11·11-12 에 정확히 120초에서 실패**했다(`canceling statement due to statement
--   timeout`). postgres 세션의 statement_timeout 이 서버 기본 2min 이고, 갱신이 함수 호출 한 문장이라
--   넘는 순간 **194행 전부가 롤백**된다. 194행 × 행당 ~0.6s(findings 28,651행) ≈ 117s — 데이터가 늘면서
--   벽을 넘었다. 결과: 신선한 daily 행 0 → 용어 사례 검색이 전부 원 계산으로 폴백 → anon 3초 벽에
--   첫 호출이 걸렸다(라이브 /search/?q=청정실 첫 시도 57014 두 번).
--
-- ★무엇을:
--   1) `findings_search_cache_refresh_row(jsonb)` — 한 행 갱신(086 갱신 함수의 루프 몸통을 그대로 옮긴
--      **단일 원천**). 086 의 `findings_search_cache_refresh(text)` 도 이 함수를 부르게 다시 정의한다
--      (같은 시그니처·같은 반환 — 몸통 복제 표류 방지, 037→039 교훈).
--   2) `findings_search_cache_refresh_batch(text, integer)` — **가장 오래된 행부터 p_limit 행만** 갱신.
--      잡 한 번 = 트랜잭션 하나라, 실패해도 잃는 것은 그 배치뿐이고 다음 배치가 남은 행을 이어 받는다.
--   3) daily 잡을 03:10·03:20·03:30·03:40 UTC 네 번 × 60행으로 바꾼다(240 ≥ 194행 · 배치당 ~40s).
--      명령 앞의 `set local statement_timeout = '10min'` 은 11-12 비서실이 잡 5 에 넣은 즉시 조치를
--      그대로 기록한 것이다(PostgreSQL 은 문장 시작 때 타이머를 건다 — 함수 안의 set 은 그 호출에
--      안 걸린다, 128 머리 참조. 여러 문장 명령은 한 암묵 트랜잭션이라 local 이 다음 문장에 걸린다).
--   4) `findings_search_cache_job_status()` — 감시용(service_role 만). daily 잡의 최근 실행 결과와
--      daily 신선 행 수를 돌려준다. findings_search_cache_health.py(GitHub Actions, 매일)가 읽고
--      실패·신선 0 이면 전용 경보 이슈를 연다(운영 센터 업무로 넘어간다 — 131).
--
-- 적용: 비서실(대표 승인 11-12). 적용 후 확인:
--   select public.findings_search_cache_refresh_batch('daily', 3);          -- 3행만 손으로 돌려 본다
--   select jsonb_pretty(public.findings_search_cache_job_status());
--   select jobid, jobname, schedule, command from cron.job where jobname like 'grm-findings-search-cache-%';
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name in ('findings_search_cache_refresh_row','findings_search_cache_refresh_batch',
--                           'findings_search_cache_job_status') order by 1, 2;
--   → anon·authenticated·PUBLIC 행 없음(job_status 는 service_role 만).
-- 되돌리기:
--   select cron.alter_job((select jobid from cron.job where jobname = 'grm-findings-search-cache-daily'),
--     schedule := '10 3 * * *',
--     command := $c$set local statement_timeout = '10min'; select public.findings_search_cache_refresh('daily')$c$);
--   (086 의 갱신 함수 원문으로 되돌리려면 086 의 6절을 다시 실행한 뒤)
--   drop function if exists public.findings_search_cache_refresh_batch(text, integer);
--   drop function if exists public.findings_search_cache_job_status();
--   drop function if exists public.findings_search_cache_refresh_row(jsonb);

-- ── 1. 한 행 갱신 — 086 루프 몸통의 단일 원천 ──────────────────────────────────────────
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
  update public.findings_search_cache
     set payload = v, computed_ms = ms, refreshed_at = clock_timestamp(), last_error = null
   where args = p_args;
  return null;
exception when others then
  execute 'reset role';
  update public.findings_search_cache set last_error = sqlerrm where args = p_args;
  return sqlerrm;
end;
$$;
revoke execute on function public.findings_search_cache_refresh_row(jsonb) from public, anon, authenticated;

-- ── 2. 086 갱신 함수 — 같은 시그니처·같은 반환, 몸통만 1을 부르게 ────────────────────────
create or replace function public.findings_search_cache_refresh(p_tier text default 'daily')
returns jsonb
language plpgsql security invoker set search_path to 'public', 'extensions' as $$
declare
  r      record;
  e      text;
  n_ok   integer := 0;
  n_err  integer := 0;
  errs   jsonb := '[]'::jsonb;
begin
  if p_tier not in ('hot', 'daily') then
    raise exception 'findings_search_cache_refresh: unknown tier %', p_tier;
  end if;
  for r in
    select c.args from public.findings_search_cache c where c.tier = p_tier order by c.args
  loop
    e := public.findings_search_cache_refresh_row(r.args);
    if e is null then
      n_ok := n_ok + 1;
    else
      n_err := n_err + 1;
      errs := errs || jsonb_build_object('args', r.args, 'error', e);
    end if;
  end loop;
  return jsonb_build_object('tier', p_tier, 'ok', n_ok, 'error', n_err, 'errors', errs);
end;
$$;
revoke execute on function public.findings_search_cache_refresh(text) from public, anon, authenticated;

-- ── 3. 배치 갱신 — 가장 오래된 행부터 p_limit 행 ─────────────────────────────────────────
-- 순서: 한 번도 안 채워진 행 → 가장 오래 묵은 행 → args(결정론). 잡이 여러 번 돌면 앞 배치가
-- 갱신한 행은 뒤로 밀려, 다음 배치가 자연히 남은 행을 받는다(오프셋 상태를 따로 두지 않는다).
create or replace function public.findings_search_cache_refresh_batch(p_tier text, p_limit integer default 60)
returns jsonb
language plpgsql security invoker set search_path to 'public', 'extensions' as $$
declare
  r      record;
  e      text;
  lim    integer := greatest(1, least(coalesce(p_limit, 60), 500));
  n_ok   integer := 0;
  n_err  integer := 0;
  errs   jsonb := '[]'::jsonb;
begin
  if p_tier not in ('hot', 'daily') then
    raise exception 'findings_search_cache_refresh_batch: unknown tier %', p_tier;
  end if;
  for r in
    select c.args from public.findings_search_cache c
     where c.tier = p_tier
     order by c.refreshed_at asc nulls first, c.args
     limit lim
  loop
    e := public.findings_search_cache_refresh_row(r.args);
    if e is null then
      n_ok := n_ok + 1;
    else
      n_err := n_err + 1;
      errs := errs || jsonb_build_object('args', r.args, 'error', e);
    end if;
  end loop;
  return jsonb_build_object('tier', p_tier, 'limit', lim, 'ok', n_ok, 'error', n_err, 'errors', errs);
end;
$$;
revoke execute on function public.findings_search_cache_refresh_batch(text, integer) from public, anon, authenticated;

-- ── 4. 감시용 상태 — service_role 만 ────────────────────────────────────────────────────
-- cron 스키마는 postgres 만 읽는다 → definer. 돌려주는 것은 잡 이름·시각·상태·메시지 앞 200자와
-- 캐시 행 수뿐(공개 데이터·비밀 없음). 그래도 공개 표면을 늘릴 이유가 없어 anon 에는 주지 않는다.
create or replace function public.findings_search_cache_job_status()
returns jsonb
language sql stable security definer set search_path to 'public', 'cron' as $$
  select jsonb_build_object(
    'now', now(),
    'daily_rows',  (select count(*) from public.findings_search_cache where tier = 'daily'),
    'daily_fresh', (select count(*) from public.findings_search_cache
                     where tier = 'daily' and payload is not null
                       and refreshed_at > now() - interval '30 hours'),
    'daily_oldest_age_s', (select max(extract(epoch from (now() - refreshed_at)))::integer
                             from public.findings_search_cache where tier = 'daily'),
    'runs', coalesce((
      select jsonb_agg(jsonb_build_object(
               'job', j.jobname, 'start', d.start_time, 'end', d.end_time, 'status', d.status,
               'message', left(coalesce(d.return_message, ''), 200)) order by d.start_time desc)
        from (select * from cron.job_run_details dd
               where dd.jobid in (select jobid from cron.job
                                   where jobname = 'grm-findings-search-cache-daily')
                 and dd.start_time > now() - interval '3 days'
               order by dd.start_time desc limit 12) d
        join cron.job j on j.jobid = d.jobid), '[]'::jsonb));
$$;
revoke execute on function public.findings_search_cache_job_status() from public, anon, authenticated;
grant execute on function public.findings_search_cache_job_status() to service_role;

-- ── 5. daily 잡 — 4회 × 60행, 명령마다 10분 상한(11-12 즉시 조치 기록) ────────────────────
do $$
declare jid bigint;
begin
  select jobid into jid from cron.job where jobname = 'grm-findings-search-cache-daily';
  if jid is null then
    raise notice '151: grm-findings-search-cache-daily 잡이 없다 — cron 변경 생략';
  else
    perform cron.alter_job(jid,
      schedule := '10,20,30,40 3 * * *',
      command  := $c$set local statement_timeout = '10min'; select public.findings_search_cache_refresh_batch('daily', 60)$c$);
  end if;
end $$;
