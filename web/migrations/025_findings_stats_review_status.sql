-- [FIND-1] findings_stats() 에 by_review_status 추가 — Codex 후속 감사 신규 Major 수리.
--
-- ★결함: `/findings/` 의 파셋 카운트·대시보드 바가 `computeFacetCounts()`/`renderDash(matched)`
--   에서 **클라이언트에 로드된 ROWS(최초 1,000행)만** 집계하면서 전체 통계처럼 표시됐다.
--   라이브 실측(2026-07-16): 화면 `FDA 483 (910)` vs DB 진실 **8,078** — 11.3% 만 반영.
--   더 나쁜 것은 비율 왜곡이다: MFDS(50)·WL(40)은 최신순 정렬 상단이라 100% 잡혀,
--   사용자는 MFDS 를 코퍼스의 ~5% 로 오해한다(실제 0.6%). F-01(dup 배지 과소표시)과
--   동일 계열 — "부분집합을 사실값처럼 표시".
--
-- ★수리 계약(사용자 확정 2026-07-16):
--   ①무필터·무검색 랜딩 = 이 RPC 의 전역 truth 사용(source·category·month·evidence·
--     review_status·top firms)
--   ②필터·검색 적용 시 = 파셋·대시보드 **숫자 숨김**(부분집합 숫자를 사실값처럼 보여주는
--     것보다 생략이 안전 — 규제 데이터 원칙)
--   ③결과 영역에 항상 보이는 문구로 추가 로딩 중임을 고지
--   ④`오래된순`·`업체명순`은 전량 로드 또는 서버 정렬 없이는 전역 정렬로 표시하지 않음
--   ⑤(후속) 검색·필터·정렬·페이지네이션을 서버 RPC 하나의 정본(canonical search)으로 이전
--
-- 이 파일이 하는 일: 파셋 5종 중 **review_status 만** 기존 RPC 에 없어 추가한다.
--   source·evidence·month 는 이미 by_source/by_evidence/by_month 로 있고,
--   category 는 by_agency_category 를 agency 합산해 유도한다(웹이 처리).
--
-- ★★불가침 — 이 파일은 **라이브 정의(pg_get_functiondef)를 그대로 베이스로** 삼았다.
--   007 원본이나 010 파일에서 복사하면 **017_findings_stats_firm_key.sql 이 재정의한
--   top_firms(firm_key group by + 대표 표시명 lateral)를 조용히 되돌린다**(010 파일 헤더도
--   "top_firms 는 017 참조" 라고 경고한다). 아래 top_firms/totals/by_* 는 라이브에서 읽어온
--   현행 바디 그대로이며, 이 파일의 diff 는 **by_review_status 키 추가 하나뿐**이다.
--
-- 안전 계약(007 이래 불변): 카운트·서지 메타만 반환하고 finding_text/finding_text_ko/
--   evidence_url/raw_json 등 원문·URL 은 어떤 경로로도 반환하지 않는다. 공개 게이트(006/010)를
--   security definer 로 우회해 **전량 집계**하되(집계는 공개 무해), row 노출은 하지 않는다.
--   ※ by_review_status 는 review_status(accepted/needs_review/rejected) 카운트 — 이미
--     row 조회(FIELDS)로 anon 에 공개되는 서지 메타라 새 노출 표면이 아니다.
--
-- 전제: 002 + 006/010 + 017. 함수 1개만 create or replace(멱등). grant 는 재선언하지
--   않는다 — signature 불변이라 007 이 부여한 anon/authenticated EXECUTE 가 유지된다.

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
        select count(*) from public.findings where scope_status = 'ok'
      ),
      'public_findings', (
        select count(*) from public.findings
        where scope_status = 'ok'
          and (finding_text_ko <> '' or finding_language = 'KO')
      ),
      'raw_signals', (select count(*) from public.raw_signals),
      'firms', (
        select count(distinct firm_name) from public.findings where scope_status = 'ok'
      ),
      'documents', (
        select count(distinct raw_signal_id) from public.findings where scope_status = 'ok'
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
        where scope_status = 'ok'
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
        where scope_status = 'ok'
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
        where scope_status = 'ok'
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
        where scope_status = 'ok'
        group by evidence_level
      ) t
    ), '[]'::jsonb),
    -- ★신규(025) — 파셋 5종 중 유일하게 RPC 에 없던 축. 무필터 랜딩에서 "검토 필요 (N)"
    -- 을 전역 진실로 표시하기 위해 필요하다.
    'by_review_status', coalesce((
      select jsonb_agg(
        jsonb_build_object('review_status', review_status, 'cnt', cnt)
        order by review_status
      )
      from (
        select review_status, count(*) as cnt
        from public.findings
        where scope_status = 'ok'
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
          where scope_status = 'ok'
          group by firm_key
          order by cnt desc, firm_key asc
          limit 30
        ) g
        join lateral (
          select firm_name
          from public.findings
          where firm_key = g.firm_key and scope_status = 'ok'
          group by firm_name
          order by count(*) desc, length(firm_name) desc, firm_name asc
          limit 1
        ) dn on true
      ) t
    ), '[]'::jsonb)
  );
$$;

-- 검증(라이브 적용 시):
-- ① 신규 키: select public.findings_stats() -> 'by_review_status';  -- [] 아님
-- ② 회귀 금지 — 기존 키가 전부 그대로이고 값이 적용 전과 동일해야 한다:
--    totals(findings/public_findings/raw_signals/firms/documents) · by_agency_category ·
--    by_month · by_source · by_evidence · top_firms
-- ③ ★017 회귀 금지: top_firms[0] 에 'firm_key' 가 있어야 한다(firm_name group by 로
--    되돌아가면 017 이 고친 순위 왜곡이 재발한다).
-- ④ 안전 계약: 반환 jsonb 어디에도 finding_text/finding_text_ko/evidence_url 이 없다.
