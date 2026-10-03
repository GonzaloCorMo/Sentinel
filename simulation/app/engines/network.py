"""Motor de red / conectividad: calidad del enlace del vehículo."""
from __future__ import annotations

import math
import random
from typing import Any


class NetworkEngine:
    """RSSI + latencia por canal + pérdida + jitter + tipo de red (5G/4G/WiFi/3G).

    El tipo de red rota cada ~30 ticks (handover simulado). Latencia y
    throughput dependen del tipo; ``3G`` añade pérdida extra.
    """

    def __init__(self) -> None:
        self._phase: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}

    def _rng_for(self, amb_id: str) -> random.Random:
        """RNG estable por unidad."""
        if amb_id not in self._rng:
            self._rng[amb_id] = random.Random(hash(("net", amb_id)) % (2**32))
        return self._rng[amb_id]

    def reset_ambulance(self, amb_id: str) -> None:
        """Olvida fase y RNG al eliminar la unidad."""
        self._phase.pop(amb_id, None)
        self._rng.pop(amb_id, None)

    def _coverage(self, amb: dict[str, Any], rng: Any) -> tuple[str, float]:
        """Tipo de red y distancia (km) al centro de la región activa."""
        from ..regions import get_active_region
        from ..route_nav import haversine_m

        try:
            region = get_active_region()
            d_km = haversine_m(float(amb.get("latitude") or region.center_lat),
                               float(amb.get("longitude") or region.center_lon),
                               region.center_lat, region.center_lon) / 1000.0
        except Exception:
            return "4G", 0.0
        if d_km < 3.0:
            return "5G", d_km
        if d_km < 8.0:
            return "4G", d_km
        if d_km > 11.0 and rng.random() < 0.15:
            return "none", d_km
        return "3G", d_km

    def tick(self, amb_id: str, tick_index: int, dt: float, amb: dict[str, Any]) -> dict[str, Any]:
        """Genera telemetría de red del tick."""
        phase = self._phase.get(amb_id, 0.0) + dt * 0.5
        self._phase[amb_id] = phase
        rng = self._rng_for(amb_id)

        # Cobertura según la distancia al centro urbano: 5G en el centro,
        # 4G en el área metropolitana, 3G en la periferia y zonas de sombra
        # ocasionales en el extrarradio. Cada unidad tiene su propia red.
        net_type, dist_km = self._coverage(amb, rng)
        signal_base = {"5G": -67, "4G": -79, "3G": -97, "none": -118}.get(net_type, -85)
        rssi = signal_base - 1.2 * dist_km + 6 * math.sin(phase * 0.3) + rng.gauss(0, 1.5)

        # Latencia según red
        lat_base = {"5G": 18, "4G": 35, "3G": 95, "none": 0}.get(net_type, 60)
        mqtt_ms = max(3.0, lat_base + 10 * math.sin(phase * 0.4) + rng.gauss(0, 3))
        http_ms = max(5.0, mqtt_ms * 1.8 + rng.gauss(0, 4))
        p2p_ms = max(2.0, mqtt_ms * 0.6 + rng.gauss(0, 2))

        # Jitter y pérdida
        jitter_ms = max(0.0, 4 + 3 * abs(math.sin(phase * 0.6)) + rng.gauss(0, 0.5))
        loss_pct = max(0.0, min(15.0, 0.5 + 2.0 * max(0.0, math.sin(phase * 0.25)) + rng.gauss(0, 0.3)))
        if net_type == "3G":
            loss_pct += 3.0

        # Throughput Mbps
        if net_type == "none":
            loss_pct = 100.0
        thr_base = {"5G": 320, "4G": 60, "3G": 8, "none": 0.0}.get(net_type, 30)
        throughput_mbps = 0.0 if net_type == "none" else max(0.5, thr_base * (1 - loss_pct / 100) + rng.gauss(0, thr_base * 0.05))

        # Active channel sigue lo que use engine (linkState), lo tomamos si está
        link_state = amb.get("linkState") or "mqtt_active"

        return {
            "networkType": net_type,
            "rssiDbm": round(rssi, 1),
            "latencyMs": {
                "mqtt": round(mqtt_ms, 1),
                "http": round(http_ms, 1),
                "p2p": round(p2p_ms, 1),
            },
            "jitterMs": round(jitter_ms, 2),
            "packetLossPct": round(loss_pct, 2),
            "throughputMbps": round(throughput_mbps, 1),
            "activeChannel": link_state,
            "handoversCount": int(max(0, tick_index // 600)),
        }
