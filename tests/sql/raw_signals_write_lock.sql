\set ON_ERROR_STOP on
-- 별도 빈 테스트 DB에서만 실행한다. 실서비스 SQL Editor에 붙이지 않는다.
-- 150_raw_signals_write_lock.sql — 원문 갱신 규칙 R3 시험(2026-11-12).
--   함수 밖 원문 변경 거부 · 파생 칸 갱신 허용 · 함수 경유는 통과 · 보관표 append-only(권한+트리거).
begin;
create role anon;
create role authenticated;
create role service_role bypassrls;
create table public.raw_signals (
  schema_version text not null default 'grm-raw-signal/v1',
  raw_signal_id text primary key, source text not null, source_kind text not null,
  document_id text not null, published_date text, collected_at text, title text not null,
  firm_name text, site_name text, site_country text, modality text,
  source_url text, official_url text,
  raw_sha256 text not null check (char_length(raw_sha256) = 64),
  raw_json text not null, row_json text not null, extraction_status text not null,
  ingested_at timestamptz not null default now(),
  unique (source, document_id)
);
grant usage on schema public to service_role;
grant select, insert, update on public.raw_signals to service_role;

create function public.t_sha(t text) returns text language sql as $$ select encode(sha256(convert_to(t,'UTF8')),'hex') $$;
create function public.t_assert(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'assertion failed: %', message; end if; end $$;
-- 문장이 거부되는지(예외가 나는지) — 하위 트랜잭션으로 감싸 바깥 트랜잭션은 살린다.
create function public.t_rejects(stmt text, message text) returns void language plpgsql as $$
begin
  begin
    execute stmt;
  exception when insufficient_privilege then   -- 트리거(42501)·권한 부족(42501)만 인정 — 오타는 실패
    return;
  end;
  raise exception 'assertion failed: % (거부되지 않음)', message;
end $$;
grant execute on function public.t_sha(text), public.t_assert(boolean,text), public.t_rejects(text,text) to service_role;

insert into public.raw_signals(raw_signal_id,source,source_kind,document_id,published_date,title,raw_sha256,raw_json,row_json,extraction_status) values
  ('rawsig-a','FDA 483','483','doc-a','2026-01-01','t-a',public.t_sha('{"obs":[1]}'),'{"obs":[1]}','{"x":1}','captured');

\ir ../../web/migrations/148_raw_signal_versions.sql
-- Supabase 기본 권한을 흉내 낸다: service_role 이 새 표에 전부 갖고 있었다(11-12 실측). 150 이 회수해야 한다.
grant all on table public.raw_signal_versions to service_role;
\ir ../../web/migrations/150_raw_signals_write_lock.sql

-- 권한: 보관표는 SELECT·INSERT 만
select public.t_assert(has_table_privilege('service_role','public.raw_signal_versions','SELECT'), 'service_role can read versions');
select public.t_assert(has_table_privilege('service_role','public.raw_signal_versions','INSERT'), 'service_role can insert versions');
select public.t_assert(not has_table_privilege('service_role','public.raw_signal_versions','UPDATE'), 'service_role cannot update versions');
select public.t_assert(not has_table_privilege('service_role','public.raw_signal_versions','DELETE'), 'service_role cannot delete versions');
select public.t_assert(not has_table_privilege('service_role','public.raw_signal_versions','TRUNCATE'), 'service_role cannot truncate versions');
select public.t_assert(not has_table_privilege('anon','public.raw_signal_versions','SELECT'), 'anon cannot read versions');
select public.t_assert(has_function_privilege('service_role','public.raw_signal_apply_version(text,text,jsonb,text,text,text,jsonb)','EXECUTE'), 'service_role can apply');
select public.t_assert(not has_function_privilege('anon','public.raw_signal_apply_version(text,text,jsonb,text,text,text,jsonb)','EXECUTE'), 'anon cannot apply');

set local role service_role;

-- 1) 함수 밖 원문 변경은 전부 거부(PATCH = UPDATE, merge-duplicates = ON CONFLICT DO UPDATE)
select public.t_rejects($s$update public.raw_signals set raw_json='{"obs":[9]}' where raw_signal_id='rawsig-a'$s$, 'PATCH raw_json');
select public.t_rejects($s$update public.raw_signals set row_json='{"x":9}' where raw_signal_id='rawsig-a'$s$, 'PATCH row_json');
select public.t_rejects($s$update public.raw_signals set raw_sha256=repeat('1',64) where raw_signal_id='rawsig-a'$s$, 'PATCH raw_sha256');
select public.t_rejects($s$insert into public.raw_signals(raw_signal_id,source,source_kind,document_id,title,raw_sha256,raw_json,row_json,extraction_status)
  values ('rawsig-a','FDA 483','483','doc-a','t',public.t_sha('{"obs":[7]}'),'{"obs":[7]}','{}','captured')
  on conflict (raw_signal_id) do update set raw_json=excluded.raw_json, raw_sha256=excluded.raw_sha256, row_json=excluded.row_json$s$,
  'merge-duplicates upsert');
