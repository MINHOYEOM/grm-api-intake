-- ============================================================================
-- 036_findings_rpc_inspector_names.sql — [FIND-483-SIGNER] 실사관 투영 1키 추가
--
-- 목적: `findings.inspector_names`(jsonb, 002 부터 존재하나 전량 빈값이던 컬럼)를 웹이
--   읽을 수 있도록 두 서빙 RPC 의 findings[] 투영에 **키 1개만** 얹는다. 집계·프로파일
--   페이지·필터는 이 파일의 범위가 **아니다** — 008 의 "조사관별 집계 금지" 계약은 그대로
--   유효하다(그 사유가 '데이터 부재'이므로, 데이터가 성숙하면 별도 결정으로 개정한다).
--   이 파일이 가능하게 하는 것은 문서 카드의 **사실 표기** 하나뿐이다.
--
-- supersede 체인: findings_search = 026 → 027 → 028 → 030 → **이 파일(036)**
--                 findings_document = 028 → **이 파일(036)**
--
-- ─ 생성 방식(수작업 복사 금지) ─────────────────────────────────────────────
--   이 파일의 두 함수 본문은 손으로 옮겨 적은 것이 아니라, 030(findings_search)·
--   028(findings_document)의 **정의를 기계적으로 읽어** 앵커 지점에 키만 삽입해 생성했다.
--   생성기는 앵커가 정확히 1회가 아니면 중단한다(부분 생성 방지). 025 적용 때 실증된
--   함정 — 옛 파일에서 복사하다 그 사이 적용된 다른 마이그레이션을 조용히 되돌리는 것 —
--   을 피하려는 조치이며, 아래 등가성 실측이 그 결과를 사후 검증한다.
--
-- ─ 변경 지점 3곳 ───────────────────────────────────────────────────────────
--   ⑴ findings_search `page_rows` CTE 는 컬럼을 **명시 열거**한다(f.* 아님) → 거기에
--      f.inspector_names 를 싣지 않으면 투영에서 pr.inspector_names 를 참조할 수 없다.
--   ⑵ findings_search findings[] jsonb_build_object 에 'inspector_names' 키.
--   ⑶ findings_document findings[] jsonb_build_object 에 'inspector_names' 키.
--      (findings_document 의 rows_out 은 f.* 라 ⑴에 해당하는 변경이 없다.)
--
-- ─ 안전 계약(불변) ─────────────────────────────────────────────────────────
--   · 시그니처·인자·반환형 불변 → 클라이언트 호출 규약 무변경.
--   · security invoker + RLS 유지 — 공개 게이트는 010/033/034 의 행 정책이 그대로 판정한다.
--     inspector_names 는 **이미 공개 게이트를 통과한 행**의 컬럼을 하나 더 읽을 뿐이며,
--     새로 노출되는 **행은 0건**이다.
--   · 원문 텍스트·URL 필드를 새로 반환하지 않는다(007/008 안전 계약 유지).
--   · anon EXECUTE 권한은 create or replace 라 보존된다(적용 후 실측 확인).
--
-- ─ 적용 실측(2026-07-30, 프로덕션) ─────────────────────────────────────────
--   등가성 증명: findings_search 를 17종 파라미터 조합(무필터 랜딩·한글/영문 질의·
--   source/category/month/evidence/review_status/agency 필터·정렬 3종·page 5/최대·
--   per 1/100·'[]' 질의(030 경화 회귀)·이스케이프 질의·공백 질의)으로 호출해
--   **적용 전 md5** 와 **적용 후 결과에서 inspector_names 키만 제거한 md5** 를 대조 →
--   17/17 IDENTICAL. 즉 이 변경은 **순수 가산(purely additive)** 이다.
--   키 부착: 반환된 전 finding 에 존재(랜딩 122/122 등). findings_document 13/13.
--   anon 경로: 24문서·125 findings 전건 키 부착·미번역 누출 0·prosecdef=false 유지.
--
-- 전제: 030(findings_search 현행) + 028(findings_document 현행) 적용 상태.
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
          coalesce((select string_agg(mr.v, ' ') from jsonb_array_elements_text(f.mfds_refs) mr(v)), '')
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
dash_cat as (
  select f.category_code as v, count(*)::int as c from filtered f group by f.category_code
),
dash_month as (
  select f.month as v, count(*)::int as c from filtered f group by f.month
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
      'by_category', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from dash_cat),    '[]'::jsonb),
      'by_month',    coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v asc)          from dash_month),  '[]'::jsonb),
      'top_firms',   coalesce((select jsonb_agg(jsonb_build_object('firm_key', k, 'firm_name', name, 'c', c) order by c desc, k asc) from dash_firms), '[]'::jsonb)),
  'page',          (select page from p),
  'docs_per_page', (select per from p),
  'pages',         (select case when (select per from p) > 0
                                then ((select doc_total from tot) + (select per from p) - 1) / (select per from p)
                                else 0 end),
  'sort',          (select sort from p)
);
$$;


create or replace function public.findings_document(p_finding_id text)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
with anchor as (
  select f.raw_signal_id
  from public.findings f
  where f.finding_id = coalesce(p_finding_id, '')
  limit 1
),
rows_out as (
  select f.*
  from public.findings f, anchor a
  where f.raw_signal_id = a.raw_signal_id
)
select case when not exists (select 1 from anchor) then 'null'::jsonb
else jsonb_build_object(
  'raw_signal_id',  (select raw_signal_id from anchor),
  'firm_name',      (select min(firm_name) from rows_out),
  -- 028: 문서 레벨 firm_key — findings_search 의 documents[] 와 같은 계약(같은 min() 규칙).
  'firm_key',       (select min(firm_key) from rows_out),
  'source',         (select min(source) from rows_out),
  'agency',         (select min(agency) from rows_out),
  'published_date', (select min(published_date) from rows_out),
  'document_id',    (select min(document_id) from rows_out),
  'evidence_url',   (select min(evidence_url) from rows_out),
  'findings', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'finding_id',        r.finding_id,
        'raw_signal_id',     r.raw_signal_id,
        'source',            r.source,
        'agency',            r.agency,
        'document_id',       r.document_id,
        'published_date',    r.published_date,
        'firm_name',         r.firm_name,
        'category_code',     r.category_code,
        'category_label_ko', r.category_label_ko,
        'finding_text',      r.finding_text,
        'finding_text_ko',   r.finding_text_ko,
        'evidence_level',    r.evidence_level,
        'review_status',     r.review_status,
        'evidence_url',      r.evidence_url,
        'cfr_refs',          r.cfr_refs,
        'mfds_refs',         r.mfds_refs,
        'inspector_names',   r.inspector_names,
        -- 028: 클라이언트 카드 조립부가 읽는 3종 — findings_search 의 findings[] 와 동일 계약.
        'firm_key',           r.firm_key,
        'translation_method', r.translation_method,
        'confidence',         r.confidence
      ) order by r.finding_id
    ) from rows_out r), '[]'::jsonb)
) end;
$$;
