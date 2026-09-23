-- 086 findings_search_cache — 검색 RPC 의 "자주 오는 질의"를 매 방문 재계산에서 미리 계산해 둔 답으로.
-- Max local web/migrations prefix was 085 on main 5316e89. Additive only — 시그니처·반환 JSON·권한 불변.
--
-- ★왜(R&D 과제 R2 · 2026-09-23 실측): `findings_search` 는 결과 캐시가 없어 같은 질의도 매번
--   다시 계산한다. 라이브(anon 키, 한국에서 curl keep-alive) 실측:
--     · 기본 목록 `{}`(= /findings/ 첫 화면이 매번 쏘는 호출)  첫 3.54s · 이후 0.98~1.14s · 190 KB
--     · 용어사전 사례 링크 `{p_q:'CAPA', p_text_only:true}`      첫 2.02s · 이후 0.77~0.88s · 456 KB
--     · `findings_stats`(085 로 스냅샷 캐시)                       0.31~0.54s  ← 전송 오버헤드의 바닥
--   즉 한 호출의 0.6~0.8초가 순수 DB 계산이고(EXPLAIN ANALYZE 웜 717ms · 콜드 1,652ms), 그 계산은
--   질의 인자가 같으면 답도 같다(수집 배치 하루 1회 · 번역 반영 하루 1회). pg_stat_statements 총
--   실행시간 1·3·4·5위가 전부 이 함수의 변형이다. 085 가 무인자 집계 7종에 한 것과 같은 일을,
--   **인자가 있는 검색의 "뜨거운 질의"** 에 한다.
--
-- ★무엇을: 정해진 질의 목록(`findings_search_cache` 표의 행)에 대해 pg_cron 이 **anon 시점**으로
--   결과를 미리 계산해 두고, 공개 RPC `findings_search` 는 (1) 호출자가 anon/authenticated 이고
--   (2) 정규화한 인자와 같은 행이 있고 (3) 그 행이 아직 신선하면 그 payload 를 돌려준다. 셋 중
--   하나라도 아니면 **원 계산으로 폴백**한다 — cron 이 죽어도 화면은 종전 속도로 돌아갈 뿐 죽지
--   않고, 목록에 없는 질의(자유 검색·2페이지 이후·필터 조합)는 종전과 완전히 같다.
--   목록(시드): 기본 목록 2종(국문 `{}` · 영문 `{p_orig_lang:'en'}`) = hot 계층(20분 갱신) +
--   용어사전 사례 링크 192종(`web/data/glossary_cases.json` 의 q · `p_text_only=true`) = daily 계층
--   (하루 1회 12:10 KST — 일일 번역 반영 뒤). 영문 용어사전 사례 링크는 유입이 0 에 가까워 뺐다.
--
-- ★계약 불변(085 와 같은 결): RPC 이름·13인자 시그니처·반환 JSON 이 그대로다(클라이언트·스크립트
--   무수정 — glossary_cases_refresh.py / findings_docs_refresh.py / findings_facets_refresh.py 는 전부
--   anon 키로 이 RPC 를 부르므로 캐시 경로를 타되 결과는 동일). 원 계산 본문은 **rename 으로 보존**
--   한다(`findings_search_compute`) — 본문을 복제해 옮기지 않는다(037→039 정본 복제 표류 방지).
--
-- ★085 와 다른 점 하나 — 정의자 권한이 아니라 **호출자 권한**이다. `findings_search` 는 030 부터
--   `security invoker` 로, 공개 집합을 RLS(`findings_public_read`, anon·authenticated)가 정의한다.
--   캐시가 그 계약을 지키려면 (a) 미리 계산하는 쪽도 anon 으로 계산해야 하고 (b) 읽는 쪽은 anon/
--   authenticated 에게만 캐시를 주고 service_role 등 다른 호출자는 종전 경로로 보내야 한다.
--   (a) 는 갱신 함수가 `set local role anon` 으로 역할을 바꾼 채 원 계산을 부르는 것으로 한다.
--   ★`set role` 은 security definer 함수 안에서 금지된다(실측: `42501 cannot set parameter "role"
--   within security-definer function`). 그래서 갱신 함수는 **security invoker** 이고, 호출자는
--   pg_cron 잡 소유자 postgres(anon 의 멤버·표 소유자)뿐이다. anon 의 `statement_timeout=3s` 는
--   rolconfig 라 `set role` 로는 적용되지 않는다(세션 시작 시에만) — 갱신은 3초 벽을 안 만난다.
--   (b) 는 공개 RPC 의 `case when current_user in ('anon','authenticated')` 한 줄이다.
--   ★캐시 표 자체는 클라이언트가 직접 못 읽는다(RLS 켬·권한 전부 회수). 읽기는 payload 만 돌려
--   주는 정의자 함수 `findings_search_cache_read(jsonb)` 를 거치며, 이 함수는 anon 이 부를 수 있어야
--   한다(공개 RPC 가 invoker 라 anon 으로 실행되므로). 돌려주는 것은 어차피 anon 으로 계산한 공개
--   응답이라 노출 확대가 아니다. 표에 **쓰는** 경로는 갱신 함수 하나(postgres)뿐 — anon 이 만드는
--   쓰기 표면은 0 이다(캐시 오염 불가).
--
-- ★키 정규화: 캐시 키는 `findings_search_cache_args(13인자)` 가 만드는 jsonb 다. 원 계산의 `p` CTE
--   가 하는 정규화(btrim(q)·coalesce·정렬 화이트리스트·page/per 클램프·upper(country)·orig_lang='en'
--   판정·text_only coalesce)를 **그대로 거울**로 옮겼다 — 두 호출이 같은 키로 접히면 원 계산도
--   같은 답을 내야 한다는 것이 캐시가 옳기 위한 유일한 조건이고, 이 함수가 그 조건을 진다.
--   tests/test_findings_search_cache.py 가 082 의 `p` CTE 표현식과 이 함수의 표현식이 같은지
--   문자열로 대조한다(정규화가 한쪽만 바뀌면 빨강).
--
-- ★검증(적용 전후, 운영자): 인자 14조합의 anon PostgREST 응답 md5 를 적용 전에 캡처해 두고,
--   적용·첫 갱신 뒤 같은 14조합을 다시 받아 **전부 동일**해야 한다(캐시 적중 = 계산 결과 그대로,
--   비적중 = 종전 경로). `set local role anon` 으로 DB 안에서 계산한 CAPA/text_only 의
--   md5(`4ec9a761…`)가 anon PostgREST 응답 md5 와 이미 같음을 사전 실측했다(jsonb::text 직렬화 =
--   PostgREST 본문).
--
-- 남는 한계: 신선도가 hot 최대 20분·daily 최대 하루 늦다(용어사전의 "사례 N건" 은 주 1회 갱신이라
-- 그보다 신선하다). 목록은 시드로 굳어 있어 **새 용어**의 사례 링크는 목록에 들기 전까지 종전
-- 속도로 계산된다(다음 단계: glossary_cases 주간 워크플로가 목록을 동기화). 표 크기는 daily 192행
-- × 100~450 KB(jsonb TOAST 압축 전) — 적용 뒤 `findings_search_cache_status().table_bytes` 로 잰다.

