-- 092 findings_similar_cache — "이 지적과 유사한 사례" 버튼을 방문자가 보는 지적에 한해 미리 계산해 둔 답으로.
-- Max local web/migrations prefix was 091 on main 5f06b5f. Additive only — 시그니처·반환 JSON·권한 불변.
--
-- ★왜(R&D 과제 R2 · 2026-09-24 실측): `findings_similar_to` 는 087 이후에도 R2 에서 가장 느린 공개 RPC 다.
--   pg_stat_statements(07-03 이후 83일): PostgREST 경유 376회 · 평균 1,365 ms · 최장 2,991 ms
--   (= anon statement_timeout 3초 벽에 잘린 값 — 잘린 호출은 여기 안 잡혀 실제 실패 수는 더 많다).
--   라이브 표본 10건: 평균 0.93 s · 첫 호출 하나는 3.77 s 에 시간 초과 오류로 끝났다. 방문자는 그때
--   "유사 사례를 지금 불러오지 못했습니다"를 본다. 079 가 10.8 s → 1.2 s 로 줄였지만, 질의가 지적 본문
--   500자 그 자체라 코퍼스 72% 가 매치되는 구조는 그대로라 더는 싸게 만들 수 없다(079 머리 참조).
--
-- ★무엇을: 결과를 미리 계산해 두되 **전량이 아니라 방문자가 실제로 보는 지적만** 한다.
--   · 대상 = 086 검색 캐시(`findings_search_cache`)에 실린 응답의 지적 = /findings/ 첫 화면(국문·영문)
--     + 용어사전 "사례 N건 보기" 링크 192종의 결과. 2026-09-24 실측 2,192건. 대상 목록을 따로 두지
--     않고 **매 실행마다 086 payload 에서 다시 뽑는다** — 086·088 이 목록을 바꾸면 자동으로 따라간다.
--   · 전량(공개 25,000건)은 안 한다: 한 건 0.8초 × 25,000 = 주당 5시간 넘는 계산이라 무료 공유
--     인스턴스에서 방문자 RPC 와 겹치면 085 가 고친 동시성 시간 초과를 되살린다.
--   · 갱신 = pg_cron 20분마다(:10/:30/:50 — 085 :00/:20/:40·086 :05/:25/:45 와 엇갈림) **20초 예산**.
--     없는 행부터, 그다음 3일 넘은 행 순으로 예산이 다할 때까지만 계산한다(한 번에 오래 붙잡지 않는다).
--     정상 상태 계산량 = 2,192 ÷ 3일 ≈ 시간당 30건 ≈ 실행당 10건·8초.
--   · 공개 RPC `findings_similar_to` 는 (1) 정규화한 limit 이 5(화면·프로브가 쓰는 값)이고 (2) 그 지적의
--     행이 8일 안에 계산됐으면 그 payload 를 준다. 아니면 **원 계산으로 폴백** — 캐시가 비거나 cron 이
--     죽어도 화면은 종전 속도로 돌 뿐 죽지 않고, 대상 밖 지적(딥링크·2페이지 이후)은 종전과 같다.
--
-- ★계약 불변: 이름·2인자 시그니처·반환 JSON·권한(anon·authenticated·service_role)이 그대로다(클라이언트
--   무수정). 원 계산 본문은 **rename 으로 보존**(`findings_similar_to_compute`) — 079 의 성능 수리 본문을
--   복제하지 않는다(정본 표류 방지). 이 RPC 는 원래 security definer 이고 공개 술어를 본문 안에서 직접
--   건다(RLS 비의존) — 그래서 086 과 달리 역할 전환(`set role`)이 필요 없다. 갱신은 postgres 로 계산한다.
--
-- ★신선도의 대가(정직하게): 캐시된 지적의 유사 사례는 최대 8일(보통 3일 안) 늦다. 그 사이 새로 들어온
--   같은 문구의 지적(일 20건 안팎 중 극히 일부)과 "동일 문구 N개 문서" 숫자의 증가분이 늦게 반영된다.
--   목록이 틀리는 것이 아니라 늦는 것이며, 클릭하면 여는 문서는 전부 실재한다.
--
-- ★검증(적용 전후, 운영자): 표본 10건의 anon PostgREST 응답 md5 를 적용 전에 캡처하고, 적용·채움 뒤
--   같은 10건을 다시 받아 동일해야 한다(캐시 = 계산 결과 그대로). limit≠5 는 캐시를 안 탄다.

-- ── 1. 캐시 표. 클라이언트 직접 접근 불가 ─────────────────────────────────────────────
create table if not exists public.findings_similar_cache (
  finding_id   text        primary key,
  payload      jsonb       not null,
  computed_ms  integer     not null check (computed_ms >= 0),
  refreshed_at timestamptz not null default now()
);
comment on table public.findings_similar_cache is
  '092: findings_similar_to(p_limit=5) 의 결과를 방문자가 보는 지적(086 검색 캐시 payload 의 지적)에 한해 미리 계산한 것. 공개 RPC 가 읽고 findings_similar_cache_refresh()(pg_cron·postgres) 가 쓴다. 클라이언트 직접 접근 불가.';
