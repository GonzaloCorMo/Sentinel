"""Contratos de la fuente de eventos externa (eventos operativos + clima).

Los usan el generador mock local (`event_source.py`) y los endpoints REST
de ingesta (`POST /api/events/ingest`, `POST /api/weather/ingest`).
Validación estricta (`extra="forbid"`): payloads con campos extra o tipos
incorrectos se rechazan y no se propagan al motor.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EventType = Literal[
    "storm",
    "fire",
    "flood",
    "accident",
    "lane_closure",
    "power_outage",
    "medical_emergency",
    "hazmat_spill",
    "construction",
    "public_event",
]

EventSeverity = Literal["low", "medium", "high", "critical"]


class WeatherReading(BaseModel):
    """Lectura puntual de una estación meteorológica."""

    model_config = {"extra": "forbid"}

    id: str
    station_id: str
    timestamp: str
    temperature_c: float = Field(ge=-50, le=60)
    humidity_pct: float = Field(ge=0, le=100)
    wind_speed_kmh: float = Field(ge=0, le=500)
    wind_direction_deg: float = Field(ge=0, le=360)
    pressure_hpa: float = Field(ge=800, le=1100)
    precipitation_mm: float = Field(ge=0)
    visibility_km: float = Field(ge=0, le=100)
    uv_index: float = Field(ge=0, le=15)


class ExternalEvent(BaseModel):
    """Evento crítico/operativo georreferenciado (incidente, emergencia, etc.)."""

    model_config = {"extra": "forbid"}

    id: str
    type: EventType
    severity: EventSeverity
    title: str = Field(max_length=200)
    description: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float | None = Field(default=None, ge=0)
    road_id: str | None = None
    started_at: str
    resolved_at: str | None = None
    geometry: list[list[float]] | None = None

