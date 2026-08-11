-- ============================================================================
-- 058_fda_inspections.sql — FDA 실사 등급(GMP) 신규 테이블 + 집계 RPC
--
-- 배경: FDA Data Dashboard API `inspections_classifications` 엔드포인트에서
--   가져온 실사 등급(NAI/VAI/OAI) 데이터를 findings 와 나란히 세울 새 축이다.
--   실측(오프라인 픽스처 전수 — scratchpad ddapi_gmp.json, 2026-08-11 캡처):
--   ProductType='Drugs' 로 받은 전량은 11,104건이지만 그 안에 성격이 다른 두 모집단이
--   섞여 있다(ProjectArea 기준) —
--     Drug Quality Assurance          6,417  ← GMP 제조소 실사. 이 파일이 싣는 유일한 대상
--     Bioresearch Monitoring          4,651  ← 임상시험 실시기관 감시(업체명이 사람 이름)
--     Unapproved and Misbranded Drugs    31
--     Over-the-Counter Drug Evaluation    5
--   합치면 OAI 비율이 GMP 14.9%/BIMO 2.0% → 합산 8.6%라는 무의미한 수가 된다 —
--   이 저장소가 052/053/054 에서 "성격이 다른 모집단을 한 축에 합치면 진단이 반드시
--   틀린다"를 세 번 겪은 바로 그 함정이다. 그래서 (A)에 project_area/product_type
--   CHECK 제약을 걸어 이 표를 구조적으로 GMP 단일 모집단에 고정한다(다른 모집단이
--   섞여 들어오면 조용히 오염되는 게 아니라 INSERT 가 즉시 실패한다 — fail-closed).
--
--   ★ProjectArea 는 DDAPI 의 filters 파라미터로 걸 수 없는 반환 전용 컬럼이다
--   (filters 에 넣으면 statuscode 413 `invalid_filters:["ProjectArea"]`) — 그래서
--   수집기는 ProductType=Drugs 전량(11,104건)을 받은 뒤 클라이언트에서 ProjectArea
--   로 걸러 이 표에 적재해야 한다. 이 사실은 수집기(다음 단계) 책임이지만, 이 표의
--   CHECK 제약이 그 필터링이 실제로 지켜졌는지를 적재 시점에 강제 검증한다.
--
-- ★InspectionID PK 근거(직접 확인, 오프라인 픽스처 6,417건 전수): 원본 11,104건
--   에는 같은 실사가 두 ProjectArea 로 동시 분류돼 생기는 중복 InspectionID 가
--   81개 있지만, GMP(Drug Quality Assurance)만 남기면 **6,417건 = 고유 ID 6,417개,
--   중복 0**이다 -- tests/test_fda_inspections.py 가 이 사실(GMP 만 추리면 PK 유일성이
--   성립한다는 전제)을 텍스트 계약으로 고정한다. 향후 FDA 가 분류 기준을 바꿔 이
--   전제가 깨지면 PRIMARY KEY 제약이 적재 시점에 즉시 터진다(조용한 데이터 손실이
--   아니라 fail-closed).
--
-- ★CountryName 정규화: country_key 는 055/057 이 만든 public.grm_normalize_country
--   를 그대로 재사용한다(새 정규화 함수를 만들지 않는다). 057 이 이 표를 위해 DDAPI
--   CountryName 27종을 그 함수에 이미 추가해뒀다 — 오프라인 픽스처 62개 CountryName
--   전수가 057 적용 후 country_key='' (미확인) 0건으로 매핑됨을 확인했다(057 파일
--   Ddapi057ExtensionTest.test_all_62_ddapi_country_names_map 참고). ★CountryCode
--   컬럼은 이 표에 싣지 않는다 — 한국 85건 전부를 포함해 96건이 CountryCode=null
--   이면서 CountryName 은 채워져 있는 실측을 확인했고(홍콩/마카오/도미니카공화국도
--   동일), country_key 축을 CountryName 파생으로 통일해 findings.country_key 와
--   같은 표현(ISO2)·같은 정본 함수를 쓰는 편이 CountryCode 의 null 누락을 아예
--   구조적으로 피한다.
--
-- 전제: 057_grm_normalize_country_ddapi.sql(grm_normalize_country 27종 확장) 이
--   먼저 적용되어 있어야 한다 — country_key generated 컬럼이 그 확장된 정의를
--   가정한다(확장 전에 이 표를 만들어도 컬럼 자체는 생성되지만 27종이 매핑 밖으로
--   샌다 -- 반드시 057 다음에 적용).
-- ============================================================================
-- ★004/009 함정 해당 없음(013/055 관례 계승): plpgsql DO 블록·선언 변수·배열 슬라이스
-- 전혀 없음 — (A)(C) 모두 language sql 순수 정의다(트리거·백필 없음 -- 신규 테이블
-- 이라 기존 행이 없다).
-- ============================================================================

