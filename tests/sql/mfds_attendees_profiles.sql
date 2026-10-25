-- mfds_attendees_ingest.sql의 격리 트랜잭션에서 실행한다.
create table public.findings(document_id text,source text,scope_status text,review_status text,
  finding_text_ko text,finding_language text);
-- 격리 DB의 FDA 응답 픽스처. 라이브 적용 시에는 실제 FDA 함수를 대상으로 같은 가드가 돈다.
create function public.findings_inspector_index() returns jsonb language sql as $$
  select '[{"inspector_key":"fda-fixture","display_name":"FDA fixture"}]'::jsonb
$$;
create function public.findings_inspector_profile(text) returns jsonb language sql as $$
  select '{"inspector_key":"fda-fixture","totals":{"documents":5}}'::jsonb
$$;
\ir ../../web/migrations/094_mfds_inspector_profiles.sql
do $$
declare i integer; people jsonb; result jsonb; status text;
begin
  for i in 1..8 loop
    insert into public.raw_signals(raw_signal_id,document_id,source,source_kind,raw_sha256,
      firm_name,raw_json,published_date)
    values ('raw-case-'||i,'mfds-case-'||i,'MFDS','gmp-inspection',repeat('c',64),
      case when i<=3 then '나제조소' else '가제조소' end,
      jsonb_build_object('inspection_start',case when i<=4 then '2024-01-01' when i<=7 then '2026-01-01' else '' end,
        'manufacturer',case when i<=3 then '나제조소' else '가제조소' end)::text,'2026-10-01');
    status := case when i<=6 then 'named' when i=7 then 'named_none' else 'absent' end;
    if i<=6 then
      people := jsonb_build_array(
        jsonb_build_object('inspector_name_norm','나상가','agency','서울지방식품의약품안전청','dept','의약품안전관리과','is_observer',false),
        jsonb_build_object('inspector_name_norm','라상가','agency','서울지방식품의약품안전청','dept','의약품안전관리과','is_observer',true));
      if i<=5 then people := people || jsonb_build_array(jsonb_build_object('inspector_name_norm','가상가','agency','서울지방식품의약품안전청','dept','의약품안전관리과','is_observer',false)); end if;
      if i<=4 then people := people || jsonb_build_array(jsonb_build_object('inspector_name_norm','다상가','agency','서울지방식품의약품안전청','dept','의약품안전관리과','is_observer',false)); end if;
      if i=1 then people := people || jsonb_build_array(jsonb_build_object('inspector_name_norm','가상가','agency','경인지방식품의약품안전청','dept','의료제품실사과','is_observer',false)); end if;
    elsif i=7 then
      people := '[{"inspector_name_norm":null,"agency":"식품의약품안전처","dept":"의약품관리과","is_observer":null}]'::jsonb;
    else people := '[]'::jsonb;
    end if;
    result := jsonb_build_object('table_status',status,'parser_version','test','table_count',case when i<=7 then 1 else 0 end,'attendees',people);
    perform public.mfds_inspection_attendees_replace('mfds-case-'||i,repeat('c',64),result);
  end loop;
end $$;

select pg_temp.assert_true(jsonb_array_length(public.mfds_inspector_index())=2,'cohort excludes below five and observer-only');
select pg_temp.assert_true(public.mfds_inspector_index() = '[{"inspector_key":"가상가","display_name":"가상가"},{"inspector_key":"나상가","display_name":"나상가"}]'::jsonb,'index contains only names, in name order');
select pg_temp.assert_true(public.mfds_inspector_profile('다상가')='null'::jsonb,'four-document profile blocked at RPC');
select pg_temp.assert_true(public.mfds_inspector_profile('라상가')='null'::jsonb,'observer-only profile blocked');
select pg_temp.assert_true(public.mfds_inspector_profile('missing')='null'::jsonb and public.mfds_inspector_profile(null)='null'::jsonb,'missing and null same response');
select pg_temp.assert_true((public.mfds_inspector_profile('가상가')->'totals'->>'documents')::int=5,'same person twice in one document counts once');
select pg_temp.assert_true(public.mfds_inspector_profile('가 상 가(참관)')=public.mfds_inspector_profile('가상가'),'key normalization whitespace and observer suffix only');
select pg_temp.assert_true((public.mfds_inspector_profile('가상가')->'manufacturers'->0->>'manufacturer')='가제조소','manufacturers in name order');
select pg_temp.assert_true((public.mfds_inspector_profile('가상가')->'by_year'->1) @> '{"year":"2026","documents":1,"total_documents":3,"named_documents":2,"table_documents":3}'::jsonb,'profile yearly numerator and same-place disclosure denominator');
select pg_temp.assert_true((public.mfds_inspector_profile('가상가')->'by_year'->2) @> '{"year":"","documents":0,"total_documents":2,"named_documents":0}'::jsonb,'absent and failed belong to denominator, unknown year preserved');
select pg_temp.assert_true((public.mfds_inspection_departments()->'departments'->0->>'agency')='경인지방식품의약품안전청','departments in name order, not volume order');
select pg_temp.assert_true((public.mfds_inspection_departments()->'departments'->2->>'documents')::int=1,'unnamed table included for agency');
select pg_temp.assert_true((public.mfds_inspection_departments()->'departments'->1->>'documents')::int=6,'agency counts unique documents, not people');
select pg_temp.assert_true((public.mfds_inspection_departments()->'departments'->2->'by_year'->1) @> '{"year":"2026","documents":1,"total_documents":3,"named_documents":2}'::jsonb,'agency yearly denominator included');
select pg_temp.assert_true(not has_function_privilege('anon','public.mfds_inspection_documents()','EXECUTE'),'internal document source hidden');
select pg_temp.assert_true(not has_function_privilege('anon','public.mfds_inspector_cohort()','EXECUTE'),'cohort helper hidden');
select pg_temp.assert_true(not has_table_privilege('anon','public.mfds_inspection_attendees','SELECT'),'raw attendee names still private');

insert into public.findings values ('mfds-case-1','MFDS','ok','approved','공개','KO'),('mfds-case-2','MFDS','ok','rejected','반려','KO');
select pg_temp.assert_true(jsonb_array_length(public.mfds_inspection_document_attendees(array['mfds-case-1','mfds-case-2']))=1,'document fact export applies findings public gate');
select pg_temp.assert_true(public.mfds_inspection_document_attendees(array_fill('mfds-case-1'::text,array[201]))='[]'::jsonb,'document export batch bounded');
set local role anon;
select public.mfds_inspector_index();
select public.mfds_inspector_profile('가상가');
select public.mfds_inspection_departments();
reset role;
