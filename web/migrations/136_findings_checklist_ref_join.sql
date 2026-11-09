-- 136: 체크리스트 사례 RPC 의 조항 매칭을 "조항 × 전체 지적" 중첩 루프에서 "지적 한 번 훑기 +
--      해시 조인" 으로 바꾼다 (2026-11-09). 반환 shape·결과 동일 — 질의 모양만 바꾼다.
--
-- 계기: 11-08 체크리스트 검증(#1363) `findings_checklist: http_500`, Supabase 로그 57014
-- (statement timeout) 4건. anon 의 statement_timeout 은 3초다(pg_roles.rolconfig 실측).
--
-- ★원인(2026-11-09 EXPLAIN ANALYZE 실측, findings 28,491행):
--   080 의 매칭은 `sec join findings on exists(select … from jsonb_array_elements_text(cfr_refs)
--   where ref = '21 CFR '||sec.section or ref like …)` 이다. 등식이 exists 안에 숨어 있어
--   플래너가 해시 조인을 못 하고 **sec × findings 중첩 루프**로 간다 — 조항 20개면
--   SubPlan 569,820회(= 20 × 28,491). 비용이 "조항 수 × 지적 수"에 비례해 데이터가 늘수록
--   길어진다(080 작성 시 26,664행 → 지금 28,491행, 화면 상한은 조항 50개).
--     조항 1개 166ms · 5개 245ms · 20개 993ms (함수 전체) / 매칭 단계만 672ms
--   평시에도 3초 상한의 1/3 을 쓰고, 다른 질의와 CPU 가 겹치면 3초를 넘는다(로그 4건).
--
-- ★수리: 각 cfr_refs 원소에서 조항 키를 **한 번** 뽑아 sec 와 등식 조인한다.
--     k = substring(ref from '^21 CFR ([^(]*)')    -- '21 CFR ' 뒤, 첫 '(' 앞까지
--   매칭 단계 672ms → 142ms (조항 20개, 같은 2,074쌍). 비용이 조항 수와 무관해진다
--   (findings 를 한 번만 훑는다; jsonb_array_elements_text 는 Memoize 로 1,174회만 돈다).
--
-- ★동치 증명(080 의 "삼키지 않는다" 계약 유지). sec.section 은 `^21[01]\.[0-9]+$` 를 통과한
--   값이라 '(' · '%' · '_' 를 포함하지 않는다. 그러면 원소 ref 에 대해
--     080:  ref = '21 CFR S'  또는  ref like '21 CFR S(%'   ⇔  ref 가 '21 CFR S' 로 시작하고
--           나머지가 '' 이거나 '(' 로 시작한다
--     136:  k = S   (k 는 '21 CFR ' 뒤에서 '(' 직전까지의 최장 접두 — 나머지는 '' 이거나 '(' 시작)
--   두 조건은 같다. `211.22` 질의는 `211.22`·`211.22(a)` 만 잡고 `211.25`·`211.220`·
--   `211.22 (a)`(공백) 는 080 과 똑같이 배제된다. 한 지적이 같은 조항을 두 원소로
--   인용하면(`211.22` + `211.22(a)`) 조인이 두 줄을 내므로 `distinct` 로 080 의 exists(1회)와
--   맞춘다. 적용 전 실측: 조항 20개 매칭 쌍 2,074 = 2,074, 함수 출력 md5 일치(PR 본문).
--
-- 그 밖(업체 중복 제거·앵커 정규식·발췌·반환 키)은 080 과 **글자 그대로** 같다.
-- security invoker 유지 — 사례는 원문 문장이라 공개 게이트는 RLS(010)가 강제한다.
--
-- 권한: 기존 함수의 재정의(create or replace 는 ACL 을 보존)지만 의도를 파일에 고정한다 —
-- public 에서 걷고 080 과 같은 소비자(anon·authenticated)+service_role 에만 준다.
--
-- 롤백: 080_findings_checklist_excerpt.sql 의 `create or replace function public.findings_checklist`
-- 블록(164~311행)을 다시 실행한다(시그니처·반환형 동일이라 drop 불필요).

create or replace function public.findings_checklist(
  p_sections text[],
  p_examples integer default 2
) returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
with p as (
  -- 009 관례: 괄호로 감싼 뒤 슬라이스
  select
    (coalesce(p_sections, '{}'::text[]))[1:50]              as secs,
    least(greatest(coalesce(p_examples, 2), 1), 5)          as ex
),
-- 입력 정규화 — 조항 형식(21x.y)만 통과시킨다. 클라이언트를 신뢰하지 않는다.
sec as (
  select distinct s.section
  from p, unnest(p.secs) as s(section)
  where s.section ~ '^21[01]\.[0-9]+$'
),
-- ★조항 매칭(136): cfr_refs 원소가 정확히 `21 CFR <섹션>` 이거나 `21 CFR <섹션>(` 로 시작할
--   때만 매치한다. 접두 매치(`like '21 CFR 211.2%'`)로 하면 `211.22` 질의가 `211.25`·
--   `211.28` 까지 삼킨다 — 하위 항 괄호까지만 허용해 `211.22`/`211.22(a)`/`211.22(d)` 를
--   잡고 `211.220`(가상) 류를 배제한다. 042 의 "하위 항목은 조항 뿌리로 통합" 규칙과
--   정확히 같은 대응이다. 080 은 이것을 exists 상관 서브질의로 적어 sec × findings 중첩
--   루프가 됐다(조항 20개 = 56만 회). 키를 원소마다 한 번 뽑아 등식 조인한다(헤더 동치 증명).
hit as (
  select distinct sec.section, r.finding_id
  from (
    select f.finding_id,
           substring(cr.ref_txt from '^21 CFR ([^(]*)') as ref_key
    from public.findings f
    cross join lateral jsonb_array_elements_text(f.cfr_refs) as cr(ref_txt)
    where cr.ref_txt like '21 CFR %'
  ) r
  join sec on sec.section = r.ref_key
),
-- ★질의 순서가 곧 비용이다. 조항 앵커 정규식을 **where** 에 두면 플래너가 그것을 조인
--   필터와 함께 평가해 (조항 15 × findings 26,664) = 40만 쌍마다 최대 10KB 본문에 정규식을
--   돌린다(실측 3.8초). 정규식을 **select 목록**에 두고 CTE 를 `materialized` 로 울타리치면
--   cfr_refs 조인을 통과한 쌍(실측 1,898)에만 돈다.
matched as materialized (
  select
    h.section,
    f.finding_id,
    f.firm_key,
    f.published_date,
    -- 있는 언어는 전부 앵커돼 있을 것 — 없는 언어를 요구하지는 않는다(국문만 있는 MFDS
    -- 계열이 조항을 인용하게 되면 영문 부재로 통째 사라지는 일을 막는다).
    coalesce(f.finding_text_ko, '') <> '' as has_ko,
    coalesce(f.finding_text, '')    <> '' as has_en,
    coalesce(f.finding_text_ko, '') ~ (replace(h.section, '.', '\.') || '(?![0-9])') as anch_ko,
    coalesce(f.finding_text, '')    ~ (replace(h.section, '.', '\.') || '(?![0-9])') as anch_en
  from hit h
  join public.findings f on f.finding_id = h.finding_id
),
cand as (
  select
    m.section, m.finding_id, m.firm_key, m.published_date,
    -- ★업체 중복 제거: 같은 업체 문서에서 사례 2건이 나오면 "여러 곳에서 반복되는 지적"이라는
    --   체크리스트의 전제가 깨진다. firm_key(013) 단위로 먼저 1건씩만 남긴다.
    row_number() over (
      partition by m.section, m.firm_key
      order by m.published_date desc, m.finding_id
    ) as rn_firm
  from matched m
  -- 본문에 조항이 실제로 적힌 행만 사례가 된다. 화면이 증명할 수 없는 문장을
  -- "실제 지적 사례"라고 싣지 않는다(080 헤더 참조).
  where (not m.has_ko or m.anch_ko)
    and (not m.has_en or m.anch_en)
    and (m.anch_ko or m.anch_en)
),
picked as (
  select
    c.section, c.finding_id, c.published_date,
    row_number() over (
      partition by c.section
      order by c.published_date desc, c.finding_id
    ) as rn
  from cand c
  where c.rn_firm = 1
),
shown as (
  -- 본문은 실제로 내보낼 행(최대 조항 20 × 사례 5 = 100)에서만 다시 읽는다. 발췌는
  -- **조항이 인용된 자리**에서 뜨고, 언어별로 따로 계산한다.
  select
    k.section, k.rn,
    f.finding_id, f.firm_name, f.published_date, f.source,
    f.document_id, f.evidence_url, f.category_code,
    public.findings_clause_excerpt(f.finding_text_ko, k.section) as ex_ko,
    public.findings_clause_excerpt(f.finding_text,    k.section) as ex_en
  -- ★`from picked k, p join findings f on … k.finding_id` 는 파싱 오류다: JOIN 은 콤마
  --   목록의 **직전 항목(p)** 에만 붙어 k 가 그 스코프에 없다. 명시적 join 을 먼저 쓴다.
  from picked k
  join public.findings f on f.finding_id = k.finding_id
  cross join p
  where k.rn <= p.ex
),
agg as (
  -- ★조항별 묶음을 `from sec s` 상관 서브질의로 만들면 조항 수만큼 shown 을 다시 훑는다
  --   (실측 15조항 2.8초 → 한 번만 훑도록 group by 로 바꾸니 0.5초). 상관 서브질의는
  --   "한 번 더 훑는 비용"이 조항 수에 비례해 붙는다.
  select
    k.section,
    jsonb_agg(
      jsonb_build_object(
        'finding_id',      k.finding_id,
        'firm_name',       k.firm_name,
        'published_date',  k.published_date,
        'source',          k.source,
        'document_id',     k.document_id,
        'evidence_url',    k.evidence_url,
        'category_code',   k.category_code,
        -- 발췌 전문이 아니라 **그 조항 문장**이다. 앞이 잘렸으면 선두 '…' 가 붙어
        -- 있어 화면이 따로 표기하지 않아도 발췌임이 드러난다.
        'excerpt_ko',      k.ex_ko->>'text',
        'excerpt',         k.ex_en->>'text',
        -- ★옛 키 이름으로도 같은 값을 낸다(080 — RPC 계약 변경 = 프로덕션 장애 창).
        --   이 함수의 반환 키 변경은 **가산만** 한다. 136 은 키를 하나도 바꾸지 않는다.
        'finding_text_ko', k.ex_ko->>'text',
        'finding_text',    k.ex_en->>'text'
      ) order by k.rn
    ) as examples
  from shown k
  group by k.section
)
select jsonb_build_object(
  'sections', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'section',  s.section,
        -- 사례가 하나도 없으면 빈 배열이다 — 화면이 "사례 없음"을 그대로 말한다.
        'examples', coalesce(a.examples, '[]'::jsonb)
      ) order by s.section
    )
    from sec s
    left join agg a on a.section = s.section
  ), '[]'::jsonb)
);
$$;

