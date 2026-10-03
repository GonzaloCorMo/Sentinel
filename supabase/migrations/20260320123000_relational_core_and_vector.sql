create extension if not exists vector;

create type public.ambulance_status as enum (
  'available',
  'dispatched',
  'in_service',
  'maintenance',
  'offline'
);

create type public.emergency_severity as enum (
  'low',
  'medium',
  'high',
  'critical'
);

create type public.emergency_status as enum (
  'open',
  'assigned',
  'resolved',
  'cancelled'
);

create table if not exists public.ambulance_units (
  id uuid primary key default gen_random_uuid(),
  external_id text not null unique,
  plate text unique,
  status public.ambulance_status not null default 'available',
  latitude double precision not null,
  longitude double precision not null,
  fuel_level smallint not null default 100 check (fuel_level between 0 and 100),
  speed_kmh real not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ambulance_units_status_idx
  on public.ambulance_units (status);

create index if not exists ambulance_units_last_seen_at_idx
  on public.ambulance_units (last_seen_at desc);

create table if not exists public.emergency_cases (
  id uuid primary key default gen_random_uuid(),
  external_id text not null unique,
  severity public.emergency_severity not null default 'medium',
  status public.emergency_status not null default 'open',
  latitude double precision not null,
  longitude double precision not null,
  metadata jsonb not null default '{}'::jsonb,
  opened_at timestamptz not null default now(),
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists emergency_cases_status_idx
  on public.emergency_cases (status);

create index if not exists emergency_cases_opened_at_idx
  on public.emergency_cases (opened_at desc);

create table if not exists public.emergency_dispatches (
  id uuid primary key default gen_random_uuid(),
  emergency_id uuid not null references public.emergency_cases(id) on delete cascade,
  ambulance_id uuid not null references public.ambulance_units(id) on delete cascade,
  command_source text not null default 'next-api',
  assignment_reason text,
  assigned_at timestamptz not null default now(),
  unassigned_at timestamptz,
  created_at timestamptz not null default now(),
  constraint emergency_dispatches_active_unique unique (emergency_id, ambulance_id, assigned_at)
);

create index if not exists emergency_dispatches_emergency_id_idx
  on public.emergency_dispatches (emergency_id);

create index if not exists emergency_dispatches_ambulance_id_idx
  on public.emergency_dispatches (ambulance_id);

create table if not exists public.ai_knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  source_type text not null,
  source_ref text,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1536),
  created_at timestamptz not null default now()
);

create index if not exists ai_knowledge_chunks_source_type_idx
  on public.ai_knowledge_chunks (source_type);

create index if not exists ai_knowledge_chunks_embedding_ivfflat_idx
  on public.ai_knowledge_chunks using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);
