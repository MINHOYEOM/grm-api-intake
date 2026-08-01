-- ============================================================================
-- 043_findings_checklist.sql — [FIND-1] 자가점검 체크리스트용 조항별 대표 사례
--
-- ★왜: 042(findings_cfr_ranking)가 "어떤 조항이 많이 인용되는가"까지 답했지만, 그건
--   여전히 화면에서 읽는 자료지 **받아서 쓰는 산출물**이 아니다. 자가점검을 하려면
--   조항마다 "실제로 이렇게 지적됐다"는 문장이 붙어야 점검자가 자기 절차서와 대조할 수
--   있다. 이 함수가 그 문장을 조항 단위로 묶어 **한 번에** 내려준다.
--
-- ★역할 분담(중복 금지): 조항 **순위·필터**(부 필터·보일러플레이트 제외·문서 수 집계)는
--   042 가 유일한 정본이다. 이 함수는 그 판단을 다시 하지 않고, 클라이언트가 042 응답에서
--   고른 섹션 목록을 그대로 받아 **사례만** 채운다. 필터 로직을 복제하면 두 곳이 갈라지고
--   (026 헤더가 경고한 "게이트 6중복" 과 같은 실패), 어느 쪽이 참인지 알 수 없게 된다.
--
-- ★security invoker (026 findings_search 와 동일 이유, 042 와는 반대):
--   이 함수는 **원문 문장을 반환한다**. 042/007 계열 집계 RPC 는 definer 라 공개 게이트를
--   우회하는데, 그 방식으로 본문을 내보내면 미번역·비공개 행까지 새어 나간다. invoker 로
--   두면 findings 의 RLS(010 정책: `(finding_text_ko <> '' or finding_language='KO') and
--   scope_status='ok'`)가 자동 적용되어, /findings/ 검색 페이지에서 볼 수 있는 것과 정확히
--   같은 집합만 나온다 — 게이트를 손으로 복제하지 않는다.
--   ※ 그래서 체크리스트의 사례 수는 042 의 문서 수보다 적을 수 있다(공개 게이트 통과분만).
--     화면이 그 차이를 적는다.
--
-- ★조항 매칭: cfr_refs 원소가 정확히 `21 CFR <섹션>` 이거나 `21 CFR <섹션>(` 로 시작할
--   때만 매치한다. 접두 매치(`like '21 CFR 211.2%'`)로 하면 `211.22` 질의가 `211.25`·
--   `211.28` 까지 삼킨다 — 하위 항 괄호까지만 허용해 `211.22`/`211.22(a)`/`211.22(d)` 를
--   잡고 `211.220`(가상) 류를 배제한다. 042 의 "하위 항목은 조항 뿌리로 통합" 규칙과 정확히
--   같은 대응이다.
--
-- ★업체 중복 제거: 같은 업체 문서에서 사례 2건이 나오면 "여러 곳에서 반복되는 지적"이라는
--   체크리스트의 전제가 깨진다. firm_key(013) 단위로 먼저 1건씩만 남긴 뒤 최신순으로 뽑는다.
--
-- ★본문에 조항이 실제로 적힌 사례를 우선한다(anchored 우선 정렬):
--   WL 위반 블록 하나가 여러 조항을 함께 인용하면(예: 211.22 와 211.25) 그 블록의 finding
--   은 두 조항 모두에 매칭된다 — 매칭 자체는 옳지만, 화면에 뜬 문장이 다른 조항 번호만
--   보여주면 점검자가 "이게 왜 이 조항 사례지?"에서 막힌다(실측: 211.25 사례로 뽑힌 문장이
--   21 CFR 211.22(a)를 인용). 그래서 finding_text/finding_text_ko 에 그 조항 번호가 실제로
--   들어 있는 행을 앞에 세우고, 없으면 그때만 나머지를 쓴다(사례를 잃지 않는다).
--
-- ★009 함정(배열 슬라이스 괄호) 해당: p_sections 를 그대로 쓰지 않고
--   `(coalesce(p_sections,'{}'::text[]))[1:50]` 로 상한을 건다 — 괄호 위치가 틀리면
--   슬라이스가 적용되지 않는다(009 원본 관례 그대로).
-- ★004 함정(별칭 충돌) 해당: 042 에서 실제로 밟았다. 여기서는 CTE 컬럼명(section)과
--   lateral 별칭(cr.ref_txt)이 겹치지 않게 처음부터 분리해 둔다.
--
-- 전제: 002 + 006 + 010(RLS·scope_status) + 013(firm_key) + 042(조항 순위 정본).
-- ============================================================================

