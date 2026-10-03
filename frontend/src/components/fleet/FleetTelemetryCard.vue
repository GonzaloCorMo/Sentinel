<script setup lang="ts">
import { GaugeChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { computed, shallowRef, watch } from "vue";
import VChart from "vue-echarts";
import { storeToRefs } from "pinia";
import { useI18n } from "vue-i18n";
import type { Ambulance } from "@/types/simulation";
import { useSimulationStore } from "@/stores/simulation";
import { displayId } from "@/lib/vehicleId";
import { energyOf, powertrainOf } from "@/lib/energyDisplay";

use([CanvasRenderer, GaugeChart, TooltipComponent]);

const props = defineProps<{ amb: Ambulance; selected?: boolean; index?: number }>();

defineEmits<{ click: [] }>();

const { t } = useI18n();
const store = useSimulationStore();
const { state } = storeToRefs(store);

const powertrain = computed<"combustion" | "electric" | "unique">(() => {
  const et = (state.value?.entityTypes ?? []).find((t) => t.id === (props.amb.entityTypeId || "ambulance_combustion"));
  return (et?.powertrain as "combustion" | "electric" | "unique") || "combustion";
});

function shortId(_id: string, idx?: number): string {
  return displayId(props.amb, idx, state.value?.entityTypes);
}

const severityLabel: Record<string, string> = {
  stable: "Estable",
  moderate: "Moderado",
  critical: "Critico",
};

function miniGauge(name: string, value: number, color: string, maxScale = 100) {
  const v = Math.min(maxScale, Math.max(0, value));
  return {
    backgroundColor: "transparent",
    series: [
      {
        type: "gauge",
        center: ["50%", "55%"],
        radius: "88%",
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: maxScale,
        axisLine: { lineStyle: { width: 8, color: [[1, color]] } },
        pointer: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        detail: { formatter: "{value}", fontSize: 11, color: "#e2e8f0", offsetCenter: [0, "18%"] },
        title: { show: true, offsetCenter: [0, "65%"], fontSize: 10, color: "#94a3b8" },
        data: [{ value: Math.round(v * 10) / 10, name }],
      },
    ],
  };
}

const primaryEnergyOpt = shallowRef(miniGauge(t("operations.fuel"), 0, "#01a982"));
const energyOpt = shallowRef(miniGauge(t("operations.battery"), 0, "#38bdf8"));
const gpsOpt = shallowRef(miniGauge("km/h", 0, "#fbbf24", 120));

watch(
  (): [string, number, number, number, string] => {
    const info = energyOf(props.amb, state.value?.entityTypes);
    const pt = powertrainOf(props.amb, state.value?.entityTypes);
    return [
      pt,
      info.value ?? 0,
      Math.round((props.amb.telemetry?.mechanical?.batteryPct ?? 0) * 10) / 10,
      Math.round((props.amb.telemetry?.positioning?.speedKmh ?? 0) * 10) / 10,
      info.labelKey,
    ];
  },
  ([pt, primary, batt, speed, labelKey]) => {
    // Gauge primario: combustible (combustion) o batería/carga (electric/unique).
    const primaryColor = pt === "combustion" ? "#01a982" : pt === "unique" ? "#a855f7" : "#38bdf8";
    primaryEnergyOpt.value = miniGauge(t(labelKey), primary, primaryColor);
    // Gauge secundario: en combustión mostramos batería auxiliar.
    // En eléctrico/unique mostramos velocidad para evitar redundancia.
    if (pt === "combustion") {
      energyOpt.value = miniGauge(t("operations.battery"), batt, "#38bdf8");
    } else {
      energyOpt.value = miniGauge("km/h", speed, "#fbbf24", 120);
    }
    gpsOpt.value = miniGauge("km/h", speed, "#fbbf24", 120);
  },
  { immediate: true },
);
</script>

<template>
  <div
    class="hpe-card flex cursor-pointer flex-col gap-2 p-3 transition-all"
    :class="selected ? 'hpe-card-selected' : ''"
    @click="$emit('click')"
  >
    <!-- Header row -->
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h3 class="font-mono text-sm font-semibold text-emerald-400" :title="amb.id">{{ shortId(amb.id, index) }}</h3>
      <div class="flex items-center gap-1.5">
        <span
          v-if="amb.patientSeverity"
          :class="['rounded-md px-2 py-0.5 text-[10px] font-semibold', `severity-${amb.patientSeverity}`]"
        >
          {{ severityLabel[amb.patientSeverity] ?? amb.patientSeverity }}
        </span>
        <span :class="['fsm-chip', `fsm-${amb.fsmState ?? 'idle'}`]">
          <span class="fsm-dot" />
          {{ amb.fsmState ?? "idle" }}
        </span>
      </div>
    </div>

    <!-- Phase + GPS -->
    <p class="text-[10px] text-slate-500">
      Fase: {{ amb.missionPhase ?? "—" }} · GPS {{ (amb.latitude ?? 0).toFixed(4) }},
      {{ (amb.longitude ?? 0).toFixed(4) }}
    </p>

    <!-- Gauges -->
    <div class="grid grid-cols-2 gap-1">
      <div class="h-[100px]">
        <VChart class="h-full w-full" :option="primaryEnergyOpt" autoresize />
      </div>
      <div class="h-[100px]">
        <VChart class="h-full w-full" :option="energyOpt" autoresize />
      </div>
      <div class="h-[100px]">
        <VChart class="h-full w-full" :option="gpsOpt" autoresize />
      </div>
    </div>

    <!-- Medical / GPS details -->
    <div class="relative grid grid-cols-2 gap-2 border-t border-slate-800/60 pt-2 text-xs text-slate-400">
      <div
        v-if="!amb.hasPatient || amb.telemetry?.medical == null"
        class="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-slate-950/90 px-2 text-center text-[11px] text-slate-500"
      >
        Sin paciente en transito
      </div>
      <div>
        <p class="text-[9px] font-medium uppercase tracking-wider text-slate-500">Medico</p>
        <p class="mt-0.5">
          BPM {{ amb.telemetry?.medical?.heartRateBpm ?? "—" }} · SpO2
          <span :class="(amb.telemetry?.medical?.spo2Pct ?? 100) < 90 ? 'text-rose-400 font-semibold' : ''">
            {{ amb.telemetry?.medical?.spo2Pct ?? "—" }}%
          </span>
        </p>
      </div>
      <div>
        <p class="text-[9px] font-medium uppercase tracking-wider text-slate-500">GPS</p>
        <p class="mt-0.5">
          Rumbo {{ amb.telemetry?.positioning?.headingDeg ?? "—" }}° · a
          {{ amb.telemetry?.positioning?.accelerationMs2 ?? "—" }} m/s2
        </p>
      </div>
    </div>
  </div>
</template>
