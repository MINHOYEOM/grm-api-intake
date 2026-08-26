-- FIND-1 taxonomy v10 이벤트 -- taxonomy_version CHECK((v1..v9) IN-list,
-- 050_findings_taxonomy_v9.sql 참조)를 (v1..v10) IN-list 로 확장한다. v1~v9 로 이미 저장된
-- 행은 그대로 보존한다(provenance) -- 이 마이그레이션은 기존 행을 재분류하지 않는다.
--
-- v10 이 고치는 것은 어휘 공백이 아니라 **매칭 엔진 결함**이다.
--
-- ★한글 조사가 영문 키워드의 단어경계를 깬다. 파이썬 `re` 의 `\b` 는 한글도 `\w` 로 보므로
--   "Audit Trail**과** 사용 대장" 에서 "trail" 뒤의 단어경계가 존재하지 않는다. `audit trail`
--   은 v2 부터 data_integrity 의 키워드였는데 **등록돼 있으면서 조용히 매칭되지 않았다.**
--   어휘를 아무리 보강해도 영영 안 고쳐지는 종류라 엔진을 고친다(경계를 ASCII 문자로만 정의).
--   이 결함은 "한국어 본문 + 영문 GMP 용어" 조합에서만 발화하므로 FDA 원문 위주 코퍼스에는
--   보이지 않았다 -- 실측: 영문 레인 22,868행에서 v9->v10 변동 **0행**.
--
-- ★둘째 변경은 평범한 표기 누락 -- v4 가 넣은 "annual product review" 의 국내 표기
--   "제품품질평가" 가 없어 식약처 지적서의 PQR 문장이 전부 캐치올로 떨어졌다.
--
-- 비용 실측(공개 findings 전건 24,876행 대조): 캐치올 회수 13 · 재배치 4(전부 개선 방향,
-- 문장의 주어가 제품품질평가인데 설비/표시포장/시험실에 걸려 있었다) · **캐치올 역행 0** ·
-- FDA/캐나다 레인 이동 0 · 이동 17행 전부 MFDS.
-- 기각한 후보 3종("백업"·"제품표준서"·"청정구역")과 그 근거는 grm_findings.py 의
-- TAXONOMY_VERSION v10 change log 및 tests/test_findings_taxonomy_v10.py 참조 --
-- 셋 다 캐치올을 줄이지만 **이미 맞게 분류된 행을 더 많이 훔친다.**
-- 전제: 002 + 004 + 011 + 012 + 044 + 045 + 047 + 049 + 050 이 먼저 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
--
-- ★004/011/012/044/045/047/049/050 의 루프변수-별칭 충돌 함정 유지: plpgsql record 변수 이름이
-- FOR 루프 쿼리 내부의 테이블 별칭과 같으면 "ERROR 55000: record ... is not assigned yet" 이 난다.
-- 아래도 루프 변수는 `loop_rec`, pg_constraint 별칭은 `con` 으로 서로 다르게 유지한다.
do $$
declare
  loop_rec record;
begin
  for loop_rec in
    select con.conname
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace nsp on nsp.oid = rel.relnamespace
    where nsp.nspname = 'public'
      and rel.relname = 'findings'
      and con.contype = 'c'
      and pg_get_constraintdef(con.oid) like '%taxonomy_version%'
  loop
    execute format('alter table public.findings drop constraint %I', loop_rec.conname);
  end loop;

  alter table public.findings
    add constraint findings_taxonomy_version_v1v2v3v4v5v6v7v8v9v10_check
    check (taxonomy_version in (
      'grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3',
      'grm-finding-taxonomy/v4', 'grm-finding-taxonomy/v5', 'grm-finding-taxonomy/v6',
      'grm-finding-taxonomy/v7', 'grm-finding-taxonomy/v8',
      'grm-finding-taxonomy/v9', 'grm-finding-taxonomy/v10'
    ));
end;
$$;

-- 검증: 신규 제약이 존재하고 v1~v10 을 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2v3v4v5v6v7v8v9v10_check';
-- 검증: 현재 저장된 taxonomy_version 값 분포.
-- select taxonomy_version, count(*) from public.findings group by 1 order by 1;
-- 검증(재분류 후): v10 앵커가 실제로 걸렸는지 -- 기대값 0
--   select count(*) from public.findings
--    where finding_text ilike '%audit trail과%' and category_code = 'other_quality_system';
--   select count(*) from public.findings
--    where finding_text like '%제품품질평가%' and category_code = 'other_quality_system';