-- ============================================================================
-- (A) public.fda_inspections — GMP 실사 등급 원장. 컬럼명은 DDAPI 원문 필드명을
--   snake_case 로 옮긴 것(수집기가 그대로 매핑할 수 있게 원문과 1:1 대응 유지).
--
--   ★project_area/product_type CHECK — 위 헤더의 "합치면 진단이 틀린다" 방어를
--   스키마 층에 고정한다. 이 두 컬럼을 왜 남기냐면(값이 상수인데도): (1) DDAPI 원본
--   필드를 그대로 보존해 재검증 가능하게 하고, (2) CHECK 제약이 "왜 이 값이어야
--   하는지"를 스키마 자체가 말하게 한다(주석이 아니라 강제).
--   ★classification_code CHECK — 오프라인 픽스처 6,417건 전수 실측: NAI/VAI/OAI
--   3종만 존재(다른 값 0건). 향후 FDA 가 신규 코드를 도입하면 이 제약이 즉시
--   터진다 -- classification_code 축 집계(RPC (C))가 3종을 하드코딩 가정하므로,
--   조용히 4번째 값이 새서 그 집계가 틀리는 것보다 적재 실패가 안전하다.
-- ============================================================================

create table if not exists public.fda_inspections (
  inspection_id        text primary key,
  fei_number            text not null default '',
  legal_name             text not null default '',
  city                    text not null default '',
  -- state/state_code/zip_code -- 오프라인 픽스처 실측: 6,417건 중 3,193건(해외 실사
  -- 전부)이 이 세 필드에서 DDAPI 로부터 JSON null 을 받는다(빈 문자열이 아니라 null).
  -- ★수집기(다음 단계)는 반드시 null 을 ''로 coalesce 한 뒤 insert 해야 한다 --
  -- 이 세 컬럼이 not null 이므로 null 을 그대로 넘기면 즉시 insert 가 실패한다
  -- (fail-closed -- 조용히 다른 기본값으로 채워지는 것보다 안전하다고 판단했다).
  state                   text not null default '',
  state_code              text not null default '',
  zip_code                text not null default '',
  -- CountryCode 컬럼은 이 표에 없다(위 헤더 참고) -- country_name 은 실측 0건 결측.
  country_name            text not null default '',
  inspection_end_date     date,
  fiscal_year             int not null,
  classification          text not null,
  classification_code     text not null check (classification_code in ('NAI', 'VAI', 'OAI')),
  project_area            text not null check (project_area = 'Drug Quality Assurance'),
  product_type            text not null check (product_type = 'Drugs'),
  posted_citations        text not null default '',
  ingested_at             timestamptz not null default now(),
  -- country_key -- 055/057 의 grm_normalize_country 정본을 재사용(신규 정규화
  -- 함수를 만들지 않는다). '055_findings_country_key.sql' (B)/(013 firm_key)와
  -- 동형인 STORED GENERATED 컬럼 -- 트리거·백필 불필요(신규 테이블이라 소급 대상
  -- 행 자체가 없다).
  country_key             text generated always as (
                             public.grm_normalize_country(country_name)
                           ) stored
);

create index if not exists fda_inspections_country_key_idx
  on public.fda_inspections (country_key);
create index if not exists fda_inspections_classification_code_idx
  on public.fda_inspections (classification_code);
create index if not exists fda_inspections_inspection_end_date_idx
  on public.fda_inspections (inspection_end_date);
create index if not exists fda_inspections_fei_number_idx
  on public.fda_inspections (fei_number);

