/**
 * Constantes vitales de ejemplo para probar el monitor del paciente sin una
 * unidad con paciente. Son datos ficticios generados en el navegador: no se
 * guardan ni se envían a ningún sitio.
 */
import type { MedicalTelemetry } from "@/types/simulation";

interface Profile {
  hr: number;
  sys: number;
  dia: number;
  spo2: number;
  rr: number;
  temp: number;
  gcs: number;
  glucose: number;
  ecg: string;
}

const NORMAL: Profile = { hr: 82, sys: 128, dia: 80, spo2: 97, rr: 16, temp: 36.8, gcs: 15, glucose: 105, ecg: "Sinusal" };

const PROFILES: Record<string, Partial<Profile>> = {
  cardiac_arrest: { hr: 150, sys: 60, dia: 35, spo2: 78, rr: 6, gcs: 3, ecg: "Fibrilacion" },
  chest_pain: { hr: 102, sys: 158, dia: 94, spo2: 95 },
  breathing: { hr: 108, spo2: 87, rr: 28 },
  stroke: { sys: 182, dia: 104, gcs: 11 },
  diabetic: { glucose: 45, gcs: 12, hr: 96 },
  allergy: { hr: 118, sys: 92, dia: 58, spo2: 91 },
  bleeding: { hr: 116, sys: 98, dia: 62 },
  fall_height: { hr: 112, sys: 96, dia: 60, gcs: 12 },
  house_fire: { spo2: 90, rr: 24, hr: 104 },
  flooding: { temp: 35.2, hr: 92 },
  child_fever: { temp: 39.8, hr: 128 },
  stabbing: { hr: 124, sys: 88, dia: 52 },
};

/** Constantes para una afección en el instante `t` (s), con pequeña variación. */
export function mockVitals(conditionKey: string, t: number): MedicalTelemetry {
  const p = { ...NORMAL, ...(PROFILES[conditionKey] ?? {}) };
  const wobble = (amp: number, freq: number, phase = 0) => amp * Math.sin(t * freq + phase);
  return {
    heartRateBpm: Math.round(p.hr + wobble(3, 0.9) + wobble(1.5, 2.3, 1)),
    bloodPressureMmhg: {
      systolic: Math.round(p.sys + wobble(3, 0.2)),
      diastolic: Math.round(p.dia + wobble(2, 0.25, 2)),
    },
    spo2Pct: Math.round(Math.min(100, p.spo2 + wobble(1, 0.4))),
    respiratoryRatePerMin: Math.round(p.rr + wobble(1, 0.3)),
    bodyTempC: Math.round((p.temp + wobble(0.05, 0.1)) * 10) / 10,
    gcsScore: p.gcs,
    bloodGlucoseMgDl: Math.round(p.glucose + wobble(2, 0.15)),
    ecgRhythm: p.ecg,
    defibrillatorStatus: conditionKey === "cardiac_arrest" ? "armed" : "standby",
  };
}
