<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed } from "vue";
import TelemetryLineChart from "./TelemetryLineChart.vue";
import { useSimulationStore } from "@/stores/simulation";

const CHART_MAX_POINTS = 48;
/** Umbrales clínicos que se marcan en ámbar sobre cada serie (un gráfico por magnitud: sin doble eje). */
const HR_LIMITS = { low: 50, high: 120 };
const SPO2_LIMIT = 92;

const store = useSimulationStore();
const { vitalsHistory, state, selectedAmbulanceId } = storeToRefs(store);

const selectedHasPatient = computed(() => {
  const id = selectedAmbulanceId.value;
  if (!id || !state.value?.ambulances?.length) return false;
  const a = state.value.ambulances.find((x) => x.id === id);
  return Boolean(a?.hasPatient && a?.telemetry?.medical);
});

const points = computed(() => {
  const raw = vitalsHistory.value;
  const stride = raw.length > CHART_MAX_POINTS * 2 ? 2 : 1;
  return stride > 1 ? raw.filter((_, i) => i % stride === 0 || i === raw.length - 1) : raw;
});

const latest = computed(() => points.value[points.value.length - 1] ?? null);

const hrAlert = computed(() => latest.value != null && (latest.value.hr < HR_LIMITS.low || latest.value.hr > HR_LIMITS.high));
const spo2Alert = computed(() => latest.value != null && latest.value.spo2 < SPO2_LIMIT);
</script>

<template>
  <div class="panel">
    <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
      <p class="text-[11px] font-medium uppercase tracking-wider text-slate-400" title="Constantes vitales simuladas (motor médico)">
        Paciente · constantes vitales
      </p>
    </div>
    <p v-if="!selectedHasPatient" class="px-3 py-10 text-center text-xs text-slate-500">
      Sin paciente en tránsito — las constantes no aplican
    </p>
    <div v-else class="grid grid-cols-1 sm:grid-cols-2">
      <div class="border-slate-800 p-2 sm:border-r">
        <div class="flex items-baseline justify-between px-1">
          <span class="text-[10px] uppercase tracking-wider text-slate-500">Frecuencia cardiaca</span>
          <span class="font-mono text-sm" :class="hrAlert ? 'text-amber-300' : 'text-slate-100'">
            {{ latest?.hr ?? "—" }}<span class="ml-0.5 text-[10px] text-slate-500">bpm</span>
          </span>
        </div>
        <TelemetryLineChart :values="points.map((x) => x.hr)" label="BPM" :thresholds="[HR_LIMITS.low, HR_LIMITS.high]" />
      </div>
      <div class="border-t border-slate-800 p-2 sm:border-t-0">
        <div class="flex items-baseline justify-between px-1">
          <span class="text-[10px] uppercase tracking-wider text-slate-500">Saturación O₂</span>
          <span class="font-mono text-sm" :class="spo2Alert ? 'text-red-300' : 'text-slate-100'">
            {{ latest?.spo2 ?? "—" }}<span class="ml-0.5 text-[10px] text-slate-500">%</span>
          </span>
        </div>
        <TelemetryLineChart :values="points.map((x) => x.spo2)" label="SpO₂" unit="%" :min="85" :max="100" :thresholds="[SPO2_LIMIT]" />
      </div>
    </div>
  </div>
</template>
