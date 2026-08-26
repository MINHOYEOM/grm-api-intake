-- ============================================================================
-- 063_fda_inspection_firm.sql — 업체 프로파일에 **FDA 실사 이력**을 붙인다.
--   (A) public.fda_inspections.firm_key  GENERATED STORED (013 정본 함수 재사용)
--   (B) public.fda_inspection_firm(p_firm_key text)  집계 RPC
--
-- ── 왜 ───────────────────────────────────────────────────────────────────────
-- 업체 프로파일(/findings/firm/?key=)은 **지적 이력만** 보여준다. 그런데 실무자가
-- 가장 먼저 묻는 것은 "우리 수탁제조소가 **최근 실사에서 OAI 를 받았나**"이고, 그
-- 질문에 답할 데이터(058 fda_inspections)는 이미 6,417건 들어와 있는데 findings 와
-- 이어지는 키가 없어 한 화면에 설 수 없었다.
--
-- ── 연결률(실측 2026-08-26, 추정 아님) ───────────────────────────────────────
--   · 실사 고유 업체키 4,164  ↔  findings 공개분 고유 업체키 3,570
--   · 겹치는 키 **783**
--   · 실사 6,417행 중 **1,627행(25.4%)** 이 findings 를 가진 업체에 붙는다
--   · findings 지적 **상위 30개 업체 중 17곳(57%)** 이 실사 이력을 얻는다
--   · legal_name → firm_key 정규화 결과가 빈 문자열인 행: **0건**
--
-- ★**연결률 20%대는 결함이 아니라 모집단 차이다.** 안 붙는 쪽은 대부분 캐나다 소재
--   (Air Liquide Canada · Isologic · Linde Canada 등 Health Canada 전용)로 애초에
--   FDA 실사 대상이 아니다. 그래서 상위 업체에서는 57%로 뛴다. 이 사실을 여기 적어
--   두는 이유는, 다음 사람이 25% 를 보고 "매칭이 깨졌다"고 판단해 키를 느슨하게
--   바꾸려는 유혹을 받기 때문이다 — 그 순간 남의 실사 이력이 붙는다.
--
-- ★**미연결 업체에 "FDA 실사 기록 없음"이라고 쓰면 거짓이다.** 이 표는 FY2020 이후
--   Drug Quality Assurance 실사만 담는다. 화면은 반드시 범위를 함께 적어야 하고
--   (firm.js), 그 범위 문자열은 하드코딩이 아니라 이 RPC 의 `scope` 에서 온다.
--   저장소의 "부재 어휘" 규율 그대로다 — 못 받은 것과 없는 것을 구분해 말한다.
--
-- ── 설계 ─────────────────────────────────────────────────────────────────────
-- (A) 013 의 4단 구조(SQL IMMUTABLE 함수 + Python 파리티 + GENERATED STORED + 파리티
--     테스트)를 그대로 계승한다. 055/058 의 country_key 와 같은 방식이라 **새 정규화
--     함수를 만들지 않는다** — 정본이 둘이 되는 순간 반드시 갈라진다.
--     ★GENERATED STORED 라 기존 6,417행이 자동 소급된다(백필 스크립트 0).
--     ★`grm_normalize_firm_name` 이 IMMUTABLE 임을 확인하고 쓴다(provolatile='i'
--       실측) — 아니면 생성열 자체를 만들 수 없다.
--
-- (B) 007/058 안전 계약 계승: **카운트와 서지 메타만** 반환한다. 원문 텍스트·URL 은
--     이 표에 애초에 없다(058 이 적재하지 않는다).
--     ★반환값은 **미존재일 때도 null 이 아니라 0건 구조**다. null 을 돌려주면 화면이
--       "RPC 가 없다(미배포)"와 "이 업체는 실사 기록이 없다"를 구분할 수 없고, 그러면
--       둘 중 하나를 반드시 잘못 말하게 된다 — 013 findings_firm_profile 이 빈 구조를
--       돌려주는 것과 같은 이유다.
--     ★입력이 무엇이든 예외를 던지지 않는다(미존재/빈 문자열/null → 0건 구조).
--
-- 전제: 013(grm_normalize_firm_name) · 058(fda_inspections) 적용 완료.
-- ============================================================================
-- ★004/009 함정 해당 없음: plpgsql DO 블록·선언 변수·배열 슬라이스 없음(순수 SQL).
-- ============================================================================

