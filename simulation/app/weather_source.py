"""Tiempo real de MeteoGalicia para la región activa.

Usa la red de observación abierta de MeteoGalicia (sin clave):

- ``listaEstacionsMeteo.action``: estaciones con coordenadas.
- ``ultimos10minEstacionsMeteo.action``: última lectura de 10 minutos de cada estación.

Las estaciones a menos de ``WEATHER_RADIUS_KM`` del centro de la región se
colocan en el mapa como lugares ``weather_station`` (id ``mg-<idEstacion>``)
y sus lecturas alimentan el motor (factor meteorológico de las rutas,
avisos del Panorama) y la tabla ``weather_readings``.

Conversión de unidades:
    - viento: m/s → km/h;
    - lluvia: suma de 10 minutos (L/m²) → intensidad en mm/h (× 6);
    - visibilidad: MeteoGalicia no la mide en estas estaciones; se estima a
      partir de la lluvia y la humedad (niebla con humedad ≥ 97 % sin lluvia).

``WEATHER_SOURCE=mock`` vuelve al tiempo sintético. Si MeteoGalicia no
responde nunca, se usan lecturas sintéticas para no dejar el motor sin
datos; en cuanto responde, se pasa a las reales.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

import httpx

from .regions import get_active_region
from .route_nav import haversine_m
from .schemas.external_events import WeatherReading

log = logging.getLogger(__name__)

_BASE = "https://servizos.meteogalicia.gal/mgrss/observacion"
_STATIONS_URL = f"{_BASE}/listaEstacionsMeteo.action"
_LATEST_URL = f"{_BASE}/ultimos10minEstacionsMeteo.action"
_MISSING = -9999.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def weather_source_mode() -> str:
    mode = (os.environ.get("WEATHER_SOURCE") or "meteogalicia").strip().lower()
    return mode if mode in {"meteogalicia", "mock"} else "meteogalicia"


def _value(measures: dict[str, float], *codes: str) -> float | None:
    for code in codes:
        v = measures.get(code)
        if v is not None and v != _MISSING:
            return float(v)
    return None


def estimate_visibility_km(precip_mmh: float, humidity_pct: float | None) -> float:
    """Visibilidad aproximada: lluvia intensa o niebla la reducen."""
    vis = 20.0
    if precip_mmh > 0:
        vis = min(vis, max(1.0, 12.0 - precip_mmh * 0.8))
    elif humidity_pct is not None and humidity_pct >= 97:
        vis = 0.8
    elif humidity_pct is not None and humidity_pct >= 93:
        vis = 4.0
    return round(vis, 1)


def to_reading(station_id: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """Fila de ``ultimos10min`` → ``WeatherReading`` (None si faltan datos básicos)."""
    measures = {
        str(m.get("codigoParametro")): m.get("valor")
        for m in row.get("listaMedidas") or []
        if isinstance(m.get("valor"), (int, float))
    }
    temp = _value(measures, "TA_AVG_1.5m", "TA_AVG_0.1m")
    if temp is None:
        return None
    humidity = _value(measures, "HR_AVG_1.5m")
    wind_ms = _value(measures, "VV_AVG_10m", "VV_AVG_2m") or 0.0
    rain_10min = _value(measures, "PP_SUM_1.5m") or 0.0
    precip_mmh = round(max(0.0, rain_10min) * 6.0, 1)
    instant = str(row.get("instanteLecturaUTC") or "")
    try:
        ts = datetime.fromisoformat(instant).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        ts = datetime.now(timezone.utc).isoformat()
    reading = WeatherReading(
        id=f"{station_id}-{instant or ts}",
        station_id=station_id,
        timestamp=ts,
        temperature_c=round(temp, 1),
        humidity_pct=round(min(100.0, max(0.0, humidity if humidity is not None else 75.0)), 1),
        wind_speed_kmh=round(max(0.0, wind_ms) * 3.6, 1),
        wind_direction_deg=round(min(360.0, max(0.0, _value(measures, "DV_AVG_10m", "DV_AVG_2m") or 0.0)), 1),
        pressure_hpa=round(min(1100.0, max(800.0, _value(measures, "PR_AVG_1.5m") or 1013.0)), 1),
        precipitation_mm=precip_mmh,
        visibility_km=estimate_visibility_km(precip_mmh, humidity),
        uv_index=0.0,
    )
    return reading.model_dump()


async def fetch_region_stations(client: httpx.AsyncClient, radius_km: float) -> list[dict[str, Any]]:
    """Estaciones de MeteoGalicia cerca del centro de la región activa."""
    r = await client.get(_STATIONS_URL, timeout=15.0)
    r.raise_for_status()
    region = get_active_region()
    out = []
    for s in r.json().get("listaEstacionsMeteo") or []:
        try:
            lat, lon = float(s["lat"]), float(s["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if haversine_m(region.center_lat, region.center_lon, lat, lon) <= radius_km * 1000:
            out.append({"id": f"mg-{s['idEstacion']}", "mgId": int(s["idEstacion"]),
                        "name": str(s.get("estacion") or "Estación"), "lat": lat, "lon": lon})
    return out


async def fetch_latest(client: httpx.AsyncClient, mg_ids: set[int]) -> dict[int, dict[str, Any]]:
    r = await client.get(_LATEST_URL, timeout=20.0)
    r.raise_for_status()
    return {
        int(row["idEstacion"]): row
        for row in r.json().get("listUltimos10min") or []
        if row.get("idEstacion") in mg_ids
    }


async def run_weather_source(engine: Any, *, seed: int | None = None) -> None:
    """Tarea de fondo: lecturas reales de MeteoGalicia (o sintéticas si así se configura)."""
    from .event_source import _WeatherDrift
    from .weather_db import upsert_weather_reading

    if weather_source_mode() != "meteogalicia":
        log.info("Tiempo: fuente sintética (WEATHER_SOURCE=mock)")
        return
    poll_s = max(60.0, _env_float("METEOGALICIA_INTERVAL_SEC", 600.0))
    radius_km = _env_float("WEATHER_RADIUS_KM", 18.0)
    drift = _WeatherDrift(random.Random(seed))
    stations: list[dict[str, Any]] = []
    last_ids: dict[str, str] = {}
    ever_ok = False
    next_poll = 0.0
    loop = asyncio.get_running_loop()
    log.info("Tiempo: MeteoGalicia (cada %.0f s, radio %.0f km)", poll_s, radius_km)
    async with httpx.AsyncClient(headers={"User-Agent": "Sentinel-sim/1.0"}) as client:
        while True:
            try:
                if loop.time() >= next_poll:
                    next_poll = loop.time() + poll_s
                    if not stations:
                        stations = await fetch_region_stations(client, radius_km)
                        log.info("Tiempo: %d estaciones de MeteoGalicia en la región", len(stations))
                    latest = await fetch_latest(client, {s["mgId"] for s in stations})
                    for st in stations:
                        row = latest.get(st["mgId"])
                        reading = to_reading(st["id"], row) if row else None
                        if reading is None or last_ids.get(st["id"]) == reading["id"]:
                            continue
                        last_ids[st["id"]] = reading["id"]
                        await engine.ingest_weather_reading(reading)
                        asyncio.create_task(upsert_weather_reading(reading))
                    ever_ok = True
                    engine.weather_source_status = {"source": "meteogalicia", "ok": True, "stations": len(stations),
                                                    "lastFetchAt": datetime.now(timezone.utc).isoformat()}
                # Las estaciones vuelven al mapa si se reinicia el escenario.
                await engine.ensure_weather_stations(stations)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Tiempo: MeteoGalicia no responde (%s)", exc)
                engine.weather_source_status = {"source": "meteogalicia", "ok": False, "stations": len(stations),
                                                "lastError": str(exc)[:160]}
                next_poll = loop.time() + 60.0
                if not ever_ok:
                    for sid in [s["id"] for s in stations] or ["mock-ws-1", "mock-ws-2"]:
                        await engine.ingest_weather_reading(drift.next(sid))
            await asyncio.sleep(30.0)
