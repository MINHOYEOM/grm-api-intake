-- ============================================================================
-- 039_findings_inspector_alias.sql — 실사관 정체성에 모호하지 않은 별칭 병합
--
-- 배경: 037 은 `Matthew Casale` 과 `Matthew B Casale` 을 **다른 사람으로** 셌다(중간이름
--   구조가 다르면 병합 안 함). 그 보수성의 이유는 옳았다 — 동명이인을 합치면 남의 실사
--   이력을 붙이는 것이라, 적게 세는 쪽이 안전하다.
--   그러나 같은 이름+성에 중간이름을 가진 후보가 **정확히 하나뿐**이면 동일인으로 보는
--   것이 안전하다. 후보가 둘 이상이면 그것이야말로 동명이인 신호이므로 병합하지 않는다.
--
--   실측(2026-07-31): 2토큰 이름 119개 중 병합 대상 **24개, 전부 모호성 없음**
--   (후보 2개 이상인 경우 0건). 결과: 실사관 612 → **588명**, 코호트 95 → **98명**.
--   실제 사례: `Logan T Williams`/`Logan T. Williams`/`Logan Williams`(16문서) ·
--   `Scott Ballard`/`Scott T Ballard`/`Scott T. Ballard`(12문서) · `Robert Ham`/`Robert J Ham`.
--
-- supersede: 037 의 `findings_inspector_index`/`findings_inspector_profile` 를 대체한다.
--   `findings_inspector_key`(순수 정규화)는 037 정의 그대로 — 이 파일은 건드리지 않는다.
--
-- ─ 단일 정본 ───────────────────────────────────────────────────────────────
--   `findings_inspector_pairs()` 를 신설해 (문서, **해소된** 키, 원표기) 쌍을 돌려주고,
--   index/profile 이 **둘 다 그것만** 쓴다. 각자 CTE 를 복제하면 한쪽만 바뀌는 표류가
--   생긴다(이 저장소가 수동 허용목록 표류로 두 번 당한 것과 같은 계열).
--
-- ─ 병합 규칙 ───────────────────────────────────────────────────────────────
--   2토큰 키(중간이름 없음) → 같은 first/last 를 가진 3토큰 이상 키가 **정확히 1개**일 때만
--   그쪽으로 흡수. 2개 이상이면 병합하지 않는다.
--   ※표기 변형(마침표·대소문자)은 037 의 `findings_inspector_key` 가 이미 흡수한다.
--
-- ─ 입력 해소(링크 호환) ────────────────────────────────────────────────────
--   `profile(p_inspector_key)` 는 **해소된 키든 병합 전 짧은 표기든 양쪽 다** 같은 프로파일로
--   착지한다. 이미 배포된 링크가 짧은 형태로 남아 있어도 깨지지 않아야 하기 때문이다.
--   실측: `logan williams` · `logan t williams` · `Logan T. Williams` 셋 다 동일 프로파일
--   (16문서·display `Logan T Williams`).
--
-- ─ 성능 대가(의도적 수용) ──────────────────────────────────────────────────
--   별칭 판정은 "이 이름+성을 가진 다른 키가 몇 개인가"라는 **전역 지식**을 요구하므로
--   문서 단위 조기 필터를 못 쓴다. 실측: profile 42ms → **177ms**, index 148ms → **165ms**.
--   프로파일은 온디맨드 1회 로드이고 인덱스는 세션당 1회라 수용 가능한 대가로 판단했다
--   (정확한 정체성 > 130ms). 더 빠르게 하려면 별칭 맵을 물리 테이블로 굳혀야 하는데,
--   그러면 갱신 트리거·표류 관리 비용이 생긴다 — 지금 규모에선 이르다.
--
-- ─ 안전 계약(037 과 동일·불변) ─────────────────────────────────────────────
--   · security definer + `set search_path = public` · 공개 게이트는 명시 술어
--     (`scope_status = 'ok'`, `public_*` 는 `finding_text_ko <> '' or finding_language = 'KO'`)
--   · 483 한정 · 코호트 게이트 **≥5문서를 RPC 안에서** 강제(딥링크 우회 차단)
--   · 카운트·서지 메타만 반환(원문·URL 무반환)
--
-- 전제: 037 적용.
-- ============================================================================

-- 정체성 단일 정본: (문서, 해소된 키, 원표기).
create or replace function public.findings_inspector_pairs()
returns table (raw_signal_id text, inspector_key text, raw_name text)
language sql
stable
security definer
set search_path = public
as $$
  with raw_pairs as (
    select distinct
      f.raw_signal_id                     as rid,
      x.nm                                as nm,
      public.findings_inspector_key(x.nm) as k0
    from public.findings f
    cross join lateral jsonb_array_elements_text(f.inspector_names) x(nm)
    where f.source = 'FDA 483'
      and f.scope_status = 'ok'
      and f.inspector_names <> '[]'::jsonb
  ),
  parts as (
    select k0,
      split_part(k0, ' ', 1)                                                as first_tok,
      (string_to_array(k0, ' '))[array_length(string_to_array(k0, ' '), 1)] as last_tok,
      array_length(string_to_array(k0, ' '), 1)                             as ntok
    from (select distinct k0 from raw_pairs where k0 <> '') s
  ),
  alias as (
    -- 2토큰 → 같은 이름+성의 3토큰 이상 키가 **정확히 1개**일 때만 흡수.
    -- 2개 이상이면 동명이인 신호이므로 병합하지 않는다(적게 세는 쪽이 안전).
    select s.k0 as from_key,
           (select l.k0 from parts l
             where l.ntok >= 3 and l.first_tok = s.first_tok and l.last_tok = s.last_tok) as to_key
    from parts s
    where s.ntok = 2
      and (select count(*) from parts l
            where l.ntok >= 3 and l.first_tok = s.first_tok and l.last_tok = s.last_tok) = 1
  )
  select rp.rid, coalesce(a.to_key, rp.k0), rp.nm
  from raw_pairs rp
  left join alias a on a.from_key = rp.k0
  where rp.k0 <> '';
$$;


create or replace function public.findings_inspector_index()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with pairs as (select * from public.findings_inspector_pairs()),
  docs as (
    select inspector_key as k, count(distinct raw_signal_id)::int as documents
    from pairs group by inspector_key
  ),
  names as (
    -- 표기 변형 중 최빈값. 동률은 긴 표기 → 사전순으로 결정론 고정.
    -- 병합 후에는 짧은 표기와 긴 표기가 같은 그룹에 있으므로, 동률이면 길이 우선이
    -- **더 온전한 이름**을 고른다.
    select inspector_key as k, raw_name as nm,
           row_number() over (
             partition by inspector_key
             order by count(*) desc, length(raw_name) desc, raw_name asc
           ) as rn
    from pairs group by inspector_key, raw_name
  )
  select coalesce((
    select jsonb_agg(
      jsonb_build_object('inspector_key', d.k, 'display_name', n.nm, 'documents', d.documents)
      order by d.k
    )
    from docs d join names n on n.k = d.k and n.rn = 1
    where d.documents >= 5          -- ★코호트 게이트(profile 과 동일 임계값)
  ), '[]'::jsonb);
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
            'public_obs_cnt', public_obs_cnt
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
            )::int as public_obs_cnt
          from rows_out
          group by raw_signal_id
          order by max(published_date) desc, raw_signal_id asc
          limit 100
        ) t
      ), '[]'::jsonb)
    )
  end;
$$;

grant execute on function public.findings_inspector_pairs() to anon, authenticated;
grant execute on function public.findings_inspector_index() to anon, authenticated;
grant execute on function public.findings_inspector_profile(text) to anon, authenticated;