-- ============================================================================
-- (B) RLS — 관례 조사 결과(002/003/010 findings·raw_signals 대조, 직접 확인):
--
--   이 저장소는 공개 테이블에 두 가지 다른 관례를 쓴다.
--   ① public.findings(002+003+010) -- RLS enable 후 anon/authenticated 에
--      **직접 SELECT 정책**을 건다(`using (finding_text_ko <> '' and
--      scope_status = 'ok')`). findings 는 "적재 원본"이 아니라 분류·번역·순도
--      필터를 거친 **공개 산출물 층**이라 행 단위 직접 노출이 그 층의 존재 이유다.
--   ② public.raw_signals(002) -- RLS enable 후 anon/authenticated 권한을 전면
--      회수하고 정책을 하나도 안 건다(완전 차단). 대신 그 위에 얹힌 집계
--      RPC(findings_stats() 등, security definer)가 `count(*) from
--      public.raw_signals`처럼 **집계만** 꺼내 쓴다(007 실측 확인 -- raw_signals
--      를 직접 읽는 anon 노출 SELECT 정책은 이 저장소 어디에도 없다).
--
--   fda_inspections 는 ①이 아니라 **②(raw_signals) 관례를 택한다**. 근거:
--   - 이 표는 findings 처럼 분류/순도 필터/번역을 거치는 "공개 산출물 층"이
--     아니다 -- DDAPI 응답을 컬럼만 옮긴 **적재 원본**이다(findings 로 치면
--     raw_signals 에 해당하는 계층이지, findings 자체가 아니다).
--   - 이번 임무가 실제로 요구하는 소비 표면은 (C)의 **집계 RPC 하나뿈이다**
--     (트렌드 페이지의 "분모"). 행 단위 직접 조회(예: 업체별 실사 이력 페이지)는
--     아직 어떤 화면도 요구하지 않는다 -- "배선 없는 슬롯 금지" 규율상, 쓰는
--     화면이 없는데 anon 직접 SELECT 를 미리 열어두지 않는다.
--   - legal_name/fei_number 는 개인식별정보가 아니라 공개 규제 통계이므로(임무서
--     명시) 잠그는 이유는 프라이버시가 아니라 **미사용 표면 최소화**다. 이후 업체
--     프로파일처럼 행 단위 조회가 실제로 필요해지면, 003_findings_public_read.sql
--     이 002 위에 그렇게 했듯 **추가 전용** 마이그레이션으로 anon SELECT 정책만
--     얹으면 된다(이 파일을 고칠 필요 없음).
--   - service_role(수집기가 쓰는 자격증명)은 RLS 를 우회하므로 이 잠금과 무관하게
--     계속 자유롭게 적재할 수 있다.
-- ============================================================================

alter table public.fda_inspections enable row level security;
revoke all on public.fda_inspections from anon, authenticated;

-- ============================================================================
-- (C) public.fda_inspection_stats() -- 집계 전용 RPC(007/038 안전 계약 계승):
--   원문·개인정보(legal_name/fei_number/city/state/zip_code) 를 어떤 경로로도
--   반환하지 않는다 -- 집계(count)와 서지 축(fiscal_year/classification_code/
--   country_key/country_name 대표값)만 반환한다. OAI 비율 등 파생 비율은 서버가
--   계산하지 않는다(007/038 관례: 서버는 센다, 나누기는 클라이언트가 한다).
--
--   ★by_country 는 "상위 N" 이 아니라 전량을 반환한다(의도적으로 임무서 문구와
--   다른 선택 -- 근거를 아래에 남긴다). 오프라인 픽스처 실측: 국가는 62종뿐이고
--   응답 크기가 수 KB 수준이라 잘라낼 이유가 없다. 038/056 의 top_countries 가
--   limit 10 을 쓴 이유는 findings.site_country 축이 findings 유입량과 함께 계속
--   불어나는 축이라서지, 이 표처럼 "한 번에 받은 6,417건 고정 스냅샷"에는 그
--   전제가 없다. 자르면 056 이 지킨 "합계 정합"(sum(by_country[].total) =
--   totals.inspections) 이 깨지고, 이 저장소는 top_countries 를 자를 때도 국가
--   구성을 숨기지 않는다는 원칙(056 "27.4%를 숨기지 않는다")을 지켜왔다 -- 여기서도
--   같은 원칙을 따른다.
--   ★country_key = '' (미확인) 버킷도 그대로 포함한다 -- 043/055/056 관례와 동일
--   (미확인을 숨기지 않는다). 이 버킷에는 대표 국가명을 고르지 않는다(여러 다른
--   미확인 국가명이 한 버킷에 섞일 수 있어 특정 원문을 "대표"로 고르면 오도한다) --
--   'country' 키를 null 로 낸다.
-- ============================================================================

