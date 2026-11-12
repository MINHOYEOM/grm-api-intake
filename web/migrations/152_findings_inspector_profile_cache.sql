-- 152 findings_inspector_profile_cache — 실사관 프로파일을 미리 계산해 둔 답으로(식은 페이지 대책).
-- Max local web/migrations prefix was 151 on main 74ee3dfd. Additive only — 공개 RPC 이름·1인자 시그니처·
-- 반환 JSON 불변.
--
-- ★왜(2026-11-12 실측, 읽기 전용 — _ops/reports/GRM_공개RPC_시간초과_진단_2026-11-13.md §2·§3):
--   findings_inspector_profile 은 매 호출 findings_inspector_pairs() 로 FDA 483 전체의 실사관 쌍 1,894개를
--   새로 계산한다. 데워진 상태 195 ms 인데 **식은 첫 호출 5,650 ms** — 공유 버퍼에 있는 페이지가 20초
--   유휴만에 식어 10~30배 느려진다(shared hit 인데 20배). anon statement_timeout=3s 벽에 걸려 24시간에
--   500 이 8건, 화면은 "실사관 정보를 제공하지 않습니다"가 된다. 방문이 드문 사이트라 거의 모든 방문이
--   "식은 첫 호출"이다.
--
-- ★무엇을(092 유사 사례 캐시와 같은 결):
--   1) 캐시 표 findings_inspector_profile_cache(inspector_key → payload). RLS on·정책 없음, 클라이언트 권한 0.
--   2) 원 계산은 이름만 바꾼다: findings_inspector_profile → findings_inspector_profile_compute
--      (070 본문·security definer·search_path 보존, 복제 0). 클라이언트 실행 권한은 거둔다(085 관례).
--   3) 같은 이름·같은 1인자·같은 jsonb 의 공개 RPC 를 새로 만든다: 캐시(24시간 안) → 없으면 원 계산 폴백.
--      캐시가 비거나 cron 이 죽어도 화면은 종전 속도로 돌 뿐 죽지 않는다.
--   4) 갱신 = pg_cron 20분마다(:15/:35/:55 — 085 */20 · 086 :05/:25/:45 · 092 :10/:30/:50 와 엇갈림)
--      **20초 예산**. 대상 = 085 스냅샷 findings_inspector_index() 의 키(2026-11-12 실측 113개). 가장 오래된
--      행부터 예산이 다할 때까지. 113 × 데움 0.2 s ≈ 23 s → 두 번(약 40분)이면 한 바퀴.
--   5) 상태 함수(감시용, payload 없음) — **service_role 만**(초안의 anon grant 는 대표 결정으로 뺐다).
--   ※ 초안 §B(체크리스트 캐시)는 이 파일에 없다 — findings_checklist 가 security invoker(RLS)라 캐시 조합기를
--     anon 이 실행해야 하는데, "새 함수는 anon revoke" 결정과 충돌해 결정 대기.
--
-- ★정확성(캐시 = 원 계산 그대로): 원 계산은 입력을 findings_inspector_key() 로 정규화한 qk 에만 의존하고
--   그 정규화는 멱등이다(key(key(x)) = key(x)). 그래서 compute(x) = compute(key(x)) 이고, 공개 RPC 는
--   key(입력) 행을 읽으므로 대소문자·마침표·공백이 다른 입력도 같은 행이 정확히 맞는다. 별칭 입력
--   (예: 'anh lac')처럼 key 가 113개 밖이면 캐시 행이 없어 원 계산으로 간다(종전과 같음).
--   원 계산 target 의 `limit 1`(정렬 없음)이 갈릴 수 있는 키는 2026-11-12 실측 113개 중 0개.
--   캐시 payload 는 'null'::jsonb(5문서 미만)도 그대로 담는다(SQL NULL 과 다르다 — coalesce 를 통과).
--
-- ★신선도의 대가: 483 소급·번역 반영이 프로파일에 최대 약 40분(행이 24시간 넘게 갱신 실패하면 원 계산
--   폴백) 늦게 보인다. 즉시 반영이 필요하면 postgres 로 refresh 를 한 번 부르면 된다.
--
-- ── 적용 순서(DB 적용은 머지 뒤 별도 — 해밀턴3/4) ─────────────────────────────────────────────
--   0) 적용 **전** 동치 기준 캡처(anon 키, PostgREST, 086 기법 · 같은 파이프라인으로 해시 — 092 줄바꿈 1자
--      오경보 전례): POST /rest/v1/rpc/findings_inspector_profile {"p_inspector_key": K} 응답 본문 md5 를
--      ① index 113개 키 전부 ② 대소문자·마침표 변형 3개(예: 'Ashar P. Parikh') ③ 별칭 키 3개
--      ④ 없는 키 2개(예: 'zz no such', '') 에 대해 저장한다(429·500 은 재시도 후 기록).
--   1) 이 파일 적용(트랜잭션 하나 — 실패하면 rename 까지 전부 롤백).
--   2) 채움: select public.findings_inspector_profile_cache_refresh(60000);  -- 두 번(113행 한 바퀴)
--   3) DB 안 동치(postgres): 캐시 행 = 원 계산 그대로여야 한다 → 0 이어야 한다.
--        select count(*) from public.findings_inspector_profile_cache c
--         where c.payload is distinct from public.findings_inspector_profile_compute(c.inspector_key);
--   4) 적용 **후** 0) 의 같은 조합을 같은 스크립트로 다시 받아 md5 전부 일치.
--   5) 권한: anon·authenticated·PUBLIC 은 공개 RPC 만.
--        select routine_name, grantee from information_schema.routine_privileges
--         where routine_name like 'findings_inspector_profile%' order by 1, 2;
--        → findings_inspector_profile 만 anon·authenticated·service_role. _compute 는 postgres·service_role,
--          _cache_refresh 는 postgres, _cache_status 는 postgres·service_role.
--        select grantee, privilege_type from information_schema.role_table_grants
--         where table_name = 'findings_inspector_profile_cache';   → anon·authenticated 행 없음
--   6) 식은 첫 호출: 30초 넘게 유휴 뒤 anon 으로 1회 — 목표 < 300 ms(종전 5,650 ms).
--   7) 상태: select jsonb_pretty(public.findings_inspector_profile_cache_status());
--        select jobname, schedule, command from cron.job where jobname = 'grm-findings-inspector-profile-cache';
--
-- ── 되돌리기(이 순서로, postgres) ─────────────────────────────────────────────────────────────
--   select cron.unschedule('grm-findings-inspector-profile-cache');
--   drop function public.findings_inspector_profile(text);
--   alter function public.findings_inspector_profile_compute(text) rename to findings_inspector_profile;
--   grant execute on function public.findings_inspector_profile(text) to public, anon, authenticated, service_role;
--   drop function public.findings_inspector_profile_cache_refresh(integer);
--   drop function public.findings_inspector_profile_cache_status();
--   drop table public.findings_inspector_profile_cache;
--   notify pgrst, 'reload schema';
--   (되돌린 뒤 0) 의 md5 를 다시 받아 일치 확인. 070 본문은 rename 으로 보존돼 있어 다시 쓸 SQL 이 없다.)

