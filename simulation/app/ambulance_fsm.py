"""FSM y heurísticas de autonomía de las unidades.

Expone:
    - `AmbulanceState`: estados de la FSM (IDLE, RESPONDING...).
    - `infer_fsm_state`: derivación del estado a partir de ``missionPhase``.
    - `can_accept_mission`: ¿tiene combustible + batería para ida+vuelta?
    - `estimate_trip_m`: longitud de ruta OSRM o fallback Haversine.
    - `count_ambs_near_hospital`: conteo para el algoritmo de staging.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from .route_nav import haversine_m, polyline_length_m
from .routing import fetch_route


class AmbulanceState(str, Enum):
    IDLE = "IDLE"
    RESPONDING = "RESPONDING"
    REFUELING = "REFUELING"
    STAGING = "STAGING"
    TRANSPORTING = "TRANSPORTING"
    UNAVAILABLE = "UNAVAILABLE"


def infer_fsm_state(amb: dict[str, Any]) -> AmbulanceState:
    """Deriva el estado FSM a partir de ``missionPhase`` (única fuente de verdad)."""
    phase = str(amb.get("missionPhase") or "idle").lower()
    if phase == "to_refuel":
        return AmbulanceState.REFUELING
    if phase == "to_emergency":
        return AmbulanceState.RESPONDING
    if phase == "to_hospital":
        return AmbulanceState.TRANSPORTING
    if phase == "to_staging":
        return AmbulanceState.STAGING
    return AmbulanceState.IDLE


def fuel_pct_for_round_trip_m(trip_one_way_m: float) -> float:
    """Heurística: consumo aproximado ida+vuelta (coherente con drenaje ~0.01%/100m del motor)."""
    km = trip_one_way_m / 1000.0
    return km * 0.2 * 2.0


def can_accept_mission(
    fuel_pct: float,
    battery_pct: float,
    trip_one_way_m: float,
    *,
    min_battery: float = 18.0,
    reserve_fuel: float = 8.0,
) -> bool:
    """¿Tiene recursos suficientes para ida+vuelta + reserva de seguridad?"""
    need = fuel_pct_for_round_trip_m(trip_one_way_m)
    return fuel_pct >= need + reserve_fuel and battery_pct >= min_battery


async def estimate_trip_m(
    http_client: Any,
    start: tuple[float, float],
    dest: tuple[float, float],
) -> float:
    """Longitud de ruta OSRM/fallback o distancia Haversine si falla."""
    route, _, _ = await fetch_route(http_client, [start, dest])
    if len(route) >= 2:
        return polyline_length_m(route)
    return haversine_m(start[0], start[1], dest[0], dest[1])


def count_ambs_near_hospital(
    ambulances: list[dict[str, Any]],
    hosp_lat: float,
    hosp_lon: float,
    radius_m: float,
) -> int:
    """Unidades IDLE o STAGING dentro de ``radius_m`` de un hospital."""
    n = 0
    for a in ambulances:
        if infer_fsm_state(a) not in (AmbulanceState.IDLE, AmbulanceState.STAGING):
            continue
        d = haversine_m(
            float(a.get("latitude") or 0),
            float(a.get("longitude") or 0),
            hosp_lat,
            hosp_lon,
        )
        if d <= radius_m:
            n += 1
    return n
