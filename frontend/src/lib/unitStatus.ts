/**
 * Estado legible de una unidad a partir de la fase de misión del motor.
 *
 * El backend expone `missionPhase` (idle, to_emergency, to_hospital…) y un
 * `fsmState` técnico (IDLE, RESPONDING…). En la interfaz solo se muestra
 * esta traducción, con un tono que decide el color del chip.
 */
import type { Ambulance } from "@/types/simulation";

export type StatusTone = "neutral" | "active" | "ok" | "alert";

export interface UnitStatus {
  /** Clave i18n bajo `status.*`. */
  key: "available" | "to_emergency" | "on_scene" | "transporting" | "handover" | "refueling" | "repositioning" | "unavailable" | "off";
  tone: StatusTone;
}

export function unitStatus(amb: Pick<Ambulance, "missionPhase" | "fsmState" | "hasPatient"> & { poweredOff?: boolean }): UnitStatus {
  // «poweredOff» = unidad libre con el motor parado en reserva: está disponible.
  if (String(amb.fsmState ?? "").toUpperCase() === "UNAVAILABLE") return { key: "unavailable", tone: "alert" };
  switch (amb.missionPhase) {
    case "to_emergency":
      return { key: "to_emergency", tone: "active" };
    case "on_scene":
      return { key: "on_scene", tone: "active" };
    case "at_hospital":
      return { key: "handover", tone: "active" };
    case "refueling":
    case "to_hospital":
      return { key: "transporting", tone: "active" };
    case "to_refuel":
      return { key: "refueling", tone: "neutral" };
    case "to_staging":
      return { key: "repositioning", tone: "neutral" };
    default:
      return { key: "available", tone: "ok" };
  }
}
