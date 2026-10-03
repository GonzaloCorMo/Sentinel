"""Motor de posicionamiento: GPS / cinemática y entorno vial."""
from __future__ import annotations

import math
import random
from typing import Any

from ..ambulance_fsm import AmbulanceState, infer_fsm_state


class PositioningEngine:
    """Genera lat/lon, velocidad, aceleración, rumbo, HDOP, satélites.

    Dos ramas según ``_navHint.on_route``:
        - On-route: usa los valores precalculados por el motor
          (posición, heading, aceleración longitudinal) y añade jitter
          en HDOP/accuracy.
        - Off-route: simula una pose estacionaria con ruido mínimo.
    """

    def __init__(self) -> None:
        self._phase: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}

    def _rng_for(self, amb_id: str) -> random.Random:
        """RNG estable por unidad (seed = hash + golden ratio)."""
        if amb_id not in self._rng:
            self._rng[amb_id] = random.Random((hash(amb_id) ^ 0x9E3779B9) % (2**32))
        return self._rng[amb_id]

    def reset_ambulance(self, amb_id: str) -> None:
        """Olvida estado al eliminar la unidad."""
        self._phase.pop(amb_id, None)
        self._rng.pop(amb_id, None)

    def tick(self, amb_id: str, tick_index: int, dt: float, amb: dict[str, Any]) -> dict[str, Any]:
        """Emite telemetría de posicionamiento del tick."""
        nav = amb.get("_navHint") or {}
        rng = self._rng_for(amb_id)

        if nav.get("on_route"):
            lat = float(amb.get("latitude") or 0.0)
            lon = float(amb.get("longitude") or 0.0)
            speed_ms = float(nav.get("speed_ms") or 12.0)
            speed_kmh = speed_ms * 3.6
            heading = float(nav.get("heading_deg") or 0.0)
            accel = float(nav.get("long_accel_ms2") or 0.0)
            road_limit = nav.get("road_speed_limit_kmh")
            hdop = max(0.8, float(nav.get("gps_hdop") or 1.0) + rng.gauss(0, 0.05))
            acc_m = max(1.0, 2.5 * hdop + abs(rng.gauss(0, 0.3)))

            return {
                "latitude": lat,
                "longitude": lon,
                "speedKmh": round(speed_kmh, 2),
                "speedMs": round(speed_ms, 2),
                "accelerationMs2": round(accel, 3),
                "headingDeg": round(heading, 1),
                "roadSpeedLimitKmh": road_limit,
                "gpsHdop": round(hdop, 2),
                "gpsAccuracyM": round(acc_m, 1),
            }

        rc = amb.get("routeCoords")
        fsm = infer_fsm_state(amb)
        if fsm == AmbulanceState.IDLE and (not rc or len(rc) < 2):
            lat = float(amb.get("latitude") or 0.0)
            lon = float(amb.get("longitude") or 0.0)
            road_limit = nav.get("road_speed_limit_kmh")
            hdop = max(0.85, 1.0 + rng.gauss(0, 0.04))
            acc_m = max(1.5, 2.5 * hdop)
            return {
                "latitude": lat,
                "longitude": lon,
                "speedKmh": 0.0,
                "speedMs": 0.0,
                "accelerationMs2": 0.0,
                "headingDeg": 0.0,
                "roadSpeedLimitKmh": road_limit,
                "gpsHdop": round(hdop, 2),
                "gpsAccuracyM": round(acc_m, 1),
            }

        # REFUELING sin polilínea: no usar el paseo aleatorio (rompía repostaje / telemetría).
        if fsm == AmbulanceState.REFUELING and (not rc or len(rc) < 2):
            lat = float(amb.get("latitude") or 0.0)
            lon = float(amb.get("longitude") or 0.0)
            road_limit = nav.get("road_speed_limit_kmh")
            hdop = max(0.85, 1.0 + rng.gauss(0, 0.04))
            acc_m = max(1.5, 2.5 * hdop)
            return {
                "latitude": lat,
                "longitude": lon,
                "speedKmh": 0.0,
                "speedMs": 0.0,
                "accelerationMs2": 0.0,
                "headingDeg": 0.0,
                "roadSpeedLimitKmh": road_limit,
                "gpsHdop": round(hdop, 2),
                "gpsAccuracyM": round(acc_m, 1),
            }

        phase = self._phase.get(amb_id, 0.0)
        phase += dt * 0.8
        self._phase[amb_id] = phase

        lat = float(amb.get("latitude") or 0.0)
        lon = float(amb.get("longitude") or 0.0)
        dlat = 0.00008 * math.sin(phase)
        dlon = 0.00008 * math.cos(phase * 1.1)
        speed_ms = 12.0 + 3.0 * math.sin(phase * 0.5)
        speed_kmh = speed_ms * 3.6
        heading = (math.degrees(math.atan2(dlon, dlat)) + 360.0) % 360.0
        accel = 0.15 * math.cos(phase * 2.0)
        road_limit = nav.get("road_speed_limit_kmh")
        hdop = max(0.9, 1.4 + 0.3 * math.sin(phase * 0.7) + rng.gauss(0, 0.08))
        acc_m = max(2.0, 3.0 * hdop)

        # Campos extendidos: satelites, constelación, altitud, jerk, drift
        sat_count = int(max(4, 12 + 3 * math.sin(phase * 0.2) + rng.gauss(0, 1)))
        altitude_m = 650.0 + 20.0 * math.sin(phase * 0.12) + rng.gauss(0, 1.0)
        vertical_speed_ms = round(0.3 * math.cos(phase * 0.3) + rng.gauss(0, 0.05), 3)
        jerk_ms3 = round(0.1 * math.sin(phase * 3.0), 3)
        gps_fix = "3D_RTK" if hdop < 1.2 else ("3D" if hdop < 2.0 else "2D")
        cep_68_m = round(max(1.2, 2.0 * hdop), 2)
        cog_deg = round(heading, 1)
        # Proximidad a objetos (simulado — LIDAR / radar)
        forward_clearance_m = max(0.8, 35.0 - 0.3 * speed_kmh + rng.gauss(0, 1.0))
        lane_offset_cm = round(rng.gauss(0, 12), 1)

        return {
            "latitude": lat + dlat,
            "longitude": lon + dlon,
            "altitudeM": round(altitude_m, 2),
            "speedKmh": round(speed_kmh, 2),
            "speedMs": round(speed_ms, 2),
            "verticalSpeedMs": vertical_speed_ms,
            "accelerationMs2": round(accel, 3),
            "jerkMs3": jerk_ms3,
            "headingDeg": round(heading, 1),
            "courseOverGroundDeg": cog_deg,
            "roadSpeedLimitKmh": road_limit,
            "gpsHdop": round(hdop, 2),
            "gpsAccuracyM": round(acc_m, 1),
            "gpsFixType": gps_fix,
            "satellitesUsed": sat_count,
            "cepMeters68Pct": cep_68_m,
            "forwardClearanceM": round(forward_clearance_m, 2),
            "laneOffsetCm": lane_offset_cm,
            "constellation": "GPS+GLONASS+Galileo",
        }
