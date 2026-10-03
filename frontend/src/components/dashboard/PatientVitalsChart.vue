<script setup lang="ts">
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { storeToRefs } from "pinia";
import { computed } from "vue";
import VChart from "vue-echarts";
import { useSimulationStore } from "@/stores/simulation";

const CHART_MAX_POINTS = 48;

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent]);

const store = useSimulationStore();
const { vitalsHistory, state, selectedAmbulanceId } = storeToRefs(store);

const selectedHasPatient = computed(() => {
  const id = selectedAmbulanceId.value;
  if (!id || !state.value?.ambulances?.length) return false;
  const a = state.value.ambulances.find((x) => x.id === id);
  return Boolean(a?.hasPatient && a?.telemetry?.medical);
});

const option = computed(() => {
  if (!selectedHasPatient.value) {
    return {
      backgroundColor: "transparent",
      graphic: {
        type: "text",
        left: "center",
        top: "middle",
        style: {
          text: "Sin paciente en tránsito — vitales no aplican",
          fill: "#64748b",
          fontSize: 13,
        },
      },
      series: [],
    };
  }
  const raw = vitalsHistory.value;
  const stride = raw.length > CHART_MAX_POINTS * 2 ? 2 : 1;
  const pts =
    stride > 1
      ? raw.filter((_, i) => i % stride === 0 || i === raw.length - 1)
      : raw;
  const times = pts.map((_, i) => i);
  return {
    graphic: [] as unknown[],
    backgroundColor: "transparent",
    textStyle: { color: "#94a3b8" },
    tooltip: { trigger: "axis" },
    legend: { data: ["BPM", "SpO₂"], textStyle: { color: "#94a3b8" } },
    grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
    xAxis: { type: "category", data: times, show: false },
    yAxis: [
      { type: "value", name: "BPM", splitLine: { lineStyle: { color: "#334155" } } },
      { type: "value", name: "SpO₂", min: 85, max: 100, splitLine: { show: false } },
    ],
    series: [
      {
        name: "BPM",
        type: "line",
        smooth: true,
        data: pts.map((p) => p.hr),
        itemStyle: { color: "#f472b6" },
      },
      {
        name: "SpO₂",
        type: "line",
        smooth: true,
        yAxisIndex: 1,
        data: pts.map((p) => p.spo2),
        itemStyle: { color: "#34d399" },
      },
    ],
  };
});
</script>

<template>
  <div class="h-[220px] w-full rounded-xl border border-slate-700 bg-slate-900/80 p-2">
    <p class="mb-1 text-xs font-medium text-slate-400" title="Constantes vitales simuladas (motor médico)">
      Paciente · vitales (alta frecuencia)
    </p>
    <VChart class="h-[180px] w-full" :option="option" autoresize />
  </div>
</template>
