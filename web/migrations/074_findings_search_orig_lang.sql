-- ============================================================================
-- 074 — 영어판 검색의 원문 언어 축 (`p_orig_lang`)
--
-- 왜: `/en/findings/` 첫 화면이 한국어였다. 코퍼스 전체로는 영어 원문이 91.7%인데
-- (FDA 14,936 · HC 9,505 · EMA 78 · MHRA 8) 기본 정렬이 최신순이고 식약처가 가장 최근
-- 편입분이라, 영어 사용자가 여는 첫 3쪽 지적 135건 중 **90건이 한글 본문**이었다
-- (2026-09-04 라이브 실측). 정적 문서 페이지는 4단계에서 원문이 영어인 문서만 골라
-- 냈는데(`doc_is_english`) 런타임 검색에는 그 필터가 없어서 생긴 비대칭이다.
--
-- ★판정은 **기관이 아니라 본문**으로 한다. 기관으로 가르면 지금은 맞지만 낡는다 —
--   실측에서 MFDS 2,125건 중 36건이 이미 한글이 아니었다(전부 `rejected` 인 OCR 잡음
--   `0000 0000…` 이라 공개 범위 밖이지만, "MFDS = 한국어"가 데이터의 사실이 아니라
--   현재의 우연이라는 것을 보여 준다). 손목록은 낡고, 데이터에서 파생한 것은 안 낡는다.
--
-- ── 저장 열로 두는 이유 ────────────────────────────────────────────────────
-- 술어를 질의 시점에 걸면 `finding_text` 를 행마다 읽어야 한다. 이 표는 72MB 이고
-- 본문 두 벌 때문에 행이 넓어 TOAST 해제 비용이 붙는다 — 068 이 165ms 를 깎아 내며
-- 피한 바로 그 비용이다. 생성 열(stored)로 한 번 계산해 두고 `searched` 는 불린만
-- 실어 나른다. 생성 열이라 원문이 바뀌면 자동으로 따라오고, 사람이 갱신을 잊을 수 없다.
--
-- ── 시그니처를 바꾸는 것에 대하여 ──────────────────────────────────────────
-- 068 은 "시그니처는 건드리지 않는다 — PostgREST 는 인자가 하나만 달라도 404 다(#681)"
-- 를 계약으로 못박았다. 여기서는 축이 하나 늘어야 해서 불가피하게 바꾸되, 다음을 지킨다:
--   * 신설 인자는 **맨 뒤 + 기본값 ''** — 11인자로 부르는 현행 사이트·스크립트는 그대로
--     동작한다(PostgREST 는 제공된 인자가 부분집합이면 해석한다).
--   * drop + create 를 **한 질의로** 보낸다 → 암묵 트랜잭션이라 중간 상태가 없다.
--     (overload 로 남겨 두면 11인자 호출이 모호해져 그쪽이 오히려 장애다.)
--   * 적용 순서는 **마이그레이션 먼저, 사이트 배포 나중** — 반대로 하면 새 JS 가
--     없는 인자를 보내 404 를 맞는다.
--
-- ── 동작 계약 ──────────────────────────────────────────────────────────────
--   p_orig_lang = ''    → 종전과 **완전히 동일**(기본값). 응답 md5 로 증명한다.
--   p_orig_lang = 'en'  → 원문에 한글이 없는 지적만. 결과·총계·페이지·패싯·대시가
--                         전부 같은 모집단에서 나온다(한 곳만 걸면 숫자가 갈린다).
--
-- ── 이 파일과 배포본을 그대로 대조할 수 있게 둔다 ──────────────────────────
-- 함수 본문에는 주석을 두지 않는다. 그래야 `select md5(prosrc) from pg_proc …` 이
-- 이 파일의 `$function$` 사이 본문 md5 와 **정확히 같고**, 누군가 프로덕션에서 직접
-- 함수를 고쳤는지 한 줄로 확인할 수 있다(적용 시점 실측: md5 555b0482…, 13,614자).
-- 068 이 본문에 달아 두었던 주석은 그 판단이 사라지지 않도록 여기로 옮겨 적는다:
--   * page_rows — page_docs(한 페이지분)를 축으로 raw_signal_id 인덱스를 타 넓은 행을
--     문서당 한 번에 집는다. 범위 판정은 filtered 와의 조인이 하므로 **검색 술어를
--     복제하지 않는다**(복제하면 두 곳이 갈리는 순간 결과가 조용히 달라진다).
--   * firm_counts — (firm_key, firm_name) 을 한 번만 집계한다. 종전 lateral 은 상위
--     10개마다 filtered 전체를 다시 훑었다(10 × 26,594행 · 81ms).
--   * firm_best_name — 대표 표시명 규칙은 종전 lateral 과 같다:
--     건수 desc → 이름 길이 desc → 이름 asc.
--
-- ── 적용 실측(2026-09-04) ──────────────────────────────────────────────────
--   * 기본 호출 21조합 응답 md5 **적용 전후 전부 동일** — 종전 동작 무변경 증명.
--   * 서버측 실행시간: 기본 568ms · `p_orig_lang='en'` 546ms(068 기준선 554ms 유지).
--   * `p_orig_lang='en'` → 문서 6,185→4,827 · 지적 24,944→22,886, 첫 쪽 42건 중
--     한글 **0건**, 기관 패싯에서 MFDS 가 빠져 죽은 칩이 생기지 않는다.
-- ============================================================================

-- 원문 언어 판정 = 본문에 한글(음절·자모)이 없는가. `Société` 같은 악센트 라틴은
-- 영어로 통과한다(비ASCII 전체를 막으면 정상 영문을 걸러낸다 — 실측 확인).
alter table public.findings
  add column if not exists original_is_english boolean
  generated always as (
    finding_text is not null and finding_text !~ '[가-힣ᄀ-ᇿ㄰-㆏]'
  ) stored;

create index if not exists findings_original_is_english_idx
  on public.findings (original_is_english);

-- 11인자 판을 먼저 내린다 — 남겨 두면 11인자 호출이 두 함수 사이에서 모호해진다.
drop function if exists public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text);

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
  p_country text default ''::text,
  p_orig_lang text default ''::text
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
    (lower(coalesce(btrim(p_orig_lang), '')) = 'en')                as f_orig_en,
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
  where (p.f_cat = '' or s.category_code = p.f_cat) and (p.f_month = '' or s.month = p.f_month)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.source
),
fac_cat as (
  select s.category_code as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_month = '' or s.month = p.f_month)
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
    and (p.f_month = '' or s.month = p.f_month) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.evidence_level
),
fac_rs as (
  select s.review_status as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_agency = '' or s.agency = p.f_agency) and (p.f_country = '' or s.country_key = p.f_country_key)
    and (not p.f_orig_en or s.original_is_english)
  group by s.review_status
),
fac_agency as (
  select s.agency as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
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
