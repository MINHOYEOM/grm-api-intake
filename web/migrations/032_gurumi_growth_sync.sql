-- ============================================================================
-- 032_gurumi_growth_sync.sql — [구름이 서버 동기화] 로그인 사용자 성장 데이터 보관 테이블
--
-- 의미 계약:
--   - 구름이 성장 시스템 v1(growth.js localStorage 스키마 version:1)의 "사실"만 보관한다:
--     weeks = {"<YYYYWW>": {"idx": <절대 주번호>, "q": {"<qid>": 0|1}}}.
--     점수·단계·이름·스트릭 등 파생값은 저장하지 않는다 — 항상 클라이언트가 재계산한다
--     (growth.js 재계산 원칙 유지 — 서버·로컬 간 드리프트 0).
--   - 사용자당 1행(user_id PK, auth.users 재사용 — 001 관례·별도 user 테이블 없음).
--     클라이언트(growth-sync.js)는 pull → 병합(week×문항 union·정답 우선) → upsert 만
--     수행한다. 서버는 병합 로직을 갖지 않는다(저장소 역할만 — 병합 규칙은 클라이언트
--     단일 소스).
--   - 하이브리드 계약: 비로그인 사용자는 이 테이블과 무관하게 localStorage 만으로 완전
--     동작한다. 이 마이그레이션 적용 전에 클라이언트가 먼저 배포돼도 사이트는 완전
--     정상이다(테이블 부재 → PostgREST 오류 → growth-sync.js 조용한 로컬 폴백).
--
-- 접근 계약(불가침): RLS 본인 행만 select/insert/update(auth.uid() = user_id).
-- anon 은 무권한 — 공개 read 없음(001/015 revoke-전면회수 관례). delete 정책·권한은
-- 만들지 않는다 — "기록 초기화"는 로컬 UX(growth.js) 소관이고, 계정 보관본의 행 삭제
-- 경로 자체를 봉쇄한다(015 의 update 미부여와 같은 이중 봉쇄 사고방식).
--
-- 이 파일은 테이블·RLS 정책·권한·updated_at 터치 트리거만 추가한다. RPC·Edge Function·
-- 인덱스(PK 외)는 만들지 않는다.
-- ============================================================================

create table if not exists public.gurumi_growth (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  version    integer not null default 1,
  weeks      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  -- 스키마 버전 고정(v1) — growth.js SCHEMA_VERSION=1 과 동일 계약. 버전 승격은
  -- 별도 마이그레이션으로만(클라이언트 병합기가 v1 모양만 신뢰).
  constraint gurumi_growth_version_v1 check (version = 1),
  -- weeks 는 객체 모양만 허용(배열·스칼라 거부) + 남용 방어 크기 상한 64KB
  -- (015 의 행수 상한과 같은 목적 — 정확한 쿼터가 아니라 대량 적재 방어.
  --  주당 문항 수 소수 × 수년치도 수 KB 수준이라 정상 사용은 여유가 크다).
  constraint gurumi_growth_weeks_object check (jsonb_typeof(weeks) = 'object'),
  constraint gurumi_growth_weeks_cap check (pg_column_size(weeks) <= 65536)
);

comment on table public.gurumi_growth is
  '[구름이 서버 동기화 v1] 로그인 사용자의 성장 "사실"(weeks·q 맵, growth.js v1 스키마 '
  '그대로)만 보관한다. 점수·단계·이름 등 파생값은 저장하지 않는다(항상 클라이언트 '
  '재계산). RLS 본인 행만 select/insert/update — anon 무권한·delete 경로 없음. '
  '병합(union·정답 우선)은 클라이언트 growth-sync.js 단일 소스.';

alter table public.gurumi_growth enable row level security;

-- 본인 행만 읽기/생성/갱신 (auth.uid() = user_id). update 는 using + with check 양쪽
-- 고정 — 본인 행을 다른 user_id 로 바꿔치기하는 경로도 봉쇄한다.
drop policy if exists gurumi_growth_select_own on public.gurumi_growth;
drop policy if exists gurumi_growth_insert_own on public.gurumi_growth;
drop policy if exists gurumi_growth_update_own on public.gurumi_growth;
create policy gurumi_growth_select_own on public.gurumi_growth
  for select using (auth.uid() = user_id);
create policy gurumi_growth_insert_own on public.gurumi_growth
  for insert with check (auth.uid() = user_id);
create policy gurumi_growth_update_own on public.gurumi_growth
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- 권한: 전면 회수 후 authenticated 에 select/insert/update 만 명시 재부여
-- (001/015/031 revoke-전면회수 관례 — 프로젝트 기본 권한 설정과 무관하게 결정론 고정).
-- anon 재부여 없음(공개 read 없음). delete 미부여(위에서 delete 정책도 없음 — 이중 봉쇄).
revoke all on public.gurumi_growth from public;
revoke all on public.gurumi_growth from anon;
revoke all on public.gurumi_growth from authenticated;
grant select, insert, update on public.gurumi_growth to authenticated;

-- ============================================================================
-- updated_at 터치 트리거 — upsert 갱신 시 서버 시각으로 자동 기록(클라이언트가 시각을
-- 보내지 않는다 — 시계 신뢰 경계를 서버로 고정). 001/015 의 private 스키마 함수 관례를
-- 따르되 security definer 는 쓰지 않는다 — NEW 행만 만지는 터치 함수라 승격 권한이
-- 필요 없고, 최소 권한 원칙상 부여하지 않는 쪽이 안전하다(015 cap 트리거는 타 행
-- count 조회가 필요해 definer 였던 것과 대비).
-- ============================================================================

create schema if not exists private;  -- 001 이 이미 생성하지만 단독 적용에도 안전하게 멱등 재보장

create or replace function private.touch_gurumi_growth_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function private.touch_gurumi_growth_updated_at() from public;
revoke all on function private.touch_gurumi_growth_updated_at() from anon;
revoke all on function private.touch_gurumi_growth_updated_at() from authenticated;

drop trigger if exists gurumi_growth_touch_before_update on public.gurumi_growth;
create trigger gurumi_growth_touch_before_update
before update on public.gurumi_growth
for each row execute function private.touch_gurumi_growth_updated_at();

-- 검증(사람 실행용, 프로덕션 SQL Editor — 컨트롤 타워 라이브 dry-run):
-- 1) RLS 정책 3종(select/insert/update own)만 존재하고 delete 정책이 없는지:
--    select polname, polcmd from pg_policy
--    where polrelid = 'public.gurumi_growth'::regclass order by polname;
--    -- 기대: *_select_own(r) · *_insert_own(a) · *_update_own(w) 3행뿐(d 없음)
-- 2) anon 무권한·authenticated select/insert/update 만인지(delete 미부여):
--    select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema = 'public' and table_name = 'gurumi_growth' order by grantee, privilege_type;
-- 3) 제약 동작: version=2 insert → check 위반, weeks='[]'::jsonb → check 위반(object 만).
-- 4) 터치 트리거: update 후 updated_at 이 서버 now() 로 갱신되는지.
-- 5) 본인 행만 보이는지(다른 사용자 세션에서 select 시 자기 행만):
--    select count(*) from public.gurumi_growth;  -- 각 세션은 0 또는 1
