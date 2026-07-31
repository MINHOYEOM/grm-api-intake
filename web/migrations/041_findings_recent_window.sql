-- ============================================================================
-- 041_findings_recent_window.sql — [FIND-1 트렌드 고도화] 기간 축 + 업체 조회
--
-- ★왜: /findings/trends/ 는 지금까지 **전 기간 누적**만 보여 준다. 그런데 보유량의 47%가
--   2024년 한 해에 몰려 있고(FOIA 대량 공개 배치), 그래서 "카테고리 순위"는 사실상 그
--   배치의 그림자다. 페이지 제목이 약속하는 "트렌드"(=시간에 따른 변화)를 답할 수 있는
--   집계가 서버에 **아예 없었다**:
--     · findings_stats().by_agency_category  — 시간축 없음(전 기간 합계)
--     · findings_stats().by_month            — 카테고리축 없음(월×기관)
--     · findings_category_matrix()           — 연도×카테고리는 있으나 연도 단위라 최근
--                                              12개월 창을 만들 수 없고, 직전 기간과의
--                                              비교(무엇이 달라졌는가)를 못 한다
--   즉 클라이언트에서 조합할 수 없는 구조적 공백이라 RPC 를 새로 만든다.
--
-- ★안전 계약(불가침, 007/010/013/017 과 동종): 이 파일의 두 함수는 어떤 경로로도
--   finding_text/finding_text_ko/evidence_url/raw_json 등 원문·URL 텍스트를 반환하지
--   않는다. 반환 가능한 값은 카운트(count/distinct count)와 서지 메타(category_code/
--   source/month/firm_key/firm_name/published_date)뿐이며, jsonb_build_object 키 목록이
--   그 계약의 유일한 표면이다.
--   ※ 트렌드 페이지가 표시하는 **실제 지적 문장**은 이 함수들이 아니라 026 의
--     findings_search(security invoker + RLS)로 따로 가져간다 — 공개 게이트(010 정책)를
--     통과한 행만 나오는 기존 경로를 그대로 재사용하는 것이고, 이 파일의 계약과는 무관하다.
--
-- security definer + `set search_path = public` 고정(007 관례) — 집계는 미번역분 포함
--   전량을 세야 페이지의 다른 수치와 정합하기 때문이다. scope_status='ok' 필터는 010 과
--   동일하게 모든 집계에 건다.
--
-- ★004 함정 해당 여부: plpgsql DO 블록·declare 변수 없음(language sql 순수 함수 2개).
--   다만 make_interval 의 **명명 인자 `months =>`** 와 CTE 컬럼명이 겹치면 헷갈리므로
--   창 길이 컬럼은 `n_months` 로 두어 이름 충돌 자체를 만들지 않는다.
-- ★009 함정(배열 슬라이스 괄호) 해당 없음 — 배열 인자를 받지 않는다.
--
-- 전제: 002 + 006 + 010(scope_status) + 013(firm_key generated 컬럼).
-- ============================================================================


-- ---------------------------------------------------------------------------
-- (A) findings_recent_window(p_months) — 최근 N개월 창 + 직전 동일 길이 창
-- ---------------------------------------------------------------------------
-- 창 경계는 **월 단위로 정렬**한다(일 단위가 아니라). by_month 막대와 by_category 창이
-- 같은 경계를 공유해야 화면 안에서 수치가 서로 어긋나지 않기 때문이다.
--   현재월 m0 기준: cur = [m0-(N-1) .. m0], prev = [m0-(2N-1) .. m0-N]
--   예) N=12, 2026-07 기준 → cur 2025-08~2026-07, prev 2024-08~2025-07
-- 진행 중인 이번 달(m0)이 cur 에 들어가 마지막 막대가 낮게 보이는 것은 의도된 사실이다 —
-- 클라이언트가 "이번 달은 진행 중"이라고 화면에 적는다(잘라내면 최신이 사라진다).
--
-- ★by_category 는 건수(cnt)와 **문서 수(docs)를 함께** 준다. 화면의 증감 비교는 문서
--   점유율을 주 지표로 쓴다 — 문서 하나에 지적이 최대 46개까지 실리므로(026 헤더 실측)
--   건수 점유율은 긴 문서 몇 건에 휘둘리지만, 문서 점유율은 실사·서한 1건을 1로 세어
--   "몇 곳에서 이 문제가 지적됐는가"를 잰다. 서버는 두 수치를 다 주고 해석은 클라이언트가
--   한다(어느 쪽이든 재현 가능하도록).
--   ※ 문서는 여러 카테고리에 동시에 속할 수 있으므로 docs 합계는 100%를 넘는다 — 이는
--     비율이 아니라 **각 영역의 등장률**이라는 뜻이고, 클라이언트가 그렇게 표기한다.
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
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_recent_window(integer) is
  '[FIND-1] 트렌드 기간 축 — 최근 N개월(기본 12) 창과 직전 동일 길이 창의 카테고리/소스/'
  '월별 집계. 카운트·서지 메타만 반환(007 안전 계약과 동종). 월 경계 정렬.';


