<script setup lang="ts">
/**
 * Informe de transferencia (ISBAR) del paciente de una unidad, generado con la
 * IA local a partir de datos simulados. Las cifras objetivas se muestran aparte,
 * calculadas por el backend, para no depender de la redacción del modelo.
 */
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";

interface Abnormal { label: string; value: number; unit: string; level: string; trend: string | null }
export interface PatientReportData {
  unit: string;
  condition: string;
  severity?: string | null;
  generatedAt: string;
  disclaimer: string;
  source: "llm" | "rules";
  summary: string;
  isbar: Record<"identification" | "situation" | "background" | "assessment" | "recommendation", string>;
  findings: string[];
  alerts: string[];
  actions: string[];
  facts: { abnormal: Abnormal[]; samples: number; ecg?: string | null; news2?: number | null };
}

const props = defineProps<{ report: PatientReportData; busy: boolean }>();
const emit = defineEmits<{ regenerate: []; close: [] }>();
const { t } = useI18n();

const ISBAR = [
  ["I", "identification"],
  ["S", "situation"],
  ["B", "background"],
  ["A", "assessment"],
  ["R", "recommendation"],
] as const;

const time = computed(() => new Date(props.report.generatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
const copied = ref(false);

function fmtValue(a: Abnormal): string {
  const v = Math.abs(a.value - Math.round(a.value)) > 0.05 ? a.value.toFixed(1) : String(Math.round(a.value));
  return `${v} ${a.unit}`;
}

function plainText(): string {
  const r = props.report;
  const lines = [
    `${t("patient.report.title")} · ${r.unit} · ${time.value}`,
    `${r.condition}`,
    "",
    ...(r.alerts.length ? [`${t("patient.report.alerts")}:`, ...r.alerts.map((x) => `- ${x}`), ""] : []),
    r.summary,
    "",
    ...ISBAR.map(([l, k]) => `${l} · ${t(`patient.report.isbar_${k}`)}: ${r.isbar[k]}`),
    "",
    `${t("patient.report.findings")}:`,
    ...r.findings.map((x) => `- ${x}`),
    "",
    `${t("patient.report.actions")}:`,
    ...r.actions.map((x) => `- ${x}`),
    "",
    r.disclaimer,
  ];
  return lines.join("\n");
}

async function copy() {
  try {
    await navigator.clipboard.writeText(plainText());
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    toast.error(t("patient.report.copy_error"));
  }
}
</script>

<template>
  <section class="border-t border-slate-800">
    <header class="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
      <div class="flex flex-wrap items-baseline gap-2">
        <h3 class="text-sm font-medium text-slate-100">{{ t('patient.report.title') }}</h3>
        <span class="font-mono text-xs text-slate-400">[{{ report.unit }}] · {{ time }}</span>
        <span class="border border-slate-700 px-1.5 py-px text-[10px] text-slate-400">
          {{ report.source === 'llm' ? t('patient.report.source_llm') : t('patient.report.source_rules') }}
        </span>
      </div>
      <div class="flex gap-1.5">
        <button type="button" class="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-800" @click="copy">
          {{ copied ? t('patient.report.copied') : t('patient.report.copy') }}
        </button>
        <button type="button" class="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-40" :disabled="busy" @click="emit('regenerate')">
          {{ busy ? t('patient.report.generating_short') : t('patient.report.regenerate') }}
        </button>
        <button type="button" class="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-400 hover:bg-slate-800" :aria-label="t('patient.report.close')" :title="t('patient.report.close')" @click="emit('close')">×</button>
      </div>
    </header>

    <div class="space-y-4 px-4 pb-4 text-sm">
      <ul v-if="report.alerts.length" class="space-y-1 border border-red-500/50 px-3 py-2">
        <li v-for="(a, i) in report.alerts" :key="i" class="flex gap-2 text-red-300">
          <span class="font-mono text-[11px] text-red-400">!</span><span>{{ a }}</span>
        </li>
      </ul>

      <p class="leading-relaxed text-slate-200">{{ report.summary }}</p>

      <dl class="divide-y divide-slate-800 border-y border-slate-800">
        <div v-for="[letter, key] in ISBAR" :key="key" class="grid grid-cols-[2rem_9rem_minmax(0,1fr)] gap-2 py-2 max-sm:grid-cols-[2rem_minmax(0,1fr)]">
          <dt class="font-mono text-base font-semibold text-slate-100">{{ letter }}</dt>
          <dt class="text-xs text-slate-500 max-sm:hidden">{{ t(`patient.report.isbar_${key}`) }}</dt>
          <dd class="text-slate-300">{{ report.isbar[key] || '—' }}</dd>
        </div>
      </dl>

      <div class="grid gap-4 md:grid-cols-3">
        <div>
          <h4 class="mb-1.5 text-[11px] uppercase tracking-wider text-slate-500">{{ t('patient.report.objective') }}</h4>
          <ul class="space-y-1 font-mono text-xs tabular-nums">
            <li v-for="a in report.facts.abnormal" :key="a.label" class="flex justify-between gap-2">
              <span class="truncate font-sans text-slate-400">{{ a.label }}</span>
              <span :class="a.level === 'crítico' ? 'text-red-400' : a.level === 'alterado' ? 'text-amber-300' : 'text-slate-300'">
                {{ fmtValue(a) }}<template v-if="a.trend"> · {{ a.trend }}</template>
              </span>
            </li>
            <li v-if="!report.facts.abnormal.length" class="font-sans text-slate-500">{{ t('patient.report.no_abnormal') }}</li>
          </ul>
        </div>
        <div>
          <h4 class="mb-1.5 text-[11px] uppercase tracking-wider text-slate-500">{{ t('patient.report.findings') }}</h4>
          <ul class="list-disc space-y-1 pl-4 text-slate-300">
            <li v-for="(f, i) in report.findings" :key="i">{{ f }}</li>
          </ul>
        </div>
        <div>
          <h4 class="mb-1.5 text-[11px] uppercase tracking-wider text-slate-500">{{ t('patient.report.actions') }}</h4>
          <ul class="list-disc space-y-1 pl-4 text-slate-300">
            <li v-for="(a, i) in report.actions" :key="i">{{ a }}</li>
            <li v-if="!report.actions.length" class="list-none text-slate-500">—</li>
          </ul>
        </div>
      </div>

      <p class="text-[11px] text-slate-500">{{ report.disclaimer }}</p>
    </div>
  </section>
</template>
