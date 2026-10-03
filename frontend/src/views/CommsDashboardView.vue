<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { unitStatus } from "@/lib/unitStatus";
import { displayId } from "@/lib/vehicleId";
import { useSimulationStore } from "@/stores/simulation";
import type { Ambulance, UnitMessage } from "@/types/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const { state, commsLog } = storeToRefs(store);

// Reloj de la vista para «hace N s» (no depende de que llegue un tick).
const now = ref(Date.now());
let clock = 0;
onMounted(() => {
  clock = window.setInterval(() => (now.value = Date.now()), 1000);
});
onBeforeUnmount(() => window.clearInterval(clock));

const networkStatus = computed(() => state.value?.networkStatus ?? { mqtt: true, p2p: true, http: true });
const linkState = computed(() => String(state.value?.linkState ?? ""));
const allDown = computed(() => !networkStatus.value.mqtt && !networkStatus.value.p2p && !networkStatus.value.http);
const fallbackActive = computed(() => /p2p|http|fallback/.test(linkState.value));

function channelName(raw: string | null | undefined): string {
  const c = String(raw ?? "").toLowerCase();
  if (c.includes("mqtt")) return t("comms.ch_mqtt");
  if (c.includes("p2p")) return t("comms.ch_p2p");
  if (c.includes("http")) return t("comms.ch_http");
  return t("comms.link_down");
}
const activeChannelLabel = computed(() => channelName(linkState.value));

// ── Enlace por unidad ────────────────────────────────────────────────
type Quality = "good" | "fair" | "weak" | "none";
/** Sin noticias de una unidad durante este tiempo → se marca como incomunicada. */
const STALE_S = 20;

interface UnitLink {
  amb: Ambulance;
  label: string;
  network: string;
  quality: Quality;
  latencyMs: number | null;
  lossPct: number | null;
  silentS: number | null;
}

function qualityOf(networkType: string | undefined, rssi: number | undefined): Quality {
  if (networkType === "none" || rssi == null) return "none";
  if (rssi >= -78) return "good";
  if (rssi >= -92) return "fair";
  return "weak";
}

const units = computed<UnitLink[]>(() => {
  const s = state.value;
  if (!s) return [];
  const channelKey = linkState.value.includes("p2p") ? "p2p" : linkState.value.includes("http") ? "http" : "mqtt";
  return s.ambulances.map((amb, idx) => {
    const net = amb.telemetry?.network;
    const last = amb.lastContactAt ? Date.parse(amb.lastContactAt) : NaN;
    return {
      amb,
      label: displayId(amb, idx, s.entityTypes),
      network: net?.networkType ?? "—",
      quality: qualityOf(net?.networkType, net?.rssiDbm),
      latencyMs: net && net.networkType !== "none" ? net.latencyMs?.[channelKey] ?? null : null,
      lossPct: net?.packetLossPct ?? null,
      silentS: Number.isNaN(last) ? null : Math.max(0, Math.round((now.value - last) / 1000)),
    };
  });
});

const isSilent = (u: UnitLink) => allDown.value || u.silentS == null || u.silentS > STALE_S;
const QUALITY_RANK: Record<Quality, number> = { none: 0, weak: 1, fair: 2, good: 3 };
/** Primero las que necesitan atención: sin contacto, sin cobertura, señal débil. */
const sortedUnits = computed(() =>
  [...units.value].sort((a, b) => Number(isSilent(b)) - Number(isSilent(a)) || QUALITY_RANK[a.quality] - QUALITY_RANK[b.quality] || a.label.localeCompare(b.label)),
);

const kpis = computed(() => {
  const list = units.value;
  const online = list.filter((u) => !isSilent(u)).length;
  const lat = list.map((u) => u.latencyMs).filter((v): v is number => v != null);
  const unread = (state.value?.unitMessages ?? []).filter((m) => m.status !== "read").length;
  return {
    online,
    total: list.length,
    silent: list.length - online,
    avgLatency: !allDown.value && lat.length ? Math.round(lat.reduce((a, b) => a + b, 0) / lat.length) : null,
    unread,
  };
});

function silentLabel(u: UnitLink): string {
  if (allDown.value) return t("comms.unit_no_channel");
  if (u.silentS == null) return "—";
  if (u.silentS <= 3) return t("comms.just_now");
  return t("comms.seconds_ago", { n: u.silentS });
}

// ── Mensajes ─────────────────────────────────────────────────────────
const target = ref<string>("all");
const draft = ref("");
const sending = ref(false);
const QUICK = ["confirm_position", "return_base", "report_status", "stand_by", "proceed_hospital"] as const;

