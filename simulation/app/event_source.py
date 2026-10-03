"""Fuente de eventos externos del gemelo digital.

Sustituye al antiguo consumer de mensajería: alimenta el motor con
eventos operativos (incidentes, cortes, emergencias) y lecturas
meteorológicas. Dos vías de entrada, ambas con el mismo contrato
(`schemas/external_events.py`):

- **Mock local** (`EVENT_SOURCE=mock`, por defecto): genera eventos y
  clima sintéticos dentro del entorno de la región activa para que el
  dashboard tenga datos en tiempo real sin infraestructura externa.
- **REST** (`POST /api/events/ingest`, `POST /api/weather/ingest`): para
  conectar una fuente real. Funciona aunque el mock esté apagado.

Variables de entorno:
    EVENT_SOURCE               mock | off           (default: mock)
    MOCK_EVENT_INTERVAL_SEC    segundos entre eventos (default: 25)
    MOCK_WEATHER_INTERVAL_SEC  segundos entre lecturas (default: 10)
    MOCK_WEATHER_STATIONS      estaciones sintéticas si no hay POIs
                               `weather_station` en el mapa (default: 4)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .regions import get_active_region
from .schemas.external_events import ExternalEvent, WeatherReading

log = logging.getLogger(__name__)

# Los eventos se reparten algo más que las emergencias (área metropolitana).
_SPREAD_FACTOR = 1.5
# Tipos que ocurren en la calzada: se prefieren avenidas y carreteras.
_ROAD_TYPES = {"accident", "lane_closure", "construction", "hazmat_spill", "flood"}

# (tipo, severidades posibles, peso relativo, títulos)
_EVENT_CATALOG: list[tuple[str, tuple[str, ...], int, tuple[str, ...]]] = [
    ("accident", ("medium", "high", "critical"), 5, ("Colisión múltiple", "Accidente de tráfico", "Atropello")),
    ("medical_emergency", ("high", "critical"), 5, ("Parada cardiorrespiratoria", "Persona inconsciente", "Dificultad respiratoria")),
    ("lane_closure", ("low", "medium"), 4, ("Corte de carril", "Calzada bloqueada")),
    ("construction", ("low",), 2, ("Obras en la vía",)),
    ("fire", ("high", "critical"), 2, ("Incendio en vivienda", "Incendio de vegetación")),
    ("power_outage", ("low", "medium"), 2, ("Corte de suministro eléctrico",)),
    ("flood", ("medium", "high"), 1, ("Inundación en paso inferior",)),
    ("hazmat_spill", ("high",), 1, ("Derrame de sustancia peligrosa",)),
    ("public_event", ("low",), 1, ("Evento multitudinario",)),
    ("storm", ("medium",), 1, ("Tormenta local",)),
]

# Descripción breve por tipo (lo que vería un operador en el aviso).
_DESCRIPTIONS: dict[str, str] = {
    "accident": "Aviso ciudadano de colisión con posibles heridos.",
    "medical_emergency": "Llamada al 112 por persona que necesita atención urgente.",
    "lane_closure": "Carril cortado; tráfico desviado.",
    "construction": "Obras con ocupación parcial de la calzada.",
    "fire": "Humo visible y aviso de vecinos.",
    "power_outage": "Corte de suministro eléctrico en la zona.",
    "flood": "Acumulación de agua que dificulta el paso.",
    "hazmat_spill": "Vertido de sustancia desconocida en la vía.",
    "public_event": "Gran afluencia de público en la zona.",
    "storm": "Tormenta con rachas fuertes de viento.",
}

# Radio por defecto (m) para los tipos viales: el motor los convierte en atascos que el routing evita.
_ROAD_RADIUS_M = {"accident": 150.0, "lane_closure": 120.0, "construction": 200.0, "hazmat_spill": 250.0}


@dataclass(frozen=True)
class EventSourceConfig:
    mode: str
    event_interval_sec: float
    weather_interval_sec: float
    weather_stations: int

    @property
    def enabled(self) -> bool:
        return self.mode == "mock"


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name) or default))
    except ValueError:
        return default


def load_event_source_config() -> EventSourceConfig:
    mode = (os.environ.get("EVENT_SOURCE") or "mock").strip().lower()
    return EventSourceConfig(
        mode=mode if mode in {"mock", "off"} else "mock",
        event_interval_sec=_env_float("MOCK_EVENT_INTERVAL_SEC", 25.0, 2.0),
        weather_interval_sec=_env_float("MOCK_WEATHER_INTERVAL_SEC", 10.0, 1.0),
        weather_stations=int(_env_float("MOCK_WEATHER_STATIONS", 4, 1)),
    )


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


async def build_mock_event(rng: random.Random, http: Any = None) -> dict[str, Any]:
    """Evento sintético válido contra `ExternalEvent`, situado en una calle real de la región activa."""
    from .emergency_catalog import is_major_road
    from .placement import random_road_point

    ev_type, severities, _w, titles = rng.choices(_EVENT_CATALOG, weights=[c[2] for c in _EVENT_CATALOG])[0]
    region = get_active_region()
    point = None
    for _ in range(4 if ev_type in _ROAD_TYPES else 1):
        point = await random_road_point(http, region.center, region.urban_sigma_m * _SPREAD_FACTOR, rng=rng)
        if ev_type not in _ROAD_TYPES or is_major_road(point.street):
            break
    assert point is not None
    description = _DESCRIPTIONS.get(ev_type, "Aviso recibido de la red de incidencias.")
    if point.street:
        description = f"{description} Ubicación: {point.street}."
    event = ExternalEvent(
        id=f"mock-{uuid4().hex[:12]}",
        type=ev_type,  # type: ignore[arg-type]
        severity=rng.choice(severities),  # type: ignore[arg-type]
        title=rng.choice(titles),
        description=description,
        latitude=round(point.lat, 6),
        longitude=round(point.lon, 6),
        radius_m=_ROAD_RADIUS_M.get(ev_type),
        started_at=_iso(),
    )
    return event.model_dump()


class _WeatherDrift:
    """Random walk acotado por estación para que las series sean verosímiles."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._state: dict[str, dict[str, float]] = {}

    def next(self, station_id: str) -> dict[str, Any]:
        rng = self._rng
        st = self._state.setdefault(
            station_id,
            {
                "temperature_c": rng.uniform(24, 30),
                "humidity_pct": rng.uniform(55, 80),
                "wind_speed_kmh": rng.uniform(8, 22),
                "wind_direction_deg": rng.uniform(40, 120),
                "pressure_hpa": rng.uniform(1008, 1016),
                "precipitation_mm": 0.0,
                "visibility_km": rng.uniform(9, 12),
            },
        )

        def walk(key: str, step: float, lo: float, hi: float) -> float:
            st[key] = min(hi, max(lo, st[key] + rng.uniform(-step, step)))
            return round(st[key], 1)

        # Chubascos ocasionales: suben precipitación y bajan visibilidad.
        if rng.random() < 0.04:
            st["precipitation_mm"] = rng.uniform(3, 12)
        else:
            st["precipitation_mm"] = max(0.0, st["precipitation_mm"] * 0.7 - 0.2)
        st["visibility_km"] = 12.0 - min(8.0, st["precipitation_mm"] * 0.6) + rng.uniform(-0.3, 0.3)

        reading = WeatherReading(
            id=f"mock-{station_id}-{uuid4().hex[:8]}",
            station_id=station_id,
            timestamp=_iso(),
            temperature_c=walk("temperature_c", 0.3, 18, 38),
            humidity_pct=walk("humidity_pct", 1.5, 30, 100),
            wind_speed_kmh=walk("wind_speed_kmh", 1.5, 0, 60),
            wind_direction_deg=walk("wind_direction_deg", 8, 0, 360),
            pressure_hpa=walk("pressure_hpa", 0.4, 990, 1030),
            precipitation_mm=round(st["precipitation_mm"], 1),
            visibility_km=round(max(0.5, st["visibility_km"]), 1),
            uv_index=round(rng.uniform(4, 10), 1),
        )
        return reading.model_dump()


