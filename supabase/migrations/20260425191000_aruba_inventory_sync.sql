-- Snapshot y trazabilidad de sincronización de inventario Aruba (POIs + carreteras).
-- Diseñado para upsert idempotente por (region_id, external_id).

CREATE TABLE IF NOT EXISTS public.external_pois_inventory (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  region_id text NOT NULL,
  external_id text NOT NULL,
  kind text NOT NULL,
  name text NOT NULL,
  latitude double precision NOT NULL,
  longitude double precision NOT NULL,
  source text NOT NULL DEFAULT 'aruba_api',
  raw jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT external_pois_inventory_region_external_uniq UNIQUE (region_id, external_id)
);

CREATE INDEX IF NOT EXISTS external_pois_inventory_region_idx
  ON public.external_pois_inventory (region_id);
CREATE INDEX IF NOT EXISTS external_pois_inventory_updated_idx
  ON public.external_pois_inventory (updated_at DESC);

CREATE TABLE IF NOT EXISTS public.external_roads_inventory (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  region_id text NOT NULL,
  external_id text NOT NULL,
  name text,
  road_type text,
  start_lat double precision NOT NULL,
  start_lon double precision NOT NULL,
  end_lat double precision NOT NULL,
  end_lon double precision NOT NULL,
  speed_limit_kmh double precision,
  lanes integer,
  length_m double precision,
  geometry jsonb,
  source text NOT NULL DEFAULT 'aruba_api',
  raw jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT external_roads_inventory_region_external_uniq UNIQUE (region_id, external_id)
);

CREATE INDEX IF NOT EXISTS external_roads_inventory_region_idx
  ON public.external_roads_inventory (region_id);
CREATE INDEX IF NOT EXISTS external_roads_inventory_updated_idx
  ON public.external_roads_inventory (updated_at DESC);

CREATE TABLE IF NOT EXISTS public.inventory_sync_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  region_id text NOT NULL,
  status text NOT NULL,
  ok boolean NOT NULL DEFAULT false,
  fetched_pois integer NOT NULL DEFAULT 0,
  fetched_roads integer NOT NULL DEFAULT 0,
  updated_pois integer NOT NULL DEFAULT 0,
  updated_roads integer NOT NULL DEFAULT 0,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inventory_sync_events_region_idx
  ON public.inventory_sync_events (region_id);
CREATE INDEX IF NOT EXISTS inventory_sync_events_created_idx
  ON public.inventory_sync_events (created_at DESC);

ALTER TABLE public.external_pois_inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.external_roads_inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_sync_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'external_pois_inventory' AND policyname = 'external_pois_service_role_all'
  ) THEN
    CREATE POLICY external_pois_service_role_all
      ON public.external_pois_inventory
      FOR ALL
      TO public
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'external_roads_inventory' AND policyname = 'external_roads_service_role_all'
  ) THEN
    CREATE POLICY external_roads_service_role_all
      ON public.external_roads_inventory
      FOR ALL
      TO public
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'inventory_sync_events' AND policyname = 'inventory_sync_events_service_role_all'
  ) THEN
    CREATE POLICY inventory_sync_events_service_role_all
      ON public.inventory_sync_events
      FOR ALL
      TO public
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END $$;
