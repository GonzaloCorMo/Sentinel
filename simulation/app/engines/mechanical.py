"""Motor mecánico: chasis, combustible, odómetro, RPM, sirenas, cabina."""
from __future__ import annotations

import math
from typing import Any

KPA_PER_PSI = 6.89476


def _engine_rpm_from_speed_ms(speed_ms: float) -> float:
    """RPM plausibles en función de velocidad (diesel urbano / interurbano)."""
    v = max(0.0, min(speed_ms, 35.0))
    return 800.0 + 75.0 * v + 8.0 * math.sin(v * 0.3)


class MechanicalEngine:
    """Simula combustible/batería, RPM, temperaturas, presiones, sirenas, IoT.

    Mantiene una fase ``_phase`` por unidad para que los valores
    oscilantes (ruido mecánico) no salten al añadir/quitar unidades.
    Implementa power-off inteligente: unidades IDLE sin misión no
    consumen recursos.
    """

    def __init__(self) -> None:
        self._phase: dict[str, float] = {}

    def reset_ambulance(self, amb_id: str) -> None:
        """Olvida la fase de esta unidad al destruirla o resetearla."""
        self._phase.pop(amb_id, None)

    def tick(self, amb_id: str, tick_index: int, dt: float, amb: dict[str, Any]) -> dict[str, Any]:
        """Genera telemetría mecánica del tick y mutualiza ``amb["poweredOff"]``."""
        phase = self._phase.get(amb_id, 0.0)
        phase += dt * 0.5
        self._phase[amb_id] = phase

        nav = amb.get("_navHint") or {}
        speed_ms = float(nav.get("speed_ms") or 0.0)
        on_route = bool(nav.get("on_route"))
        fsm = str(amb.get("fsmState") or "IDLE").upper()
        sirens_active = fsm in ("RESPONDING", "TRANSPORTING")

        # ── Power-off inteligente ────────────────────────────────────────
        # Si el vehículo está ocioso (IDLE, sin ruta, sin paciente, sin
        # emergencia asignada), se considera "apagado" → no consume combustible
        # ni batería. Sirve para que la flota en reserva no agote recursos.
        is_powered_off = (
            not on_route
            and fsm == "IDLE"
            and not amb.get("hasPatient")
            and not amb.get("assignedEmergencyId")
            and not amb.get("routeCoords")
        )
        # Persistimos flag en el propio objeto para que el resto del motor
        # (AI observer, scoring, UI) pueda consultarlo sin recomputar.
        amb["poweredOff"] = is_powered_off

        base_fuel = float(amb.get("fuelLevel", 85.0))
        if is_powered_off:
            # Valores congelados (sin wobble). Consumo cero.
            fuel = base_fuel
            battery = 100.0
            engine_temp = 25.0 + 2.0 * math.sin(phase * 0.05)       # ambiente
            cabin_c = 20.0 + 0.4 * math.sin(phase * 0.1)
            rpm = 0.0
            secondary_v = 12.4 + 0.2 * math.sin(phase * 0.1)
            oil_temp = 28.0 + 2.0 * math.sin(phase * 0.08)
            brake_fluid = 22.0 + 1.0 * math.sin(phase * 0.1)
            tires_kpa = {"fl": 240.0, "fr": 240.0, "rl": 240.0, "rr": 240.0}
            tires_psi = {k: round(v / KPA_PER_PSI, 1) for k, v in tires_kpa.items()}
            odom_km = float(amb.get("odometerKm", 0.0))
            return {
                # Básicos
                "fuelLevelPct": round(fuel, 1),
                "batteryPct": round(battery, 1),
                "engineTempC": round(engine_temp, 1),
                "tirePressureKpa": tires_kpa,
                "tirePressurePsi": tires_psi,
                "oilTempC": round(oil_temp, 1),
                "brakeFluidTempC": round(brake_fluid, 1),
                "secondaryBatteryVoltageV": round(secondary_v, 2),
                "longitudinalG": 0.0,
                "lateralG": 0.0,
                "sirensOn": False,
                "sirensActive": False,
                "odometerKm": round(odom_km, 3),
                "engineRpm": 0.0,
                "cabinTemperatureC": round(cabin_c, 2),
                # Extendidos (en reposo)
                "avgConsumptionL100km": 0.0,
                "rangeKm": round(max(0.0, (fuel / 100.0) * 640.0), 1),
                "tireWearPct": {k: round(min(100.0, 12.0 + i * 0.2), 2)
                                for i, k in enumerate(("fl", "fr", "rl", "rr"))},
                "oilPressureBar": 0.0,
                "coolantLevelPct": 95.0,
                "adBlueLevelPct": max(10.0, 75.0 - (tick_index * 0.0002 % 60.0)),
                "washerFluidPct": 60.0,
                "brakePadFrontMm": max(1.0, 11.0 - tick_index * 0.00005),
                "brakePadRearMm": max(1.0, 10.5 - tick_index * 0.00004),
                "axleLoadKg": {"front": 1800, "rear": 2100},
                "currentGear": 0,
                "gearboxTempC": round(25.0 + 1.0 * math.sin(phase * 0.1), 1),
                "ecoScore": 100.0,
                "regenKwh": 0.0,
                "alternatorA": 0.0,
                "engineVibrationG": 0.0,
                "engineHealthPct": round(max(0.0, min(100.0, 95.0)), 1),
                "nextServiceKm": max(0, 15000 - int(odom_km) % 15000),
                # Estado de energía
                "powerState": "off",
                "ignitionOn": False,
            }
        # No restar tick_index global: es monótono y vacía el depósito en simulaciones largas
        # aunque la unidad esté en IDLE en hospital (provoca bucle repostaje ↔ staging).
        wobble = 0.5 * math.sin(phase)
        if on_route:
            fuel = max(5.0, min(100.0, base_fuel + wobble))
        else:
            # En parado: solo ruido de sensor; el consumo real va por engine al circular.
            fuel = max(5.0, min(100.0, base_fuel + 0.35 * math.sin(phase * 0.9)))
        battery = max(15.0, min(100.0, 88.0 + 2.0 * math.sin(phase * 0.7)))
        if on_route:
            engine_temp = 90.0 + 12.0 * math.sin(phase * 0.35) + min(8.0, 0.25 * speed_ms)
        else:
            engine_temp = 82.0 + 6.0 * math.sin(phase * 0.25)
        base_p_kpa = 240.0
        tires_kpa = {
            "fl": round(base_p_kpa + 3 * math.sin(phase), 1),
            "fr": round(base_p_kpa + 3 * math.cos(phase), 1),
            "rl": round(base_p_kpa + 2 * math.sin(phase + 1), 1),
            "rr": round(base_p_kpa + 2 * math.cos(phase + 1), 1),
        }
        tires_psi = {k: round(v / KPA_PER_PSI, 1) for k, v in tires_kpa.items()}
        oil_temp = 88.0 + 6.0 * math.sin(phase * 0.4) + 0.02 * (nav.get("speed_ms") or 0.0)
        brake_fluid = 32.0 + 4.0 * abs(math.sin(phase * 0.6)) + (nav.get("brake_heat") or 0.0)
        secondary_v = 12.8 + 0.6 * math.sin(phase * 0.2) + 0.01 * battery

        long_g = float(nav.get("longitudinal_g", 0.0))
        lat_g = float(nav.get("lateral_g", 0.0))

        cabin_c = 22.0 + 0.8 * math.sin(phase * 0.5) + 0.05 * (speed_ms if on_route else 0.0)
        odom_km = float(amb.get("odometerKm", 0.0))
        rpm = _engine_rpm_from_speed_ms(speed_ms if on_route else max(0.0, float(amb.get("speedKmh", 0) or 0) / 3.6))

        sirens_on = sirens_active and (tick_index % 30 < 18)

        # ── Campos extendidos ──────────────────────────────────────────────
        # Consumo y autonomía
        avg_consumption = 8.5 + (12.0 if sirens_active else 0.0) * 0.4 + 0.02 * speed_ms
        range_km = max(0.0, (fuel / 100.0) * 80.0 * (8.0 / max(0.1, avg_consumption / 10.0)))
        # Desgaste neumáticos por rueda (0-100%, 0=nuevo)
        tire_wear = {k: round(min(100.0, 12.0 + tick_index * 0.00001 + i * 0.2), 2)
                     for i, k in enumerate(("fl", "fr", "rl", "rr"))}
        # Presión aceite motor (bar) y nivel fluidos
        oil_pressure_bar = 3.8 + 0.6 * math.sin(phase * 0.4) + (0.04 * speed_ms if on_route else 0.0)
        coolant_pct = max(60.0, 95.0 + 2.0 * math.sin(phase * 0.3))
        adblue_pct = max(10.0, 75.0 - (tick_index * 0.0002 % 60.0))
        washer_fluid_pct = max(20.0, 60.0 + 10.0 * math.sin(phase * 0.2))
        # Desgaste frenos por eje (mm pastilla)
        brake_pad_front_mm = max(1.0, 11.0 - tick_index * 0.00005)
        brake_pad_rear_mm = max(1.0, 10.5 - tick_index * 0.00004)
        # Carga eje (kg)
        axle_load_front = 1800 + 50 * math.sin(phase * 0.15)
        axle_load_rear = 2100 + 80 * math.sin(phase * 0.17)
        # Transmisión: marcha actual + temperatura caja
        gear = 0 if not on_route else max(1, min(6, 1 + int(speed_ms / 6)))
        gearbox_temp = 78.0 + 4.0 * math.sin(phase * 0.3) + (0.15 * speed_ms if on_route else 0.0)
        # Eficiencia energética (eco score 0-100) y regeneración frenada
        eco_score = max(0.0, min(100.0, 80.0 - abs(long_g) * 60.0 - abs(lat_g) * 40.0 + 10.0 * math.sin(phase * 0.1)))
        regen_kwh = max(0.0, 0.02 * max(0.0, -long_g))  # pequeño aporte cuando frena
        # Alternador
        alternator_a = 45 + 15 * math.sin(phase * 0.4) + (6 if sirens_active else 0)
        # Vibración motor (rms g)
        engine_vibration_g = 0.05 + 0.08 * abs(math.sin(phase * 2.5)) + (0.02 if on_route else 0)
        # Salud general (0-100)
        engine_health = max(0.0, min(100.0, 92.0 - (100 - fuel) * 0.08 - (engine_temp - 90) * 0.3))
        # Próximo mantenimiento
        next_service_km = max(0, 15000 - int(odom_km) % 15000)

        return {
            # ── Básicos (compat con UI actual) ──
            "fuelLevelPct": round(fuel, 1),
            "batteryPct": round(battery, 1),
            "engineTempC": round(engine_temp, 1),
            "tirePressureKpa": tires_kpa,
            "tirePressurePsi": tires_psi,
            "oilTempC": round(oil_temp, 1),
            "brakeFluidTempC": round(brake_fluid, 1),
            "secondaryBatteryVoltageV": round(secondary_v, 2),
            "longitudinalG": round(long_g, 3),
            "lateralG": round(lat_g, 3),
            "sirensOn": sirens_on,
            "sirensActive": sirens_active,
            "odometerKm": round(odom_km, 3),
            "engineRpm": round(rpm, 1),
            "cabinTemperatureC": round(cabin_c, 2),
            # ── Extendidos ──
            "avgConsumptionL100km": round(avg_consumption, 2),
            "rangeKm": round(range_km, 1),
            "tireWearPct": tire_wear,
            "oilPressureBar": round(oil_pressure_bar, 2),
            "coolantLevelPct": round(coolant_pct, 1),
            "adBlueLevelPct": round(adblue_pct, 1),
            "washerFluidPct": round(washer_fluid_pct, 1),
            "brakePadFrontMm": round(brake_pad_front_mm, 2),
            "brakePadRearMm": round(brake_pad_rear_mm, 2),
            "axleLoadKg": {"front": round(axle_load_front, 0), "rear": round(axle_load_rear, 0)},
            "currentGear": gear,
            "gearboxTempC": round(gearbox_temp, 1),
            "ecoScore": round(eco_score, 1),
            "regenKwh": round(regen_kwh, 3),
            "alternatorA": round(alternator_a, 1),
            "engineVibrationG": round(engine_vibration_g, 3),
            "engineHealthPct": round(engine_health, 1),
            "nextServiceKm": next_service_km,
            "powerState": "running" if on_route else "idle",
            "ignitionOn": True,
        }
