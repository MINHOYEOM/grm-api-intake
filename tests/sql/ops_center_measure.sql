\set ON_ERROR_STOP on
-- 운영 센터 128(자동 측정 SQL 갈래) 행동 시험. 별도 빈 테스트 DB 에서만 실행한다(실서비스 SQL 에디터에 붙이지 않는다).
-- 바탕 표(findings·raw_signals·finding_clause·finding_term·newsletter_subscribers_daily)는 측정이 읽는 열만 흉내 낸다.
-- CI: .github/workflows/grm-ops-center-sql-check.yml · 뮤테이션: tests/run_ops_center_sql_mutations.py
begin;

-- ── Supabase 흉내(ops_center_decide.sql 과 같은 바탕) ─────────────────────────
create role anon;
create role authenticated;
create role service_role bypassrls;
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('test.uid', true), '')::uuid
$$;
create schema private;
grant usage on schema private to authenticated;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
alter default privileges in schema private grant execute on functions to anon, authenticated;
create function private.is_admin() returns boolean language sql stable as $$
  select case current_setting('test.is_admin', true) when 'on' then true when 'null' then null else false end
$$;
grant execute on function private.is_admin() to authenticated;
create table public.admin_audit_log (
  id bigint generated always as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null, target_type text, target_id text,
  details jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());

-- 측정이 읽는 표(002·010·077·106·107 의 해당 열만)
create table public.raw_signals (raw_signal_id text primary key, ingested_at timestamptz not null default now());
create table public.findings (
  finding_id text primary key,
  source text not null, agency text not null, document_type text not null default 'x',
  finding_text_ko text not null default '', finding_language text,
  scope_status text not null default 'ok', review_status text not null default 'accepted',
  ingested_at timestamptz not null default now());
create table public.finding_clause (finding_id text not null references public.findings(finding_id) on delete cascade,
  citation_key text not null, primary key (finding_id, citation_key));
create table public.finding_term (finding_id text not null references public.findings(finding_id) on delete cascade,
  term_id text not null, primary key (finding_id, term_id));
create table public.newsletter_subscribers_daily (snap_date date not null, list_id integer not null,
  total_subscribers integer not null, primary key (snap_date, list_id));
-- 087 funnel_touch_report 흉내: 시험 표에서 by_channel·total_submits 를 같은 모양으로
create table public.t_funnel (channel text primary key, submits integer not null);
create function public.funnel_touch_report() returns jsonb language sql stable as $$
  select jsonb_build_object(
    'by_channel', coalesce((select jsonb_agg(jsonb_build_object('channel', channel, 'submits', submits)
                                             order by submits desc, channel) from public.t_funnel), '[]'::jsonb),
    'total_submits', coalesce((select sum(submits) from public.t_funnel), 0),
    'first_kst', '2026-09-23')
$$;

\ir ../../web/migrations/122_ops_center_tables.sql
\ir ../../web/migrations/123_ops_home.sql
\ir ../../web/migrations/124_ops_center_seed.sql
\ir ../../web/migrations/125_ops_decision_write.sql
\ir ../../web/migrations/127_ops_center_seed_planning.sql
\ir ../../web/migrations/128_ops_measure.sql

