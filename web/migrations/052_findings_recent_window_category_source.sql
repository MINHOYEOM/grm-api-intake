-- ============================================================================
-- 052_findings_recent_window_category_source.sql
--   [트렌드 · 달라진 점] 041 findings_recent_window 에 카테고리×소스 교차표를 추가한다.
--
-- ★왜: /findings/trends/ 의 "달라진 점"이 **수집 커버리지 변화를 규제 변화로 표시**하고
--   있다. 2026-08 국내(MFDS) 백필로 최근 창 807건 / 직전 창 174건이 되면서 점유율이
--   10.63% → 30.01%(2.82배)로 벌어졌고, 그 비대칭이 카테고리 구성비에 그대로 실린다.
--
--   실측(2026-08-06, 041 과 **같은 필터**(scope_status='ok') · 최근 2025-09~2026-08 vs
--   직전 2024-09~2025-08. 창 합계 cur 2,689 / prev 1,637):
--     카테고리            화면 표시    MFDS 제외    판정
--     표시/포장           +3.78       +5.42       진짜인데 축소
--     기타 품질시스템      +3.75       −0.18       ★유령 — 부호까지 반대
--     밸리데이션/적격성    +1.47       +0.02       유령
--     세척밸리데이션       +1.12       −0.09       ★유령 — 부호까지 반대
--     컴퓨터화시스템       +0.80       +1.18       진짜인데 임계 미달로 은폐
--     원자재/공급업체      −1.03       −0.08       유령
--     품질부서 감독        −1.61       +0.02       ★유령 — 부호까지 반대
--     공정밸리데이션       −1.63       −0.94       유령(정렬 후 임계 미달)
--     무균보증            −6.14       −4.51       진짜인데 과장
--   즉 표시 8행 중 **5행이 유령이고 그중 3행은 부호까지 반대**이며, 진짜 신호인
--   컴퓨터화시스템은 임계 아래에 가려 안 보인다. 정렬하면 8행 → 3행(표시/포장 +5.42 ·
--   컴퓨터화시스템 +1.18 · 무균보증 −4.51)이 된다.
--   규제담당자가 자가점검 우선순위를 반대로 잡게 만드는 종류의 오류다.
--
-- ★임계값 주의(교훈): 이 수리를 설계할 때의 실측은 MFDS 배율 **99.2배**였고 그 값에
--   맞춰 배율 상한 3 을 제안했었다. 그런데 백필이 직전 창에도 MFDS 를 넣으면서 배율이
--   **2.82** 로 떨어졌다 — 상한 3 이었다면 MFDS 가 그대로 통과해 **이 수리가 아무 일도
--   하지 않는다**(고쳤다고 믿는데 화면은 그대로인, 가장 나쁜 결과). 상한은 오늘 실측
--   간극(EU 1.305 ↔ MFDS 2.823)의 가운데인 **2** 로 잡았다. 데이터가 또 움직이면 이
--   값도 다시 재야 한다 — trends.js MOVER_SOURCE_MAX_RATIO 주석에 같은 경고를 남겼다.
--
--   ※ MFDS 비대칭은 우리 수집만의 문제가 아니다 — 식약처가 옛 해외 실사분의 지적 내용을
--     가려 공개해(PDF 셀이 `0000`) 과거 구간이 원천에서부터 비어 있다(2022~23년 100%
--     마스킹 → 2026년 0%). 어느 쪽이든 두 창은 견줄 수 없고, 그래서 정렬이 필요하다.
--
-- ★화면이 두 창의 소스 구성을 맞추려면 **카테고리×소스 교차표**가 필요하다. 041 은
--   by_category(소스 전부 합산)와 by_source(카테고리 전부 합산)만 주고 교차표가 없으며,
--   배포된 다른 RPC(007/013/017/038/042/043)에도 없어 클라이언트 조합이 불가능하다.
--   → 041 의 **같은 함수**에 키 하나(by_category_source)를 추가만 한다.
--
-- ★하위호환(불가침): 기존 5개 키(scope/totals/by_month/by_category/by_source)는 이름·
--   타입·값·정렬이 041 과 완전히 동일하다. 이 파일은 041 본문(54~175행)을 그대로 옮겨
--   적고 마지막 키 하나를 덧붙인 create or replace 이며 시그니처도 같으므로, 기존 호출부
--   (POST /rest/v1/rpc/findings_recent_window {"p_months":12})는 무변경으로 동작한다.
--   반대로 052 미적용 라이브에서는 이 키가 없고, trends.js 는 키가 없으면 조정 없이 종전
--   경로로 간다(alignSourceMix 폴백) — 신·구 어느 조합에서도 패널이 깨지지 않는다.
--
-- ★041 안전 계약 승계(불가침):
--   · finding_text / finding_text_ko / evidence_url / raw_json 등 원문·URL 텍스트를 어떤
--     경로로도 반환하지 않는다. 새 키가 내보내는 값도 카운트와 서지 메타(category_code /
--     source)뿐이다.
--   · security definer + `set search_path = public` 고정(007 관례).
--   · scope_status = 'ok' 필터를 모든 집계에 동일하게 건다 — 새 키는 041 의 f / cur / prv
--     CTE 를 **그대로 재사용**하므로 필터·창 경계가 구조적으로 같다(테이블 재스캔 없음).
--   · work_mem 은 설정하지 않는다(041 도 걸지 않는다 — 그 계약은 findings_search 계열의
--     행 투영에만 해당).
--
-- ★키 이름 규약 주의: 041 의 by_source 는 최근 창을 `cnt`/`docs`(접두어 없음)로, by_category
--   는 `cur_cnt`/`cur_docs` 로 적는다. 이 비대칭은 **고치지 않는다**(고치면 하위호환이
--   깨진다). 새 키는 클라이언트가 by_category 모양으로 접어 쓰므로 by_category 규약을 따른다.
--
-- ★문서 수(docs)의 가산성: raw_signal 은 소스 하나에만 속하므로, 같은 카테고리에서 소스별
--   문서 수의 합 = 그 카테고리의 문서 수다. 그래서 클라이언트가 소스 부분집합으로 접어도
--   문서 수가 정확하다. 카테고리 축은 그렇지 않다(한 문서가 여러 카테고리에 속함) — 041 이
--   "docs 합계는 100%를 넘는다"고 적어 둔 그 성질이다.
--
-- 전제: 041 적용 상태(같은 함수의 create or replace).
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
        'cur_cnt',  cur_cnt,  'cur_docs',  cur_docs,
        'prev_cnt', prev_cnt, 'prev_docs', prev_docs
      ) order by src, cur_cnt desc, code
    )
    from (
      select
        coalesce(c.category_code, p2.category_code) as code,
        coalesce(c.source,        p2.source)        as src,
        coalesce(c.n, 0)  as cur_cnt,  coalesce(c.d, 0)  as cur_docs,
        coalesce(p2.n, 0) as prev_cnt, coalesce(p2.d, 0) as prev_docs
      from (
        select category_code, source,
               count(*) as n, count(distinct raw_signal_id) as d
        from cur group by category_code, source
      ) c
      full outer join (
        select category_code, source,
               count(*) as n, count(distinct raw_signal_id) as d
        from prv group by category_code, source
      ) p2
        on p2.category_code = c.category_code
       and p2.source        = c.source
    ) t
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_recent_window(integer) is
  '최근 N개월 vs 직전 N개월 창 집계(041) + 카테고리x소스 교차표(052). '
  'by_category_source 는 화면이 두 창의 소스 구성을 맞춰 구성비를 계산하기 위한 것이다 '
  '- 어느 소스를 뺄지는 서버가 정하지 않고 사실만 내려 준다.';

