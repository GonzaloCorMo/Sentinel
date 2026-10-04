"""Motor médico: vitales del paciente (solo activo con ``hasPatient``).

Genera BPM, presión arterial, SpO₂, EtCO₂, GCS, ritmo ECG, FR, NEWS2
y shock index usando un RNG sembrado por ``ambulance_id`` para que cada
unidad tenga un perfil estable.

Perfiles soportados vía ``patientSeverity``:
    - ``stable``: vitales en rangos normales con poco drift.
    - ``moderate``: empeoramiento progresivo controlable.
    - ``critical``: hipotensión, taquicardia, hipoxemia, ECG arrítmico.
"""
from __future__ import annotations

import math
import random
from typing import Any

_ECG_WEIGHTS_NORMAL = (
    ("Sinusal", 0.80),
    ("Taquicardia", 0.15),
    ("Fibrilacion", 0.05),
)

_ECG_WEIGHTS_CRITICAL = (
    ("Sinusal", 0.30),
    ("Taquicardia", 0.40),
    ("Fibrilacion", 0.30),
)


# Ajustes de las constantes según la afección del paciente (``patientKindKey``,
# tipos de emergency_catalog). Valores aproximados y ficticios, solo para que
# la simulación sea coherente: no son criterios clínicos.
#   d_*  → se suma al valor calculado; set_* → se fija.
_CONDITION_PROFILES: dict[str, dict[str, Any]] = {
    "cardiac_arrest": {"set_hr": 150, "set_sys": 60, "set_dia": 35, "set_spo2": 78, "set_gcs": 3, "set_ecg": "Fibrilacion", "d_rr": -8},
    "chest_pain": {"d_hr": 15, "d_sys": 25, "d_dia": 10, "d_spo2": -2, "d_troponin": 90},
    "breathing": {"d_spo2": -9, "d_rr": 10, "d_hr": 15},
    "stroke": {"d_sys": 45, "d_dia": 20, "d_gcs": -4},
    "syncope": {"d_sys": -22, "d_dia": -12, "d_hr": -12},
    "elderly_fall": {"d_hr": 10, "d_pain": 4},
    "seizure": {"d_hr": 25, "d_gcs": -5, "d_spo2": -4},
    "diabetic": {"set_glucose": 45, "d_gcs": -3, "d_hr": 12},
    "abdominal": {"d_hr": 15, "d_pain": 4},
    "intoxication": {"d_gcs": -4, "d_rr": -4, "d_spo2": -4},
    "allergy": {"d_sys": -30, "d_dia": -15, "d_hr": 25, "d_spo2": -6},
    "anxiety": {"d_hr": 25, "d_rr": 8},
    "child_fever": {"set_temp": 39.8, "d_hr": 20},
    "bleeding": {"d_hr": 25, "d_sys": -25, "d_hb": -3},
    "traffic_accident": {"d_hr": 20, "d_sys": -15, "d_pain": 4},
    "pedestrian_hit": {"d_hr": 25, "d_sys": -20, "d_gcs": -2, "d_pain": 5},
    "street_fall": {"d_hr": 8, "d_pain": 3},
    "bike_accident": {"d_hr": 15, "d_pain": 4},
    "work_accident": {"d_hr": 15, "d_pain": 4},
    "fall_height": {"d_hr": 25, "d_sys": -25, "d_gcs": -3, "d_pain": 5},
    "house_fire": {"d_spo2": -6, "d_spco": 12, "d_rr": 6},
    "vehicle_fire": {"d_spo2": -3, "d_spco": 6, "d_pain": 4},
    "gas_leak": {"d_spco": 15, "d_gcs": -2},
    "flooding": {"set_temp": 35.2, "d_hr": 10},
    "assault": {"d_hr": 20, "d_pain": 4},
    "stabbing": {"d_hr": 30, "d_sys": -30, "d_dia": -15, "d_hb": -3},
    "multi_vehicle": {"d_hr": 25, "d_sys": -25, "d_pain": 5},
}


