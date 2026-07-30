-- ============================================================================
-- 037_findings_inspector_profile.sql — [FIND-483-SIGNER 2단계] 실사관 프로파일 서빙
--
-- ★★008 의 범위 주석을 **명시적으로 개정한다**.
--   008_findings_category_matrix.sql 상단에는 이렇게 적혀 있다:
--     "★scope: 조사관(inspector)별 집계는 데이터 부재로 이번 범위가 아니다 —
--      어떤 형태로도 넣지 않는다."
--   그 금지의 사유는 **데이터 부재**였고, 그 전제가 2026-07-30 소급 백필로 해소됐다:
--   1,546 문서 중 1,102(71%)에서 실사관을 확보(findings 6,840행·표기 정규화 후 808명).
--   따라서 이 파일이 그 주석을 대체한다. 단 **무제한 개정이 아니다** — 아래 코호트
--   게이트가 붙은 형태로만 허용한다.
--
-- ─ 코호트 게이트(이 파일의 핵심 계약) ──────────────────────────────────────
--   실측 분포: 문서 1건 579명(62.8%) · 2건 154 · 3~4건 100 · **5건 이상 99명**(최다 24).
--   문서 1~2건으로는 "이 실사관의 지적 성향"을 말할 수 없다 — 표본이 아니라 일화다.
--   그래서 **문서 5건 이상인 실사관만** 프로파일을 갖는다. 미만은 `null` 을 돌려준다.
--   ★게이트를 UI 가 아니라 **RPC 안에** 둔 이유: UI 게이트는 딥링크로 우회된다.
--   서버가 거부해야 "1건짜리 빈 프로파일"이 원천적으로 존재하지 않는다.
--
-- ─ 하지 않는 것(의도적 범위 제한) ──────────────────────────────────────────
--   · 실사관 **순위·비교·엄격도 추론**을 하지 않는다. 반환값은 전부 공개 문서에서
--     기계적으로 센 수치이며, 해석은 싣지 않는다.
--   · 실사관 **디렉터리(목록 열람) 페이지를 만들지 않는다**. `findings_inspector_index`
--     는 "이 이름을 링크로 걸어도 되는가"를 클라이언트가 판정하기 위한 **키 목록**이며,
--     사람을 훑어보는 화면의 데이터소스가 아니다. 진입은 항상 **보고 있던 문서**에서
--     그 문서의 서명자를 눌러 들어오는 경로뿐이다.
--   · 원문 텍스트·URL 을 반환하지 않는다(007/008 안전 계약 유지) — 카운트와 서지
--     메타(카테고리 코드·연도·업체명·문서 id·발행일)만 나간다.
--
-- ─ 정체성 키 ───────────────────────────────────────────────────────────────
--   같은 사람이 문서마다 다르게 적힌다: `Eileen A. Liu` / `Eileen A Liu`.
--   정규화(소문자·마침표 제거·공백 정규화) 없이 세면 한 사람이 둘로 갈라진다
--   (실측: 표기 922 → 정규화 808, 114개 키가 변형 병합).
--   ★중간이름 유무가 다른 표기(`Matthew Casale` vs `Matthew B Casale`)는 **병합하지
--   않는다**. 동명이인을 한 사람으로 합치면 남의 실사 이력을 붙이는 것이라, 조금
--   적게 세는 쪽이 안전하다. 이 보수성은 의도이며 한계로 문서화한다.
--   `findings_inspector_key()` 가 그 정규화의 단일 정본이다 — 클라이언트(JS)도 링크
--   판정 시 **같은 규칙**을 써야 한다.
--
-- ─ 안전 계약 ───────────────────────────────────────────────────────────────
--   · security definer + `set search_path = public` (013 findings_firm_profile 과 동형).
--     공개 게이트는 RLS 가 아니라 **명시 술어**로 강제한다: `scope_status = 'ok'`,
--     그리고 `public_*` 카운트는 `finding_text_ko <> '' or finding_language = 'KO'`.
--   · 483 전용(`source = 'FDA 483'`) — inspector_names 를 채우는 소스가 이것뿐이다.
--   · 입력이 무엇이든 예외를 던지지 않는다(미존재/형식오류/null → 빈 결과 또는 null).
--
-- 전제: 036(inspector_names 투영) 적용 + 실사관 백필 완료.
-- ============================================================================

-- ── 정체성 키 정본 ──────────────────────────────────────────────────────────
-- 소문자 · 마침표 제거 · 공백 정규화. 클라이언트가 링크 판정에 쓰는 규칙과 반드시 일치.
create or replace function public.findings_inspector_key(p_name text)
returns text
language sql
immutable
set search_path = public
as $$
  select btrim(regexp_replace(lower(replace(coalesce(p_name, ''), '.', '')), '\s+', ' ', 'g'))
$$;


