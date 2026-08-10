-- ============================================================================
-- 056_findings_search_country_axis.sql — findings_search 에 국가(country_key) 축 추가
--
-- 배경: 055_findings_country_key.sql 이 findings.country_key(ISO2, generated stored) 와
--   findings_zone_category()(038 supersede)를 만들었지만, 검색 화면의 정본 RPC인
--   findings_search(026→...→040→054)는 country_key 를 전혀 모른다 — 필터도, 대시보드
--   집계도 없다. 슬롯(country_key 컬럼)은 있는데 소비 배선이 없는 상태로 몇 주를 사는
--   것이 이 저장소의 가장 흔한 결함 계열이다(MEMORY: WL statement_ko 3주·MFDS egress
--   프록시 3주 등) — 이 파일이 그 배선이다.
--
-- 생성 방식(수작업 복사 금지) — 054 와 동일 관례: 054(=현행 findings_search 정의)를
--   기계적으로 읽어 앵커 12곳에 덧붙여 생성했다(gen_056.py, 스크래치패드 보관 —
--   저장소에는 결과 SQL 만 남긴다). 앵커가 정확히 1회가 아니면 생성기가 중단한다.
--
-- supersede 체인: findings_search = 026 → 027 → 028 → 030 → 036 → 040 → 054 → **이 파일(056)**
--
-- ─ 변경 지점(전부 추가, 기존 파라미터·반환 키는 하나도 바꾸지 않는다) ──────────
--   ① 파라미터 p_country text default '' 를 목록 **끝**에 추가(웹 호출부 findings.js
--      fetchSearch() 는 named JSON body 라 위치 무관 — SQL Editor 등 위치 인자 호출부를
--      보호하기 위해 그래도 끝에 둔다).
--   ② p CTE에 f_country(정규화된 원본)·f_country_key(실제 비교값) 계산 컬럼 추가.
--      ★sentinel 'UNKNOWN' → f_country_key=''(country_key='' 미확인 버킷과 매치).
--        p_country='' 는 필터 없음(기존 파라미터들과 동일 계약) — 그래서 "미확인"을
--        빈 문자열로 표현할 수 없다(그러면 "필터 없음"과 구분이 안 된다). 대문자 코드는
--        스캐폴드가 못 낼 값(실제 ISO2 는 항상 2자)이라 충돌하지 않는다.
--   ③ searched CTE 투영 목록에 f.country_key 추가(filtered 가 `select s.*` 라 자동 상속).
--   ④ filtered CTE where 절에 country_key 조건 추가.
--   ⑤ fac_source/fac_cat/fac_month/fac_ev/fac_rs/fac_agency 6개 파셋 CTE 모두에 국가
--      조건을 추가한다(자기 축은 그대로 제외, 국가는 "다른 필터"이므로 전부 포함 —
--      p_agency 가 이미 이 6곳에 들어가 있는 것과 동일한 원칙). fac_country 는 만들지
--      않는다(임무서 1b 가 dash.by_country 만 지정 — 드롭다운은 그걸로 채운다).
--   ⑥ dash_country CTE 신설 — country_key 별 문서 수(docs, count(distinct raw_signal_id))와
--      지적 수(findings, count(*))를 한 키에 함께 낸다. country_key='' 행(미확인)도
--      그대로 포함한다 — 27.4%를 숨기지 않는다.
--   ⑦ dash.by_country 키 신설.
--
-- ─ 합계 정합 ────────────────────────────────────────────────────────────────
--   dash_country 는 filtered 전량을 country_key 로 그룹화할 뿐이라 행 손실이 없다 →
--   sum(dash.by_country[].docs) = totals.documents, sum(dash.by_country[].findings) =
--   totals.findings 가 국가 필터 적용 여부와 무관하게 항상 성립한다.
--
-- ─ p_country 필터 의미론 ──────────────────────────────────────────────────────
--   p_country=''         → 필터 없음(기존 관례).
--   p_country='UNKNOWN'  → country_key='' 인 행만(소재국 미확인, findings.site_country
--                           가 비었거나 055 매핑 정본 밖의 변종).
--   p_country='US' 등    → 해당 ISO2 코드로 정확히 일치(대소문자 무시 — upper() 비교).
--
-- 전제: 040 적용 + 055_findings_country_key.sql(findings.country_key 컬럼) 적용.
-- ============================================================================

