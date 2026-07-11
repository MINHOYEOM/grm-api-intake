-- ※ 2026-07: scope_status='ok' 필터가 010_findings_scope_purity.sql 에서 추가됨(프로덕션
-- 현행 정의는 010 참조).
--
-- FIND-1 F3a 집계 서빙 RPC — 미번역분 포함 전량 집계를 공개 게이트(006, anon/authenticated
-- SELECT 는 finding_text_ko<>'' or finding_language='KO' 인 행만 허용)를 우회해 제공한다.
-- 근거(F2e 정책, docs/GRM_Findings인텔리전스_전략로드맵_2026-07-07.md 부록): 백필분은 전량
-- 영문·미번역이라 row 단위 조회로는 웹에 노출되지 않지만, "집계(트렌드·히트맵·카운트)는
-- 번역 없이 전량 활용" — 사전계산 stats 층은 원문 텍스트를 절대 반환하지 않으므로 게이트와
-- 무충돌이다(공식 공개 문서의 서지 메타 집계는 공개 무해 — 원문 지적 내용 자체가 아니다).
--
-- 안전 계약(불가침): 아래 두 함수는 어떤 경로로도 finding_text/finding_text_ko/evidence_url/
-- raw_json/row_json 등 원문·URL 텍스트 필드를 반환하지 않는다. 반환 가능한 값은 오직 카운트
-- (count/distinct count)와 서지 메타(agency/category_code/month/source/evidence_level/
-- firm_name/published_date)뿐이다 — jsonb_build_object 키 목록이 그 계약의 유일한 표면이다.
--
-- security definer 로 006 의 RLS 를 우회하되, mutable search_path 취약점을 막기 위해
-- `set search_path = public` 을 고정한다(Supabase advisors 경고 방지 — 001_reaction.sql 의
-- private.sync_reaction_count() 와 동일 관례). ★004 교훈: 함수 파라미터/변수명이 컬럼명·
-- 테이블 별칭과 겹치면 라이브에서 모호성 오류가 난다 — findings_firm_stats 의 파라미터는
-- `p_firm`(컬럼명은 `firm_name`)이라 겹치지 않는다. 두 함수 모두 순수 SQL(language sql)이라
-- plpgsql DO 블록/record 변수 자체가 없어 004 류 별칭 충돌 경로가 원천적으로 없다.
--
-- 전제: 002_findings.sql(findings/raw_signals) + 006_findings_publish_gate.sql(공개 게이트
-- 정책) 이 먼저 적용되어 있어야 한다. 이 파일은 함수 2개만 추가하며 기존 테이블·RLS·정책은
-- 전혀 건드리지 않는다.

-- public.findings_stats(): 전체 집계 스냅샷. 빈 테이블에서도 유효한 jsonb 를 반환한다
-- (coalesce 로 빈 배열 처리).
create or replace function public.findings_stats()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'totals', jsonb_build_object(
      'findings', (select count(*) from public.findings),
      'public_findings', (
        select count(*) from public.findings
        where finding_text_ko <> '' or finding_language = 'KO'
      ),
      'raw_signals', (select count(*) from public.raw_signals),
      'firms', (select count(distinct firm_name) from public.findings)
    ),
    'by_agency_category', coalesce((
      select jsonb_agg(
        jsonb_build_object('agency', agency, 'category_code', category_code, 'cnt', cnt)
        order by agency, category_code
      )
      from (
        select agency, category_code, count(*) as cnt
        from public.findings
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
        group by evidence_level
      ) t
    ), '[]'::jsonb),
    'top_firms', coalesce((
      select jsonb_agg(
        jsonb_build_object('firm_name', firm_name, 'cnt', cnt, 'public_cnt', public_cnt)
        order by cnt desc, firm_name asc
      )
      from (
        select
          firm_name,
          count(*) as cnt,
          count(*) filter (
            where finding_text_ko <> '' or finding_language = 'KO'
          ) as public_cnt
        from public.findings
        group by firm_name
        order by cnt desc, firm_name asc
        limit 30
      ) t
    ), '[]'::jsonb)
  );
$$;

-- public.findings_firm_stats(p_firm): 특정 업체 1곳의 집계. p_firm 은 정확 일치(ilike 아님 —
-- 인젝션·성능 안전, 웹이 findings_stats() 의 top_firms.firm_name 값을 그대로 넘기는 계약).
-- 미존재 업체를 넘기면 totals 0(빈 배열들)의 유효 jsonb 를 반환한다(에러가 아니다).
create or replace function public.findings_firm_stats(p_firm text)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'firm_name', p_firm,
    'totals', jsonb_build_object(
      'findings', (
        select count(*) from public.findings where firm_name = p_firm
      ),
      'public_findings', (
        select count(*) from public.findings
        where firm_name = p_firm
          and (finding_text_ko <> '' or finding_language = 'KO')
      )
    ),
    'by_category', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'cnt', cnt)
        order by category_code
      )
      from (
        select category_code, count(*) as cnt
        from public.findings
        where firm_name = p_firm
        group by category_code
      ) t
    ), '[]'::jsonb),
    'by_month', coalesce((
      select jsonb_agg(
        jsonb_build_object('month', month, 'cnt', cnt)
        order by month
      )
      from (
        select left(published_date, 7) as month, count(*) as cnt
        from public.findings
        where firm_name = p_firm
        group by left(published_date, 7)
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
        where firm_name = p_firm
        group by source
      ) t
    ), '[]'::jsonb),
    'first_seen', (
      select min(published_date) from public.findings where firm_name = p_firm
    ),
    'last_seen', (
      select max(published_date) from public.findings where firm_name = p_firm
    )
  );
$$;

-- Supabase 는 함수 생성 시 기본적으로 PUBLIC 에 execute 를 부여할 수 있으므로, 먼저
-- 전면 회수한 뒤 anon/authenticated 로만 명시적으로 재부여한다(001_reaction.sql 의
-- private.sync_reaction_count() 회수 관례와 동형 — 다만 이 두 함수는 anon 에게도 열어야
-- 하는 공개 집계이므로 회수 후 anon/authenticated 로 재부여한다).
revoke all on function public.findings_stats() from public;
revoke all on function public.findings_firm_stats(text) from public;

grant execute on function public.findings_stats() to anon, authenticated;
grant execute on function public.findings_firm_stats(text) to anon, authenticated;

-- 검증: 빈 테이블에서도 유효한 jsonb(모든 배열 필드가 [])를 반환하는지 확인.
-- select public.findings_stats();
-- 검증: 미존재 업체명을 넘겨도 에러 없이 totals 0 의 유효 jsonb 를 반환하는지 확인.
-- select public.findings_firm_stats('__does_not_exist__');
