-- MFDS 공개 문서 기준 참여 이력. FDA RPC의 정의·시그니처·응답은 변경하지 않는다.
-- 색인은 이름순·건수 없음. 코호트는 참관을 제외한 고유 문서 5건 이상이다.
-- 제조소·기관·부서는 원문 표기이며 동일인·직급·주도 여부를 추론하지 않는다.
-- 내부 함수는 직접 공개하지 않는다. 공개 함수는 명시적 MFDS 원천·해시 게이트를 쓴다.
-- 호출자는 마이그레이션 전체를 한 트랜잭션으로 실행한다(실패 시 신설 객체도 롤백).
create temporary table mfds_fda_rpc_guard_before as
  with idx as materialized (select public.findings_inspector_index() as value)
  select md5(value::text) as index_md5, value->0->>'inspector_key' as sample_key,
    md5(public.findings_inspector_profile(value->0->>'inspector_key')::text) as profile_md5,
    md5(pg_get_functiondef('public.findings_inspector_index()'::regprocedure)) as index_def_md5,
    md5(pg_get_functiondef('public.findings_inspector_profile(text)'::regprocedure)) as profile_def_md5
  from idx;

create function public.mfds_inspector_key(p_name text) returns text
language sql immutable set search_path = public as $$
  select regexp_replace(regexp_replace(coalesce(p_name,''),'\s+','','g'),'\(참관\)$','')
$$;

create function public.mfds_inspection_documents()
returns table(document_id text, raw_signal_id text, source_raw_sha256 text,
  manufacturer text, inspection_start text, inspection_end text, published_date text,
  year text, table_status text)
language sql stable security definer set search_path = public as $$
  with states as materialized (
    select a.document_id, a.raw_signal_id, a.source_raw_sha256, min(a.table_status) as table_status
    from public.mfds_inspection_attendees a
    group by a.document_id, a.raw_signal_id, a.source_raw_sha256
  )
  select distinct on (r.document_id)
    r.document_id, r.raw_signal_id, r.raw_sha256,
    coalesce(nullif(j.manufacturer,''),r.firm_name,''),
    coalesce(j.inspection_start,''), coalesce(j.inspection_end,''), coalesce(r.published_date,''),
    coalesce(substring(j.inspection_start from '^[0-9]{4}'),''), coalesce(s.table_status,'failed')
  from public.raw_signals r
  cross join lateral jsonb_to_record(r.raw_json::jsonb)
    j(manufacturer text, inspection_start text, inspection_end text)
  left join states s on s.document_id=r.document_id and s.raw_signal_id=r.raw_signal_id
    and s.source_raw_sha256=r.raw_sha256
  where r.source='MFDS' and r.source_kind='gmp-inspection'
  order by r.document_id, r.ingested_at desc, r.raw_signal_id desc
$$;

create function public.mfds_inspector_cohort() returns table(inspector_key text)
language sql stable security definer set search_path = public as $$
  with docs as materialized (select * from public.mfds_inspection_documents())
  select a.inspector_name_norm
  from public.mfds_inspection_attendees a
  join docs d on d.document_id=a.document_id and d.raw_signal_id=a.raw_signal_id
    and d.source_raw_sha256=a.source_raw_sha256
  where a.inspector_name_norm is not null and a.is_observer=false and d.table_status='named'
  group by a.inspector_name_norm
  having count(distinct a.document_id) >= 5
$$;

create function public.mfds_inspector_index() returns jsonb
language sql stable security definer set search_path = public as $$
  with cohort as materialized (select * from public.mfds_inspector_cohort())
  select coalesce(jsonb_agg(jsonb_build_object('inspector_key',inspector_key,
    'display_name',inspector_key) order by inspector_key collate "C"),'[]'::jsonb)
  from cohort
$$;

