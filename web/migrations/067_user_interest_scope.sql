-- ============================================================================
-- 067 — 관심 범위(기관·분류·국가) 저장층 + 내 범위의 최근 공개 문서 RPC
--
-- 왜: 관심 업체 워치리스트(015)는 "이름을 아는 대상"만 담는다. 그런데 실무자의 관심은
-- 업체보다 넓은 축으로도 잡힌다 — "우리는 무균주사제라 무균보증만 본다", "우리 수탁처가
-- 인도에 있어 인도 건만 본다". 그 축을 저장해 두면 둘러보기 첫 화면이 전체가 아니라
-- **그 사람의 범위에서 최근에 생긴 것**부터 보여줄 수 있다.
--
-- 015 관례 계승(불가침):
--   · 사용자는 auth.users 재사용(별도 user 테이블 없음).
--   · RLS 본인 행만(select/insert/delete own). **update 정책 없음** — 등록/해제만 있는
--     모델이라 수정 경로 자체를 봉쇄한다.
--   · 저장하는 것은 **축의 키뿐**이다(기관 코드·분류 코드·국가 키). 규제 사실·원문·URL 은
--     저장하지 않는다(001 provenance 관례).
--   · 사용자당 상한을 트리거로 강제(015 의 50 과 같은 자리 — 여기는 축 합계 30).
--
-- findings 와 FK 로 묶지 않는다: category_code/country_key 는 generated 컬럼 값이거나
-- 어휘 목록이라 참조 대상 단일 행이 없고, 아직 데이터가 없는 축을 미리 등록하는 것도
-- 유효한 사용 시나리오다(015 가 firm_key 를 FK 로 묶지 않은 것과 같은 이유).
-- ============================================================================

create table if not exists public.user_interest (
  user_id    uuid not null references auth.users(id) on delete cascade,
  -- 축 종류. 어휘를 넓힐 일이 생기면 이 체크를 고치는 것이 유일한 관문이다.
  kind       text not null check (kind in ('agency', 'category', 'country')),
  value      text not null,              -- 불투명 키(기관 코드·분류 코드·국가 키)
  created_at timestamptz not null default now(),
  primary key (user_id, kind, value)
);

alter table public.user_interest enable row level security;

drop policy if exists user_interest_select_own on public.user_interest;
drop policy if exists user_interest_insert_own on public.user_interest;
drop policy if exists user_interest_delete_own on public.user_interest;
create policy user_interest_select_own on public.user_interest
  for select using (auth.uid() = user_id);
create policy user_interest_insert_own on public.user_interest
  for insert with check (auth.uid() = user_id);
create policy user_interest_delete_own on public.user_interest
  for delete using (auth.uid() = user_id);

-- ★anon 에게는 테이블 권한 자체를 주지 않는다. RLS 가 이미 행을 막지만(auth.uid()
--   = user_id), 015 firm_watchlist 는 권한과 정책 **둘 다** 두는 쪽이다. 적용 직후
--   실측에서 이 revoke 가 빠져 anon 에 select/insert 권한이 남아 있었다(프로젝트
--   기본 grant) — RLS 한 겹에만 기대는 상태였고, 015 수준으로 맞췄다.
revoke all on public.user_interest from anon;
grant select, insert, delete on public.user_interest to authenticated;

-- 사용자당 상한(015 의 firm_watchlist cap 과 같은 방식·같은 자리).
create or replace function private.enforce_user_interest_cap()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if (select count(*) from public.user_interest where user_id = new.user_id) >= 30 then
    raise exception '관심 범위는 사용자당 최대 30개까지 등록할 수 있습니다 (user_interest cap = 30)';
  end if;
  return new;
end;
$$;

revoke all on function private.enforce_user_interest_cap() from public;
revoke all on function private.enforce_user_interest_cap() from anon;
revoke all on function private.enforce_user_interest_cap() from authenticated;

drop trigger if exists user_interest_cap_before_insert on public.user_interest;
create trigger user_interest_cap_before_insert
before insert on public.user_interest
for each row execute function private.enforce_user_interest_cap();


-- ── 내 범위의 최근 공개 문서 ────────────────────────────────────────────────
-- ★security definer 라 findings 의 RLS 를 우회한다. 그래서 **공개 게이트를 이 함수
--   본문에서 그대로 재현한다**(006/010: scope_status='ok' 이고 국문이 있는 행만).
--   재현을 빠뜨리면 정의자 권한으로 미공개 지적이 새어 나간다 — 이 함수에서 가장
--   위험한 한 줄이라 여기 적어 둔다.
-- ★범위는 **호출자 자신의 것만** 읽는다(auth.uid()). 인자로 사용자를 받지 않는다 —
--   받으면 남의 관심사를 조회할 수 있는 문이 생긴다.
-- 매칭 = 내 기관 ∪ 내 분류 ∪ 내 국가 ∪ 내 관심 업체(015). 하나도 등록하지 않았거나
-- 비로그인이면 빈 배열(널이 아닌 유효 jsonb — 화면이 "아직 없음"을 그릴 수 있게).
create or replace function public.findings_my_recent(p_limit int default 8)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with me as (select auth.uid() as uid),
  ax as (
    select kind, value from public.user_interest, me where user_id = me.uid
  ),
  fw as (
    select firm_key from public.firm_watchlist, me where user_id = me.uid
  ),
  hit as (
    select
      f.raw_signal_id,
      max(f.published_date) as published_date,
      max(f.source)         as source,
      max(f.agency)         as agency,
      max(f.firm_name)      as firm_name,
      max(f.firm_key)       as firm_key,
      count(*)::int         as obs_cnt,
      -- 왜 걸렸는지를 화면이 말할 수 있어야 한다("무균보증 때문에 떴습니다").
      (array_agg(distinct f.category_code))[1:3] as categories
    from public.findings f
    where f.scope_status = 'ok'
      -- ★공개 게이트 재현(위 주석 참조) — 지우지 말 것.
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and (select uid from me) is not null
      and (
        f.agency        in (select value from ax where kind = 'agency')
        or f.category_code in (select value from ax where kind = 'category')
        or f.country_key   in (select value from ax where kind = 'country')
        or f.firm_key      in (select firm_key from fw)
      )
    group by f.raw_signal_id
    order by max(f.published_date) desc, f.raw_signal_id asc
    limit greatest(1, least(coalesce(p_limit, 8), 30))
  )
  select coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'raw_signal_id',  raw_signal_id,
        'published_date', published_date,
        'source',         source,
        'agency',         agency,
        'firm_name',      firm_name,
        'firm_key',       firm_key,
        'obs_cnt',        obs_cnt,
        'categories',     to_jsonb(categories)
      )
      order by published_date desc, raw_signal_id asc
    )
    from hit
  ), '[]'::jsonb);
$$;

revoke all on function public.findings_my_recent(int) from public;
revoke all on function public.findings_my_recent(int) from anon;
grant execute on function public.findings_my_recent(int) to authenticated;

-- ============================================================================
-- 검증(사람 실행용)
--   -- 비로그인(anon)에서는 실행 자체가 막혀야 한다:
--   --   select public.findings_my_recent();   → permission denied
--   -- 로그인 사용자로 관심 범위 0개일 때:
--   --   select public.findings_my_recent();   → []
--   -- 공개 게이트 재현 확인(정의자 권한 누수 없음):
--   --   select count(*) from public.findings
--   --   where scope_status='ok' and finding_text_ko='' and finding_language<>'KO';
--   --   위 행들의 raw_signal_id 는 findings_my_recent 결과에 절대 나오면 안 된다.
-- ============================================================================
