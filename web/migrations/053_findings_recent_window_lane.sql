-- ============================================================================
-- 053_findings_recent_window_lane.sql
--   [트렌드 · 달라진 점] 052 교차표의 축을 **소스 → 수집 채널(lane)** 로 낮춘다.
--
-- ★왜: 052 가 적용된 당일 국내 회수 백필(+914)이 들어오자 식약처 점유율 배율이
--   2.823 → **1.572** 로 떨어져 게이트를 통과해 버렸고, 유령 행이 그대로 남았다.
--   임계값을 다시 낮추는 것은 답이 아니었다 — **문제는 임계값이 아니라 축의 입도였다.**
--
--   식약처를 한 소스로 합치면 성격이 전혀 다른 셋이 한 카운터에 섮인다
--   (실측 2026-08-06, 최근 12M vs 직전 12M):
--     레인                    최근    직전   증가율
--     FDA 483                 1,051    771    1.36
--     FDA 경고서한              800    677    1.18
--     식약처/회수               338    277    **1.22**  ← FDA 만큼 비교 가능
--     식약처/GMP실사            635    172    3.69
--     식약처/행정처분            134      2    **67.0** ← 직전 창이 사실상 비어 있다
--   합치면 2.45 배로 보이고(점유율 배율 1.57 = "정상"), 셋 사이의 극단한 차이가
--   사라진다. 이 저장소에 이미 있는 교훈 그대로다 —
--   **원인이 다른 사건을 한 카운터에 합치면 진단은 반드시 틀린다.**
--
--   레인 축으로 바꾸면 **같은 임계값(배율 2)** 으로 정확히 갈린다(실측):
--     카테고리          현재    레인 정렬 후
--     표시/포장        +3.77   **+4.99**  진짜(축소돼 있었다)
--     컴퓨터화시스템    +0.76   **+1.01**  진짜(임계 아래에 가려 있었다)
--     품질부서 감독     −1.10   **+0.08**  유령 제거(부호 반전)
--     밸리데이션/적격성  +1.36   **+0.02**  유령 제거
--     기타 품질시스템   +1.93   **−0.25**  유령 제거(부호 반전)
--     불만/회수         −1.53   **−0.47**  유령 제거
--     공정밸리데이션    −1.22   **−0.76**  유령 제거
--     무균보증          −4.72   **−3.68**  진짜(과장돼 있었다)
--   표시 7행 → 3행. 빠지는 레인은 식약처/행정처분(표본)·식약처/GMP실사(배율)·
--   MHRA(표본) 이고 **식약처/회수는 살아남는다** — 국내 자료를 통짜로 버리지 않는다.
--
-- ★쪼개는 기준은 하나다: **document_type 이 수집 채널을 뜻하는가.**
--   식약처 = 그렇다(행정처분 API·회수 API·nedrug 게시판 — 공개 이력 길이와 마스킹
--   정책이 각각 다르다). 경고서한 = 아니다(document_type 이 **발신 부서**라 수십 종으로
--   갈라져 전부 표본 미달로 떨어진다). 그래서 식약처만 쪼갬다.
--
-- ★하위호환: 052 와 같은 함수의 create or replace. 기존 5키 불변이고
--   by_category_source 에 `lane` 을 더한다. 묶음키가 source → lane 으로 낮아져
--   식약처 행이 3개로 쪼개지만, 이 키의 소비자는 trends.js alignSourceMix 하나뿐이고
--   같은 PR 에서 함께 바뀐다. `source` 필드는 그대로 남긴다(lane 이 source 를 결정).
--   053 미적용 응답에서는 lane 이 없고, 클라이언트가 source 로 폴백한다.
--
-- ★041/052 안전 계약 승계: 원문 미반환 · security definer + search_path 고정 ·
--   scope_status='ok' · f/cur/prv CTE 재사용(테이블 재스캔 없음 — lane 은 같은 CTE 안에서
--   파생되므로 raw_signals 조인도 필요 없다. document_type 은 findings 에 이미 있다).
--
-- 전제: 052 적용 상태(같은 함수의 create or replace).
-- ============================================================================