-- ---------------------------------------------------------------------------
-- 권한 — 007 관례(전면 회수 후 anon/authenticated 재부여). create or replace 는 기존
-- ACL 을 보존하지만 041 과 같은 형태로 명시해 둔다(멱등).
-- ---------------------------------------------------------------------------
revoke all on function public.findings_recent_window(integer) from public;
grant execute on function public.findings_recent_window(integer) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용)
-- ============================================================================
-- 1) 기존 5개 키가 그대로인가 (하위호환) — 총 6개가 나와야 한다
--    select count(*) from jsonb_object_keys(public.findings_recent_window(12));
--
-- 2) 교차표 합 = by_category 합 (소스 축으로 쪼갠 것이므로 정확히 같아야 한다)
--    with r as (select public.findings_recent_window(12) j)
--    select (select sum((e->>'cur_cnt')::int) from r, jsonb_array_elements(r.j->'by_category') e)
--         = (select sum((e->>'cur_cnt')::int) from r, jsonb_array_elements(r.j->'by_category_source') e)
--    from r;   -- true
--
-- 3) 문서 수 가산성 — 카테고리별로 소스 docs 합 = by_category docs
--    with r as (select public.findings_recent_window(12) j),
--         a as (select e->>'category_code' c, (e->>'cur_docs')::int d
--               from r, jsonb_array_elements(r.j->'by_category') e),
--         b as (select e->>'category_code' c, sum((e->>'cur_docs')::int) d
--               from r, jsonb_array_elements(r.j->'by_category_source') e group by 1)
--    select count(*) from a join b using (c) where a.d <> b.d;   -- 0
--
-- 4) 원문 텍스트가 어떤 키로도 새지 않는가 (안전 계약)
--    select public.findings_recent_window(12)::text ilike '%finding_text%';   -- false
--
-- 5) anon 키로 실제 호출되는가(권한)
--    curl -s "$URL/rest/v1/rpc/findings_recent_window" -H "apikey: $ANON" \
--         -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--         -d '{"p_months":12}' | jq '.by_category_source | length'
