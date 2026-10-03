<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useSimulationStore } from "@/stores/simulation";
import type { AIProposal } from "@/types/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const { state } = storeToRefs(store);

const expanded = ref(false);
const showLog = ref(false);

// Detecta staleness en cliente comparando con state actual (más rápido que el
// sweep de backend que corre cada 5s).
function detectStaleReason(p: AIProposal): string | null {
  if (p.staleReason) return p.staleReason;
  const s = state.value;
  if (!s) return null;
  const detail = (p.anomalyDetail || {}) as Record<string, unknown>;
  const eid = (detail.emergencyId as string) || "";
  const atype = p.anomalyType;
  const emergencyTypes = [
    "dispatch_request", "critical_response_needed", "eta_exceeded",
    "smart_dispatch", "unattended_emergency",
  ];
  if (emergencyTypes.includes(atype) && eid) {
    const em = s.emergencies?.find((e) => e.id === eid);
    if (!em) return t("ai.stale_em_gone");
    if (em.status === "resolved") return t("ai.stale_em_resolved");
    if (String(em.status) === "cancelled") return t("ai.stale_em_cancelled");
    if (["unattended_emergency", "dispatch_request"].includes(atype)) {
      const assigned = s.ambulances?.find((a) => a.assignedEmergencyId === eid);
      if (assigned) return t("ai.stale_em_assigned");
    }
  }
  if (atype === "fuel_critical" && p.ambulanceId) {
    const amb = s.ambulances?.find((a) => a.id === p.ambulanceId);
    if (!amb) return t("ai.stale_amb_gone");
    if ((amb.fuelLevel ?? 0) > 20) return t("ai.stale_fuel_ok");
  }
  if (atype === "vitals_critical" && p.ambulanceId) {
    const amb = s.ambulances?.find((a) => a.id === p.ambulanceId);
    if (!amb) return t("ai.stale_amb_gone");
    if (!amb.hasPatient) return t("ai.stale_no_patient");
    if (amb.missionPhase === "to_hospital") return t("ai.stale_to_hospital");
  }
  return null;
}

const proposals = computed<(AIProposal & { clientStaleReason: string | null })[]>(() =>
  (state.value?.aiProposals ?? [])
    .filter((p) => p.status === "pending")
    .map((p) => ({ ...p, clientStaleReason: detectStaleReason(p) })),
);

const dispatchCountByEmergency = computed<Record<string, number>>(() => {
  const map: Record<string, number> = {};
  for (const p of proposals.value) {
    const eid = (p.anomalyDetail as Record<string, unknown>)?.emergencyId as string | undefined;
    const kind = (p.anomalyDetail as Record<string, unknown>)?.dispatchKind as string | undefined;
    if (eid && kind) map[eid] = (map[eid] ?? 0) + 1;
  }
  return map;
});