const messages = computed<UnitMessage[]>(() => [...(state.value?.unitMessages ?? [])].reverse());

function targetLabel(m: UnitMessage): string {
  return m.unitId ? m.unitLabel || m.unitId.slice(0, 6) : t("comms.all_units");
}

function selectUnit(u: UnitLink) {
  target.value = String(u.amb.id);
}

async function send(text = draft.value) {
  const body = text.trim();
  if (!body || sending.value) return;
  sending.value = true;
  try {
    await store.sendUnitMessage(target.value === "all" ? null : target.value, body);
    if (text === draft.value) draft.value = "";
    toast.success(allDown.value ? t("comms.msg_queued") : t("comms.msg_sent"));
  } catch (e) {
    toast.error(t("comms.msg_error"), { description: e instanceof Error ? e.message : String(e) });
  } finally {
    sending.value = false;
  }
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── Canales de datos ─────────────────────────────────────────────────
const channelHealth = computed(() => {
  const net = networkStatus.value;
  return [
    { name: t("comms.ch_mqtt"), key: "mqtt" as const, active: net.mqtt, priority: 1 },
    { name: t("comms.ch_p2p"), key: "p2p" as const, active: net.p2p, priority: 2 },
    { name: t("comms.ch_http"), key: "http" as const, active: net.http, priority: 3 },
  ];
});
const busy = ref<Record<string, boolean>>({});

async function toggleChannel(key: "mqtt" | "p2p" | "http") {
  busy.value[key] = true;
  try {
    const next = { ...networkStatus.value, [key]: !networkStatus.value[key] };
    await store.setNetwork(next);
    toast.success(t("comms.channel_changed", {
      channel: channelName(key),
      state: next[key] ? t("comms.toggle_on").toLowerCase() : t("comms.toggle_off").toLowerCase(),
    }));
  } catch (e) {
    toast.error(t("comms.channel_change_error"), { description: e instanceof Error ? e.message : String(e) });
  } finally {
    busy.value[key] = false;
  }
}

async function restoreAll() {
  busy.value._all = true;
  try {
    await store.setNetwork({ mqtt: true, p2p: true, http: true });
    toast.success(t("comms.restore_all"));
  } catch (e) {
    toast.error(t("comms.channel_change_error"), { description: e instanceof Error ? e.message : String(e) });
  } finally {
    busy.value._all = false;
  }
}

const recentSends = computed(() => [...commsLog.value].reverse().slice(0, 40));
const failedSends = computed(() => commsLog.value.slice(-40).filter((x) => !x.ok).length);
</script>

<template>
  <div class="space-y-4">
    <div>
      <h1 class="text-lg font-semibold tracking-tight text-slate-100">{{ t('comms.title') }}</h1>
      <p class="text-sm text-slate-500">{{ t('comms.subtitle') }}</p>
    </div>

    <!-- Resumen -->
    <div class="grid grid-cols-2 gap-px overflow-hidden rounded border border-slate-800 bg-slate-800 lg:grid-cols-4">
      <div class="bg-slate-900 px-4 py-3">
        <p class="text-[11px] text-slate-500">{{ t('comms.kpi_link') }}</p>
        <p class="mt-1 flex items-center gap-2 text-sm font-medium" :class="allDown ? 'text-red-300' : fallbackActive ? 'text-amber-300' : 'text-slate-100'">
          <span class="h-2 w-2 rounded-full" :class="allDown ? 'bg-red-400' : fallbackActive ? 'bg-amber-400' : 'bg-green-400'" />
          {{ allDown ? t('comms.link_down') : activeChannelLabel }}
        </p>
      </div>
      <div class="bg-slate-900 px-4 py-3">
        <p class="text-[11px] text-slate-500">{{ t('comms.kpi_online') }}</p>
        <p class="mt-1 font-mono text-lg" :class="kpis.silent ? 'text-amber-300' : 'text-slate-100'">{{ kpis.online }}<span class="text-slate-500">/{{ kpis.total }}</span></p>
      </div>
      <div class="bg-slate-900 px-4 py-3">
        <p class="text-[11px] text-slate-500">{{ t('comms.kpi_latency') }}</p>
        <p class="mt-1 font-mono text-lg text-slate-100">{{ kpis.avgLatency ?? '—' }}<span v-if="kpis.avgLatency != null" class="text-xs text-slate-500"> ms</span></p>
      </div>
      <div class="bg-slate-900 px-4 py-3">
        <p class="text-[11px] text-slate-500">{{ t('comms.kpi_unread') }}</p>
        <p class="mt-1 font-mono text-lg text-slate-100">{{ kpis.unread }}</p>
      </div>
    </div>

    <p v-if="allDown" class="rounded border border-red-500/40 px-4 py-2 text-sm text-red-300">{{ t('comms.all_channels_down') }}</p>
    <p v-else-if="fallbackActive" class="rounded border border-amber-500/40 px-4 py-2 text-sm text-amber-300">{{ t('comms.fallback_active', { channel: activeChannelLabel }) }}</p>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <!-- Enlace por unidad -->
      <section class="panel">
        <header class="border-b border-slate-800 px-4 py-3">
          <h2 class="text-sm font-medium text-slate-100">{{ t('comms.units_title') }}</h2>
          <p class="text-xs text-slate-500">{{ t('comms.units_help') }}</p>
        </header>
        <div class="max-h-[min(520px,60vh)] overflow-auto text-xs">
          <table class="w-full border-collapse text-left">
            <thead class="sticky top-0 bg-slate-900 text-[11px] text-slate-500">
              <tr>
                <th class="px-4 py-2 font-normal">{{ t('comms.col_unit') }}</th>
                <th class="px-3 py-2 font-normal">{{ t('comms.col_status') }}</th>
                <th class="px-3 py-2 font-normal">{{ t('comms.col_network') }}</th>
                <th class="px-3 py-2 font-normal">{{ t('comms.col_signal') }}</th>
                <th class="px-3 py-2 text-right font-normal">{{ t('comms.col_ms') }}</th>
                <th class="px-3 py-2 text-right font-normal">{{ t('comms.col_loss') }}</th>
                <th class="px-4 py-2 font-normal">{{ t('comms.col_last_contact') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="u in sortedUnits"
                :key="u.amb.id"
                class="cursor-pointer border-t border-slate-800 hover:bg-slate-800/50"
                :class="target === String(u.amb.id) ? 'bg-slate-800/70' : ''"
                :title="t('comms.select_to_message')"
                @click="selectUnit(u)"
              >
                <td class="px-4 py-1.5 font-mono text-slate-100">{{ u.label }}</td>
                <td class="px-3 py-1.5 text-slate-400">{{ t(`status.${unitStatus(u.amb).key}`) }}</td>
                <td class="px-3 py-1.5 font-mono text-slate-300">{{ u.network === 'none' ? '—' : u.network }}</td>
                <td class="px-3 py-1.5" :class="{ good: 'text-slate-300', fair: 'text-slate-300', weak: 'text-amber-300', none: 'text-red-300' }[u.quality]">
                  {{ t(`comms.quality_${u.quality}`) }}
                </td>
                <td class="px-3 py-1.5 text-right font-mono text-slate-300">{{ u.latencyMs != null ? Math.round(u.latencyMs) : '—' }}</td>
                <td class="px-3 py-1.5 text-right font-mono" :class="(u.lossPct ?? 0) >= 5 ? 'text-amber-300' : 'text-slate-300'">{{ u.lossPct != null ? `${u.lossPct.toFixed(1)} %` : '—' }}</td>
                <td class="px-4 py-1.5" :class="isSilent(u) ? 'text-red-300' : 'text-slate-400'">{{ silentLabel(u) }}</td>
              </tr>
              <tr v-if="!sortedUnits.length">
                <td colspan="7" class="px-4 py-6 text-center text-slate-500">{{ t('comms.no_units') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Mensajes a las unidades -->
      <section class="panel flex flex-col">
        <header class="border-b border-slate-800 px-4 py-3">
          <h2 class="text-sm font-medium text-slate-100">{{ t('comms.messages_title') }}</h2>
          <p class="text-xs text-slate-500">{{ t('comms.messages_help') }}</p>
        </header>
        <form class="space-y-2 border-b border-slate-800 px-4 py-3" @submit.prevent="send()">
          <label class="block text-[11px] text-slate-500" for="msg-target">{{ t('comms.to') }}</label>
          <select id="msg-target" v-model="target" class="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100">
            <option value="all">{{ t('comms.all_units') }}</option>
            <option v-for="u in units" :key="u.amb.id" :value="String(u.amb.id)">{{ u.label }} · {{ t(`status.${unitStatus(u.amb).key}`) }}</option>
          </select>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="q in QUICK"
              :key="q"
              type="button"
              class="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
              :disabled="sending"
              @click="send(t(`comms.quick_${q}`))"
            >{{ t(`comms.quick_${q}`) }}</button>
          </div>
          <label class="sr-only" for="msg-text">{{ t('comms.message') }}</label>
          <div class="flex gap-2">
            <input
              id="msg-text"
              v-model="draft"
              maxlength="280"
              :placeholder="t('comms.message_placeholder')"
              class="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 placeholder:text-slate-600"
            />
            <button
              type="submit"
              class="rounded bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-950 hover:bg-slate-300 disabled:opacity-40"
              :disabled="sending || !draft.trim()"
            >{{ t('comms.send') }}</button>
          </div>
        </form>
        <ul class="max-h-[min(340px,40vh)] flex-1 divide-y divide-slate-800 overflow-auto text-xs">
          <li v-for="m in messages" :key="m.id" class="px-4 py-2">
            <div class="flex items-center gap-2">
              <span class="font-mono text-slate-200">{{ targetLabel(m) }}</span>
              <span class="text-slate-600">{{ formatTime(m.createdAt) }}</span>
              <span
                class="ml-auto text-[11px]"
                :class="m.status === 'queued' ? 'text-amber-300' : m.status === 'read' ? 'text-slate-300' : 'text-slate-500'"
              >
                {{ t(`comms.msg_status_${m.status}`) }}<template v-if="m.status !== 'queued' && m.channel"> · {{ channelName(m.channel) }}</template>
              </span>
            </div>
            <p class="mt-0.5 text-slate-400">{{ m.text }}</p>
          </li>
          <li v-if="!messages.length" class="px-4 py-6 text-center text-slate-500">{{ t('comms.no_messages') }}</li>
        </ul>
      </section>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <!-- Canales de datos -->
      <section class="panel">
        <div class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <div>
            <h2 class="text-sm font-medium text-slate-100">{{ t('comms.channel_controls') }}</h2>
            <p class="text-xs text-slate-500">{{ t('comms.channel_controls_help') }}</p>
          </div>
          <button
            type="button"
            class="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-40"
            :disabled="busy._all || (networkStatus.mqtt && networkStatus.p2p && networkStatus.http)"
            @click="restoreAll"
          >{{ t('comms.restore_all') }}</button>
        </div>
        <ul class="divide-y divide-slate-800">
          <li v-for="ch in channelHealth" :key="ch.key" class="flex items-center gap-3 px-4 py-2.5">
            <span class="w-5 font-mono text-xs text-slate-500">{{ ch.priority }}º</span>
            <span class="h-2 w-2 rounded-full" :class="ch.active ? 'bg-green-400' : 'bg-slate-600'" />
            <span class="flex-1 text-sm" :class="ch.active ? 'text-slate-100' : 'text-slate-500'">{{ ch.name }}</span>
            <span class="text-xs" :class="ch.active ? 'text-slate-400' : 'text-slate-600'">{{ ch.active ? t('comms.enabled') : t('comms.off') }}</span>
            <button
              type="button"
              class="w-20 rounded border border-slate-700 px-2 py-1 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-40"
              :disabled="busy[ch.key]"
              @click="toggleChannel(ch.key)"
            >{{ ch.active ? t('comms.toggle_off') : t('comms.toggle_on') }}</button>
          </li>
        </ul>
      </section>

      <!-- Últimos envíos de datos -->
      <section class="panel">
        <h2 class="border-b border-slate-800 px-4 py-3 text-sm font-medium text-slate-100">{{ t('comms.log_title') }}</h2>
        <div class="max-h-[220px] overflow-auto text-xs">
          <table class="w-full border-collapse text-left">
            <thead class="sticky top-0 bg-slate-900 text-[11px] text-slate-500">
              <tr>
                <th class="px-4 py-2 font-normal">{{ t('comms.col_time') }}</th>
                <th class="px-4 py-2 font-normal">{{ t('comms.col_channel') }}</th>
                <th class="px-4 py-2 text-right font-normal">{{ t('comms.col_ms') }}</th>
                <th class="px-4 py-2 font-normal">{{ t('comms.col_ok') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in recentSends" :key="r.seq" class="border-t border-slate-800">
                <td class="px-4 py-1.5 font-mono text-slate-400">{{ formatTime(r.at) }}</td>
                <td class="px-4 py-1.5 text-slate-200">{{ channelName(r.channel) }}</td>
                <td class="px-4 py-1.5 text-right font-mono text-slate-300">{{ Math.round(Number(r.latencyMs)) }} ms</td>
                <td class="px-4 py-1.5">
                  <span v-if="r.ok" class="text-slate-300">{{ t('common.yes') }}</span>
                  <span v-else class="text-red-300">{{ t('common.error') }}</span>
                </td>
              </tr>
              <tr v-if="!recentSends.length">
                <td colspan="4" class="px-4 py-6 text-center text-slate-500">{{ t('comms.no_events') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="failedSends" class="border-t border-slate-800 px-4 py-2 text-xs text-red-300">{{ t('comms.errors_warn') }}</p>
      </section>
    </div>
  </div>
</template>
