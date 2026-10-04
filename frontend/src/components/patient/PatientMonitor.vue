<script setup lang="ts">
/**
 * Monitor del paciente de una unidad: visor 3D con la zona afectada y panel
 * de constantes. Usa las constantes simuladas por el motor (ficticias) y la
 * afección del catálogo de emergencias. Si la unidad no atiende a ningún
 * paciente, ofrece una vista de ejemplo con datos generados en el navegador.
 */
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { mockVitals } from "@/lib/patientMock";
import { alertTone, CONDITION_KEYS, zonesFor } from "@/lib/patientZones";
import { unitStatus } from "@/lib/unitStatus";
import type { Ambulance, Emergency, MedicalTelemetry } from "@/types/simulation";

// three.js solo se descarga al abrir el monitor.
const PatientViewer3D = defineAsyncComponent(() => import("./PatientViewer3D.vue"));

const props = defineProps<{
  amb: Ambulance | null;
  label: string;
  emergencies: Emergency[];
}>();

const { t, te } = useI18n();

const live = computed(() => {
  const a = props.amb;
  if (!a) return false;
  return !!a.patientKindKey && (a.hasPatient || a.missionPhase === "on_scene");
});

// ── Vista de ejemplo ─────────────────────────────────────────────────────
const demoCondition = ref("chest_pain");
const demoCritical = ref(false);
const now = ref(0);
let timer = 0;
onMounted(() => {
  const t0 = performance.now();
  timer = window.setInterval(() => (now.value = (performance.now() - t0) / 1000), 1000);
});
onBeforeUnmount(() => window.clearInterval(timer));

const emergency = computed(() => props.emergencies.find((e) => e.id === props.amb?.patientEmergencyId) ?? null);
const conditionKey = computed(() => (live.value ? props.amb?.patientKindKey ?? null : demoCondition.value));
const severity = computed(() => (live.value ? props.amb?.patientSeverity ?? "moderate" : demoCritical.value ? "critical" : "moderate"));
const tone = computed(() => alertTone(severity.value));
const zones = computed(() => zonesFor(conditionKey.value, emergency.value?.emergencyType));

const vitals = computed<MedicalTelemetry | null>(() => {
  if (live.value) return props.amb?.telemetry?.medical ?? null;
  void now.value; // recalcula cada segundo
  return mockVitals(demoCondition.value, now.value);
});

function conditionName(key: string | null): string {
  if (!key) return "—";
  return te(`patient.condition.${key}`) ? t(`patient.condition.${key}`) : key;
}

const labels = computed(() => {
  const out: Record<string, { zone: string; detail: string }> = {};
  zones.value.forEach((z, i) => {
    out[z.id] = { zone: t(`patient.zone.${z.id}`).toUpperCase(), detail: i === 0 ? conditionName(conditionKey.value) : "" };
  });
  return out;
});

// ── Constantes ───────────────────────────────────────────────────────────
type Level = "ok" | "warn" | "crit";
interface Row { key: string; label: string; value: string; unit: string; level: Level }

function level(v: number | undefined, crit: [number, number], warn: [number, number]): Level {
  if (v == null || Number.isNaN(v)) return "ok";
  if (v < crit[0] || v > crit[1]) return "crit";
  if (v < warn[0] || v > warn[1]) return "warn";
  return "ok";
}

const rows = computed<Row[]>(() => {
  const v = vitals.value;
  const fmt = (n: number | undefined, d = 0) => (n == null ? "—" : n.toFixed(d));
  const sys = v?.bloodPressureMmhg?.systolic;
  const dia = v?.bloodPressureMmhg?.diastolic;
  const ecg = v?.ecgRhythm ?? "";
  return [
    { key: "hr", label: t("patient.hr"), value: fmt(v?.heartRateBpm), unit: "lpm", level: level(v?.heartRateBpm, [50, 120], [60, 100]) },
    { key: "spo2", label: t("patient.spo2"), value: fmt(v?.spo2Pct), unit: "%", level: level(v?.spo2Pct, [90, 101], [94, 101]) },
    { key: "bp", label: t("patient.bp"), value: sys == null ? "—" : `${sys}/${dia}`, unit: "mmHg", level: level(sys, [90, 180], [100, 160]) },
    { key: "rr", label: t("patient.rr"), value: fmt(v?.respiratoryRatePerMin), unit: "rpm", level: level(v?.respiratoryRatePerMin, [8, 25], [12, 20]) },
    { key: "temp", label: t("patient.temp"), value: fmt(v?.bodyTempC, 1), unit: "°C", level: level(v?.bodyTempC, [35.5, 39], [36, 38]) },
    { key: "gcs", label: t("patient.gcs"), value: fmt(v?.gcsScore), unit: "/15", level: level(v?.gcsScore, [9, 15], [15, 15]) },
    { key: "glucose", label: t("patient.glucose"), value: fmt(v?.bloodGlucoseMgDl), unit: "mg/dL", level: level(v?.bloodGlucoseMgDl, [60, 250], [70, 180]) },
    {
      key: "ecg",
      label: t("patient.ecg"),
      value: ecg ? (te(`patient.ecg_rhythm.${ecg}`) ? t(`patient.ecg_rhythm.${ecg}`) : ecg) : "—",
      unit: "",
      level: ecg === "Fibrilacion" ? "crit" : ecg === "Taquicardia" ? "warn" : "ok",
    },
  ];
});

