-- Max local web/migrations prefix was 082 on main d714375. Additive only.
create table public.rum_referrer_path_daily (
  snap_date date not null,
  referer_host text not null,
  request_path text not null check (request_path like '/%' and request_path !~ '[?#]'),
  visits integer not null check (visits > 0),
  sample_interval double precision not null check (sample_interval >= 1),
  primary key (snap_date, referer_host, request_path)
);

-- Each attempt is retained, including zero rows and rejected lower-quality runs.
-- api_limit_hit means possibly incomplete (exactly hitting a limit is ambiguous).
create table public.rum_referrer_path_runs (
  id bigint generated always as identity primary key,
  collected_at timestamptz not null default now(),
  snap_date date not null,
  window_start timestamptz not null,
  window_end timestamptz not null,
  api_limit integer not null check (api_limit > 0),
  day_cap integer not null check (day_cap > 0),
  received_rows integer not null check (received_rows >= 0),
  retained_rows integer not null check (retained_rows >= 0),
  dropped_pairs integer not null check (dropped_pairs >= 0),
  api_limit_hit boolean not null,
  day_cap_hit boolean not null,
  sample_interval double precision not null check (sample_interval >= 1),
  stored boolean not null,
  reason text not null
);
create index on public.rum_referrer_path_runs (snap_date, id desc) where stored;
alter table public.rum_referrer_path_daily enable row level security;
alter table public.rum_referrer_path_runs enable row level security;
revoke all on public.rum_referrer_path_daily, public.rum_referrer_path_runs from public, anon, authenticated;
-- Operational traffic stays service-role only, unlike public-facing content.
grant all on public.rum_referrer_path_daily, public.rum_referrer_path_runs to service_role;
grant usage, select on sequence public.rum_referrer_path_runs_id_seq to service_role;

create function public.store_rum_referrer_paths(p_rows jsonb, p_run jsonb,
                                               p_allow_downgrade boolean default false)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  attempt public.rum_referrer_path_runs;
  previous public.rum_referrer_path_runs;
  will_store boolean := true;
  why text := 'stored';
begin
  attempt := jsonb_populate_record(null::public.rum_referrer_path_runs, p_run);
  if jsonb_typeof(p_rows) <> 'array' or attempt.retained_rows <> jsonb_array_length(p_rows)
     or attempt.window_start <> (attempt.snap_date::timestamp at time zone 'UTC')
     or attempt.window_end < attempt.window_start
     or attempt.window_end >= attempt.window_start + interval '1 day' then
    raise exception 'Invalid cross collection window or row count';
  end if;
  if exists (select 1 from jsonb_to_recordset(p_rows) as r(snap_date date)
             where r.snap_date is distinct from attempt.snap_date) then
    raise exception 'Cross rows contain a different date';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('rum-cross:' || attempt.snap_date::text, 0));
  select * into previous from public.rum_referrer_path_runs
    where snap_date = attempt.snap_date and stored order by id desc limit 1;
  if found then
    if attempt.window_end < previous.window_end then
      will_store := false; why := 'shorter-window';
    elsif (attempt.api_limit_hit or attempt.day_cap_hit)
          and not (previous.api_limit_hit or previous.day_cap_hit) then
      will_store := false; why := 'would-truncate';
    -- collect_rum_analytics.keep_days: PRECISION_BAND = 3.0. Rechecked under lock
    -- so concurrent attempts cannot overwrite precise data with sampled data.
    elsif not (p_allow_downgrade or attempt.sample_interval <= previous.sample_interval
               or attempt.sample_interval < 3.0 or previous.sample_interval >= 3.0) then
      will_store := false; why := 'precision-downgrade';
    end if;
  end if;
  if will_store then
    delete from public.rum_referrer_path_daily where snap_date = attempt.snap_date;
    insert into public.rum_referrer_path_daily
      select * from jsonb_populate_recordset(null::public.rum_referrer_path_daily, p_rows);
  end if;
  insert into public.rum_referrer_path_runs
    (snap_date, window_start, window_end, api_limit, day_cap, received_rows,
     retained_rows, dropped_pairs, api_limit_hit, day_cap_hit, sample_interval, stored, reason)
    values (attempt.snap_date, attempt.window_start, attempt.window_end, attempt.api_limit,
            attempt.day_cap, attempt.received_rows, attempt.retained_rows, attempt.dropped_pairs,
            attempt.api_limit_hit, attempt.day_cap_hit, attempt.sample_interval, will_store, why);
  return jsonb_build_object('stored', will_store, 'reason', why);
end;
$$;
revoke all on function public.store_rum_referrer_paths(jsonb,jsonb,boolean) from public, anon, authenticated;
grant execute on function public.store_rum_referrer_paths(jsonb,jsonb,boolean) to service_role;
