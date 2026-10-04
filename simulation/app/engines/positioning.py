"""Motor de posicionamiento: GPS / cinemática y entorno vial."""
from __future__ import annotations

import random
from typing import Any



class PositioningEngine:
    """Genera lat/lon, velocidad, aceleración, rumbo, HDOP, satélites.

    Dos ramas según ``_navHint.on_route``:
        - On-route: usa los valores precalculados por el motor
          (posición, heading, aceleración longitudinal) y añade jitter
          en HDOP/accuracy.
        - Sin ruta: unidad detenida, posición fija y velocidad cero.
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

        # Sin ruta activa la unidad está detenida (libre, en el lugar, en
        # transferencia, repostando…): posición fija y velocidad cero. Antes
        # se añadía una oscilación de ~9 m que el motor reescribía en la
        # posición en cada tick y hacía que las unidades «se mecieran».
        lat = float(amb.get("latitude") or 0.0)
        lon = float(amb.get("longitude") or 0.0)
        hdop = max(0.85, 1.0 + rng.gauss(0, 0.04))
        prev_heading = amb.get("_prevHeadingDeg")
        return {
            "latitude": lat,
            "longitude": lon,
            "speedKmh": 0.0,
            "speedMs": 0.0,
            "accelerationMs2": 0.0,
            "headingDeg": round(float(prev_heading), 1) if isinstance(prev_heading, (int, float)) else 0.0,
            "roadSpeedLimitKmh": nav.get("road_speed_limit_kmh"),
            "gpsHdop": round(hdop, 2),
            "gpsAccuracyM": round(max(1.5, 2.5 * hdop), 1),
        }
