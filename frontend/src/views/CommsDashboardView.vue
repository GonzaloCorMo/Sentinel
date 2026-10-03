<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useSimulationStore } from "@/stores/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const { state, commsLog } = storeToRefs(store);

const rows = computed(() => [...commsLog.value].reverse());
const hasErrors = computed(() => rows.value.some((x) => x.error));

const networkStatus = computed(() => state.value?.networkStatus ?? { mqtt: true, p2p: true, http: true });
const linkState = computed(() => state.value?.linkState ?? "—");
const allDown = computed(() => !networkStatus.value.mqtt && !networkStatus.value.p2p && !networkStatus.value.http);
const fallbackActive = computed(() => {
  const ls = String(linkState.value);
  return ls.includes("p2p") || ls.includes("http") || ls.includes("fallback");
});
/** Canal en uso, en lenguaje claro (el motor expone p. ej. "mqtt_active"). */
const activeChannelLabel = computed(() => {
  const ls = String(linkState.value).toLowerCase();
  if (ls.includes("mqtt")) return t("comms.ch_mqtt");
  if (ls.includes("p2p")) return t("comms.ch_p2p");
  if (ls.includes("http")) return t("comms.ch_http");
  return t("comms.link_down");
});

function channelName(raw: string): string {
  const c = String(raw).toLowerCase();
  if (c.includes("mqtt")) return t("comms.ch_mqtt");
  if (c.includes("p2p")) return t("comms.ch_p2p");
  if (c.includes("http")) return t("comms.ch_http");
  return raw;
}

/** El motor resume cada envío como "batch u=5": se traduce a "5 unidades". */
function formatSummary(raw: string | undefined): string {
  const m = /batch u=(\d+)/.exec(String(raw ?? ""));
  return m ? t("comms.batch_units", { n: Number(m[1]) }, Number(m[1])) : (raw ?? "");
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

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
</script>

<template>
  <div class="space-y-4">
    <div>
      <h1 class="text-lg font-semibold tracking-tight text-slate-100">{{ t('comms.title') }}</h1>
      <p class="text-sm text-slate-500">{{ t('comms.subtitle') }}</p>
    </div>

    <!-- Canal en uso -->
    <div
      class="flex flex-wrap items-center gap-3 rounded border px-4 py-3"
      :class="allDown ? 'border-red-500/40' : fallbackActive ? 'border-amber-500/40' : 'border-slate-800 bg-slate-900'"
    >
      <span class="h-2 w-2 rounded-full" :class="allDown ? 'bg-red-400' : fallbackActive ? 'bg-amber-400' : 'bg-green-400'" />
      <span class="text-xs text-slate-500">{{ t('comms.active_link') }}</span>
      <span class="text-sm font-medium" :class="allDown ? 'text-red-300' : fallbackActive ? 'text-amber-300' : 'text-slate-100'">
        {{ allDown ? t('comms.all_channels_down') : activeChannelLabel }}
      </span>
      <span v-if="fallbackActive && !allDown" class="text-xs text-amber-300/80">{{ t('comms.fallback_active', { channel: activeChannelLabel }) }}</span>
    </div>

    <!-- Canales -->
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

    <!-- Últimos envíos -->
    <section class="panel">
      <h2 class="border-b border-slate-800 px-4 py-3 text-sm font-medium text-slate-100">{{ t('comms.log_title') }}</h2>
      <div class="max-h-[min(480px,55vh)] overflow-auto text-xs">
        <table class="w-full border-collapse text-left">
          <thead class="sticky top-0 bg-slate-900 text-[11px] text-slate-500">
            <tr>
              <th class="px-4 py-2 font-normal">{{ t('comms.col_time') }}</th>
              <th class="px-4 py-2 font-normal">{{ t('comms.col_channel') }}</th>
              <th class="px-4 py-2 text-right font-normal">{{ t('comms.col_ms') }}</th>
              <th class="px-4 py-2 font-normal">{{ t('comms.col_summary') }}</th>
              <th class="px-4 py-2 font-normal">{{ t('comms.col_ok') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.seq" class="border-t border-slate-800">
              <td class="px-4 py-1.5 font-mono text-slate-400">{{ formatTime(r.at) }}</td>
              <td class="px-4 py-1.5 text-slate-200">{{ channelName(r.channel) }}</td>
              <td class="px-4 py-1.5 text-right font-mono text-slate-300">{{ Math.round(Number(r.latencyMs)) }} ms</td>
              <td class="max-w-[260px] truncate px-4 py-1.5 text-slate-400" :title="r.summary">{{ formatSummary(r.summary) }}</td>
              <td class="px-4 py-1.5">
                <span v-if="r.ok" class="text-slate-300">{{ t('common.yes') }}</span>
                <span v-else class="text-red-300">{{ t('common.error') }}</span>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="5" class="px-4 py-6 text-center text-slate-500">{{ t('comms.no_events') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="hasErrors" class="border-t border-slate-800 px-4 py-2 text-xs text-red-300">{{ t('comms.errors_warn') }}</p>
    </section>
  </div>
</template>
