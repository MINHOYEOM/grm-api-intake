-- FIND-1 업체명 정규화(firm_key) + 업체 프로파일 RPC — 로드맵 백로그 FIND-FIRM-ALIAS
-- ("업체명 표기·별칭 정규화") 의 백엔드 절반. 웹 페이지(업체 프로파일 화면)는 후속 PR.
--
-- 근거(컨트롤 타워 라이브 실측, 2026-07): public.findings.firm_name 은 982개 고유 표기가
-- 있으나, 아래 규칙으로 정규화하면 855개 실업체로 수렴한다(충돌 100그룹 -- 서로 다른
-- 표기가 같은 firm_key 로 묶이는 그룹 -- 표본 검사 결과 오병합 0건). 전형적 사례: 아래
-- 6개 표기가 전부 같은 회사(SCA Pharmaceuticals)로 묶인다 --
--   "SCA Pharmaceuticals" / "SCA Pharmaceuticals, Inc." / "SCA Pharmaceuticals LLC" /
--   "SCA Pharmaceuticals Inc" / "SCA Pharmaceuticals, Inc" / "SCA PHARMACEUTICALS INC."
--
-- 정규화 규칙(순서 고정 -- 아래 (A) grm_normalize_firm_name 이 이 규칙의 SQL 정본이며,
-- grm_findings.py 의 normalize_firm_name() 이 파이썬 정본이다. 두 구현은 반드시 동일
-- 결과를 내야 하고, 그 파리티가 유일한 진짜 계약이다 -- 이 파일의 SQL 은 파이썬 정본의
-- 복제본일 뿐, 규칙이 바뀌면 두 곳을 함께 고친다):
--   1) HTML 엔티티 복원: `&amp;` -> `&`, `&#039;` -> `'`
--   2) lowercase
--   3) `[.,]` 제거
--   4) 단어경계 법인접미사 제거: inc|llc|ltd|co|corp|corporation|company|limited|lp|llp|
--      pvt|private|gmbh|sa|srl|dba (Postgres 단어경계는 `\b` 가 아니라 `\y` -- 010 의
--      \yfarm\y 관례와 동일. 단어경계이므로 "Coherus" 처럼 "co" 를 부분 문자열로만 포함한
--      이름은 안전하다 -- \y 는 "co" 앞뒤 모두 단어 경계를 요구하므로 "coherus" 내부에서는
--      매치되지 않는다)
--   5) 연속 공백을 1개로 축약 후 trim
--
-- 파이썬/SQL 파리티는 tests/test_findings_firm_key.py 가 오프라인으로 고정한다(파이썬
-- 함수의 결과값을 20+ 실측 변형 픽스처로 고정 + SQL 함수 본문에 규칙 5개가 모두 반영됐는지
-- 텍스트 계약으로 고정). 실 SQL 실행(라이브 dry-run)은 컨트롤 타워가 검증한다 -- 이 CC
-- 작업 환경은 Postgres 접속이 없다.
--
-- ============================================================================
-- ★004/009 함정 해당 없음: 이 파일은 plpgsql DO 블록/선언 변수를 전혀 쓰지 않는다(004
-- 류 별칭·루프변수 충돌 경로 자체가 없음 -- (A)(C) 모두 language sql 순수 함수다). 배열
-- 슬라이스(009 의 `(coalesce(...))[1:500]` 괄호 함정)도 이 파일에는 없다 -- (C) RPC 는
-- 배열 인자를 받지 않고 스칼라 text 하나(p_firm_key)만 받는다.
-- ============================================================================
--
-- 전제: 002_findings.sql(findings 테이블) + 010_findings_scope_purity.sql(scope_status
-- 컬럼 + 공개 게이트) 이 먼저 적용되어 있어야 한다. 이 파일은 007/008/009/010 의 기존
-- 함수를 전혀 건드리지 않는다(findings_stats/findings_firm_stats/findings_category_matrix/
-- findings_translation_queue/findings_translation_rows 무변경).

