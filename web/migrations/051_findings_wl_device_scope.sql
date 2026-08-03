-- ============================================================================
-- 051_findings_wl_device_scope.sql — WL scope: **명백한 의료기기 편지**를 non_pharma 로
--
-- ※ 이 파일이 033 의 `grm_classify_wl_scope` 를 create or replace 로 **supersede** 한다
--   (010→020→023→024, 033 관례와 동형). 시그니처(3-인자) 불변 → 033(B) 트리거 재배선 없음.
--
-- ★배경(2026-08-03 실측): 공개 중인 WL findings 에 **순수 의료기기 QSR 지적**이 94건 섞여
--   있었다 — Abiomed(심장펌프)·Globus Medical(척추 임플란트)·Beckman Coulter(진단)·
--   Zyno Medical(인퓨전펌프)·Sol-Millennium(주사기)·Robbins Instruments 등 39개 문서.
--   내용도 전부 21 CFR 820 QSR 이다(820.198 불만조사·820.120 라벨링·820.30(c) 설계입력·CAPA).
--   §1.1 범위는 "의약품 전반(**의료기기 제외**)"이고 033 도 이미 기기를 배제 대상으로 적었다.
--
-- ★왜 033 이 못 잡았나 — **규칙 순서**다. 033 의 ①(제약 신호 → ok)이 ②(기기 → non_pharma)
--   보다 먼저 평가되는데, ① 의 어휘가 넓어 기기 편지를 먼저 삼킨다:
--     · `\ysterile`   ← "sterile wound dressings"(CellEra) · "sterile syringes"(Sol-Millennium)
--     · `\ybiolog`    ← "Edge Biologicals"(체외진단 업체명)
--     · `pharmaceutic` ← **상호**만으로 매치("Aquavit Pharmaceuticals" — FDA 는 이 회사 제품을
--                        201(h) 로 **device 라고 명시 판정**했다)
--     · `compound`    ← 산문의 compound/compounding 언급
--   즉 ② 의 기기 토큰(`21 CFR 820`·`medical device`)은 **도달조차 못 했다**.
--
-- ★설계 = ① 앞에 **좁은** 규칙 ⓪ 하나를 넣는다(① 이하는 한 글자도 바꾸지 않는다).
--   ⓪ 가 성립하려면 **둘 다** 필요하다:
--     (a) 명백한 기기 근거 — `21 CFR 820`(기기 QSR) 또는 `section 201(h)`/`321(h)`
--         (FD&C Act 의 **device 정의 조항**. FDA 가 "이 제품은 기기다"라고 선언하는 자리다)
--     (b) **의약품 특정** 근거가 없을 것 — 여기서 "의약품 특정"은 ① 보다 훨씬 좁다.
--         21 CFR 210/211 · drug product/substance · API · 503(b)/503A/조제 · NDC · 동물용의약품.
--   ★(b) 에서 `current good manufacturing` 을 **의도적으로 제외**했다 — 21 CFR 820 이 곧
--     **기기 CGMP** 라 기기 편지도 그 표현을 쓴다(실측: Synovo·InfuTronix·Magnolia 는 그
--     단어 하나 때문에 보호받고 있었다). 반대로 21 CFR 211 을 실제 인용하는 복합 편지
--     (Dental Technologies·Amnio Technology)는 (b)에 걸려 그대로 'ok' 로 남는다.
--   ★판정 축은 **본문(p_doc_text)만** 쓴다. 상호(p_firm)는 넣지 않는다 — "Pharmaceuticals"
--     라는 이름의 기기 제조사가 실재하고(Aquavit), 반대로 상호로 기기를 단정할 수도 없다.
--
-- ★불변식(033 과 동일): 삭제가 아니라 **플래그**(scope_status) — 되돌릴 수 있다.
--   483 분류(024)·미승인의약품 정책(033 ①, CODEX 검수 rubric §4)은 **전혀 건드리지 않는다.**
--   HCT/P 는 이 파일의 대상이 아니다 — 024 가 "본문 신호로 배제하지 않는다(est_type 축만)"
--   고 근거와 함께 결정했고, WL 에는 est_type 이 없어 그 결정을 뒤집는 셈이 되기 때문이다.
--
-- 실측 dry-run(적용 전, 읽기전용): WL 1,299 문서 중 기기 근거 보유 44 → 그중 의약품 특정
--   근거 없음 **39 문서 / findings 94건**(전부 현재 scope_status='ok'·번역 완료). 표본 전수
--   육안 확인: FDA 가 본문에서 직접 "these products are devices"/"medical devices"/
--   "in vitro diagnostics" 라고 판정한 문서들이다.
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
  'WL scope 분류(033 → 051). ⓪ 명백한 기기(21 CFR 820·201(h)) ∧ 의약품 특정 근거 전무 → '
  'non_pharma. ① 제약 신호 → ok(미승인drug·OTC 정책 유지). ② 비제약 → non_pharma. ③ 기본 ok. '
  '⓪ 의 의약품 근거에는 current good manufacturing 을 넣지 않는다 — 21 CFR 820 이 곧 기기 CGMP.';

-- ============================================================================
-- (B) 소급 백필 — 저장된 WL 전 행을 새 규칙으로 재분류(scope_status 만, 삭제 아님).
-- 033(B) 와 **동일 패턴**: 문서 본문은 트리거와 같은 출처(wl_body_full → excerpt)를 쓰고,
-- CTE 로 판정을 먼저 계산해 **바뀌는 행만** UPDATE 한다(불필요 write·커넥터 타임아웃 방지).
-- 트리거는 033(C) 가 이미 이 함수를 호출하므로 재배선하지 않는다(시그니처 불변).
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
-- 1) 기기 편지가 비공개로 바뀌었는가(전부 non_pharma 여야 한다):
--    select firm_name, scope_status, count(*) from public.findings
--    where source='FDA Warning Letter' and firm_name in
--      ('Abiomed Inc.','Integra LifeSciences Corporation','Sol-Millennium Medical, Inc.',
--       'Robbins Instruments, LLC','Edge Biologicals Inc.','Aquavit Pharmaceuticals, Inc')
--    group by 1,2 order by 1;
-- 2) ★과잉차단 회귀 감시 — **복합(기기+의약품) 편지는 'ok' 로 남아야 한다**:
--    select firm_name, scope_status, count(*) from public.findings
--    where source='FDA Warning Letter' and firm_name in
--      ('Dental Technologies Inc.','Amnio Technology, LLC') group by 1,2;
-- 3) ★미승인drug 정책 불변(033 ① · CODEX rubric §4) — 'ok' 여야 한다:
--    select firm_name, scope_status from public.findings
--    where source='FDA Warning Letter' and firm_name in
--      ('VitaCig, Inc.','Swisschems','Xcel Research LLC') group by 1,2;
-- 4) 483 분류 불변(WL 만 바뀐다):
--    select scope_status, count(*) from public.findings where source='FDA 483' group by 1;
-- 5) 공개 게이트: set role anon; select count(*) from public.findings where scope_status<>'ok'; -- 0
