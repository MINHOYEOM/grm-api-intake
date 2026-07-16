-- ============================================================================
-- 026_findings_search.sql — [FIND-1] 서버 canonical search
--
-- 025 §⑤ 이월 항목의 이행: 검색·필터·정렬·페이지네이션·파셋 집계의 정본을 서버 RPC 로
-- 옮긴다. 클라이언트(web/assets/findings.js)는 결과를 **소비만** 한다.
--
-- ★왜: Codex 재감사가 찾은 "화면 FDA 483 (910) vs DB 진실 8,078" 은 숫자 표시 버그가
--   아니라 구조 문제의 증상이었다 — 클라이언트가 최신 1,000행만 로드한 뒤 그 위에서
--   검색·필터·정렬·집계를 전부 수행했기 때문이다. 025 는 "틀린 숫자를 숨기는" 응급
--   처치였고(§①②), 아래가 그대로 남아 있었다:
--     · 발행월 옵션이 로드분에 있는 월만 노출
--     · `오래된순`·`업체명순`이 전역 정렬이 아니라 비활성화로 회피
--     · 검색·필터가 로드분 안에서 24문서를 채우면 나머지 코퍼스를 조회조차 안 함
--   전부 같은 뿌리(ROWS 의존)이고, 회피책은 기능을 깎아서 정직해진 것이지 고쳐진 게
--   아니다. 이 파일이 그 뿌리를 제거한다.
--
-- 적용 순서(중요): 이 마이그레이션을 **먼저** 프로덕션에 적용하고, 그 다음에 findings.js
--   전환(PR-B)을 머지한다. web/migrations/*.sql 는 자동 적용되지 않는데(사람이 SQL
--   Editor) Cloudflare Pages 는 머지 즉시 배포되므로, JS 를 먼저 내보내면 RPC 없는
--   사이트가 라이브가 된다. 적용 직후 이 파일은 **호출자가 없는 상태**로 대기한다
--   (019 가 확립한 "적재→검증→전환" 패턴의 1단계 = 되돌리기 비용 0).
--
-- ★security invoker (관례 이탈, 의도적):
--   기존 findings RPC 는 전부 security definer 라서 공개 게이트 술어
--   `(finding_text_ko <> '' or finding_language = 'KO') and scope_status = 'ok'` 를
--   각자 WHERE 절에 손으로 복제한다 — 현재 정책 1곳(010) + RPC 5곳 = 6중복. 게이트가
--   바뀌면 6곳을 동기화해야 하고 하나 빠뜨리면 비공개 데이터 노출이다.
--   이 파일의 두 함수는 **findings 테이블만** 읽고, anon 은 이미 `grant select on
--   findings`(003) + RLS 정책(010)을 갖고 있다. 따라서 invoker 로 만들면 RLS 가 자동
--   적용되어 게이트 복제가 아예 불필요해진다 — 게이트의 단일 진실이 정책 하나로 돌아온다.
--   findings_stats 가 definer 인 건 raw_signals(anon 전면 차단) 카운트 때문이고,
--   여기엔 그 필요가 없다. search_path 고정은 invoker 에서도 그대로 유지한다.
--   ※ 그래서 이 함수를 service_role/postgres 로 호출하면 RLS 가 적용되지 않아 비공개
--     행까지 보인다(정상). 게이트 검증은 **반드시 anon 키로 PostgREST 를 통해** 한다.
--
-- ★검색 semantics = ILIKE 부분일치 유지 (FTS 로 바꾸지 않는다):
--   018 이 이미 FTS GIN 인덱스를 만들어 뒀지만 재사용하면 **안 된다**. 현재 클라이언트
--   검색은 String.indexOf 부분일치다. FTS 'simple' 사전은 한국어 형태소를 모르므로
--   (018:87-92 가 실측: websearch 기본 AND 는 표본 질의 3종 전부 0건) `무균` 질의가
--   `무균실`·`무균의`·`무균 공정` 을 놓친다 = 조용한 검색 축소. 정확한 숫자를 얻으려고
--   검색 품질을 떨어뜨리면 순손실이다. 018 의 FTS 인덱스는 findings_similar(유사 문구
--   검색) 전용으로 그대로 둔다 — 두 기능은 목적이 다르다.
--   LIKE 와일드카드(%·_·\)는 이스케이프한다 — indexOf 는 이들을 리터럴로 취급하므로
--   이스케이프하지 않으면 semantics 가 갈린다.
--
-- ★trgm 인덱스는 이 파일에 넣지 않는다(2단계로 미룸):
--   blob ILIKE seq scan 이 지배 비용(라이브 실측 69ms/9,292행)이고 코퍼스에 선형이다.
--   gin_trgm_ops 는 ILIKE semantics 를 바꾸지 않으면서 가속하므로 언제든 추가할 수 있다.
--   018:36 이 trgm 인덱스를 거부한 근거("쓰이지 않는데 매일 백필 insert 마다 쓰기 증폭만
--   유발")는 이제 쓰이므로 뒤집혔지만, 023 전량 백필이 커넥터 타임아웃→롤백된 교훈을
--   존중해 **라이브 p95 를 측정한 뒤** 별도 마이그레이션으로 추가한다. 현재 전체 형태
--   실측 128ms 는 감당 가능하다.
--
-- ★문서 단위 정렬의 전제(라이브 실측 2026-07-16):
--   공개 문서 1,356건 · 지적 8,168건 · avg 6.02/doc · max 46
--   docs_multi_date 0 · docs_multi_firm 0 · docs_multi_source 0
--   즉 raw_signal_id 하나가 **단일** published_date/firm_name/source 를 갖는다(위반 0).
--   클라이언트 buildDocHead() 가 rows[0] 을 문서 대표값으로 쓰는 가정이 실측으로 확인됐고,
--   따라서 서버가 문서 단위로 정렬·페이지네이션할 수 있다. 이 전제가 깨지면 문서 대표값이
--   임의값이 되므로 collector 측 불변식 테스트로 고정한다.
--
-- 정렬 결정론: 전 정렬에 min(finding_id) 최종 타이브레이크를 둔다. 022 가 fp16 동률로
--   데인 결함(타이브레이크 없는 order by → 평가 29슬롯 미판정)의 재발 방지다. 현
--   클라이언트 firm_asc 는 최종 타이브레이크가 없어 JS sort 안정성에 우연히 기대고 있다.
-- 한글 정렬: DB 기본 collate 는 en_US.UTF-8 이라 클라이언트 localeCompare 와 한글 업체명
--   순서가 갈린다. ICU ko-KR-x-icu 가 사용 가능(실측)하므로 firm_asc 에 명시한다.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- (A) findings_search — 검색·필터·정렬·페이지네이션·파셋의 단일 정본
-- ---------------------------------------------------------------------------
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
as $$
with p as (
  -- 입력은 전부 서버에서 정규화·클램프한다 — 클라이언트를 신뢰하지 않는다.
  select
    coalesce(btrim(p_q), '')                                       as q,
    -- LIKE 와일드카드 이스케이프(기본 escape = 백슬래시). indexOf 는 %·_ 를 리터럴로
    -- 취급하므로 이스케이프해야 semantics 가 일치한다. 백슬래시를 **먼저** 치환해야
    -- 뒤에 삽입한 이스케이프 문자를 다시 이스케이프하지 않는다.
    replace(replace(replace(coalesce(btrim(p_q), ''), '\', '\\'), '%', '\%'), '_', '\_')
                                                                   as q_esc,
    coalesce(p_source, '')                                         as f_source,
    coalesce(p_category, '')                                       as f_cat,
    coalesce(p_month, '')                                          as f_month,
    coalesce(p_evidence, '')                                       as f_ev,
    coalesce(p_review_status, '')                                  as f_rs,
    coalesce(p_agency, '')                                         as f_agency,
    case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
         then p_sort else 'date_desc' end                          as sort,
    greatest(coalesce(p_page, 1), 1)                               as page,
    least(greatest(coalesce(p_docs_per_page, 24), 1), 100)         as per
),
-- 검색만 적용한 공통 베이스. 필터는 아직 걸지 않는다 — 파셋이 "자기 축만 제외하고 나머지
-- 필터 적용"(표준 파세팅)을 하려면 검색이 공통 베이스여야 하기 때문이다.
-- ★select * 금지: 초안에서 select *(width=753)로 두었더니 CTE 가 temp 파일로 스필했다
--   (실측 temp read=9,773). 필요한 컬럼만 투영한다.
-- ★공개 게이트를 여기 쓰지 않는다 — security invoker 라 RLS(010)가 자동 적용된다.
searched as (
  select
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.document_id, f.published_date,
    f.firm_name, f.category_code, f.category_label_ko, f.finding_text, f.finding_text_ko,
    f.evidence_level, f.review_status, f.evidence_url, f.cfr_refs, f.mfds_refs,
    left(f.published_date, 7) as month
  from public.findings f, p
  where p.q = ''
     or (
          -- 검색 대상 blob. ★이 컬럼 목록이 "무엇이 검색되는가"의 정본이며 테스트가
          --   고정한다(조용한 축소 방지). 클라이언트 상수 라벨(EVIDENCE_LABEL "증거 A",
          --   STATUS_LABEL "검토 필요", 영문 카테고리명)은 **의도적으로 제외**한다 —
          --   셋 다 드롭다운 필터로 대체 가능하고, 클라이언트 상수를 SQL 에 복제하는 것은
          --   "서버가 정본" 원칙에 정면 배치되기 때문이다.
          -- month 는 별도로 넣지 않는다 — published_date 가 blob 에 있어 '2026-07' 이
          --   부분일치로 잡힌다.
          coalesce(f.finding_text_ko, '')    || ' ' ||
          coalesce(f.finding_text, '')       || ' ' ||
          coalesce(f.firm_name, '')          || ' ' ||
          coalesce(f.category_code, '')      || ' ' ||
          coalesce(f.category_label_ko, '')  || ' ' ||
          coalesce(f.document_id, '')        || ' ' ||
          coalesce(f.agency, '')             || ' ' ||
          coalesce(f.source, '')             || ' ' ||
          coalesce(f.published_date, '')     || ' ' ||
          coalesce(f.evidence_level, '')     || ' ' ||
          coalesce(f.review_status, '')      || ' ' ||
          coalesce(f.translation_method, '') || ' ' ||
          coalesce(f.cfr_refs::text, '')     || ' ' ||
          coalesce(f.mfds_refs::text, '')
        ) ilike '%' || p.q_esc || '%'
),
-- 필터 전량 적용분 = 결과 목록·totals 의 모집단.
filtered as (
  select s.* from searched s, p
  where (p.f_source = '' or s.source          = p.f_source)
    and (p.f_cat    = '' or s.category_code   = p.f_cat)
    and (p.f_month  = '' or s.month           = p.f_month)
    and (p.f_ev     = '' or s.evidence_level  = p.f_ev)
    and (p.f_rs     = '' or s.review_status   = p.f_rs)
    and (p.f_agency = '' or s.agency          = p.f_agency)
),
-- 문서 단위 집약. min() 은 §2.1 실측(문서당 단일값)에 근거 — max() 와 동일하다.
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
        -- 선택되지 않은 정렬의 CASE 는 전 행 NULL(상수)이라 순서에 영향이 없다.
        (case when p.sort = 'firm_asc' then d.firm end) collate "ko-KR-x-icu" asc nulls last,
        (case when p.sort = 'date_asc' then d.pub  end) asc  nulls last,
        (case when p.sort = 'date_desc' then d.pub end) desc nulls last,
        (case when p.sort = 'firm_asc' then d.pub  end) desc nulls last,
        d.tie asc   -- ★전 정렬 공통 최종 타이브레이크(022 교훈)
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
-- ★초안 결함 수정: filtered 를 page_docs 에 직접 join 하면 CTE 에 인덱스가 없어 planner
--   가 nested loop 로 풀어 O(n×m) 가 된다(실측 Rows Removed by Join Filter: 58,848).
--   페이지 문서 id 를 배열 하나로 접어 = any() 로 조회한다(24개 원소 대상 in-memory 비교).
--   ★array(select …) 생성자를 쓴다. any(select array_agg(…)) 로 쓰면 서브쿼리 형태로
--     해석돼 `text = text[]` 타입 에러가 난다(dry-run 이 잡은 실제 버그).
page_rows as (
  select f.*
  from filtered f
  where f.raw_signal_id = any (array(select pd.raw_signal_id from page_docs pd))
),
page_docs_full as (
  select
    pd.rn,
    pr.raw_signal_id,
    min(pr.firm_name)      as firm_name,
    min(pr.source)         as source,
    min(pr.agency)         as agency,
    min(pr.published_date) as published_date,
    min(pr.document_id)    as document_id,
    min(pr.evidence_url)   as evidence_url,
    count(*)::int          as matched_findings,
    -- 문서 내 지적 순서 = finding_id asc. 현재 클라이언트가 보는 순서(서버가
    -- published_date desc, finding_id asc 로 보내고 문서 내 날짜는 동일)와 같다.
    jsonb_agg(
      jsonb_build_object(
        'finding_id',        pr.finding_id,
        'raw_signal_id',     pr.raw_signal_id,
        'source',            pr.source,
        'agency',            pr.agency,
        'document_id',       pr.document_id,
        'published_date',    pr.published_date,
        'firm_name',         pr.firm_name,
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
  join page_docs pd on pd.raw_signal_id = pr.raw_signal_id
  group by pd.rn, pr.raw_signal_id
),
-- 파셋 = 표준 파세팅: 각 축은 **자기 자신을 뺀** 나머지 필터를 적용해 센다.
-- 클라이언트 computeFacetCounts(row, exclude) 와 동일 의미다.
-- 단위는 **지적(행) 수** — 현 화면·findings_stats.by_source 와 같은 단위다(FDA 483 = 8,078).
-- 라이브 실측상 축당 ~2ms 라 5축 전부 켜도 사실상 공짜다.
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
)
select jsonb_build_object(
  'documents', coalesce(
      (select jsonb_agg(
         jsonb_build_object(
           'raw_signal_id',    d.raw_signal_id,
           'firm_name',        d.firm_name,
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
      'by_review_status', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_rs),     '[]'::jsonb)),
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
  '025 §⑤ 이행.';


-- ---------------------------------------------------------------------------
-- (B) findings_document — 딥링크 1회 해석
-- ---------------------------------------------------------------------------
-- 현재 클라이언트는 2회 왕복한다: finding_id=eq.<id> 로 단건 → 그 raw_signal_id 로
-- raw_signal_id=eq.<rsid> 재조회(findings.js:1547-1584). 게다가 필드 3단 폴백을 메인
-- 로드와 **별도로 재구현**해 뒀다(:1534-1545). 1회로 통합한다.
--
-- 비공개/부재는 구분 없이 빈 결과다 — "존재 여부 정보 누설 금지" 계약(findings.js:1444)
-- 을 서버에서 유지한다. invoker+RLS 라 자동으로 성립한다: 비공개 행은 RLS 가 거르므로
-- 함수는 그 행이 존재하는지조차 알 수 없다.
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
        'mfds_refs',         r.mfds_refs
      ) order by r.finding_id
    ) from rows_out r), '[]'::jsonb)
) end;
$$;

comment on function public.findings_document(text) is
  '[FIND-1] 딥링크 1회 해석 — finding_id 로 그 지적이 속한 문서 전체를 반환. 비공개/부재는 '
  '구분 없이 null(존재 여부 누설 금지). security invoker + RLS(010)가 게이트를 강제한다.';


-- ---------------------------------------------------------------------------
-- (C) 권한
-- ---------------------------------------------------------------------------
-- invoker 이므로 execute 권한만으로는 아무것도 열리지 않는다 — 호출자의 findings 에 대한
-- select 권한(003)과 RLS 정책(010)이 그대로 적용된다. 이게 definer 대비 이 설계의 안전
-- 마진이다.
grant execute on function public.findings_search(text, text, text, text, text, text, text, text, int, int) to anon, authenticated;
grant execute on function public.findings_document(text) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용, 프로덕션 SQL Editor)
-- ============================================================================
-- ★게이트 검증은 여기서 하면 안 된다 — SQL Editor 는 service_role/postgres 라 RLS 가
--   적용되지 않는다. 반드시 **anon 키로 PostgREST** 를 통해 확인한다:
--     curl -s "$URL/rest/v1/rpc/findings_search" -H "apikey: $ANON" \
--          -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--          -d '{"p_q":"","p_page":1}' | jq '.totals'
--   기대: totals.findings = 8,168 (공개분과 정확히 일치 = 비공개 0건 누출)
--
-- 1) 랜딩 totals/facets 가 findings_stats 와 정합한가 (by_source FDA 483 = 8,078)
--    select findings_search('', '', '', '', '', '', '', 'date_desc', 1, 24) -> 'facets' -> 'by_source';
--
-- 2) 정렬 3종이 결정론인가 (같은 입력 2회 = 같은 문서 순서)
--    select (findings_search('','','','','','','','firm_asc',1,24)->'documents') @> '[]'::jsonb;
--
-- 3) 파셋 합 = totals 정합 (필터 적용 시)
--    with r as (select findings_search('','fda-483','','','','','','date_desc',1,24) as j)
--    select (select sum((e->>'c')::int) from r, jsonb_array_elements(r.j->'facets'->'by_category') e),
--           (r.j->'totals'->>'findings')::int from r;
--
-- 4) 페이지 경계 — 마지막 페이지 rn 이 doc_total 을 넘지 않는가
--    select findings_search('','','','','','','','date_desc', 99999, 24) -> 'documents';  -- []