create function pg_temp.assert_true(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;
create function pg_temp.assert_eq(got text, want text, message text) returns void language plpgsql as $$
begin
  if got is distinct from want then
    raise exception 'assertion failed: % (got %, want %)', message, coalesce(got, 'NULL'), coalesce(want, 'NULL');
  end if;
end $$;
create function pg_temp.v(p_slug text) returns text language sql as $$
  select trim_scale(current_value)::text from public.ops_goal where slug = p_slug
$$;
create function pg_temp.n(p_slug text) returns text language sql as $$
  select note from public.ops_goal where slug = p_slug
$$;

-- ── 1. 권한 ─────────────────────────────────────────────────────────────────
select pg_temp.assert_true(not has_function_privilege('anon', 'public.ops_measure_all()', 'EXECUTE'), 'anon cannot run ops_measure_all');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'public.ops_measure_all()', 'EXECUTE'), 'members cannot run ops_measure_all');
select pg_temp.assert_true(has_function_privilege('service_role', 'public.ops_measure_all()', 'EXECUTE'), 'service_role may run ops_measure_all');
select pg_temp.assert_true((select prosecdef from pg_proc where oid = 'public.ops_measure_all()'::regprocedure), 'ops_measure_all is security definer');
select pg_temp.assert_true((select proconfig::text like '%search_path=public%' from pg_proc where oid = 'public.ops_measure_all()'::regprocedure), 'ops_measure_all fixes search_path');
do $$
declare f record;
begin
  for f in select p.oid::regprocedure::text as sig, p.prosecdef, p.proconfig
             from pg_proc p join pg_namespace s on s.oid = p.pronamespace
            where s.nspname = 'private' and (p.proname like 'ops\_m\_%' or p.proname = 'ops_measure_all_at') loop
    perform pg_temp.assert_true(not has_function_privilege('anon', f.sig, 'EXECUTE'), 'anon cannot call ' || f.sig);
    perform pg_temp.assert_true(not has_function_privilege('authenticated', f.sig, 'EXECUTE'), 'authenticated cannot call ' || f.sig);
    perform pg_temp.assert_true(not f.prosecdef, f.sig || ' is invoker');
    perform pg_temp.assert_true(f.proconfig::text like '%search_path=public%', f.sig || ' fixes search_path');
  end loop;
end $$;
select pg_temp.assert_eq((select count(*)::text from pg_proc p join pg_namespace s on s.oid = p.pronamespace
                          where s.nspname = 'private' and p.proname like 'ops\_m\_%'), '11', 'measure helpers 11');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'private.ops_measure_log', 'SELECT'), 'members cannot read the log');
select pg_temp.assert_true(not has_table_privilege('anon', 'private.ops_measure_log', 'SELECT'), 'anon cannot read the log');

-- ── 2. 시드: 새 목표 3 · measure_ref · 수동 목표 불변 ──────────────────────────
select pg_temp.assert_eq((select count(*)::text from public.ops_goal), '29', 'goals 26 + 3');
select pg_temp.assert_eq((select string_agg(slug || ':' || unit || ':' || direction || ':' || coalesce(trim_scale(target)::text, '-') || ':'
                                 || coalesce(trim_scale(warn_at)::text, '-') || ':' || coalesce(trim_scale(bad_at)::text, '-') || ':'
                                 || period_hours || ':' || measure_kind, ',' order by slug)
                          from public.ops_goal where slug in ('infra-db-size','growth-sub-top','pr-subs-day')),
                         'growth-sub-top:%:gte:-:-:-:24:sql,infra-db-size:MB:lte:400:400:450:24:sql,pr-subs-day:명:gte:-:-:-:24:sql',
                         'new goal lines');
select pg_temp.assert_eq((select label from public.ops_goal where slug = 'infra-db-size'), '무료 DB 사용량', 'db size label');
select pg_temp.assert_eq((select string_agg(slug || '=' || measure_ref, ',' order by slug) from public.ops_goal where measure_kind = 'sql'),
  'dq-clause-mfds=private.ops_m_clause_pct_mfds,dq-clause-wl=private.ops_m_clause_pct_wl,dq-term-min=private.ops_m_term_min,'
  || 'growth-sub-top=private.ops_m_funnel_top_share,infra-db-size=private.ops_m_db_size_mb,infra-ingest-age=private.ops_m_ingest_age,'
  || 'pr-subs-day=private.ops_m_subs_daily_delta,tr-backlog=private.ops_m_tr_backlog', 'sql measure refs');
select pg_temp.assert_eq((select string_agg(slug, ',' order by slug) from public.ops_goal
                          where measure_kind = 'github' and measure_ref = 'web/ops_measure_github.py'),
                         'dev-ci,dev-link-streak,dev-pr-old,edu-terms-week,infra-deploy-fail,sr-ontime', 'github refs');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal where measure_kind = 'manual' and measure_ref is not null),
                         '0', 'manual goals untouched');
create temp table manual_before as
  select slug, current_value, measured_at, note from public.ops_goal where measure_kind = 'manual';