const levelClass: Record<Level, string> = { ok: "text-slate-100", warn: "text-amber-300", crit: "text-red-400" };
</script>

<template>
  <section class="panel">
    <header class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
      <div>
        <h2 class="text-sm font-medium text-slate-100">{{ t('patient.title') }}</h2>
        <p class="text-xs text-slate-500">{{ live ? t('patient.subtitle') : t('patient.demo_note') }}</p>
      </div>
      <div v-if="live && amb" class="flex items-center gap-2 text-xs">
        <span class="font-mono text-slate-200">[{{ label }}]</span>
        <span class="text-slate-400">{{ t(`status.${unitStatus(amb).key}`) }}</span>
        <span
          class="border px-1.5 py-px font-mono text-[11px]"
          :class="tone === 'crit' ? 'border-red-500/60 text-red-300' : 'border-amber-500/60 text-amber-300'"
        >{{ conditionName(conditionKey) }}</span>
      </div>
      <div v-else class="flex flex-wrap items-center gap-2 text-xs">
        <label class="text-slate-500" for="pv-demo-condition">{{ t('patient.demo_condition') }}</label>
        <select id="pv-demo-condition" v-model="demoCondition" class="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200">
          <option v-for="k in CONDITION_KEYS" :key="k" :value="k">{{ conditionName(k) }}</option>
        </select>
        <div class="flex overflow-hidden rounded border border-slate-700" role="group" :aria-label="t('patient.demo_severity')">
          <button
            type="button"
            class="px-2 py-1"
            :class="!demoCritical ? 'bg-slate-800 text-amber-300' : 'text-slate-400 hover:bg-slate-800'"
            :aria-pressed="!demoCritical"
            @click="demoCritical = false"
          >{{ t('patient.sev_warning') }}</button>
          <button
            type="button"
            class="border-l border-slate-700 px-2 py-1"
            :class="demoCritical ? 'bg-slate-800 text-red-400' : 'text-slate-400 hover:bg-slate-800'"
            :aria-pressed="demoCritical"
            @click="demoCritical = true"
          >{{ t('patient.sev_critical') }}</button>
        </div>
      </div>
    </header>

    <div class="grid lg:grid-cols-[minmax(0,1fr)_17rem]">
      <div class="relative h-[440px] border-b border-slate-800 lg:border-b-0 lg:border-r">
        <PatientViewer3D
          :zones="zones"
          :tone="tone"
          :labels="labels"
          :description="t('patient.aria', { condition: conditionName(conditionKey) })"
        />
      </div>
      <dl class="divide-y divide-slate-800">
        <div v-for="r in rows" :key="r.key" class="flex items-baseline justify-between gap-2 px-4 py-2">
          <dt class="min-w-0 truncate text-xs text-slate-500" :title="r.label">{{ r.label }}</dt>
          <dd class="flex shrink-0 items-baseline gap-1">
            <span class="min-w-[6ch] text-right font-mono text-base tabular-nums" :class="levelClass[r.level]">{{ r.value }}</span>
            <span class="w-[5ch] font-mono text-[11px] text-slate-500">{{ r.unit }}</span>
          </dd>
        </div>
        <p v-if="!live" class="px-4 py-2.5 text-[11px] text-slate-500">{{ t('patient.demo_disclaimer') }}</p>
      </dl>
    </div>
  </section>
</template>
