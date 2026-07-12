-- GRM 관심 업체 워치리스트(등록/해제/목록) — 저장 계층. Supabase(Postgres) SQL 편집기에서 1회 실행.
-- 이메일 통지 잡은 별도 후속 PR(이 파일은 저장 계층 절반만).
--
-- 001_reaction.sql 관례 계승(불가침):
--   · 사용자는 Supabase auth.users 재사용(별도 user 테이블 없음).
--   · RLS 본인 행만(select/insert/delete own — auth.uid() = user_id).
--   · 불투명 키만 저장 — firm_key 는 013_findings_firm_key.sql 의 정규화 키(불투명 앵커)다.
--     업체 사실·지적사항 원문·원문 URL 은 저장/전송하지 않는다(001 provenance 관례).
--   · firm_display 는 등록 시점의 "표시명 스냅샷"(마이페이지 목록 렌더용 라벨)일 뿐,
--     규제 사실 데이터가 아니다 — 표시명이 이후 바뀌어도 갱신하지 않는다(update 정책 자체가 없다).
--
-- 전제: 013_findings_firm_key.sql(firm_key 정규화·프로파일 RPC)이 먼저 적용되어 있어야
-- 웹 UI(firm.js)가 유효한 firm_key 를 넘겨준다. 다만 이 테이블은 findings 테이블과 FK 로
-- 묶지 않는다 — firm_key 는 generated 컬럼 값이라 참조 대상 단일 행이 없고, 업체가 아직
-- findings 에 없어도(신규 관심 업체) 등록 자체는 유효한 사용 시나리오다.

create table if not exists public.firm_watchlist (
  user_id      uuid not null references auth.users(id) on delete cascade,
  firm_key     text not null,                -- 불투명 정규화 키(= 013 findings.firm_key)
  firm_display text not null default '',     -- 등록 시점 표시명 스냅샷(목록 렌더용)
  created_at   timestamptz not null default now(),
  primary key (user_id, firm_key)
);

alter table public.firm_watchlist enable row level security;

-- 본인 행만 읽기/등록/해제 (001 과 동일한 3종 정책 — RLS 로 DB 레벨 강제).
-- update 정책은 만들지 않는다(등록/해제만 있는 모델 — 수정 경로 자체를 봉쇄).
drop policy if exists firm_watchlist_select_own on public.firm_watchlist;
drop policy if exists firm_watchlist_insert_own on public.firm_watchlist;
drop policy if exists firm_watchlist_delete_own on public.firm_watchlist;
create policy firm_watchlist_select_own on public.firm_watchlist for select using (auth.uid() = user_id);
create policy firm_watchlist_insert_own on public.firm_watchlist for insert with check (auth.uid() = user_id);
create policy firm_watchlist_delete_own on public.firm_watchlist for delete using (auth.uid() = user_id);

-- 권한: anon 에는 아무 권한 없음(001 reaction_count 의 revoke-전면회수 관례).
-- authenticated 에도 select/insert/delete 만 명시적으로 재부여한다(update 미부여 —
-- 위에서 update 정책도 없으므로 이중 봉쇄).
revoke all on public.firm_watchlist from public;
revoke all on public.firm_watchlist from anon;
revoke all on public.firm_watchlist from authenticated;
grant select, insert, delete on public.firm_watchlist to authenticated;

-- ============================================================================
-- 남용 방어: 사용자당 상한 50개 — before insert 트리거로 count 검사, 초과 시
-- raise exception(메시지에 상한 명시). 001 의 private 스키마 트리거 함수 관례
-- (security definer + set search_path = public + 함수 execute 전면 회수)를 그대로 따른다.
--
-- ★004 함정(plpgsql 루프변수-별칭 충돌) 해당 없음 사실 명시: 이 트리거 함수에는
-- FOR 루프도 declare 별칭도 전혀 없다(단일 if-count 검사 하나뿐) — 004 류 충돌 경로
-- 자체가 존재하지 않는다.
--
-- 동시성 주(설계 의도): count 기반 검사라 동시 insert 경합 시 상한을 1~2개 스치듯
-- 넘길 이론적 여지가 있으나, 이 상한은 정확한 쿼터가 아니라 남용(수천 행 적재) 방어가
-- 목적이므로 충분하다 — 직렬화 잠금으로 등록 UX 를 느리게 만들지 않는다.
-- ============================================================================

create schema if not exists private;  -- 001 이 이미 생성하지만 단독 적용에도 안전하게 멱등 재보장

create or replace function private.enforce_firm_watchlist_cap()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if (select count(*) from public.firm_watchlist where user_id = new.user_id) >= 50 then
    raise exception '관심 업체는 사용자당 최대 50개까지 등록할 수 있습니다 (firm_watchlist cap = 50)';
  end if;
  return new;
end;
$$;

revoke all on function private.enforce_firm_watchlist_cap() from public;
revoke all on function private.enforce_firm_watchlist_cap() from anon;
revoke all on function private.enforce_firm_watchlist_cap() from authenticated;

drop trigger if exists firm_watchlist_cap_before_insert on public.firm_watchlist;
create trigger firm_watchlist_cap_before_insert
before insert on public.firm_watchlist
for each row execute function private.enforce_firm_watchlist_cap();

-- 검증(사람 실행용, 프로덕션 SQL Editor — 컨트롤 타워 라이브 dry-run):
-- 1) RLS 정책 3종(select/insert/delete own)만 존재하고 update 정책이 없는지:
--    select polname, polcmd from pg_policy
--    where polrelid = 'public.firm_watchlist'::regclass order by polname;
-- 2) anon 무권한·authenticated select/insert/delete 만인지:
--    select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema = 'public' and table_name = 'firm_watchlist' order by grantee, privilege_type;
-- 3) 상한 트리거 동작(테스트 사용자로 50행 적재 후 51번째 insert 가 예외로 거부되는지):
--    insert 51st → ERROR: 관심 업체는 사용자당 최대 50개까지 등록할 수 있습니다
-- 4) 본인 행만 보이는지(다른 사용자 세션에서 select 시 0행):
--    select count(*) from public.firm_watchlist;  -- 각 세션은 자기 행 수만
