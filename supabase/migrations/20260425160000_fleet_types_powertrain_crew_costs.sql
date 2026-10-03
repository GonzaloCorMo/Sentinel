-- Añade powertrain (combustion|electric|unique), crew_min/crew_max, cost_per_min,
-- activation_cost a fleet_entity_types y precarga 9 vehículos builtin del catálogo
-- HPE Sentinel (matriz Policía/Ambulancia/Bomberos/Protección Civil × Combustión/Eléctrico
-- + Dron Único). Idempotente: re-ejecutable vía ON CONFLICT DO UPDATE.

ALTER TABLE public.fleet_entity_types
  ADD COLUMN IF NOT EXISTS powertrain text,
  ADD COLUMN IF NOT EXISTS crew_min integer,
  ADD COLUMN IF NOT EXISTS crew_max integer,
  ADD COLUMN IF NOT EXISTS cost_per_min numeric(10,2),
  ADD COLUMN IF NOT EXISTS activation_cost numeric(10,2);

-- Constraints. CHECK envuelto en DO $$ porque ADD CONSTRAINT no soporta IF NOT EXISTS
-- en versiones antiguas de Postgres; comprobamos pg_constraint primero.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fleet_entity_types_powertrain_chk'
  ) THEN
    ALTER TABLE public.fleet_entity_types
      ADD CONSTRAINT fleet_entity_types_powertrain_chk
        CHECK (powertrain IS NULL OR powertrain IN ('combustion','electric','unique'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fleet_entity_types_crew_chk'
  ) THEN
    ALTER TABLE public.fleet_entity_types
      ADD CONSTRAINT fleet_entity_types_crew_chk
        CHECK (crew_min IS NULL OR (crew_min >= 0 AND crew_max IS NOT NULL AND crew_max >= crew_min));
  END IF;
END $$;

-- Seed: 9 filas builtin coexistiendo con los IDs legacy (ambulance, police_patrol, etc.)
INSERT INTO public.fleet_entity_types
  (id, kind, name, speed_kmh, color, built_in, source,
   powertrain, crew_min, crew_max, cost_per_min, activation_cost, capabilities)
VALUES
  ('police_combustion',           'vehicle','Policía Combustión',          80, '#2563eb', true, 'builtin', 'combustion', 2, 2, 1.20, 15, '[]'::jsonb),
  ('police_electric',             'vehicle','Policía Eléctrico',           80, '#2563eb', true, 'builtin', 'electric',   2, 2, 0.80, 18, '[]'::jsonb),
  ('ambulance_combustion',        'vehicle','Ambulancia Combustión',       80, '#01a982', true, 'builtin', 'combustion', 2, 3, 2.50, 25, '[]'::jsonb),
  ('ambulance_electric',          'vehicle','Ambulancia Eléctrico',        80, '#01a982', true, 'builtin', 'electric',   2, 3, 1.80, 30, '[]'::jsonb),
  ('firetruck_combustion',        'vehicle','Bomberos Combustión',         70, '#dc2626', true, 'builtin', 'combustion', 4, 6, 4.00, 50, '[]'::jsonb),
  ('firetruck_electric',          'vehicle','Bomberos Eléctrico',          70, '#dc2626', true, 'builtin', 'electric',   4, 6, 3.00, 60, '[]'::jsonb),
  ('civil_protection_combustion', 'vehicle','Protección Civil Combustión', 70, '#f59e0b', true, 'builtin', 'combustion', 1, 2, 0.80, 10, '[]'::jsonb),
  ('civil_protection_electric',   'vehicle','Protección Civil Eléctrico',  70, '#f59e0b', true, 'builtin', 'electric',   1, 2, 0.50, 12, '[]'::jsonb),
  ('drone_unique',                'vehicle','Dron',                       200, '#06b6d4', true, 'builtin', 'unique',     0, 0, 0.30,  5, '[]'::jsonb)
ON CONFLICT (id) DO UPDATE SET
  kind            = EXCLUDED.kind,
  name            = EXCLUDED.name,
  speed_kmh       = EXCLUDED.speed_kmh,
  color           = EXCLUDED.color,
  built_in        = EXCLUDED.built_in,
  source          = EXCLUDED.source,
  powertrain      = EXCLUDED.powertrain,
  crew_min        = EXCLUDED.crew_min,
  crew_max        = EXCLUDED.crew_max,
  cost_per_min    = EXCLUDED.cost_per_min,
  activation_cost = EXCLUDED.activation_cost,
  updated_at      = now();
