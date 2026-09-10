-- ============================================================================
-- 081_findings_wl_bimo_scope.sql — WL scope: **BIMO 기기 임상(IDE) 서한**을 non_pharma 로
--
-- ※ 이 파일이 051 의 `grm_classify_wl_scope` 를 create or replace 로 **supersede** 한다
--   (010→020→023→024→033→051 관례와 동형). 시그니처(3-인자) 불변 → 033(C) 트리거 재배선 없음.
--   051 의 ⓪(기기 QSR)·①(제약 허용)·②(비제약)·③(기본) 은 **한 글자도 바꾸지 않는다**.
--
-- ★배경(2026-09-10 실측): FDA BIMO(Bioresearch Monitoring) 가 **기기 임상시험자·IRB** 에게 보낸
--   경고서한이 제약 범위(scope_status='ok')로 공개돼 있었다 —
--     · Stephen J. Fallon, Ph.D.(HIV 자가검사 기기 연구, 21 CFR 812.110·50.25·50.27) findings 4건
--     · Massachusetts Institute of Technology(IRB, Part 56 + Part 812 IDE) findings 2건
--   둘 다 내용은 informed consent·IRB 기록 등 **임상시험 수행**이지 의약품 GMP 가 아니다.
--   §1.1 범위는 "의약품 전반(의료기기 제외)"이고 033 도 IRB·임상시험자를 배제 대상으로 적었다.
--
-- ★왜 033/051 이 못 잡았나 — **발신 기관명**이다. 기기 임상 중 HIV 진단·혈액 관련 기기는
--   CBER 소관이라 서한 서두·서명·주소에 "Center for **Biolog**ics Evaluation and Research" 가
--   세 번 찍힌다. ① 의 `\ybiolog` 토큰이 이 기관명에 걸려 'ok' 가 먼저 확정되고, ② 의
--   `clinical investigat`·`informed consent`·`IRB` 는 **도달조차 못 한다**(051 과 같은 순서 문제).
--   051 ⓪ 도 못 잡는다 — 임상시험자 서한은 QSR(21 CFR 820)·201(h) 를 인용하지 않기 때문.
--
-- ★설계 = 051 ⓪ 바로 뒤, ① 앞에 **좁은** 규칙 ⓪′ 하나를 더 넣는다.
--   ⓪′ 가 성립하려면 **둘 다** 필요하다:
--     (a) 명백한 기기 임상 근거 — `21 CFR 812`(Investigational Device Exemptions) 인용 또는
--         `investigational device` 문구. BIMO 라는 단어나 임상 어휘만으로는 부족하다(약물 임상도 BIMO 다).
--     (b) **약물 임상·의약품 특정** 근거가 없을 것 — `21 CFR 312`(IND)·investigational (new) drug·
--         21 CFR 210/211·drug product/substance·API·NDC. 약물 임상시험자 서한(312)은 종전대로
--         ① 의 `investigational drug` 로 'ok' 에 남는다(033 이 명시한 정책).
--   ★(b) 에 `biolog` 를 **넣지 않는다** — 그 토큰이 바로 새는 구멍이다. 생물의약품 임상은 IND(312)를
--     인용하므로 (b) 의 312 로 보호된다.
--   ★판정 축은 **본문(p_doc_text)만** 쓴다. 상호(p_firm)는 넣지 않는다(051 과 동일 원칙).
--
-- ★불변식(033·051 과 동일): 삭제가 아니라 **플래그**(scope_status) — 되돌릴 수 있다.
--   483 분류(024)·미승인의약품 정책(033 ①)·기기 QSR 규칙(051 ⓪)은 **전혀 건드리지 않는다.**
--
-- 실측 dry-run(적용 전, 읽기전용 — 새 분기를 SELECT 로 인라인해 전 WL 행 재판정):
--   바뀌는 행 = MIT 2건(ok→non_pharma) 뿐. Fallon 4건은 같은 날 수동으로 non_pharma 처리돼 있어
--   판정 일치(변화 없음). 812 를 인용하는 WL 30 문서 중 나머지 28 은 이미 non_pharma(051 기기)이고,
--   1 건(David M. Lubeck, M.D. 안과 임상)은 약물 신호가 있어 'ok' 유지 — 과잉차단 0.
-- ============================================================================

