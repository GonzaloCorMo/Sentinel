-- ─────────────────────────────────────────────────────────────────────────────
-- weather_readings: persiste todas las lecturas recibidas del topic Kafka
-- `aruba.weather`. Primary key en `id` (uuid que manda el broker); el upsert
-- de la app es idempotente — mensajes duplicados no producen filas extra.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.weather_readings (
    id                 text             PRIMARY KEY,
    station_id         text             NOT NULL,
    "timestamp"        timestamptz      NOT NULL,
    temperature_c      double precision NOT NULL,
    humidity_pct       double precision NOT NULL,
    wind_speed_kmh     double precision NOT NULL,
    wind_direction_deg double precision NOT NULL,
    pressure_hpa       double precision NOT NULL,
    precipitation_mm   double precision NOT NULL,
    visibility_km      double precision NOT NULL,
    uv_index           double precision NOT NULL,
    received_at        timestamptz      NOT NULL DEFAULT now()
);

-- Índice compuesto: acceso rápido a "última lectura por estación"
CREATE INDEX IF NOT EXISTS idx_weather_readings_station_ts
    ON public.weather_readings (station_id, "timestamp" DESC);

-- Vista de conveniencia: última lectura por estación (DISTINCT ON es O(n log n)
-- sobre el índice, muy eficiente con pocas estaciones).
CREATE OR REPLACE VIEW public.weather_stations_latest AS
SELECT DISTINCT ON (station_id) *
FROM   public.weather_readings
ORDER  BY station_id, "timestamp" DESC;

-- RLS: el backend usa service_role (bypass total). Anon/authenticated solo leen.
ALTER TABLE public.weather_readings ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'weather_readings' AND policyname = 'anon_select_weather'
    ) THEN
        CREATE POLICY "anon_select_weather" ON public.weather_readings
            FOR SELECT TO anon, authenticated USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'weather_readings' AND policyname = 'service_all_weather'
    ) THEN
        CREATE POLICY "service_all_weather" ON public.weather_readings
            FOR ALL TO service_role USING (true);
    END IF;
END $$;
