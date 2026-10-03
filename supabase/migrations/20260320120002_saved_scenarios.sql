create table if not exists public.saved_scenarios (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists saved_scenarios_updated_at_idx
  on public.saved_scenarios (updated_at desc);
