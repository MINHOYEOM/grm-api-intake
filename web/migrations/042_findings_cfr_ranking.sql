-- ============================================================================
-- 042_findings_cfr_ranking.sql — [FIND-1 트렌드] 인용 조항 랭킹(보일러플레이트 제외)
--
-- ★왜: 카테고리("무균보증/무균공정")는 우리가 붙인 분류라 그 자체로는 자가점검 항목이
--   되지 못한다. 규제기관이 **실제로 인용한 조항**은 사내 SOP 와 1:1로 붙는 단위라,
--   "무엇을 확인해야 하는가"에 가장 가까운 축이다. 그런데 cfr_refs 를 그대로 세면
--   1위가 `21 CFR 211.34`(컨설턴트) 가 되어 완전히 틀린 그림이 나온다 — 아래 세 겹의
--   필터가 그 이유이자 이 파일의 전부다.
--
-- ── (1) 부(Part) 필터: 21 CFR 210/211 조항만 ────────────────────────────────
--   나머지는 제조소 자가점검으로 옮길 수 없는 축이다:
--     · Part 201(표시)·200 — 대부분 **정의 조항**이다. 실측 컨텍스트:
--       "the intended use (as defined in 21 CFR 201.128)", "(See 21 CFR 201.5)".
--     · Part 330 — OTC 모노그래프 **입법 근거** 참조("proposed rule issued under").
--     · Part 312/50/1271 — 임상시험·HCT/P 로 GMP 제조소 범위 밖.
--   정규식 `^21 CFR 21[01]\.[0-9]` 는 **부 전체 참조도 자동으로 배제**한다
--   (`21 CFR 210`·`21 CFR 211`·`21 CFR Part 211` 은 점+숫자가 없어 매치 안 됨) —
--   부 전체는 100개 조항을 가리키는 이름이지 확인 가능한 요구사항이 아니다. 이 값들은
--   대부분 "in conformance with CGMP as set forth in 21 CFR parts 210 and 211" 이라는
--   판정 문구에서 나온다.
--
-- ── (2) 명시 제외: 조항이되 위반 인용이 아닌 것 ─────────────────────────────
--   Part 211 안에도 위반 인용이 아닌 조항이 있어 부 필터만으로는 못 거른다.
--   **추측이 아니라 실측 근거로만 제외한다**(라이브 컨텍스트 확인):
--     · `21 CFR 211.34`(Consultants) — 경고서한 **맺음말 템플릿**. 문서 351건에
--       정확히 1건씩·16개 카테고리에 균등 산포. 본문에 조항이 남은 72건을 전수
--       확인한 결과 **72/72(100%)** 가 "executive management is responsible for
--       ensuring the consultant is qualified as set forth in 21 CFR 211.34" 형태의
--       권고문이었다. 즉 이 조항 위반으로 지적된 것이 아니라, FDA 가 모든 편지 끝에
--       붙이는 시정 권고다. 필터 없이 세면 이 조항이 **1위**가 된다.
--     · `21 CFR 210.1(b)` — "책임 소재" 정의 조항. 실측 컨텍스트: "you are
--       responsible for assuring that drugs you produce are neither adulterated nor
--       misbranded. [See 21 CFR 210.1(b), 21 CFR 200.10(b).]"
--   ※ 새 보일러플레이트가 보이면 이 목록에 **근거 문구와 함께** 추가한다. 통계적
--     휴리스틱(문서당 1회·다수 카테고리 산포)만으로는 못 가른다 — 진짜 위반 인용인
--     `21 CFR 211.22`(문서 154건·1.00/문서·8개 카테고리)가 정확히 같은 모양이다.
--     가르는 것은 **문장이 무엇을 말하는가**뿐이라, 사람이 확인하고 근거를 남긴다.
--
-- ── (3) 집계 단위: 문서 수(distinct raw_signal_id) ──────────────────────────
--   cfr_refs 는 **위반 블록 단위**로 추출된다(findings_extractors._from_warning_letter
--   가 full_block 에서 뽑아 그 블록의 finding 에 싣는다). 블록 분해가 실패해 편지
--   전체가 1건이 되는 degrade 경로에서는 편지의 모든 조항이 한 finding 에 실린다
--   (실측: refs 20개 이상인 finding 이 200건 넘는다). 그래서 **지적 문장 단위 배정은
--   신뢰 구간이 넓다.** 반면 "이 조항을 인용한 문서가 몇 건인가"는 그 잡음과 무관하게
--   참이다 — 조항이 그 문서 어딘가에 인용된 것은 사실이기 때문이다. 랭킹을 문서 수로
--   내는 이유이고, 건수(findings)도 함께 주되 화면의 주 지표로 쓰지 않는다.
--
-- ── (4) 하위 항목 통합: 211.22(a)/(d) → 211.22 ──────────────────────────────
--   같은 요구사항이 (a)/(b)/(d) 로 갈라져 순위가 흩어지는 것을 막는다. 조항 뿌리가
--   사내 SOP 단위와 맞는 입도이기도 하다. 어떤 하위 항목이 실제로 인용됐는지는
--   variants 로 함께 돌려준다(정보를 버리지 않는다).
--
-- ★범위 한계(화면에 반드시 표기): 이 랭킹은 사실상 **FDA Warning Letter 전용**이다.
--   FDA 483 은 210/211 **조항**을 인용한 문서가 실측 0건이다(483 관찰문은 조항 대신
--   요구사항을 산문으로 쓰고, 조항이 나와도 "21 CFR parts 210 and 211" 같은 부 전체
--   참조뿐이라 (1)에서 배제된다). scope.sources 가 이 사실을 응답에 실어 보낸다.
--
-- ★안전 계약(불가침, 007/041 과 동종): 카운트와 서지 메타(조항 번호·카테고리 코드·
--   소스)만 반환한다. finding_text/evidence_url 등 원문은 어떤 키로도 내려주지 않는다.
--
-- 전제: 002 + 006 + 010(scope_status). 013(firm_key)은 쓰지 않는다.
-- ============================================================================