-- ============================================================================
-- (A) public.grm_normalize_firm_name(p_name text) -- 정규화 함수. generated column((B))
-- 표현식에 쓰이므로 반드시 IMMUTABLE 이어야 한다(Postgres 는 STORED GENERATED 컬럼
-- 표현식에 IMMUTABLE 이 아닌 함수 사용을 거부한다). lower/replace/regexp_replace/trim
-- 은 전부 카탈로그·테이블을 참조하지 않는 순수 내장 함수라 IMMUTABLE 선언이 안전하다.
-- ============================================================================

create or replace function public.grm_normalize_firm_name(p_name text)
returns text
language sql
immutable
set search_path = public
as $$
  select trim(
    regexp_replace(
      regexp_replace(
        regexp_replace(
          lower(replace(replace(coalesce(p_name, ''), '&amp;', '&'), '&#039;', '''')),
          '[.,]', '', 'g'
        ),
        '\y(inc|llc|ltd|co|corp|corporation|company|limited|lp|llp|pvt|private|gmbh|sa|srl|dba)\y', '', 'g'
      ),
      '\s+', ' ', 'g'
    )
  );
$$;

-- ============================================================================
-- (B) findings.firm_key -- generated always ... stored 컬럼. 트리거·백필 서비스가
-- 필요 없다: STORED GENERATED 컬럼은 (1) 컬럼 추가 시점에 기존 행 전체가 자동 재계산
-- 되고, (2) 이후 모든 INSERT/UPDATE(firm_name 변경 포함)에서 자동으로 다시 계산된다.
-- ============================================================================

alter table public.findings
  add column if not exists firm_key text generated always as (
    public.grm_normalize_firm_name(firm_name)
  ) stored;

create index if not exists idx_findings_firm_key
  on public.findings (firm_key);

-- ============================================================================
-- (C) public.findings_firm_profile(p_firm_key) -- 업체 프로파일 RPC. 007/010 관례와
-- 동일(security definer/stable/language sql/search_path 고정/revoke-then-grant).
--
-- ★안전 계약(불가침, 007/008 과 동일 종류): 이 함수는 finding_text/finding_text_ko 를
-- 어떤 경로로도 반환하지 않는다 -- 집계(count)와 서지 메타(firm_name/category_code/연도/
-- source/published_date/raw_signal_id)만 반환한다. jsonb_build_object 키 목록이 그
-- 계약의 유일한 표면이다. documents 배열도 문서 "목록"만 반환한다(obs_cnt 는 그 문서에
-- 속한 findings 행 수일 뿐, 원문 텍스트가 아니다).
--
-- 전부 scope_status='ok' 필터(010 관례 계승 -- non_pharma/fragment 로 플래그된 행은
-- 집계에서도 제외).
--
-- display_name: firm_key 그룹 내 가장 흔한 firm_name 원문 표기(동률이면 더 긴 표기 -- 예:
-- "SCA Pharmaceuticals" 와 "SCA Pharmaceuticals, Inc." 가 동수면 후자를 표시명으로 채택
-- -- 법인 성격이 드러나는 더 완전한 표기를 우선한다).
--
-- documents: raw_signal_id 로 그룹핑한 문서 목록, 최신 published_date 순 상한 100.
-- public_obs_cnt 는 공개 게이트 조건(006/010: finding_text_ko<>'' or finding_language='KO')
-- 을 만족하는 행 수 -- "이 문서의 지적사항 중 몇 건이 이미 웹에 공개돼 있는지"를 뜻한다.
--
-- 존재하지 않는 firm_key 를 넘기면 에러가 아니라 빈 구조(널이 아닌 유효 jsonb -- totals
-- 카운트 0·배열 필드는 전부 '[]'::jsonb·display_name 은 빈 문자열)를 반환한다.
-- ============================================================================

create or replace function public.findings_firm_profile(p_firm_key text)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'firm_key', p_firm_key,
    'display_name', coalesce((
      select firm_name
      from (
        select firm_name, count(*) as cnt
        from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
        group by firm_name
        order by cnt desc, length(firm_name) desc, firm_name asc
        limit 1
      ) t
    ), ''),
    'totals', jsonb_build_object(
      'findings', (
        select count(*) from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
      ),
      'public_findings', (
        select count(*) from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
          and (finding_text_ko <> '' or finding_language = 'KO')
      ),
      'documents', (
        select count(distinct raw_signal_id) from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
      ),
      'first_seen', (
        select min(published_date) from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
      ),
      'last_seen', (
        select max(published_date) from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
      )
    ),
    'by_category', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'cnt', cnt)
        order by cnt desc, category_code
      )
      from (
        select category_code, count(*) as cnt
        from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
        group by category_code
      ) t
    ), '[]'::jsonb),
    'by_year', coalesce((
      select jsonb_agg(
        jsonb_build_object('year', year, 'cnt', cnt)
        order by year
      )
      from (
        select left(published_date, 4) as year, count(*) as cnt
        from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
        group by left(published_date, 4)
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
        where firm_key = p_firm_key and scope_status = 'ok'
        group by source
      ) t
    ), '[]'::jsonb),
    'documents', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'raw_signal_id', raw_signal_id,
          'published_date', published_date,
          'source', source,
          'obs_cnt', obs_cnt,
          'public_obs_cnt', public_obs_cnt
        )
        order by published_date desc, raw_signal_id asc
      )
      from (
        select
          raw_signal_id,
          max(published_date) as published_date,
          max(source) as source,
          count(*) as obs_cnt,
          count(*) filter (
            where finding_text_ko <> '' or finding_language = 'KO'
          ) as public_obs_cnt
        from public.findings
        where firm_key = p_firm_key and scope_status = 'ok'
        group by raw_signal_id
        order by max(published_date) desc, raw_signal_id asc
        limit 100
      ) t
    ), '[]'::jsonb)
  );
$$;

-- Supabase 는 함수 생성 시 기본적으로 PUBLIC 에 execute 를 부여할 수 있으므로, 먼저
-- 전면 회수한 뒤 anon/authenticated 로만 명시적으로 재부여한다(007/008/009 관례와 동일).
revoke all on function public.grm_normalize_firm_name(text) from public;
revoke all on function public.findings_firm_profile(text) from public;

grant execute on function public.grm_normalize_firm_name(text) to anon, authenticated;
grant execute on function public.findings_firm_profile(text) to anon, authenticated;

-- 검증(사람 실행용, 프로덕션 SQL Editor -- 컨트롤 타워 라이브 dry-run):
-- 1) 실측 수렴(982 -> 855)이 재현되는지:
--    select count(distinct firm_name) as raw_firms, count(distinct firm_key) as norm_firms
--    from public.findings;
-- 2) 충돌 그룹(서로 다른 firm_name 표기가 같은 firm_key 로 묶인 경우) 표본:
--    select firm_key, array_agg(distinct firm_name order by firm_name) as variants, count(*) as n
--    from public.findings group by firm_key having count(distinct firm_name) > 1
--    order by n desc limit 20;
-- 3) generated 컬럼이 신규 insert 에도 자동 반영되는지(다음 daily append 관찰) --
--    이 파일 자체는 오프라인 텍스트 계약 테스트로만 검증됨(tests/test_findings_firm_key.py).
-- 4) 미존재 firm_key 를 넘겨도 에러 없이 빈 구조를 반환하는지:
--    select public.findings_firm_profile('__does_not_exist__');
-- 5) 안전 계약(원문 텍스트 미반환) 수동 확인 -- 반환 jsonb 최상위/documents 배열 원소
--    어디에도 finding_text/finding_text_ko 키가 없어야 한다:
--    select public.findings_firm_profile((select firm_key from public.findings limit 1));