select public.t_assert((select raw_json from public.raw_signals where raw_signal_id='rawsig-a') = '{"obs":[1]}', 'raw unchanged after rejections');

-- 2) 파생 칸만 바꾸는 갱신·같은 값 merge·ignore-duplicates·새 행 INSERT 는 통과
update public.raw_signals set modality='chemical', title='t-a1' where raw_signal_id='rawsig-a';
select public.t_assert((select modality from public.raw_signals where raw_signal_id='rawsig-a') = 'chemical', 'derived column update allowed');
insert into public.raw_signals(raw_signal_id,source,source_kind,document_id,title,raw_sha256,raw_json,row_json,extraction_status)
  values ('rawsig-a','FDA 483','483','doc-a','t',public.t_sha('{"obs":[7]}'),'{"obs":[7]}','{}','captured')
  on conflict do nothing;
insert into public.raw_signals(raw_signal_id,source,source_kind,document_id,title,raw_sha256,raw_json,row_json,extraction_status)
  values ('rawsig-n','MFDS','gmp-inspection','doc-n','t-n',public.t_sha('{}'),'{}','{}','captured');
select public.t_assert((select count(*) from public.raw_signals) = 2, 'new row insert allowed');

-- 3) 함수 경유는 통과하고 옛 판을 남긴다
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-a', public.t_sha('{"obs":[1]}'),
     jsonb_build_object('raw_json','{"obs":[1,2]}','row_json','{"x":2}','raw_sha256',public.t_sha('{"obs":[1,2]}')),
     'reextract','test','')->>'status') = 'applied', 'apply through function');
select public.t_assert((select raw_json from public.raw_signals where raw_signal_id='rawsig-a') = '{"obs":[1,2]}', 'current replaced via function');
select public.t_assert((select raw_json from public.raw_signal_versions where raw_signal_id='rawsig-a' and version_no=1) = '{"obs":[1]}', 'old version kept');

-- 4) 함수가 끝나면 표식이 꺼진다 — 같은 트랜잭션의 다음 직접 변경도 거부
select public.t_rejects($s$update public.raw_signals set raw_json='{"obs":[8]}' where raw_signal_id='rawsig-a'$s$, 'PATCH after function in same transaction');

-- 5) 보관표는 고칠 수도 지울 수도 없다(권한)
select public.t_rejects($s$update public.raw_signal_versions set changed_by='x'$s$, 'service_role update versions');
select public.t_rejects($s$delete from public.raw_signal_versions$s$, 'service_role delete versions');

reset role;

-- 6) 소유자(postgres)도 행 단위로는 못 고친다(트리거) — 실수 방지
select public.t_rejects($s$update public.raw_signal_versions set changed_by='x'$s$, 'owner update versions');
select public.t_rejects($s$delete from public.raw_signal_versions$s$, 'owner delete versions');

-- 7) 사람용 비상구: 같은 트랜잭션에서 표식을 켜면 SQL 로도 바꿀 수 있다
do $$
begin
  perform set_config('grm.raw_apply_version', 'on', true);
  update public.raw_signals set raw_json='{"obs":[1,2]}', raw_sha256=public.t_sha('{"obs":[1,2]}') where raw_signal_id='rawsig-a';
  perform set_config('grm.raw_apply_version', '', true);
end $$;

rollback;
\echo 'raw_signals write lock SQL checks: PASS'
