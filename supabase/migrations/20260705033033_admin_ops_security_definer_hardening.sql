-- Harden Admin SECURITY DEFINER helpers so they are not exposed as public RPC.

create schema if not exists private;

revoke all on schema private from public;
revoke all on schema private from anon;
grant usage on schema private to authenticated;

create or replace function private.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.admin_user au
    where au.user_id = auth.uid()
      and au.role = 'Admin'
      and au.revoked_at is null
  );
$$;

revoke all on function private.is_admin() from public;
revoke all on function private.is_admin() from anon;
grant execute on function private.is_admin() to authenticated;

drop policy if exists "admin users can read admin users" on public.admin_user;
create policy "admin users can read admin users"
on public.admin_user
for select
to authenticated
using (private.is_admin());

drop policy if exists "admin users can read audit log" on public.admin_audit_log;
create policy "admin users can read audit log"
on public.admin_audit_log
for select
to authenticated
using (private.is_admin());

drop policy if exists "admin users can read newsletter dispatch log" on public.newsletter_dispatch_log;
create policy "admin users can read newsletter dispatch log"
on public.newsletter_dispatch_log
for select
to authenticated
using (private.is_admin());

drop function if exists public.is_admin();

revoke all on function public.grm_bootstrap_admin_user() from public;
revoke all on function public.grm_bootstrap_admin_user() from anon;
revoke all on function public.grm_bootstrap_admin_user() from authenticated;