create or replace function public.findings_search(
  p_q             text default '',
  p_source        text default '',
  p_category      text default '',
  p_month         text default '',
  p_evidence      text default '',
  p_review_status text default '',
  p_agency        text default '',
  p_sort          text default 'date_desc',
  p_page          int  default 1,
  p_docs_per_page int  default 24,
  -- [056] 국가 필터(ISO2 코드). 반드시 목록 끝에 추가한다 — 기존 호출부
  -- (web/assets/findings.js fetchSearch())는 named JSON body 라 위치와 무관하지만,
  -- 위치 인자를 쓰는 잠재 호출부(SQL Editor 등)를 깨지 않기 위한 054 관례 계승.
  p_country       text default ''
) returns jsonb
language sql
stable
security invoker
set search_path = public, extensions
set work_mem = '8MB'   -- Major 1: CTE materialize(≈3MiB)가 인스턴스 기본 2184kB 를 넘겨
                       -- temp 스필하던 것을 함수 실행 동안만 상향해 해소(실측 119ms·spill 0)
as $$
with p as (
  select
    coalesce(btrim(p_q), '')                                       as q,
    replace(replace(replace(coalesce(btrim(p_q), ''), '\', '\\'), '%', '\%'), '_', '\_') as q_esc,
    coalesce(p_source, '')                                         as f_source,
    coalesce(p_category, '')                                       as f_cat,
    coalesce(p_month, '')                                          as f_month,
    coalesce(p_evidence, '')                                       as f_ev,
    coalesce(p_review_status, '')                                  as f_rs,
    coalesce(p_agency, '')                                         as f_agency,
    -- [056] f_country=정규화된 원본 파라미터('' 이면 필터 없음). f_country_key=실제
    --   비교 대상 country_key 값 — 'UNKNOWN' sentinel 은 country_key='' (미확인 버킷)
    --   으로 치환한다. 진짜 ISO2 코드('US' 등)는 그대로 통과한다. 대소문자 무시(upper).
    upper(coalesce(btrim(p_country), ''))                          as f_country,
    (case when upper(coalesce(btrim(p_country), '')) = 'UNKNOWN' then ''
          else upper(coalesce(btrim(p_country), '')) end)          as f_country_key,
    case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
         then p_sort else 'date_desc' end                          as sort,
    -- Minor 2: 상한 400,000 — (page-1)*per(최대 100) = 4천만 < 2^31 로 int overflow 차단.
    least(greatest(coalesce(p_page, 1), 1), 400000)                as page,
    least(greatest(coalesce(p_docs_per_page, 24), 1), 100)         as per
),
searched as (
  select
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date, f.firm_name,
    f.firm_key, f.category_code, f.evidence_level, f.review_status, f.country_key,
    left(f.published_date, 7) as month
  from public.findings f, p
  where p.q = ''
     or (
          -- Minor 1: blob = 종전 searchTermsFor 순서(finding_text 선두) · refs 는 원소만
          -- (JSON 구두점 미포함) · review_status 는 원값+'_'→' ' 표기 변형.
          -- 표시 라벨(증거/검토/영문 카테고리명)은 D3 로 의도적 미탑재(헤더 ③ 참조).
          coalesce(f.finding_text, '')       || ' ' ||
          coalesce(f.finding_text_ko, '')    || ' ' ||
          coalesce(f.firm_name, '')          || ' ' ||
          coalesce(f.document_id, '')        || ' ' ||
          coalesce(f.agency, '')             || ' ' ||
          coalesce(f.source, '')             || ' ' ||
          coalesce(f.published_date, '')     || ' ' ||
          coalesce(f.evidence_level, '')     || ' ' ||
          coalesce(f.review_status, '')      || ' ' ||
          replace(coalesce(f.review_status, ''), '_', ' ') || ' ' ||
          coalesce(f.category_code, '')      || ' ' ||
          coalesce(f.category_label_ko, '')  || ' ' ||
          coalesce(f.translation_method, '') || ' ' ||
          coalesce((select string_agg(cr.v, ' ') from jsonb_array_elements_text(f.cfr_refs)  cr(v)), '') || ' ' ||
          coalesce((select string_agg(mr.v, ' ') from jsonb_array_elements_text(f.mfds_refs) mr(v)), '') || ' ' ||
          -- [040] 실사관 이름. refs 와 같은 방식으로 **원소만** 싣는다(JSON 구두점 미포함 —
          --   030 이 `cfr_refs::text` 로 `[]` 질의가 전건 매치되던 것을 고친 것과 같은 이유).
          coalesce((select string_agg(ins.v, ' ') from jsonb_array_elements_text(f.inspector_names) ins(v)), '')
        ) ilike '%' || p.q_esc || '%'
),
filtered as (
  select s.* from searched s, p
  where (p.f_source = '' or s.source          = p.f_source)
    and (p.f_cat    = '' or s.category_code   = p.f_cat)
    and (p.f_month  = '' or s.month           = p.f_month)
    and (p.f_ev     = '' or s.evidence_level  = p.f_ev)
    and (p.f_rs     = '' or s.review_status   = p.f_rs)
    and (p.f_agency = '' or s.agency          = p.f_agency)
    -- [056] country_key 필터.
    and (p.f_country = '' or s.country_key     = p.f_country_key)
),
docs as (
  select
    f.raw_signal_id,
    min(f.published_date) as pub,
    min(f.firm_name)      as firm,
    min(f.finding_id)     as tie,
    count(*)::int         as doc_findings
  from filtered f
  group by f.raw_signal_id
),
ordered as (
  select
    d.raw_signal_id,
    row_number() over (
      order by
        (case when p.sort = 'firm_asc' then d.firm end) collate "ko-KR-x-icu" asc nulls last,
        (case when p.sort = 'date_asc' then d.pub  end) asc  nulls last,
        (case when p.sort = 'date_desc' then d.pub end) desc nulls last,
        (case when p.sort = 'firm_asc' then d.pub  end) desc nulls last,
        d.tie asc
    )::int as rn
  from docs d, p
),
tot as (
  select
    (select count(*) from docs)::int                        as doc_total,
    (select coalesce(sum(doc_findings), 0) from docs)::int  as finding_total
),
page_docs as (
  select o.raw_signal_id, o.rn
  from ordered o, p
  where o.rn > (p.page - 1) * p.per
    and o.rn <= p.page * p.per
),
page_rows as (
  select
    fl.rn,
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.document_id, f.published_date,
    f.firm_name, f.firm_key, f.category_code, f.category_label_ko, f.finding_text,
    f.finding_text_ko, f.translation_method, f.confidence,
    f.evidence_level, f.review_status, f.evidence_url, f.cfr_refs, f.mfds_refs, f.inspector_names
  from (
    select fi.finding_id, fi.raw_signal_id, pd.rn
    from filtered fi
    join page_docs pd on pd.raw_signal_id = fi.raw_signal_id
  ) fl
  join public.findings f on f.finding_id = fl.finding_id
),
page_docs_full as (
  select
    pr.rn,
    pr.raw_signal_id,
    min(pr.firm_name)      as firm_name,
    min(pr.source)         as source,
    min(pr.agency)         as agency,
    min(pr.published_date) as published_date,
    min(pr.document_id)    as document_id,
    min(pr.evidence_url)   as evidence_url,
    min(pr.firm_key)       as firm_key,
    count(*)::int          as matched_findings,
    jsonb_agg(
      jsonb_build_object(
        'finding_id',        pr.finding_id,
        'raw_signal_id',     pr.raw_signal_id,
        'source',            pr.source,
        'agency',            pr.agency,
        'document_id',       pr.document_id,
        'published_date',    pr.published_date,
        'firm_name',         pr.firm_name,
        'firm_key',          pr.firm_key,
        'translation_method', pr.translation_method,
        'confidence',        pr.confidence,
        'category_code',     pr.category_code,
        'category_label_ko', pr.category_label_ko,
        'finding_text',      pr.finding_text,
        'finding_text_ko',   pr.finding_text_ko,
        'evidence_level',    pr.evidence_level,
        'review_status',     pr.review_status,
        'evidence_url',      pr.evidence_url,
        'cfr_refs',          pr.cfr_refs,
        'mfds_refs',         pr.mfds_refs,
        'inspector_names',  pr.inspector_names
      ) order by pr.finding_id
    ) as findings
  from page_rows pr
  group by pr.rn, pr.raw_signal_id
),
fac_source as (
  select s.source as v, count(*)::int as c from searched s, p
  where (p.f_cat = '' or s.category_code = p.f_cat) and (p.f_month = '' or s.month = p.f_month)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
  group by s.source
),
fac_cat as (
  select s.category_code as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_month = '' or s.month = p.f_month)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
  group by s.category_code
),
fac_month as (
  select s.month as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
  group by s.month
),
fac_ev as (
  select s.evidence_level as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
  group by s.evidence_level
),
fac_rs as (
  select s.review_status as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
  group by s.review_status
),
fac_agency as (
  select s.agency as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_rs = '' or s.review_status = p.f_rs) and (p.f_country = '' or s.country_key = p.f_country_key)
  group by s.agency
),
dash_agency as (
  select f.agency as v, count(*)::int as c from filtered f group by f.agency
),
-- [054] 문서 기준 기관 축 — 화면의 기관 스탯은 이 축을 쓴다. 문서당 지적 건수가
--   소스마다 6.01(483) ~ 1.00(MFDS 회수)로 달라서, 지적사항 축으로 기관을 비교하면
--   추출 입도가 편중처럼 보인다.
dash_agency_docs as (
  select f.agency as v, count(distinct f.raw_signal_id)::int as c from filtered f group by f.agency
),
dash_cat as (
  select f.category_code as v, count(*)::int as c from filtered f group by f.category_code
),
dash_month as (
  select f.month as v, count(*)::int as c from filtered f group by f.month
),
-- [054] 문서 기준 월별 축 — 월 추이는 '유입량' 지표라 문서가 자연 단위다.
--   지적사항으로 세면 483 대량 백필 한 달이 다른 달을 전부 눌러버린다.
dash_month_docs as (
  select f.month as v, count(distinct f.raw_signal_id)::int as c from filtered f group by f.month
),
-- [056] 국가별 문서 수+지적 수 — 둘을 한 키에 함께 담는다(agency 처럼 별도 _docs 키를
--   또 만들지 않는다. dash.by_country[].docs/.findings 로 병기).
--   country_key='' 행(미확인)도 그대로 남긴다 -- 27.4%를 숨기지 않는다(정직 원칙).
--   정렬은 문서 수 내림차순(054 의 문서축 우선 원칙과 동형).
dash_country as (
  select
    f.country_key                                as v,
    count(distinct f.raw_signal_id)::int          as docs,
    count(*)::int                                 as findings
  from filtered f
  group by f.country_key
),
dash_firms as (
  select g.firm_key as k, dn.firm_name as name, g.c
  from (
    select f.firm_key, count(*)::int as c
    from filtered f
    where coalesce(f.firm_key, '') <> ''
    group by f.firm_key
    order by count(*) desc, f.firm_key asc
    limit 10
  ) g
  join lateral (
    select f2.firm_name
    from filtered f2
    where f2.firm_key = g.firm_key
    group by f2.firm_name
    order by count(*) desc, length(f2.firm_name) desc, f2.firm_name asc
    limit 1
  ) dn on true
)
select jsonb_build_object(
  'documents', coalesce(
      (select jsonb_agg(
         jsonb_build_object(
           'raw_signal_id',    d.raw_signal_id,
           'firm_name',        d.firm_name,
           'firm_key',         d.firm_key,
           'source',           d.source,
           'agency',           d.agency,
           'published_date',   d.published_date,
           'document_id',      d.document_id,
           'evidence_url',     d.evidence_url,
           'matched_findings', d.matched_findings,
           'findings',         d.findings
         ) order by d.rn
       ) from page_docs_full d),
      '[]'::jsonb),
  'totals', jsonb_build_object(
      'documents', (select doc_total from tot),
      'findings',  (select finding_total from tot)),
  'facets', jsonb_build_object(
      'by_source',        coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_source), '[]'::jsonb),
      'by_category',      coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_cat),    '[]'::jsonb),
      'by_month',         coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v desc)          from fac_month),  '[]'::jsonb),
      'by_evidence',      coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v asc)           from fac_ev),     '[]'::jsonb),
      'by_review_status', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_rs),     '[]'::jsonb),
      'by_agency',        coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_agency), '[]'::jsonb)),
  'dash', jsonb_build_object(
      'by_agency',   coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from dash_agency), '[]'::jsonb),
      'by_agency_docs', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from dash_agency_docs), '[]'::jsonb),
      'by_category', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from dash_cat),    '[]'::jsonb),
      'by_month',    coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v asc)          from dash_month),  '[]'::jsonb),
      'by_month_docs', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v asc)          from dash_month_docs), '[]'::jsonb),
      -- [056] 합계 정합: sum(by_country[].docs) = totals.documents,
      --   sum(by_country[].findings) = totals.findings (country_key='' 미확인 행 포함,
      --   filtered 전량을 country_key 로 그룹화하므로 행 손실이 없다).
      'by_country',  coalesce((select jsonb_agg(jsonb_build_object('v', v, 'docs', docs, 'findings', findings) order by docs desc, v asc) from dash_country), '[]'::jsonb),
      'top_firms',   coalesce((select jsonb_agg(jsonb_build_object('firm_key', k, 'firm_name', name, 'c', c) order by c desc, k asc) from dash_firms), '[]'::jsonb)),
  'page',          (select page from p),
  'docs_per_page', (select per from p),
  'pages',         (select case when (select per from p) > 0
                                then ((select doc_total from tot) + (select per from p) - 1) / (select per from p)
                                else 0 end),
  'sort',          (select sort from p)
);
$$;

