<script setup lang="ts">
import { onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";

const { t, locale } = useI18n();

interface Highlight {
  title: string;
  detail: string;
  severity: "info" | "warning" | "critical";
}
interface ShiftReport {
  id: string | null;
  started_at: string;
  ended_at: string;
  window_minutes: number;
  kpis: Record<string, number>;
  summary: string;
  highlights: Highlight[];
  recommendations: string[];
  generated_by: string;
  created_at?: string;
}

const reports = ref<ShiftReport[]>([]);
const loading = ref(false);
const generating = ref(false);
const windowMinutes = ref(180);
const selected = ref<ShiftReport | null>(null);

async function fetchReports() {
  loading.value = true;
  try {
    const r = await fetch("/api/ai/shift-reports?limit=15");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    reports.value = await r.json();
    if (!selected.value && reports.value.length) selected.value = reports.value[0];
  } catch (e) {
    toast.error(`${t("reports.list_error")} ${(e as Error).message}`);
  } finally {
    loading.value = false;
  }
}

async function generate() {
  generating.value = true;
  try {
    const r = await fetch("/api/ai/shift-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ windowMinutes: windowMinutes.value }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const newReport = await r.json();
    reports.value.unshift(newReport);
    selected.value = newReport;
    toast.success(t("reports.generated_ok"));
  } catch (e) {
    toast.error(`${t("reports.generate_error")} ${(e as Error).message}`);
  } finally {
    generating.value = false;
  }
}

function fmtDate(iso?: string): string {
  if (!iso) return "—";
  try {
    const localeMap: Record<string, string> = { es: "es-ES", gl: "gl-ES", en: "en-US" };
    return new Date(iso).toLocaleString(localeMap[locale.value] || "es-ES", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

/** Métricas que se muestran (las técnicas, como registros de telemetría, se omiten). */
const KPI_KEYS = [
  "fleetTotal",
  "fleetWithPatient",
  "emergenciesActive",
  "emergenciesResolvedInWindow",
  "fleetLowFuel",
  "aiProposalsTotal",
  "aiProposalsApproved",
  "aiProposalsRejected",
] as const;

function severityClass(s: string): string {
  if (s === "critical") return "border-rose-500/50 bg-rose-950/25 text-rose-200";
  if (s === "warning") return "border-amber-500/50 bg-amber-950/25 text-amber-200";
  return "border-sky-500/40 bg-sky-950/20 text-sky-200";
}
function severityDot(s: string): string {
  if (s === "critical") return "bg-rose-400";
  if (s === "warning") return "bg-amber-400";
  return "bg-sky-400";
}

onMounted(fetchReports);
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="text-lg font-semibold tracking-tight text-slate-100">{{ t('reports.title') }}</h1>
        <p class="mt-0.5 text-sm text-slate-500">
          {{ t('reports.subtitle') }}
        </p>
      </div>
      <div class="flex items-end gap-2">
        <div>
          <label class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            {{ t('reports.window_min') }}
          </label>
          <input
            v-model.number="windowMinutes"
            type="number"
            min="5"
            max="1440"
            class="w-28 rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 outline-none focus:border-emerald-500/50"
          />
        </div>
        <button
          class="rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-950 shadow-sm transition hover:bg-slate-300 disabled:opacity-50"
          :disabled="generating"
          @click="generate"
        >
          {{ generating ? t('reports.generating') : t('reports.generate') }}
        </button>
      </div>
    </div>

    <div class="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
      <!-- Lista -->
      <aside class="rounded-xl border border-slate-800/60 bg-slate-950/40 p-2">
        <div class="mb-2 flex items-center justify-between px-2 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          {{ t('reports.history') }}
          <button class="text-slate-500 hover:text-slate-200" :title="t('common.refresh')" :aria-label="t('common.refresh')" @click="fetchReports" :disabled="loading">
            ↻
          </button>
        </div>
        <p v-if="!reports.length && !loading" class="py-8 text-center text-xs text-slate-600">
          {{ t('reports.empty') }}
        </p>
        <ul class="max-h-[70vh] space-y-1 overflow-y-auto pr-1">
          <li v-for="r in reports" :key="r.id || r.created_at">
            <button
              type="button"
              class="w-full rounded-lg border px-3 py-2 text-left text-xs transition"
              :class="selected?.id === r.id
                ? 'border-emerald-500/60 bg-emerald-950/30 text-emerald-100'
                : 'border-slate-800/70 text-slate-300 hover:bg-slate-800/50'"
              @click="selected = r"
            >
              <div class="flex items-center justify-between">
                <span class="font-mono font-semibold">{{ fmtDate(r.created_at) }}</span>
                <span class="text-[10px] text-slate-500">{{ r.window_minutes }} min</span>
              </div>
              <div class="mt-1 truncate text-[11px] text-slate-400">
                {{ r.summary }}
              </div>
            </button>
          </li>
        </ul>
      </aside>

      <!-- Detalle -->
      <section v-if="selected" class="space-y-4">
        <!-- KPIs -->
        <dl class="grid grid-cols-2 gap-px overflow-hidden rounded border border-slate-800 bg-slate-800 md:grid-cols-4">
          <div v-for="k in KPI_KEYS" :key="k" class="bg-slate-900 px-3 py-2">
            <dt class="text-[11px] text-slate-500">{{ t(`reports.kpi.${k}`) }}</dt>
            <dd class="font-mono text-lg text-slate-100">{{ selected.kpis?.[k] ?? '—' }}</dd>
          </div>
        </dl>

        <!-- Summary -->
        <div class="rounded border border-slate-800 bg-slate-900 p-4">
          <h3 class="mb-2 text-xs font-medium text-slate-400">{{ t('reports.summary_title') }}</h3>
          <p class="text-sm leading-relaxed text-slate-200">{{ selected.summary }}</p>
          <p class="mt-2 text-[10px] text-slate-500">
            {{ fmtDate(selected.started_at) }} → {{ fmtDate(selected.ended_at) }}
          </p>
        </div>

        <!-- Highlights -->
        <div>
          <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">{{ t('reports.highlights') }}</h3>
          <div class="space-y-2">
            <div
              v-for="(h, i) in selected.highlights"
              :key="i"
              class="rounded-lg border px-3 py-2.5"
              :class="severityClass(h.severity)"
            >
              <div class="flex items-start gap-2">
                <span class="mt-1.5 h-2 w-2 flex-shrink-0 rounded-full" :class="severityDot(h.severity)" />
                <div class="flex-1">
                  <p class="text-sm font-semibold">{{ h.title }}</p>
                  <p class="mt-0.5 text-[12px] opacity-90">{{ h.detail }}</p>
                </div>
                <span class="text-[11px] opacity-80">{{ t(`reports.severity.${h.severity}`) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Recommendations -->
        <div>
          <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">{{ t('reports.recommendations') }}</h3>
          <ul class="space-y-1.5 rounded-lg border border-slate-800/60 bg-slate-950/40 p-3">
            <li v-for="(r, i) in selected.recommendations" :key="i" class="flex items-start gap-2 text-sm text-slate-300">
              <span class="mt-1.5 h-1 w-1 flex-shrink-0 rounded-full bg-emerald-500" />
              <span>{{ r }}</span>
            </li>
          </ul>
        </div>
      </section>

      <section v-else class="rounded-xl border border-dashed border-slate-700 p-8 text-center text-sm text-slate-500">
        {{ t('reports.select_or_generate') }}
      </section>
    </div>
  </div>
</template>
