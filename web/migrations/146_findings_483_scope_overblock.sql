-- 146_findings_483_scope_overblock.sql
-- FDA 483 범위 분류기(grm_classify_483_scope) 과차단 수리 — 가산만, 시그니처(4-인자)·반환형(text) 불변.
--
-- 배경(2026-11-12 전수 점검): non_pharma 483 문서 271건 중 의약품·생물의약품 제조소가 섞여 있었다
--   (Henogen SRL 'Viral Vector Manufacturer', Aeon Biotherapeutics, Legacy Pharmaceutical Packaging,
--    Unico Holdings 'Oral Solution Manufacturer'). 시설 유형이 'Manufacturer' 같은 일반어이거나
--   규칙① 이 모르는 어휘라 ④(본문·업체명 휴리스틱)로 떨어진 것.
-- 변경(대표 승인 2026-11-12):
--   ① 규칙① 의약품 시설 정규식에 viral vector·gene/cell therap·biotherap·monoclonal·recombinant·
--      oral solution·pharmaceutical packag 가산.
--   ④ 업체명에 pharmac·biolog·therapeut·biosciences 가 있으면 ④ 로 non_pharma 판정하지 않는다.
--   ② 부정목록에서 `bioanalytical`·`^sponsor$` 제거 — 의약품 임상 스폰서(GCP)·생물분석 CRO 를
--      범위에 넣는다(대표 결정). 제거된 두 유형은 ③/④ 본문 축으로 흘러가므로 기기·식품 신호만
--      있는 스폰서는 여전히 non_pharma.
--   fragment 임계(30자)는 이 함수에 본문이 없어 바꾸지 않는다 — 완결된 짧은 문장은 행 단위 update 로 처리.
-- 트리거(findings_scope_status_biu)는 INSERT 에만 걸려 있어 기존 행은 자동 변하지 않는다 → 대상 행 update 는 별도.
-- 원복: 이전 본문(024 이후 프로덕션 현행)은 이 PR 본문·ops 노트에 보관. 아래 함수를 이전 본문으로 create or replace.

create or replace function public.grm_classify_483_scope(p_est_type text, p_len integer, p_doc_text text, p_firm_name text)
 returns text
 language sql
 immutable
 set search_path to 'public'
as $function$
  select case
    when coalesce(p_est_type, '') ~* '(drug|pharmac|active pharmaceutical|\yapi\y|outsourcing facility|compound|biolog|vaccine|dosage|homeopathic|heparin|anda sponsor|red cross|plasma derivative|viral vector|gene therap|cell therap|biotherap|monoclonal|recombinant|oral solution|pharmaceutical packag)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    when coalesce(p_est_type, '') ~* '(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor|pet food|animal feed|infant formula|produce manufacturer|aircraft|\yfarm\y|institutional review board|clinical investigator|medical device|health care facility|\yfood\y|smoked fish|dietary supplement|veterinar|tissue)'
      then 'non_pharma'
    when coalesce(p_est_type, '') ~* '(sterile|plasma|blood|nuclear|own label|repacker/relabeler|(control|contract) testing laborator)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    when coalesce(p_firm_name, '') !~* '(pharmac|biolog|therapeut|biosciences)'
         and regexp_replace(coalesce(p_doc_text, ''), '(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra|\yFD&C\y|department of health)', ' ', 'gi')
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
    when coalesce(p_len, 0) < 30
      then 'fragment'
    else 'ok'
  end;
$function$;
