-- ============================================================================
-- 082 — findings_search 본문 전용 검색 축 (`p_text_only`)
--
-- 왜(#804): `findings_search` 는 본문(finding_text/finding_text_ko)뿐 아니라 분류
-- 코드·분류 라벨(한글)·document_id·source·기관·검토상태·조항 참조·실사관 이름까지
-- 이어붙인 문자열에 `ilike '%q%'` 를 건다. 그래서 용어사전 "이 용어로 찾은 지적사례
-- N건" 의 N 에는 **본문에 그 용어가 한 번도 안 나오는 지적**이 섞인다. 2026-08-26
-- 전수 재측정(177 용어, RLS 공개 집합)에서 `CAPA` 는 1,963건 중 1,794건(91.4%)이
-- 분류 코드/라벨에서만 걸렸고 본문 실재는 169건뿐이었다. `Recall` 70.8%·`품질부서`
-- 72.0%·`GMP` 는 document_id 978건·source 86건이 섞였다. 나머지 164개 용어는 오염
-- 0 이거나 3% 미만이라 전면 결함이 아니라 "분류 라벨·코드에 그 낱말이 들어간 용어"에
-- 한정된 결함이다(이슈 실측 표 참조).
--
-- ── 셋 중 하나가 아니라 넷째 안 ─────────────────────────────────────────────
-- 이슈는 판단이 필요해 세 안을 놓고 코드 없이 열렸다:
--   ① RPC 검색 범위 자체를 본문으로 좁힌다 — 화면 검색(`/findings/?q=`) 결과도 바뀐다
--     (예: "CAPA" 검색 시 CAPA 분류 지적이 안 나옴). RPC 계약 변경이라 화면 검색의
--     의미가 넓은 검색에서 좁은 검색으로 바뀐다 — 되돌릴 수 없는 손실이다.
--   ② 용어사전만 별도로 본문 카운트를 낸다 — 숫자는 정직해지지만 클릭하면 N 과 다른
--     개수가 나온다(현재 "화면과 같은 함수로 센다" 정합성을 파기).
--   ③ 현행 유지 + 문구만 "검색되는"으로 바꾼다 — 숫자의 **의미**는 정직해지지만
--     숫자 자체(1,963)는 여전히 부풀려진 채로 남는다.
--   ④(채택) **새 파라미터 `p_text_only`** 로 검색 모드 자체를 본문 전용으로 전환할
--     수 있게 하고, 용어사전 링크가 `&text=1` 로 그 모드를 켠 채로 이동한다.
--     기본값(false)은 기존 전체 검색(①의 손실 없음) — 화면 검색은 그대로 넓고,
--     용어사전은 클릭한 순간 본문 전용으로 좁혀 **숫자와 클릭 결과가 다시 같아진다**
--     (②의 정합성 파기 없음). 문구도 "본문에 이 용어가 있는 지적사례"로 바꿔 숫자의
--     의미까지 정직해진다(③ 겸함). 세 안의 장점만 취하고 단점(검색 범위 축소·정합성
--     파기)은 갖지 않는다.
--
-- ── 저장 열이 아니라 CASE 로 두는 이유 ──────────────────────────────────────
-- 074 의 `original_is_english` 처럼 저장 열로 만들 만한 축이 아니다 — "본문에 q 가
-- 있는가"는 q 마다 달라지는 질의 시점 판정이라 미리 계산해 둘 수 없다(원문 언어는
-- q 와 무관한 행 고유 속성이라 저장 열이 맞았다). 대신 `searched` CTE 의 ilike 대상
-- 문자열을 `p.f_text_only` 로 분기한다 — **검색 술어를 복제하지 않는다**(068/074의
-- 규율 승계). `filtered`/`docs`/facets/dash 는 전부 `searched` 파생이라 이 한 곳만
-- 고치면 총계·패싯·대시보드가 전부 같은 모집단에서 나온다(한 곳만 걸면 숫자가 갈린다).
--
-- ── 시그니처를 바꾸는 것에 대하여(074 와 동일 계약) ─────────────────────────
-- 074 는 "drop + create 를 한 질의로 보낸다 → overload 로 남겨 두면 11인자 호출이
-- 모호해져 그쪽이 오히려 장애다"를 못박았다. 여기서도 그대로 지킨다:
--   * 신설 인자는 **맨 뒤 + 기본값 false** — 12인자로 부르는 현행 사이트·
--     glossary_cases_refresh.py 는 그대로 동작한다(PostgREST 는 제공된 인자가
--     부분집합이면 해석한다).
--   * drop + create 를 한 질의로 보낸다(암묵 트랜잭션, 중간 상태 없음).
--   * 적용 순서는 **마이그레이션 먼저, 사이트/스크립트 배포 나중**.
--   * `security invoker` 를 명시로 유지한다(030 계약 승계 — 068/074 는 명시를
--     생략했지만 SECURITY INVOKER 는 Postgres 함수 기본값이라 동작은 이미 같았다.
--     여기서는 검토자가 한눈에 확인할 수 있도록 다시 명시로 적는다).
--   * `grant execute ... to anon, authenticated` 를 명시로 재부여한다. 068/074 는
--     명시 grant 없이도 라이브에서 동작했다(PUBLIC 기본 EXECUTE 권한에 의존한 것으로
--     보인다 — anon/authenticated 는 PUBLIC 의 암묵 멤버). 이 시그니처는 drop 으로
--     기존 함수 OID 의 권한이 사라지고 새 OID 로 다시 생성되므로, 검토·롤백을 쉽게
--     하려고 026~030 관례대로 명시 grant 를 다시 적는다(있어도 무해, 없으면 PUBLIC
--     기본에 의존하게 된다 — 명시가 더 안전하다).
--
-- ── 동작 계약 ──────────────────────────────────────────────────────────────
--   p_text_only = false(기본, 생략 시)  → 074 와 **완전히 동일**. 검색 대상 문자열·
--     결과·총계·페이지·패싯·대시가 전부 byte-identical(응답 md5 로 증명한다).
--   p_text_only = true                 → ilike 매치 대상이
--     `coalesce(finding_text,'') || ' ' || coalesce(finding_text_ko,'')` 뿐이다.
--     분류 코드·라벨·document_id·source·기관·검토상태·조항 참조·실사관 이름은
--     매치 대상에서 빠진다. p_orig_lang 필터는 이 축과 독립적으로 그대로 적용된다
--     (같은 `p` CTE 의 서로 다른 축 — AND 관계 아님, 검색 대상 문자열만 바뀔 뿐
--     원문 언어 필터는 `filtered` 단계에서 별도로 걸린다).
--
-- ── 검증(적용 후 운영자가 직접 실행) ─────────────────────────────────────────
--   -- ① 동작 무변경 증명 — 고정 질의 4개, p_text_only 생략(기본 false)이 074 배포본과
--   --   byte-identical 이어야 한다(적용 전 074 응답을 미리 캡처해 대조).
--   select q, md5(public.findings_search(p_q := q)::text) as md5_default_false
--   from unnest(array['CAPA', 'Recall', '품질관리', 'GMP']) as q;
--
--   -- ② 본문 전용 축소 증명 — CAPA 는 169건 부근이어야 한다(이슈 #804 2026-08-26 실측).
--   select q,
--          (public.findings_search(p_q := q, p_text_only := true)
--            -> 'totals' ->> 'findings')::int as text_only_true_findings
--   from unnest(array['CAPA', 'Recall', '품질관리', 'GMP']) as q;
--
-- ── 되돌리는 법 ────────────────────────────────────────────────────────────
-- 074 의 12인자 본문을 그대로 다시 만든다(이 파일의 13번째 인자 `p_text_only` 와
-- 그 인자를 참조하는 `p.f_text_only`/`searched` CASE 분기만 제거한 것과 동일):
--
--   drop function if exists public.findings_search(
--     text, text, text, text, text, text, text, text, integer, integer, text, text, boolean);
--
--   -- 그 뒤 074_findings_search_orig_lang.sql 의
--   -- "create or replace function public.findings_search(...)" 전문을 그대로 재실행한다
--   -- (11→12 인자 drop 문 포함, 074 파일 원문 그대로 — 이 파일에서 새로 만들지 않는다).
-- ============================================================================

-- 13인자 판을 먼저 내린다 — 남겨 두면 12인자 호출이 두 함수 사이에서 모호해진다.
drop function if exists public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text, text);

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

grant execute on function public.findings_search(
  text, text, text, text, text, text, text, text, integer, integer, text, text, boolean
) to anon, authenticated;
