<script setup lang="ts">
import { GaugeChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { storeToRefs } from "pinia";
import { computed } from "vue";
import VChart from "vue-echarts";
import { useSimulationStore } from "@/stores/simulation";

use([CanvasRenderer, GaugeChart, TooltipComponent]);

const store = useSimulationStore();
const { state, selectedAmbulanceId } = storeToRefs(store);

const selected = computed(() => {
  const id = selectedAmbulanceId.value;
  if (!id || !state.value) return null;
  return state.value.ambulances.find((a) => a.id === id) ?? null;
});

function clampPct(n: number) {
  if (!Number.isFinite(n)) return 0;
  return Math.min(100, Math.max(0, n));
}

const fuelOption = computed(() => {
  const v = clampPct(selected.value?.telemetry?.mechanical?.fuelLevelPct ?? 0);
  return gaugeOption("Combustible %", v, "#01a982");
});

const battOption = computed(() => {
  const v = clampPct(selected.value?.telemetry?.mechanical?.batteryPct ?? 0);
  return gaugeOption("Batería %", v, "#38bdf8");
});

function gaugeOption(name: string, value: number, color: string) {
  return {
    backgroundColor: "transparent",
    series: [
      {
        type: "gauge",
        center: ["50%", "58%"],
        radius: "72%",
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: 100,
        splitNumber: 5,
        axisLine: {
          lineStyle: {
            width: 10,
            color: [
              [0.3, "#ef4444"],
              [0.7, "#fbbf24"],
              [1, color],
            ],
          },
        },
        pointer: { itemStyle: { color } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: "#94a3b8", distance: 12, fontSize: 9 },
        title: { show: false },
        detail: {
          valueAnimation: true,
          formatter: "{value}%",
          color: "#e2e8f0",
          fontSize: 14,
          offsetCenter: [0, "24%"],
        },
        data: [{ value: Math.round(value * 10) / 10, name }],
      },
    ],
  };
}
</script>

<template>
  <div class="shrink-0 space-y-2">
    <p class="text-xs font-medium text-slate-400">Mecánica</p>
    <div class="grid grid-cols-2 gap-2">
      <div class="overflow-hidden rounded-xl border border-slate-700 bg-slate-900/80 p-1">
        <p class="px-1 text-[10px] text-slate-500">Combustible</p>
        <div class="h-[132px] w-full min-h-[132px] max-h-[132px]">
          <VChart class="!h-full !min-h-0 !max-h-full w-full" :option="fuelOption" autoresize />
        </div>
      </div>
      <div class="overflow-hidden rounded-xl border border-slate-700 bg-slate-900/80 p-1">
        <p class="px-1 text-[10px] text-slate-500">Batería</p>
        <div class="h-[132px] w-full min-h-[132px] max-h-[132px]">
          <VChart class="!h-full !min-h-0 !max-h-full w-full" :option="battOption" autoresize />
        </div>
      </div>
      <p
        v-if="selected?.telemetry?.mechanical"
        class="col-span-2 text-xs text-slate-400"
        :title="`Motor ${selected.telemetry.mechanical.engineTempC}°C · sirenas ${selected.telemetry.mechanical.sirensOn}`"
      >
        Motor {{ selected.telemetry.mechanical.engineTempC }}°C · sirenas
        {{ selected.telemetry.mechanical.sirensOn ? "ON" : "OFF" }}
      </p>
    </div>
  </div>
</template>