create function public.mfds_inspector_profile(p_inspector_key text) returns jsonb
language sql stable security definer set search_path = public as $$
  with q as materialized (select public.mfds_inspector_key(p_inspector_key) as k),
  cohort as materialized (select * from public.mfds_inspector_cohort()),
  docs as materialized (select * from public.mfds_inspection_documents()),
  rows_out as materialized (
    select a.* from public.mfds_inspection_attendees a
    join docs d on d.document_id=a.document_id and d.raw_signal_id=a.raw_signal_id
      and d.source_raw_sha256=a.source_raw_sha256
    where a.inspector_name_norm=(select k from q) and d.table_status='named'
  ),
  participant_docs as materialized (
    select d.* from docs d where exists
      (select 1 from rows_out a where a.document_id=d.document_id and a.is_observer=false)
  ),
  years as materialized (
    select d.year, count(*)::int as total_documents,
      count(*) filter (where d.table_status='named')::int as named_documents,
      count(*) filter (where d.table_status in ('named','named_none'))::int as table_documents
    from docs d group by d.year
  ),
  firms as materialized (
    select manufacturer,count(*)::int as documents from participant_docs
    where manufacturer<>'' group by manufacturer
  ),
  units as materialized (
    select agency,dept,count(distinct document_id)::int as documents
    from rows_out where is_observer=false group by agency,dept
  )
  select case when not exists (select 1 from cohort where inspector_key=(select k from q))
    then 'null'::jsonb else jsonb_build_object(
    'inspector_key',(select k from q), 'display_name',(select k from q),
    'totals',jsonb_build_object('documents',(select count(*) from participant_docs)),
    'by_year',coalesce((select jsonb_agg(jsonb_build_object(
      'year',y.year,'total_documents',y.total_documents,'named_documents',y.named_documents,
      'table_documents',y.table_documents,
      'documents',(select count(*) from participant_docs p where p.year=y.year),
      'observer_documents',(select count(distinct d.document_id) from docs d join rows_out a
        on a.document_id=d.document_id where a.is_observer=true and d.year=y.year)
      ) order by y.year='',y.year) from years y),'[]'::jsonb),
    'manufacturers',coalesce((select jsonb_agg(jsonb_build_object('manufacturer',manufacturer,
      'documents',documents) order by manufacturer collate "C") from firms),'[]'::jsonb),
    'by_agency_department',coalesce((select jsonb_agg(jsonb_build_object('agency',agency,
      'dept',dept,'documents',documents) order by agency collate "C",dept collate "C") from units),'[]'::jsonb),
    'documents',coalesce((select jsonb_agg(jsonb_build_object('document_id',document_id,
      'manufacturer',manufacturer,'inspection_start',inspection_start,'inspection_end',inspection_end,
      'published_date',published_date) order by inspection_start desc,document_id) from participant_docs),'[]'::jsonb),
    'observer_documents',coalesce((select jsonb_agg(jsonb_build_object('document_id',d.document_id,
      'manufacturer',d.manufacturer,'inspection_start',d.inspection_start,'inspection_end',d.inspection_end,
      'published_date',d.published_date) order by d.inspection_start desc,d.document_id)
      from docs d where exists (select 1 from rows_out a where a.document_id=d.document_id
        and a.is_observer=true)),'[]'::jsonb)
  ) end
$$;