-- ── 코호트 인덱스 ───────────────────────────────────────────────────────────
-- 프로파일을 가질 자격이 있는 실사관의 키·표시명·문서수만 돌려준다.
-- 용도는 **링크 판정 하나** — 클라이언트가 문서 카드의 서명자 이름을 링크로 걸지
-- 말지 정하기 위해 세션당 1회 받아 캐시한다(문서 카드마다 RPC 를 때리지 않는다).
create or replace function public.findings_inspector_index()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with pairs as (
    select
      public.findings_inspector_key(x.nm) as k,
      x.nm                                as nm,
      f.raw_signal_id                     as rid
    from public.findings f
    cross join lateral jsonb_array_elements_text(f.inspector_names) x(nm)
    where f.source = 'FDA 483'
      and f.scope_status = 'ok'
      and f.inspector_names <> '[]'::jsonb
  ),
  docs as (
    select k, count(distinct rid)::int as documents
    from pairs
    where k <> ''
    group by k
  ),
  names as (
    -- 표기 변형 중 최빈값. 동률은 긴 표기 → 사전순으로 결정론 고정(동률에서 실행마다
    -- 다른 값이 나오면 링크 라벨이 흔들린다 — 이 저장소엔 타이브레이크 부재로 A/B 평가가
    -- 뒤집힌 전례가 있다).
    -- ★단일 패스: 종전엔 코호트 1명마다 pairs 를 다시 훑는 상관 서브쿼리라 95회 재스캔이
    --   일어나 283ms 였다(실측). 윈도우 함수로 한 번에 뽑아 148ms. 결과는 불변 —
    --   함수와 독립적으로 계산한 기대값과 바이트 동일(md5 c167f1e5…) 실측 확인.
    select k, nm,
           row_number() over (
             partition by k order by count(*) desc, length(nm) desc, nm asc
           ) as rn
    from pairs
    where k <> ''
    group by k, nm
  )
  select coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'inspector_key', d.k,
        'display_name',  n.nm,
        'documents',     d.documents
      )
      order by d.k
    )
    from docs d
    join names n on n.k = d.k and n.rn = 1
    where d.documents >= 5          -- ★코호트 게이트(profile 과 동일 임계값)
  ), '[]'::jsonb);
$$;


-- ── 실사관 프로파일 ─────────────────────────────────────────────────────────
-- 코호트 미달이면 `null` — 빈 프로파일 페이지가 존재하지 않게 서버가 막는다.
create or replace function public.findings_inspector_profile(p_inspector_key text)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with pairs as (
    select
      f.raw_signal_id, f.finding_id, f.published_date, f.source, f.category_code,
      f.firm_name, f.firm_key, f.finding_text_ko, f.finding_language, x.nm
    from public.findings f
    cross join lateral jsonb_array_elements_text(f.inspector_names) x(nm)
    where f.source = 'FDA 483'
      and f.scope_status = 'ok'
      and f.inspector_names <> '[]'::jsonb
      and public.findings_inspector_key(x.nm)
          = public.findings_inspector_key(coalesce(p_inspector_key, ''))
      and public.findings_inspector_key(coalesce(p_inspector_key, '')) <> ''
  )
  select case
    when (select count(distinct raw_signal_id) from pairs) < 5 then 'null'::jsonb
    else jsonb_build_object(
      'inspector_key', public.findings_inspector_key(coalesce(p_inspector_key, '')),
      'display_name', coalesce((
        select nm from pairs group by nm
        order by count(*) desc, length(nm) desc, nm asc limit 1
      ), ''),
      'totals', jsonb_build_object(
        'findings',        (select count(*) from pairs),
        'public_findings', (select count(*) from pairs
                            where finding_text_ko <> '' or finding_language = 'KO'),
        'documents',       (select count(distinct raw_signal_id) from pairs),
        'firms',           (select count(distinct firm_name) from pairs where firm_name <> ''),
        'first_seen',      (select min(published_date) from pairs),
        'last_seen',       (select max(published_date) from pairs)
      ),
      'by_category', coalesce((
        select jsonb_agg(jsonb_build_object('category_code', category_code, 'cnt', cnt)
                         order by cnt desc, category_code)
        from (select category_code, count(*)::int as cnt from pairs group by category_code) t
      ), '[]'::jsonb),
      'by_year', coalesce((
        select jsonb_agg(jsonb_build_object('year', year, 'cnt', cnt) order by year)
        from (select left(published_date, 4) as year, count(*)::int as cnt
              from pairs group by left(published_date, 4)) t
      ), '[]'::jsonb),
      'documents', coalesce((
        select jsonb_agg(
          jsonb_build_object(
            'raw_signal_id',  raw_signal_id,
            'published_date', published_date,
            'source',         source,
            'firm_name',      firm_name,
            'firm_key',       firm_key,
            'obs_cnt',        obs_cnt,
            'public_obs_cnt', public_obs_cnt
          )
          order by published_date desc, raw_signal_id asc
        )
        from (
          select
            raw_signal_id,
            max(published_date) as published_date,
            max(source)         as source,
            max(firm_name)      as firm_name,
            max(firm_key)       as firm_key,
            count(*)::int       as obs_cnt,
            count(*) filter (
              where finding_text_ko <> '' or finding_language = 'KO'
            )::int as public_obs_cnt
          from pairs
          group by raw_signal_id
          order by max(published_date) desc, raw_signal_id asc
          limit 100
        ) t
      ), '[]'::jsonb)
    )
  end;
$$;


-- ── 실행 권한 ───────────────────────────────────────────────────────────────
-- 013 관례: 집계 RPC 는 anon 이 직접 호출한다(정적 사이트라 서버가 없다).
-- `findings_inspector_key` 에는 **명시적 grant 를 두지 않는다** — 위 두 definer 함수가
-- 내부에서만 호출하기 때문이다. 다만 정직하게 적어둔다: 이 프로젝트의 기본 권한 설정상
-- public 스키마 함수는 anon/authenticated 에 EXECUTE 가 자동 부여되므로(실측:
-- `proacl` 에 `anon=X/postgres`), 이 헬퍼도 실제로는 anon 이 호출할 수 있다.
-- 위험은 없다 — 데이터에 접근하지 않는 순수 문자열 정규화 함수이고, 반환값은 입력에서
-- 유도된 문자열뿐이다. "내부 전용"은 **호출 설계상의 의도**이지 권한으로 강제된 경계가
-- 아니라는 뜻이며, 경계를 강제해야 할 이유가 생기면 `revoke execute … from public` 이
-- 필요하다(지금은 불필요).
grant execute on function public.findings_inspector_index() to anon, authenticated;
grant execute on function public.findings_inspector_profile(text) to anon, authenticated;
