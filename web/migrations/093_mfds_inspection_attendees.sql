-- MFDS 공개 실사자 표 적재. 기존 FDA 함수·findings·raw_signals는 변경하지 않는다.
-- 문서별 named/named_none/absent/failed. absent/failed는 seq=0 상태 행 하나를 둔다.
-- 성명 없는 행의 is_observer=NULL은 '참관 아님'이 아니라 확인 불가다.
-- 재적재는 서비스 전용 RPC 한 트랜잭션에서 upsert + 잔여 행 제거로 수행한다.
create table public.mfds_inspection_attendees (
  document_id text not null,
  attendee_seq integer not null,
  raw_signal_id text not null references public.raw_signals(raw_signal_id),
  source_raw_sha256 text not null check (source_raw_sha256 ~ '^[a-f0-9]{64}$'),
  inspector_name_norm text,
  agency text,
  dept text,
  is_observer boolean,
  table_status text not null check (table_status in ('named','named_none','absent','failed')),
  parse_reason text not null default '',
  parser_version text not null,
  table_count integer not null check (table_count >= 0),
  primary key (document_id, attendee_seq),
  check (parser_version <> ''),
  check ((table_status = 'failed') = (parse_reason <> '')),
  check (
    (table_status in ('absent','failed') and attendee_seq = 0
      and inspector_name_norm is null and agency is null and dept is null and is_observer is null)
    or
    (table_status in ('named','named_none') and attendee_seq > 0 and table_count > 0
      and coalesce(agency, '') <> '' and coalesce(dept, '') <> ''
      and ((inspector_name_norm is null and is_observer is null)
        or (table_status = 'named' and inspector_name_norm ~ '^[가-힣]{2,5}$'
            and is_observer is not null)))
  )
);
create index mfds_attendees_raw_signal_idx on public.mfds_inspection_attendees(raw_signal_id);
create index mfds_attendees_name_document_idx
  on public.mfds_inspection_attendees(inspector_name_norm, document_id)
  where inspector_name_norm is not null and is_observer = false;

alter table public.mfds_inspection_attendees enable row level security;
revoke all on public.mfds_inspection_attendees from public, anon, authenticated;
grant select, insert, update, delete on public.mfds_inspection_attendees to service_role;

create function public.mfds_inspection_attendees_replace(
  p_document_id text, p_raw_sha256 text, p_result jsonb
) returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_raw public.raw_signals%rowtype;
  v_status text := p_result->>'table_status';
  v_rows jsonb := p_result->'attendees';
  v_count integer;
  v_written integer;
begin
  -- 같은 문서의 병행 재적재가 서로의 잔여 행을 남기지 않게 직렬화한다.
  perform pg_advisory_xact_lock(hashtextextended('mfds-attendees:' || p_document_id, 0));
  select * into strict v_raw from public.raw_signals
    where document_id = p_document_id and source = 'MFDS' and source_kind = 'gmp-inspection';
  if v_raw.raw_sha256 is distinct from p_raw_sha256 then
    raise exception 'stale MFDS source hash' using errcode = '22023';
  end if;
  if jsonb_typeof(p_result) is distinct from 'object'
      or jsonb_typeof(v_rows) is distinct from 'array'
      or coalesce(v_status, '') not in ('named','named_none','absent','failed') then
    raise exception 'invalid MFDS table result' using errcode = '22023';
  end if;
  v_count := jsonb_array_length(v_rows);
  if (v_status in ('absent','failed') and v_count <> 0)
      or (v_status in ('named','named_none') and v_count = 0)
      or (v_status = 'named' and not exists (
        select 1 from jsonb_array_elements(v_rows) x where x->>'inspector_name_norm' is not null
      )) then
    raise exception 'inconsistent MFDS table result' using errcode = '22023';
  end if;
  if v_count = 0 then
    v_rows := '[{"inspector_name_norm":null,"agency":null,"dept":null,"is_observer":null}]'::jsonb;
  end if;
  insert into public.mfds_inspection_attendees
    (document_id, attendee_seq, raw_signal_id, source_raw_sha256,
     inspector_name_norm, agency, dept, is_observer, table_status, parse_reason,
     parser_version, table_count)
  select p_document_id, case when v_count = 0 then 0 else x.seq::integer end,
    v_raw.raw_signal_id, v_raw.raw_sha256,
    x.row->>'inspector_name_norm', x.row->>'agency', x.row->>'dept',
    (x.row->>'is_observer')::boolean, v_status, coalesce(p_result->>'reason', ''),
    p_result->>'parser_version', (p_result->>'table_count')::integer
  from jsonb_array_elements(v_rows) with ordinality x(row, seq)
  on conflict (document_id, attendee_seq) do update set
    raw_signal_id = excluded.raw_signal_id, source_raw_sha256 = excluded.source_raw_sha256,
    inspector_name_norm = excluded.inspector_name_norm, agency = excluded.agency,
    dept = excluded.dept, is_observer = excluded.is_observer,
    table_status = excluded.table_status, parse_reason = excluded.parse_reason,
    parser_version = excluded.parser_version, table_count = excluded.table_count;
  get diagnostics v_written = row_count;
  delete from public.mfds_inspection_attendees where document_id = p_document_id
    and (attendee_seq > v_count or (v_count > 0 and attendee_seq = 0));
  return v_written;
end;
$$;
revoke all on function public.mfds_inspection_attendees_replace(text,text,jsonb)
  from public, anon, authenticated;
grant execute on function public.mfds_inspection_attendees_replace(text,text,jsonb) to service_role;
