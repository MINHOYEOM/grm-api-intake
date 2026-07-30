-- ============================================================================
-- 036_findings_rpc_inspector_names.sql — [FIND-483-SIGNER] 실사관 이름 투영 1키 추가
--
-- 목적: `findings.inspector_names`(jsonb, 002 부터 존재하나 전량 빈값이던 컬럼)를
--   웹이 읽을 수 있도록 두 서빙 RPC 의 findings[] 투영에 **키 1개만** 얹는다.
--   집계·프로파일 페이지·필터는 이 파일의 범위가 아니다(008 의 "조사관별 집계 금지"
--   계약은 그대로 유효 — 그 계약의 사유는 '데이터 부재'이고, 데이터가 성숙하면 별도
--   결정으로 개정한다. 이 파일은 문서 카드의 **사실 표기**만 가능하게 한다).
--
-- supersede 체인: findings_search = 026 → 027 → 028 → 030 → **이 파일(036)**.
--   findings_document = 028 → **이 파일(036)**.
--
-- ─ ★왜 전문(全文) CREATE OR REPLACE 가 아닌 DO 블록인가 ────────────────────
--   025 적용 때 실증된 함정: 저장소의 옛 마이그레이션 파일에서 정의를 복사해 키를
--   추가하면, 그 사이에 적용된 다른 마이그레이션(017 의 top_firms firm_key group by)을
--   **조용히 되돌린다**. 초록 CI 도 이걸 못 잡는다 — 함수는 정상 동작하고 값만 옛것으로
--   돌아가기 때문이다.
--   그래서 이 파일은 `pg_get_functiondef()` 로 **라이브 정의를 런타임에 읽어** 베이스로
--   삼고, 앵커 지점에 키만 삽입한다. 신규 DB 를 마이그레이션 순서대로 재생(replay)해도
--   030/028 의 산출물 위에서 동작하므로 재현 가능하다.
--
-- ─ 자기검증(부분 적용 방지) ────────────────────────────────────────────────
--   ① 앵커가 정확히 1회가 아니면 예외로 중단한다. 정의가 표류해 앵커가 0회/2회가 되면
--      조용히 어긋난 함수를 심는 대신 **적용 자체를 실패**시킨다.
--   ② 적용 후 두 함수가 모두 inspector_names 를 보유하는지 재확인하고, 아니면 예외.
--   ③ 멱등: 이미 적용됐으면(정의에 inspector_names 존재) 건너뛴다. 재실행 안전.
--
-- ─ 수정 지점 2곳(findings_search) ──────────────────────────────────────────
--   ⑴ `page_rows` CTE 가 컬럼을 **명시 열거**하므로(f.* 아님) 거기에 f.inspector_names
--      를 실어야 한다. 이걸 빠뜨리고 투영만 고치면 `pr.inspector_names` 가 없다는
--      컴파일 오류가 난다.
--   ⑵ findings[] jsonb_build_object 투영에 'inspector_names' 키 추가.
--   findings_document 는 `rows_out` 이 f.* 라 ⑵만 필요하다.
--
-- ─ 안전 계약(불변) ─────────────────────────────────────────────────────────
--   · 함수 시그니처·인자·반환형 불변 → 클라이언트 호출 규약 무변경.
--   · security invoker + RLS 유지(공개 게이트는 010/033/034 의 행 정책이 그대로 판정).
--     inspector_names 는 **이미 공개 게이트를 통과한 행**의 컬럼 하나를 더 읽을 뿐,
--     새로 노출되는 **행**은 0건이다.
--   · 원문 텍스트·URL 필드를 새로 반환하지 않는다(007/008 안전 계약 유지).
--   · 실행 권한(anon EXECUTE) 은 CREATE OR REPLACE 라 보존된다 — 적용 후 실측 확인함.
--
-- ─ 적용 실측(2026-07-30, 프로덕션) ─────────────────────────────────────────
--   등가성 증명: findings_search 를 17종 파라미터 조합(무필터 랜딩·한글/영문 질의·
--   source/category/month/evidence/review_status/agency 필터·정렬 3종·page 5/최대·
--   per 1/100·'[]' 질의(030 경화 회귀)·이스케이프 질의·공백 질의)으로 호출해
--   **적용 전 md5** 와 **적용 후 결과에서 inspector_names 키만 제거한 md5** 를 대조 →
--   17/17 전부 IDENTICAL. 즉 이 변경은 **순수 가산(purely additive)** 이다.
--   키 부착 실측: 반환된 전 finding 에 키 존재(랜딩 122/122 등), 0문서 반환 케이스는 0.
--   findings_document: 13 findings 전건 키 부착. anon EXECUTE 두 함수 모두 true 유지.
--   prosecdef=false(invoker) 유지 확인.
--
-- 전제: 030(findings_search 현행) + 028(findings_document 현행) 적용 상태.
-- ============================================================================

do $mig$
declare
  d text;
  n int;
begin
  ---------------------------------------------------------------- findings_search
  select pg_get_functiondef(p.oid) into d
  from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
  where ns.nspname = 'public' and p.proname = 'findings_search';

  if d is null then
    raise exception '036: public.findings_search 가 존재하지 않는다';
  end if;

  if position('inspector_names' in d) > 0 then
    raise notice '036: findings_search 이미 적용됨 — 건너뜀';
  else
    select count(*) into n from regexp_matches(d, 'f\.cfr_refs,\s*f\.mfds_refs', 'g');
    if n <> 1 then raise exception '036: page_rows 앵커가 % 회(1 이어야 함)', n; end if;
    select count(*) into n from regexp_matches(d, '''mfds_refs'',\s+pr\.mfds_refs', 'g');
    if n <> 1 then raise exception '036: findings[] 투영 앵커가 % 회(1 이어야 함)', n; end if;

    d := regexp_replace(d, '(f\.cfr_refs,\s*f\.mfds_refs)', '\1, f.inspector_names');
    d := regexp_replace(d, '(''mfds_refs'',\s+pr\.mfds_refs)',
                        '\1,' || chr(10) || '        ''inspector_names'',  pr.inspector_names');
    execute d;
    raise notice '036: findings_search 적용 완료';
  end if;

  -------------------------------------------------------------- findings_document
  select pg_get_functiondef(p.oid) into d
  from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
  where ns.nspname = 'public' and p.proname = 'findings_document';

  if d is null then
    raise exception '036: public.findings_document 가 존재하지 않는다';
  end if;

  if position('inspector_names' in d) > 0 then
    raise notice '036: findings_document 이미 적용됨 — 건너뜀';
  else
    select count(*) into n from regexp_matches(d, '''mfds_refs'',\s+r\.mfds_refs', 'g');
    if n <> 1 then raise exception '036: findings_document 투영 앵커가 % 회(1 이어야 함)', n; end if;

    d := regexp_replace(d, '(''mfds_refs'',\s+r\.mfds_refs)',
                        '\1,' || chr(10) || '        ''inspector_names'',   r.inspector_names');
    execute d;
    raise notice '036: findings_document 적용 완료';
  end if;

  ------------------------------------------------------------------- 사후 자기검증
  select count(*) into n
  from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
  where ns.nspname = 'public' and p.proname in ('findings_search', 'findings_document')
    and pg_get_functiondef(p.oid) like '%inspector_names%';
  if n <> 2 then
    raise exception '036: 적용 후 검증 실패 — inspector_names 보유 함수가 % 개(2 이어야 함)', n;
  end if;
end
$mig$;
