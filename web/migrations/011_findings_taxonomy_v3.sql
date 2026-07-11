-- FIND-1 taxonomy v3 이벤트 -- 이미 살아있는 public.findings 의 taxonomy_version
-- CHECK((v1, v2) IN-list, 004_findings_taxonomy_v2.sql 참조)를 (v1, v2, v3) IN-list 로
-- 확장한다. v1/v2 로 이미 저장된 행은 그대로 보존한다(provenance) -- 이 마이그레이션은
-- 기존 행을 재분류하지 않는다(재분류는 별도 findings_reclassify_service.py 가 담당).
-- 전제: 002_findings.sql(fresh-install 정본) + 004_findings_taxonomy_v2.sql 이 먼저
-- 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
--
-- ★004 의 루프변수-별칭 충돌 함정 재확인: plpgsql record 변수 이름이 FOR 루프 쿼리
-- 내부에서 쓰는 테이블 별칭과 같으면 Postgres 가 `별칭.컬럼` 을 SQL 별칭이 아니라
-- 아직 할당되지 않은 plpgsql record 로 해석해 "ERROR 55000: record ... is not assigned
-- yet" 을 낸다(004 를 라이브 적용할 때 con/con 충돌로 실측됨). 아래는 루프 변수를
-- `loop_rec`, pg_constraint 별칭을 `con` 으로 서로 다르게 유지해 그 함정을 재확인 없이
-- 되풀이하지 않는다.
--
-- ★009 의 배열 슬라이스 괄호 함정은 이 마이그레이션에 해당 사항 없음: 이 파일은 배열
-- 슬라이스(`expr[a:b]`) 구문을 전혀 사용하지 않는다 -- DO 블록의 pg_constraint 조회와
-- 단일 taxonomy_version 컬럼 CHECK 재작성뿐이다.
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
    add constraint findings_taxonomy_version_v1v2v3_check
    check (taxonomy_version in (
      'grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3'
    ));
end;
$$;

-- 검증: 현재 저장된 taxonomy_version 값 분포(v1/v2/v3 외 값이 있으면 안 된다).
-- select distinct taxonomy_version from public.findings order by taxonomy_version;
-- 검증: 신규 제약이 존재하고 v1/v2/v3 를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2v3_check';
