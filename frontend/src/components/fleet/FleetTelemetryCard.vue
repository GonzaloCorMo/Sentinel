<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useI18n } from "vue-i18n";
import type { Ambulance } from "@/types/simulation";
import { useSimulationStore } from "@/stores/simulation";
import { displayId } from "@/lib/vehicleId";
import { energyOf, powertrainOf } from "@/lib/energyDisplay";
import StatusChip from "@/components/ui/StatusChip.vue";

const props = defineProps<{ amb: Ambulance; selected?: boolean; index?: number }>();

defineEmits<{ click: [] }>();

const { t } = useI18n();
const store = useSimulationStore();
const { state } = storeToRefs(store);

const SPEED_SCALE_KMH = 120;
/** Batería auxiliar de 12 V: escala de la barra y umbrales de aviso. */
const AUX_BATTERY_MIN_V = 11;
const AUX_BATTERY_MAX_V = 14.8;
const AUX_BATTERY_WARN_V = 12.2;
const AUX_BATTERY_CRIT_V = 11.8;

const severityLabel = computed<Record<string, string>>(() => ({
  stable: t("operations.severity_stable"),
  moderate: t("operations.severity_moderate"),
  critical: t("operations.severity_critical"),
}));

const label = computed(() => displayId(props.amb, props.index, state.value?.entityTypes));
const typeName = computed(
  () => state.value?.entityTypes?.find((e) => e.id === (props.amb.entityTypeId || "ambulance"))?.name ?? "",
);

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
    // En combustión no hay batería de tracción (batteryPct = 0): se muestra la
    // tensión de la batería auxiliar de 12 V, que es la que puede fallar.
    const volts = props.amb.telemetry?.mechanical?.secondaryBatteryVoltageV;
    if (volts != null) {
      const vLevel = volts < AUX_BATTERY_CRIT_V ? "crit" : volts < AUX_BATTERY_WARN_V ? "warn" : "ok";
      rows.push({
        key: "battery",
        label: t("operations.aux_battery"),
        value: volts,
        unit: "V",
        pct: ((volts - AUX_BATTERY_MIN_V) / (AUX_BATTERY_MAX_V - AUX_BATTERY_MIN_V)) * 100,
        level: vLevel,
      });
    }
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
          :class="['rounded-sm px-1.5 py-px text-[11px]', `severity-${amb.patientSeverity}`]"
        >
          {{ severityLabel[amb.patientSeverity] ?? amb.patientSeverity }}
        </span>
        <StatusChip :unit="amb" />
      </div>
    </div>

    <!-- Ubicación + GPS -->
    <div class="flex items-center justify-between gap-2 px-3 pt-2 text-[10px] text-slate-500">
      <span class="truncate">{{ typeName }}</span>
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
          {{ b.value.toFixed(b.unit === "%" ? 0 : b.unit === "V" ? 2 : 1) }}<span class="ml-0.5 text-[10px] text-slate-500">{{ b.unit }}</span>
        </dd>
      </div>
    </dl>

    <!-- Paciente / rumbo -->
    <div class="grid grid-cols-2 border-t border-slate-800 text-xs">
      <div class="border-r border-slate-800 px-3 py-2">
        <p class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('vehicle.patient') }}</p>
        <p v-if="amb.telemetry?.medical" class="mt-0.5 font-mono text-slate-200">
          {{ Math.round(amb.telemetry.medical.heartRateBpm) }}<span class="text-[10px] text-slate-500"> lpm</span>
          ·
          <span :class="lowSpo2 ? 'text-red-300' : ''">{{ amb.telemetry.medical.spo2Pct }}%</span>
          <span class="text-[10px] text-slate-500"> SpO₂</span>
        </p>
        <p v-else class="mt-0.5 text-[11px] text-slate-500">{{ t('operations.no_patient') }}</p>
      </div>
      <div class="px-3 py-2">
        <p class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('fleet.heading') }}</p>
        <p class="mt-0.5 font-mono text-slate-200">
          {{ amb.telemetry?.positioning?.headingDeg ?? "—" }}°
          · {{ amb.telemetry?.positioning?.accelerationMs2 ?? "—" }}<span class="text-[10px] text-slate-500"> m/s²</span>
        </p>
      </div>
    </div>
  </div>
</template>