-- ---------------------------------------------------------------------------
-- (B) findings_firm_search(p_q, p_limit) — 업체명 부분일치 조회
-- ---------------------------------------------------------------------------
-- ★왜: 트렌드 페이지의 업체 랭킹 Top 30 은 "공개 문서가 많은 순"이라 사실상 미국 대형
--   컴파운딩 약국 목록이다. 정작 실무에서 필요한 질문("우리 CMO·원료 공급업체가 지적받은
--   이력이 있나")은 이름을 넣어 찾는 것인데, 그 경로가 없었다. 013 의 업체 프로파일
--   페이지(/findings/firm/?key=)는 이미 존재하지만 Top 30 을 거치지 않으면 도달할 수
--   없어 사실상 사장돼 있다 — 이 함수가 그 진입로를 연다.
--
-- 검색 semantics 는 026 findings_search 와 동일하게 **ILIKE 부분일치**로 맞춘다(FTS 로
-- 바꾸지 않는다 — 026 헤더의 근거 참조). LIKE 와일드카드(%·_·\)는 이스케이프한다.
-- 백슬래시를 먼저 치환해야 뒤에 삽입한 이스케이프 문자를 다시 이스케이프하지 않는다.
--
-- 그룹핑 단위는 firm_name 이 아니라 **firm_key**(013 정규화 컬럼)다 — 같은 업체의 표기
-- 변형이 여러 행으로 흩어지는 왜곡을 017 이 top_firms 에서 이미 제거했고, 여기도 같은
-- 기준을 쓴다. 표시명(firm_name)은 그 그룹에서 가장 흔한 원문 표기(동률이면 더 긴 표기)로
-- 채운다 — 013 findings_firm_profile().display_name / 017 top_firms 와 완전히 동일한
-- 타이브레이크 규칙이다.
--
-- 질의가 2자 미만이면 빈 결과를 돌려준다 — 1자 ILIKE 는 코퍼스 대부분을 훑어 오는
-- 사실상의 전량 스캔이라 조회로서 의미가 없다(서버 보호 겸 UX).
create or replace function public.findings_firm_search(
  p_q     text    default '',
  p_limit integer default 20
) returns jsonb
language sql
stable
security definer
set search_path = public
as $$
with p as (
  select
    btrim(coalesce(p_q, '')) as q,
    replace(replace(replace(btrim(coalesce(p_q, '')), '\', '\\'), '%', '\%'), '_', '\_')
      as q_esc,
    least(greatest(coalesce(p_limit, 20), 1), 50) as lim
),
hit as (
  select
    f.firm_key,
    count(*)                          as findings,
    count(distinct f.raw_signal_id)   as documents,
    min(f.published_date)             as first_seen,
    max(f.published_date)             as last_seen
  from public.findings f, p
  where f.scope_status = 'ok'
    and char_length(p.q) >= 2
    and f.firm_name ilike '%' || p.q_esc || '%'
  group by f.firm_key
  order by count(*) desc, f.firm_key
  limit (select lim from p)
)
select jsonb_build_object(
  'query',       (select q from p),
  'match_firms', (select count(*) from hit),
  'items', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'firm_key',   h.firm_key,
        'firm_name',  (
          select f2.firm_name
          from public.findings f2
          where f2.firm_key = h.firm_key and f2.scope_status = 'ok'
          group by f2.firm_name
          order by count(*) desc, length(f2.firm_name) desc, f2.firm_name asc
          limit 1
        ),
        'findings',   h.findings,
        'documents',  h.documents,
        'first_seen', h.first_seen,
        'last_seen',  h.last_seen,
        'top_category', (
          select f3.category_code
          from public.findings f3
          where f3.firm_key = h.firm_key and f3.scope_status = 'ok'
          group by f3.category_code
          order by count(*) desc, f3.category_code asc
          limit 1
        ),
        'sources', coalesce((
          select jsonb_agg(s.source order by s.n desc, s.source)
          from (
            select f4.source, count(*) as n
            from public.findings f4
            where f4.firm_key = h.firm_key and f4.scope_status = 'ok'
            group by f4.source
          ) s
        ), '[]'::jsonb)
      ) order by h.findings desc, h.firm_key
    ) from hit h
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_firm_search(text, integer) is
  '[FIND-1] 업체명 부분일치 조회 — firm_key 단위 집계 + 표시명/기간/대표 카테고리/소스. '
  '카운트·서지 메타만 반환(007 안전 계약과 동종). 2자 미만 질의는 빈 결과.';


-- ---------------------------------------------------------------------------
-- (C) 권한 — 007 관례(전면 회수 후 anon/authenticated 재부여)
-- ---------------------------------------------------------------------------
revoke all on function public.findings_recent_window(integer) from public;
revoke all on function public.findings_firm_search(text, integer) from public;

grant execute on function public.findings_recent_window(integer) to anon, authenticated;
grant execute on function public.findings_firm_search(text, integer) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용, 프로덕션 SQL Editor / anon 키 PostgREST)
-- ============================================================================
-- 1) 창 경계가 월 정렬인가 (N=12 → cur 12개월, prev 12개월, by_month 24행)
--    select public.findings_recent_window(12) -> 'scope';
--    select jsonb_array_length(public.findings_recent_window(12) -> 'by_month');   -- <= 24
--
-- 2) 창 합계가 by_category 합과 정합한가(건수 기준)
--    with r as (select public.findings_recent_window(12) j)
--    select (r.j->'totals'->'cur'->>'findings')::int,
--           (select sum((e->>'cur_cnt')::int) from r, jsonb_array_elements(r.j->'by_category') e)
--    from r;   -- 두 값이 같아야 한다
--
-- 3) 원문 텍스트가 어떤 키로도 새지 않는가 (안전 계약)
--    select public.findings_recent_window(12)::text ilike '%finding_text%';   -- false
--    select public.findings_firm_search('pharma')::text ilike '%finding_text%';  -- false
--
-- 4) 2자 미만 질의는 빈 결과
--    select public.findings_firm_search('a') -> 'items';   -- []
--
-- 5) anon 키로 실제 호출되는가(권한)
--    curl -s "$URL/rest/v1/rpc/findings_recent_window" -H "apikey: $ANON" \
--         -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--         -d '{"p_months":12}' | jq '.totals'
