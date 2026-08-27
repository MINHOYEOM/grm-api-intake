-- ============================================================================
-- 065 — 프로파일 RPC 에 문서별 분류와 '반복 확인된 영역'을 더한다 (순수 가산)
--
-- 왜: 업체·실사관 프로파일은 여태 **원시 건수**만 보여줬다("무균보증 51건"). 두 가지가
-- 구조적으로 불가능했기 때문이다.
--
--   ① 프로파일 안에서 좁히기 — 카테고리를 누르면 검색 페이지로 나가야 했다.
--      findings_search 에는 업체·실사관 필터가 없어(firm_name·inspector_names 는 자유
--      검색 blob 에만 있다) `?cat=X&q=이름` 우회로 착지시켜 왔고, 표기 변형(별칭)으로
--      등록·서명된 문서는 부분일치에서 빠질 수 있었다. 문서마다 어떤 분류가 붙어 있는지
--      를 프로파일 응답이 이미 알고 있으면, 나가지 않고 그 자리에서 좁힐 수 있다.
--
--   ② 반복 여부 — "이 업체는 같은 영역을 세 번 지적받았다"가 실무에서 가장 바로 쓰이는
--      신호인데, by_category 는 전 기간 합계라 **한 번에 몰린 것과 여러 번 반복된 것을
--      구분하지 못한다**. 같은 문서 안에서 같은 분류로 5건이 잡힌 것은 반복이 아니라
--      한 번의 실사다 — 그래서 repeats 는 건수가 아니라 **서로 다른 문서 수**로 센다
--      (count(distinct raw_signal_id) >= 2). 이 정의가 이 마이그레이션의 핵심이다.
--
-- 순수 가산 계약(불가침):
--   * 함수 시그니처 무변경 — findings_firm_profile(text) · findings_inspector_profile(text).
--     PostgREST 는 인자가 하나만 달라도 404 를 주므로 라이브 화면이 즉시 깨진다(#681).
--   * 기존 키(totals·by_category·by_year·by_source·documents 의 기존 필드)는 값·순서·
--     타입 전부 무변경. 아래 두 키만 **추가**된다:
--       - documents[].categories : 그 문서에 붙은 분류 코드(중복 제거·코드 오름차순)
--       - repeats                : 문서 2건 이상에서 반복 확인된 분류
--   * 코호트 게이트(실사관 문서 5건 미만이면 null)와 공개 게이트(006/010)는 그대로.
--
-- 빈 분류는 repeats 에서 뺀다 — "미분류가 반복됐다"는 조치로 이어지지 않는다(분류기
-- 상태를 말할 뿐이다). by_category 는 종전대로 전량을 세므로 두 수치가 다를 수 있고,
-- 그게 맞다(#812 의 교훈: 모집단을 밝히되 축을 조용히 바꾸지 않는다).
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
        where firm_key = p_firm_key and scope_status = 'ok'
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
        where firm_key = p_firm_key and scope_status = 'ok'
        group by raw_signal_id
        order by max(published_date) desc, raw_signal_id asc
        limit 100
      ) t
    ), '[]'::jsonb)
  );
$$;


