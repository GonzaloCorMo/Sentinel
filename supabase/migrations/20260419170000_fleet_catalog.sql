-- Catálogo persistente de flota: tipos de entidad (vehículos/lugares) y
-- unidades registradas por los usuarios con rol "vehicle". Las creadas por la
-- IA (autónoma o tras aprobación HITL) se persisten con source='ai'.

create table if not exists public.fleet_entity_types (
  id           text primary key,
  kind         text not null check (kind in ('vehicle','place')),
  name         text not null,
  speed_kmh    real,
  color        text,
  icon_svg     text,
  built_in     boolean not null default false,
  source       text not null default 'manual' check (source in ('manual','ai','builtin')),
  created_by   uuid references auth.users(id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists fleet_entity_types_kind_idx on public.fleet_entity_types (kind);

create table if not exists public.fleet_vehicles (
  id              uuid primary key default gen_random_uuid(),
  owner_user_id   uuid references auth.users(id) on delete cascade,
  entity_type_id  text not null,
  display_label   text not null,
  last_lat        double precision,
  last_lon        double precision,
  metadata        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists fleet_vehicles_owner_idx on public.fleet_vehicles (owner_user_id);
create index if not exists fleet_vehicles_type_idx  on public.fleet_vehicles (entity_type_id);
