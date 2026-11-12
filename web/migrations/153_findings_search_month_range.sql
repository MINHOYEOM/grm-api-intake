-- 153 findings_search 기간 필터 — `p_month` 가 한 달(`YYYY-MM`)뿐 아니라 **월 범위**(`YYYY-MM..YYYY-MM`)도 받는다.
-- Max local web/migrations prefix was 152 on main. Additive only — RPC 이름·13인자 시그니처·반환 JSON 모양 불변.
--
-- ★왜(2026-11-13, 대표 요청 ④): 검색 결과 화면(/search/)에 "기간" 필터가 필요한데, 원 계산
--   (`findings_search_compute`, 082 본문 · 086 에서 rename)은 `s.month = p.f_month` 한 달 일치만 안다.
--   "최근 1년"을 고르려면 12번 부르거나 클라이언트가 걸러야 해서 쓸 수 없었다.
--
-- ★무엇을: 원 계산 본문에서 **월 조건 6곳만** 바꾼다(filtered 1 · fac_source/cat/ev/rs/agency 5;
--   fac_month 는 원래 월 조건이 없어 그대로 — 기간을 골라도 월별 분포는 전 기간을 보여 준다).
--     `s.month = p.f_month`  →  `s.month between p.f_mfrom and p.f_mto`
--   `p` CTE 에 두 열을 더한다:
--     · `p_month` 에 `..` 가 없으면 f_mfrom = f_mto = f_month — **종전과 같은 한 달 일치**
--       (`x between a and a` ≡ `x = a`; month 가 null 인 행은 양쪽 다 빠진다).
--     · `..` 가 있으면 앞·뒤를 나눠 쓰고, 빈 쪽은 열린 끝(`0000-00` / `9999-99`).
--       예: `2025-11..2026-10`(12개월) · `2025-11..`(그 뒤 전부) · `..2024-12`(그 앞 전부).
--   month 는 `left(published_date, 7)` 이고 라이브 28,677행 전부 `^\d{4}-\d{2}$` 모양이다
--   (2026-11-13 실측, 위반 0) — 문자열 비교가 날짜 비교와 같다.
--
-- ★캐시(086/151) 정합: 캐시 키 함수 `findings_search_cache_args` 의 월 정규화는
--   `coalesce(p_month, '')` 이고 원 계산의 `f_month` 도 그대로다 — **키 함수는 손대지 않는다**.
--   범위 값은 문자열 그대로 키가 되므로 같은 범위는 같은 키로 접히고, 범위가 다르면 다른 키다.
--   캐시 목록(시드·용어 사례)은 전부 p_month='' 라 기존 payload 는 이 적용으로 바뀌지 않는다
--   (재갱신 불필요). 범위 질의는 목록에 없으므로 종전처럼 원 계산을 탄다.
--
-- ★계약: `create or replace` 같은 시그니처 — OID·권한(anon 이 invoker 공개 RPC 를 거쳐 부른다)
--   그대로. 공개 RPC `findings_search`(086 래퍼)는 손대지 않는다. 반환 키 추가 0.
--   본문은 082 원문에서 위 치환만 한 것이다 — tests/test_findings_search_month_range.py 가 082 본문에
--   같은 치환을 해서 이 파일 본문과 **글자 그대로 같은지** 대조한다(복제 표류 방지).
--
-- ★적용 순서: 이 migration 먼저 → 그 뒤 화면 PR(search.js 가 범위를 보냄). 화면이 먼저 나가면
--   옛 함수는 `'2025-11..2026-10'` 을 한 달 값으로 보고 0건을 낸다 — 화면은 그 경우를 알아채고
--   (월별 분포에는 그 기간 행이 있는데 결과 0) 기간 필터를 숨기도록 방어해 두었다.
--
-- 적용: 비서실(대표 승인 뒤). 적용 후 확인:
--   -- ① 종전 값 불변 — 적용 전에 같은 질의의 md5 를 받아 두고 대조(같아야 한다).
--   select m, md5(public.findings_search_compute(p_month := m)::text)
--     from unnest(array['', '2026-10', '2024-03']) m;
--   -- ② 범위 = 월 합 — 두 수가 같아야 한다.
--   select (public.findings_search_compute(p_month := '2026-08..2026-10')->'totals'->>'findings')::int as ranged,
--          (select sum((public.findings_search_compute(p_month := m)->'totals'->>'findings')::int)
--             from unnest(array['2026-08','2026-09','2026-10']) m) as summed;
--   -- ③ 열린 끝
--   select (public.findings_search_compute(p_month := '2026-01..')->'totals'->>'findings')::int;
-- 되돌리기: 082 의 `create or replace function public.findings_search(` 전문을 이름만
--   `findings_search_compute` 로 바꿔 다시 실행한다(같은 시그니처 — 권한 유지).