function dispatchKindLabel(kind: string): string {
  return kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function dispatchInfo(p: AIProposal): { count: number; kind: string } | null {
  const d = (p.anomalyDetail || {}) as Record<string, unknown>;
  const eid = d.emergencyId as string | undefined;
  const kind = d.dispatchKind as string | undefined;
  if (!eid || !kind) return null;
  return { count: dispatchCountByEmergency.value[eid] ?? 1, kind: dispatchKindLabel(kind) };
}

const aiLog = computed<AIProposal[]>(() => state.value?.aiLog ?? []);

const aiMode = computed(() => state.value?.aiMode ?? "hitl");

const hasContent = computed(() => proposals.value.length > 0 || aiLog.value.length > 0);

const anomalyLabel = computed<Record<string, string>>(() => ({
  fuel_critical: t("ai.anomaly_fuel"),
  vitals_critical: t("ai.anomaly_vitals"),
  unattended_emergency: t("ai.anomaly_unattended"),
  redistribution: t("ai.anomaly_redistribution"),
  dispatch_request: t("ai.anomaly_dispatch"),
  smart_dispatch: t("ai.anomaly_dispatch"),
  critical_response_needed: t("ai.anomaly_critical_response"),
  eta_exceeded: t("ai.anomaly_eta_exceeded"),
}));

const anomalyUrgency: Record<string, string> = {
  fuel_critical: "warning",
  vitals_critical: "critical",
  unattended_emergency: "critical",
  redistribution: "info",
  dispatch_request: "warning",
  smart_dispatch: "warning",
  critical_response_needed: "critical",
  eta_exceeded: "warning",
};

function humanizeType(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function briefSummary(p: AIProposal): string {
  const d = (p.anomalyDetail || {}) as Record<string, unknown>;
  if (p.anomalyType === "fuel_critical") {
    const pct = (d.fuelLevel as number | undefined)?.toFixed(1) ?? "?";
    return t("ai.fuel_at", { pct });
  }
  if (p.anomalyType === "vitals_critical") {
    return `SpO2 ${d.spo2 ?? "?"}% · BPM ${d.bpm ?? "?"} · GCS ${d.gcs ?? "?"}`;
  }
  if (p.anomalyType === "unattended_emergency") {
    const sec = (d.ageSeconds as number | undefined)?.toFixed(0) ?? "?";
    return t("ai.unattended_for", { sec });
  }
  const desc = d.description as string | undefined;
  const eta = d.etaSeconds as number | undefined;
  if (desc || eta != null) {
    const short = desc ? (desc.length > 80 ? desc.slice(0, 80) + "…" : desc) : "";
    const etaStr = eta != null ? ` · ETA ${Math.round(eta)}s` : "";
    return short + etaStr;
  }
  return p.llmExplanation?.summary || "";
}

async function resolve(id: string, action: "approved" | "rejected") {
  try {
    await store.resolveProposal(id, action);
    toast.success(action === "approved" ? t("ai.approved_toast") : t("ai.rejected_toast"));
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t("ai.resolve_err"));
  }
}

async function toggleMode() {
  const next = aiMode.value === "hitl" ? "autonomous" : "hitl";
  try {
    await store.setAiMode(next);
    toast.info(next === "autonomous" ? t("ai.switch_auto_toast") : t("ai.switch_hitl_toast"));
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t("ai.switch_err"));
  }
}

function ambLabel(id: string | undefined): string {
  if (!id || id === "system") return t("ai.system");
  return id.slice(0, 8) + "...";
}

// ── Explicabilidad: estado expand/collapse por propuesta ───────────────
const detailsOpen = ref<Record<string, boolean>>({});
function toggleDetails(id: string) {
  detailsOpen.value[id] = !detailsOpen.value[id];
}

const confidenceLabel = computed<Record<string, string>>(() => ({
  low: t("ai.conf_low"),
  medium: t("ai.conf_medium"),
  high: t("ai.conf_high"),
}));
const urgencyLabel = computed<Record<string, string>>(() => ({
  routine: t("ai.urg_routine"),
  urgent: t("ai.urg_urgent"),
  critical: t("ai.urg_critical"),
}));
function confidenceClass(c: string): string {
  if (c === "high") return "bg-green-500/20 text-green-300";
  if (c === "low") return "bg-amber-500/20 text-amber-300";
  return "bg-slate-700/40 text-slate-300";
}
function urgencyBadge(u: string): string {
  if (u === "critical") return "bg-rose-600/30 text-rose-200";
  if (u === "urgent") return "bg-amber-500/25 text-amber-200";
  return "bg-slate-700/40 text-slate-300";
}
function urgencyBorder(u: string | undefined): string {
  if (u === "critical") return "border-rose-500/60";
  if (u === "urgent") return "border-amber-500/50";
  return "border-emerald-500/40";
}
</script>

