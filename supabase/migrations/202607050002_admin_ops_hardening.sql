-- Hardening for Admin operations telemetry and duplicate-send protection.

alter table public.newsletter_dispatch_log
  add column if not exists workflow text,
  add column if not exists ref text,
  add column if not exists github_run_id bigint,
  add column if not exists github_run_status text,
  add column if not exists github_run_conclusion text;

create index if not exists newsletter_dispatch_log_workflow_idx
on public.newsletter_dispatch_log (workflow, created_at desc);

create index if not exists newsletter_dispatch_log_github_run_id_idx
on public.newsletter_dispatch_log (github_run_id)
where github_run_id is not null;

create unique index if not exists newsletter_dispatch_success_once_idx
on public.newsletter_dispatch_log (publish_date, mode)
where github_status >= 200 and github_status < 300;
