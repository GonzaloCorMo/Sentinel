"""Motor ambiental: condiciones exteriores e interiores de la cabina."""
from __future__ import annotations

import math
import random
from typing import Any


class EnvironmentalEngine:
    """Telemetría del entorno: clima exterior, cabina, radiación, vibración.

    Oscilaciones periódicas (``sin`` con fases lentas) más ruido gaussiano
    para que los valores se vean vivos sin saltos irreales.
    """

    def __init__(self) -> None:
        self._phase: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}

    def _rng_for(self, amb_id: str) -> random.Random:
        """RNG estable por unidad."""
        if amb_id not in self._rng:
            self._rng[amb_id] = random.Random(hash(("env", amb_id)) % (2**32))
        return self._rng[amb_id]

    def reset_ambulance(self, amb_id: str) -> None:
        """Olvida fase y RNG al eliminar la unidad."""
        self._phase.pop(amb_id, None)
        self._rng.pop(amb_id, None)

    def tick(self, amb_id: str, tick_index: int, dt: float, amb: dict[str, Any]) -> dict[str, Any]:
        """Avanza un tick; ruido cabina sube cuando ``on_route``."""
        phase = self._phase.get(amb_id, 0.0) + dt * 0.3
        self._phase[amb_id] = phase
        rng = self._rng_for(amb_id)

        nav = amb.get("_navHint") or {}
        speed_ms = float(nav.get("speed_ms") or 0.0)
        on_route = bool(nav.get("on_route"))

        # Exterior
        ext_temp = 18.0 + 8.0 * math.sin(phase * 0.05) + rng.gauss(0, 0.4)
        ext_humidity = 55.0 + 15.0 * math.sin(phase * 0.07) + rng.gauss(0, 1.5)
        ext_pressure_hpa = 1013.25 + 3.0 * math.sin(phase * 0.02) + rng.gauss(0, 0.2)
        wind_speed = max(0.0, 3.0 + 5.0 * abs(math.sin(phase * 0.12)) + rng.gauss(0, 0.5))
        wind_direction = (phase * 12.0 + rng.gauss(0, 10)) % 360.0
        visibility_km = max(0.4, 15.0 - 5.0 * max(0.0, math.sin(phase * 0.09)) + rng.gauss(0, 0.8))
        precip = max(0.0, 0.4 * max(0.0, math.sin(phase * 0.08)) + rng.gauss(0, 0.05))
        uv_index = max(0.0, 3.0 + 4.0 * math.sin(phase * 0.05))

        # Cabina
        cab_temp = 22.0 + 1.5 * math.sin(phase * 0.4) + 0.03 * speed_ms + rng.gauss(0, 0.1)
        cab_humidity = 45.0 + 6.0 * math.sin(phase * 0.25) + rng.gauss(0, 0.6)
        cab_co2_ppm = max(400, 620 + 80 * math.sin(phase * 0.3) + rng.gauss(0, 15))
        cab_noise_db = (72 if on_route else 55) + 8 * math.sin(phase * 0.7) + rng.gauss(0, 1.2)
        cab_lux = (8000 if on_route else 300) + rng.gauss(0, 100)
        air_quality_voc_ppb = max(0.0, 90.0 + 40.0 * math.sin(phase * 0.2) + rng.gauss(0, 6))
        pm25_ug_m3 = max(0.0, 12.0 + 6.0 * math.sin(phase * 0.13) + rng.gauss(0, 0.8))

        chassis_vibration_g = (0.6 if on_route else 0.05) + 0.4 * abs(math.sin(phase * 2.0)) + rng.gauss(0, 0.04)

        return {
            "exterior": {
                "tempC": round(ext_temp, 1),
                "humidityPct": round(max(0.0, min(100.0, ext_humidity)), 1),
                "pressureHpa": round(ext_pressure_hpa, 2),
                "windSpeedMs": round(wind_speed, 2),
                "windDirectionDeg": round(wind_direction, 1),
                "visibilityKm": round(visibility_km, 2),
                "precipMmH": round(precip, 2),
                "uvIndex": round(uv_index, 1),
            },
            "cabin": {
                "tempC": round(cab_temp, 2),
                "humidityPct": round(max(0.0, min(100.0, cab_humidity)), 1),
                "co2Ppm": round(cab_co2_ppm, 0),
                "noiseDb": round(cab_noise_db, 1),
                "illuminanceLux": round(cab_lux, 0),
                "vocPpb": round(air_quality_voc_ppb, 1),
                "pm25UgM3": round(pm25_ug_m3, 1),
                "airFilterLifePct": round(max(0.0, 100.0 - (tick_index * 0.0005 % 100.0)), 2),
            },
            "chassisVibrationG": round(chassis_vibration_g, 3),
        }
