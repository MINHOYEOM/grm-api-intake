-- 080 — 자가점검 체크리스트의 사례 발췌를 **조항 위치에서** 뜬다 (2026-09-06)
--
-- ## 무엇이 잘못됐나 (실측)
--
-- /findings/checklist/ 는 "조항마다 실제 지적 문장"을 약속하고 인쇄해서 쓰는 점검표다.
-- 그런데 043 은 사례로 뽑은 finding 의 **앞머리**를 내려주고 화면은 그 앞 240자를 찍었다.
-- 조항 매칭은 `cfr_refs`(그 finding 이 인용한 조항 전부) 로 하는데, 발췌는 그 조항이 어디서
-- 인용됐는지와 **무관하게 늘 문서 앞머리**였다. 두 갈래로 약속이 깨졌다.
--
--   (1) **비지적 문장이 사례로 실린다.** WL 파서는 번호/헤딩 앵커를 못 찾으면 편지 전체를
--       finding 1건으로 방출한다(`findings_extractors._from_warning_letter` 의 degrade).
--       그 통짜 행은 `cfr_refs` 가 편지 전체의 인용 목록이라 여러 조항에 동시에 걸리고,
--       앞머리는 위반이 아니라 **편지 서두**다.
--       실측: PReye, LLC(finding-958c…, 본문 9,267자·cfr_refs 19개)가 211.22·211.42 두 곳에
--       사례로 붙었고 화면 문장은 "귀사의 의약품 제조시설인 PReye, LLC(FEI 3031057987…)에
--       대하여 2026년 3월 17일부터 19일까지 실사를 실시하였다…" — 지적이 아니라 서두다.
--
--   (2) **다른 조항의 문장이 실린다.** 정상 분해된 위반 블록도 본문 안에서 다른 조항을
--       함께 인용한다. 실측: Jabil Inc.(finding-ef37…) 의 211.22(d) 블록은 본문 중간에
--       211.188·211.42(c)(10)(v)·211.100(a) 를 인용한다(국문 기준 위치 52 / 415 / 587 / 712).
--       앞 240자에는 211.22(d) 밖에 안 들어가므로, 211.100·211.188·211.42 세 조항의 사례로
--       **같은 211.22(d) 문장**이 반복해서 붙었다.
--
-- ★기존 `anchored` 플래그는 이 결함을 **하나도 잡지 못했다.** 그 플래그는 조항 번호가 본문
--   **어딘가에** 있는지만 봤는데, 화면에 보이는 건 앞 240자다. 상위 15개 조항·조항당 2건
--   = 30건을 실측하니 8건이 "보이는 문장에 그 조항이 없음"인데 `anchored` 는 30건 전부
--   true 였다. **판정 대상과 표시 대상이 어긋난 계기**다.
--
-- ## 어떻게 고치나
--
--   · 발췌를 **그 조항이 인용된 문장에서 시작**한다(`findings_clause_excerpt`). 앞으로
--     최대 300자를 되짚어 문장 경계를 찾고, 거기서 560자를 뜬다. 조항 번호는 발췌 시작
--     기준 300자 안에 있으므로 **발췌 안에 반드시 들어간다**(구조적 보장).
--     ↳ (1) 도 이걸로 낫는다: 통짜 행이라도 발췌가 서두가 아니라 그 조항의 위반 서술에서
--       시작한다. 편지 서두는 특정 조항 번호를 인용하지 않으므로(인용하는 건 "parts 210
--       and 211" 같은 부 단위다) 조항 위치 발췌가 서두를 고를 수 없다.
--   · 조항 번호가 본문에 **없는** 행은 사례에서 **뺀다.** 그런 행은 발췌를 앞머리에서 뜰
--     수밖에 없고, 그 문장은 그 조항의 지적이라고 화면이 증명할 수 없다 — 약속과 다른 것을
--     싣느니 없다고 말한다(이 저장소의 부재 어휘 관례). 실측상 이 행들은 대부분 480자
--     절단 유산본(옛 표시 상한)이라 조항 인용이 잘려 나간 것이다.
--     ↳ 모집단 실측(공개 게이트 통과분): 조항×finding 후보 2,056쌍 중 1,656쌍(80.5%)이
--       국문·영문 **양쪽 모두** 앵커됨, 400쌍이 양쪽 모두 미앵커. 한쪽만 앵커된 쌍은 **0**
--       이었다(번역이 인용을 그대로 옮긴다) — 그래서 언어별로 갈라지는 사례가 없다.
--     ↳ 042 순위 상위 20개 조항은 전부 앵커 사례가 5건 이상 남는다(체크리스트 UI 의 최대
--       설정이 조항 20개·조항당 5건이다). 앵커 사례가 0인 조항은 순위 34위 211.150(후보
--       2건)·36위 211.208(후보 1건) 둘뿐이고, 이 둘은 `?section=` 로만 닿는다 — 화면은
--       "사례 없음"을 그대로 말한다. 이 하한은 `verify_checklist_examples.py` 가 전수로
--       지킨다(0이 되는 것도 결함이다).
--
-- ★조항 경계: `position()` 대신 `regexp_instr(txt, '211\.22(?![0-9])')` 를 쓴다. 단순
--   부분일치면 `211.22` 질의가 `211.226` 본문을 앵커로 오인한다(043 이 `cfr_refs` 매칭에서
--   이미 막은 것과 같은 함정을 본문 매칭에서 다시 밟지 않는다).
-- ★공백 정규화·길이 상한을 **여기서** 끝낸다. 종전에는 checklist.js 가 다시 240자로 잘라
--   조항이 화면 밖으로 밀려났다 — "무엇이 보이는가"의 정본은 한 곳이어야 한다.
-- ★반환 필드의 `finding_text`/`finding_text_ko` 는 이제 **전문이 아니라 발췌**다. 옛 키를
--   지우지 않고 같은 값을 함께 내는 이유는 배포 순서에 있다(함수 본문 주석 참조) —
--   SQL 과 정적 JS 는 서로 다른 시각에 라이브가 되므로 키 변경은 가산만 한다.
-- ★security invoker 유지 — 043 헤더의 이유 그대로다(본문을 내보내므로 RLS 가 게이트다).
-- ★성능은 오히려 좋아졌다(실측, 조항 15개·사례 2건): 043 1,645ms → 080 601ms.
--   601ms 중 582ms 는 `cfr_refs` 조인 자체(조항 15 × findings 26,664 = 40만 쌍 평가)로
--   043 과 공유하는 바닥값이다. 나머지 층에서 세 가지를 바로잡았다 — 발췌를 후보 전체가
--   아니라 내보낼 행에서만 뜬 것, 조항별 묶음을 상관 서브질의 대신 group by 로 만든 것,
--   헬퍼의 CTE 에 `materialized` 를 건 것(각 함수 본문 주석 참조).
--
-- 전제: 002 + 006 + 010(RLS·scope_status) + 013(firm_key) + 042(조항 순위 정본) + 043.
-- ============================================================================


-- ── 발췌 헬퍼 ────────────────────────────────────────────────────────────────
-- 본문에서 조항이 인용된 자리를 찾아 그 문장부터 발췌한다. 순수 함수라 단독으로 검증할 수
-- 있다(가드 스크립트가 이 함수를 직접 호출해 경계 조건을 확인한다).
--
--   반환 jsonb: { "anchored": bool, "text": text }
--     anchored=false 면 조항이 본문에 없다는 뜻이고, text 는 앞머리 발췌다(호출부가 사례로
--     쓰지 않는다 — 아래 findings_checklist 가 그런 행을 거른다).
--
-- ★되짚기 상한(300)이 발췌 길이(560)보다 작아야 조항이 발췌 안에 들어간다. 이 부등식이
--   이 함수의 계약이다 — 값을 바꿀 땐 둘의 관계를 먼저 확인하라.
-- ★300 은 실측으로 고른 값이다. 되짚기가 짧으면 문장 경계를 못 찾아 발췌가 **문장 중간**
--   에서 시작한다 — FDA 경고서한의 영문 위반 문장은 길어서 조항 인용이 문장 끝에 온다.
--   상위 20조항×5건(=100건) 실측 영문 기준 문장 중간 시작: 되짚기 200 → 45건 / 300 → 11건
--   / 400 → 2건. 대신 발췌가 길어진다(평균 356 → 477 → 540자). 인쇄 점검표라 길이도 비용
--   이므로 300/560 을 택했다(국문은 평균 285 → 355자).
create or replace function public.findings_clause_excerpt(
  p_text text,
  p_section text,
  p_max integer default 560,
  p_lead integer default 300
) returns jsonb
language sql
immutable
parallel safe
set search_path = public
as $$
-- ★모든 CTE 에 `materialized` 를 붙인 것은 취향이 아니라 **30배 차이**다. 붙이지 않으면
--   플래너가 CTE 를 서브질의로 평탄화하면서 `txt`(= 본문 전체 공백 정규화)를 참조 지점마다
--   다시 계산한다 — 한 호출에 10회 넘게. 실측: 실제 본문 30건에 대한 60회 호출이
--   906ms → 29.6ms. 울타리가 곧 "한 번만 계산한다"는 계약이다.
with n as materialized (
  select
    -- 개행·연속 공백은 한 칸으로 — 발췌는 한 문단으로 읽히는 것이 목적이다.
    regexp_replace(coalesce(p_text, ''), '\s+', ' ', 'g')                as txt,
    -- 점은 정규식 메타문자라 이스케이프한다. 뒤에 숫자가 오면 다른 조항이다(211.226).
    replace(coalesce(p_section, ''), '.', '\.') || '(?![0-9])'           as pat,
    least(greatest(coalesce(p_max, 560), 120), 1200)                     as maxlen
  from (select 1) _
),
-- ★되짚기 상한은 **발췌 길이에 묶인다**(`maxlen - 40`). 두 값을 각자 clamp 만 하면
--   기본값끼리는 부등식이 맞아도 호출자가 p_max=120·p_lead=400 을 주는 순간 조항이 발췌
--   밖으로 밀려난다 — 보장이 "기본값에서만 참"이 되는 것이다. 값이 아니라 관계를 강제한다.
n2 as materialized (
  select n.*, least(greatest(coalesce(p_lead, 300), 0), 400, maxlen - 40) as leadmax
  from n
),
a as materialized (
  select txt, maxlen, leadmax,
    case when txt = '' or coalesce(p_section, '') = '' then 0
         else regexp_instr(txt, pat) end as pos
  from n2
),
l as materialized (
  -- 앵커 앞 되짚기 구간(최대 leadmax 자). pos=0 이면 빈 문자열이 되도록 길이를 0 으로 막는다
  -- (substr 은 음수 길이를 오류로 뱉는다).
  select a.*, greatest(pos - leadmax, 1) as ls,
    substr(txt, greatest(pos - leadmax, 1), greatest(pos - greatest(pos - leadmax, 1), 0)) as lead
  from a
),
c as materialized (
  -- 되짚기 구간의 **마지막** 문장 경계 뒤로 자른다(탐욕 `.*` 가 마지막 경계까지 먹는다).
  select l.*, regexp_replace(lead, '^.*[.!?] ', '') as sent from l
),
w as materialized (
  select c.*,
    -- 문장 경계를 못 찾았고 앞이 실제로 잘렸다면 낱말 경계로 스냅한다 — 그러지 않으면
    -- "…사는 완제의약품의"처럼 단어 중간에서 시작한다(실측 211.67 사례).
    case when sent = lead and ls > 1 then regexp_replace(lead, '^\S* ', '') else sent end as lead_kept
  from c
),
s as materialized (
  select w.*, case when pos = 0 then 1 else pos - length(lead_kept) end as st from w
)
select jsonb_build_object(
  'anchored', pos > 0,
  'text',
    case when txt = '' then ''
    else (case when st > 1 then '…' else '' end)
      || substr(txt, st, maxlen)
      || (case when length(txt) > st - 1 + maxlen then '…' else '' end)
    end
)
from s;
$$;

comment on function public.findings_clause_excerpt(text, text, integer, integer) is
  '[FIND-1] 본문에서 21 CFR 조항이 인용된 문장부터 발췌한다. 조항이 없으면 anchored=false + '
  '앞머리 발췌. 되짚기 상한(기본 300) < 발췌 길이(기본 560) 가 계약 — 조항이 발췌 안에 '
  '들어간다는 보장이 거기서 나온다.';

grant execute on function public.findings_clause_excerpt(text, text, integer, integer)
  to anon, authenticated;


-- ── 체크리스트 사례 (043 대체) ──────────────────────────────────────────────
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
-- ★질의 순서가 곧 비용이다. 조항 앵커 정규식을 **where** 에 두면 플래너가 그것을 조인
--   필터와 함께 평가해 (조항 15 × findings 26,664) = 40만 쌍마다 최대 10KB 본문에 정규식을
--   돌린다(실측 3.8초). 정규식을 **select 목록**에 두고 CTE 를 `materialized` 로 울타리치면
--   cfr_refs 조인을 통과한 쌍(실측 1,898)에만 돈다.
matched as materialized (
  select
    sec.section,
    f.finding_id,
    f.firm_key,
    f.published_date,
    -- 있는 언어는 전부 앵커돼 있을 것 — 없는 언어를 요구하지는 않는다(국문만 있는 MFDS
    -- 계열이 조항을 인용하게 되면 영문 부재로 통째 사라지는 일을 막는다).
    coalesce(f.finding_text_ko, '') <> '' as has_ko,
    coalesce(f.finding_text, '')    <> '' as has_en,
    coalesce(f.finding_text_ko, '') ~ (replace(sec.section, '.', '\.') || '(?![0-9])') as anch_ko,
    coalesce(f.finding_text, '')    ~ (replace(sec.section, '.', '\.') || '(?![0-9])') as anch_en
  from sec
  join public.findings f
    -- ★조항 매칭: cfr_refs 원소가 정확히 `21 CFR <섹션>` 이거나 `21 CFR <섹션>(` 로 시작할
    --   때만 매치한다. 접두 매치(`like '21 CFR 211.2%'`)로 하면 `211.22` 질의가 `211.25`·
    --   `211.28` 까지 삼킨다 — 하위 항 괄호까지만 허용해 `211.22`/`211.22(a)`/`211.22(d)` 를
    --   잡고 `211.220`(가상) 류를 배제한다. 042 의 "하위 항목은 조항 뿌리로 통합" 규칙과
    --   정확히 같은 대응이다.
    on exists (
         select 1
         from jsonb_array_elements_text(f.cfr_refs) as cr(ref_txt)
         where cr.ref_txt = '21 CFR ' || sec.section
            or cr.ref_txt like '21 CFR ' || sec.section || '(%'
       )
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
        -- ★옛 키 이름으로도 같은 값을 낸다. SQL 은 마이그레이션으로, 화면 JS 는 정적
        --   사이트 배포로 올라가 **서로 다른 시각에 라이브가 된다** — 이 저장소가 이미
        --   한 번 데인 자리다(RPC 계약 변경 = 프로덕션 장애 창). 키를 갈아치우면 둘 중
        --   어느 순서로 올려도 그 사이에 화면이 빈다(실측: 새 RPC + 옛 JS = 사례 문장이
        --   통째로 비어 업체·날짜만 남았다). 그래서 이 함수의 반환 키 변경은 **가산만**
        --   한다. 옛 JS 는 이 값을 받아 240자로 다시 자르는데, 조항 번호는 발췌 시작
        --   기준 200자 안에 있으므로 그렇게 잘라도 여전히 보인다.
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
  '본문에 없는 행은 사례에서 제외된다(080). security invoker 라 공개 게이트는 RLS(010)가 '
  '강제한다. 조항 순위·필터의 정본은 042 이며 이 함수는 그 판단을 복제하지 않는다.';

grant execute on function public.findings_checklist(text[], integer) to anon, authenticated;


-- ============================================================================
-- 검증 (사람 실행용) — 전수 검사는 `python verify_checklist_examples.py`
-- ============================================================================
-- ★게이트 검증은 SQL Editor 가 아니라 **anon 키 PostgREST** 로 한다(026 헤더와 동일 이유 —
--   SQL Editor 는 service_role 이라 RLS 가 적용되지 않는다).
--
-- 1) 발췌 안에 조항이 실제로 들어 있는가 (하나라도 0 이면 실패)
--    with r as (select public.findings_checklist(array['211.22','211.100','211.188'], 3) j)
--    select e->>'excerpt_ko' ~ '211\.(22|100|188)'
--    from r, jsonb_array_elements(r.j->'sections') s, jsonb_array_elements(s->'examples') e;
--
-- 2) 되짚기 상한 < 발췌 길이 계약 — 조항이 발췌 앞부분에 오는가
--    select public.findings_clause_excerpt(repeat('가 ', 500) || '(21 CFR 211.22).', '211.22');
--
-- 3) 이웃 조항을 앵커로 삼지 않는가 (211.226 본문이 211.22 앵커가 되면 실패)
--    select public.findings_clause_excerpt('앞 문장. 뒤 문장(21 CFR 211.226).', '211.22');
--    -- anchored=false 여야 한다
