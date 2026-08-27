-- ============================================================================
-- 066 — 실사일을 findings 에 실어 화면까지 잇는다 (순수 가산)
--
-- 왜: 검색 결과와 문서 페이지가 보여주는 날짜는 `published_date` 하나뿐인데, 이건
-- **우리가 그 문서를 확보한 날**이지 규제기관이 실사한 날이 아니다. 두 날짜가 갈리는
-- 정도가 소스마다 다르고, FDA 483 에서는 그 차이가 파괴적이다.
--
--   실측(raw_signals 전수 · 2026-08-27):
--     FDA 483                1,995건 파싱 / 1,994건이 공개일과 다름 · 평균 1,524일(4.2년)
--                            · 최대 6,143일(16.8년). 문서 941건이 공개일 2024-01-17
--                            하나를 공유하는데 실사는 2015~2019년이다(FOIA 일괄 공개).
--     MFDS                     639건 / 639건 다름 · 평균 231일
--     EU GMP NCR (EudraGMDP)    78건 /  78건 다름 · 평균 119일
--     MHRA GMP NCR               8건 /   8건 다름 · 평균 128일
--                            ─────────────────────────────────────────
--                            합계 2,719건이 "표시된 날짜 ≠ 실제 실사일"
--
-- 이건 표시 취향 문제가 아니라 **사실 왜곡**이다. 문서 페이지의 설명문에는 이미
-- "연도가 없으면 몇 년 전 지적이 현재 상태로 읽힌다 — 실명 업체 페이지에서 그건 사실
-- 왜곡이다"라는 설계 의도가 주석으로 적혀 있는데, 정작 FDA 483 941장에서는 그 의도가
-- 지고 있었다. 2015년 실사가 "2024-01-17"을 달고 나간다.
--
-- 부수 효과로 검색 결과의 변별력도 오른다 — 그 941장의 공개일은 값이 **하나**지만
-- 실사일은 219종이다.
--
-- ── 범위: 왜 경고서한과 캐나다 실사는 빼는가 ────────────────────────────────
--   * FDA Warning Letter (1,313건) — `letter_date`(서한일)와 `posted_date`(게시일)를
--     둘 다 갖고 있고 `published_date` = 게시일이다. 차이가 7~14일로 **정상적인 발행
--     지연**이라 "몇 년 전 것이 현재로 읽히는" 문제가 없다. 그리고 서한일은 실사일이
--     아니다 — 같은 칸에 넣으면 칸의 뜻이 무너진다. 넣지 않는다.
--   * Health Canada Inspection (1,824건) — raw_json 에 **실사일이 아예 없다**
--     (`ins_number`·`insType`·`rating` 등만 있다). 이건 표시 결함이 아니라 수집 결함
--     이고, 리포트카드를 다시 받아야 푼다. 이 마이그레이션의 범위 밖이다.
--
-- ── 키를 소스가 아니라 이름으로 찾는다 ──────────────────────────────────────
-- 소스별 하드코딩 표를 만들지 않는다(표는 새 소스가 들어오면 조용히 낡는다). 대신
-- 날짜 키 **이름**을 정해진 순서로 훑고 먼저 파싱되는 값을 쓴다:
--     record_date → inspection_end_date → inspection_end
-- 지금은 각각 FDA 483 / EU·MHRA NCR / MFDS 하나씩에만 있어 충돌이 없고, 같은 이름을
-- 쓰는 새 소스는 배선 없이 자동으로 잡힌다. Python 쪽 `inspection_date_from_raw`
-- (grm_findings.py)가 **같은 순서·같은 파싱**을 쓴다 — 두 곳이 갈리면 백필분과 신규
-- 적재분이 다른 값을 갖게 되므로 테스트가 두 구현의 일치를 잰다.
--
-- ── 순수 가산 계약(불가침) ─────────────────────────────────────────────────
--   * `findings_search` **시그니처 무변경**. PostgREST 는 인자가 하나만 달라도 404 를
--     주므로 라이브 화면이 즉시 깨진다(#681).
--   * 응답의 기존 키는 값·순서·타입 전부 무변경. documents[] 에 `inspection_date`
--     **한 개만** 추가된다. 값이 없는 문서는 빈 문자열이라 소비처가 없어도 무해하다.
--   * `published_date` 는 **손대지 않는다** — dedup 키·수집 창·발행 축이 전부 그 위에
--     서 있다. 실사일은 새 칸이고, 옛 칸을 대체하지 않는다.
-- ============================================================================

-- ── 1. 칼럼 ────────────────────────────────────────────────────────────────
alter table public.findings
  add column if not exists inspection_date text;

comment on column public.findings.inspection_date is
  '규제기관이 실사한 날(FDA 483=발부일, EU/MHRA NCR·MFDS=실사 종료일). YYYY-MM-DD 또는 '
  '빈 문자열. published_date(우리가 문서를 확보한 날)와 다른 축이며 대체하지 않는다.';

-- ── 2. 백필 (재실행 안전) ──────────────────────────────────────────────────
-- 백슬래시 이스케이프를 피하려고 정규식 대신 문자클래스를 쓴다.
with d as (
  select rs.raw_signal_id,
         nullif(btrim(coalesce(
           rs.raw_json::jsonb->>'record_date',
           rs.raw_json::jsonb->>'inspection_end_date',
           rs.raw_json::jsonb->>'inspection_end',
           '')), '') as v
  from public.raw_signals rs
),
parsed as (
  select raw_signal_id,
         case
           -- ★왕복 대조로 '달력에 없는 날'을 버린다. to_date 는 관대해서 02/30/2015 를
           --   조용히 2015-03-02 로 바꾼다 — 그러면 파이썬 쪽(달력 검증)과 갈린다.
           when v ~ '^[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]$'
                and to_char(to_date(v, 'MM/DD/YYYY'), 'MM/DD/YYYY') = v
             then to_char(to_date(v, 'MM/DD/YYYY'), 'YYYY-MM-DD')
           when left(v, 10) ~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$'
                and to_char(to_date(left(v, 10), 'YYYY-MM-DD'), 'YYYY-MM-DD') = left(v, 10)
             then left(v, 10)
           else ''
         end as insp
  from d
  where v is not null
)
update public.findings f
   set inspection_date = p.insp
  from parsed p
 where f.raw_signal_id = p.raw_signal_id
   and coalesce(f.inspection_date, '') is distinct from p.insp;

-- 값이 없는 행은 빈 문자열로 고정한다(NULL 과 '' 두 가지 '없음'을 만들지 않는다).
update public.findings
   set inspection_date = ''
 where inspection_date is null;

-- ── 3. RPC — documents[] 에 키 하나 추가 (시그니처 무변경) ──────────────────
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
  select
    fl.rn,
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.document_id, f.published_date,
    f.inspection_date,
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
    -- 실사일은 문서 속성이라 그 문서의 모든 finding 이 같은 값을 갖는다. min() 은
    -- published_date 와 같은 관례이고, 값이 없으면 빈 문자열이 그대로 나온다.
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