-- ============================================================================
-- (A) firm_key 생성열 + 인덱스
-- ============================================================================
alter table public.fda_inspections
  add column if not exists firm_key text
  generated always as (public.grm_normalize_firm_name(legal_name)) stored;

create index if not exists fda_inspections_firm_key_idx
  on public.fda_inspections (firm_key);

-- ============================================================================
-- (B) public.fda_inspection_firm(p_firm_key)
--
--   ★security definer + set search_path = public — 표 자체는 RLS enable + 전체
--     revoke 라(058) 이 RPC 가 유일한 노출 경로다. 013 findings_firm_profile 과 동형.
--   ★행 상한 — 한 업체의 실사가 200건을 넘는 경우는 실측상 없다(최다 21건). 상한을
--     두는 이유는 미래의 대형 법인 통합으로 키가 뭉칠 때 응답이 터지지 않게 하는
--     것이고, **잘렸다는 사실을 totals 로 알 수 있다**(inspections 총계는 상한과
--     무관하게 전량을 센다 — 조용한 절단 금지).
-- ============================================================================
create or replace function public.fda_inspection_firm(p_firm_key text)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with k as (
    select btrim(coalesce(p_firm_key, '')) as key
  ),
  rows_ as (
    -- 빈 키는 **센티널 문자열을 지어내지 않고** 조건으로 걸러 낸다 — 지어낸 센티널은
    -- 언젠가 진짜 키와 충돌하고, 그때 남의 실사 이력이 붙는다.
    select i.*
    from public.fda_inspections i, k
    where k.key <> '' and i.firm_key = k.key
  )
  select jsonb_build_object(
    'firm_key', (select key from k),
    -- scope — 화면이 "없음"을 말할 때 **범위를 함께 적게** 하는 근거. 하드코딩 금지.
    -- 이 표가 담는 것은 FY2020 이후 GMP 실사뿐이므로, 범위 없이 "실사 기록 없음"이라고
    -- 쓰면 그 문장은 거짓이 된다.
    'scope', jsonb_build_object(
      'source', 'FDA Data Dashboard API — inspections_classifications',
      'project_area', 'Drug Quality Assurance',
      'fiscal_year_min', (select min(fiscal_year) from public.fda_inspections),
      'fiscal_year_max', (select max(fiscal_year) from public.fda_inspections),
      'latest_inspection_end_date', (select max(inspection_end_date) from public.fda_inspections)
    ),
    'totals', jsonb_build_object(
      -- ★상한과 무관하게 전량을 센다(아래 목록이 잘려도 이 수는 진실이다).
      'inspections', (select count(*) from rows_),
      'nai', (select count(*) from rows_ where classification_code = 'NAI'),
      'vai', (select count(*) from rows_ where classification_code = 'VAI'),
      'oai', (select count(*) from rows_ where classification_code = 'OAI'),
      'sites', (select count(distinct fei_number) from rows_ where fei_number <> ''),
      'first_inspection_end_date', (select min(inspection_end_date) from rows_),
      'last_inspection_end_date', (select max(inspection_end_date) from rows_)
    ),
    'inspections', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'inspection_end_date', r.inspection_end_date,
          'fiscal_year', r.fiscal_year,
          'classification_code', r.classification_code,
          -- legal_name — 한 firm_key 에 표기 변종이 여럿 붙을 수 있어(013 정규화의
          -- 목적이 그것이다) **어느 표기의 사업장이 실사받았는지**를 그대로 보여준다.
          -- FDA 가 공개한 값이며 화면의 업체명과 다를 수 있다(그 차이 자체가 정보다).
          'legal_name', r.legal_name,
          'city', r.city,
          'state', r.state,
          'country_key', r.country_key,
          'country_name', r.country_name,
          -- 지적서가 실제로 공개됐는가 — 우리가 483 본문을 확보할 수 있는지의 상한.
          'citations_posted', (r.posted_citations = 'Yes')
        )
        order by r.inspection_end_date desc nulls last, r.inspection_id
      )
      from (
        select * from rows_
        order by inspection_end_date desc nulls last, inspection_id
        limit 200
      ) r
    ), '[]'::jsonb)
  );
$$;

-- ============================================================================
-- (C) 권한 — revoke 가 grant 보다 **먼저**(058/059/062 과 같은 순서).
-- ============================================================================
revoke all on function public.fda_inspection_firm(text) from public;
grant execute on function public.fda_inspection_firm(text) to anon, authenticated;
