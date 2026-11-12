\set ON_ERROR_STOP on
-- 별도 빈 테스트 DB에서만 실행한다. 실서비스 SQL Editor에 붙이지 않는다.
-- 148_raw_signal_versions.sql — 원문 갱신 함수 계약 시험(원문 갱신 규칙 R1, 2026-11-12).
begin;
create role anon;
create role authenticated;
create role service_role bypassrls;
-- 002_findings.sql 의 raw_signals 모양(147 이후 published_date null 허용)
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

-- 행 a: 지문이 맞는 정상 행 / 행 b: PATCH 로 지문이 어긋난 행(483 2,217건 모양 — 정규화 안 된 직렬화)
insert into public.raw_signals(raw_signal_id,source,source_kind,document_id,published_date,title,raw_sha256,raw_json,row_json,extraction_status) values
  ('rawsig-a','FDA 483','483','doc-a','2026-01-01','t-a',public.t_sha('{"obs":[1]}'),'{"obs":[1]}','{"x":1}','captured'),
  ('rawsig-b','MFDS','gmp-inspection','doc-b','2026-01-02','t-b',repeat('0',64),'{"b": 2, "a": 1}','{"y":1}','captured');

\ir ../../web/migrations/148_raw_signal_versions.sql

-- 권한
select public.t_assert((select relrowsecurity from pg_class where oid='public.raw_signal_versions'::regclass), 'RLS enabled');
select public.t_assert(not has_table_privilege('anon','public.raw_signal_versions','SELECT'), 'anon cannot read versions');
select public.t_assert(not has_table_privilege('authenticated','public.raw_signal_versions','SELECT'), 'authenticated cannot read versions');
select public.t_assert(not has_function_privilege('anon','public.raw_signal_apply_version(text,text,jsonb,text,text,text,jsonb)','EXECUTE'), 'anon cannot apply');
select public.t_assert(not has_function_privilege('authenticated','public.raw_signal_apply_version(text,text,jsonb,text,text,text,jsonb)','EXECUTE'), 'authenticated cannot apply');
select public.t_assert(has_function_privilege('service_role','public.raw_signal_apply_version(text,text,jsonb,text,text,text,jsonb)','EXECUTE'), 'service_role can apply');

set local role service_role;

-- 1) 정상 갱신: 옛 판 보관 → 교체
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-a', public.t_sha('{"obs":[1]}'),
     jsonb_build_object('raw_json','{"obs":[1,2]}','row_json','{"x":2}','raw_sha256',public.t_sha('{"obs":[1,2]}'),'title','t-a2'),
     'source_update','test','c1','{"changed_keys":["obs"]}'::jsonb)->>'status') = 'applied', 'apply returns applied');
select public.t_assert((select raw_json from public.raw_signals where raw_signal_id='rawsig-a') = '{"obs":[1,2]}', 'current replaced');
select public.t_assert((select title from public.raw_signals where raw_signal_id='rawsig-a') = 't-a2', 'title replaced');
select public.t_assert((select raw_json from public.raw_signal_versions where raw_signal_id='rawsig-a' and version_no=1) = '{"obs":[1]}', 'old version kept');
select public.t_assert((select record_json->>'title' from public.raw_signal_versions where raw_signal_id='rawsig-a' and version_no=1) = 't-a', 'old title kept');

-- 2) 같은 판을 다시 넣으면 unchanged, 보관표 그대로
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-a', public.t_sha('{"obs":[1,2]}'),
     jsonb_build_object('raw_json','{"obs":[1,2]}','row_json','{"x":2}','raw_sha256',public.t_sha('{"obs":[1,2]}')),
     'source_update','test','c1')->>'status') = 'unchanged', 'same content is unchanged');
select public.t_assert((select count(*) from public.raw_signal_versions where raw_signal_id='rawsig-a') = 1, 'no version for unchanged');

-- 3) 예상 지문이 다르면 conflict, 아무것도 안 바뀜
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-a', repeat('f',64),
     jsonb_build_object('raw_json','{"obs":[9]}','row_json','{}','raw_sha256',public.t_sha('{"obs":[9]}')),
     'repair','test','')->>'status') = 'conflict', 'stale expected sha conflicts');
select public.t_assert((select raw_json from public.raw_signals where raw_signal_id='rawsig-a') = '{"obs":[1,2]}', 'conflict changes nothing');

-- 4) 지문이 raw_json 과 안 맞으면 예외
do $$
begin
  begin
    perform public.raw_signal_apply_version('rawsig-a', encode(sha256(convert_to('{"obs":[1,2]}','UTF8')),'hex'),
      jsonb_build_object('raw_json','{"obs":[3]}','row_json','{}','raw_sha256',repeat('a',64)), 'repair','test','');
    raise exception 'assertion failed: wrong sha accepted';
  exception when others then
    if sqlerrm like 'assertion failed:%' then raise; end if;
  end;
end $$;

-- 5) baseline: 어긋난 지문 재매김 — 내용 같음, 옛 raw_json 은 쌓지 않음
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-b', repeat('0',64),
     jsonb_build_object('raw_json','{"a":1,"b":2}','row_json','{"y":1}','raw_sha256',public.t_sha('{"a":1,"b":2}')),
     'baseline','r2-test','')->>'status') = 'applied', 'baseline applies');
select public.t_assert((select raw_sha256 from public.raw_signals where raw_signal_id='rawsig-b') = public.t_sha('{"a":1,"b":2}'), 'baseline sha now matches content');
select public.t_assert((select raw_json is null and raw_sha256 = repeat('0',64) from public.raw_signal_versions where raw_signal_id='rawsig-b'), 'baseline keeps old sha only');

-- 6) baseline 으로 내용을 바꾸려 하면 예외
do $$
begin
  begin
    perform public.raw_signal_apply_version('rawsig-b', encode(sha256(convert_to('{"a":1,"b":2}','UTF8')),'hex'),
      jsonb_build_object('raw_json','{"a":1,"b":3}','row_json','{}','raw_sha256',encode(sha256(convert_to('{"a":1,"b":3}','UTF8')),'hex')),
      'baseline','test','');
    raise exception 'assertion failed: baseline content change accepted';
  exception when others then
    if sqlerrm like 'assertion failed:%' then raise; end if;
  end;
end $$;

-- 7) 판 번호가 이어진다
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-a', public.t_sha('{"obs":[1,2]}'),
     jsonb_build_object('raw_json','{"obs":[1,2,3]}','row_json','{"x":3}','raw_sha256',public.t_sha('{"obs":[1,2,3]}')),
     'reextract','test','c2')->>'version_no') = '3', 'next version number');
select public.t_assert((select array_agg(version_no order by version_no) from public.raw_signal_versions where raw_signal_id='rawsig-a') = array[1,2], 'versions 1,2 kept');

-- 8) 없는 행
select public.t_assert(
  (select public.raw_signal_apply_version('rawsig-zzz', repeat('0',64),
     jsonb_build_object('raw_json','{}','row_json','{}','raw_sha256',public.t_sha('{}')),
     'repair','test','')->>'status') = 'not_found', 'missing row');

reset role;
rollback;
\echo 'raw_signal_versions SQL checks: PASS'
