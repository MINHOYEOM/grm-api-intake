-- FIND-1 taxonomy v9 이벤트 -- taxonomy_version CHECK((v1..v8) IN-list,
-- 049_findings_taxonomy_v8.sql 참조)를 (v1..v9) IN-list 로 확장한다. v1~v8 로 이미 저장된
-- 행은 그대로 보존한다(provenance) -- 이 마이그레이션은 기존 행을 재분류하지 않는다.
--
-- v9 가 고치는 것: FDCA 503B(a)(10)**(B)** 용기 표시정보 16건. v8 이 "별건으로 남긴다"고
-- 적어둔 바로 그 건이다.
--
-- ★같은 483 관찰의 (A)/(B) 짝이 **우연한 어휘 차이로 두 카테고리에 갈려** 있었다:
--   (A) "The **labels** of your outsourcing facility's ... 503B(a)(10)(A)"  -> 표시/포장(17) 121행
--   (B) "The **containers** of your outsourcing facility's ... 503B(a)(10)(B)" -> 캐치올 16행
-- (B)항 문장에는 "label" 이 한 번도 안 나와 기존 키워드에 안 걸렸다. "표시/포장" 필터를 쓰는
-- 사용자가 (B) 건을 영영 못 보는 상태였다.
--
-- ★인용 조항을 요구하지 않는 규칙이다 -- 실측 16건의 인용이 전부 OCR 로 깨져 있다
-- ("503B(a)(I0)(B)"·"(a)(lO){B}"·"(10)(8)"). 손상된 부분이 아니라 온전한 부분에 규칙을 건다.
-- 비용 실측(후보 3종 교차 측정, 전 15,096행): rescue 16 · collateral 0.
-- 자세한 내용은 grm_findings.py 의 TAXONOMY_VERSION v9 change log 참조.
-- 전제: 002 + 004 + 011 + 012 + 044 + 045 + 047 + 049 가 먼저 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
--
-- ★004/011/012/044/045/047/049 의 루프변수-별칭 충돌 함정 유지: plpgsql record 변수 이름이 FOR 루프
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
    add constraint findings_taxonomy_version_v1v2v3v4v5v6v7v8v9_check
    check (taxonomy_version in (
      'grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3',
      'grm-finding-taxonomy/v4', 'grm-finding-taxonomy/v5', 'grm-finding-taxonomy/v6',
      'grm-finding-taxonomy/v7', 'grm-finding-taxonomy/v8',
      'grm-finding-taxonomy/v9'
    ));
end;
$$;

-- 검증: 현재 저장된 taxonomy_version 값 분포(v1~v7 외 값이 있으면 안 된다).
-- select distinct taxonomy_version from public.findings order by taxonomy_version;
-- 검증: 신규 제약이 존재하고 v1~v7 를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2v3v4v5v6v7v8v9_check';
-- 검증(재분류 후): 캐치올이 실제로 줄었는지 --
--   select count(*) from public.findings where category_code = 'other_quality_system';
-- 검증(구 v7 앵커): `of` 접착이 남긴 오배치가 사라졌는지 -- 기대값 0
--   select count(*) from public.findings
--    where finding_text ~* '\yofinvestigations?\y' and category_code = 'material_supplier_control';