-- ── 1. 캐시 표(= 뜨거운 질의 목록 + 그 답). 클라이언트 직접 접근 불가 ─────────────────
create table if not exists public.findings_search_cache (
  args         jsonb       not null,
  tier         text        not null check (tier in ('hot', 'daily')),
  payload      jsonb,
  computed_ms  integer     check (computed_ms is null or computed_ms >= 0),
  refreshed_at timestamptz,
  last_error   text,
  added_at     timestamptz not null default now(),
  primary key (args)
);
comment on table public.findings_search_cache is
  '086: findings_search 의 뜨거운 질의(정규화 인자) 목록과 anon 시점으로 미리 계산한 응답. 공개 RPC 가 읽고 findings_search_cache_refresh()(pg_cron·postgres) 가 쓴다. 클라이언트 직접 접근 불가.';
alter table public.findings_search_cache enable row level security;
revoke all on table public.findings_search_cache from public, anon, authenticated;

-- ── 2. 키 정규화 — 082 `p` CTE 의 거울(표현식을 바꾸면 테스트가 잡는다) ──────────────
create or replace function public.findings_search_cache_args(
  p_q text default ''::text,
  p_source text default ''::text,
  p_category text default ''::text,
  p_month text default ''::text,
  p_evidence text default ''::text,
  p_review_status text default ''::text,
  p_agency text default ''::text,
  p_sort text default 'date_desc'::text,
  p_page integer default 1,
  p_docs_per_page integer default 24,
  p_country text default ''::text,
  p_orig_lang text default ''::text,
  p_text_only boolean default false
) returns jsonb
language sql immutable set search_path to 'public' as $$
  select jsonb_build_object(
    'p_q',             coalesce(btrim(p_q), ''),
    'p_source',        coalesce(p_source, ''),
    'p_category',      coalesce(p_category, ''),
    'p_month',         coalesce(p_month, ''),
    'p_evidence',      coalesce(p_evidence, ''),
    'p_review_status', coalesce(p_review_status, ''),
    'p_agency',        coalesce(p_agency, ''),
    'p_sort',          case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
                            then p_sort else 'date_desc' end,
    'p_page',          least(greatest(coalesce(p_page, 1), 1), 400000),
    'p_docs_per_page', least(greatest(coalesce(p_docs_per_page, 24), 1), 100),
    'p_country',       upper(coalesce(btrim(p_country), '')),
    'p_orig_lang',     case when lower(coalesce(btrim(p_orig_lang), '')) = 'en' then 'en' else '' end,
    'p_text_only',     coalesce(p_text_only, false)
  );
