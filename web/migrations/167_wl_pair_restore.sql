-- Apply this migration before using wl_pair_restore.py --apply.
create or replace function public.grm_wl_pair_restore(
  p_finding_id text,
  p_raw_signal_id text,
  p_old_md5 text,
  p_new_text text
)
returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  v_old_text text;
begin
  select f.finding_text into v_old_text
    from public.findings as f
   where f.finding_id = p_finding_id
     and f.raw_signal_id = p_raw_signal_id
     and f.source = 'FDA Warning Letter'
   for update;

  if not found then
    return 'no_match';
  end if;
  if v_old_text = p_new_text then
    return 'already_applied';
  end if;
  if md5(convert_to(v_old_text, 'UTF8')) is distinct from p_old_md5 then
    return 'stale';
  end if;

  update public.findings
     set finding_text = p_new_text
   where finding_id = p_finding_id;
  if not found then
    return 'concurrent_change';
  end if;
  return 'applied';
end;
$$;

revoke all on function public.grm_wl_pair_restore(text, text, text, text) from public, anon, authenticated;
grant execute on function public.grm_wl_pair_restore(text, text, text, text) to service_role;
