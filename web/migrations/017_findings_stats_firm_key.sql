-- FIND-1 트렌드 업체 랭킹 정규화 — findings_stats().top_firms 를 firm_name(표기) 대신
-- firm_key(013_findings_firm_key.sql 정규화 컬럼) 로 그룹핑한다.
--
-- 근거(컨트롤 타워 라이브 실측): findings_stats().top_firms 는 지금까지 firm_name 원문
-- 표기로 group by 해왔다. 같은 업체의 표기 변형(예: "SCA Pharmaceuticals" /
-- "SCA Pharmaceuticals, Inc." / "SCA Pharmaceuticals LLC" / "SCA Pharmaceuticals Inc" /
-- "SCA Pharmaceuticals, Inc" / "SCA PHARMACEUTICALS INC." — 013 헤더에 기록된 실측 사례)
-- 가 별개 행으로 쪼개져 실제로는 1위권 업체가 순위표에서 여러 행으로 흩어지고 랭킹이
-- 왜곡된다. firm_key 로 묶으면 이 왜곡이 사라진다.
--
-- ============================================================================
-- supersede 체인: 007_findings_stats_rpc.sql(원본 정의) → 010_findings_scope_purity.sql
-- (scope_status='ok' 필터 + totals.documents 추가, create or replace 로 007 을 supersede)
-- → 이 파일(017)이 010 을 다시 create or replace 로 supersede한다.
--
-- 변경점은 **top_firms 키 하나뿐**이다:
--   - group by firm_name → group by firm_key(013 generated 컬럼, grm_normalize_firm_name
--     정규화 결과)
--   - 각 행에 firm_key 를 새로 추가하고, firm_name 은 그 firm_key 그룹 내 "가장 흔한
--     원문 표기"(동률이면 더 긴 표기)로 채운다 — 013_findings_firm_key.sql 의
--     findings_firm_profile().display_name 서브쿼리와 완전히 동일한 타이브레이크 규칙
--     (order by count(*) desc, length(firm_name) desc, firm_name asc limit 1) 이다.
--   - cnt/public_cnt 의 정의(전체 건수 / 국문 열람 가능 건수)는 그대로다.
-- totals/by_agency_category/by_month/by_source/by_evidence 4개 키는 010 의 findings_stats()
-- 바디와 **글자 하나 다르지 않게** 동일하다 — scope_status='ok' 필터, totals.documents
-- 계산, 서브쿼리 구조 전부 그대로 복사했다(tests/test_findings_stats_firm_key.py 의
-- OtherKeysUnchangedTest 가 010 파일과의 텍스트 대조로 이를 고정한다).
--
-- findings_firm_stats/findings_category_matrix/findings_translation_queue/
-- findings_translation_rows 4개 함수는 이 파일이 전혀 건드리지 않는다(010 정의가 계속
-- 유효). grant(revoke-then-grant) 도 재선언하지 않는다 — create or replace 는 시그니처가
-- 불변(findings_stats() 그대로)이면 기존 함수의 grant 를 보존하므로, 007/010 이 이미
-- anon/authenticated 에 부여한 EXECUTE 가 그대로 유지된다.
--
-- ★안전 계약(불가침, 007/010/013 과 동종): 이 함수는 어떤 경로로도 finding_text/
-- finding_text_ko/evidence_url/raw_json/row_json 등 원문·URL 텍스트를 반환하지 않는다 —
-- 반환 가능한 값은 카운트(count/distinct count)와 서지 메타(agency/category_code/month/
-- source/evidence_level/firm_key/firm_name/published_date)뿐이다. top_firms.public_cnt
-- 계산에 finding_text_ko 가 등장하지만 count(*) filter (where finding_text_ko <> '' ...)
-- 형태의 불리언 게이트일 뿐 반환값이 아니다(007/010 원본과 동일한 종류의 참조).
--
-- ★004/009 함정 해당 여부: 004(선언 변수/별칭 충돌)류 함정 해당 없음 — 이 파일은
-- plpgsql DO 블록·declare 변수를 전혀 쓰지 않는다(language sql 순수 함수 하나뿐). 009
-- (배열 슬라이스 괄호 함정)도 해당 없음 — 이 함수는 배열 인자를 전혀 받지 않는다
-- (파라미터 없는 findings_stats() 그대로).
-- ============================================================================
--
-- 전제: 002_findings.sql + 006_findings_publish_gate.sql + 007_findings_stats_rpc.sql +
-- 010_findings_scope_purity.sql(scope_status 컬럼 + findings_stats 현행 정의) +
-- 013_findings_firm_key.sql(firm_key generated 컬럼) 이 먼저 적용되어 있어야 한다.

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
          -- 013_findings_firm_key.sql findings_firm_profile().display_name 과 동일한
          -- 타이브레이크: 그룹 내 최빈 표기, 동률이면 더 긴 표기, 그래도 동률이면 알파벳.
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

-- 검증(사람 실행용, 프로덕션 SQL Editor — 컨트롤 타워 라이브 dry-run):
-- 1) SCA Pharmaceuticals 6변형이 top_firms 에서 한 행으로 합쳐지는지:
--    select * from jsonb_array_elements(public.findings_stats() -> 'top_firms') e
--    where e ->> 'firm_key' = (
--      select firm_key from public.findings where firm_name ilike 'SCA Pharmaceuticals%' limit 1
--    );
-- 2) firm_name 표시값이 013 display_name 로직(최빈·동률시 최장)과 일치하는지 표본 대조:
--    select f.firm_key, f.firm_name as display_from_stats,
--      (public.findings_firm_profile(f.firm_key) ->> 'display_name') as display_from_profile
--    from jsonb_to_recordset(public.findings_stats() -> 'top_firms')
--      as f(firm_key text, firm_name text) limit 30;
--    -- display_from_stats = display_from_profile 이어야 한다(전 행).
-- 3) 다른 키(totals/by_month/by_agency_category/by_source/by_evidence)가 010 적용
--    직후와 동일한지(이 마이그레이션 적용 전후 값이 바뀌면 안 됨) — 배포 전후 스냅샷 대조.
-- 4) 안전 계약(원문 텍스트 미반환) 수동 확인 — top_firms 배열 원소 어디에도
--    finding_text/finding_text_ko 키가 없어야 한다:
--    select public.findings_stats() -> 'top_firms' -> 0;