$$;
revoke execute on function public.findings_search_cache_args(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean) from public;
grant execute on function public.findings_search_cache_args(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)
  to anon, authenticated, service_role;

-- ── 3. 원 계산은 이름만 바꾼다(본문·security invoker·search_path·work_mem·권한 보존) ────
-- ★085 와 달리 anon 실행 권한을 회수하지 않는다 — 공개 RPC 가 invoker 라 anon 으로 이 함수를
--   부른다. 직접 호출은 캐시를 건너뛸 뿐 같은 답을 더 느리게 받는 경로라 막을 이유가 없다.
alter function public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)
  rename to findings_search_compute;

-- ── 4. 캐시 읽기 — payload 만, 신선한 행만. anon 이 부를 수 있어야 한다(위 설명) ────────
create or replace function public.findings_search_cache_read(p_args jsonb) returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select c.payload
  from public.findings_search_cache c
  where c.args = p_args
    and c.payload is not null
    and c.refreshed_at > now() - (case c.tier when 'hot' then interval '75 minutes'
                                              else interval '30 hours' end);
$$;
revoke execute on function public.findings_search_cache_read(jsonb) from public;
grant execute on function public.findings_search_cache_read(jsonb) to anon, authenticated, service_role;

-- ── 5. 공개 RPC — 같은 이름·같은 13인자·같은 JSON. 캐시 → 없으면 원 계산 ────────────────
create function public.findings_search(
  p_q text default ''::text,
  p_source text default ''::text,
  p_category text default ''::text,
  p_month text default ''::text,
  p_evidence text default ''::text,
  p_review_status text default ''::text,
  p_agency text default ''::text,
  p_sort text default 'date_desc'::text,
  p_page integer default 1,
  p_docs_per_page integer default 24,
  p_country text default ''::text,
  p_orig_lang text default ''::text,
  p_text_only boolean default false
) returns jsonb
language sql
stable
security invoker
set search_path to 'public', 'extensions'
as $$
  select coalesce(
    case when current_user in ('anon', 'authenticated')
         then public.findings_search_cache_read(public.findings_search_cache_args(
                p_q, p_source, p_category, p_month, p_evidence, p_review_status, p_agency,
                p_sort, p_page, p_docs_per_page, p_country, p_orig_lang, p_text_only))
    end,
    public.findings_search_compute(
      p_q, p_source, p_category, p_month, p_evidence, p_review_status, p_agency,
      p_sort, p_page, p_docs_per_page, p_country, p_orig_lang, p_text_only));
$$;
revoke execute on function public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean) from public;
grant execute on function public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean)
  to anon, authenticated, service_role;

