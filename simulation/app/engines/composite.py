"""Agregación de los motores de telemetría del vehículo.

Composición:
  - positioning (GPS, cinemática, satélites)
  - mechanical  (chasis, combustible, presiones, consumibles, mantenimiento)
  - medical     (vitales + ventilador + lab POC + scores clínicos; solo con paciente)
  - environmental (clima exterior + calidad aire cabina + vibración)
  - network     (RSSI, latencias MQTT/HTTP/P2P, pérdida de paquetes)

Además calcula indicadores compuestos (health scores, alertas derivadas) útiles
para análisis masivo y modelos de IA posteriores.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .environmental import EnvironmentalEngine
from .mechanical import MechanicalEngine
from .medical import MedicalEngine
from .network import NetworkEngine
from .positioning import PositioningEngine


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelemetryComposite:
    """Orquesta los 5 motores de telemetría y calcula scores derivados.

    Cada motor mantiene su propio estado por unidad (``state_by_amb``).
    `tick` corre todos en orden, ensambla la telemetría v2.0 y añade
    campos derivados (salud vehículo, riesgo clínico, alertas).
    """

    def __init__(self) -> None:
        self.positioning = PositioningEngine()
        self.mechanical = MechanicalEngine()
        self.medical = MedicalEngine()
        self.environmental = EnvironmentalEngine()
        self.network = NetworkEngine()

    def reset_ambulance(self, amb_id: str) -> None:
        """Limpia el estado de esta unidad en los 5 motores (al eliminarla o reset)."""
        self.positioning.reset_ambulance(amb_id)
        self.mechanical.reset_ambulance(amb_id)
        self.medical.reset_ambulance(amb_id)
        self.environmental.reset_ambulance(amb_id)
        self.network.reset_ambulance(amb_id)

    def tick(self, amb_id: str, tick_index: int, dt: float, amb: dict[str, Any]) -> dict[str, Any]:
        """Avanza un tick en todos los motores y devuelve telemetría v2.0.

        Medical se omite si la unidad no tiene paciente (``hasPatient``).

        Args:
            amb_id: UUID de la unidad.
            tick_index: Contador global del engine.
            dt: Segundos simulados (dt_sim).
            amb: Dict de la ambulancia con ``_navHint`` y campos de estado.

        Returns:
            Dict con claves ``positioning``, ``mechanical``, ``medical``
            (o None), ``environmental``, ``network``, ``derived``, ``meta``.
        """
        pos = self.positioning.tick(amb_id, tick_index, dt, amb)
        mech = self.mechanical.tick(amb_id, tick_index, dt, amb)
        env = self.environmental.tick(amb_id, tick_index, dt, amb)
        net = self.network.tick(amb_id, tick_index, dt, amb)
        med = self.medical.tick(amb_id, tick_index, dt, amb) if amb.get("hasPatient") else None

        derived = self._derive_scores(amb, pos, mech, med, env, net)

        return {
            "positioning": pos,
            "mechanical": mech,
            "medical": med,
            "environmental": env,
            "network": net,
            "derived": derived,
            "meta": {
                "timestamp": _iso_now(),
                "tick": tick_index,
                "schemaVersion": "2.0",
                "ambulanceId": amb_id,
            },
        }

    # ── Scores derivados (entre motores) ─────────────────────────────────
    def _derive_scores(
        self,
        amb: dict[str, Any],
        pos: dict[str, Any],
        mech: dict[str, Any],
        med: dict[str, Any] | None,
        env: dict[str, Any],
        net: dict[str, Any],
    ) -> dict[str, Any]:
        """Calcula scores y alertas cruzando los 5 motores.

        - ``vehicleHealthPct``: motor (40%) + combustible (20%) +
          neumáticos (20%) + frenos (20%).
        - ``linkQualityPct``: pérdida + latencia MQTT.
        - ``cabinComfortPct``: ruido + CO₂ + vibración.
        - ``clinicalRiskScore`` (si hay paciente): NEWS2 + shock index +
          hipoxemia + GCS bajo.
        - ``drivingAggressionScore``: G-forces laterales/longitudinales +
          jerk.
        - ``vehicleAlerts`` / ``clinicalAlerts``: listas de flags.
        """
        veh_engine = float(mech.get("engineHealthPct", 100))
        veh_fuel = float(mech.get("fuelLevelPct", 100))
        tire_avg = (
            sum(mech.get("tireWearPct", {}).values()) / max(1, len(mech.get("tireWearPct", {})))
        ) if mech.get("tireWearPct") else 0
        tire_health = max(0.0, 100.0 - tire_avg)
        brake_min = min(
            mech.get("brakePadFrontMm", 10),
            mech.get("brakePadRearMm", 10),
        )
        brake_health = max(0.0, min(100.0, brake_min * 10.0))
        vehicle_health = round((veh_engine * 0.4 + veh_fuel * 0.2 + tire_health * 0.2 + brake_health * 0.2), 1)

        # Calidad de enlace 0-100
        loss = float(net.get("packetLossPct", 0))
        lat_mqtt = float(net.get("latencyMs", {}).get("mqtt", 50))
        link_quality = round(max(0.0, min(100.0, 100.0 - loss * 4.0 - max(0.0, lat_mqtt - 20) * 0.5)), 1)

        # Confort cabina (ruido + CO2 + vibración)
        noise = float(env.get("cabin", {}).get("noiseDb", 60))
        co2 = float(env.get("cabin", {}).get("co2Ppm", 600))
        vib = float(env.get("chassisVibrationG", 0.1))
        cabin_comfort = round(max(0.0, min(100.0, 100.0 - (max(0, noise - 55)) * 1.2 - (max(0, co2 - 800)) * 0.05 - vib * 30)), 1)

        # Riesgo clínico (si hay paciente): heurística basada en NEWS2 + shock_index
        clinical_risk = None
        clinical_alerts: list[str] = []
        if med:
            news2 = int(med.get("news2Score", 0))
            si = float(med.get("shockIndex", 0.5))
            spo2 = int(med.get("spo2Pct", 100))
            gcs = int(med.get("gcsScore", 15))
            # Riesgo 0-100 (mayor = peor)
            risk = min(100.0, news2 * 8.0 + max(0.0, si - 0.7) * 40.0 + max(0, 94 - spo2) * 4.0 + max(0, 13 - gcs) * 4.0)
            clinical_risk = round(risk, 1)
            if news2 >= 7: clinical_alerts.append("NEWS2_high")
            if si >= 1.0: clinical_alerts.append("shock_index_high")
            if spo2 < 92: clinical_alerts.append("hypoxemia")
            if gcs <= 8: clinical_alerts.append("gcs_severe")
            if float(med.get("lactateMmolL", 0)) >= 4.0: clinical_alerts.append("hyperlactatemia")

        # Alertas vehículo
        vehicle_alerts: list[str] = []
        if veh_fuel < 20: vehicle_alerts.append("low_fuel")
        if mech.get("engineTempC", 0) > 105: vehicle_alerts.append("engine_overheat")
        if tire_avg > 75: vehicle_alerts.append("tires_worn")
        if brake_min < 3: vehicle_alerts.append("brakes_service")
        if float(mech.get("oilPressureBar", 4)) < 2.0: vehicle_alerts.append("low_oil_pressure")

        # Driving style (G-forces y jerk)
        lat_g = abs(float(mech.get("lateralG", 0)))
        long_g = abs(float(mech.get("longitudinalG", 0)))
        jerk = abs(float(pos.get("jerkMs3", 0)))
        aggression = round(min(100.0, lat_g * 120 + long_g * 100 + jerk * 80), 1)

        return {
            "vehicleHealthPct": vehicle_health,
            "linkQualityPct": link_quality,
            "cabinComfortPct": cabin_comfort,
            "drivingAggressionScore": aggression,
            "clinicalRiskScore": clinical_risk,
            "clinicalAlerts": clinical_alerts,
            "vehicleAlerts": vehicle_alerts,
        }