-- ── 3. 바탕 데이터 ─────────────────────────────────────────────────────────
-- 경고서한 10건 중 7건 연결(2건은 조항 2개 → distinct 로 7)
insert into public.findings (finding_id, source, agency)
  select 'wl' || i, 'FDA Warning Letter', 'FDA' from generate_series(1, 10) i;
insert into public.finding_clause select 'wl' || i, 'k1' from generate_series(1, 7) i;
insert into public.finding_clause values ('wl1', 'k2'), ('wl2', 'k2');
-- 식약처(agency) 4건 중 2건
insert into public.findings (finding_id, source, agency, document_type)
  values ('m1', 'MFDS', 'MFDS', 'gmp'), ('m2', 'MFDS', 'MFDS', 'gmp'), ('m3', 'MFDS', 'MFDS', 'gmp'), ('m4', 'MFDS', 'MFDS', 'gmp');
insert into public.finding_clause values ('m1', 'k1'), ('m3', 'k1');
-- 용어: 483 33건(아래 미번역 3건 포함) 중 12(36.4%) · 캐나다 25건 중 20(80%) · 경고서한 10건(표본 부족 → 제외) · 식약처 4건(제외)
insert into public.findings (finding_id, source, agency, finding_text_ko)
  select 'f' || i, 'FDA 483', 'FDA', '번역' from generate_series(1, 30) i;
insert into public.finding_term select 'f' || i, 't1' from generate_series(1, 12) i;
insert into public.finding_term values ('f1', 't2');
insert into public.findings (finding_id, source, agency, finding_text_ko)
  select 'hc' || i, 'Health Canada Inspection', 'HC', '번역' from generate_series(1, 25) i;
insert into public.finding_term select 'hc' || i, 't1' from generate_series(1, 20) i;
-- 미번역: 483·경고서한 한글 없음. 3일 넘은 것 3건(경고서한 2·483 1), 최근 1건, rejected·scope 밖·식약처는 안 셈
update public.findings set ingested_at = now() - interval '5 days' where finding_id in ('wl3', 'wl4');
update public.findings set ingested_at = now() - interval '1 day' where finding_id = 'wl5';
insert into public.findings (finding_id, source, agency, ingested_at) values ('f-old', 'FDA 483', 'FDA', now() - interval '4 days');
insert into public.findings (finding_id, source, agency, review_status, ingested_at)
  values ('f-rej', 'FDA 483', 'FDA', 'rejected', now() - interval '9 days');
insert into public.findings (finding_id, source, agency, scope_status, ingested_at)
  values ('f-scope', 'FDA 483', 'FDA', 'non_pharma', now() - interval '9 days');
update public.findings set ingested_at = now() - interval '10 days' where finding_id = 'm2';
update public.findings set finding_text_ko = '번역' where source = 'FDA Warning Letter' and finding_id not in ('wl3','wl4','wl5');
-- 적재: 마지막 10시간 전
insert into public.raw_signals values ('r1', now() - interval '30 hours'), ('r2', now() - interval '10 hours');
-- 구독자: 어제 13 → 오늘 15(목록 3) · 옛 목록 2 는 무관
insert into public.newsletter_subscribers_daily values
  ((now() at time zone 'Asia/Seoul')::date - 1, 3, 13), ((now() at time zone 'Asia/Seoul')::date, 3, 15),
  ((now() at time zone 'Asia/Seoul')::date - 30, 2, 1);
-- 구독 신청 경로: 23건 중 네이버 11
insert into public.t_funnel values ('naver', 11), ('google', 5), ('direct', 7);

