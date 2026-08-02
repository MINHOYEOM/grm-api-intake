-- FIND-1 taxonomy v8 이벤트 -- 이미 살아있는 public.findings 의 taxonomy_version
-- CHECK((v1..v7) IN-list, 047_findings_taxonomy_v7.sql 참조)를 (v1..v8) IN-list 로
-- 확장한다. v1~v7 로 이미 저장된 행은 그대로 보존한다(provenance) -- 이 마이그레이션은
-- 기존 행을 재분류하지 않는다(재분류는 findings_reclassify_service.py 담당).
--
-- v8 이 고치는 것: 캐치올(other_quality_system) 2,497행의 지배 원인인 **어휘 공백**.
-- 21 CFR 조항문에 taxonomy 키워드가 한 단어도 안 나오는 문형들이다(211.42~58 건물/공조,
-- 211.111 시간한도, 211.166(a) 안정성 배치수, 211.180(e) 어순역전, FDCA 503B 등).
--
-- ★v5/v6/v7 과 **성질이 다른 변경**이다. 앞의 셋은 매칭 전 haystack 을 고치는 복원 계층이라
-- 기존 정분류를 빼앗을 수 없었다. 어휘 추가는 first-match-wins 매칭 자체를 바꿔 이미 올바른
-- 행을 앞선 카테고리가 빼앗을 수 있다. 그래서 채택을 수치 게이트로 강제했다:
--     rescue_ok >= 5  AND  collateral_harmful <= rescue_ok * 0.3
-- 후보 9종이 통과했고 기각군 6종(bare record / written procedure / bare specification 등)은
-- 게이트가 자동 차단했다(collateral 이 rescue 의 2.8~13.6배).
-- ★collateral 은 건수가 아니라 **원문을 읽어** 개악/개선을 가른 뒤 개악만 셌다(합계 6건).
-- ★재정렬은 하지 않았다 -- 규칙을 taxonomy 뒤쪽에 붙여 비용을 낮췄다(ISO 규칙을 5번째 대신
-- 15번째에 붙이니 collateral 54 -> 0).
-- 자세한 내용은 grm_findings.py 의 TAXONOMY_VERSION v8 change log 참조.
-- 전제: 002 + 004 + 011 + 012 + 044 + 045 + 047 이 먼저 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
--
-- ★004/011/012/044/045/047 의 루프변수-별칭 충돌 함정 유지: plpgsql record 변수 이름이 FOR 루프
-- 쿼리 내부의 테이블 별칭과 같으면 "ERROR 55000: record ... is not assigned yet" 이 난다.
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
    add constraint findings_taxonomy_version_v1v2v3v4v5v6v7v8_check
    check (taxonomy_version in (
      'grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3',
      'grm-finding-taxonomy/v4', 'grm-finding-taxonomy/v5', 'grm-finding-taxonomy/v6',
      'grm-finding-taxonomy/v7', 'grm-finding-taxonomy/v8'
    ));
end;
$$;

-- 검증: 현재 저장된 taxonomy_version 값 분포(v1~v7 외 값이 있으면 안 된다).
-- select distinct taxonomy_version from public.findings order by taxonomy_version;
-- 검증: 신규 제약이 존재하고 v1~v7 를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2v3v4v5v6v7v8_check';
-- 검증(재분류 후): 캐치올이 실제로 줄었는지 --
--   select count(*) from public.findings where category_code = 'other_quality_system';
-- 검증(구 v7 앵커): `of` 접착이 남긴 오배치가 사라졌는지 -- 기대값 0
--   select count(*) from public.findings
--    where finding_text ~* '\yofinvestigations?\y' and category_code = 'material_supplier_control';
