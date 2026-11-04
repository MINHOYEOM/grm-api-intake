-- 지식그래프 2순위 후속 — 같은 회사의 표기 변종 26그룹을 별칭으로 묶어 업체 프로필·실사 이력이
-- 합쳐 보이게 한다. **firm_key 는 바꾸지 않는다**(생성 컬럼·URL·firm_watchlist 불변). 가산만:
--   (A) 별칭 테이블 firm_alias (B) 그룹 조회 함수 grm_firm_group_keys
--   (C) findings_firm_profile (D) fda_inspection_firm — 두 RPC 의 **반환 shape 무변경**,
--       `firm_key = p_firm_key` 한 줄만 `= any(그룹 키들)` 로 바뀐다. 별칭이 없는 업체는 배열이
--       [자기 자신] 이라 결과가 이전과 같다(아래 검증 쿼리).
--
-- 시드 근거(2026-11-03 읽기 전용 시뮬레이션, grm_normalize_firm_name_v2 의 26그룹):
-- alias_key = 합쳐질 기존 firm_key, canonical_key = v2 결과이자 이미 존재하는 기존 firm_key.
-- 시드는 grm_findings.FIRM_KEY_ALIASES 와 같아야 한다(tests/test_firm_alias_groups.py 가 고정).
-- 같은 회사 단위 병합이라 사이트 구분은 findings.fei(101)가 맡는다. 알려진 위험: natco pharma
-- (canada)·biocon sdn bhd 처럼 해외 자회사가 본사 그룹에 들어간다.
--
-- ★ 04/009 류 함정: plpgsql 없음(language sql). 배열 슬라이스 없음.

-- (A) 별칭 테이블 — 클라이언트 직접 접근 없음(RPC 가 security definer 로만 읽는다).
create table if not exists public.firm_alias (
  alias_key     text primary key,
  canonical_key text not null,
  check (alias_key <> canonical_key)
);
alter table public.firm_alias enable row level security;
revoke all on table public.firm_alias from public, anon, authenticated;

insert into public.firm_alias (alias_key, canonical_key) values
  ('agc biologics spa', 'agc biologics'),
  ('akorn ag', 'akorn'),
  ('the american national red cross', 'american national red cross'),
  ('aurobindo pharma (unit vii)', 'aurobindo pharma'),
  ('bausch and lomb incorporated', 'bausch & lomb'),
  ('biocon sdn bhd', 'biocon'),
  ('central admixture pharmacy services (caps)', 'central admixture pharmacy services'),
  ('college pharmacy incorporated', 'college pharmacy'),
  ('divi''s laboratories (unit ii)', 'divi''s laboratories'),
  ('downing labs (formerly nuvision pharmacy )', 'downing labs'),
  ('fresenius kabi ag', 'fresenius kabi'),
  ('hartley medical center pharmacy incorporated', 'hartley medical center pharmacy'),
  ('imprimis njof -', 'imprimis njof'),
  ('infusion partners (subsidiary of bioscrip)', 'infusion partners'),
  ('jd and sn', 'jd & sn'),
  ('johnson and johnson (mcneil)', 'johnson & johnson'),
  ('logistic support unit (now operating under del 3-002794-a)', 'logistic support unit'),
  ('medi-fare drug and home health center', 'medi-fare drug & home health center'),
  ('natco pharma (canada)', 'natco pharma'),
  ('peanut of america (pca)', 'peanut of america'),
  ('pharmacia and upjohn', 'pharmacia & upjohn'),
  ('region care (northwell health)', 'region care'),
  ('sanofi spa', 'sanofi'),
  ('south coast specialty compounding (park pharmacy and compounding)', 'south coast specialty compounding'),
  ('sun pharmaceutical medicare (spml)', 'sun pharmaceutical medicare'),
  ('the wellness pharmacy', 'wellness pharmacy')
on conflict (alias_key) do nothing;

-- (B) 그룹 조회 — 정본 키 + 그 정본으로 향하는 모든 별칭. 별칭에 없는 키는 [자기 자신].
-- 입력이 별칭이든 정본이든 같은 배열(순서 무관)을 낸다.
create or replace function public.grm_firm_group_keys(p_key text)
returns text[]
language sql
stable
security definer
set search_path = public
as $$
  with c as (
    select coalesce(
      (select a.canonical_key from public.firm_alias a where a.alias_key = p_key),
      p_key) as ck
  )
  select array(
    select ck from c
    union
    select a.alias_key from public.firm_alias a, c where a.canonical_key = c.ck
  );
$$;
revoke all on function public.grm_firm_group_keys(text) from public, anon, authenticated;

-- (C)(D) 아래 두 함수는 065 / 063 본문을 그대로 옮기고 키 비교만 바꿨다.


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
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
        group by firm_name
        order by cnt desc, length(firm_name) desc, firm_name asc
        limit 1
      ) t
    ), ''),
    'totals', jsonb_build_object(
      'findings', (
        select count(*) from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
      ),
      'public_findings', (
        select count(*) from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
          and (finding_text_ko <> '' or finding_language = 'KO')
      ),
      'documents', (
        select count(distinct raw_signal_id) from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
      ),
      'first_seen', (
        select min(published_date) from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
      ),
      'last_seen', (
        select max(published_date) from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
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
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
        group by category_code
      ) t
    ), '[]'::jsonb),
    -- ★신설: 반복 확인된 영역 — 문서 수로 센다(같은 문서 안 5건 = 반복 아님).
    'repeats', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'category_code', category_code,
          'documents',     documents,
          'findings',      findings,
          'first_seen',    first_seen,
          'last_seen',     last_seen,
          'years',         years
        )
        order by documents desc, findings desc, category_code
      )
      from (
        select
          category_code,
          count(distinct raw_signal_id)::int as documents,
          count(*)::int                      as findings,
          min(published_date)                as first_seen,
          max(published_date)                as last_seen,
          to_jsonb(array_agg(distinct left(published_date, 4)
                             order by left(published_date, 4))) as years
        from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
          and coalesce(category_code, '') <> ''
        group by category_code
        having count(distinct raw_signal_id) >= 2
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
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
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
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
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
          'public_obs_cnt', public_obs_cnt,
          -- ★신설: 이 문서에 붙은 분류(프로파일 안 좁히기의 입력).
          'categories', categories
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
          ) as public_obs_cnt,
          to_jsonb(array_agg(distinct category_code
                             order by category_code)) as categories
        from public.findings
        where firm_key = any(public.grm_firm_group_keys(p_firm_key)) and scope_status = 'ok'
        group by raw_signal_id
        order by max(published_date) desc, raw_signal_id asc
        limit 100
      ) t
    ), '[]'::jsonb)
  );
$$;

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
    where k.key <> '' and i.firm_key = any(public.grm_firm_group_keys(k.key))
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

revoke all on function public.fda_inspection_firm(text) from public;
grant execute on function public.findings_firm_profile(text) to anon, authenticated;
grant execute on function public.fda_inspection_firm(text) to anon, authenticated;

-- 검증(사람 실행용 — 적용 후):
--   -- 별칭 없는 업체는 이전과 동일: 적용 전에 md5 를 떠 두고 적용 후 비교
--   select md5(public.findings_firm_profile('hospira')::text);
--   -- 별칭 그룹: 어느 쪽 키로 열어도 같은 합계
--   select public.findings_firm_profile('biocon')->'totals' = public.findings_firm_profile('biocon sdn bhd')->'totals';
--   select public.grm_firm_group_keys('akorn ag');   -- {akorn,"akorn ag"}