<template>
  <div v-if="hasContent || expanded" class="fixed bottom-4 right-4 z-[500] flex flex-col items-end gap-2">
    <!-- Collapsed button -->
    <button
      v-if="!expanded"
      type="button"
      class="group flex items-center gap-2.5 rounded-2xl border border-slate-700/60 bg-slate-900 px-4 py-2.5 text-sm font-medium shadow-2xl shadow-black/30 transition-colors hover:border-emerald-600/40 hover:bg-slate-800"
      @click="expanded = true"
    >
      <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-600/15 text-emerald-400">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      </div>
      <span class="text-slate-300">{{ t('ai.observer') }}</span>
      <span
        v-if="proposals.length"
        class="flex h-5 min-w-[1.25rem] items-center justify-center rounded-sm px-1.5 text-[10px] font-bold"
        :class="proposals.some(p => anomalyUrgency[p.anomalyType] === 'critical')
          ? 'bg-rose-500 text-white animate-pulse'
          : 'bg-amber-500 text-slate-950'"
      >
        {{ proposals.length }}
      </span>
      <span
        v-if="aiMode === 'autonomous'"
        class="rounded-md bg-cyan-600/30 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-cyan-300"
      >
        {{ t('ai.auto') }}
      </span>
    </button>

    <!-- Expanded panel -->
    <div
      v-else
      class="w-[24rem] max-h-[75vh] overflow-hidden rounded-2xl border border-slate-700/60 bg-slate-900 shadow-2xl shadow-black/30 transition-colors"
    >
      <!-- Header -->
      <div class="flex items-center justify-between border-b border-slate-800/80 bg-slate-950/60 px-4 py-3">
        <div class="flex items-center gap-2.5">
          <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-600/15 text-emerald-400">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </div>
          <h3 class="text-sm font-semibold text-slate-200">{{ t('ai.observer') }}</h3>
        </div>
        <div class="flex items-center gap-2">
          <!-- Mode toggle -->
          <button
            type="button"
            class="flex items-center gap-1.5 rounded-lg px-2 py-1 text-[10px] font-semibold uppercase transition"
            :class="aiMode === 'autonomous'
              ? 'bg-cyan-600/20 text-cyan-300 hover:bg-cyan-600/30'
              : 'bg-amber-600/20 text-amber-300 hover:bg-amber-600/30'"
            :title="aiMode === 'autonomous' ? t('ai.mode_auto_tooltip') : t('ai.mode_hitl_tooltip')"
            @click="toggleMode"
          >
            <span class="relative flex h-4 w-8 items-center rounded-full transition" :class="aiMode === 'autonomous' ? 'bg-cyan-600' : 'bg-slate-600'">
              <span class="absolute h-3 w-3 rounded-full bg-white shadow transition-transform" :class="aiMode === 'autonomous' ? 'translate-x-4' : 'translate-x-0.5'" />
            </span>
            {{ aiMode === 'autonomous' ? t('ai.auto') : t('ai.hitl_short') }}
          </button>
          <button
            type="button"
            class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-slate-300"
            @click="expanded = false"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Tab bar -->
      <div class="flex border-b border-slate-800/80">
        <button
          type="button"
          class="flex-1 px-3 py-2 text-xs font-medium transition"
          :class="!showLog ? 'text-amber-300 border-b-2 border-amber-400' : 'text-slate-500 hover:text-slate-300'"
          @click="showLog = false"
        >
          {{ t('ai.tab_pending', { n: proposals.length }) }}
        </button>
        <button
          type="button"
          class="flex-1 px-3 py-2 text-xs font-medium transition"
          :class="showLog ? 'text-cyan-300 border-b-2 border-cyan-400' : 'text-slate-500 hover:text-slate-300'"
          @click="showLog = true"
        >
          {{ t('ai.tab_log', { n: aiLog.length }) }}
        </button>
      </div>

      <!-- Scrollable content -->
      <div class="max-h-[calc(75vh-8rem)] overflow-y-auto">
        <!-- Pending proposals -->
        <div v-if="!showLog" class="flex flex-col gap-2.5 p-3">
          <div v-if="!proposals.length" class="py-6 text-center text-xs text-slate-500">
            {{ t('ai.no_proposals') }}
          </div>

          <div
            v-for="p in proposals"
            :key="p.id"
            class="panel overflow-hidden p-0"
          >
            <!-- Urgency indicator bar -->
            <div
              class="h-0.5"
              :class="anomalyUrgency[p.anomalyType] === 'critical' ? 'bg-rose-500' : anomalyUrgency[p.anomalyType] === 'warning' ? 'bg-amber-500' : 'bg-sky-500'"
            />
            <div class="p-3">
              <div class="mb-2 flex items-start gap-2">
                <!-- Urgency dot -->
                <div class="mt-0.5">
                  <span
                    class="inline-block h-2.5 w-2.5 rounded-full"
                    :class="anomalyUrgency[p.anomalyType] === 'critical' ? 'bg-rose-500 animate-pulse' : anomalyUrgency[p.anomalyType] === 'warning' ? 'bg-amber-500' : 'bg-sky-500'"
                  />
                </div>
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-1.5">
                    <p class="text-xs font-semibold text-slate-200">
                      {{ anomalyLabel[p.anomalyType] ?? humanizeType(p.anomalyType) }}
                    </p>
                    <span v-if="p.llmExplanation?.urgency"
                      class="rounded px-1 py-0.5 text-[8px] font-bold"
                      :class="urgencyBadge(p.llmExplanation.urgency)"
                    >{{ urgencyLabel[p.llmExplanation.urgency] }}</span>
                  </div>
                  <p v-if="p.ambulanceId" class="text-[10px] text-slate-500">
                    {{ p.anomalyType === 'unattended_emergency' ? t('ai.emergency') : t('ai.ambulance') }}: {{ ambLabel(p.ambulanceId) }}
                  </p>
                </div>
              </div>

              <!-- Dispatch info (units + kind) -->
              <p v-if="dispatchInfo(p)" class="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-emerald-300">
                <span class="rounded bg-emerald-600/20 px-1.5 py-0.5 text-[10px] font-bold tabular-nums">
                  {{ dispatchInfo(p)!.count }}
                </span>
                <span>{{ t('ai.units_dispatch', { n: dispatchInfo(p)!.count }, dispatchInfo(p)!.count) }} · {{ dispatchInfo(p)!.kind }}</span>
              </p>

              <!-- Brief summary (always visible) -->
              <p v-if="briefSummary(p)" class="mb-2 line-clamp-2 text-[11px] leading-snug text-slate-300">
                {{ briefSummary(p) }}
              </p>

              <!-- Detalles toggle -->
              <button
                v-if="p.llmExplanation || p.llmReasoning || p.matchedProtocolContent || p.anomalyDetail"
                type="button"
                class="mb-2 text-[10px] text-emerald-400/70 hover:text-emerald-300"
                @click="toggleDetails(p.id)"
              >
                {{ detailsOpen[p.id] ? '▾ ' + t('ai.details_close') : '▸ ' + t('ai.details_open') }}
              </button>

              <!-- Detalles expandido -->
              <div v-if="detailsOpen[p.id]" class="mb-3 space-y-2">
                <!-- LLM reasoning -->
                <div
                  v-if="p.llmExplanation || p.llmReasoning"
                  class="rounded-lg border-l-2 bg-emerald-950/20 px-3 py-2 text-[11px] leading-relaxed text-slate-300"
                  :class="urgencyBorder(p.llmExplanation?.urgency)"
                >
                  <p class="mb-1 text-[9px] font-semibold uppercase tracking-wider text-emerald-400/70">
                    {{ t('ai.why') }}
                    <span v-if="p.llmExplanation?.confidence"
                      class="ml-1.5 rounded px-1.5 py-0.5 text-[8px] font-bold"
                      :class="confidenceClass(p.llmExplanation.confidence)"
                    >{{ confidenceLabel[p.llmExplanation.confidence] }}</span>
                  </p>
                  <p class="italic">{{ p.llmExplanation?.summary || p.llmReasoning }}</p>

                  <div v-if="p.llmExplanation?.keyFactors?.length" class="mt-2">
                    <p class="mb-1 text-[9px] font-semibold uppercase tracking-wider text-slate-400">{{ t('ai.key_factors') }}</p>
                    <ul class="space-y-0.5">
                      <li
                        v-for="(f, i) in p.llmExplanation.keyFactors"
                        :key="i"
                        class="flex items-start gap-1.5 text-[10px] text-slate-300"
                      >
                        <span class="mt-1 h-1 w-1 flex-shrink-0 rounded-full bg-emerald-500/60" />
                        <span>{{ f }}</span>
                      </li>
                    </ul>
                  </div>
                  <div v-if="p.llmExplanation?.protocolReferences?.length" class="mt-2">
                    <p class="mb-1 text-[9px] font-semibold uppercase tracking-wider text-slate-400">{{ t('ai.protocol_cites') }}</p>
                    <ul class="space-y-1">
                      <li
                        v-for="(r, i) in p.llmExplanation.protocolReferences"
                        :key="i"
                        class="rounded border border-slate-700/40 bg-slate-950/40 px-2 py-1 text-[10px] italic text-slate-400"
                      >
                        «{{ r }}»
                      </li>
                    </ul>
                  </div>
                </div>

                <!-- Protocol chunk RAG -->
                <div
                  v-if="p.matchedProtocolContent"
                  class="rounded-lg border border-slate-700/50 bg-slate-950/60 px-2.5 py-2 text-[11px] leading-snug text-slate-400"
                >
                  <p class="mb-0.5 flex items-center gap-2 text-[9px] font-semibold uppercase tracking-wider text-slate-500">
                    {{ t('ai.protocol_chunk') }}
                    <span v-if="p.similarity != null" class="rounded bg-slate-800 px-1 py-0.5 font-mono text-[8px]">
                      {{ (p.similarity * 100).toFixed(0) }}% match
                    </span>
                  </p>
                  {{ p.matchedProtocolContent }}
                </div>
              </div>

              <!-- Stale banner: contexto cambió → aprobar deshabilitado -->
              <div
                v-if="p.clientStaleReason"
                class="mb-2 rounded-lg border border-slate-500/40 bg-slate-800/50 px-2.5 py-1.5 text-[11px] text-slate-300"
              >
                ⚠️ <span class="font-semibold">{{ t('ai.stale_label') }}</span> {{ p.clientStaleReason }}
              </div>

              <!-- Action buttons -->
              <div class="flex gap-2">
                <button
                  type="button"
                  class="flex-1 rounded-lg px-3 py-1.5 text-xs font-medium transition"
                  :class="p.clientStaleReason
                    ? 'bg-slate-800 text-slate-400 cursor-not-allowed opacity-60'
                    : 'bg-slate-100 text-slate-950 hover:bg-slate-300'"
                  :disabled="!!p.clientStaleReason"
                  :title="p.clientStaleReason ? t('ai.approved_disabled') : t('ai.approve')"
                  @click="!p.clientStaleReason && resolve(p.id, 'approved')"
                >
                  {{ p.clientStaleReason ? t('ai.obsolete') : t('ai.approve_btn') }}
                </button>
                <button
                  type="button"
                  class="flex-1 rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-800"
                  @click="resolve(p.id, 'rejected')"
                >
                  {{ p.clientStaleReason ? t('ai.discard') : t('ai.reject') }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- AI Log -->
        <div v-if="showLog" class="flex flex-col gap-1.5 p-3">
          <div v-if="!aiLog.length" class="py-6 text-center text-xs text-slate-500">
            {{ t('ai.log_empty') }}
          </div>

          <div
            v-for="p in [...aiLog].reverse()"
            :key="p.id"
            class="rounded-xl border px-3 py-2.5 text-[11px]"
            :class="
              p.status === 'auto_approved'
                ? 'border-cyan-700/40 bg-cyan-950/20 text-cyan-300'
                : p.status === 'approved'
                  ? 'border-green-700/40 bg-green-950/20 text-green-300'
                  : 'border-red-700/40 bg-red-950/20 text-red-300'
            "
          >
            <div class="flex items-center gap-2">
              <span
                class="h-1.5 w-1.5 rounded-full"
                :class="
                  p.status === 'auto_approved' ? 'bg-cyan-400'
                  : p.status === 'approved' ? 'bg-green-400'
                  : 'bg-red-400'
                "
              />
              <span class="font-medium">{{ anomalyLabel[p.anomalyType] ?? p.anomalyType }}</span>
              <span
                class="ml-auto rounded-md px-1.5 py-0.5 text-[9px] font-semibold uppercase"
                :class="
                  p.status === 'auto_approved'
                    ? 'bg-cyan-600/30 text-cyan-200'
                    : p.status === 'approved'
                      ? 'bg-green-600/30 text-green-200'
                      : 'bg-red-600/30 text-red-200'
                "
              >
                {{ p.status === 'auto_approved' ? t('ai.auto') : p.status }}
              </span>
            </div>
            <p v-if="p.llmReasoning" class="mt-1.5 text-[10px] italic leading-snug opacity-80">
              {{ p.llmReasoning.slice(0, 140) }}{{ p.llmReasoning.length > 140 ? '...' : '' }}
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
