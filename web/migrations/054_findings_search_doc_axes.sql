-- ============================================================================
-- 054_findings_search_doc_axes.sql — 대시보드 축을 '문서'로 (지적사항 축은 병존)
--
-- 배경: /findings/ 대시보드의 기관별 숫자가 **지적사항 건수**였다. 그런데 문서당
--   지적 건수가 소스마다 구조적으로 다르다 — FDA 483 = 6.01건/문서, 경고서한 = 2.53,
--   MFDS 회수·행정처분 = 1.00. 그래서 같은 코퍼스가 지적사항 축에서는 FDA:MFDS =
--   87:13 으로, 문서 축에서는 69:31 로 보인다. 화면의 대표 숫자가 "무엇을 세는지"에
--   따라 편중의 크기가 두 배 넘게 달라진 것이다.
--
--   이건 트렌드 페이지에서 이미 한 번 겪은 결함과 같은 계열이다(052/053 — 성격이 다른
--   모집단을 한 축에 합치면 추세를 못 잰다). 여기선 **분모가 문서마다 다른 지표를
--   기관 비교에 쓴 것**이 원인이다.
--
-- 처방: 대시보드에 **문서 기준 축을 추가**한다. 지적사항 축을 없애지 않는다 — 카테고리
--   분포처럼 지적사항이 자연 단위인 지표가 남아 있고, 툴팁에 두 숫자를 병기해야
--   "왜 다른가"가 화면에서 설명되기 때문이다. 축을 바꾸는 게 아니라 **축을 밝힌다**.
--
-- supersede 체인: findings_search = 026 → 027 → 028 → 030 → 036 → 040 → **이 파일(054)**
--   (findings_document 는 036 정의가 현행 그대로 — 이 파일은 dash 블록만 건드린다.)
--
-- ─ 생성 방식(수작업 복사 금지) ─────────────────────────────────────────────
--   040 의 findings_search 정의를 **기계적으로 읽어** 앵커 4곳에 덧붙여 생성했다.
--   앵커가 정확히 1회가 아니면 생성기가 중단한다.
--
-- ─ 변경 지점 4곳(전부 추가, 기존 키 무변경) ────────────────────────────────
--   ① CTE `dash_agency_docs` — 기관별 count(distinct raw_signal_id)
--   ② CTE `dash_month_docs`  — 월별 count(distinct raw_signal_id)
--   ③ dash.by_agency_docs 키 신설
--   ④ dash.by_month_docs  키 신설
--   기존 dash.by_agency/by_month(지적사항 건수)는 **그대로 남는다** — 구버전
--   findings.js 셸이 그대로 동작하고(하위호환), 신버전은 툴팁에 둘을 병기한다.
--
-- ─ 합계 정합 ───────────────────────────────────────────────────────────────
--   문서(raw_signal_id) 1건은 agency 가 정확히 하나다 → sum(by_agency_docs.c) =
--   totals.documents 가 항상 성립한다. by_month_docs 도 같다(한 문서의 지적은
--   published_date 를 공유한다). 화면의 [문서] 스탯과 기관 블록 합이 어긋나지 않는다.
--
-- ─ 노출 관점 ───────────────────────────────────────────────────────────────
--   새로 공개되는 **행은 0건**이다. 이미 반환하던 집합을 다른 단위로 한 번 더 셀 뿐,
--   행 본문(finding_text 등)을 새로 싣지 않는다. RLS(010) 게이트는 그대로다.
--
-- 전제: 040 적용.
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
  p_docs_per_page int  default 24
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
    case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
         then p_sort else 'date_desc' end                          as sort,
    -- Minor 2: 상한 400,000 — (page-1)*per(최대 100) = 4천만 < 2^31 로 int overflow 차단.
    least(greatest(coalesce(p_page, 1), 1), 400000)                as page,
    least(greatest(coalesce(p_docs_per_page, 24), 1), 100)         as per
),
searched as (
  select
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date, f.firm_name,
    f.firm_key, f.category_code, f.evidence_level, f.review_status,
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
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.source
),
fac_cat as (
  select s.category_code as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_month = '' or s.month = p.f_month)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.category_code
),
fac_month as (
  select s.month as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.month
),
fac_ev as (
  select s.evidence_level as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.evidence_level
),
fac_rs as (
  select s.review_status as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.review_status
),
fac_agency as (
  select s.agency as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_rs = '' or s.review_status = p.f_rs)
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
      'top_firms',   coalesce((select jsonb_agg(jsonb_build_object('firm_key', k, 'firm_name', name, 'c', c) order by c desc, k asc) from dash_firms), '[]'::jsonb)),
  'page',          (select page from p),
  'docs_per_page', (select per from p),
  'pages',         (select case when (select per from p) > 0
                                then ((select doc_total from tot) + (select per from p) - 1) / (select per from p)
                                else 0 end),
  'sort',          (select sort from p)
);
$$;