-- ── 6. 갱신 — 계층별로 목록을 돌며 anon 시점으로 계산해 저장. 호출자는 postgres(pg_cron)뿐 ──
-- security invoker 인 이유는 파일 머리의 ★085 와 다른 점 참조(definer 안에서는 set role 불가).
-- 행 하나가 실패해도 나머지는 계속 간다(행마다 서브트랜잭션) — 실패한 행은 last_error 에 남고
-- 그 행의 payload/refreshed_at 은 그대로라(낡으면 읽기가 거른다) 화면은 폴백으로 간다.
create or replace function public.findings_search_cache_refresh(p_tier text default 'daily')
returns jsonb
language plpgsql security invoker set search_path to 'public', 'extensions' as $$
declare
  r      record;
  t0     timestamptz;
  v      jsonb;
  ms     integer;
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
    begin
      -- 손으로 넣은 비정규 행은 계산하지 않는다 — 키가 다르면 캐시가 영영 안 맞는다.
      if public.findings_search_cache_args(
           r.args->>'p_q', r.args->>'p_source', r.args->>'p_category', r.args->>'p_month',
           r.args->>'p_evidence', r.args->>'p_review_status', r.args->>'p_agency', r.args->>'p_sort',
           (r.args->>'p_page')::integer, (r.args->>'p_docs_per_page')::integer,
           r.args->>'p_country', r.args->>'p_orig_lang', (r.args->>'p_text_only')::boolean) <> r.args
      then
        raise exception 'args not normalized';
      end if;
      t0 := clock_timestamp();
      execute 'set local role anon';
      v := public.findings_search_compute(
             r.args->>'p_q', r.args->>'p_source', r.args->>'p_category', r.args->>'p_month',
             r.args->>'p_evidence', r.args->>'p_review_status', r.args->>'p_agency', r.args->>'p_sort',
             (r.args->>'p_page')::integer, (r.args->>'p_docs_per_page')::integer,
             r.args->>'p_country', r.args->>'p_orig_lang', (r.args->>'p_text_only')::boolean);
      execute 'reset role';
      ms := (extract(epoch from (clock_timestamp() - t0)) * 1000)::integer;
      update public.findings_search_cache
         set payload = v, computed_ms = ms, refreshed_at = clock_timestamp(), last_error = null
       where args = r.args;
      n_ok := n_ok + 1;
    exception when others then
      execute 'reset role';
      n_err := n_err + 1;
      errs := errs || jsonb_build_object('args', r.args, 'error', sqlerrm);
      update public.findings_search_cache set last_error = sqlerrm where args = r.args;
    end;
  end loop;
  return jsonb_build_object('tier', p_tier, 'ok', n_ok, 'error', n_err, 'errors', errs);
end;
$$;
revoke execute on function public.findings_search_cache_refresh(text) from public, anon, authenticated;

-- 신선도 관측용(운영자·프로브): 행 수·신선한 행 수·계층별 최고령·최장 계산·표 크기. payload 는 안 낸다.
create or replace function public.findings_search_cache_status() returns jsonb
language sql stable security definer set search_path to 'public' as $$
  select jsonb_build_object(
    'rows',  count(*),
    'fresh', count(*) filter (where payload is not null and refreshed_at > now() -
               (case tier when 'hot' then interval '75 minutes' else interval '30 hours' end)),
    'hot_rows',           count(*) filter (where tier = 'hot'),
    'hot_oldest_age_s',   max(extract(epoch from (now() - refreshed_at)))  filter (where tier = 'hot')::integer,
    'daily_rows',         count(*) filter (where tier = 'daily'),
    'daily_oldest_age_s', max(extract(epoch from (now() - refreshed_at)))  filter (where tier = 'daily')::integer,
    'errors',             count(*) filter (where last_error is not null),
    'max_computed_ms',    max(computed_ms),
    'table_bytes',        pg_total_relation_size('public.findings_search_cache'))
  from public.findings_search_cache;
$$;
revoke execute on function public.findings_search_cache_status() from public;
grant execute on function public.findings_search_cache_status() to anon, authenticated, service_role;

