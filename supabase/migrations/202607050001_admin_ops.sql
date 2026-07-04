-- GRM Admin operations layer.
-- Bootstrap owner/admin email: yeomminho1472@gmail.com

create table if not exists public.admin_user (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique,
  role text not null default 'Admin' check (role = 'Admin'),
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users(id) on delete set null,
  revoked_at timestamptz
);

create table if not exists public.admin_audit_log (
  id bigint generated always as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null,
  target_type text,
  target_id text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.newsletter_dispatch_log (
  id bigint generated always as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  publish_date date not null,
  mode text not null default 'send' check (mode in ('send')),
  github_status integer,
  github_run_url text,
  github_response jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create or replace function public.is_admin()
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

revoke all on function public.is_admin() from public;
grant execute on function public.is_admin() to authenticated;

create or replace function public.grm_bootstrap_admin_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if lower(coalesce(new.email, '')) = lower('yeomminho1472@gmail.com') then
    insert into public.admin_user (user_id, email, role, revoked_at)
    values (new.id, new.email, 'Admin', null)
    on conflict (user_id) do update
    set email = excluded.email,
        role = 'Admin',
        revoked_at = null;
  end if;
  return new;
end;
$$;

drop trigger if exists grm_bootstrap_admin_user_on_auth_user on auth.users;
create trigger grm_bootstrap_admin_user_on_auth_user
after insert or update of email on auth.users
for each row execute function public.grm_bootstrap_admin_user();

insert into public.admin_user (user_id, email, role, revoked_at)
select id, email, 'Admin', null
from auth.users
where lower(email) = lower('yeomminho1472@gmail.com')
on conflict (user_id) do update
set email = excluded.email,
    role = 'Admin',
    revoked_at = null;

alter table public.admin_user enable row level security;
alter table public.admin_audit_log enable row level security;
alter table public.newsletter_dispatch_log enable row level security;

grant select on public.admin_user to authenticated;
grant select on public.admin_audit_log to authenticated;
grant select on public.newsletter_dispatch_log to authenticated;

drop policy if exists "admin users can read admin users" on public.admin_user;
create policy "admin users can read admin users"
on public.admin_user
for select
to authenticated
using (public.is_admin());

drop policy if exists "admin users can read audit log" on public.admin_audit_log;
create policy "admin users can read audit log"
on public.admin_audit_log
for select
to authenticated
using (public.is_admin());

drop policy if exists "admin users can read newsletter dispatch log" on public.newsletter_dispatch_log;
create policy "admin users can read newsletter dispatch log"
on public.newsletter_dispatch_log
for select
to authenticated
using (public.is_admin());

create index if not exists admin_user_email_idx on public.admin_user (lower(email));
create index if not exists admin_audit_log_created_at_idx on public.admin_audit_log (created_at desc);
create index if not exists newsletter_dispatch_log_created_at_idx on public.newsletter_dispatch_log (created_at desc);
create index if not exists newsletter_dispatch_log_publish_date_idx on public.newsletter_dispatch_log (publish_date desc);