-- ── 4. 첫 측정 ───────────────────────────────────────────────────────────────
create temp table r1 as select public.ops_measure_all() as r;
select pg_temp.assert_eq((select jsonb_array_length(r -> 'updated')::text from r1), '8', 'all 8 sql goals updated');
select pg_temp.assert_eq((select (r -> 'failed')::text from r1), '[]', 'nothing failed');
select pg_temp.assert_eq(pg_temp.v('dq-clause-wl'), '70', 'WL 7/10 distinct');
select pg_temp.assert_eq(pg_temp.n('dq-clause-wl'), '70.0% (10건 중 7건) · 경고서한 전체 기준', 'WL note has num/den');
select pg_temp.assert_eq(pg_temp.v('dq-clause-mfds'), '50', 'MFDS 2/4');
select pg_temp.assert_true(pg_temp.n('dq-clause-mfds') like '50.0% (4건 중 2건)%', 'MFDS note');
select pg_temp.assert_eq(pg_temp.v('dq-term-min'), '36.4', 'lowest type 483 = 12/33');
select pg_temp.assert_eq(pg_temp.n('dq-term-min'), '483 36.4% (33건 중 12건) · 다음 캐나다 80.0% · 유형 2개(20건 미만 제외)', 'term note');
select pg_temp.assert_eq(pg_temp.v('infra-ingest-age'), '10', 'ingest age 10h');
select pg_temp.assert_true(pg_temp.n('infra-ingest-age') like '마지막 적재 % KST (raw_signals 최신)', 'ingest note');
select pg_temp.assert_eq(pg_temp.v('tr-backlog'), '3', 'WL+483 untranslated older than 3 days');
select pg_temp.assert_true(pg_temp.n('tr-backlog') like '3일 넘은 미번역 3건 · 전체 미번역 4건 · 정의:%', 'backlog note has definition');
select pg_temp.assert_eq(pg_temp.v('pr-subs-day'), '2', 'subscribers +2');
select pg_temp.assert_true(pg_temp.n('pr-subs-day') like '% 15명 · % 13명 대비 · Brevo 목록 스냅샷 차', 'subs note');
select pg_temp.assert_eq(pg_temp.v('growth-sub-top'), '47.8', 'naver 11/23');
select pg_temp.assert_eq(pg_temp.n('growth-sub-top'), '네이버 47.8% (23건 중) · 2위 직접 30.4% · 2026-09-23 이후 누적', 'funnel note');
select pg_temp.assert_true((select current_value > 0 from public.ops_goal where slug = 'infra-db-size'), 'db size > 0');
select pg_temp.assert_true(pg_temp.n('infra-db-size') like 'DB %MB · 무료 한도 500MB 의 %', 'db size note');
select pg_temp.assert_true((select bool_and(measured_at = now()) from public.ops_goal where measure_kind = 'sql'), 'measured_at = run time');
select pg_temp.assert_true((select bool_and(current_value between 0 and 100) from public.ops_goal where measure_kind = 'sql' and unit = '%'),
                           'percent values in 0..100');
select pg_temp.assert_true((select bool_and(char_length(note) <= 120) from public.ops_goal), 'notes fit 120');
select pg_temp.assert_eq((select count(*)::text from private.ops_measure_log where status = 'ok'), '8', 'log one row per goal');
select pg_temp.assert_true((select bool_and(ms >= 0 and ms < 5000) from private.ops_measure_log), 'each measure well under 5s');
select pg_temp.assert_true(not exists (select 1 from manual_before b join public.ops_goal g using (slug)
                                        where (g.current_value, g.measured_at, g.note) is distinct from (b.current_value, b.measured_at, b.note)),
                           'manual goals untouched by measuring');

-- ── 5. 분모 0 → 측정 보류(값 null 이 아니라 이전 값 유지 · 메모만) ────────────────
update public.ops_goal set measured_at = now() - interval '1 hour' where measure_kind = 'sql';
delete from public.findings where source = 'FDA Warning Letter';
delete from public.newsletter_subscribers_daily where list_id = 3 and snap_date < (now() at time zone 'Asia/Seoul')::date;
delete from public.t_funnel;
create temp table r2 as select private.ops_measure_all_at(now()) as r;
select pg_temp.assert_eq((select (r -> 'held')::text from r2), '["dq-clause-wl", "growth-sub-top", "pr-subs-day"]', 'held list');
select pg_temp.assert_eq(pg_temp.v('dq-clause-wl'), '70', 'held goal keeps previous value');
select pg_temp.assert_true((select measured_at = now() - interval '1 hour' from public.ops_goal where slug = 'dq-clause-wl'), 'held goal keeps measured_at');
select pg_temp.assert_true(pg_temp.n('dq-clause-wl') like '측정 보류 __-__: 분모 0 · 경고서한 전체 기준', 'hold note');
select pg_temp.assert_true(pg_temp.n('pr-subs-day') like '측정 보류 __-__: 비교할 전날 스냅샷 없음', 'subs hold note');
select pg_temp.assert_true(pg_temp.n('growth-sub-top') like '측정 보류 __-__: 구독 신청 0건(분모 0)', 'funnel hold note');
select pg_temp.assert_eq(pg_temp.v('growth-sub-top'), '47.8', 'funnel keeps previous value');
select pg_temp.assert_true((select measured_at = now() from public.ops_goal where slug = 'dq-clause-mfds'), 'other goals still measured');
-- 보류가 이어지면 값이 낡아 자연 강등(123 규칙 그대로)
select pg_temp.assert_eq((select private.ops_goal_state(direction, target, warn_at, bad_at, current_value, measured_at, period_hours, now() + interval '36 hours')
                          from public.ops_goal where slug = 'dq-clause-wl'), 'unknown', 'held value goes stale after 1.5x');
