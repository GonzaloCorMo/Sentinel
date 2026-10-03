<script setup lang="ts">
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import { useI18n } from "vue-i18n";
import FleetTelemetryCard from "@/components/fleet/FleetTelemetryCard.vue";
import PatientVitalsChart from "@/components/dashboard/PatientVitalsChart.vue";
import { useSimulationStore } from "@/stores/simulation";
import { displayId } from "@/lib/vehicleId";

const { t } = useI18n();
const store = useSimulationStore();
const { state, selectedAmbulanceId, uiFilters } = storeToRefs(store);

function selectAmbulance(id: string) {
  store.selectAmbulance(id);
}

const typeFilter = ref<string>("all");
const searchQuery = ref<string>("");
const patientOnlyFilter = ref<boolean>(false);
const sortBy = ref<"default" | "fuel" | "battery" | "severity">("default");

const entityTypesMap = computed<Record<string, string>>(() => {
  const m: Record<string, string> = {};
  for (const ty of state.value?.entityTypes ?? []) m[ty.id] = ty.name;
  return m;
});

function powertrainOf(a: { entityTypeId?: string }): "combustion" | "electric" | "unique" {
  const et = (state.value?.entityTypes ?? []).find((t) => t.id === (a.entityTypeId || "ambulance"));
  return (et?.powertrain as "combustion" | "electric" | "unique") || "combustion";
}

function energyPctOf(a: { entityTypeId?: string; fuelLevel?: number; telemetry?: { mechanical?: { batteryPct?: number } } }): number {
  return powertrainOf(a) === "electric"
    ? (a.telemetry?.mechanical?.batteryPct ?? 100)
    : (a.fuelLevel ?? 100);
}

const availableTypes = computed(() => {
  const set = new Set<string>();
  for (const a of state.value?.ambulances ?? []) set.add(a.entityTypeId || "ambulance");
  return Array.from(set).sort();
});

