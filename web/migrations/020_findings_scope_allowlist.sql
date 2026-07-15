-- ※ 2026-07-16: public.grm_classify_483_scope(text, integer, text, text) 의 (A) 함수
-- 바디가 023_findings_scope_tiered.sql 에서 create or replace 되어 이 파일의 정의를
-- **supersede** 한다(Codex 감사 F-03 — 허용목록의 넓은 토큰 sterile|plasma|blood|tissue|
-- own label|(control|contract) testing laborator 가 명백한 기기/식품 라벨을 선점하는 결함
-- 수리: 허용목록을 강/약 2계층으로 나누고 그 사이에 부정목록을 끼운 3계층 판정으로 교체).
-- 프로덕션 현행 분류 규칙은 023 을 참조하라(007/008/009→010, 018/021→022 관례와 동형).
-- 시그니처(4-인자)가 불변이므로 아래 (C) 트리거는 재배선 없이 023 의 함수를 계속 호출한다
-- — 즉 (C) 트리거·(D) 010 2-인자 함수 drop 은 여전히 프로덕션 현행 사실이고, (B) 백필도
-- 여전히 역사적 사실이다(023 은 방어적 재정렬을 위해 동형의 멱등 백필을 별도로 포함한다).
-- 아래 (A) 함수 바디는 git 히스토리·원복용 원본으로 남긴다.
--
-- FIND-1 데이터 순도 2차 마이그레이션 — 010 의 est_type **부정목록(denylist)** 을
-- **허용목록(allowlist) 우선 + 가드된 본문/업체명 폴백** 으로 교체한다.
--
-- 010 과 동일하게 **삭제가 아니다** — row 는 전부 그대로 남고, 공개 게이트(010 (E) 정책)·
-- 집계 RPC(010 (F))에서만 걸러진다. scope_status 값 집합도 010 그대로 3종
-- ('ok'/'non_pharma'/'fragment') — 새 상태값을 만들지 않는다. 따라서 이 파일은
-- **정책·RPC·check 제약을 일절 건드리지 않는다**(아래 "영향 범위" 참조).
--
-- ★중요: 이 파일은 010_findings_scope_purity.sql 의 (B) 분류 함수 grm_classify_483_scope
-- 와 (D) 트리거 함수 grm_findings_scope_status_trigger 를 **supersede** 한다 — 010 이
-- 007/008/009 를 supersede 한 것과 같은 관례다. 010 의 (B)/(D) 바디는 git 히스토리·원복용
-- 원본으로 그대로 남기고(파일 상단에 이 사실을 알리는 주석만 추가), 프로덕션 현행 분류
-- 규칙은 이 파일이 정의한다. 010 의 (A) 컬럼·check 제약, (E) 정책, (F) RPC 5종은 이
-- 파일이 건드리지 않으므로 010 이 계속 현행 정의다(단 findings_stats().top_firms 는 017).
--
-- ============================================================================
-- 왜 (근거: 컨트롤 타워 라이브 실측, 2026-07-15)
-- ============================================================================
-- 010 의 grm_classify_483_scope 는 est_type **부정목록** 이다(shell egg|cheese|peanut|
-- pet food|animal feed|infant formula|farm|clinical investigator|...). FDA 가 **일반적인**
-- establishment_type 을 쓰면 그대로 통과한다 — 이것이 실측된 누출 경로다.
--
-- 실측 방법: 라이브 공개 findings 8,545건 전량(anon)에 FDA OII Electronic Reading Room
-- 표 2,036행의 establishment_type 을 media_id 로 조인(= FDA 원본 기준, 추정 아님).
-- findings_stats() 의 findings=public_findings=8,545 로 전수 커버리지 확인됨.
--   - 공개 FDA 483: 8,455건 / 문서 1,366개
--   - **확인된 누출: 375건 / 문서 62개 (전체 공개 findings 의 4.39%)**
--       · 식품 272건 (Blue Bell Creameries·Plainview Milk·Sanger Fresh Cut Produce·
--         Vulto Creamery·Bravo Packing(동물 사료)·Quality Formulation·Thermo Pac 등)
--       · 의료기기 103건 (Advanced Medical Optics·Hill-Rom·Becton Dickinson·Theranos·
--         Magellan Diagnostics + 병원 MDR 483 — Brigham·Virginia Mason·Reading Hospital 등)
--   - 누출은 전부 **일반값 est_type 버킷**에 있다(Manufacturer 461건/71문서, (빈값) 114건/
--     23문서, Importer/Warehouse/Repacker, Initial Distributor..., Health Care Facility 등
--     합계 734건/125문서).
--
-- ★단순 배제가 답이 아닌 이유(수치): 그 일반값 734건 중 **392건(68문서)은 정당한 제약
-- 제조소**다 — Teva Parenteral·Hospira·Gilead·Genentech·Pfizer·Catalent·Dr. Reddy's·
-- American National Red Cross + 조제약국(compounding pharmacy) 다수. est_type 허용목록만
-- 쓰고 일반값을 전부 배제하면 이 392건이 함께 비공개된다 = **과잉 차단**. 그래서 일반값은
-- 배제하지 않고 **문서 본문 신호 + firm_name 으로 2차 판정**한다.
--
-- ============================================================================
-- 규칙 (판정 순서 — 먼저 매치하는 조건이 우선)
-- ============================================================================
--   ① est_type 이 **제약 허용목록** 매치 → 'ok'(길이 미달이면 'fragment')
--   ② est_type 이 **비제약 부정목록** 매치 → 'non_pharma'
--   ③ est_type 이 일반값/빈값 → 문서 본문에 **제약 신호가 전혀 없고** 기기/식품 신호
--      또는 식품계 firm_name 이 있으면 → 'non_pharma'
--   ④ 30자 미만 추출 단편 → 'fragment'   ⑤ 그 외 → 'ok'
--
-- ★①이 ②보다 **먼저** 오는 것이 이 설계의 핵심이다. FDA 의 **복합 라벨**을 살리기
-- 위해서다 — "Pharmaceutical and Medical Device Manufacturer"(Hospira 24건)·"Human Tissue
-- and Medical Device Manufacturer"(RTI Biologics)·"Biologics & Medical Device
-- Manufacturer"(CSL Behring)·"Medical Food and OTC Drug Manufacturer" 는 부정목록의
-- 'medical device'/'food' 토큰에 걸리지만 실제로는 제약 시설이다. 순서를 뒤집어 측정하면
-- 과잉 차단이 4건 → 42건으로 늘어난다(실측).
--
-- ★③의 '제약 신호가 전혀 없고' 가드가 필수다. "Federal Food, Drug, and Cosmetic Act"
-- (503B 조항 인용)·"Food and Drug Administration"(FDA 양식 머리글, OCR 잔재 포함)은 제약
-- 483 에도 흔하므로 판정 전에 마스킹한다. 'sanitation'·'adulterated' 는 제약·식품 양쪽에
-- 흔해서 신호로 쓰지 않는다.
--
-- 이 규칙의 실측 정확도(공개 483 8,455건 전량 대조, 문서 단위 수기 판정 기준):
--   - 누출 정확 차단        : 354 / 375 (94.4%)
--   - 정당 제약 과잉 차단   : **4건** (정당분 8,078건의 0.05%)
--   - 잔여 누출             : 21건
-- 잔여 21건은 대부분 Unico Holdings(소아 전해질 경구용액 — FDA 스스로 같은 업체 483 을
-- 'Manufacturer'/'Medical Food and OTC Drug Manufacturer'/'Pediatric Electrolyte Oral
-- Solution Manufacturer' 로 제각각 라벨) 로, 과잉 차단 4건도 같은 업체다 — 규칙으로
-- 풀리지 않는 진짜 경계 사례이므로 보수적으로 공개 유지(오삭제 방지)한다.
--
-- 적용 후 예상: 공개 FDA 483 8,455 → 8,097건, 공개 findings 합계 8,545 → 8,187건.
-- 정확한 수치는 (C) 백필 UPDATE 의 실제 영향 row count 로 확정된다.
--
-- ============================================================================
-- 영향 범위 (scope_status 술어를 쓰는 곳 — 전부 자동 반영, 이 파일은 무수정)
-- ============================================================================
-- scope_status='ok' 는 010 이 이미 심어둔 **단일 공개 술어**다. 값 집합을 바꾸지 않으므로
-- 아래는 전부 재정의 없이 자동으로 따라온다:
--   - 010 (E) findings_public_read 정책 → /findings/ 검색·목록·딥링크
--   - 010 (F) findings_stats / findings_firm_stats → /findings/ 카운트, 017 top_firms
--   - 010 (F) findings_category_matrix → /findings/trends/ 집계·히트맵
--   - 010 (F) findings_translation_queue → 번역 예산에서 제외(플래그분 번역 안 함)
--   - 014 findings_firm_counts / 018 findings_similar(S1 렉시컬 유사 문구 검색) → 같은 술어 상속
--   - 019 findings_similar_by_id(S2 의미 유사도 임베딩) → 019 가 기준 finding·후보 양쪽에
--     `f.scope_status = 'ok'` 를 적용하므로 자동 반영(019 플래그 ENABLE_FINDINGS_EMBED 가
--     켜지는 시점에 이 파일의 판정이 그대로 반영된 상태로 출발한다 — 임베딩 생성 대상에서도
--     플래그분이 빠진다)
-- ※ 010 (F) findings_translation_rows 는 010 이 의도적으로 scope 필터를 두지 않았다
--   (--apply 원문 byte 대조 전용) — 이 파일도 그대로 둔다.
--
-- ============================================================================
-- 되돌리는 법
-- ============================================================================
--   - 개별 오분류 복구: `update public.findings set scope_status = 'ok' where finding_id = '<id>';`
--   - 020 만 원복(010 거동으로 회귀): 010_findings_scope_purity.sql 의 (B) 분류 함수와
--     (D) 트리거 함수 블록을 그대로 재실행한 뒤, 이 파일 (C) 백필을 010 (C) 백필로 바꿔
--     한 번 돌리면 된다. 010 의 2-인자 함수는 이 파일이 drop 하므로 재생성이 필요하다
--     (010 파일에 원본 소스가 그대로 남아 있다).
--   - 전체 해제: `update public.findings set scope_status = 'ok';` (컬럼/제약/정책 불변)
--
-- 전제: 010_findings_scope_purity.sql 이 먼저 적용되어 있어야 한다(scope_status 컬럼·
-- check 제약·공개 정책·RPC 5종은 010 이 만든 것을 그대로 쓴다).

-- ============================================================================
-- (A) 분류 함수 — 4-인자로 교체. 010 의 2-인자 버전은 (D) 트리거 재배선 후 drop 한다.
-- FDA 483 전용 판정 로직이며, 호출측이 source='FDA 483' 일 때만 사용해야 한다는 010 의
-- 계약(호출측 책임)을 그대로 승계한다 — 아래 (D) 트리거와 (C) 백필이 그 게이트를 지킨다.
--
-- p_doc_text = **문서 단위** 관찰 텍스트(개별 finding 이 아니다). 범위(scope)는 문서의
-- 속성이지 문장의 속성이 아니기 때문이다 — 식품 483 의 개별 관찰은 식품 단어를 아예 안
-- 담는 경우가 흔하다(예: Blue Bell 문서의 "All reasonable precautions are not taken to
-- ensure that production procedures do not contribute contamination from any source.").
-- 문장 단위로 판정하면 이런 행이 그대로 누출된다 — 실측으로 확인된 과소 계상 원인이다.
-- ============================================================================

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
    -- ① 제약 허용목록(부정목록보다 먼저 — 복합 라벨 보호)
    when coalesce(p_est_type, '') ~* '(drug|pharmac|\yapi\y|active pharmaceutical|outsourcing facility|compound|biolog|sterile|vaccine|plasma|blood|red cross|tissue|nuclear|dosage|homeopathic|heparin|anda sponsor|own label|repacker/relabeler|(control|contract) testing laborator)'
      then case when coalesce(p_len, 0) < 30 then 'fragment' else 'ok' end
    -- ② 비제약 부정목록(010 원본 + 실측으로 드러난 누락 어휘: medical device·health care
    --    facility·food·smoked fish·dietary supplement·veterinar)
    when coalesce(p_est_type, '') ~* '(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor|pet food|animal feed|infant formula|produce manufacturer|aircraft|\yfarm\y|institutional review board|clinical investigator|bioanalytical|^sponsor$|medical device|health care facility|\yfood\y|smoked fish|dietary supplement|veterinar)'
      then 'non_pharma'
    -- ③ 일반값/빈값 est_type → 문서 본문·업체명 2차 판정. 제약 신호가 하나라도 있으면
    --    무조건 공개 유지(오삭제 방지 = 과잉 차단 방지). FFDCA 인용·FDA 양식 머리글은
    --    판정 전에 마스킹한다.
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
    -- ④ 30자 미만 추출 단편(010 과 동일 임계)
    when coalesce(p_len, 0) < 30
      then 'fragment'
    else 'ok'
  end;
$$;

-- ============================================================================
-- (B) 기존 행 백필 — FDA 483 소스만. 문서 단위 본문은 **해당 문서의 findings.finding_text
-- 전체를 string_agg** 해서 만든다(= 위 실측 대조에 쓴 입력과 동일한 텍스트이므로 측정된
-- 정확도가 그대로 재현된다). scope_status 와 무관하게 전 행을 집계해야 문서 단위 진실이
-- 나오므로 doc_text CTE 에는 필터를 두지 않는다.
--
-- 010 (C) 와 달리 이 UPDATE 는 **483 전 행을 무조건 재분류**한다(멱등 — 재실행하면 같은
-- 값으로 다시 쓴다). 010 이 'fragment' 로 표시한 행이 이제 'non_pharma' 로 바뀔 수 있다
-- (식품 문서의 30자 미만 단편) — 둘 다 비공개이므로 공개 결과에는 차이가 없다.
-- ============================================================================

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

-- ============================================================================
-- (C) 트리거 갱신 — 이후 어떤 경로로 insert 되든(일일 append, F2 백필 auto-cron) FDA 483
-- 행은 자동으로 새 규칙으로 분류된다. 다른 소스는 전혀 손대지 않는다(010 과 동일).
--
-- ★문서 본문 출처가 (B) 백필과 다른 이유: BEFORE INSERT 시점에는 같은 문서의 형제 행이
-- 아직 없을 수 있어 findings 를 집계할 수 없다. 대신 raw_signals.raw_json 의
-- `fda_483_observations` 배열(= findings 추출의 원천 — findings_extractors.
-- _from_fda_483_observations 참조)을 문서 본문으로 쓴다. deficiency 에 detail 을 더해
-- 본문이 더 길어지는데, ③ 규칙은 "제약 신호가 하나라도 있으면 공개 유지" 가드라 본문이
-- 길어질수록 플래그가 **덜** 걸린다 — 즉 오삭제 방향으로는 안전한 비대칭이다.
--
-- SECURITY DEFINER 불필요(010 과 동일 근거): findings 에 insert 하는 주체(service_role)는
-- 이미 raw_signals 읽기 권한을 갖는다.
-- ============================================================================

create or replace function public.grm_findings_scope_status_trigger()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  v_raw jsonb;
  v_est_type text;
  v_doc_text text;
begin
  if new.source = 'FDA 483' then
    select rs.raw_json::jsonb
      into v_raw
    from public.raw_signals rs
    where rs.raw_signal_id = new.raw_signal_id;

    if v_raw is null then
      -- raw_signal 이 같은 트랜잭션 내에서 아직 보이지 않는 등 방어적 예외 상황 —
      -- 명시된 기본값 'ok' 로 둔다(오탐으로 신규 데이터를 숨기지 않는 안전 측 기본값).
      new.scope_status := 'ok';
    else
      v_est_type := coalesce(nullif(trim(v_raw ->> 'establishment_type'), ''), '');

      -- jsonb_typeof 가드: 배열이 아닌 값이 들어와도 트리거가 절대 죽지 않게 한다
      -- (수집 파이프라인 전체를 막는 사고 방지 — 최악의 경우 본문 없이 판정한다).
      select coalesce(
               string_agg(
                 coalesce(obs ->> 'deficiency', '') || ' ' || coalesce(obs ->> 'detail', ''),
                 ' '
               ),
               ''
             )
        into v_doc_text
      from jsonb_array_elements(
             case
               when jsonb_typeof(v_raw -> 'fda_483_observations') = 'array'
                 then v_raw -> 'fda_483_observations'
               else '[]'::jsonb
             end
           ) as obs;

      if coalesce(v_doc_text, '') = '' then
        v_doc_text := new.finding_text;   -- 관찰 배열이 없으면 최소한 본 행으로 판정
      end if;

      new.scope_status := public.grm_classify_483_scope(
        v_est_type,
        length(new.finding_text),
        v_doc_text,
        coalesce(new.firm_name, '')
      );
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists findings_scope_status_biu on public.findings;

create trigger findings_scope_status_biu
before insert on public.findings
for each row execute function public.grm_findings_scope_status_trigger();

-- ============================================================================
-- (D) 010 의 2-인자 분류 함수 제거 — 위 (C) 에서 트리거가 4-인자 버전으로 재배선된
-- **뒤에** 실행해야 한다(이 파일은 순차 실행이므로 이 위치가 그 순서를 보장한다).
-- 남겨두면 4-인자 버전과 이름이 겹쳐 오버로드가 되고, 옛 규칙을 호출하는 경로가 조용히
-- 살아남을 수 있다 — 규칙은 하나여야 한다.
-- ============================================================================

drop function if exists public.grm_classify_483_scope(text, integer);

-- 검증(사람 실행용, 프로덕션 SQL Editor):
-- 1) 483 분포(적용 후):
--    select scope_status, count(*) from public.findings where source = 'FDA 483' group by scope_status order by scope_status;
-- 2) 공개 게이트가 실제로 걸러내는지(010 정책 그대로):
--    set role anon; select count(*) from public.findings where scope_status <> 'ok'; -- 항상 0
--    reset role;
-- 3) 대표 누출 문서가 실제로 비공개로 바뀌었는지(식품·기기 각 1):
--    select firm_name, scope_status, count(*) from public.findings
--    where firm_name ilike 'Blue Bell%' or firm_name ilike 'Advanced Medical Optics%'
--    group by firm_name, scope_status order by firm_name;
-- 4) 정당 제약이 살아있는지(과잉 차단 회귀 감시 — 전부 'ok' 여야 한다):
--    select firm_name, scope_status, count(*) from public.findings
--    where firm_name ilike 'Teva Parenteral%' or firm_name ilike 'Hospira%'
--       or firm_name ilike 'Gilead%' or firm_name ilike 'Genentech%'
--    group by firm_name, scope_status order by firm_name;
-- 5) 신규 483 insert 에 트리거가 자동 적용되는지는 라이브 배치(다음 daily append)에서
--    scope_status 분포 변화로 관찰한다(이 파일 자체는 오프라인 텍스트 계약 테스트로만
--    검증됨 — tests/test_findings_scope_allowlist.py 참조).