create or replace function public.grm_classify_wl_scope(
  p_len integer,
  p_doc_text text,
  p_firm text
)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    -- ⓪ [051] 명백한 기기 + 의약품 특정 근거 전무 → non_pharma. ① 보다 **먼저** 본다.
    when coalesce(p_doc_text, '') ~*
           '(21 CFR ?820|\y820\.[0-9]|section 201\(h\)|21 U\.?S\.?C\.? ?§? ?321\(h\))'
     and coalesce(p_doc_text, '') !~*
           '(21 CFR ?21[01]\.|drug product|drug substance|active pharmaceutical|\yAPI\y|section 503\(b\)|section 503A|compounding pharmac|\yNDC\y|new animal drug)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'non_pharma' end
    -- ⓪′ [081] BIMO 기기 임상(21 CFR 812 / investigational device) + 약물 임상·의약품 특정 근거
    --    전무 → non_pharma. ① 보다 **먼저** 본다 — CBER 기관명의 "Biologics" 가 ① 에 걸리기 전에.
    when coalesce(p_doc_text, '') ~*
           '(21 CFR ?812\.|\y812\.[0-9]|investigational device)'
     and coalesce(p_doc_text, '') !~*
           '(21 CFR ?312\.|\y312\.[0-9]|investigational (new )?drug|21 CFR ?21[01]\.|drug product|drug substance|active pharmaceutical|\yAPI\y|\yNDC\y)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'non_pharma' end
    -- ① 제약/의약품/생물의약품/미승인drug 신호가 하나라도 → ok (비대칭 안전 — 483 ③과 동형).
    --    OTC·미승인 새 의약품(505/355)·생물의약품·임상 investigational drug 포함.
    when (coalesce(p_doc_text, '') || ' ' || coalesce(p_firm, '')) ~*
         '(drug product|drug substance|active pharmaceutical|\yAPI\y|21 CFR 21[0-2]|21[0-2]\.[0-9]|compound|\ysterile|aseptic|\yUSP\y|\yOTC\y|monograph|pharmaceutic|\yNDC\y|injectable|\ytablet|\ycapsule|homeopath|hand sanitizer|antiseptic|sunscreen|drug facts|\ybiolog|vaccine|\yplasma|heparin|active ingredient|dietary supplement|section 505|\y505\(|\y355\(|21 U\.?S\.?C\.? ?355|\ynew drug\y|unapproved.{0,4}drug|investigational drug)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    -- ② 제약 신호 전무 + 기기(21 CFR 820)/식품/화장품/IRB/임상시험자 신호만 → non_pharma
    --    (483 ②④ 비제약 버킷과 동형).
    when (coalesce(p_doc_text, '') || ' ' || coalesce(p_firm, '')) ~*
         '(21 CFR 820|\y820\.[0-9]|medical device|device master record|device history record|\yMDR\y|design history file|premarket|510\(k\)|\ydevice\y|cosmetic|shampoo|\ylotion|makeup|mascara|\yfood\y|dairy|\yjuice|seafood|\ycheese|pet food|animal food|tobacco|\yvape|clinical investigat|informed consent|\yIRB\y)'
      then 'non_pharma'
    -- ③ 신호 없음 → 안전측 기본 'ok'(30자 미만 추출 단편만 fragment). 483 ⑤와 동형.
    when coalesce(p_len, 0) < 30 then 'fragment'
    else 'ok'
  end;
$$;

comment on function public.grm_classify_wl_scope(integer, text, text) is
  'WL scope 분류(033 → 051 → 081). ⓪ 명백한 기기(21 CFR 820·201(h)) ∧ 의약품 특정 근거 전무 → '
  'non_pharma. ⓪′ BIMO 기기 임상(21 CFR 812·investigational device) ∧ 약물 임상(312)·의약품 근거 '
  '전무 → non_pharma(CBER 기관명 "Biologics" 가 ① 에 걸리기 전에). ① 제약 신호 → ok(미승인drug·OTC '
  '정책 유지). ② 비제약 → non_pharma. ③ 기본 ok.';

-- ============================================================================
-- (B) 소급 백필 — 저장된 WL 전 행을 새 규칙으로 재분류(scope_status 만, 삭제 아님).
-- 033(B)·051(B) 와 **동일 패턴**: 문서 본문은 트리거와 같은 출처(wl_body_full → excerpt)를 쓰고,
-- CTE 로 판정을 먼저 계산해 **바뀌는 행만** UPDATE 한다. 트리거는 033(C) 가 이미 이 함수를
-- 호출하므로 재배선하지 않는다(시그니처 불변).
-- ============================================================================
with wl_doc as (
  select distinct f.raw_signal_id,
    coalesce(nullif(rs.raw_json::jsonb ->> 'wl_body_full', ''),
             rs.raw_json::jsonb ->> 'wl_body_excerpt', '') as body
  from public.findings f
  join public.raw_signals rs on rs.raw_signal_id = f.raw_signal_id
  where f.source = 'FDA Warning Letter'
),
reclass as (
  select f.finding_id,
    public.grm_classify_wl_scope(
      length(f.finding_text),
      coalesce(nullif(d.body, ''), f.finding_text),
      coalesce(f.firm_name, '')
    ) as new_scope
  from public.findings f
  join wl_doc d on d.raw_signal_id = f.raw_signal_id
  where f.source = 'FDA Warning Letter'
)
update public.findings f
set scope_status = r.new_scope
from reclass r
where r.finding_id = f.finding_id
  and f.scope_status is distinct from r.new_scope;

-- 검증(사람 실행용, 프로덕션 SQL Editor):
-- 1) BIMO 기기 임상 서한이 비공개로 바뀌었는가(전부 non_pharma 여야 한다):
--    select firm_name, scope_status, count(*) from public.findings
--    where source='FDA Warning Letter' and firm_name in
--      ('Stephen J. Fallon, Ph.D.','Massachusetts Institute of Technology MIT')
--    group by 1,2 order by 1;
-- 2) ★과잉차단 회귀 감시 — **약물 임상시험자 서한(312)은 'ok' 로 남아야 한다**:
--    select firm_name, scope_status, count(*) from public.findings
--    where source='FDA Warning Letter' and firm_name like '%Lubeck%' group by 1,2;
-- 3) ★미승인drug 정책 불변(033 ①) — 'ok' 여야 한다:
--    select firm_name, scope_status from public.findings
--    where source='FDA Warning Letter' and firm_name in
--      ('VitaCig, Inc.','Swisschems','Xcel Research LLC') group by 1,2;
-- 4) 483 분류 불변(WL 만 바뀐다):
--    select scope_status, count(*) from public.findings where source='FDA 483' group by 1;
-- 5) 공개 게이트: set role anon; select count(*) from public.findings where scope_status<>'ok'; -- 0
