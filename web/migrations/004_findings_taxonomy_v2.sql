-- FIND-1 M5b taxonomy v2 이벤트 — 이미 살아있는 public.findings 의 taxonomy_version
-- CHECK(v1 등호)를 (v1, v2) IN-list 로 확장한다. v1 로 이미 저장된 행은 그대로 보존한다
-- (provenance) — 이 마이그레이션은 기존 행을 재분류하지 않는다.
-- 전제: 002_findings.sql(fresh-install 정본)이 먼저 적용되어 있어야 한다.

-- 컬럼 인라인 CHECK 는 Postgres 가 자동 생성한 제약 이름에 의존할 수 없으므로,
-- pg_constraint 를 조회해 public.findings 의 taxonomy_version 컬럼만 참조하는 CHECK
-- 제약을 전부 찾아 drop 한 뒤, 명명된 제약을 새로 추가한다. 재실행해도 멱등하다
-- (동일 이름 제약이 이미 있으면 drop 후 재생성).
do $$
declare
  con record;
begin
  for con in
    select con.conname
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace nsp on nsp.oid = rel.relnamespace
    where nsp.nspname = 'public'
      and rel.relname = 'findings'
      and con.contype = 'c'
      and pg_get_constraintdef(con.oid) like '%taxonomy_version%'
  loop
    execute format('alter table public.findings drop constraint %I', con.conname);
  end loop;

  alter table public.findings
    add constraint findings_taxonomy_version_v1v2_check
    check (taxonomy_version in ('grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2'));
end;
$$;

-- 검증: 현재 저장된 taxonomy_version 값 분포(v1/v2 외 값이 있으면 안 된다).
-- select distinct taxonomy_version from public.findings order by taxonomy_version;
-- 검증: 신규 제약이 존재하고 v1/v2 를 모두 허용하는지 확인.
-- select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2_check';