create or replace function public.findings_cfr_ranking(p_months integer default 12)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
with b as (
  select
    least(greatest(coalesce(p_months, 12), 1), 60) as n_months,
    date_trunc('month', current_date)              as m0,
    current_date                                   as today
),
w as (
  select
    n_months,
    to_char(today, 'YYYY-MM-DD')                                         as as_of,
    to_char(m0 - make_interval(months => (n_months - 1)::int), 'YYYY-MM') as cur_from,
    to_char(m0, 'YYYY-MM')                                               as cur_to
  from b
),
-- (2) 명시 제외 — 조항이되 위반 인용이 아닌 것. 근거는 파일 헤더 참조.
-- ★004 함정 실사례(이 파일에서 실제로 밟았다): 이 CTE 의 컬럼명을 `ref` 로 두고
--   `not exists (select 1 from excluded e where e.ref = ref)` 라고 쓰면, 서브쿼리 안의
--   맨 `ref` 가 바깥 lateral 별칭이 아니라 **안쪽 `e.ref` 로 해석**되어 조건이 항상 참이
--   되고 NOT EXISTS 가 전량을 걸러 낸다(적용 후 docs_with_clause=0 으로 발각). 그래서
--   컬럼명을 `bad_ref` 로 분리하고 lateral 도 `cf(ref_txt)` 로 명시 별칭을 준다 —
--   이름이 겹치지 않으면 이 경로 자체가 생기지 않는다.
excluded(bad_ref) as (
  values ('21 CFR 211.34'), ('21 CFR 210.1(b)')
),
pairs as (
  select
    f.raw_signal_id,
    f.finding_id,
    f.source,
    f.category_code,
    left(f.published_date, 7) as month,
    -- (4) 하위 항목 통합: '21 CFR 211.22(d)' → '211.22'
    regexp_replace(regexp_replace(cf.ref_txt, '^21 CFR ', ''), '\(.*$', '') as section,
    regexp_replace(cf.ref_txt, '^21 CFR ', '')                             as variant
  from public.findings f,
       lateral jsonb_array_elements_text(f.cfr_refs) as cf(ref_txt)
  where f.scope_status = 'ok'
    -- (1) 부 필터 + 부 전체 참조 자동 배제(점+숫자 요구)
    and cf.ref_txt ~ '^21 CFR 21[01]\.[0-9]'
    and not exists (select 1 from excluded e where e.bad_ref = cf.ref_txt)
),
agg as (
  select
    p.section,
    count(distinct p.raw_signal_id)                                            as docs,
    count(distinct p.finding_id)                                               as findings,
    count(distinct p.raw_signal_id) filter (where p.month >= (select cur_from from w)) as recent_docs
  from pairs p
  group by p.section
)
select jsonb_build_object(
  'scope', jsonb_build_object(
    'months',            (select n_months from w),
    'as_of',             (select as_of from w),
    'cur_from',          (select cur_from from w),
    'cur_to',            (select cur_to from w),
    'part_filter',       '21 CFR 210/211',
    'unit',              'documents',
    'excluded_sections', (select jsonb_agg(bad_ref order by bad_ref) from excluded),
    'docs_with_clause',  (select count(distinct raw_signal_id) from pairs),
    'sources', coalesce((
      select jsonb_agg(jsonb_build_object('source', source, 'docs', d) order by d desc, source)
      from (
        select source, count(distinct raw_signal_id) as d from pairs group by source
      ) t
    ), '[]'::jsonb)
  ),
  'items', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'section',      a.section,
        'docs',         a.docs,
        'recent_docs',  a.recent_docs,
        'findings',     a.findings,
        'top_category', (
          select p2.category_code
          from pairs p2
          where p2.section = a.section
          group by p2.category_code
          order by count(distinct p2.raw_signal_id) desc, p2.category_code asc
          limit 1
        ),
        'variants', coalesce((
          select jsonb_agg(v.variant order by v.variant)
          from (select distinct p3.variant from pairs p3 where p3.section = a.section) v
        ), '[]'::jsonb)
      ) order by a.docs desc, a.section
    ) from agg a
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_cfr_ranking(integer) is
  '[FIND-1] 인용 조항 랭킹 — 21 CFR 210/211 조항만, 보일러플레이트(211.34 컨설턴트 권고 · '
  '210.1(b) 정의) 명시 제외, 하위 항목은 조항 뿌리로 통합, 단위는 문서 수. 카운트·서지 '
  '메타만 반환(007 안전 계약과 동종). 실측상 FDA Warning Letter 전용(483 은 조항 인용 0건).';

revoke all on function public.findings_cfr_ranking(integer) from public;
grant execute on function public.findings_cfr_ranking(integer) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용)
-- ============================================================================
-- 1) 보일러플레이트가 실제로 빠졌는가 — 1위가 211.34 가 아니어야 한다
--    select public.findings_cfr_ranking(12) -> 'items' -> 0;
--    select jsonb_path_query_array(public.findings_cfr_ranking(12), '$.items[*].section')
--             @> '["211.34"]'::jsonb;    -- false
--
-- 2) 제외 근거가 응답에 실려 화면이 그 사실을 적을 수 있는가
--    select public.findings_cfr_ranking(12) -> 'scope' -> 'excluded_sections';
--
-- 3) 범위 한계(483 조항 인용 0건)가 응답으로 드러나는가
--    select public.findings_cfr_ranking(12) -> 'scope' -> 'sources';
--
-- 4) 원문 텍스트가 어떤 키로도 새지 않는가 (안전 계약)
--    select public.findings_cfr_ranking(12)::text ilike '%finding_text%';   -- false