create function public.mfds_inspection_departments() returns jsonb
language sql stable security definer set search_path = public as $$
  with docs as materialized (select * from public.mfds_inspection_documents()),
  years as materialized (
    select year,count(*)::int as total_documents,
      count(*) filter (where table_status='named')::int as named_documents,
      count(*) filter (where table_status in ('named','named_none'))::int as table_documents
    from docs group by year
  ),
  unit_docs as materialized (
    select distinct a.agency,a.dept,d.document_id,d.year
    from public.mfds_inspection_attendees a
    join docs d on d.document_id=a.document_id and d.raw_signal_id=a.raw_signal_id
      and d.source_raw_sha256=a.source_raw_sha256
    where a.agency is not null and a.dept is not null and d.table_status in ('named','named_none')
  ),
  units as materialized (
    select agency,dept,count(*)::int as documents from unit_docs group by agency,dept
  )
  select jsonb_build_object(
    'by_year',coalesce((select jsonb_agg(to_jsonb(y) order by y.year='',y.year) from years y),'[]'::jsonb),
    'departments',coalesce((select jsonb_agg(jsonb_build_object(
      'agency',u.agency,'dept',u.dept,'documents',u.documents,
      'by_year',(select jsonb_agg(jsonb_build_object('year',y.year,
        'documents',(select count(*) from unit_docs x where x.agency=u.agency and x.dept=u.dept and x.year=y.year),
        'total_documents',y.total_documents,'named_documents',y.named_documents,
        'table_documents',y.table_documents) order by y.year='',y.year) from years y)
      ) order by u.agency collate "C",u.dept collate "C") from units u),'[]'::jsonb)
  )
$$;

-- 정적 문서 정본 갱신용. 공개 findings가 있는 요청 문서의 사실 행만 반환한다.
-- 공개 프로파일 자격과 별개인 문서 표기이며, 개별 프로파일 링크는 런타임 색인이 결정한다.
create function public.mfds_inspection_document_attendees(p_document_ids text[]) returns jsonb
language sql stable security definer set search_path = public as $$
  with requested as materialized (
    select distinct unnest(case when cardinality(p_document_ids)<=200 then p_document_ids else array[]::text[] end) as document_id
  ),
  docs as materialized (select * from public.mfds_inspection_documents()),
  pairs as materialized (
    select a.document_id,a.inspector_name_norm,a.is_observer,min(a.attendee_seq) as seq
    from public.mfds_inspection_attendees a
    join docs d on d.document_id=a.document_id and d.raw_signal_id=a.raw_signal_id
      and d.source_raw_sha256=a.source_raw_sha256
    join requested r on r.document_id=a.document_id
    where a.inspector_name_norm is not null and d.table_status='named'
      and exists (select 1 from public.findings f where f.document_id=a.document_id
        and f.source='MFDS' and f.scope_status='ok' and f.review_status<>'rejected'
        and (f.finding_text_ko<>'' or f.finding_language='KO'))
    group by a.document_id,a.inspector_name_norm,a.is_observer
  ),
  grouped as materialized (
    select document_id,jsonb_agg(jsonb_build_object('name',inspector_name_norm,'is_observer',is_observer)
      order by seq,inspector_name_norm collate "C") as attendees from pairs group by document_id
  )
  select coalesce(jsonb_agg(to_jsonb(g) order by g.document_id),'[]'::jsonb) from grouped g
$$;

revoke all on function public.mfds_inspector_key(text),public.mfds_inspection_documents(),
  public.mfds_inspector_cohort(),public.mfds_inspector_index(),public.mfds_inspector_profile(text),
  public.mfds_inspection_departments(),public.mfds_inspection_document_attendees(text[])
  from public,anon,authenticated;
grant execute on function public.mfds_inspector_index(),public.mfds_inspector_profile(text),
  public.mfds_inspection_departments(),public.mfds_inspection_document_attendees(text[])
  to anon,authenticated,service_role;

do $$
begin
  if not exists (select 1 from pg_temp.mfds_fda_rpc_guard_before b
      where b.sample_key is not null
      and b.index_md5=md5(public.findings_inspector_index()::text)
      and b.profile_md5=md5(public.findings_inspector_profile(b.sample_key)::text)
      and b.index_def_md5=md5(pg_get_functiondef('public.findings_inspector_index()'::regprocedure))
      and b.profile_def_md5=md5(pg_get_functiondef('public.findings_inspector_profile(text)'::regprocedure))) then
    raise exception 'FDA inspector RPC response/definition md5 changed or empty baseline';
  end if;
end $$;
drop table pg_temp.mfds_fda_rpc_guard_before;
