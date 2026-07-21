-- [FIND-1 P1 A-S2 · 2026-07-21 RCA 원인 A-1] WL scope 분류 — 비제약(의료기기/식품/IRB/임상
-- 시험자) 문서 격리. scope 분류 트리거가 source='FDA 483' 에만 걸려(020/010) WL 은 전량
-- 컬럼 기본값 'ok' 로 무검증 통과했다. WL 은 483 의 establishment_type 이 없어 483 분류기를
-- 그대로 못 쓴다 — **문서 본문(wl_body) 신호**로 판정하는 별도 분류기를 신설한다(483 ④ 본문
-- 축과 동형·비대칭 안전: 제약/의약품/생물의약품/미승인drug 신호가 하나라도 있으면 'ok').
--
-- 라이브 대조(2026-07-21, 읽기전용): WL 1,292 문서 중 28 문서(≈116 findings)만 non_pharma —
-- 전부 의료기기(21 CFR 820·MDR·DHF)·식품·IRB·임상시험자. 미승인의약품 WL(505(a)/355·SARM·
-- 전자담배 드럭클레임)은 제약 신호를 가져 'ok' 유지. no-signal 55문서도 안전측 'ok' 유지.
--
-- 불변식:
--   - **삭제 아닌 플래그**(scope_status, 값 되돌림 가능). 483 분류(024)는 전혀 안 건드린다.
--   - OTC/미승인drug WL 은 'ok'(조직 정책 — CODEX 검수 rubric §4). 새 상태값을 만들지 않는다
--     (ok/non_pharma/fragment 3종 유지). OTC-only-claim 을 별도 격리하는 4번째 값은 스코프
--     정책 미확정이라 도입하지 않는다.
--   - 비대칭 안전: 제약 신호가 하나라도 있으면 ok — 오삭제(정당 제약 WL 숨김) 방향으로는 안전.
--
-- 전제: 020(트리거·483 4-인자 분류기) + 024(483 분류기 갱신). 이 파일은 483 경로를 그대로
-- 보존하고 WL 경로만 추가한다.

-- ============================================================================
-- (A) WL scope 분류 함수 — 문서 본문(+업체명) 신호 기반. immutable·순수.
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

-- ============================================================================
-- (B) 소급 백필(A-S3) — 저장된 WL 전 행을 새 규칙으로 재분류(scope_status 만, 삭제 아님).
-- 문서 본문 = raw_signals.raw_json 의 wl_body_full(없으면 wl_body_excerpt) — 트리거와 동일
-- 출처(일관성). CTE 로 판정을 먼저 계산하고 **바뀌는 행만** UPDATE 한다(불필요 write·부하 방지;
-- 023 전량 UPDATE 커넥터 타임아웃 전례 대비 — WL 만이라 모수도 작다).
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

-- ============================================================================
-- (C) 트리거 갱신 — 483 경로는 020 그대로 보존하고 WL 경로(elsif)만 추가한다. 이후 어떤
-- 경로로 insert 되든(일일 append·백필 auto-cron) WL 행은 자동으로 새 규칙으로 분류된다.
-- WL 은 BEFORE INSERT 시점 형제 행이 안 보여 findings 집계 불가 → raw_signals.raw_json 의
-- wl_body_full/excerpt(= WL 파서 원천, findings_extractors._from_warning_letter 참조)를
-- 문서 본문으로 쓴다. 본문이 길수록 ① 제약 가드에 걸려 non_pharma 가 **덜** 걸린다(안전 비대칭).
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
  elsif new.source = 'FDA Warning Letter' then
    select rs.raw_json::jsonb
      into v_raw
    from public.raw_signals rs
    where rs.raw_signal_id = new.raw_signal_id;

    if v_raw is null then
      new.scope_status := 'ok';   -- 방어적 기본값(안전측 — 신규 숨김 방지)
    else
      v_doc_text := coalesce(
        nullif(v_raw ->> 'wl_body_full', ''),
        v_raw ->> 'wl_body_excerpt',
        ''
      );
      if coalesce(v_doc_text, '') = '' then
        v_doc_text := new.finding_text;   -- 본문 없으면 최소한 본 행으로 판정
      end if;

      new.scope_status := public.grm_classify_wl_scope(
        length(new.finding_text),
        v_doc_text,
        coalesce(new.firm_name, '')
      );
    end if;
  end if;

  return new;
end;
$$;

-- 트리거 재배선(이름 동일 — before insert). 020 과 동일 정의라 재선언은 무해·멱등.
drop trigger if exists findings_scope_status_biu on public.findings;
create trigger findings_scope_status_biu
before insert on public.findings
for each row execute function public.grm_findings_scope_status_trigger();

-- 검증(사람 실행용, 프로덕션 SQL Editor):
-- 1) WL 분포(적용 후): non_pharma ≈ 116, 나머지 ok/rejected 는 scope='ok':
--    select scope_status, count(*) from public.findings where source='FDA Warning Letter' group by 1;
-- 2) 대표 기기 WL 이 비공개로 바뀌었는지:
--    select firm_name, scope_status from public.findings
--    where firm_name in ('Sea-Long Medical Systems, LLC','Globus Medical, Inc.','Criticare Technologies, Inc.')
--    group by 1,2;
-- 3) 미승인drug WL 이 살아있는지(과잉차단 회귀 감시 — 'ok' 여야 한다):
--    select firm_name, scope_status from public.findings
--    where firm_name in ('VitaCig, Inc.','Swisschems','Xcel Research LLC') group by 1,2;
-- 4) 483 분류 불변(WL 만 바뀌고 483 은 동일):
--    select source, scope_status, count(*) from public.findings where source='FDA 483' group by 1,2 order by 2;
-- 5) 공개 게이트 확인: set role anon; select count(*) from public.findings where scope_status<>'ok'; -- 0
