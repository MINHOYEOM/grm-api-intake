-- Replace the SECURITY DEFINER heart_counts view with a public aggregate table.
-- Individual reactions stay protected by reaction RLS; only per-card counts are public.

create table if not exists public.reaction_count (
  card_id text not null,
  kind text not null check (kind in ('heart', 'scrap')),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (card_id, kind)
);

alter table public.reaction_count enable row level security;

revoke all on public.reaction_count from public;
revoke all on public.reaction_count from anon;
revoke all on public.reaction_count from authenticated;
grant select on public.reaction_count to anon, authenticated;

drop policy if exists "public can read reaction counts" on public.reaction_count;
create policy "public can read reaction counts"
on public.reaction_count
for select
to anon, authenticated
using (true);

insert into public.reaction_count (card_id, kind, total, updated_at)
select card_id, kind, count(*)::int, now()
from public.reaction
group by card_id, kind
on conflict (card_id, kind) do update
set total = excluded.total,
    updated_at = excluded.updated_at;

create or replace function private.sync_reaction_count()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.reaction_count (card_id, kind, total, updated_at)
    values (new.card_id, new.kind, 1, now())
    on conflict (card_id, kind) do update
    set total = public.reaction_count.total + 1,
        updated_at = now();
    return new;
  end if;

  if tg_op = 'DELETE' then
    update public.reaction_count
    set total = greatest(total - 1, 0),
        updated_at = now()
    where card_id = old.card_id
      and kind = old.kind;

    delete from public.reaction_count
    where total <= 0;

    return old;
  end if;

  return null;
end;
$$;

revoke all on function private.sync_reaction_count() from public;
revoke all on function private.sync_reaction_count() from anon;
revoke all on function private.sync_reaction_count() from authenticated;

drop trigger if exists reaction_count_after_insert on public.reaction;
drop trigger if exists reaction_count_after_delete on public.reaction;

create trigger reaction_count_after_insert
after insert on public.reaction
for each row execute function private.sync_reaction_count();

create trigger reaction_count_after_delete
after delete on public.reaction
for each row execute function private.sync_reaction_count();

create or replace view public.heart_counts
with (security_invoker = true) as
select card_id, total as hearts
from public.reaction_count
where kind = 'heart'
  and total > 0;

grant select on public.heart_counts to anon, authenticated;
