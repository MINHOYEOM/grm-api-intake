-- ============================================================================
-- 030_findings_search_hardening.sql — [FIND-1] canonical search 경화
--   (Codex 통합 정밀점검 2026-07-16: Major 1 + Minor 1 + Minor 2 수리)
--
-- supersede 체인: 026(원본) → 027(대시보드 축) → 028(투영 3종) → **이 파일(030)**이
--   findings_search 를 create or replace 로 supersede 한다. findings_document 는 028
--   정의가 현행 그대로다(이 파일의 세 결함 모두 무관 — 페이지네이션·blob·집계가 없다).
--   ※ 029 는 데이터 정정(HTML 엔티티)이라 함수 체인과 무관, 번호만 사이에 있다.
--
-- ─ Major 1: 무검색 랜딩 temp spill ─────────────────────────────────────────
--   실측: work_mem 이 2184kB 인 인스턴스에서 searched/filtered CTE 의 materialize
--   (≈3.05MiB 쓰기)가 한도를 넘겨 temp 로 스필했다(anon: temp read=4095 written=390,
--   실행 ~127ms). 026b 의 "spill 0" 검증은 027 이 대시보드 축(dash_* 4개 = filtered
--   추가 소비)을 얹기 **전** 측정이라, 축 추가 후 다중 소비 재측정을 빠뜨린 것이 원인.
--   수리 = 함수에 `set work_mem = '8MB'`. 실측 근거: 8MB 에서 웜 119ms·spill 완전
--   소멸(shared hit 만). materialize 실사용 ≈3MiB 라 8MB 는 2.6배 마진이고, 함수 실행
--   동안만 적용되므로 세션·전역 설정 불변. 동시 호출 시 추가 소비도 실사용분(≈3MiB)뿐.
--
-- ─ Minor 2: 극단 p_page 정수 overflow ──────────────────────────────────────
--   p_page=2147483647 이면 (page-1)*per 의 int 연산이 22003(integer out of range)로
--   HTTP 400. 수리 = 상한 클램프 400,000(400k 페이지 × per 최대 100 = 4천만 < 2^31,
--   실 코퍼스 ~1.4천 페이지의 280배 여유). 범위 밖 페이지는 종전처럼 빈 documents.
--
-- ─ Minor 1: 검색 semantics 표류(축소·확대 동시) ────────────────────────────
--   ①확대(버그성): blob 이 `cfr_refs::text` 를 실어 **JSON 구두점이 검색 대상**이 됐다
--     — `[]` 질의가 8,168건 전부 매치(빈 배열 리터럴), `["` 류도 매치. 종전 클라이언트는
--     배열 **원소만** join 했다. 수리 = jsonb_array_elements_text 로 원소만 추출(빈 배열
--     은 '' — 종전과 동치). 검색 시에만 행당 평가되며 실측 비용 증가 무시 가능.
--   ②축소(문서화 밖): 종전 blob 에 있던 `review_status 의 '_'→' ' 표기 변형`("needs
--     review" 42건 매치)이 빠져 있었다. 수리 = replace(review_status,'_',' ') 복원.
--   ③축소(D3 확장 — 의도 유지, 문서화만 확장): 증거·검토상태·카테고리의 **표시 라벨**
--     ("증거 A"·"Evidence A"·"검토 필요"·영문 카테고리명 cat.en)은 클라이언트 상수라
--     서버 blob 에 싣지 않는다. D3 원 결정은 한국어 라벨만 명시했으나 취지(라벨 검색은
--     드롭다운 필터로 대체·클라이언트 상수를 SQL 에 복제하지 않음)는 영문 라벨에 동일
--     적용된다. ★이것은 수리하지 않는 의도적 축소다 — 넓히려면 라벨을 서버 정본으로
--     올리는 별도 트랙(설계서 D3-(c))이 정도(正道)다.
--   ④blob 필드 순서를 종전 searchTermsFor 순서로 재정렬 — blob 은 공백 결합이라 필드
--     경계를 넘는 우연 매치(cross-field)가 존재하는데(종전도 동일), 순서가 다르면 우연
--     매치의 **집합이 달라진다**(실측: "Baxalta US Inc. documentation_records" 종전 0
--     vs 현재 1 — firm_name 뒤 필드가 document_id 에서 category_code 로 바뀐 탓).
--     순서를 종전과 맞춰 표류를 최소화한다(우연 매치 자체의 제거는 목표가 아님 — 종전
--     동작의 보존이 목표다).
--
-- 불가침 계약(026~028 승계, 전부 테스트 고정): security invoker(RLS 단일 게이트 —
--   검증은 반드시 anon PostgREST)·ILIKE 부분일치(FTS 금지)·좁은 searched CTE(본문
--   텍스트 미탑재)·전 정렬 min(finding_id) 타이브레이크·firm_asc ko-KR-x-icu·
--   facets(자기축 제외) vs dash(전량 적용) 모집단 분리·행 투영 3종(028).
-- 적용: 사람이 Supabase SQL Editor/MCP 로 적용(자동 아님). 반환 계약(키 구조) 무변경
--   이라 클라이언트 무수정·즉시 호환. 롤백 = 028 재적용.
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
    f.evidence_level, f.review_status, f.evidence_url, f.cfr_refs, f.mfds_refs
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
        'mfds_refs',         pr.mfds_refs
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

comment on function public.findings_search(text, text, text, text, text, text, text, text, int, int) is
  '[FIND-1] /findings/ 의 canonical search — 검색(ILIKE 부분일치)·필터·정렬·문서 단위 '
  '페이지네이션·파셋 집계의 단일 정본. security invoker 라 공개 게이트는 RLS(010)가 강제한다. '
  '030: work_mem 8MB(spill 해소)·p_page 상한·blob 종전 semantics 정렬. 025 §⑤ 이행.';

grant execute on function public.findings_search(text, text, text, text, text, text, text, text, int, int) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용 — ★게이트·semantics 검증은 anon 키로 PostgREST 를 통해)
-- ============================================================================
-- 1) Minor 1 확대 소멸: {"p_q":"[]"} -> totals.findings = 0  (종전 8,168 전건 매치)
-- 2) Minor 1 축소 복원: {"p_q":"needs review"} -> totals.findings = 42(±당일 증가분)
-- 3) 기존 매치 불변:   {"p_q":"무균"} -> 2,454(±당일 증가분) · refs 원소("21 CFR ...") 매치 유지
-- 4) Minor 2: {"p_page":2147483647} -> HTTP 200 + 빈 documents (종전 400/22003)
-- 5) Major 1: explain (analyze, buffers) select findings_search(...) -> temp 없음·웜 ~120ms