create or replace function public.findings_search_compute(
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
)
returns jsonb
language sql
stable
security invoker
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
    (case when position('..' in coalesce(p_month, '')) > 0
          then coalesce(nullif(split_part(p_month, '..', 1), ''), '0000-00')
          else coalesce(p_month, '') end)                         as f_mfrom,
    (case when position('..' in coalesce(p_month, '')) > 0
          then coalesce(nullif(split_part(p_month, '..', 2), ''), '9999-99')
          else coalesce(p_month, '') end)                         as f_mto,
    coalesce(p_evidence, '')                                       as f_ev,
    coalesce(p_review_status, '')                                  as f_rs,
    coalesce(p_agency, '')                                         as f_agency,
    upper(coalesce(btrim(p_country), ''))                          as f_country,
    (case when upper(coalesce(btrim(p_country), '')) = 'UNKNOWN' then ''
          else upper(coalesce(btrim(p_country), '')) end)          as f_country_key,
    (lower(coalesce(btrim(p_orig_lang), '')) = 'en')                as f_orig_en,
    coalesce(p_text_only, false)                                    as f_text_only,
    case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
         then p_sort else 'date_desc' end                          as sort,
    least(greatest(coalesce(p_page, 1), 1), 400000)                as page,
    least(greatest(coalesce(p_docs_per_page, 24), 1), 100)         as per
),
searched as (
  select
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date, f.firm_name,
    f.firm_key, f.category_code, f.evidence_level, f.review_status, f.country_key,
    f.original_is_english,
    left(f.published_date, 7) as month
  from public.findings f, p
  where p.q = ''
     or (
          case when p.f_text_only then
            coalesce(f.finding_text, '')       || ' ' ||
            coalesce(f.finding_text_ko, '')
          else
            coalesce(f.finding_text, '')       || ' ' ||
            coalesce(f.finding_text_ko, '')    || ' ' ||
            coalesce(f.firm_name, '')          || ' ' ||
            coalesce(f.document_id, '')        || ' ' ||
            coalesce(f.agency, '')             || ' ' ||
            coalesce(f.source, '')             || ' ' ||
            coalesce(f.published_date, '')     || ' ' ||
            coalesce(f.evidence_level, '')      || ' ' ||
            coalesce(f.review_status, '')      || ' ' ||
            replace(coalesce(f.review_status, ''), '_', ' ') || ' ' ||
            coalesce(f.category_code, '')      || ' ' ||
            coalesce(f.category_label_ko, '')  || ' ' ||
            coalesce(f.translation_method, '') || ' ' ||
            coalesce((select string_agg(cr.v, ' ') from jsonb_array_elements_text(f.cfr_refs)  cr(v)), '') || ' ' ||
            coalesce((select string_agg(mr.v, ' ') from jsonb_array_elements_text(f.mfds_refs) mr(v)), '') || ' ' ||
            coalesce((select string_agg(ins.v, ' ') from jsonb_array_elements_text(f.inspector_names) ins(v)), '')
          end
        ) ilike '%' || p.q_esc || '%'
),
filtered as (
  select s.* from searched s, p
  where (p.f_source = '' or s.source          = p.f_source)
    and (p.f_cat    = '' or s.category_code   = p.f_cat)
    and (p.f_month  = '' or s.month between p.f_mfrom and p.f_mto)
    and (p.f_ev     = '' or s.evidence_level  = p.f_ev)
    and (p.f_rs     = '' or s.review_status   = p.f_rs)
    and (p.f_agency = '' or s.agency          = p.f_agency)
    and (p.f_country = '' or s.country_key     = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
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
  where (p.f_cat = '' or s.category_code = p.f_cat) and (p.f_month = '' or s.month between p.f_mfrom and p.f_mto)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.source
),
fac_cat as (
  select s.category_code as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_month = '' or s.month between p.f_mfrom and p.f_mto)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.category_code
),
fac_month as (
  select s.month as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.month
),
fac_ev as (
  select s.evidence_level as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month between p.f_mfrom and p.f_mto) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.evidence_level
),
fac_rs as (
  select s.review_status as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month between p.f_mfrom and p.f_mto) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.review_status
),
fac_agency as (
  select s.agency as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month between p.f_mfrom and p.f_mto) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_rs = '' or s.review_status = p.f_rs) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
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
