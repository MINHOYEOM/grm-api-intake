-- [FIND-1] HCT/P(인체 세포·조직) 범위 제외 — 사용자 범위 결정(2026-07-16).
--
-- ※ 이 파일이 023_findings_scope_tiered.sql 의 (A) 분류 함수를 create or replace 로
--   **supersede** 한다(007/008/009→010, 018/021→022, 020→023 관례와 동형). 프로덕션
--   현행 분류 규칙은 이 파일이다. 시그니처(4-인자)가 불변이라 020(C) 트리거는 재배선
--   없이 이 함수를 계속 호출한다.
--
-- ★배경: 023 트리거의 첫 실동작(2026-07-16 04:37 KST intake)에서 들어온 신규 483 이
--   난임클리닉(Center for Assisted Reproduction)의 **HCT/P 483**(21 CFR 1271 — 기증자
--   적격성·감염병 위험 표시)이었고 'ok' 로 공개됐다. 확인 결과 020 도 동일 판정이라
--   023 회귀는 아니었으나(020/023 모두 allow 에 tissue 계열 토큰 보유), **범위 정의상
--   문제**였다: HCT/P 는 의약품 GMP(21 CFR 210/211)가 아니라 21 CFR 1271 소관이고,
--   한국에서도 약사법이 아닌 인체조직안전법 소관이다. §1.1 "의약품 전반(의료기기 제외)"
--   범위 밖 → 사용자 판단으로 제외 확정.
--
-- ★설계 = **est_type 으로만 배제한다. 본문(HCT/P 텍스트 신호)으로 배제하지 않는다.**
--   실측 근거(dry-run): 본문에 HCT/P 신호가 있으면서 tissue 계열 라벨이 **아닌** 문서 3건은
--   전부 FDA 가 **의약품/생물의약품으로 규제한 세포치료 업체**다 —
--     Celltex Therapeutics('Biological Drug Manufacturer', 12건) ·
--     Liveyon Labs('(빈값)', 9건) · Liveyon('Manufacturer', 8건).
--   FDA 가 "이 제품은 미승인 생물의약품"이라고 판단한 집행 사례라 한국 제약 QA 에게
--   유효한 규제 정보다. 본문 신호로 배제하면 이 29건이 함께 사라진다 → 배제 축은 오직
--   "그 시설이 HCT/P 전용 조직시설/조직은행인가"(= est_type)여야 한다.
--
-- ★변경 = 023 의 3계층 중 tissue 토큰의 계층만 이동(그 외 전부 불변):
--   ① 강한 허용목록에서 `human tissue` **제거**
--   ② 부정목록에 `tissue` **추가**
--   ③ 약한 허용목록에서 `tissue` **제거**
--   ④⑤⑥(본문 폴백·fragment·ok)은 023 과 완전 동일 — 위 설계 근거대로 본문 축은 안 건드린다.
--
-- ★혈액제제 경계 보존(불가침 — dry-run 으로 확인): 혈액·혈장분획은 명백한 의약품이라
--   그대로 공개 유지된다.
--     'Blood Bank'(26건) → ③ `blood` → ok
--     'Plasma Derivative Manufacturer'(2건) → ① `plasma derivative` → ok
--     'American National Red Cross'(3건) → ① `red cross` → ok
--     'Vaccine/Blood Products Manufacturer' → ① `vaccine` → ok
--     'Biological Drug Manufacturer'(Celltex 12건) → ① `drug`/`biolog` → ok
--
-- ★영향(dry-run 실측): ok → non_pharma **22건**뿐 — 'Human Tissue Establishment' 13 ·
--   'Human Tissue and Medical Device Manufacturer' 6 · 'Reproductive Human Tissue' 3
--   (+ 'Tissue Testing Laboratory' 은 OII 표에만 있고 findings 0). 공개 8,187 → 8,168 실측(적용 전 신규 intake 3건 유입분 포함: 8,187+3-22).
--   그 외 라벨의 판정은 전량 023 과 동일하다.
--
-- 시그니처(4-인자) 불변 → 020(C) 트리거는 재배선 없이 이 함수를 계속 호출한다.

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
    -- ① 강한 제약 허용목록(023 에서 `human tissue` 제거 — HCT/P 범위 제외)
    when coalesce(p_est_type, '') ~* '(drug|pharmac|active pharmaceutical|\yapi\y|outsourcing facility|compound|biolog|vaccine|dosage|homeopathic|heparin|anda sponsor|red cross|plasma derivative)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    -- ② 비제약 부정목록(023 + `tissue` — HCT/P 조직시설·조직은행·생식조직·조직시험소)
    when coalesce(p_est_type, '') ~* '(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor|pet food|animal feed|infant formula|produce manufacturer|aircraft|\yfarm\y|institutional review board|clinical investigator|bioanalytical|^sponsor$|medical device|health care facility|\yfood\y|smoked fish|dietary supplement|veterinar|tissue)'
      then 'non_pharma'
    -- ③ 약한 허용목록(023 에서 `tissue` 제거 — 나머지 불변)
    when coalesce(p_est_type, '') ~* '(sterile|plasma|blood|nuclear|own label|repacker/relabeler|(control|contract) testing laborator)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    -- ④ 일반값/빈값 est_type → 문서 본문·업체명 2차 판정(023 과 완전 동일 — 본문 축 불변)
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
    -- ⑤ 30자 미만 추출 단편(023 과 동일 임계)
    when coalesce(p_len, 0) < 30
      then 'fragment'
    else 'ok'
  end;
$$;

-- (B) 백필 — **tissue 라벨 행으로 범위 한정**한다. 근거: 위 dry-run 으로 판정이 바뀌는
-- 라벨이 tissue 계열뿐임을 실증했으므로 전 483 재계산은 불필요하고, 023 적용 때 전량
-- UPDATE(doc_text string_agg 9,289행 + 행마다 무거운 정규식)가 **커넥터 타임아웃 → 롤백**을
-- 유발한 전례가 있다. 한정 백필은 ~22행이라 즉시 끝나고 결과는 전량 재계산과 동일하다.
-- doc_text 는 이 경로에서 판정에 영향이 없지만(①②에서 확정) 계약대로 그대로 넘긴다.
with doc_text as (
  select f.raw_signal_id, string_agg(f.finding_text, ' ') as doc_text
  from public.findings f
  where f.source = 'FDA 483'
    and f.raw_signal_id in (
      select rs.raw_signal_id from public.raw_signals rs
      where rs.source = 'FDA 483'
        and coalesce(nullif(trim((rs.raw_json::jsonb) ->> 'establishment_type'), ''), '') ~* 'tissue'
    )
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
-- ① HCT/P 배제: 'Human Tissue Establishment'·'Reproductive Human Tissue'·
--    'Human Tissue and Medical Device Manufacturer' → non_pharma
-- ② 혈액제제 보존: 'Blood Bank'·'Plasma Derivative Manufacturer'·'American National Red Cross'
--    ·'Biological Drug Manufacturer' → ok
-- ③ 본문 축 불변: Celltex/Liveyon(세포치료·FDA 가 미승인 생물의약품으로 규제) 29건 ok 유지
-- ④ 공개 총수 8,168 실측(적용 직전 8,190 = 8,187 + 신규 intake 3건, -22)