create or replace function public.findings_inspector_profile(p_inspector_key text)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with allp as (select * from public.findings_inspector_pairs()),
  q as (select public.findings_inspector_key(coalesce(p_inspector_key, '')) as qk),
  target as (
    -- 입력이 해소된 키든 병합 전 짧은 표기든 **양쪽 다** 같은 프로파일로 착지한다
    -- (이미 배포된 링크가 어느 형태로 남아 있어도 깨지지 않게).
    select a.inspector_key
    from allp a, q
    where q.qk <> ''
      and (a.inspector_key = q.qk or public.findings_inspector_key(a.raw_name) = q.qk)
    limit 1
  ),
  pairs as (
    select a.* from allp a
    where a.inspector_key = (select inspector_key from target)
  ),
  rows_out as (
    select f.raw_signal_id, f.finding_id, f.published_date, f.source, f.category_code,
           f.firm_name, f.firm_key, f.finding_text_ko, f.finding_language, p.raw_name as nm
    from pairs p
    join public.findings f on f.raw_signal_id = p.raw_signal_id
    where f.source = 'FDA 483' and f.scope_status = 'ok'
  )
  select case
    when (select count(distinct raw_signal_id) from rows_out) < 5 then 'null'::jsonb
    else jsonb_build_object(
      'inspector_key', (select inspector_key from target),
      'display_name', coalesce((
        select nm from rows_out group by nm
        order by count(*) desc, length(nm) desc, nm asc limit 1
      ), ''),
      'totals', jsonb_build_object(
        'findings',        (select count(*) from rows_out),
        'public_findings', (select count(*) from rows_out
                            where finding_text_ko <> '' or finding_language = 'KO'),
        'documents',       (select count(distinct raw_signal_id) from rows_out),
        'firms',           (select count(distinct firm_name) from rows_out where firm_name <> ''),
        'first_seen',      (select min(published_date) from rows_out),
        'last_seen',       (select max(published_date) from rows_out)
      ),
      'by_category', coalesce((
        select jsonb_agg(jsonb_build_object('category_code', category_code, 'cnt', cnt)
                         order by cnt desc, category_code)
        from (select category_code, count(*)::int as cnt from rows_out group by category_code) t
      ), '[]'::jsonb),
      -- ★신설: 이 실사관이 서명한 공개 문서에서 **반복 확인된 영역**(문서 2건 이상).
      -- 037 정책(순위·비교 금지)과 충돌하지 않는다 — 다른 실사관과 견주지 않고 이 사람의
      -- 공개 이력 안에서만 재등장 여부를 말한다.
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
          from rows_out
          where coalesce(category_code, '') <> ''
          group by category_code
          having count(distinct raw_signal_id) >= 2
        ) t
      ), '[]'::jsonb),
      'by_year', coalesce((
        select jsonb_agg(jsonb_build_object('year', year, 'cnt', cnt) order by year)
        from (select left(published_date, 4) as year, count(*)::int as cnt
              from rows_out group by left(published_date, 4)) t
      ), '[]'::jsonb),
      'documents', coalesce((
        select jsonb_agg(
          jsonb_build_object(
            'raw_signal_id',  raw_signal_id,  'published_date', published_date,
            'source',         source,         'firm_name',      firm_name,
            'firm_key',       firm_key,       'obs_cnt',        obs_cnt,
            'public_obs_cnt', public_obs_cnt,
            -- ★신설: 프로파일 안 좁히기의 입력.
            'categories',     categories
          )
          order by published_date desc, raw_signal_id asc
        )
        from (
          select raw_signal_id,
            max(published_date) as published_date, max(source) as source,
            max(firm_name) as firm_name, max(firm_key) as firm_key,
            count(*)::int as obs_cnt,
            count(*) filter (
              where finding_text_ko <> '' or finding_language = 'KO'
            )::int as public_obs_cnt,
            to_jsonb(array_agg(distinct category_code
                               order by category_code)) as categories
          from rows_out
          group by raw_signal_id
          order by max(published_date) desc, raw_signal_id asc
          limit 100
        ) t
      ), '[]'::jsonb)
    )
  end;
$$;

-- create or replace 는 기존 권한을 보존하지만, 013/039 관례대로 명시적으로 재부여한다.
grant execute on function public.findings_firm_profile(text) to anon, authenticated;
grant execute on function public.findings_inspector_profile(text) to anon, authenticated;

-- ============================================================================
-- 검증(사람 실행용 — 적용 전/후 프로덕션 SQL Editor 또는 anon RPC)
--
-- ★순수 가산 증명: 적용 **후** 응답에서 신설 키를 제거한 md5 가 적용 **전** 응답의 md5 와
--   같아야 한다(062 가 쓴 것과 같은 방식 — 눈으로 훑는 대신 산술로 못박는다).
--
--   -- 적용 전에 찍어 둔다:
--   select md5(public.findings_firm_profile('<firm_key>')::text);
--
--   -- 적용 후(신설 2키 제거):
--   select md5((
--     (public.findings_firm_profile('<firm_key>') - 'repeats')
--     || jsonb_build_object('documents', (
--          select jsonb_agg(d - 'categories' order by ord)
--          from jsonb_array_elements(
--                 public.findings_firm_profile('<firm_key>') -> 'documents'
--               ) with ordinality as x(d, ord)
--        ))
--   )::text);
--
-- 반복 정의 확인(같은 문서 안 다건이 반복으로 새어 들어가지 않는지):
--   select category_code, count(*) as findings, count(distinct raw_signal_id) as docs
--   from public.findings
--   where firm_key = '<firm_key>' and scope_status = 'ok'
--   group by category_code order by docs desc, findings desc;
--   -- docs = 1 인 분류는 repeats 에 나오면 안 된다.
-- ============================================================================
