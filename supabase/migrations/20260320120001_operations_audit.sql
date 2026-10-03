create table if not exists public.operation_events (
  id uuid primary key default gen_random_uuid(),
  action text not null,
  status text not null default 'ok',
  source text not null default 'next-api',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists operation_events_created_at_idx
  on public.operation_events (created_at desc);

create table if not exists public.simulation_snapshots (
  id uuid primary key default gen_random_uuid(),
  ambulance_count int not null default 0,
  emergency_count int not null default 0,
  simulation_speed numeric not null default 1,
  is_simulating boolean not null default true,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists simulation_snapshots_created_at_idx
  on public.simulation_snapshots (created_at desc);
