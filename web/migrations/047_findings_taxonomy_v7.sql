-- FIND-1 taxonomy v7 이벤트 -- 이미 살아있는 public.findings 의 taxonomy_version
-- CHECK((v1..v6) IN-list, 045_findings_taxonomy_v6.sql 참조)를 (v1..v7) IN-list 로
-- 확장한다. v1~v6 으로 이미 저장된 행은 그대로 보존한다(provenance) -- 이 마이그레이션은
-- 기존 행을 재분류하지 않는다(재분류는 별도 findings_reclassify_service.py 가 담당,
-- grm-findings-reclassify.yml workflow_dispatch 로 재사용 -- 신규 워크플로 불요).
--
-- v7 이 고치는 것: v6 의 **거울상** 손상. 같은 텍스트층이 공백을 끼워넣기만 하는 게 아니라
-- **탈락**시켜 앞 단어를 신호어에 들러붙게 만든다("rejection ofcomponents", "Clothing
-- ofpersonnel", "materials ofequipment"). 이때도 \b 단어경계 키워드가 조용히 빗나간다.
--
-- ★v6 과 결정적으로 다른 점: 이 손상은 **캐치올로 떨어지지 않는다.** 라이브 실측 109행 중
-- 캐치올은 34행뿐이고 나머지는 엉뚱한 특정 카테고리에 앉아 있다(신호어가 가려지면 매치
-- 순서상 다른 키워드가 대신 이기기 때문). 즉 "미분류 건수"나 "캐치올 비율" 같은 지표로는
-- 원리적으로 안 보인다. 실측 사례:
--   · 21 CFR 211.192 "records ofinvestigations ..."  5건 material_supplier_control -> deviation_capa
--   · 21 CFR 211.80  "rejection ofcomponents"        5건 stability_storage -> material_supplier_control
--   · 21 CFR 211.65  "workmanship ofequipment"       3건 material_supplier_control -> equipment_facility
--
-- 접두어는 `of` 하나로 못박았다 -- 후보 15종 실측 결과 of 109 · in 4 · and 1 이었고, in 의
-- 4건은 손상이 아니라 실제 영어 단어였다(instability · invalidation).
-- 복원은 분류기의 **메모리 안 haystack 한정**이며 저장된 finding_text 는 바이트 불변이다.
-- 자세한 내용은 grm_findings.py 의 TAXONOMY_VERSION v7 change log 참조.
-- 전제: 002_findings.sql(fresh-install 정본) + 004 + 011 + 012 + 044 + 045 가 먼저 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
--
-- ★004/011/012/044/045 의 루프변수-별칭 충돌 함정 유지: plpgsql record 변수 이름이 FOR 루프
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
    add constraint findings_taxonomy_version_v1v2v3v4v5v6v7_check
    check (taxonomy_version in (
      'grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3',
      'grm-finding-taxonomy/v4', 'grm-finding-taxonomy/v5', 'grm-finding-taxonomy/v6',
      'grm-finding-taxonomy/v7'
    ));
end;
$$;

-- 검증: 현재 저장된 taxonomy_version 값 분포(v1~v7 외 값이 있으면 안 된다).
-- select distinct taxonomy_version from public.findings order by taxonomy_version;
-- 검증: 신규 제약이 존재하고 v1~v7 를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2v3v4v5v6v7_check';
-- 검증(재분류 후): `of` 접착이 남긴 오배치가 사라졌는지 -- 기대값 0
--   select count(*) from public.findings
--    where finding_text ~* '\yofinvestigations?\y' and category_code = 'material_supplier_control';