comment on function public.findings_checklist(text[], integer) is
  '[FIND-1] 자가점검 체크리스트 — 042 가 고른 조항 목록을 받아 조항별 대표 지적 문장을 '
  '반환한다(업체 중복 제거·최신순). 발췌는 **그 조항이 인용된 문장**에서 시작하며, 조항이 '
  '본문에 없는 행은 사례에서 제외된다(080). 조항 매칭은 cfr_refs 원소의 조항 키 등식 조인 '
  '(136 — 비용이 조항 수와 무관). security invoker 라 공개 게이트는 RLS(010)가 강제한다. '
  '조항 순위·필터의 정본은 042 이며 이 함수는 그 판단을 복제하지 않는다.';

revoke all on function public.findings_checklist(text[], integer) from public, anon, authenticated;
grant execute on function public.findings_checklist(text[], integer) to anon, authenticated, service_role;


-- ============================================================================
-- 검증 (사람 실행용)
-- ============================================================================
-- 1) 적용 전후 출력이 같은가 — 적용 **전**에 080 출력 md5 를 떠 두고 적용 후 다시 뜬다.
--    select md5(public.findings_checklist(array['211.22','211.25','211.42','211.46','211.56',
--      '211.63','211.67','211.68','211.84','211.100','211.110','211.113','211.160','211.165',
--      '211.166','211.180','211.188','211.192','211.194','211.198'], 2)::text);
-- 2) 시간: 같은 호출을 explain (analyze, buffers) — "SubPlan" 이 사라지고 Hash Join 이어야 한다.
-- 3) 권한: select proacl from pg_proc where proname = 'findings_checklist';
--    → {postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,service_role=X/postgres}
--      (선두 `=X/` 즉 PUBLIC 이 없어야 한다)
-- 4) 게이트(RLS)는 anon 키 PostgREST 로 본다 — `python verify_checklist_examples.py`.
