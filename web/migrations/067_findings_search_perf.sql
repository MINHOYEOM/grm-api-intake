-- ============================================================================
-- 067 — findings_search 성능 수리 (동작 무변경 · 순수 리팩터)
--
-- 왜: 이 함수는 anon 역할의 statement_timeout **3초** 안에서 돌아야 하는데 한 호출이
-- **758ms** 였다. 여유가 4배뿐이라, 정본 재생성(62페이지를 연달아 호출)이 부하가 몰리는
-- 구간에서 타임아웃을 맞았다 — 08-27 실행에서 9페이지가 죽어 문서 583건이 빠졌고,
-- 그 전 08-22 실행도 1페이지를 잃은 채 머지됐다. 재시도(#830·#831)는 증상을 막았을
-- 뿐이고, 코퍼스가 자라면 다시 터진다.
--
-- ★프로파일부터 떴다(explain analyze · 26,594 findings / 6,529 문서 기준):
--     searched → docs → ordered          70ms
--     패싯 6 + 대시 7                    301ms   (그중 dash_firms 혼자 81ms)
--     페이지 조립(page_rows + jsonb)     302ms   (그중 PK 재조회 165ms)
--   추측으로 '전체 정렬이 문제'라고 짚었는데 **정렬은 70ms 로 병목이 아니었다.**
--   실제 병목은 아래 둘이고, 이 마이그레이션은 그 둘만 고친다.
--
-- ── 고치는 것 ①: page_rows 가 이미 읽은 행을 PK 로 다시 읽는다 ──────────────
-- 종전에는 `filtered`(26,594행) 전체를 page_docs(100행)와 해시 조인한 뒤, 나온 394행을
-- **한 건씩 findings_pkey 로 다시 조회**했다(394 loops · 1,576 버퍼 · 165ms). findings 는
-- finding_text/finding_text_ko 때문에 행이 넓어 TOAST 해제 비용이 붙는다.
-- 새 구조는 page_docs(100)를 축으로 `findings_rawsig_text_md5_uq`(선두 컬럼
-- raw_signal_id)를 타 문서당 한 번에 집는다(100 loops · 659 버퍼).
-- ★범위 판정은 `filtered` 와의 조인으로 그대로 한다 — **검색 술어를 복제하지 않는다.**
--   복제하면 두 곳이 갈리는 순간 검색 결과가 조용히 달라진다. finding_id 는 PK 라
--   이 조인은 행을 늘리지도 줄이지도 않는다(순수 범위 필터).
-- 실측: 페이지 조립 302ms → 171ms.
--
-- ── 고치는 것 ②: dash_firms 가 상위 10개마다 전수를 다시 훑는다 ─────────────
-- 종전 lateral 은 firm_key 하나마다 `filtered` 전체를 훑어 대표 표시명을 골랐다
-- (10 loops × 26,594행 · "Rows Removed by Filter: 26466" · 81ms).
-- 새 구조는 (firm_key, firm_name) 을 **한 번** 집계하고, 상위 10 과 대표명을 그 위에서
-- 고른다. 대표명 선택 규칙(건수 desc → 이름 길이 desc → 이름 asc)은 그대로다.
-- 동치 확인: 두 구현의 산출 jsonb 가 `=` 로 참(실측).
--
-- ── 건드리지 않는 것 ───────────────────────────────────────────────────────
--   * 시그니처 — PostgREST 는 인자가 하나만 달라도 404 다(#681).
--   * 응답의 모든 키·값·순서 — 이번엔 신설 키조차 없다. **완전 동일**이 계약이다.
--     20개 파라미터 조합(기본·페이지·소스·분류·월·기관·국가·질의(영/한/%/_)·정렬 3종·
--     조합·근거·검토상태·빈 결과·per=7)의 md5 를 적용 전후로 대조해 증명한다.
--   * 패싯 6종의 개별 집계 — 각자 **자기 필터만 뺀** 서로 다른 행 집합이라 한 번에 묶을
--     수 없다. 묶으려면 필터별 분기를 만들어야 하고 그건 동작 변경 위험이라 하지 않았다.
--     (대시 7종은 같은 집합이라 묶을 수 있지만, 위 둘로 여유가 충분해 이번 범위 밖.)
-- ============================================================================

create or replace function public.findings_search(
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
  p_country text default ''::text
)
returns jsonb
language sql
stable
set search_path to 'public', 'extensions'
set work_mem to '8MB'
as $function$
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
    upper(coalesce(btrim(p_country), ''))                          as f_country,
    (case when upper(coalesce(btrim(p_country), '')) = 'UNKNOWN' then ''
          else upper(coalesce(btrim(p_country), '')) end)          as f_country_key,
    case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
         then p_sort else 'date_desc' end                          as sort,
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
  -- [067] page_docs(한 페이지분)를 축으로 raw_signal_id 인덱스를 타 넓은 행을 문서당 한
  -- 번에 집는다. 범위 판정은 filtered 와의 조인이 하므로 검색 술어를 복제하지 않는다.
  select
    pd.rn,
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.document_id, f.published_date,
    f.inspection_date,
    f.firm_name, f.firm_key, f.category_code, f.category_label_ko, f.finding_text,
    f.finding_text_ko, f.translation_method, f.confidence,
    f.evidence_level, f.review_status, f.evidence_url, f.cfr_refs, f.mfds_refs, f.inspector_names
  from page_docs pd
  join public.findings f on f.raw_signal_id = pd.raw_signal_id
  join filtered fl on fl.finding_id = f.finding_id
),
page_docs_full as (
  select
    pr.rn,
    pr.raw_signal_id,
    min(pr.firm_name)      as firm_name,
    min(pr.source)         as source,
    min(pr.agency)         as agency,
    min(pr.published_date) as published_date,
    min(coalesce(pr.inspection_date, '')) as inspection_date,
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
dash_agency_docs as (
  select f.agency as v, count(distinct f.raw_signal_id)::int as c from filtered f group by f.agency
),
dash_cat as (
  select f.category_code as v, count(*)::int as c from filtered f group by f.category_code
),
dash_month as (
  select f.month as v, count(*)::int as c from filtered f group by f.month
),
dash_month_docs as (
  select f.month as v, count(distinct f.raw_signal_id)::int as c from filtered f group by f.month
),
dash_country as (
  select
    f.country_key                                as v,
    count(distinct f.raw_signal_id)::int          as docs,
    count(*)::int                                 as findings
  from filtered f
  group by f.country_key
),
-- [067] (firm_key, firm_name) 을 한 번만 집계한다. 종전 lateral 은 상위 10개마다
-- filtered 전체를 다시 훑었다(10 × 26,594행 · 81ms).
firm_counts as (
  select f.firm_key, f.firm_name, count(*)::int as nc
  from filtered f
  where coalesce(f.firm_key, '') <> ''
  group by f.firm_key, f.firm_name
),
firm_totals as (
  select fc.firm_key, sum(fc.nc)::int as c
  from firm_counts fc
  group by fc.firm_key
  order by sum(fc.nc) desc, fc.firm_key asc
  limit 10
),
-- 대표 표시명 선택 규칙은 종전 lateral 과 같다: 건수 desc → 이름 길이 desc → 이름 asc.
firm_best_name as (
  select distinct on (fc.firm_key) fc.firm_key, fc.firm_name
  from firm_counts fc
  order by fc.firm_key, fc.nc desc, length(fc.firm_name) desc, fc.firm_name asc
),
dash_firms as (
  select ft.firm_key as k, fb.firm_name as name, ft.c
  from firm_totals ft
  join firm_best_name fb on fb.firm_key = ft.firm_key
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
           'inspection_date',  d.inspection_date,
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
      'by_country',  coalesce((select jsonb_agg(jsonb_build_object('v', v, 'docs', docs, 'findings', findings) order by docs desc, v asc) from dash_country), '[]'::jsonb),
      'top_firms',   coalesce((select jsonb_agg(jsonb_build_object('firm_key', k, 'firm_name', name, 'c', c) order by c desc, k asc) from dash_firms), '[]'::jsonb)),
  'page',          (select page from p),
  'docs_per_page', (select per from p),
  'pages',         (select case when (select per from p) > 0
                                then ((select doc_total from tot) + (select per from p) - 1) / (select per from p)
                                else 0 end),
  'sort',          (select sort from p)
);
$function$;
