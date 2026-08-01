-- FIND-1 taxonomy v5 이벤트 -- 이미 살아있는 public.findings 의 taxonomy_version
-- CHECK((v1, v2, v3, v4) IN-list, 012_findings_taxonomy_v4.sql 참조)를
-- (v1, v2, v3, v4, v5) IN-list 로 확장한다. v1~v4 로 이미 저장된 행은 그대로 보존한다
-- (provenance) -- 이 마이그레이션은 기존 행을 재분류하지 않는다(재분류는 별도
-- findings_reclassify_service.py 가 담당, grm-findings-reclassify.yml workflow_dispatch 로
-- 재사용 -- 신규 워크플로 불요).
--
-- v5 가 고치는 것: 21 CFR 211.113(a)("objectionable microorganisms in drug products **not
-- required to be sterile**" -- 즉 **비무균** 제품의 미생물 관리 조항)가 문장 속 "sterile"
-- 한 단어 때문에 무균보증/무균공정으로 분류되던 역극성 결함. 라이브 실측 25건 전량이
-- 무균 버킷에 있었고, v5 적용 후 정본 카테고리인 contamination_control(오염/교차오염 관리)로
-- 이동한다. 자세한 내용은 grm_findings.py 의 TAXONOMY_VERSION v5 change log 참조.
-- 전제: 002_findings.sql(fresh-install 정본) + 004 + 011 + 012 가 먼저 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
--
-- ★004/011/012 의 루프변수-별칭 충돌 함정 유지: plpgsql record 변수 이름이 FOR 루프 쿼리
-- 내부의 테이블 별칭과 같으면 "ERROR 55000: record ... is not assigned yet" 이 난다.
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
    add constraint findings_taxonomy_version_v1v2v3v4v5_check
    check (taxonomy_version in (
      'grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3',
      'grm-finding-taxonomy/v4', 'grm-finding-taxonomy/v5'
    ));
end;
$$;

-- 검증: 현재 저장된 taxonomy_version 값 분포(v1~v5 외 값이 있으면 안 된다).
-- select distinct taxonomy_version from public.findings order by taxonomy_version;
-- 검증: 신규 제약이 존재하고 v1~v5 를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2v3v4v5_check';
-- 검증(재분류 후): 211.113(a) 문형이 무균 버킷에 남아 있지 않은지 -- 기대값 0
--   select count(*) from public.findings
--    where finding_text ~* 'not\s+requi\w*\s+to\s+be\s+steril'
--      and category_code = 'aseptic_sterility_assurance';