-- ── 1. 캐시 표. 클라이언트 직접 접근 불가 ─────────────────────────────────────────────────────
create table if not exists public.findings_inspector_profile_cache (
  inspector_key text        primary key,   -- findings_inspector_key() 로 정규화된 키(index 의 키)
  payload       jsonb,                     -- 원 계산 반환 그대로('null'::jsonb 포함). 아직 미계산이면 SQL NULL
  computed_ms   integer     check (computed_ms is null or computed_ms >= 0),
  refreshed_at  timestamptz,
  last_error    text
);
comment on table public.findings_inspector_profile_cache is
  '152: findings_inspector_profile 결과를 findings_inspector_index() 키에 한해 미리 계산한 것. 공개 RPC 가 읽고 findings_inspector_profile_cache_refresh()(pg_cron·postgres) 가 쓴다. 클라이언트 직접 접근 불가.';
alter table public.findings_inspector_profile_cache enable row level security;
revoke all on table public.findings_inspector_profile_cache from public, anon, authenticated;

-- ── 2. 원 계산은 이름만 바꾼다(본문·security definer·search_path 보존). 클라이언트 권한은 거둔다 ──
alter function public.findings_inspector_profile(text) rename to findings_inspector_profile_compute;
revoke execute on function public.findings_inspector_profile_compute(text) from public, anon, authenticated;

-- ── 3. 공개 RPC — 같은 이름·같은 1인자·같은 jsonb. 캐시(24시간 안) → 없으면 원 계산 ──────────
create function public.findings_inspector_profile(p_inspector_key text)
returns jsonb
language sql
stable
security definer
set search_path to 'public'
as $$
  select coalesce(
    (select c.payload
       from public.findings_inspector_profile_cache c
      where c.inspector_key = public.findings_inspector_key(coalesce(p_inspector_key, ''))
        and c.payload is not null
        and c.refreshed_at > now() - interval '24 hours'),
    public.findings_inspector_profile_compute(p_inspector_key));
