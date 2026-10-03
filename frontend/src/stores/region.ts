/**
 * Store de regiones / mapas seleccionables.
 *
 * Espejo del registro backend (`simulation/app/regions.py`). Cargado al
 * arranque vía `/api/regions`. Cambiar la activa llama a
 * `POST /api/regions/active`, que también resetea la simulación en backend.
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { i18n } from "@/i18n";

export interface RegionInfo {
  id: string;
  name: string;
  country: string;
  timezone?: string;
  center: [number, number];
  zoom: number;
  spawn: [number, number];
}

interface RegionsResponse {
  active: string;
  regions: RegionInfo[];
}

export const useRegionStore = defineStore("region", () => {
  const regions = ref<RegionInfo[]>([]);
  const activeId = ref<string>("santiago");
  const loading = ref(false);
  const switching = ref(false);

  const active = computed<RegionInfo | null>(() =>
    regions.value.find((r) => r.id === activeId.value) ?? null,
  );

  async function fetchRegions() {
    loading.value = true;
    try {
      const r = await fetch("/api/regions");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as RegionsResponse;
      regions.value = j.regions;
      activeId.value = j.active;
    } catch (e) {
      console.error("[region] fetch fallo", e);
    } finally {
      loading.value = false;
    }
  }

  async function setActive(regionId: string): Promise<RegionInfo | null> {
    if (regionId === activeId.value) return active.value;
    switching.value = true;
    try {
      const r = await fetch("/api/regions/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ regionId }),
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => "");
        throw new Error(txt || `HTTP ${r.status}`);
      }
      const j = (await r.json()) as { active: string; region: RegionInfo };
      activeId.value = j.active;
      const next = regions.value.find((x) => x.id === j.active) ?? j.region;
      const t = i18n.global.t;
      toast.success(t("region.switch_success", { name: next.name }), {
        description: t("region.switch_success_desc"),
      });
      return next;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(i18n.global.t("region.switch_error"), { description: msg });
      return null;
    } finally {
      switching.value = false;
    }
  }

  return { regions, activeId, active, loading, switching, fetchRegions, setActive };
});
