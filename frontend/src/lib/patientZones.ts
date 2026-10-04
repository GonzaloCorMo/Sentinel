/**
 * Zonas anatómicas del visor 3D del paciente y afección → zonas afectadas.
 *
 * Las coordenadas están en el espacio del modelo `public/models/male-base.glb`
 * («Male base», Артур Мигранов, CC BY): altura ~20,7 unidades, pies en y = 0,
 * mirando hacia +z. El lado izquierdo del paciente es +x. Pose en A: hombros
 * a y ≈ 16,5 y manos a la altura de la cadera (|x| ≈ 5,6).
 *
 * Las afecciones son las claves de `simulation/app/emergency_catalog.py`
 * (`patientKindKey` de la unidad). Todo es orientativo y ficticio.
 */

export type ZoneId =
  | "head" | "face" | "neck" | "chest" | "heart" | "lungs" | "abdomen" | "pelvis"
  | "hip_r" | "arm_l" | "arm_r" | "hand_r" | "leg_l" | "leg_r" | "spine" | "body";

export interface Zone {
  id: ZoneId;
  /** Centro en coordenadas del modelo. */
  center: [number, number, number];
  /** Radio de la zona resaltada (unidades del modelo). */
  radius: number;
}

export const ZONES: Record<ZoneId, Zone> = {
  head: { id: "head", center: [0, 19.5, 0.1], radius: 1.45 },
  face: { id: "face", center: [0, 19.0, 1.0], radius: 1.0 },
  neck: { id: "neck", center: [0, 17.8, 0], radius: 0.95 },
  chest: { id: "chest", center: [0, 15.3, 0.4], radius: 2.0 },
  heart: { id: "heart", center: [0.65, 15.2, 0.8], radius: 1.5 },
  lungs: { id: "lungs", center: [0, 15.4, 0.2], radius: 2.1 },
  abdomen: { id: "abdomen", center: [0, 12.6, 0.6], radius: 1.7 },
  pelvis: { id: "pelvis", center: [0, 9.8, 0.3], radius: 1.7 },
  hip_r: { id: "hip_r", center: [-1.25, 9.4, 0.2], radius: 1.3 },
  arm_l: { id: "arm_l", center: [3.9, 13.2, 0], radius: 1.4 },
  arm_r: { id: "arm_r", center: [-3.9, 13.2, 0], radius: 1.4 },
  hand_r: { id: "hand_r", center: [-5.4, 10.1, 0.2], radius: 1.0 },
  leg_l: { id: "leg_l", center: [1.15, 5.4, 0.1], radius: 1.7 },
  leg_r: { id: "leg_r", center: [-1.15, 5.4, 0.1], radius: 1.7 },
  spine: { id: "spine", center: [0, 13.5, -1.0], radius: 2.2 },
  body: { id: "body", center: [0, 11, 0], radius: 7 },
};

/** Zonas afectadas por cada tipo de emergencia del catálogo. */
const BY_CONDITION: Record<string, ZoneId[]> = {
  chest_pain: ["heart"],
  cardiac_arrest: ["heart"],
  breathing: ["lungs"],
  stroke: ["head"],
  syncope: ["head"],
  elderly_fall: ["hip_r"],
  seizure: ["head"],
  diabetic: ["abdomen"],
  abdominal: ["abdomen"],
  intoxication: ["abdomen", "head"],
  allergy: ["neck"],
  anxiety: ["chest"],
  child_fever: ["head"],
  bleeding: ["hand_r"],
  traffic_accident: ["neck", "chest"],
  pedestrian_hit: ["leg_r", "head"],
  street_fall: ["hand_r"],
  bike_accident: ["arm_l", "face"],
  work_accident: ["leg_l"],
  fall_height: ["spine", "pelvis"],
  house_fire: ["lungs"],
  vehicle_fire: ["arm_r", "lungs"],
  gas_leak: ["lungs", "head"],
  flooding: ["body"],
  assault: ["face"],
  stabbing: ["abdomen"],
  multi_vehicle: ["chest", "pelvis"],
};

/** Si solo se conoce el tipo general de emergencia. */
const BY_TYPE: Record<string, ZoneId[]> = {
  medical: ["chest"],
  trauma: ["leg_r", "head"],
  fire: ["lungs"],
  hazmat: ["lungs"],
  flood: ["body"],
  altercation: ["face"],
  mass_casualty: ["chest", "pelvis"],
};

export const CONDITION_KEYS = Object.keys(BY_CONDITION);

export function zonesFor(conditionKey: string | null | undefined, emergencyType?: string | null): Zone[] {
  const ids = (conditionKey && BY_CONDITION[conditionKey]) || (emergencyType && BY_TYPE[emergencyType]) || [];
  return ids.map((id) => ZONES[id]);
}

/** Gravedad del paciente → tono del resaltado: rojo si es crítico, ámbar en el resto. */
export function alertTone(severity: string | null | undefined): "crit" | "warn" {
  return severity === "critical" ? "crit" : "warn";
}