-- 스냅샷이 2일 넘게 낡으면 보류(증감 0 으로 덮지 않는다)
insert into public.newsletter_subscribers_daily values
  ((now() at time zone 'Asia/Seoul')::date - 6, 3, 10), ((now() at time zone 'Asia/Seoul')::date - 5, 3, 11);
delete from public.newsletter_subscribers_daily where snap_date = (now() at time zone 'Asia/Seoul')::date;
select pg_temp.assert_true((private.ops_m_subs_daily_delta(now()) -> 'value') = 'null'::jsonb, 'stale snapshot holds');
-- 분모 20건 미만 유형만 남으면 보류
select pg_temp.assert_true((private.ops_m_term_min(now()) ->> 'value') = '36.4', 'term min still 483');
select pg_temp.assert_true((private.ops_m_ratio(0, 0, 'x') -> 'value') = 'null'::jsonb, 'ratio 0/0 is hold, not 0 or null-value write');

-- ── 6. 실패 · 시간 초과 · 범위 밖 → 이전 값 유지, 나머지는 계속 ─────────────────────
update public.ops_goal set measured_at = now() - interval '1 hour' where measure_kind = 'sql';
create or replace function private.ops_m_tr_backlog(p_now timestamptz) returns jsonb language plpgsql
  set search_path = public as $$ begin raise exception 'boom'; end $$;
create or replace function private.ops_m_ingest_age(p_now timestamptz) returns jsonb language plpgsql
  set search_path = public as $$ begin raise exception 'slow' using errcode = '57014'; end $$;
create or replace function private.ops_m_clause_pct_mfds(p_now timestamptz) returns jsonb language sql
  set search_path = public as $$ select jsonb_build_object('value', 150, 'note', 'x') $$;
create temp table r3 as select private.ops_measure_all_at(now()) as r;
select pg_temp.assert_eq((select string_agg(e ->> 'slug' || ':' || (e ->> 'status'), ',' order by e ->> 'slug')
                          from r3, jsonb_array_elements(r -> 'failed') e),
                         'dq-clause-mfds:error,infra-ingest-age:timeout,tr-backlog:error', 'failures listed');
select pg_temp.assert_eq(pg_temp.v('tr-backlog'), '1', 'failed goal keeps value (1 after WL rows were deleted in 5)');
select pg_temp.assert_eq(pg_temp.v('infra-ingest-age'), '10', 'timed-out goal keeps value');
select pg_temp.assert_eq(pg_temp.v('dq-clause-mfds'), '50', 'out-of-range percent rejected, value kept');
select pg_temp.assert_true((select bool_and(measured_at = now() - interval '1 hour') from public.ops_goal
                            where slug in ('tr-backlog','infra-ingest-age','dq-clause-mfds')), 'failed goals keep measured_at');
select pg_temp.assert_true(pg_temp.n('tr-backlog') like '측정 실패 __-__: P0001 boom', 'failure note');
select pg_temp.assert_true(pg_temp.n('infra-ingest-age') like '측정 실패 __-__: 시간 초과', 'timeout note');
select pg_temp.assert_true((select bool_and(measured_at = now()) from public.ops_goal where slug in ('dq-term-min','infra-db-size')),
                           'one failure does not stop the rest');