create or replace function public.findings_recent_window(p_months integer default 12)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
with b as (
  select
    least(greatest(coalesce(p_months, 12), 1), 36) as n_months,
    date_trunc('month', current_date)              as m0,
    current_date                                   as today
),
w as (
  select
    n_months,
    to_char(today, 'YYYY-MM-DD')                                        as as_of,
    to_char(m0 - make_interval(months => (n_months - 1)::int), 'YYYY-MM')     as cur_from,
    to_char(m0, 'YYYY-MM')                                              as cur_to,
    to_char(m0 - make_interval(months => (n_months * 2 - 1)::int), 'YYYY-MM') as prev_from,
    to_char(m0 - make_interval(months => n_months::int), 'YYYY-MM')      as prev_to
  from b
),
-- 두 창 전체를 한 번만 스캔한다(cur/prev 를 각각 따로 훑지 않는다).
f as (
  select
    left(x.published_date, 7) as month,
    x.category_code,
    x.source,
    -- ★[053] 레인 = 수집 채널. 기관명이 아니다.
    --   식약처는 한 기관이지만 **서로 다른 공개 채널 셋**을 운영한다
    --   (data.go.kr 행정처분 API · data.go.kr 회수 API · nedrug 게시판).
    --   채널마다 공개 이력의 길이와 마스킹 정책이 다르므로, 한 덩어리로 세면
    --   진단이 반드시 틀린다(실측 증가율: 회수 1.22 · GMP실사 3.69 · 행정처분 67.0).
    --   ★다른 소스는 쪼개지 않는다 — 경고서한의 document_type 은 **발신 부서**
    --   (문서 속성)이라 수십 종으로 갈라져 전부 표본 미달로 떨어진다.
    --   즉 기준은 "document_type 이 **수집 채널**을 뜻하는가" 하나다.
    case when x.source = 'MFDS' then x.source || '/' || x.document_type
         else x.source end                        as lane,
    x.raw_signal_id,
    x.firm_key
  from public.findings x, w
  where x.scope_status = 'ok'
    and left(x.published_date, 7) >= w.prev_from
    and left(x.published_date, 7) <= w.cur_to
),
cur as (select f.* from f, w where f.month >= w.cur_from),
prv as (select f.* from f, w where f.month <= w.prev_to)
select jsonb_build_object(
  'scope', (
    select jsonb_build_object(
      'months',    n_months,
      'as_of',     as_of,
      'cur_from',  cur_from,
      'cur_to',    cur_to,
      'prev_from', prev_from,
      'prev_to',   prev_to
    ) from w
  ),
  'totals', jsonb_build_object(
    'cur', jsonb_build_object(
      'findings',  (select count(*) from cur),
      'documents', (select count(distinct raw_signal_id) from cur),
      'firms',     (select count(distinct firm_key) from cur)
    ),
    'prev', jsonb_build_object(
      'findings',  (select count(*) from prv),
      'documents', (select count(distinct raw_signal_id) from prv),
      'firms',     (select count(distinct firm_key) from prv)
    )
  ),
  'by_month', coalesce((
    select jsonb_agg(
      jsonb_build_object('month', month, 'cnt', cnt, 'docs', docs) order by month
    )
    from (
      select month, count(*) as cnt, count(distinct raw_signal_id) as docs
      from f group by month
    ) t
  ), '[]'::jsonb),
  'by_category', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'category_code', code,
        'cur_cnt',  cur_cnt,  'cur_docs',  cur_docs,
        'prev_cnt', prev_cnt, 'prev_docs', prev_docs
      ) order by cur_cnt desc, code
    )
    from (
      select
        coalesce(c.category_code, p2.category_code) as code,
        coalesce(c.n, 0) as cur_cnt,  coalesce(c.d, 0)  as cur_docs,
        coalesce(p2.n, 0) as prev_cnt, coalesce(p2.d, 0) as prev_docs
      from (
        select category_code, count(*) as n, count(distinct raw_signal_id) as d
        from cur group by category_code
      ) c
      full outer join (
        select category_code, count(*) as n, count(distinct raw_signal_id) as d
        from prv group by category_code
      ) p2 on p2.category_code = c.category_code
    ) t
  ), '[]'::jsonb),
  -- by_source 도 두 창을 함께 준다. 증감 비교(달라진 점)의 최대 교란 요인이 **소스 구성
  -- 변화**이기 때문이다 — 예컨대 한쪽 창에만 식약처가 들어와 있으면 카테고리 구성이
  -- 달라진 게 아니라 모집단이 달라진 것이다. 화면이 이 사실을 감추지 않고 두 창의 소스
  -- 구성을 나란히 적을 수 있도록 서버가 두 값을 다 내려 준다.
  'by_source', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'source', src,
        'cnt',      cur_cnt,  'docs',      cur_docs,
        'prev_cnt', prev_cnt, 'prev_docs', prev_docs
      ) order by cur_cnt desc, src
    )
    from (
      select
        coalesce(c.source, p2.source) as src,
        coalesce(c.n, 0)  as cur_cnt,  coalesce(c.d, 0)  as cur_docs,
        coalesce(p2.n, 0) as prev_cnt, coalesce(p2.d, 0) as prev_docs
      from (
        select source, count(*) as n, count(distinct raw_signal_id) as d
        from cur group by source
      ) c
      full outer join (
        select source, count(*) as n, count(distinct raw_signal_id) as d
        from prv group by source
      ) p2 on p2.source = c.source
    ) t
  ), '[]'::jsonb),
  -- ★[052 신규] 카테고리 × 소스 교차표 — by_category 와 by_source 를 곱한 자리다.
  --   위 by_source 주석이 예견한 바로 그 상황("한쪽 창에만 식약처가 들어와 있으면 카테고리
  --   구성이 달라진 게 아니라 모집단이 달라진 것")이 2026-08 에 실제로 벌어졌다. 그때의
  --   대응은 "화면이 감추지 않고 나란히 적는다"였는데, 각주로 적는 것만으로는 부족했다 —
  --   표제(달라진 점 1위)는 단정적이고 고지는 수동적이라 읽는 사람이 표를 먼저 믿는다.
  --   이제 계산에서 정렬한다: 화면이 두 창에서 견줄 수 있는 소스만 남겨 분자·분모를
  --   **함께** 좁힌다. 분모에서만 빼는 순진한 구현은 결함을 키운다(실측: 기타 품질시스템
  --   +3.65 → +5.27, 없던 유령 2행 신규 발생).
  --   ★어느 소스를 뺄지는 서버가 정하지 않는다 — 서버는 사실(교차 카운트)만 주고 판정
  --   규칙과 화면 표기는 클라이언트(trends.js alignSourceMix)가 한다. by_source 주석이
  --   선언한 "서버가 두 값을 다 내려 준다"의 연장이다.
  --   full outer join 이라 한쪽 창에만 있는 (카테고리, 소스) 짝도 0 으로 채워 나온다.
  --   행 수 상한은 taxonomy 20종 × 소스 종수(현재 5)라 별도 limit 을 두지 않는다.
  'by_category_source', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'category_code', code,
        'source',        src,
        'lane',          ln,
        'cur_cnt',  cur_cnt,  'cur_docs',  cur_docs,
        'prev_cnt', prev_cnt, 'prev_docs', prev_docs
      ) order by ln, cur_cnt desc, code
    )
    from (
      select
        coalesce(c.category_code, p2.category_code) as code,
        coalesce(c.source,        p2.source)        as src,
        coalesce(c.lane,          p2.lane)          as ln,
        coalesce(c.n, 0)  as cur_cnt,  coalesce(c.d, 0)  as cur_docs,
        coalesce(p2.n, 0) as prev_cnt, coalesce(p2.d, 0) as prev_docs
      from (
        select category_code, source, lane,
               count(*) as n, count(distinct raw_signal_id) as d
        from cur group by category_code, source, lane
      ) c
      full outer join (
        select category_code, source, lane,
               count(*) as n, count(distinct raw_signal_id) as d
        from prv group by category_code, source, lane
      ) p2
        on p2.category_code = c.category_code
       and p2.lane          = c.lane
    ) t
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_recent_window(integer) is
  '최근 N개월 vs 직전 N개월 창 집계(041) + 카테고리x수집채널 교차표(052->053). '
  'lane 은 기관이 아니라 수집 채널이다 - 식약처는 공개 이력 길이가 다른 셋 개 채널을 '
  '운영하므로 합치면 추세 비교가 틀린다. 어느 레인을 빼는지는 서버가 정하지 않는다.';