-- 040 이 이미 anon/authenticated 에 grant 했고 함수 시그니처는 파라미터가 하나 늘었을 뿐
-- 이름·반환형이 그대로다 — create or replace 는 기존 grant 를 보존하므로 재부여 불필요
-- (054 관례 계승).

-- 검증(사람 실행용, 프로덕션 SQL Editor):
-- 1) 국가 필터 미적용 시 문서/지적 합계가 054 실측과 동일한지(무회귀):
--    select (r->'totals'->>'documents')::int, (r->'totals'->>'findings')::int
--    from public.findings_search() r;
-- 2) dash.by_country 합이 totals 와 일치하는지(합계 정합):
--    with r as (select public.findings_search() as j)
--    select
--      (select sum((e->>'docs')::int)     from r, jsonb_array_elements(j->'dash'->'by_country') e),
--      (r.j->'totals'->>'documents')::int,
--      (select sum((e->>'findings')::int) from r, jsonb_array_elements(j->'dash'->'by_country') e),
--      (r.j->'totals'->>'findings')::int
--    from r;
-- 3) p_country='UNKNOWN' 이 country_key='' 행만 돌려주는지(sentinel 의미론):
--    select (public.findings_search(p_country := 'UNKNOWN')->'totals'->>'documents')::int;
--    -- 위 값이 findings_country_unmapped() 의 findings 합 + site_country='' 인 findings 의
--    -- 문서 수와 대략 정합해야 한다(findings_country_unmapped 는 site_country<>'' 인 것만
--    -- 세므로 site_country='' 인 문서까지 더해야 완전 대조된다 — 사람 검증 시 유의).
-- 4) 실제 ISO2 코드 필터가 좁혀지는지:
--    select (public.findings_search(p_country := 'IN')->'totals'->>'documents')::int;