create index if not exists findings_similar_cache_refreshed_at_idx
  on public.findings_similar_cache (refreshed_at);
alter table public.findings_similar_cache enable row level security;
revoke all on table public.findings_similar_cache from public, anon, authenticated;

-- ── 2. 원 계산은 이름만 바꾼다(본문·security definer·search_path·권한 보존) ─────────────
alter function public.findings_similar_to(text, integer) rename to findings_similar_to_compute;

-- ── 3. 공개 RPC — 같은 이름·같은 2인자·같은 JSON. 캐시(limit 5·8일 안) → 없으면 원 계산 ──
create function public.findings_similar_to(p_finding_id text, p_limit integer default 5)
returns jsonb
language sql
stable
security definer
set search_path to 'public', 'extensions'
as $$
  select coalesce(
    case when greatest(1, least(coalesce(p_limit, 5), 50)) = 5 then
      (select s.payload
         from public.findings_similar_cache s
        where s.finding_id = p_finding_id
          and s.refreshed_at > now() - interval '8 days')
    end,
    public.findings_similar_to_compute(p_finding_id, p_limit));
$$;
revoke all on function public.findings_similar_to(text, integer) from public;
grant execute on function public.findings_similar_to(text, integer) to anon, authenticated, service_role;

-- ── 3-b. 대상 = 086 payload 에 실린 지적 ID(중복 제거). 갱신·상태가 같은 정의를 쓴다(한 곳) ──
-- ★jsonb_path_query 로 ID 만 뽑는다 — jsonb_array_elements 두 겹으로 펼치면 지적 본문 전체를 끌고
--   정렬해 1.28 s·디스크 정렬 22 MB 였고, 이 식은 같은 2,192건을 71 ms 에 낸다(2026-09-24 실측).
create or replace function public.findings_similar_cache_targets() returns setof text
language sql stable security invoker set search_path to 'public' as $$
  select distinct fid
    from (select jsonb_path_query(c.payload, '$.documents[*].findings[*].finding_id') #>> '{}' as fid
            from public.findings_search_cache c
           where c.payload is not null) s
   where coalesce(fid, '') <> '';
$$;
revoke execute on function public.findings_similar_cache_targets() from public, anon, authenticated;

-- ── 4. 갱신 — 대상은 매번 086 payload 에서 뽑는다. 예산(ms) 안에서만 계산. 호출자는 postgres 뿐 ──
-- 행 하나가 실패해도 나머지는 계속 간다(행마다 서브트랜잭션). 실패한 행은 옛 값(또는 없음)이 남아
-- 공개 RPC 가 폴백한다. 14일 넘은 행(대상에서 빠진 지적의 잔재)은 지운다.
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
      insert into public.findings_similar_cache (finding_id, payload, computed_ms, refreshed_at)
      values (r.finding_id, v, (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer, clock_timestamp())
      on conflict (finding_id) do update
        set payload = excluded.payload, computed_ms = excluded.computed_ms, refreshed_at = excluded.refreshed_at;
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

-- 신선도 관측용(운영자·프로브): 대상 수·신선한(8일 안) 대상 수·최고령·최장 계산. payload 는 안 낸다.
create or replace function public.findings_similar_cache_status() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  with target as (
    select finding_id from public.findings_similar_cache_targets() as t(finding_id)
  )
  select jsonb_build_object(
    'target',        (select count(*) from target),
    'fresh_target',  (select count(*) from target t join public.findings_similar_cache s using (finding_id)
                       where s.refreshed_at > now() - interval '8 days'),
    'rows',          (select count(*) from public.findings_similar_cache),
    'oldest_age_s',  (select extract(epoch from (now() - min(refreshed_at)))::integer from public.findings_similar_cache),
    'max_computed_ms', (select max(computed_ms) from public.findings_similar_cache),
    'table_bytes',   pg_total_relation_size('public.findings_similar_cache'));
$$;
revoke execute on function public.findings_similar_cache_status() from public;
grant execute on function public.findings_similar_cache_status() to anon, authenticated, service_role;

-- ── 5. pg_cron 20분마다(:10/:30/:50) 20초 예산. 같은 잡 이름 재호출은 스케줄 갱신(멱등) ──
create extension if not exists pg_cron;
select cron.schedule('grm-findings-similar-cache', '10,30,50 * * * *',
                     $c$select public.findings_similar_cache_refresh(20000, 200)$c$);

-- 첫 채움(2,192건 × 약 0.8초 ≈ 30분)은 마이그레이션에 묶지 않는다 — 적용 뒤 운영자가 예산을 나눠
-- `select public.findings_similar_cache_refresh(120000, 400)` 을 몇 번 부르거나 cron 에 맡긴다(약 하루).
notify pgrst, 'reload schema';