revoke all on function public.findings_recent_window(integer) from public;
grant execute on function public.findings_recent_window(integer) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용)
-- ============================================================================
-- 1) 키 6개 유지 · lane 필드 존재
--    select count(*) from jsonb_object_keys(public.findings_recent_window(12));   -- 6
--    select distinct e->>'lane' from jsonb_array_elements(
--      public.findings_recent_window(12)->'by_category_source') e order by 1;
--    -- 식약처는 MFDS/admin-action · MFDS/gmp-inspection · MFDS/recall-quality 로 3행,
--    -- 경고서한·483·EU·MHRA 는 소스명 그대로 1행이어야 한다.
--
-- 2) 레인으로 쪼개도 합은 보존된다 (by_category 합과 동일)
--    with r as (select public.findings_recent_window(12) j)
--    select (select sum((e->>'cur_cnt')::int) from r, jsonb_array_elements(r.j->'by_category') e)
--         = (select sum((e->>'cur_cnt')::int) from r, jsonb_array_elements(r.j->'by_category_source') e)
--    from r;   -- true
--
-- 3) lane 은 source 를 결정한다(1:1 역함수)
--    select count(*) from (
--      select e->>'lane' ln, count(distinct e->>'source') n
--      from jsonb_array_elements(public.findings_recent_window(12)->'by_category_source') e
--      group by 1 having count(distinct e->>'source') > 1) t;   -- 0
--
-- 4) 원문 미유출
--    select public.findings_recent_window(12)::text ilike '%finding_text%';   -- false
