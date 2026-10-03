import type { Ambulance, EntityType } from "@/types/simulation";

/** Prefijo corto según tipo de vehículo (para labels internos AMB-001, HELI-01…). */
export function prefixForType(typeId: string, typeName?: string): string {
  if (typeId === "ambulance") return "AMB";
  if (typeId === "helicopter") return "HELI";
  if (typeId === "police_patrol") return "POL";
  const base = (typeName || typeId).toUpperCase().replace(/[^A-Z0-9]/g, "");
  return base.slice(0, 4) || "UNID";
}

/**
 * Devuelve el identificador visible de una unidad. Orden de preferencia:
 *   1. `displayLabel` si el spawn le asignó uno (lo más común con tipos custom)
 *   2. `prefix-NNN` calculado a partir del tipo real
 */
export function displayId(
  amb: Ambulance,
  idx?: number,
  entityTypes?: EntityType[] | null,
): string {
  if (amb.displayLabel) return amb.displayLabel;
  const typeId = amb.entityTypeId || "ambulance";
  const type = entityTypes?.find((t) => t.id === typeId);
  const prefix = prefixForType(typeId, type?.name);
  if (idx != null) return `${prefix}-${String(idx + 1).padStart(3, "0")}`;
  return `${prefix}-${amb.id.slice(0, 6).toUpperCase()}`;
}
