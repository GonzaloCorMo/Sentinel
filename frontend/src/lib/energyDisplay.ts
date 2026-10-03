/**
 * Resuelve la fuente de energía visible de un vehículo en función de la
 * propulsión declarada en su `entityType`:
 *
 *   - `combustion` → muestra **combustible** (fuelLevelPct).
 *   - `electric`/`unique` → muestra **batería** (batteryPct).
 *   - tipo desconocido / vehículo legacy → cae a combustible (compat).
 *
 * Devuelve label localizado + valor + clave i18n usada
 * para que la UI pueda volver a traducir si cambia el idioma sin
 * recomputar el catálogo.
 */
import type { Ambulance, EntityType } from "@/types/simulation";

export type EnergyKind = "fuel" | "battery";

export interface EnergyInfo {
  kind: EnergyKind;
  /** Valor 0-100 (%). null si no hay telemetría aún. */
  value: number | null;
  /** Clave i18n del label corto ("operations.fuel" | "operations.battery"). */
  labelKey: string;
  /** Clave i18n del label genérico ("operations.energy"). */
  energyKey: string;
}

export function powertrainOf(
  amb: Ambulance,
  entityTypes: EntityType[] | undefined,
): "combustion" | "electric" | "unique" {
  const id = amb.entityTypeId;
  if (id && entityTypes) {
    const t = entityTypes.find((e) => e.id === id);
    if (t?.powertrain === "electric" || t?.powertrain === "unique" || t?.powertrain === "combustion") {
      return t.powertrain;
    }
  }
  return "combustion";
}

export interface OperatingCost {
  /** Activación €. 0 si vehículo nunca activó. */
  activation: number;
  /** Coste tiempo activo €. */
  runtime: number;
  /** Suma activación + runtime €. */
  total: number;
  /** Minutos activos (sim time). */
  activeMinutes: number;
  /** Tarifa €/min para mostrar. */
  ratePerMin: number;
  /** Si tiene metadata coste (entityType.costPerMin definido). */
  available: boolean;
}

export function operatingCostOf(
  amb: Ambulance,
  entityTypes: EntityType[] | undefined,
): OperatingCost {
  const id = amb.entityTypeId;
  const t = id && entityTypes ? entityTypes.find((e) => e.id === id) : undefined;
  const ratePerMin = t?.costPerMin ?? 0;
  const activationCost = t?.activationCost ?? 0;
  const activeSeconds = (amb as Ambulance & { activeSeconds?: number }).activeSeconds ?? 0;
  const activated = (amb as Ambulance & { activated?: boolean }).activated ?? false;
  const minutes = activeSeconds / 60;
  const runtime = minutes * ratePerMin;
  const activation = activated ? activationCost : 0;
  return {
    activation,
    runtime,
    total: activation + runtime,
    activeMinutes: minutes,
    ratePerMin,
    available: !!t && (ratePerMin > 0 || activationCost > 0),
  };
}

export function energyOf(
  amb: Ambulance,
  entityTypes: EntityType[] | undefined,
): EnergyInfo {
  const pt = powertrainOf(amb, entityTypes);
  if (pt === "combustion") {
    const v = amb.telemetry?.mechanical?.fuelLevelPct ?? amb.fuelLevel ?? null;
    return {
      kind: "fuel",
      value: v == null ? null : Math.round(v * 10) / 10,
      labelKey: "operations.fuel",
      energyKey: "operations.energy",
    };
  }
  // electric o unique → batería
  const v = amb.telemetry?.mechanical?.batteryPct ?? amb.batteryLevel ?? null;
  return {
    kind: "battery",
    value: v == null ? null : Math.round(v * 10) / 10,
    labelKey: "operations.battery",
    energyKey: "operations.energy",
  };
}
