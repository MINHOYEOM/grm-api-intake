-- GRM 워치리스트 주간 이메일 통지(T-WL1) — 재통지 방지 로그. Supabase(Postgres) SQL 편집기에서
-- 1회 실행. 저장 계층은 015_firm_watchlist.sql(등록/해제/목록) 완료·라이브 — 이 파일은 그 후속
-- (통지 잡)이 재실행/재스케줄 되어도 같은 (user, finding) 쌍을 두 번 보내지 않도록 하는 멱등
-- 기록층만 추가한다. 이메일 발송 본체(watchlist_notify_service.py)와 워크플로
-- (.github/workflows/grm-watchlist-notify.yml)는 별도 파일 — 이 파일은 스키마만.
--
-- 설계:
--   · service_role 전용 테이블 — 사람(anon/authenticated) 은 이 테이블을 절대 직접 읽거나
--     쓰지 않는다. 유일한 접근자는 watchlist_notify_service.py(GitHub Actions, service-role
--     키)뿐이다. 그래서 001/015 처럼 "본인 행만" RLS 정책 3종을 만들 필요가 없다 — 애초에
--     클라이언트가 접근할 이유 자체가 없으므로 정책 0개 + 전 클라이언트 역할 무권한으로
--     전면 차단한다(002_findings.sql 의 raw_signals/findings 와 같은 종류의 "service_role
--     전용 테이블" 패턴 — 001/015 는 "본인 행 공개" 패턴이라 이 표와는 다르다. 여기서
--     재사용하는 것은 001/015 의 "revoke all from public/anon/authenticated" 전면회수
--     관례 그 자체이지, 본인-행 정책 패턴이 아니다).
--   · user_id -> auth.users(id) on delete cascade: 001/015 와 동일 관례 — 계정 삭제 시
--     통지 로그도 함께 정리된다(고아 행 방지).
--   · finding_id -> public.findings(finding_id) on delete cascade: finding_id 는 언제나
--     통지 시점에 실제로 존재하는 findings 행에서 가져온 값이라(watchlist_notify_service.py
--     가 findings 쿼리 결과에서만 finding_id 를 얻는다) FK 로 묶어도 안전하고, 드물게
--     findings 행이 재분류/삭제되면 로그도 함께 정리되어 별도 청소가 필요 없다.
--   · primary key (user_id, finding_id): 이 쌍이 곧 멱등 키다 — 같은 사용자에게 같은
--     finding 을 두 번 통지하지 않는다(INSERT ... ON CONFLICT DO NOTHING 으로 서비스가
--     이 제약을 그대로 활용한다. PostgREST 로는 `Prefer: resolution=ignore-duplicates`).
--
-- ============================================================================
-- ★004 함정(plpgsql 루프변수-별칭 충돌) 해당 없음: 이 파일은 plpgsql 함수/DO 블록을 전혀
-- 쓰지 않는다 — 테이블 DDL + revoke + 인덱스뿐이므로 004 류 충돌 경로 자체가 없다.
--
-- ★009 함정(anon PostgREST 조회가 RLS/게이트에 가려 대상을 못 봄) 해당 없음: 009 의 문제는
-- "번역 파이프라인이 anon 키로 findings_translation_queue 를 조회해야 하는데 006 공개
-- 게이트가 미번역 행을 anon 에게서 숨겨 0건으로 보이는" 구조였다. 이 표는 애초에 anon/
-- authenticated 가 조회할 일이 없다(유일한 소비자가 service_role 키를 쓰는
-- watchlist_notify_service.py 뿐) — RLS 정책이 0개이므로 "정책이 뭔가를 가린다"는 009 류
-- 결함 경로 자체가 존재하지 않는다(service_role 은 RLS 를 항상 우회하므로 정책 유무와
-- 무관하게 전량 조회/삽입 가능).
-- ============================================================================
--
-- 전제: 015_firm_watchlist.sql(워치리스트 저장 계층) + 013_findings_firm_key.sql(firm_key)
-- 가 먼저 적용되어 있어야 한다(통지 서비스가 firm_watchlist.firm_key 로 findings.firm_key
-- 를 매칭하므로). 이 파일은 001/002/013/015 의 기존 테이블·정책·함수를 전혀 건드리지 않는다.

create table if not exists public.firm_watch_notification_log (
  user_id    uuid not null references auth.users(id) on delete cascade,
  finding_id text not null references public.findings(finding_id) on delete cascade,
  sent_at    timestamptz not null default now(),
  primary key (user_id, finding_id)
);

alter table public.firm_watch_notification_log enable row level security;

-- 정책 0개(의도적) — service_role 전용 테이블이라 어떤 client 역할에도 행을 노출할 필요가
-- 없다. RLS 만 켜두고 정책을 만들지 않으면 anon/authenticated 는 항상 0행을 본다(설령
-- 아래 revoke 를 깜빡하더라도 이중 방어). service_role 은 RLS 우회 대상이라 무관.

-- 권한: 001/015 의 revoke-전면회수 관례 그대로 — anon/authenticated 는 이 표에 어떤 권한도
-- 갖지 않는다(select/insert/update/delete 전부 0). service_role 은 Supabase 기본 설정상
-- public 스키마 테이블에 이미 접근 가능하므로 별도 grant 가 필요 없다(002 의 raw_signals/
-- findings 와 동일 — service_role 을 향한 명시적 grant 문 자체가 없다).
revoke all on public.firm_watch_notification_log from public;
revoke all on public.firm_watch_notification_log from anon;
revoke all on public.firm_watch_notification_log from authenticated;

-- 조회 성능: watchlist_notify_service.py 는 이번 실행의 후보 finding_id 목록으로 "이미
-- 통지됨" 여부를 조회한다(finding_id=in.(...) — PK 의 선두 컬럼이 아닌 finding_id 단독
-- 조회이므로 보조 인덱스가 필요하다).
create index if not exists firm_watch_notification_log_finding_idx
  on public.firm_watch_notification_log (finding_id);

-- 검증(사람 실행용, 프로덕션 SQL Editor — 컨트롤 타워 라이브 dry-run):
-- 1) RLS 는 켜져 있고 정책은 0개인지:
--    select relrowsecurity from pg_class where oid = 'public.firm_watch_notification_log'::regclass;  -- true
--    select count(*) from pg_policy where polrelid = 'public.firm_watch_notification_log'::regclass;  -- 0
-- 2) anon/authenticated 무권한인지:
--    select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema = 'public' and table_name = 'firm_watch_notification_log'
--      and grantee in ('anon', 'authenticated');  -- 0행 기대
-- 3) 멱등 PK 가 실제로 중복 삽입을 막는지(같은 (user_id, finding_id) 재삽입 시 ON CONFLICT
--    DO NOTHING 이 조용히 무시하는지):
--    insert into public.firm_watch_notification_log (user_id, finding_id) values ('<uuid>', '<id>')
--    on conflict (user_id, finding_id) do nothing;  -- 두 번째 실행부터 영향받은 행 0
-- 4) anon 세션에서 0행만 보이는지(RLS 정책 0개 확인):
--    set role anon; select count(*) from public.firm_watch_notification_log;  -- 0 (또는 권한 오류)
--    reset role;
