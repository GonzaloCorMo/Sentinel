/**
 * Pinia store del catálogo de tipos (vehicle/place) y config de despacho.
 *
 * Lista builtin + custom + IA; permite crear/editar/borrar tipos custom
 * y conmutar la flag `dispatchRequiresApproval` (auto-dispatch vs HITL).
 * Expone `generateIncidents(count)` para demos del Escenario builder.
 */
import { defineStore } from "pinia";
import { ref } from "vue";
import { toast } from "vue-sonner";
import type { EntityType } from "@/types/simulation";

export const useEntitiesStore = defineStore("entities", () => {
  const entityTypes = ref<EntityType[]>([]);
  const dispatchConfig = ref<{ dispatchRequiresApproval: boolean }>({ dispatchRequiresApproval: false });
  const loading = ref(false);

  /** Recarga el catálogo completo (builtin + custom + IA) desde el backend. */
  async function fetchEntityTypes() {
    try {
      const r = await fetch("/api/sim/entity-types");
      if (r.ok) entityTypes.value = await r.json();
    } catch {
      /* silent */
    }
  }

  /** Registra un tipo custom; el backend auto-genera desc/caps si se omiten. */
  async function createEntityType(data: {
    name: string;
    kind: string;
    speedKmh?: number;
    color: string;
    iconSvg?: string | null;
    description?: string | null;
    capabilities?: string | null;
    powertrain?: "combustion" | "electric" | "unique" | null;
    crewMin?: number;
    crewMax?: number;
    costPerMin?: number;
    activationCost?: number;
  }) {
    const r = await fetch("/api/sim/entity-types", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!r.ok) throw new Error("Error al crear tipo de entidad");
    const res = await r.json();
    await fetchEntityTypes();
    return res.id as string;
  }

  /** Borra un tipo custom (no afecta a builtin). */
  async function removeEntityType(id: string) {
    const r = await fetch(`/api/sim/entity-types/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error("Error al eliminar tipo de entidad");
    await fetchEntityTypes();
  }

  /** Parchea un tipo custom; vaciar desc/caps los regenera con IA. */
  async function updateEntityType(id: string, patch: {
    name?: string;
    speedKmh?: number | null;
    color?: string;
    iconSvg?: string | null;
    description?: string | null;
    capabilities?: string[] | null;
    powertrain?: "combustion" | "electric" | "unique" | null;
    crewMin?: number | null;
    crewMax?: number | null;
    costPerMin?: number | null;
    activationCost?: number | null;
  }) {
    const r = await fetch(`/api/sim/entity-types/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error("Error al actualizar tipo de entidad");
    const res = await r.json();
    await fetchEntityTypes();
    return res;
  }

  async function fetchDispatchConfig() {
    try {
      const r = await fetch("/api/sim/dispatch-config");
      if (r.ok) dispatchConfig.value = await r.json();
    } catch {
      /* silent */
    }
  }

  async function setDispatchConfig(requiresApproval: boolean) {
    const r = await fetch("/api/sim/dispatch-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dispatchRequiresApproval: requiresApproval }),
    });
    if (r.ok) {
      dispatchConfig.value = await r.json();
    }
  }

  /** Genera N incidencias demo (LLM si disponible, sino pool fijo). */
  async function generateIncidents(count: number) {
    loading.value = true;
    try {
      const r = await fetch("/api/sim/generate-incidents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count }),
      });
      if (!r.ok) throw new Error("Error al generar incidencias");
      const data = await r.json();
      toast.success(`${data.generated} incidencias generadas`);
      return data;
    } catch (e) {
      toast.error("Error al generar incidencias");
      throw e;
    } finally {
      loading.value = false;
    }
  }

  return {
    entityTypes,
    dispatchConfig,
    loading,
    fetchEntityTypes,
    createEntityType,
    removeEntityType,
    updateEntityType,
    fetchDispatchConfig,
    setDispatchConfig,
    generateIncidents,
  };
});