create or replace function public.fda_inspection_stats()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with base as (
    select inspection_id, classification_code, fiscal_year, country_key, country_name
    from public.fda_inspections
  )
  select jsonb_build_object(
    'scope', jsonb_build_object(
      'source', 'FDA Data Dashboard API — inspections_classifications',
      -- project_area/product_type 은 (A)의 CHECK 제약이 표 전체에 이미 강제하므로
      -- 상수로 echo 한다(제약이 깨지면 애초에 이 값의 행이 표에 존재할 수 없다).
      'project_area', 'Drug Quality Assurance',
      'product_type', 'Drugs',
      -- 적재 대상에서 뺀 모집단 -- 정성적 고지일 뿐 이 표에서 라이브로 집계한 수치가
      -- 아니다(그 행들은 애초에 이 표에 없다 -- 헤더의 2026-08-11 실측: BIMO 4,651 /
      -- Unapproved and Misbranded Drugs 31 / OTC Drug Evaluation 5, ProductType=
      -- Drugs 전체 11,104건 중 GMP 6,417건을 뺀 나머지).
      'excluded_project_areas', jsonb_build_array(
        'Bioresearch Monitoring',
        'Unapproved and Misbranded Drugs',
        'Over-the-Counter Drug Evaluation'
      ),
      'fiscal_year_min', (select min(fiscal_year) from base),
      'fiscal_year_max', (select max(fiscal_year) from base),
      -- 055 의 excluded_unknown_country/findings_country_unmapped() 와 같은 취지 --
      -- 매핑 밖으로 새는 country_name 이 있으면 조용히 묻지 않고 센다.
      'unmapped_country_count', (select count(*) from base where country_key = '')
    ),
    'totals', jsonb_build_object(
      'inspections', (select count(*) from base),
      'nai', (select count(*) from base where classification_code = 'NAI'),
      'vai', (select count(*) from base where classification_code = 'VAI'),
      'oai', (select count(*) from base where classification_code = 'OAI')
    ),
    'by_year', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'fiscal_year', t.fiscal_year,
          'nai', t.nai, 'vai', t.vai, 'oai', t.oai
        )
        order by t.fiscal_year
      )
      from (
        select
          fiscal_year,
          count(*) filter (where classification_code = 'NAI')::int as nai,
          count(*) filter (where classification_code = 'VAI')::int as vai,
          count(*) filter (where classification_code = 'OAI')::int as oai
        from base
        group by fiscal_year
      ) t
    ), '[]'::jsonb),
    'by_country', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'code',    g.country_key,
          'country', case when g.country_key = '' then null else rep.country_name end,
          'nai', g.nai, 'vai', g.vai, 'oai', g.oai, 'total', g.total
        )
        order by g.total desc, g.country_key
      )
      from (
        select
          country_key,
          count(*) filter (where classification_code = 'NAI')::int as nai,
          count(*) filter (where classification_code = 'VAI')::int as vai,
          count(*) filter (where classification_code = 'OAI')::int as oai,
          count(*)::int as total
        from base
        group by country_key
      ) g
      left join lateral (
        -- 대표 표기: country_key 그룹 내 건수가 가장 많은 country_name 원문(동률이면
        -- 사전순 -- 013/055 결정론 관례와 동일). country_key='' 그룹에도 계산은 되지만
        -- 위 jsonb_build_object 의 case 가 그 값을 null 로 덮어써 출력하지 않는다.
        select b2.country_name
        from base b2
        where b2.country_key = g.country_key
        group by b2.country_name
        order by count(*) desc, b2.country_name asc
        limit 1
      ) rep on true
    ), '[]'::jsonb)
  );
$$;

revoke all on function public.fda_inspection_stats() from public;
grant execute on function public.fda_inspection_stats() to anon, authenticated;

-- 검증(사람 실행용, 프로덕션 SQL Editor -- 컨트롤 타워 라이브 dry-run, 수집기가
-- 이 표를 채운 뒤):
-- 1) PK 유일성 전제가 실제로 성립하는지(0건이어야 한다 -- 성립 안 하면 애초에
--    insert 가 여기까지 오지 못했겠지만, 방어적으로 재확인):
--    select count(*) from (select inspection_id, count(*) c from public.fda_inspections
--                            group by inspection_id having count(*) > 1) t;
-- 2) 헤더의 실측치가 재현되는지:
--    select public.fda_inspection_stats() -> 'totals';
--    -- {"inspections": 6417, "nai": 1409, "vai": 4055, "oai": 953} 근방이어야 한다
--    -- (수집기가 적재를 끝낸 시점 기준 -- 이후 신규 회계연도 실사가 쌓이면 자연히 늘어난다).
-- 3) 국가 축 미확인 0건인지(057 확장이 실제로 적용됐는지의 방증):
--    select public.fda_inspection_stats() -> 'scope' ->> 'unmapped_country_count';  -- '0' 근방
-- 4) 합계 정합(country 절단이 없는지):
--    with r as (select public.fda_inspection_stats() as j)
--    select
--      (select sum((e->>'total')::int) from r, jsonb_array_elements(j->'by_country') e),
--      (r.j->'totals'->>'inspections')::int
--    from r;  -- 두 값이 같아야 한다
-- 5) anon 이 표를 직접 못 읽는지(집계 RPC 로만 접근 가능한지):
--    set role anon; select count(*) from public.fda_inspections;  -- permission denied
--    reset role;
