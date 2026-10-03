-- ─────────────────────────────────────────────────────────────────────────────
-- Hace que la vista weather_stations_latest sea editable.
--
-- DISTINCT ON impide que PostgreSQL genere reglas de actualización
-- automáticas, así que añadimos triggers INSTEAD OF para INSERT, UPDATE
-- y DELETE que redirigen a la tabla subyacente weather_readings.
--
-- INSERT  → upsert por id (idempotente).
-- UPDATE  → upsert por id; si se omite received_at, se pone now().
-- DELETE  → elimina por id (la fila de la lectura concreta).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.weather_latest_instead_of()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.weather_readings (
            id, station_id, "timestamp",
            temperature_c, humidity_pct, wind_speed_kmh, wind_direction_deg,
            pressure_hpa, precipitation_mm, visibility_km, uv_index,
            received_at
        ) VALUES (
            NEW.id, NEW.station_id, NEW."timestamp",
            NEW.temperature_c, NEW.humidity_pct, NEW.wind_speed_kmh, NEW.wind_direction_deg,
            NEW.pressure_hpa, NEW.precipitation_mm, NEW.visibility_km, NEW.uv_index,
            COALESCE(NEW.received_at, now())
        )
        ON CONFLICT (id) DO UPDATE SET
            station_id         = EXCLUDED.station_id,
            "timestamp"        = EXCLUDED."timestamp",
            temperature_c      = EXCLUDED.temperature_c,
            humidity_pct       = EXCLUDED.humidity_pct,
            wind_speed_kmh     = EXCLUDED.wind_speed_kmh,
            wind_direction_deg = EXCLUDED.wind_direction_deg,
            pressure_hpa       = EXCLUDED.pressure_hpa,
            precipitation_mm   = EXCLUDED.precipitation_mm,
            visibility_km      = EXCLUDED.visibility_km,
            uv_index           = EXCLUDED.uv_index,
            received_at        = EXCLUDED.received_at;
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO public.weather_readings (
            id, station_id, "timestamp",
            temperature_c, humidity_pct, wind_speed_kmh, wind_direction_deg,
            pressure_hpa, precipitation_mm, visibility_km, uv_index,
            received_at
        ) VALUES (
            NEW.id, NEW.station_id, NEW."timestamp",
            NEW.temperature_c, NEW.humidity_pct, NEW.wind_speed_kmh, NEW.wind_direction_deg,
            NEW.pressure_hpa, NEW.precipitation_mm, NEW.visibility_km, NEW.uv_index,
            COALESCE(NEW.received_at, now())
        )
        ON CONFLICT (id) DO UPDATE SET
            station_id         = EXCLUDED.station_id,
            "timestamp"        = EXCLUDED."timestamp",
            temperature_c      = EXCLUDED.temperature_c,
            humidity_pct       = EXCLUDED.humidity_pct,
            wind_speed_kmh     = EXCLUDED.wind_speed_kmh,
            wind_direction_deg = EXCLUDED.wind_direction_deg,
            pressure_hpa       = EXCLUDED.pressure_hpa,
            precipitation_mm   = EXCLUDED.precipitation_mm,
            visibility_km      = EXCLUDED.visibility_km,
            uv_index           = EXCLUDED.uv_index,
            received_at        = EXCLUDED.received_at;
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM public.weather_readings WHERE id = OLD.id;
        RETURN OLD;
    END IF;

    RETURN NULL;
END;
$$;

-- Elimina el trigger anterior si existía (para idempotencia)
DROP TRIGGER IF EXISTS weather_latest_instead_of_trigger
    ON public.weather_stations_latest;

CREATE TRIGGER weather_latest_instead_of_trigger
    INSTEAD OF INSERT OR UPDATE OR DELETE
    ON public.weather_stations_latest
    FOR EACH ROW
    EXECUTE FUNCTION public.weather_latest_instead_of();
