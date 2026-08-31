-- ============================================================================
-- 070_findings_inspector_document_id.sql — 실사관 프로파일 문서 목록에 document_id 를 얹는다
--
-- 왜: 실사관 프로파일(037/039/065)은 문서 이력을 리스트로 보여주면서도 그 문서의
--   정적 상세 페이지(`findings/doc/{document_id}/`)로 가는 링크를 만들 방법이 없었다 —
--   `documents[]` 원소가 raw_signal_id 만 갖고 있고, 정적 문서 페이지의 slug 는
--   `findings.document_id` 다(실측: 3,293/3,301 이 정적 페이지 slug 와 그대로 일치).
--   그 결과 실측 프로파일 화면에서 **문서 상세로 가는 링크가 0개**였다(업체 링크는
--   firm_key 로 이미 8개가 정상 동작 — 문서 쪽만 막다른 길).
--
-- ★순수 가산 계약(불가침, 065 관례 그대로) ─────────────────────────────────
--   * 함수 시그니처 무변경 — findings_inspector_profile(text). PostgREST 는 인자가
--     하나만 달라도 404 를 주므로 시그니처를 바꾸면 라이브 화면이 즉시 깨진다(#681).
--   * 기존 키(inspector_key·display_name·totals·by_category·repeats·by_year 전부,
--     그리고 documents[] 의 기존 필드 raw_signal_id·published_date·source·firm_name·
--     firm_key·obs_cnt·public_obs_cnt·categories) — 값·순서·타입 전부 무변경.
--   * 아래 **한 필드만** 추가된다: documents[].document_id.
--   * findings_inspector_index()·findings_inspector_pairs()·findings_inspector_key()
--     는 이 파일에서 건드리지 않는다(재선언 없음) — 이번 요청 범위는 profile 의
--     문서 목록뿐이고, 색인/정체성 정본을 복제하면 037→039 가 경계한 표류가 재발한다.
--   * 코호트 게이트(문서 5건 미만 null)·공개 게이트(scope_status='ok')·안전 계약
--     (security definer·search_path 고정·원문/URL 무반환)은 그대로.
--   * 이 함수의 최신 정의는 065_profile_categories_and_repeats.sql 이 갖고 있다(그
--     뒤로 재선언 없음) — 그 본문을 `pg_get_functiondef` 로 그대로 가져와 이 한 줄만
--     얹었다(로직 재작성 없음, 검증은 아래 md5 스니펫 참조).
--
-- 2026-08-31 — 037 이 세운 "실사관 디렉터리(목록 열람) 페이지를 만들지 않는다"
--   조항은 이 파일이 건드리는 범위가 아니다(그 조항의 개정은 웹 레이어 — inspector.js/
--   inspector.html/037 헤더 자체의 주석 — 에서 이뤄진다). 이 마이그레이션은 문서 링크
--   한 필드만 추가하며, 037/039 가 세운 안전·범위 계약과 무관하게 그대로 둔다.
--
-- 전제: 065 적용(documents[].categories 존재) + findings.document_id 컬럼(002) 존재.
-- ============================================================================

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
           f.firm_name, f.firm_key, f.finding_text_ko, f.finding_language,
           -- ★신설: 정적 문서 상세 페이지 slug(002 findings.document_id) — 문서 목록에서
           -- findings/doc/{document_id}/ 링크를 만들기 위한 입력.
           f.document_id, p.raw_name as nm
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
            'categories',     categories,
            -- ★신설(이 마이그레이션의 유일한 가산 필드): 정적 문서 상세 페이지 slug.
            -- 문서 하나(raw_signal_id)는 document_id 하나에 대응하므로 max() 는 다른
            -- 집계 컬럼(published_date·source·firm_name·firm_key)과 같은 이유로만
            -- 존재한다 — 그룹 내 유일값을 뽑는 관용구이지 여러 값 중 하나를 고르는
            -- 게 아니다.
            'document_id',    document_id
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
                               order by category_code)) as categories,
            max(document_id) as document_id
          from rows_out
          group by raw_signal_id
          order by max(published_date) desc, raw_signal_id asc
          limit 100
        ) t
      ), '[]'::jsonb)
    )
  end;
$$;

-- create or replace 는 기존 권한을 보존하지만, 065/039 관례대로 명시적으로 재부여한다.
grant execute on function public.findings_inspector_profile(text) to anon, authenticated;

-- ============================================================================
-- 검증(사람 실행용 — 적용 전/후 프로덕션 SQL Editor 또는 anon RPC)
--
-- ★순수 가산 증명(065 가 쓴 방식과 동형): 적용 후 응답에서 documents[].document_id 를
--   제거한 md5 가 적용 전 응답의 md5 와 같아야 한다.
--
--   -- 적용 전에 찍어 둔다(코호트에 있는 실사관 키로):
--   select md5(public.findings_inspector_profile('<inspector_key>')::text);
--
--   -- 적용 후(신설 필드 제거):
--   select md5((
--     public.findings_inspector_profile('<inspector_key>')
--     || jsonb_build_object('documents', (
--          select jsonb_agg(d - 'document_id' order by ord)
--          from jsonb_array_elements(
--                 public.findings_inspector_profile('<inspector_key>') -> 'documents'
--               ) with ordinality as x(d, ord)
--        ))
--   )::text);
--
-- document_id 실재 확인(정적 문서 페이지 slug 와의 일치율 — 헤더에 적은 3,293/3,301 재현):
--   select count(*) filter (where document_id is not null and document_id <> '') as with_id,
--          count(*) as total
--   from public.findings where source = 'FDA 483' and scope_status = 'ok';
-- ============================================================================
