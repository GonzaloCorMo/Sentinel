"""Persistencia de lecturas meteorológicas (aruba.weather) en Supabase.

El backend usa ``SUPABASE_SERVICE_ROLE_KEY`` (bypass RLS). Todas las
operaciones son async-safe: el cliente ``supabase-py`` es sincrónico, así
que las envolvemos con ``asyncio.to_thread`` para no bloquear el event loop.

Tabla destino : ``public.weather_readings``
Vista de apoyo: ``public.weather_stations_latest``  (DISTINCT ON station_id)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

_TABLE = "weather_readings"
_VIEW_LATEST = "weather_stations_latest"

# Columnas del payload AsyncAPI WeatherReading que se mapean 1-a-1 a la tabla.
_READING_COLS = (
    "id",
    "station_id",
    "timestamp",
    "temperature_c",
    "humidity_pct",
    "wind_speed_kmh",
    "wind_direction_deg",
    "pressure_hpa",
    "precipitation_mm",
    "visibility_km",
    "uv_index",
)


def _sb() -> Any | None:
    from .supabase_client import get_supabase

    return get_supabase()


def _extract_row(reading: dict[str, Any]) -> dict[str, Any]:
    """Extrae solo las columnas de la tabla (descarta receivedAt, etc.)."""
    row = {col: reading[col] for col in _READING_COLS if col in reading}
    # Pydantic serializa timestamp como str ISO-8601; PostgreSQL lo acepta.
    return row


# ─────────────────────────────── write ────────────────────────────────────────


async def upsert_weather_reading(reading: dict[str, Any]) -> None:
    """Upsert idempotente de una lectura. Seguro para fire-and-forget.

    Si la DB no está disponible o el upsert falla, se registra un warning
    y se continúa — la lectura sigue disponible en el in-memory del engine.
    """
    sb = _sb()
    if sb is None:
        return
    row = _extract_row(reading)
    if not row.get("id") or not row.get("station_id"):
        log.warning("weather_db: lectura sin id/station_id, descartando")
        return
    try:
        await asyncio.to_thread(
            lambda: sb.table(_TABLE).upsert(row, on_conflict="id").execute()
        )
    except Exception as exc:
        log.warning(
            "weather_db upsert failed (station=%s id=%s): %s",
            row.get("station_id"),
            row.get("id"),
            exc,
        )


# ─────────────────────────────── read ─────────────────────────────────────────


async def get_latest_reading(station_id: str) -> dict[str, Any] | None:
    """Lectura más reciente para una estación. None si no hay datos o DB off."""
    sb = _sb()
    if sb is None:
        return None
    try:
        result = await asyncio.to_thread(
            lambda: (
                sb.table(_VIEW_LATEST)
                .select("*")
                .eq("station_id", station_id)
                .limit(1)
                .execute()
            )
        )
        data: list[dict[str, Any]] = getattr(result, "data", None) or []
        return data[0] if data else None
    except Exception as exc:
        log.warning("weather_db get_latest failed (station=%s): %s", station_id, exc)
        return None


async def get_all_latest_readings() -> dict[str, dict[str, Any]]:
    """Última lectura por estación (via vista weather_stations_latest).

    Devuelve dict ``{station_id: row}``. Vacío si DB no disponible o sin datos.
    """
    sb = _sb()
    if sb is None:
        return {}
    try:
        result = await asyncio.to_thread(
            lambda: sb.table(_VIEW_LATEST).select("*").execute()
        )
        data: list[dict[str, Any]] = getattr(result, "data", None) or []
        return {row["station_id"]: row for row in data if row.get("station_id")}
    except Exception as exc:
        log.warning("weather_db get_all_latest failed: %s", exc)
        return {}


async def get_station_ids_from_db() -> list[str]:
    """Lista de station_ids conocidos en DB (para reconstruir la lista de
    estaciones tras un reinicio, cuando engine.weather_by_station está vacío)."""
    readings = await get_all_latest_readings()
    return list(readings.keys())
