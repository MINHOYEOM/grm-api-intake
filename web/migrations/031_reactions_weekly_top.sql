-- ============================================================================
-- 031_reactions_weekly_top.sql — [GRM 인기 카드] KST 주간 count-only 공개 RPC
--
-- 의미 계약:
--   - Asia/Seoul 기준 이번 주 월요일 00:00부터 조회 시점까지 생성되어, 조회 시점에도
--     public.reaction 에 남아 있는 활성 반응만 집계한다.
--   - heart/scrap을 함께 보되 카드별 count(distinct user_id)를 점수로 삼는다. 한 사용자가
--     같은 카드에 두 kind를 모두 남겨도 distinct_user_count는 1만 증가한다.
--   - 동률은 내부 집계값 scraps desc -> hearts desc -> card_id asc 순으로 결정한다.
--   - p_limit은 NULL 포함 기본 3, 유효 범위 1~5로 서버에서 clamp한다.
--
-- 공개 반환 계약(불가침): card_id, distinct_user_count 두 필드만 반환한다.
-- user_id·개별 created_at·kind별 수·제목·요약·원문 URL·raw 데이터는 반환하지 않는다.
-- 제목과 링크는 후속 웹 클라이언트가 커밋된 search-index.json과 card_id를 교차해 얻고,
-- 인덱스에 없는 card_id는 표시하지 않는다.
--
-- reaction의 본인 행 전용 RLS를 넘어 전체 활성 상태를 집계해야 하므로 security definer가
-- 필요하다. mutable search_path를 막고 public.reaction을 fully-qualified로 참조한다.
-- 함수 생성 시 생길 수 있는 기본 EXECUTE를 명시적으로 회수한 뒤 공개 클라이언트 역할인
-- anon/authenticated에만 재부여한다.
--
-- 이 파일은 함수·주석·함수 권한만 추가한다. 테이블·이벤트 로그·Edge Function·인덱스는
-- 만들거나 변경하지 않는다. 적용 전 검증은 docs/specs/GRM_031_적용검증계획_2026-07-18.md.
-- ============================================================================

create or replace function public.reactions_weekly_top(p_limit integer default 3)
returns table (
  card_id text,
  distinct_user_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
  with params as (
    select least(greatest(coalesce(p_limit, 3), 1), 5) as result_limit
  ),
  bounds as (
    select
      date_trunc('week', now() at time zone 'Asia/Seoul')
        at time zone 'Asia/Seoul' as week_start,
      now() as observed_at
  ),
  ranked as (
    select
      r.card_id,
      count(distinct r.user_id) as distinct_user_count,
      count(distinct r.user_id) filter (where r.kind = 'scrap') as scraps,
      count(distinct r.user_id) filter (where r.kind = 'heart') as hearts
    from public.reaction r
    cross join bounds b
    where r.created_at >= b.week_start
      and r.created_at <= b.observed_at
    group by r.card_id
  )
  select
    ranked.card_id,
    ranked.distinct_user_count
  from ranked
  order by
    ranked.distinct_user_count desc,
    ranked.scraps desc,
    ranked.hearts desc,
    ranked.card_id asc
  limit (select result_limit from params);
$$;

comment on function public.reactions_weekly_top(integer) is
  '[GRM 인기 카드 v1] KST 월요일 00:00부터 조회 시점까지의 활성 heart/scrap을 카드별 '
  'distinct user로 집계한다. 반환 allowlist는 card_id·distinct_user_count뿐이며 사용자 '
  '식별자·개별 시각·kind별 수·콘텐츠·URL은 반환하지 않는다. 제목·링크는 클라이언트가 '
  '커밋된 search-index.json과 교차하고, 미등록 card_id는 표시하지 않는다.';

-- Supabase 프로젝트의 함수 기본 권한 설정과 무관하게 공개 표면을 결정론적으로 고정한다.
revoke all on function public.reactions_weekly_top(integer) from public;
revoke all on function public.reactions_weekly_top(integer) from anon;
revoke all on function public.reactions_weekly_top(integer) from authenticated;
revoke all on function public.reactions_weekly_top(integer) from service_role;

grant execute on function public.reactions_weekly_top(integer) to anon, authenticated;
