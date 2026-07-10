-- FIND-1 H1 카테고리×연도 히트맵 서빙 RPC — 007_findings_stats_rpc.sql 과 동일한 안전
-- 계약·관례를 따르는 집계 전용 함수 1개를 추가한다. 007 이 먼저 적용되어 있어야 하며
-- (006 공개 게이트를 우회해 전량 집계를 제공하는 근거는 007 파일 상단 주석 참조), 이
-- 파일은 그 근거를 반복하지 않고 안전 계약만 재확인한다: 아래 함수는 어떤 경로로도
-- finding_text/finding_text_ko/evidence_url/raw_json/row_json 등 원문·URL 텍스트 필드를
-- 반환하지 않는다. 반환 가능한 값은 오직 카운트(count)와 서지 메타(category_code/year)
-- 뿐이다 — jsonb_build_object 키 목록이 그 계약의 유일한 표면이다.
--
-- security definer 로 006 의 RLS 를 우회하되, mutable search_path 취약점을 막기 위해
-- `set search_path = public` 을 고정한다(007/001_reaction.sql 과 동일 관례). ★004 교훈:
-- 이 함수는 파라미터가 없는 순수 SQL(language sql) 함수라 plpgsql DO 블록/record 변수
-- 자체가 없고, 컬럼명과 겹칠 파라미터도 없어 004 류 별칭 충돌 경로가 원천적으로 없다.
--
-- ★scope: 조사관(inspector)별 집계는 데이터 부재로 이번 범위가 아니다 — 어떤 형태로도
-- 넣지 않는다.
--
-- 전제: 002_findings.sql(findings/raw_signals) + 006_findings_publish_gate.sql(공개 게이트
-- 정책) + 007_findings_stats_rpc.sql 이 먼저 적용되어 있어야 한다. 이 파일은 함수 1개만
-- 추가하며 기존 테이블·RLS·정책·007 의 두 함수는 전혀 건드리지 않는다.

-- public.findings_category_matrix(): 카테고리×연도 매트릭스. 빈 테이블에서도 유효한
-- jsonb 를 반환한다(coalesce 로 빈 배열 처리). year 는 `left(published_date, 4)` —
-- published_date 가 빈 문자열인 행은 연도 미상으로 집계·연도 목록 양쪽에서 제외한다.
create or replace function public.findings_category_matrix()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'years', coalesce((
      select jsonb_agg(year order by year)
      from (
        select distinct left(published_date, 4) as year
        from public.findings
        where left(published_date, 4) <> ''
      ) t
    ), '[]'::jsonb),
    'cells', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'year', year, 'cnt', cnt)
        order by category_code, year
      )
      from (
        select category_code, left(published_date, 4) as year, count(*) as cnt
        from public.findings
        where left(published_date, 4) <> ''
        group by category_code, left(published_date, 4)
      ) t
      where cnt > 0
    ), '[]'::jsonb),
    'category_totals', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'cnt', cnt)
        order by cnt desc, category_code
      )
      from (
        select category_code, count(*) as cnt
        from public.findings
        group by category_code
      ) t
    ), '[]'::jsonb)
  );
$$;

-- Supabase 는 함수 생성 시 기본적으로 PUBLIC 에 execute 를 부여할 수 있으므로, 먼저
-- 전면 회수한 뒤 anon/authenticated 로만 명시적으로 재부여한다(007 과 동일 관례 — 이
-- 함수도 공개 집계이므로 anon 에게도 열어야 한다).
revoke all on function public.findings_category_matrix() from public;

grant execute on function public.findings_category_matrix() to anon, authenticated;

-- 검증: 빈 테이블에서도 유효한 jsonb(years/cells/category_totals 전부 [])를 반환하는지 확인.
-- select public.findings_category_matrix();
-- 검증: cells 의 모든 항목이 cnt>0 인지(0건 조합이 섞여 들어오지 않는지) 확인.
-- select jsonb_array_length((select public.findings_category_matrix()->'cells'));