class MedicalEngine:
    """Genera vitales, scores y ritmo ECG según la severidad del paciente."""

    def __init__(self) -> None:
        self._phase: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}

    def _rng_for(self, amb_id: str) -> random.Random:
        """RNG estable por unidad (seed = hash del id) para reproducibilidad."""
        if amb_id not in self._rng:
            self._rng[amb_id] = random.Random(hash(amb_id) % (2**32))
        return self._rng[amb_id]

    def reset_ambulance(self, amb_id: str) -> None:
        """Limpia fase y RNG al eliminar la unidad o hacer reset."""
        self._phase.pop(amb_id, None)
        self._rng.pop(amb_id, None)

    def _pick_ecg(self, rng: random.Random, severity: str) -> str:
        """Muestrea ritmo ECG ponderado (distinto set si ``severity=="critical"``)."""
        weights = _ECG_WEIGHTS_CRITICAL if severity == "critical" else _ECG_WEIGHTS_NORMAL
        r = rng.random()
        acc = 0.0
        for label, w in weights:
            acc += w
            if r <= acc:
                return label
        return weights[0][0]

    def tick(self, amb_id: str, tick_index: int, dt: float, amb: dict[str, Any]) -> dict[str, Any]:
        """Avanza un tick; el caller debe garantizar ``amb["hasPatient"]``."""
        phase = self._phase.get(amb_id, 0.0)
        phase += dt * 0.6
        self._phase[amb_id] = phase

        rng = self._rng_for(amb_id)
        noise = rng.gauss(0, 0.8)
        fsm = str(amb.get("fsmState") or "IDLE").upper()
        mphase = str(amb.get("missionPhase") or "idle")
        nav = amb.get("_navHint") or {}
        on_route = bool(nav.get("on_route"))
        severity = str(amb.get("patientSeverity") or "stable")

        hr_base, sys_bp, dia_bp, spo2_n = self._base_vitals(
            fsm, mphase, on_route, severity, phase, noise, rng,
        )

        hr = max(40.0, min(180.0, hr_base))
        spo2 = min(99, max(70, spo2_n))
        defib_states = ("standby", "charging", "armed", "disarmed")
        defib = defib_states[tick_index % len(defib_states)]

        etco2_base = 35.0 if severity != "critical" else 28.0
        etco2 = max(15.0, min(60.0, etco2_base + 3.0 * math.sin(phase * 0.5) + rng.gauss(0, 1.5)))

        glucose_base = 110.0 if severity != "critical" else 160.0
        glucose = max(40.0, min(350.0, glucose_base + 8.0 * math.sin(phase * 0.3) + rng.gauss(0, 4.0)))

        temp_base = 36.8 if severity != "critical" else 38.2
        body_temp = max(35.0, min(41.0, temp_base + 0.6 * math.sin(phase * 0.2) + rng.gauss(0, 0.05)))

        infusion = max(0.0, 25.0 + 5.0 * math.sin(phase * 0.15) + rng.gauss(0, 1.0))

        gcs = self._calc_gcs(severity, phase, rng)
        resp_rate = self._calc_resp_rate(severity, phase, rng)

        # ── Derivados hemodinámicos ───────────────────────────────────────
        map_mmhg = round((sys_bp + 2 * dia_bp) / 3, 1)
        pulse_pressure = sys_bp - dia_bp
        shock_index = round(hr / max(1, sys_bp), 2)
        mshock_index = round(hr / max(1, map_mmhg), 2)

        # ── Ventilación ───────────────────────────────────────────────────
        vent_mode = "AC-VC" if severity == "critical" else ("SIMV" if severity == "moderate" else "CPAP")
        fio2 = 40 if severity == "stable" else (60 if severity == "moderate" else 90)
        peep_cmh2o = 5 if severity == "stable" else (8 if severity == "moderate" else 12)
        peak_pressure_cmh2o = 18 + int(4 * math.sin(phase * 0.5)) + (6 if severity == "critical" else 0)
        tidal_volume_ml = 480 + int(30 * math.sin(phase * 0.3)) - (40 if severity == "critical" else 0)
        minute_volume_l = round((tidal_volume_ml * resp_rate) / 1000.0, 2)

        # ── Oxígeno y consumibles ─────────────────────────────────────────
        o2_tank_psi = max(200, 2100 - int(tick_index * 0.6) % 1900)
        o2_tank_pct = round(max(10.0, 100.0 - (tick_index * 0.02 % 85.0)), 1)
        iv_bag_pct = max(5.0, 90.0 - (tick_index * 0.015 % 85.0))
        suction_pressure_mmhg = 120 + int(15 * math.sin(phase * 0.4))

        # ── Perfusión periférica / oximetría avanzada ─────────────────────
        perfusion_index = round(max(0.1, 2.5 + 1.5 * math.sin(phase * 0.3) - (1.2 if severity == "critical" else 0)) + rng.gauss(0, 0.1), 2)
        pleth_variability = int(max(5, min(30, 12 + 5 * math.sin(phase * 0.4) + (8 if severity == "critical" else 0))))
        spco_pct = round(max(0.0, 1.5 + rng.gauss(0, 0.3) + (4 if severity == "critical" else 0)), 2)
        spmet_pct = round(max(0.0, 0.8 + rng.gauss(0, 0.15)), 2)

        # ── Laboratorio point-of-care ─────────────────────────────────────
        lactate_mmol_l = round(max(0.3, 1.2 + 0.4 * math.sin(phase * 0.2) + (3.5 if severity == "critical" else (1.0 if severity == "moderate" else 0))) + rng.gauss(0, 0.1), 2)
        ph = round(max(7.10, min(7.55, 7.40 - (0.15 if severity == "critical" else 0) - 0.02 * math.sin(phase * 0.3) + rng.gauss(0, 0.01))), 3)
        hemoglobin_g_dl = round(max(6.0, 13.5 - (2.5 if severity == "critical" else 0) + rng.gauss(0, 0.2)), 2)
        potassium_mmol_l = round(max(2.5, 4.2 + (0.8 if severity == "critical" else 0) + rng.gauss(0, 0.1)), 2)
        sodium_mmol_l = round(max(125.0, 138.0 + rng.gauss(0, 0.8)), 1)
        troponin_ng_l = round(max(0.0, 8.0 + (120.0 if severity == "critical" else (30.0 if severity == "moderate" else 0)) + rng.gauss(0, 2)), 1)

        # ── Scores clínicos (NEWS2, shock_index, triage) ──────────────────
        news2 = self._calc_news2(hr, resp_rate, spo2, sys_bp, body_temp, fsm)
        pain_score = self._calc_pain(severity, phase, rng)
        triage_level = self._calc_triage(severity, gcs, sys_bp, spo2)

        # ── Infusión fármacos activos ─────────────────────────────────────
        adrenaline_ug_min = round(0.05 if severity == "critical" else 0.0, 3)
        noradrenaline_ug_min = round(0.15 if severity == "critical" else 0.0, 3)

        # ── Afección concreta del paciente ────────────────────────────────
        prof = _CONDITION_PROFILES.get(str(amb.get("patientKindKey") or ""), {})
        ecg = self._pick_ecg(rng, severity)
        if prof:
            hr = float(prof.get("set_hr", hr + prof.get("d_hr", 0)))
            sys_bp = int(prof.get("set_sys", sys_bp + prof.get("d_sys", 0)))
            dia_bp = int(prof.get("set_dia", dia_bp + prof.get("d_dia", 0)))
            spo2 = int(min(100, max(60, prof.get("set_spo2", spo2 + prof.get("d_spo2", 0)))))
            gcs = int(min(15, max(3, prof.get("set_gcs", gcs + prof.get("d_gcs", 0)))))
            resp_rate = int(max(4, resp_rate + prof.get("d_rr", 0)))
            glucose = float(prof.get("set_glucose", glucose))
            body_temp = float(prof.get("set_temp", body_temp))
            pain_score = int(min(10, max(0, pain_score + prof.get("d_pain", 0))))
            spco_pct = round(spco_pct + prof.get("d_spco", 0), 2)
            hemoglobin_g_dl = round(max(5.0, hemoglobin_g_dl + prof.get("d_hb", 0)), 2)
            troponin_ng_l = round(troponin_ng_l + prof.get("d_troponin", 0), 1)
            ecg = str(prof.get("set_ecg", ecg))
            map_mmhg = round((sys_bp + 2 * dia_bp) / 3, 1)
            pulse_pressure = sys_bp - dia_bp
            shock_index = round(hr / max(1, sys_bp), 2)
            mshock_index = round(hr / max(1, map_mmhg), 2)
            news2 = self._calc_news2(hr, resp_rate, spo2, sys_bp, body_temp, fsm)

        return {
            # ── Básicos (UI existente) ──
            "heartRateBpm": round(hr, 1),
            "bloodPressureMmhg": {"systolic": sys_bp, "diastolic": dia_bp},
            "spo2Pct": spo2,
            "defibrillatorStatus": defib,
            "etco2MmHg": round(etco2, 1),
            "bloodGlucoseMgDl": round(glucose, 1),
            "bodyTempC": round(body_temp, 2),
            "infusionRateMlH": round(infusion, 1),
            "ecgRhythm": ecg,
            "gcsScore": gcs,
            "respiratoryRatePerMin": resp_rate,
            # ── Derivados hemodinámicos ──
            "meanArterialPressureMmhg": map_mmhg,
            "pulsePressureMmhg": pulse_pressure,
            "shockIndex": shock_index,
            "modifiedShockIndex": mshock_index,
            # ── Oxímetria avanzada ──
            "perfusionIndex": perfusion_index,
            "plethVariabilityPct": pleth_variability,
            "spcoPct": spco_pct,
            "spmetPct": spmet_pct,
            # ── Ventilador ──
            "ventilator": {
                "mode": vent_mode,
                "fio2Pct": fio2,
                "peepCmH2O": peep_cmh2o,
                "peakPressureCmH2O": peak_pressure_cmh2o,
                "tidalVolumeMl": tidal_volume_ml,
                "minuteVolumeL": minute_volume_l,
            },
            # ── Consumibles ──
            "oxygenTankPsi": o2_tank_psi,
            "oxygenTankPct": o2_tank_pct,
            "ivBagLevelPct": round(iv_bag_pct, 1),
            "suctionPressureMmhg": suction_pressure_mmhg,
            # ── Lab POC ──
            "lactateMmolL": lactate_mmol_l,
            "bloodPh": ph,
            "hemoglobinGdL": hemoglobin_g_dl,
            "potassiumMmolL": potassium_mmol_l,
            "sodiumMmolL": sodium_mmol_l,
            "troponinNgL": troponin_ng_l,
            # ── Fármacos activos ──
            "activeInfusions": {
                "adrenalineUgMin": adrenaline_ug_min,
                "noradrenalineUgMin": noradrenaline_ug_min,
            },
            # ── Scores clínicos ──
            "news2Score": news2,
            "painScore": pain_score,
            "triageLevel": triage_level,
        }

    def _base_vitals(
        self,
        fsm: str,
        mphase: str,
        on_route: bool,
        severity: str,
        phase: float,
        noise: float,
        rng: random.Random,
    ) -> tuple[float, int, int, int]:
        """Return (hr_base, systolic, diastolic, spo2_raw) depending on state and severity."""

        if severity == "critical":
            return self._vitals_critical(fsm, mphase, on_route, phase, noise, rng)
        elif severity == "moderate":
            return self._vitals_moderate(fsm, mphase, on_route, phase, noise, rng)
        else:
            return self._vitals_stable(fsm, mphase, on_route, phase, noise, rng)

    def _vitals_stable(
        self, fsm: str, mphase: str, on_route: bool,
        phase: float, noise: float, rng: random.Random,
    ) -> tuple[float, int, int, int]:
        if fsm == "RESPONDING" or mphase == "to_emergency":
            hr = 80 + 8 * math.sin(phase * 1.1) + noise * 0.8
            sys = 122 + int(5 * math.sin(phase * 0.9))
            dia = 76 + int(3 * math.cos(phase * 0.85))
            spo2 = 97 + int(rng.gauss(0, 0.5))
        elif fsm == "TRANSPORTING" or mphase == "to_hospital":
            hr = 76 + 6 * math.sin(phase * 1.0) + noise * 0.7
            sys = 120 + int(4 * math.sin(phase * 0.85))
            dia = 74 + int(3 * math.cos(phase * 0.88))
            spo2 = 97 + int(rng.gauss(0, 0.4))
        else:
            hr = 72 + 5 * math.sin(phase) + noise * 0.6
            sys = 118 + int(4 * math.sin(phase * 0.8))
            dia = 72 + int(3 * math.cos(phase * 0.9))
            spo2 = 98 + int(rng.gauss(0, 0.3))
        if on_route:
            hr += 3.0
        return hr, sys, dia, spo2

    def _vitals_moderate(
        self, fsm: str, mphase: str, on_route: bool,
        phase: float, noise: float, rng: random.Random,
    ) -> tuple[float, int, int, int]:
        if fsm == "RESPONDING" or mphase == "to_emergency":
            hr = 108 + 10 * math.sin(phase * 1.2) + noise * 1.2
            sys = 138 + int(8 * math.sin(phase * 0.9))
            dia = 82 + int(5 * math.cos(phase * 0.85))
            spo2 = 94 + int(2 * math.sin(phase)) + int(rng.gauss(0, 0.8))
        elif fsm == "TRANSPORTING" or mphase == "to_hospital":
            hr = 104 + 8 * math.sin(phase * 1.0) + noise
            sys = 134 + int(7 * math.sin(phase * 0.85))
            dia = 80 + int(4 * math.cos(phase * 0.88))
            spo2 = 94 + int(2 * math.sin(phase * 1.05)) + int(rng.gauss(0, 0.7))
        else:
            hr = 100 + 6 * math.sin(phase * 0.95) + noise * 0.9
            sys = 130 + int(6 * math.sin(phase * 0.8))
            dia = 78 + int(4 * math.cos(phase * 0.9))
            spo2 = 95 + int(rng.gauss(0, 0.6))
        if on_route:
            hr += 4.0
        return hr, sys, dia, spo2

    def _vitals_critical(
        self, fsm: str, mphase: str, on_route: bool,
        phase: float, noise: float, rng: random.Random,
    ) -> tuple[float, int, int, int]:
        if fsm == "RESPONDING" or mphase == "to_emergency":
            hr = 135 + 15 * math.sin(phase * 1.3) + noise * 1.5
            sys = 90 + int(15 * math.sin(phase * 0.9))
            dia = 55 + int(8 * math.cos(phase * 0.85))
            spo2 = 82 + int(5 * math.sin(phase * 0.7)) + int(rng.gauss(0, 1.5))
        elif fsm == "TRANSPORTING" or mphase == "to_hospital":
            hr = 130 + 12 * math.sin(phase * 1.1) + noise * 1.3
            sys = 88 + int(12 * math.sin(phase * 0.85))
            dia = 52 + int(6 * math.cos(phase * 0.88))
            spo2 = 83 + int(4 * math.sin(phase * 0.8)) + int(rng.gauss(0, 1.2))
        else:
            hr = 125 + 10 * math.sin(phase * 0.95) + noise * 1.2
            sys = 85 + int(10 * math.sin(phase * 0.8))
            dia = 50 + int(5 * math.cos(phase * 0.9))
            spo2 = 84 + int(3 * math.sin(phase * 0.9)) + int(rng.gauss(0, 1.0))
        if on_route:
            hr += 5.0
        return hr, sys, dia, spo2

    def _calc_gcs(self, severity: str, phase: float, rng: random.Random) -> int:
        if severity == "critical":
            return int(max(3, min(8, round(5 + 2 * math.sin(phase * 0.4) + rng.gauss(0, 0.8)))))
        elif severity == "moderate":
            return int(max(6, min(12, round(10 + 2 * math.sin(phase * 0.4) + rng.gauss(0, 1.0)))))
        else:
            return int(max(12, min(15, round(14 + math.sin(phase * 0.4) + rng.gauss(0, 0.5)))))

    def _calc_resp_rate(self, severity: str, phase: float, rng: random.Random) -> int:
        if severity == "critical":
            return max(6, min(35, int(28 + 5 * math.sin(phase * 0.6) + rng.gauss(0, 2.0))))
        elif severity == "moderate":
            return max(14, min(28, int(22 + 4 * math.sin(phase * 0.6) + rng.gauss(0, 1.5))))
        else:
            return max(12, min(20, int(16 + 2 * math.sin(phase * 0.6) + rng.gauss(0, 1.0))))

    def _calc_news2(self, hr: float, rr: int, spo2: int, sys: int, temp: float, fsm: str) -> int:
        """NEWS2 abreviado (Royal College Physicians). Puntuaciones agregadas 0-20+."""
        score = 0
        if rr <= 8 or rr >= 25: score += 3
        elif rr >= 21: score += 2
        elif rr <= 11: score += 1
        if spo2 <= 91: score += 3
        elif spo2 <= 93: score += 2
        elif spo2 <= 95: score += 1
        if sys <= 90 or sys >= 220: score += 3
        elif sys <= 100: score += 2
        elif sys <= 110: score += 1
        if hr <= 40 or hr >= 131: score += 3
        elif hr >= 111: score += 2
        elif hr >= 91 or hr <= 50: score += 1
        if temp <= 35.0: score += 3
        elif temp >= 39.1: score += 2
        elif temp <= 36.0 or temp >= 38.1: score += 1
        return score

    def _calc_pain(self, severity: str, phase: float, rng: random.Random) -> int:
        base = {"stable": 3, "moderate": 6, "critical": 9}.get(severity, 3)
        return int(max(0, min(10, base + int(round(math.sin(phase * 0.4) + rng.gauss(0, 0.5))))))

    def _calc_triage(self, severity: str, gcs: int, sys: int, spo2: int) -> int:
        """Escala triage Manchester/START simplificada (1 = emergencia, 5 = leve)."""
        if severity == "critical" or gcs <= 8 or sys < 90 or spo2 < 90:
            return 1  # Resucitación
        if severity == "moderate" or gcs <= 12 or spo2 < 94:
            return 2  # Emergencia
        return 3 if severity != "stable" else 4
