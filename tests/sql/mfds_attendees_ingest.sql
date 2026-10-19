\set ON_ERROR_STOP on
-- 별도 빈 테스트 DB에서만 실행한다. 실서비스 SQL Editor에 붙이지 않는다.
begin;
create role anon;
create role authenticated;
create role service_role bypassrls;
create table public.raw_signals (
  raw_signal_id text primary key, document_id text not null,
  source text not null, source_kind text not null, raw_sha256 text not null
);
grant usage on schema public to service_role;
grant select on public.raw_signals to service_role;
insert into public.raw_signals values
  ('raw-a','doc-a','MFDS','gmp-inspection',repeat('a',64)),
  ('raw-fda','doc-fda','FDA 483','483',repeat('b',64));
\ir ../../web/migrations/093_mfds_inspection_attendees.sql

create function pg_temp.assert_true(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;
select pg_temp.assert_true((select relrowsecurity from pg_class where oid='public.mfds_inspection_attendees'::regclass), 'RLS enabled');
select pg_temp.assert_true(not has_table_privilege('anon','public.mfds_inspection_attendees','SELECT'), 'anon cannot read names');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.mfds_inspection_attendees','SELECT'), 'authenticated cannot read names');
select pg_temp.assert_true(not has_function_privilege('anon','public.mfds_inspection_attendees_replace(text,text,jsonb)','EXECUTE'), 'anon cannot ingest');

set local role service_role;
select public.mfds_inspection_attendees_replace('doc-a',repeat('a',64), '{"table_status":"named","parser_version":"test","table_count":2,"attendees":[{"inspector_name_norm":"가상가","agency":"식품의약품안전처","dept":"의약품품질과","is_observer":false},{"inspector_name_norm":"가상가","agency":"경인지방식품의약품안전청","dept":"의료제품실사과","is_observer":false},{"inspector_name_norm":"가상나","agency":"식품의약품안전처","dept":"의약품품질과","is_observer":true}]}'::jsonb);
reset role;
create temp table first_state as select md5(jsonb_agg(to_jsonb(a) order by attendee_seq)::text) as hash from public.mfds_inspection_attendees a;
select pg_temp.assert_true((select count(distinct (document_id,inspector_name_norm)) = 1 from public.mfds_inspection_attendees where is_observer=false), 'same name per document once');
set local role service_role;
select public.mfds_inspection_attendees_replace('doc-a',repeat('a',64), '{"table_status":"named","parser_version":"test","table_count":2,"attendees":[{"inspector_name_norm":"가상가","agency":"식품의약품안전처","dept":"의약품품질과","is_observer":false},{"inspector_name_norm":"가상가","agency":"경인지방식품의약품안전청","dept":"의료제품실사과","is_observer":false},{"inspector_name_norm":"가상나","agency":"식품의약품안전처","dept":"의약품품질과","is_observer":true}]}'::jsonb);
reset role;
select pg_temp.assert_true((select hash from first_state) = (select md5(jsonb_agg(to_jsonb(a) order by attendee_seq)::text) from public.mfds_inspection_attendees a), 'idempotent byte equality');

set local role service_role;
do $$
begin
  begin
    perform public.mfds_inspection_attendees_replace('doc-fda',repeat('b',64),'{}');
    raise exception 'accepted FDA source';
  exception when no_data_found then null; end;
  begin
    perform public.mfds_inspection_attendees_replace('doc-a',repeat('f',64),'{}');
    raise exception 'accepted stale hash';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.mfds_inspection_attendees_replace('doc-a',repeat('a',64),'{"table_status":"named","table_count":1,"parser_version":"test","attendees":[{"inspector_name_norm":"가상가","agency":"","dept":"","is_observer":false}]}');
    raise exception 'accepted incomplete row';
  exception when check_violation then null; end;
end $$;
reset role;
select pg_temp.assert_true((select hash from first_state) = (select md5(jsonb_agg(to_jsonb(a) order by attendee_seq)::text) from public.mfds_inspection_attendees a), 'invalid retry leaves old document intact');

set local role service_role;
select public.mfds_inspection_attendees_replace('doc-a',repeat('a',64),'{"table_status":"named_none","table_count":1,"parser_version":"test","attendees":[{"inspector_name_norm":null,"agency":"식품의약품안전처","dept":"의약품품질과","is_observer":null}]}');
reset role;
select pg_temp.assert_true((select count(*) = 1 and bool_and(inspector_name_norm is null and is_observer is null) from public.mfds_inspection_attendees), 'smaller replacement removes stale people, preserves unknown');
set local role service_role;
select public.mfds_inspection_attendees_replace('doc-a',repeat('a',64),'{"table_status":"absent","table_count":0,"parser_version":"test","attendees":[]}');
reset role;
select pg_temp.assert_true((select count(*) = 1 and min(attendee_seq) = 0 and min(table_status)='absent' from public.mfds_inspection_attendees), 'absent is one state row');
set local role service_role;
select public.mfds_inspection_attendees_replace('doc-a',repeat('a',64),'{"table_status":"failed","reason":"missing_text","table_count":0,"parser_version":"test","attendees":[]}');
reset role;
select pg_temp.assert_true((select count(*) = 1 and min(table_status)='failed' and min(parse_reason)='missing_text' from public.mfds_inspection_attendees), 'failure state persisted');
select pg_temp.assert_true((select count(*)=2 and bool_and(raw_sha256 = case source when 'MFDS' then repeat('a',64) else repeat('b',64) end) from public.raw_signals), 'sources untouched');
rollback;
