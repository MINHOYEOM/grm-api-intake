-- [FIND-1 · 2026-07-21] rejected 오추출을 공개 게이트에서 숨긴다 — 검수 파이프라인 정합.
-- 배경: 공개 read RLS(010)는 `(finding_text_ko<>'' or finding_language='KO') and scope_status='ok'`
-- 만 걸어 review_status='rejected'(검수에서 오추출로 판정된 행)를 배제하지 않았다. 그래서
-- 번역된 rejected finding 이 /findings/ 검색·목록·상세에 정상 지적처럼 노출됐다 — 검수(반려)의
-- 의미가 사라진다(RCA 원인 C 정합성 결함, 사용자 확정 2026-07-21: "검색에서 숨김").
--
-- 두 표면만 고친다:
--   (A) 공개 read RLS `findings_public_read` — `and review_status <> 'rejected'` 추가.
--       findings_search(026)·행 상세는 security invoker 라 이 RLS 가 자동 적용된다 → 검색·
--       목록·상세·파셋에서 rejected 가 사라진다(추가 RPC 변경 불필요). 클라이언트 검토상태
--       필터 옵션은 by_review_status 파셋으로 **동적 생성**되므로 rejected 옵션도 자동 소거.
--   (B) findings_stats(025) — security definer(RLS 우회)라 집계에 같은 필터를 명시 추가한다.
--       라이브 정의(pg_get_functiondef)를 그대로 베이스로 삼았다 — 017 top_firms(firm_key
--       group by + 대표 표시명 lateral)를 되돌리지 않기 위해서다(025 헤더 경고와 동일). diff 는
--       모든 `where scope_status = 'ok'` 에 `and review_status <> 'rejected'` 를 더한 것뿐이다.
--       by_review_status 도 이 필터를 받아 accepted/needs_review 만 남는다(공개 파셋 정합).
--
-- 불변식: 삭제 아닌 숨김(review_status 값 불변, 되돌림 = 이 필터 제거). review_status 는
-- NOT NULL(002 check)이라 `<> 'rejected'` 가 accepted/needs_review 를 모두 포함한다.
-- 관리자(service_role)는 RLS 우회라 rejected 를 여전히 본다(P2 리포트·admin 무영향).

-- ============================================================================
-- (A) 공개 read RLS — rejected 배제 추가
-- ============================================================================
drop policy if exists findings_public_read on public.findings;
create policy findings_public_read
on public.findings
for select
to anon, authenticated
using (
  (finding_text_ko <> '' or finding_language = 'KO')
  and scope_status = 'ok'
  and review_status <> 'rejected'
);

-- ============================================================================
-- (B) findings_stats — 집계에 동일 필터 추가(라이브 정의 기반, diff = review_status 필터뿐)
-- ============================================================================
create or replace function public.findings_stats()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'totals', jsonb_build_object(
      'findings', (
        select count(*) from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
      ),
      'public_findings', (
        select count(*) from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
          and (finding_text_ko <> '' or finding_language = 'KO')
      ),
      'raw_signals', (select count(*) from public.raw_signals),
      'firms', (
        select count(distinct firm_name) from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
      ),
      'documents', (
        select count(distinct raw_signal_id) from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
      )
    ),
    'by_agency_category', coalesce((
      select jsonb_agg(
        jsonb_build_object('agency', agency, 'category_code', category_code, 'cnt', cnt)
        order by agency, category_code
      )
      from (
        select agency, category_code, count(*) as cnt
        from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
        group by agency, category_code
      ) t
    ), '[]'::jsonb),
    'by_month', coalesce((
      select jsonb_agg(
        jsonb_build_object('month', month, 'agency', agency, 'cnt', cnt)
        order by month, agency
      )
      from (
        select left(published_date, 7) as month, agency, count(*) as cnt
        from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
        group by left(published_date, 7), agency
      ) t
    ), '[]'::jsonb),
    'by_source', coalesce((
      select jsonb_agg(
        jsonb_build_object('source', source, 'cnt', cnt)
        order by source
      )
      from (
        select source, count(*) as cnt
        from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
        group by source
      ) t
    ), '[]'::jsonb),
    'by_evidence', coalesce((
      select jsonb_agg(
        jsonb_build_object('evidence_level', evidence_level, 'cnt', cnt)
        order by evidence_level
      )
      from (
        select evidence_level, count(*) as cnt
        from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
        group by evidence_level
      ) t
    ), '[]'::jsonb),
    'by_review_status', coalesce((
      select jsonb_agg(
        jsonb_build_object('review_status', review_status, 'cnt', cnt)
        order by review_status
      )
      from (
        select review_status, count(*) as cnt
        from public.findings
        where scope_status = 'ok' and review_status <> 'rejected'
        group by review_status
      ) t
    ), '[]'::jsonb),
    'top_firms', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'firm_key', firm_key, 'firm_name', firm_name, 'cnt', cnt, 'public_cnt', public_cnt
        )
        order by cnt desc, firm_key asc
      )
      from (
        select g.firm_key, dn.firm_name, g.cnt, g.public_cnt
        from (
          select
            firm_key,
            count(*) as cnt,
            count(*) filter (
              where finding_text_ko <> '' or finding_language = 'KO'
            ) as public_cnt
          from public.findings
          where scope_status = 'ok' and review_status <> 'rejected'
          group by firm_key
          order by cnt desc, firm_key asc
          limit 30
        ) g
        join lateral (
          select firm_name
          from public.findings
          where firm_key = g.firm_key and scope_status = 'ok' and review_status <> 'rejected'
          group by firm_name
          order by count(*) desc, length(firm_name) desc, firm_name asc
          limit 1
        ) dn on true
      ) t
    ), '[]'::jsonb)
  );
$$;

-- 검증(사람 실행용, 프로덕션 SQL Editor):
-- 1) anon 이 rejected 를 못 보는지: set role anon;
--    select count(*) from public.findings where review_status='rejected'; -- 0
--    reset role;
-- 2) findings_stats.by_review_status 에 rejected 가 없는지:
--    select public.findings_stats()->'by_review_status'; -- accepted/needs_review 만
-- 3) 017 회귀 금지: top_firms[0] 에 'firm_key' 가 있어야 한다.
-- 4) totals.findings 가 rejected 제외분으로 줄었는지(적용 전 대비 -rejected).