select pg_temp.assert_eq((select string_agg(status, ',' order by status) from private.ops_measure_log where status <> 'ok' and slug in ('tr-backlog','infra-ingest-age')),
                         'error,timeout', 'failures logged');
-- 실제 statement_timeout: 문장 시작에 건 타이머가 측정 중에 울리면 그 목표만 시간 초과, 나머지는 계속
create or replace function private.ops_m_ingest_age(p_now timestamptz) returns jsonb language plpgsql
  set search_path = public as $$ begin perform pg_sleep(3); return jsonb_build_object('value', 1, 'note', 'late'); end $$;
update public.ops_goal set measure_ref = 'private.ops_m_ingest_age' where slug = 'infra-ingest-age';
set local statement_timeout = '700ms';
create temp table r4 as select private.ops_measure_all_at(now()) as r;
set local statement_timeout = 0;
select pg_temp.assert_eq((select e ->> 'status' from r4, jsonb_array_elements(r -> 'results') e where e ->> 'slug' = 'infra-ingest-age'),
                         'timeout', 'real statement_timeout is caught');
select pg_temp.assert_eq(pg_temp.v('infra-ingest-age'), '10', 'timed-out value kept');
select pg_temp.assert_eq((select e ->> 'status' from r4, jsonb_array_elements(r -> 'results') e where e ->> 'slug' = 'pr-subs-day'),
                         'hold', 'goals after the timeout still run');

-- ── 7. measure_ref 는 형식·존재 확인 뒤에만 실행(표의 글자를 SQL 로 실행하지 않는다) ─────
create table public.t_canary (x int);
update public.ops_goal set measure_ref = 'private.ops_m_db_size_mb(now()); drop table public.t_canary; --' where slug = 'infra-db-size';
update public.ops_goal set measure_ref = 'private.ops_m_nope' where slug = 'dq-term-min';
create temp table r5 as select private.ops_measure_all_at(now()) as r;
select pg_temp.assert_true(to_regclass('public.t_canary') is not null, 'injected SQL not executed');
select pg_temp.assert_eq((select string_agg(e ->> 'slug', ',' order by e ->> 'slug') from r5, jsonb_array_elements(r -> 'failed') e
                          where e ->> 'error' like '22023%'), 'dq-term-min,infra-db-size', 'bad refs fail safely');
-- github·manual 목표는 SQL 갈래가 건드리지 않는다
select pg_temp.assert_true(not exists (select 1 from private.ops_measure_log l join public.ops_goal g using (slug)
                                        where g.measure_kind <> 'sql'), 'only sql goals measured');

-- ── 8. 시드 멱등: 128 재실행 · 124 → 128 재실행 ────────────────────────────────
update public.ops_goal set measure_ref = 'private.ops_m_db_size_mb' where slug = 'infra-db-size';
update public.ops_goal set measure_ref = 'private.ops_m_term_min' where slug = 'dq-term-min';
create temp table before_rerun as select slug, current_value, measured_at, note from public.ops_goal;
\ir ../../web/migrations/128_ops_measure.sql
select pg_temp.assert_eq((select count(*)::text from public.ops_goal), '29', 'rerun keeps 29 goals');
select pg_temp.assert_true(not exists (select 1 from before_rerun b join public.ops_goal g using (slug)
                                        where (g.current_value, g.measured_at, g.note) is distinct from (b.current_value, b.measured_at, b.note)),
                           'rerun does not overwrite measured values or notes');
\ir ../../web/migrations/124_ops_center_seed.sql
select pg_temp.assert_eq((select measure_kind from public.ops_goal where slug = 'edu-terms-week'), 'sql', '124 resets edu-terms-week kind');
\ir ../../web/migrations/128_ops_measure.sql
select pg_temp.assert_eq((select measure_kind || '/' || measure_ref from public.ops_goal where slug = 'edu-terms-week'),
                         'github/web/ops_measure_github.py', '128 after 124 restores the github kind');
select pg_temp.assert_eq((select count(*)::text from public.ops_goal where measure_ref is not null), '14', '14 goals have a measure ref');

rollback;
\echo 'ops_center_measure.sql: PASS'
