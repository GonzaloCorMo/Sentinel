-- Metadatos de tipos de flota: descripción y capacidades.
-- Se completan automáticamente con IA si el usuario no los rellena al guardar.

alter table public.fleet_entity_types
  add column if not exists description text,
  add column if not exists capabilities jsonb;

update public.fleet_entity_types
   set capabilities = '[]'::jsonb
 where capabilities is null;

alter table public.fleet_entity_types
  alter column capabilities set default '[]'::jsonb;
