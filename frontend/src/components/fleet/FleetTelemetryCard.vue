<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useI18n } from "vue-i18n";
import type { Ambulance } from "@/types/simulation";
import { useSimulationStore } from "@/stores/simulation";
import { displayId } from "@/lib/vehicleId";
import { energyOf, powertrainOf } from "@/lib/energyDisplay";

const props = defineProps<{ amb: Ambulance; selected?: boolean; index?: number }>();

defineEmits<{ click: [] }>();

const { t } = useI18n();
const store = useSimulationStore();
const { state } = storeToRefs(store);

const SPEED_SCALE_KMH = 120;

const severityLabel: Record<string, string> = {
  stable: "Estable",
  moderate: "Moderado",
  critical: "Crítico",
};

const label = computed(() => displayId(props.amb, props.index, state.value?.entityTypes));

/** Barras de telemetría: energía primaria, batería auxiliar (solo combustión) y velocidad.
 *  Barras CSS en lugar de gauges en canvas: N tarjetas × 3 instancias de ECharts
 *  se repintaban en cada tick del stream. */
const bars = computed(() => {
  const types = state.value?.entityTypes;
  const info = energyOf(props.amb, types);
  const pt = powertrainOf(props.amb, types);
  const speed = props.amb.telemetry?.positioning?.speedKmh ?? 0;
  const rows: { key: string; label: string; value: number; unit: string; pct: number; level: "ok" | "warn" | "crit" }[] = [];
  const level = (pct: number) => (pct < 15 ? "crit" : pct < 30 ? "warn" : "ok");
  const primary = info.value ?? 0;
  rows.push({ key: "energy", label: t(info.labelKey), value: primary, unit: "%", pct: primary, level: level(primary) });
  if (pt === "combustion") {
    const batt = props.amb.telemetry?.mechanical?.batteryPct ?? 0;
    rows.push({ key: "battery", label: t("operations.battery"), value: batt, unit: "%", pct: batt, level: level(batt) });
  }
  rows.push({
    key: "speed",
    label: t("operations.speed"),
    value: speed,
    unit: "km/h",
    pct: Math.min(100, (speed / SPEED_SCALE_KMH) * 100),
    level: "ok",
  });
  return rows;
});

const lowSpo2 = computed(() => (props.amb.telemetry?.medical?.spo2Pct ?? 100) < 90);
</script>

<template>
  <div
    class="panel flex cursor-pointer flex-col p-0"
    :class="selected ? 'panel-selected' : 'hover:border-slate-700'"
    @click="$emit('click')"
  >
    <!-- Cabecera -->
    <div class="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
      <h3 class="truncate font-mono text-[13px] font-medium text-slate-100" :title="amb.id">{{ label }}</h3>
      <div class="flex shrink-0 items-center gap-1.5">
        <span
          v-if="amb.patientSeverity"
          :class="['rounded-sm px-1.5 py-px font-mono text-[10px] uppercase', `severity-${amb.patientSeverity}`]"
        >
          {{ severityLabel[amb.patientSeverity] ?? amb.patientSeverity }}
        </span>
        <span :class="['fsm-chip', `fsm-${amb.fsmState ?? 'idle'}`]">
          <span class="fsm-dot" />
          {{ amb.fsmState ?? "idle" }}
        </span>
      </div>
    </div>

    <!-- Fase + GPS -->
    <div class="flex items-center justify-between gap-2 px-3 pt-2 text-[10px] text-slate-500">
      <span class="truncate">Fase · <span class="text-slate-300">{{ amb.missionPhase ?? "—" }}</span></span>
      <span class="font-mono">{{ (amb.latitude ?? 0).toFixed(4) }}, {{ (amb.longitude ?? 0).toFixed(4) }}</span>
    </div>

    <!-- Telemetría -->
    <dl class="space-y-1.5 px-3 py-2.5">
      <div v-for="b in bars" :key="b.key" class="grid grid-cols-[5.5rem_1fr_4.5rem] items-center gap-2">
        <dt class="truncate text-[10px] uppercase tracking-wider text-slate-500">{{ b.label }}</dt>
        <div class="h-1 bg-slate-800" role="presentation">
          <div
            class="h-full"
            :class="b.level === 'crit' ? 'bg-red-400' : b.level === 'warn' ? 'bg-amber-400' : 'bg-slate-300'"
            :style="{ width: `${Math.max(0, Math.min(100, b.pct))}%` }"
          />
        </div>
        <dd
          class="text-right font-mono text-xs"
          :class="b.level === 'crit' ? 'text-red-300' : b.level === 'warn' ? 'text-amber-300' : 'text-slate-100'"
        >
          {{ b.value.toFixed(b.unit === "%" ? 0 : 1) }}<span class="ml-0.5 text-[10px] text-slate-500">{{ b.unit }}</span>
        </dd>
      </div>
    </dl>

    <!-- Médico / GPS -->
    <div class="grid grid-cols-2 border-t border-slate-800 text-xs">
      <div class="border-r border-slate-800 px-3 py-2">
        <p class="text-[10px] uppercase tracking-wider text-slate-500">Médico</p>
        <p v-if="amb.hasPatient && amb.telemetry?.medical" class="mt-0.5 font-mono text-slate-200">
          {{ amb.telemetry.medical.heartRateBpm }}<span class="text-[10px] text-slate-500"> bpm</span>
          ·
          <span :class="lowSpo2 ? 'text-red-300' : ''">{{ amb.telemetry.medical.spo2Pct }}%</span>
          <span class="text-[10px] text-slate-500"> SpO₂</span>
        </p>
        <p v-else class="mt-0.5 text-[11px] text-slate-500">Sin paciente</p>
      </div>
      <div class="px-3 py-2">
        <p class="text-[10px] uppercase tracking-wider text-slate-500">Rumbo · accel.</p>
        <p class="mt-0.5 font-mono text-slate-200">
          {{ amb.telemetry?.positioning?.headingDeg ?? "—" }}°
          · {{ amb.telemetry?.positioning?.accelerationMs2 ?? "—" }}<span class="text-[10px] text-slate-500"> m/s²</span>
        </p>
      </div>
    </div>
  </div>
</template>
