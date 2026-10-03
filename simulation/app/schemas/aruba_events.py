"""Modelos AsyncAPI 3.0.0 — Aruba Pulse (`aruba.events` y `aruba.weather`).

Alineados con la especificación oficial documentada en
`hpe-docs/events-sync.md` §5. Validación estricta (`extra="forbid"`):
mensajes con campos extra o tipos incorrectos se descartan en el
consumer, no se propagan al motor.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

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
    """Lectura puntual de una estación meteorológica de Aruba."""

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


class ArubaEvent(BaseModel):
    """Evento crítico/operativo en Aruba (incidente, emergencia, etc.)."""

    model_config = {"extra": "forbid"}

    id: str
    type: EventType
    severity: EventSeverity
    title: str = Field(max_length=200)
    description: str
    latitude: float = Field(ge=12.4, le=12.7)
    longitude: float = Field(ge=-70.1, le=-69.8)
    radius_m: float | None = Field(default=None, ge=0)
    road_id: str | None = None
    started_at: str
    resolved_at: str | None = None
    geometry: list[list[float]] | None = None


def _decode(raw: bytes | str | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("aruba payload decode failed: %s", exc)
        return None
    if not isinstance(obj, dict):
        log.warning("aruba payload is not a JSON object: %r", type(obj).__name__)
        return None
    return obj


def parse_event(raw: bytes | str | dict[str, Any]) -> ArubaEvent | None:
    obj = _decode(raw)
    if obj is None:
        return None
    try:
        return ArubaEvent.model_validate(obj)
    except ValidationError as exc:
        log.warning("aruba event validation failed: %s (id=%s)", exc.error_count(), obj.get("id"))
        return None


def parse_weather(raw: bytes | str | dict[str, Any]) -> WeatherReading | None:
    obj = _decode(raw)
    if obj is None:
        return None
    try:
        return WeatherReading.model_validate(obj)
    except ValidationError as exc:
        log.warning(
            "aruba weather validation failed: %s (station=%s)",
            exc.error_count(),
            obj.get("station_id"),
        )
        return None