const filteredAmbulances = computed(() => {
  const ambs = state.value?.ambulances ?? [];
  const q = searchQuery.value.trim().toLowerCase();
  const ui = uiFilters.value;
  let list = ambs.filter((a) => {
    const type = a.entityTypeId || "ambulance";
    if (typeFilter.value !== "all" && type !== typeFilter.value) return false;
    if (patientOnlyFilter.value && !a.hasPatient) return false;
    if (ui.entityTypeId && type !== ui.entityTypeId) return false;
    if (ui.fuelBelow != null && (a.fuelLevel ?? 100) >= ui.fuelBelow) return false;
    if (ui.fuelAbove != null && (a.fuelLevel ?? 0) <= ui.fuelAbove) return false;
    if (ui.batteryBelow != null && (a.telemetry?.mechanical?.batteryPct ?? 100) >= ui.batteryBelow) return false;
    if (ui.hasPatient != null && !!a.hasPatient !== ui.hasPatient) return false;
    if (ui.severity && a.patientSeverity !== ui.severity) return false;
    if (ui.missionPhase && (a.missionPhase || "idle") !== ui.missionPhase) return false;
    if (ui.poweredOff != null && !!a.poweredOff !== ui.poweredOff) return false;
    if (q) {
      const hay = `${a.id} ${a.displayLabel || ""} ${a.locationLabel || ""} ${type}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  if (uiFilters.value.limit) list = list.slice(0, uiFilters.value.limit);
  if (sortBy.value === "fuel") {
    list = [...list].sort((a, b) => energyPctOf(a) - energyPctOf(b));
  } else if (sortBy.value === "battery") {
    list = [...list].sort((a, b) => (a.telemetry?.mechanical?.batteryPct ?? 100) - (b.telemetry?.mechanical?.batteryPct ?? 100));
  } else if (sortBy.value === "severity") {
    const rank: Record<string, number> = { critical: 0, moderate: 1, stable: 2 };
    list = [...list].sort((a, b) => (rank[a.patientSeverity || ""] ?? 9) - (rank[b.patientSeverity || ""] ?? 9));
  }
  return list;
});

const stats = computed(() => {
  const all = state.value?.ambulances ?? [];
  return {
    total: all.length,
    shown: filteredAmbulances.value.length,
    withPatient: all.filter((a) => a.hasPatient).length,
    lowFuel: all.filter((a) => energyPctOf(a) < 25).length,
    idle: all.filter((a) => (a.missionPhase ?? "idle") === "idle" || !a.missionPhase).length,
  };
});

function typeLabel(id: string): string {
  return entityTypesMap.value[id] || id;
}

function resetFilters() {
  typeFilter.value = "all";
  searchQuery.value = "";
  patientOnlyFilter.value = false;
  sortBy.value = "default";
}

interface MlPredictionRow {
  ambulanceId: string;
  isAnomaly?: boolean;
  anomalyScore?: number;
  severity?: string;
  ok?: boolean;
}
const mlResults = ref<MlPredictionRow[]>([]);
const SEVERITY_ORDER: Record<string, number> = { critical: 0, warning: 1 };
/** Resultados ordenados (lo urgente primero) y con el nombre visible de cada unidad. */
const mlRows = computed(() =>
  [...mlResults.value]
    .sort((a, b) => (SEVERITY_ORDER[a.severity ?? ""] ?? 2) - (SEVERITY_ORDER[b.severity ?? ""] ?? 2))
    .map((r) => {
      const amb = state.value?.ambulances?.find((a) => a.id === r.ambulanceId);
      return { ...r, label: amb ? displayId(amb, undefined, state.value?.entityTypes) : r.ambulanceId.slice(0, 8) };
    }),
);
const mlBusy = ref(false);
async function runMlPrediction() {
  mlBusy.value = true;
  try {
    const r = await fetch("/api/ml/predict/fleet-anomaly/all", { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    mlResults.value = (d.results || []) as MlPredictionRow[];
  } catch (e) {
    console.error("ml predict failed", e);
  } finally {
    mlBusy.value = false;
  }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="text-lg font-semibold tracking-tight text-slate-100">{{ t('fleet.title') }}</h1>
        <p class="mt-0.5 text-sm text-slate-500">
          {{ t('fleet.subtitle') }}
        </p>
      </div>
      <dl class="flex divide-x divide-slate-800 rounded border border-slate-800 bg-slate-900 text-[11px]">
        <div class="px-3 py-1.5">
          <dt class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('fleet.units') }}</dt>
          <dd class="font-mono text-sm text-slate-100">{{ stats.shown }}<span class="text-slate-500">/{{ stats.total }}</span></dd>
        </div>
        <div class="px-3 py-1.5">
          <dt class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('fleet.with_patient') }}</dt>
          <dd class="font-mono text-sm text-slate-100">{{ stats.withPatient }}</dd>
        </div>
        <div class="px-3 py-1.5">
          <dt class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('fleet.low_fuel') }}</dt>
          <dd class="font-mono text-sm" :class="stats.lowFuel > 0 ? 'text-amber-300' : 'text-slate-100'">{{ stats.lowFuel }}</dd>
        </div>
        <div class="px-3 py-1.5">
          <dt class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('fleet.available') }}</dt>
          <dd class="font-mono text-sm text-slate-100">{{ stats.idle }}</dd>
        </div>
      </dl>
    </div>

    <div class="rounded-xl border border-slate-800/70 bg-slate-950/40 p-3">
      <div class="flex flex-wrap items-end gap-3">
        <div class="min-w-[12rem] flex-1">
          <label class="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ t('fleet.search') }}</label>
          <input
            v-model="searchQuery"
            type="search"
            :placeholder="t('fleet.search_placeholder')"
            class="w-full rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-emerald-500/50"
          />
        </div>
        <div class="min-w-[10rem]">
          <label class="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ t('fleet.unit_type') }}</label>
          <select
            v-model="typeFilter"
            class="w-full rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-emerald-500/50"
          >
            <option value="all">{{ t('fleet.all') }}</option>
            <option v-for="ty in availableTypes" :key="ty" :value="ty">{{ typeLabel(ty) }}</option>
          </select>
        </div>
        <div class="min-w-[10rem]">
          <label class="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ t('fleet.sort_by') }}</label>
          <select
            v-model="sortBy"
            class="w-full rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-emerald-500/50"
          >
            <option value="default">{{ t('fleet.sort_default') }}</option>
            <option value="fuel">{{ t('fleet.sort_fuel') }}</option>
            <option value="battery">{{ t('fleet.sort_battery') }}</option>
            <option value="severity">{{ t('fleet.sort_severity') }}</option>
          </select>
        </div>
        <label class="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-300">
          <input v-model="patientOnlyFilter" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-800 text-emerald-500 focus:ring-emerald-500/30" />
          {{ t('fleet.patient_only') }}
        </label>
        <button
          type="button"
          class="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-400 transition hover:bg-slate-800"
          @click="resetFilters"
        >{{ t('fleet.reset') }}</button>
      </div>
    </div>

    <div v-if="filteredAmbulances.length" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <FleetTelemetryCard
        v-for="(a, idx) in filteredAmbulances"
        :key="a.id"
        :amb="a"
        :index="idx"
        :selected="selectedAmbulanceId === a.id"
        @click="selectAmbulance(a.id)"
      />
    </div>
    <p v-else-if="stats.total === 0" class="rounded border border-dashed border-slate-700 p-8 text-center text-sm text-slate-500">
      {{ t('fleet.no_units_sim') }}
    </p>
    <p v-else class="rounded border border-dashed border-slate-700 p-8 text-center text-sm text-slate-500">
      {{ t('fleet.no_units_filter') }}
    </p>

    <section class="panel">
      <div class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div class="max-w-xl">
          <h2 class="text-sm font-medium text-slate-100">{{ t('fleet.ml_pipeline') }}</h2>
          <p class="text-xs text-slate-500">{{ t('fleet.ml_desc') }}</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button
            class="rounded bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-950 hover:bg-slate-300 disabled:opacity-50"
            :disabled="mlBusy"
            @click="runMlPrediction"
          >{{ mlBusy ? t('fleet.predicting') : t('fleet.predict_now') }}</button>
          <a class="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 no-underline hover:bg-slate-800" href="/api/ml/export?hours=6&format=csv" download>{{ t('fleet.csv_6h') }}</a>
          <a class="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 no-underline hover:bg-slate-800" href="/api/ml/export?hours=6&format=parquet" download>{{ t('fleet.parquet_6h') }}</a>
        </div>
      </div>
      <ul v-if="mlRows.length" class="divide-y divide-slate-800">
        <li v-for="r in mlRows" :key="r.ambulanceId" class="flex items-center justify-between gap-3 px-4 py-2 text-xs">
          <span class="font-mono text-slate-200">{{ r.label }}</span>
          <span
            class="rounded-sm border px-1.5 py-px"
            :class="r.severity === 'critical'
              ? 'border-red-500/40 text-red-300'
              : r.severity === 'warning'
                ? 'border-amber-500/40 text-amber-300'
                : 'border-slate-700 text-slate-400'"
            :title="`${t('fleet.anomaly_score')}: ${r.anomalyScore?.toFixed(3) ?? '—'}`"
          >{{ r.severity === 'critical' ? t('fleet.ml_critical') : r.severity === 'warning' ? t('fleet.ml_warning') : t('fleet.normal_label') }}</span>
        </li>
      </ul>
      <p v-else class="px-4 py-3 text-xs text-slate-500">{{ t('fleet.ml_no_predictions') }}</p>
    </section>

    <div class="max-w-3xl">
      <p class="mb-2 text-xs text-slate-500">
        {{ t('fleet.vitals_title') }}
      </p>
      <PatientVitalsChart />
    </div>
  </div>
</template>