$$;
revoke all on function public.findings_inspector_profile(text) from public;
grant execute on function public.findings_inspector_profile(text) to anon, authenticated, service_role;

-- ── 4. 갱신 — 대상은 매번 085 스냅샷 index 에서 뽑는다. 예산(ms) 안에서만. 호출자는 postgres 뿐 ──
-- 행 하나가 실패해도 나머지는 계속 간다(행마다 서브트랜잭션). 실패한 행은 옛 payload 가 남고
-- last_error 에 사유를 적는다. index 에서 빠진 지 14일 넘은 행은 지운다.
create or replace function public.findings_inspector_profile_cache_refresh(p_budget_ms integer default 20000)
returns jsonb
language plpgsql security invoker set search_path to 'public' as $$
declare
  r        record;
  t_start  timestamptz := clock_timestamp();
  t0       timestamptz;
  v        jsonb;
  v_keys   text[];
  n_ok     integer := 0;
  n_err    integer := 0;
  n_pruned integer;
  budget   interval := make_interval(secs => greatest(1000, least(coalesce(p_budget_ms, 20000), 600000)) / 1000.0);
begin
  select coalesce(array_agg(distinct e->>'inspector_key'), '{}'::text[])
    into v_keys
    from jsonb_array_elements(coalesce(public.findings_inspector_index(), '[]'::jsonb)) e
   where coalesce(e->>'inspector_key', '') <> '';
  if cardinality(v_keys) = 0 then
    raise exception 'findings_inspector_index() empty — refusing to touch findings_inspector_profile_cache';
  end if;

  insert into public.findings_inspector_profile_cache (inspector_key)
  select k from unnest(v_keys) as u(k)
  on conflict (inspector_key) do nothing;

  with del as (
    delete from public.findings_inspector_profile_cache c
     where not (c.inspector_key = any(v_keys))
       and coalesce(c.refreshed_at, '-infinity'::timestamptz) < now() - interval '14 days'
    returning 1
  )
  select count(*) into n_pruned from del;

  for r in
    select c.inspector_key
      from public.findings_inspector_profile_cache c
     where c.inspector_key = any(v_keys)
     order by c.refreshed_at asc nulls first, c.inspector_key
  loop
    exit when clock_timestamp() - t_start > budget;
    begin
      t0 := clock_timestamp();
      v  := public.findings_inspector_profile_compute(r.inspector_key);
      update public.findings_inspector_profile_cache
         set payload      = v,
             computed_ms  = (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer,
             refreshed_at = clock_timestamp(),
             last_error   = null
       where inspector_key = r.inspector_key;
      n_ok := n_ok + 1;
    exception when others then
      update public.findings_inspector_profile_cache
         set last_error = sqlerrm
       where inspector_key = r.inspector_key;
      n_err := n_err + 1;
    end;
  end loop;

  return jsonb_build_object(
    'target', cardinality(v_keys), 'computed', n_ok, 'error', n_err, 'pruned', n_pruned,
    'elapsed_ms', (extract(epoch from (clock_timestamp() - t_start)) * 1000)::integer);
end;
$$;
revoke execute on function public.findings_inspector_profile_cache_refresh(integer) from public, anon, authenticated;

-- ── 5. 신선도 관측용(운영자·감시기) — payload 는 안 낸다. service_role 만 ──────────────────────
create or replace function public.findings_inspector_profile_cache_status()
returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select jsonb_build_object(
    'rows',            count(*),
    'fresh',           count(*) filter (where payload is not null and refreshed_at > now() - interval '24 hours'),
    'oldest_age_s',    (extract(epoch from (now() - min(refreshed_at))))::integer,
    'errors',          count(*) filter (where last_error is not null),
    'max_computed_ms', max(computed_ms),
    'table_bytes',     pg_total_relation_size('public.findings_inspector_profile_cache'))
  from public.findings_inspector_profile_cache;
$$;
revoke execute on function public.findings_inspector_profile_cache_status() from public, anon, authenticated;
grant execute on function public.findings_inspector_profile_cache_status() to service_role;

-- ── 6. pg_cron 20분마다(:15/:35/:55) 20초 예산. 같은 잡 이름 재호출은 스케줄 갱신(멱등) ────────
create extension if not exists pg_cron;
select cron.schedule('grm-findings-inspector-profile-cache', '15,35,55 * * * *',
                     $c$select public.findings_inspector_profile_cache_refresh(20000)$c$);

-- 첫 채움은 마이그레이션에 묶지 않는다 — 적용 뒤 운영자가 위 "적용 순서 2)" 로 부르거나 cron 에 맡긴다(약 40분).
notify pgrst, 'reload schema';