-- ── 7. 시드 — 기본 목록 2종(hot) + 용어사전 사례 링크 192종(daily) ──────────────────────
-- daily 목록은 web/data/glossary_cases.json(2026-09-23 커밋본) 의 `q` 전량이다. 렌더러는 그 q 를
-- `findings/?q=<q>&text=1` 로 링크하고 findings.js 는 `p_text_only=true` 로 부른다 — 여기 넣는
-- 인자가 그 호출과 같은 키로 접히도록 같은 정규화 함수를 통과시킨다.
insert into public.findings_search_cache (args, tier) values
  (public.findings_search_cache_args(), 'hot'),
  (public.findings_search_cache_args(p_orig_lang := 'en'), 'hot'),
  (public.findings_search_cache_args(p_q := 'ALCOA', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'API Starting Material', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Acceptance Criteria', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Access Control', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Accuracy', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Action Limit', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Active Ingredient', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Airlock', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Alert Limit', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Aseptic Processing', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Assay', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Audit Trail', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Audit Trail Review', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Authorized Person', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'BFS', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Batch', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Batch Records', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Batch Release', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Bioburden', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Biological Indicator', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Bulk Product', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'CAPA', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Change Management', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Cleaning Validation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Cleanroom', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Cleanroom Classification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Closed System', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Container Closure Integrity', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Contemporaneous', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Contract Manufacturer', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Corrective Action', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Critical Intervention', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Depyrogenation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Detectability', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Deviation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Electronic Record', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Endotoxin', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Environmental Monitoring', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Expiration Date', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Filter Integrity Test', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'First Air', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'GMP', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'HEPA', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Hazard', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Isolator', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Legible', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Line Clearance', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Lot Number', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Manufacturer', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Media Fill', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Operational Qualification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Out-of-Specification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Out-of-Trend', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Outsourced Activities', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'PUPSIT', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Packaging Material', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Performance Qualification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Personnel Monitoring', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Pharmaceutical Quality System', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Precision', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Preventive Action', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Process Validation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Product Complaint', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Pyrogen', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Qualification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Quality Assurance', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Quality Manual', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Quarantine', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'RABS', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Raw Material', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Recall', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Repeatability', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Reprocessing', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Retest Date', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Risk Assessment', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Robustness', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Sanitation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Severity', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Site Master File', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Specification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Stability Testing', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'State of Control', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Sterility Assurance Level', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Supplier Qualification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'True Copy', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Unidirectional Airflow', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Validation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Validation Protocol', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Validation Report', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'Worst Case', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'adverse event', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'air handling', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'annual product review', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'archive', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'complaint handling', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'computerized system', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'container closure', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'contingency plan', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'critical area', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'distribution record', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'equipment qualification', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'field alert', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'good documentation practice', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'in-process control', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'labeling control', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'laboratory controls', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'laboratory investigation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'master production', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'method validation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'microbial limit', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'mix-up', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'non-conformance', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'ongoing stability', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'paper and electronic', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'preventive maintenance', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'purified water', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'quality agreement', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'quality control unit', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'quality system', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'reconciliation', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'reserve sample', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'root cause', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'sampling plan', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'shelf life', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'smoke study', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'sporicidal', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'test method', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'usage log', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'visual inspection', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'water system', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := 'written procedure', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '검출한계', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '교육훈련', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '교정', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '교차오염', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '기록', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '기준서', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '데이터 거버넌스', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '데이터 보존', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '데이터 완전성', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '데이터 이관', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '동결건조', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '메타데이터', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '멸균', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '무균성', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '무균시험', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '반품', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '백업', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '변경관리', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '불순물', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '불순물 프로파일', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '비생균 입자 모니터링', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '생산', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '설치적격성평가', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '소독', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '수율', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '시간 제한', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '시험성적서', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '시험용 검체', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '양압', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '영업자 회수', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '오염', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '오염관리전략', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '완제의약품', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '완제품', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '원기록', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '원료의약품', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '원자료', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '위해성', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '위험', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '위험 감소', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '위험 분석', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '위험 평가', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '위험기반 의사결정', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '자율점검', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '재밸리데이션', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '재작업', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '중간제품', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '중간체', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '지속적 개선', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '직무기술서', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '최종멸균', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '출발물질', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '특이성', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '포장', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '표준품', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '품목허가', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '품질관리', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '품질부서', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '품질위험관리', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '해충', p_text_only := true), 'daily'),
  (public.findings_search_cache_args(p_q := '확인시험', p_text_only := true), 'daily')
on conflict (args) do nothing;

-- ── 8. pg_cron — hot 20분(085 의 :00/:20/:40 과 겹치지 않게 :05/:25/:45) · daily 12:10 KST ──
-- 같은 잡 이름 재호출은 스케줄 갱신(멱등, 071/085 관례). 잡 소유자 = 이 파일을 적용하는 postgres.
create extension if not exists pg_cron;
select cron.schedule('grm-findings-search-cache-hot',   '5,25,45 * * * *', $c$select public.findings_search_cache_refresh('hot')$c$);
select cron.schedule('grm-findings-search-cache-daily', '10 3 * * *',      $c$select public.findings_search_cache_refresh('daily')$c$);

-- ── 9. hot 2종은 지금 채운다(적용 직후부터 첫 화면이 캐시 경로) + PostgREST 스키마 캐시 갱신 ──
-- daily 192종(약 3분)은 적용 뒤 운영자가 `select public.findings_search_cache_refresh('daily')` 로
-- 한 번 채운다 — 마이그레이션 한 문장에 3분짜리 계산을 묶지 않는다(적용 도구 타임아웃·롤백 범위).
select public.findings_search_cache_refresh('hot');
notify pgrst, 'reload schema';