create or replace function public.findings_checklist(
  p_sections text[],
  p_examples integer default 2
) returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
with p as (
  select
    -- 009 관례: 괄호로 감싼 뒤 슬라이스
    (coalesce(p_sections, '{}'::text[]))[1:50]              as secs,
    least(greatest(coalesce(p_examples, 2), 1), 5)          as ex
),
-- 입력 정규화 — 조항 형식(21x.y)만 통과시킨다. 클라이언트를 신뢰하지 않는다.
sec as (
  select distinct s.section
  from p, unnest(p.secs) as s(section)
  where s.section ~ '^21[01]\.[0-9]+$'
),
cand as (
  select
    sec.section,
    f.finding_id,
    f.firm_name,
    f.firm_key,
    f.published_date,
    f.source,
    f.document_id,
    f.evidence_url,
    f.category_code,
    f.finding_text,
    f.finding_text_ko,
    -- 본문에 조항 번호가 실제로 적혀 있으면 0(우선), 아니면 1. position() 은 리터럴
    -- 부분일치라 LIKE 와일드카드 이스케이프가 필요 없다(섹션은 숫자·점뿐이기도 하다).
    case
      when position(sec.section in coalesce(f.finding_text_ko, '')) > 0
        or position(sec.section in coalesce(f.finding_text, '')) > 0
      then 0 else 1
    end as anchored,
    row_number() over (
      partition by sec.section, f.firm_key
      order by
        case
          when position(sec.section in coalesce(f.finding_text_ko, '')) > 0
            or position(sec.section in coalesce(f.finding_text, '')) > 0
          then 0 else 1
        end,
        f.published_date desc, f.finding_id
    ) as rn_firm
  from sec
  join public.findings f
    on exists (
         select 1
         from jsonb_array_elements_text(f.cfr_refs) as cr(ref_txt)
         where cr.ref_txt = '21 CFR ' || sec.section
            or cr.ref_txt like '21 CFR ' || sec.section || '(%'
       )
),
picked as (
  select
    c.*,
    row_number() over (
      partition by c.section
      order by c.anchored, c.published_date desc, c.finding_id
    ) as rn
  from cand c
  where c.rn_firm = 1
)
select jsonb_build_object(
  'sections', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'section',  s.section,
        'examples', coalesce((
          select jsonb_agg(
            jsonb_build_object(
              'finding_id',      k.finding_id,
              'firm_name',       k.firm_name,
              'published_date',  k.published_date,
              'source',          k.source,
              'document_id',     k.document_id,
              'evidence_url',    k.evidence_url,
              'category_code',   k.category_code,
              -- 본문에 그 조항이 실제로 적힌 사례인지 — 화면이 이 사실을 표기해
              -- "왜 이게 이 조항 사례지?"를 막는다(anchored=false 면 같은 위반 블록이
              -- 여러 조항을 함께 인용한 경우다).
              'anchored',        (k.anchored = 0),
              'finding_text',    k.finding_text,
              'finding_text_ko', k.finding_text_ko
            ) order by k.rn
          )
          from picked k, p
          where k.section = s.section and k.rn <= p.ex
        ), '[]'::jsonb)
      ) order by s.section
    ) from sec s
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_checklist(text[], integer) is
  '[FIND-1] 자가점검 체크리스트 — 042 가 고른 조항 목록을 받아 조항별 대표 지적 문장을 '
  '반환한다(업체 중복 제거·최신순). security invoker 라 공개 게이트는 RLS(010)가 강제한다. '
  '조항 순위·필터의 정본은 042 이며 이 함수는 그 판단을 복제하지 않는다.';

grant execute on function public.findings_checklist(text[], integer) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용)
-- ============================================================================
-- ★게이트 검증은 SQL Editor 가 아니라 **anon 키 PostgREST** 로 한다(026 헤더와 동일 이유 —
--   SQL Editor 는 service_role 이라 RLS 가 적용되지 않는다).
--   curl -s "$URL/rest/v1/rpc/findings_checklist" -H "apikey: $ANON" \
--        -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--        -d '{"p_sections":["211.22","211.192"],"p_examples":2}' | jq '.sections[].section'
--
-- 1) 조항 매칭이 이웃 조항을 삼키지 않는가 (211.22 질의에 211.25/211.28 이 섞이면 실패)
--    select jsonb_pretty(public.findings_checklist(array['211.22'], 2));
--    -- 각 example 의 finding 이 실제로 211.22(…)를 인용하는지 cfr_refs 로 확인
--
-- 2) 형식이 아닌 입력은 조용히 무시되는가(인젝션·오타 방어)
--    select public.findings_checklist(array['211.22; drop table', 'abc', ''], 2);  -- 211.22 류만
--
-- 3) 업체 중복 제거가 걸리는가 (같은 firm_key 가 한 섹션에서 2번 나오면 실패)
--    with r as (select public.findings_checklist(array['211.22'], 5) j)
--    select count(*), count(distinct e->>'firm_name')
--    from r, jsonb_array_elements(r.j->'sections'->0->'examples') e;