def _station_ids(engine: Any, fallback_count: int) -> list[str]:
    """Usa los POIs `weather_station` del mapa (se pintan en el dashboard); si no hay, ids sintéticos."""
    ids = [str(p["id"]) for p in engine.pois if p.get("kind") == "weather_station" and p.get("id")]
    return ids or [f"mock-ws-{i + 1}" for i in range(fallback_count)]


async def _resolve_later(engine: Any, event: dict[str, Any], after_s: float) -> None:
    await asyncio.sleep(after_s)
    resolved = dict(event)
    resolved["resolved_at"] = _iso()
    await engine.ingest_external_event(resolved)


async def run_event_source(engine: Any, *, seed: int | None = None) -> None:
    """Tarea de fondo del lifespan: alimenta el motor según `EVENT_SOURCE`."""
    cfg = load_event_source_config()
    engine.set_event_source_status({"enabled": cfg.enabled, "source": cfg.mode, "status": "running" if cfg.enabled else "disabled", "lastError": None})
    if not cfg.enabled:
        log.info("Event source: mock desactivado (EVENT_SOURCE=%s); solo ingesta REST", cfg.mode)
        return

    log.info(
        "Event source: mock local (eventos cada %.0fs, clima cada %.0fs)",
        cfg.event_interval_sec,
        cfg.weather_interval_sec,
    )
    rng = random.Random(seed)
    drift = _WeatherDrift(rng)
    loop = asyncio.get_running_loop()
    next_event_at = loop.time() + min(5.0, cfg.event_interval_sec)
    pending: set[asyncio.Task[None]] = set()
    try:
        while True:
            try:
                for sid in _station_ids(engine, cfg.weather_stations):
                    await engine.ingest_weather_reading(drift.next(sid))
                if loop.time() >= next_event_at:
                    event = await build_mock_event(rng, getattr(engine, "_http", None))
                    await engine.ingest_external_event(event)
                    # Los eventos se autorresuelven pasado un rato para que el mapa no se sature.
                    task = asyncio.create_task(_resolve_later(engine, event, rng.uniform(120, 420)))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                    jitter = rng.uniform(0.6, 1.4)
                    next_event_at = loop.time() + cfg.event_interval_sec * jitter
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # el mock nunca debe tumbar el lifespan
                log.warning("Event source: fallo generando datos mock: %s", exc)
                engine.set_event_source_status({"status": f"error:{type(exc).__name__}", "lastError": str(exc)})
            await asyncio.sleep(cfg.weather_interval_sec)
    finally:
        for task in pending:
            task.cancel()


__all__ = ["EventSourceConfig", "build_mock_event", "load_event_source_config", "run_event_source"]
