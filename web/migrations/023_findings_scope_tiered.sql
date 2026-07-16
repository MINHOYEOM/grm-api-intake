-- ※ 2026-07-16: public.grm_classify_483_scope(text, integer, text, text) 의 (A) 함수
-- 바디가 024_findings_scope_hctp.sql 에서 create or replace 되어 이 파일의 정의를
-- **supersede** 한다(사용자 범위 결정 — HCT/P(21 CFR 1271 인체 세포·조직)를 GRM 범위에서
-- 제외: tissue 토큰만 계층 이동 — 강한 허용목록에서 `human tissue` 제거·부정목록에 `tissue`
-- 추가·약한 허용목록에서 `tissue` 제거. 본문/업체명 폴백·fragment·ok 는 이 파일과 완전
-- 동일 — 본문 축은 안 건드린다). 프로덕션 현행 분류 규칙은 024 를 참조하라(007/008/009→
-- 010, 018/021→022, 020→023 관례와 동형). 시그니처(4-인자)가 불변이므로 이 파일 아래
-- (B) 백필과 020 이 만든 (C) 트리거·(D) 010 2-인자 함수 drop 은 재배선 없이 여전히
-- 프로덕션 현행 사실이다(024 는 tissue 라벨 행으로 한정한 자체 백필을 별도로 포함한다).
-- 아래 (A) 함수 바디는 git 히스토리·원복용 원본으로 남긴다.
--
-- [FIND-1] 483 범위 분류기 3계층 교체 — Codex 감사 F-03 (2026-07-15/16).
--
-- ★결함(020): 허용목록이 부정목록보다 먼저인 단일 2단 구조에서, 허용목록의 **넓은 토큰**
--   (sterile|plasma|blood|tissue|own label|(control|contract) testing laborator)이 명백한
--   기기/식품 라벨을 선점한다. 라이브 재현(컨트롤타워 CONFIRMED):
--     'Sterile Medical Device Manufacturer' → ok / 'Sterile Food Manufacturer' → ok
--   현 코퍼스 실측 위험도는 낮다 — FDA 실제 어휘 76종(문서 2,036건·2018~2026) 전수 확인
--   결과 그런 라벨은 실존 0이고, allow∧deny 동시 매치 라벨 4종은 전부 정당 제약(아래
--   보존 목록). 즉 기존 발현 0·경로만 실재(트리거가 신규 insert 에 연결돼 있으므로 수리).
--
-- ★수리 = 허용목록을 강/약 2계층으로 분리한 3계층 판정(020 의 ①②를 ①②③으로):
--   ① strong allow — 제약을 **단독으로 확정**하는 토큰만(drug|pharmac|biolog|vaccine|
--      compound|outsourcing facility|active pharmaceutical|\yapi\y|dosage|homeopathic|
--      heparin|anda sponsor|human tissue|red cross|plasma derivative).
--      → 복합 라벨 보존은 이 계층이 담당한다(020 의 "allow 우선" 취지 유지):
--        'Pharmaceutical and Medical Device Manufacturer'(drug/pharmac) ·
--        'Medical Food and OTC Drug Manufacturer'(drug) ·
--        'Biologics & Medical Device Manufacturer'(biolog) ·
--        'Human Tissue and Medical Device Manufacturer'(human tissue — CBER HCT/P 라벨.
--        ★'tissue' 단독은 약한 토큰이라 ③으로 내려가므로, 이 실존 라벨만 'human tissue'
--        로 강한 계층에 명시해 회귀를 막는다)
--   ② deny — 020 부정목록 그대로(medical device|food|health care facility|...).
--      → 이제 'Sterile/Blood/Plasma/Tissue + Medical Device|Food ...' 류가 여기서 차단된다.
--   ③ weak allow — 넓은 토큰(sterile|plasma|blood|tissue|nuclear|own label|
--      repacker/relabeler|(control|contract) testing laborator). deny 를 통과한 뒤에만
--      허용하므로 실존 라벨('Blood Bank'·'Plasma Derivative Manufacturer'는 ①에서 이미
--      처리·'Contract/Control Testing Laboratory'·'Repacker/Relabeler'·'Human Tissue
--      Establishment'는 ①)이 전부 종전과 동일하게 ok 를 유지한다.
--   ④⑤⑥ 일반값 본문/업체명 폴백·fragment·ok — 020 과 동일(무변경).
--
-- ★회귀 계약(테스트 고정): FDA 실존 어휘의 분류 결과는 020 과 **전량 동일**해야 한다.
--   바뀌는 것은 실존하지 않는 위험 라벨의 방어뿐이다. 시그니처(4-인자) 불변이라 020(C)
--   트리거는 재배선 없이 이 함수를 계속 호출한다. (B) 백필도 불필요하지만 방어적
--   재정렬을 위해 020(B)와 동형의 멱등 UPDATE 를 포함한다(예상 변경 0행 — 적용 시 검증).

create or replace function public.grm_classify_483_scope(
  p_est_type text,
  p_len integer,
  p_doc_text text,
  p_firm_name text
)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    -- ① 강한 제약 허용목록(부정목록보다 먼저 — 실존 복합 라벨 4종 보존)
    when coalesce(p_est_type, '') ~* '(drug|pharmac|active pharmaceutical|\yapi\y|outsourcing facility|compound|biolog|vaccine|dosage|homeopathic|heparin|anda sponsor|human tissue|red cross|plasma derivative)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    -- ② 비제약 부정목록(020 원본 그대로)
    when coalesce(p_est_type, '') ~* '(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor|pet food|animal feed|infant formula|produce manufacturer|aircraft|\yfarm\y|institutional review board|clinical investigator|bioanalytical|^sponsor$|medical device|health care facility|\yfood\y|smoked fish|dietary supplement|veterinar)'
      then 'non_pharma'
    -- ③ 약한 허용목록(deny 통과 후에만 — 020 에서 넓어서 위험했던 토큰들)
    when coalesce(p_est_type, '') ~* '(sterile|plasma|blood|tissue|nuclear|own label|repacker/relabeler|(control|contract) testing laborator)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    -- ④ 일반값/빈값 est_type → 문서 본문·업체명 2차 판정(020 과 동일, 무변경)
    when regexp_replace(coalesce(p_doc_text, ''), '(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra|\yFD&C\y|department of health)', ' ', 'gi')
           !~* '(drug products?|drug substance|active pharmaceutical ingredient|aseptic|sterilit|\ysterile\y|compounded?|\yUSP\y|batch record|master production|\y211\.\d|finished pharmaceutical|prescription|\yNDC\y|\yOTC\y|potency|adverse drug|quality control unit|\yDSCSA\y|tablets?|capsules?|injectable|vials?)'
         and (
           regexp_replace(coalesce(p_doc_text, ''), '(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra|\yFD&C\y|department of health)', ' ', 'gi')
             ~* '(\yMDR\y|medical device report|device history record|device master record|finished devices?\y|user facility|21 CFR 820|\y820\.\d|design (input|output|history file)|marketed device)'
           or regexp_replace(coalesce(p_doc_text, ''), '(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra|\yFD&C\y|department of health)', ' ', 'gi')
             ~* '(food[- ]contact|\yfoods?\y|animal food|low[- ]acid canned|infant formula|\yHACCP\y|\yjuice\y|seafood|ice cream|\ycheese\y|\ymilk\y)'
           or coalesce(p_firm_name, '')
             ~* '(creamer|creamery|dairy|\yfoods?\y|\yfarms?\y|orchard|bakery|baking|tortiller|produce|beverage|brewing|nestle|juice)'
         )
      then 'non_pharma'
    -- ⑤ 30자 미만 추출 단편(020 과 동일 임계)
    when coalesce(p_len, 0) < 30
      then 'fragment'
    else 'ok'
  end;
$$;

-- (B) 방어적 재정렬 백필 — 020(B)와 동형·멱등. 실존 어휘 분석상 예상 변경 0행이며,
-- 적용 직후 scope_status 분포가 020 적용 시점(ok 8,097/non_pharma 910/fragment 192
-- + 이후 신규 유입분)과 정합해야 한다.
with doc_text as (
  select
    f.raw_signal_id,
    string_agg(f.finding_text, ' ') as doc_text
  from public.findings f
  where f.source = 'FDA 483'
  group by f.raw_signal_id
)
update public.findings f
set scope_status = public.grm_classify_483_scope(
  coalesce(nullif(trim((rs.raw_json::jsonb) ->> 'establishment_type'), ''), ''),
  length(f.finding_text),
  coalesce(d.doc_text, f.finding_text),
  coalesce(f.firm_name, '')
)
from public.raw_signals rs
join doc_text d on d.raw_signal_id = rs.raw_signal_id
where rs.raw_signal_id = f.raw_signal_id
  and f.source = 'FDA 483';

-- 검증(라이브 적용 시):
-- ① Codex 반례 8종이 전부 non_pharma 로 바뀌었는지(수리 목표):
--    select public.grm_classify_483_scope('Sterile Medical Device Manufacturer',120,'','');  -- non_pharma
--    select public.grm_classify_483_scope('Sterile Food Manufacturer',120,'','');            -- non_pharma
-- ② 실존 복합 라벨 4종 보존(회귀 금지):
--    'Pharmaceutical and Medical Device Manufacturer'·'Medical Food and OTC Drug Manufacturer'
--    ·'Biologics & Medical Device Manufacturer'·'Human Tissue and Medical Device Manufacturer'
--    전부 ok(본문 무관).
-- ③ 백필 변경 0행: scope_status 분포가 적용 전과 동일.
