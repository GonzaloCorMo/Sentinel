<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useSimulationStore } from "@/stores/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const { state, commsLog, commsStreamStatus, streamStatus } = storeToRefs(store);

const rows = computed(() => [...commsLog.value].reverse());
const hasErrors = computed(() => rows.value.some((x) => x.error));

const networkStatus = computed(() => state.value?.networkStatus ?? { mqtt: true, p2p: true, http: true });
const linkState = computed(() => state.value?.linkState ?? "—");
const allDown = computed(() => !networkStatus.value.mqtt && !networkStatus.value.p2p && !networkStatus.value.http);
const fallbackActive = computed(() => {
  const ls = String(linkState.value);
  return ls.includes("p2p") || ls.includes("http") || ls.includes("fallback");
});
const activeChannelLabel = computed(() => {
  const ls = String(linkState.value).toLowerCase();
  if (ls.includes("mqtt")) return "MQTT";
  if (ls.includes("p2p")) return "P2P";
  if (ls.includes("http")) return "HTTP";
  return ls;
});

const channelHealth = computed(() => {
  const net = networkStatus.value;
  return [
    { name: "MQTT", key: "mqtt" as const, active: net.mqtt, color: "emerald", priority: 1 },
    { name: "P2P", key: "p2p" as const, active: net.p2p, color: "sky", priority: 2 },
    { name: "HTTP", key: "http" as const, active: net.http, color: "amber", priority: 3 },
  ];
});

const busy = ref<Record<string, boolean>>({});

async function toggleChannel(key: "mqtt" | "p2p" | "http") {
  busy.value[key] = true;
  try {
    const next = { ...networkStatus.value, [key]: !networkStatus.value[key] };
    await store.setNetwork(next);
    toast.success(t("comms.channel_changed", {
      channel: key.toUpperCase(),
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
  <div class="space-y-5">
    <div>
      <h2 class="text-lg font-semibold text-slate-200">{{ t('comms.title') }}</h2>
      <p class="mt-0.5 text-sm text-slate-500">
        {{ t('comms.subtitle', { sse: streamStatus, comms: commsStreamStatus }) }}
      </p>
    </div>

    <!-- Stat cards -->
    <div class="grid gap-4 md:grid-cols-3">
      <div class="hpe-card flex items-stretch overflow-hidden">
        <div class="w-1 shrink-0 bg-emerald-500" />
        <div class="p-4">
          <p class="text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('comms.active_link') }}</p>
          <p class="mt-1 text-xl font-mono font-semibold text-emerald-400">{{ linkState }}</p>
        </div>
      </div>
      <div class="hpe-card flex items-stretch overflow-hidden">
        <div class="w-1 shrink-0" :class="networkStatus.mqtt ? 'bg-emerald-500' : 'bg-slate-600'" />
        <div class="p-4">
          <p class="text-[10px] font-medium uppercase tracking-wider text-slate-500">MQTT</p>
          <p class="mt-1 text-xl font-semibold" :class="networkStatus.mqtt ? 'text-emerald-400' : 'text-slate-500'">
            {{ networkStatus.mqtt ? t('comms.enabled') : t('comms.off') }}
          </p>
        </div>
      </div>
      <div class="hpe-card flex items-stretch overflow-hidden">
        <div class="w-1 shrink-0" :class="networkStatus.p2p || networkStatus.http ? 'bg-amber-500' : 'bg-slate-600'" />
        <div class="p-4">
          <p class="text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('comms.p2p_http') }}</p>
          <p class="mt-1 text-lg font-semibold text-slate-200">
            {{ networkStatus.p2p ? t('comms.p2p_on') : t('comms.p2p_off') }} ·
            {{ networkStatus.http ? t('comms.http_on') : t('comms.http_off') }}
          </p>
        </div>
      </div>
    </div>

    <!-- Banners de estado fallback / total down -->
    <div
      v-if="allDown"
      class="rounded-xl border border-rose-600/50 bg-rose-950/30 px-4 py-3 text-sm text-rose-200"
    >
      ⚠️ {{ t('comms.all_channels_down') }}
    </div>
    <div
      v-else-if="fallbackActive"
      class="rounded-xl border border-amber-500/40 bg-amber-950/25 px-4 py-3 text-sm text-amber-200"
    >
      🔄 {{ t('comms.fallback_active', { channel: activeChannelLabel }) }}
    </div>

    <!-- Controles toggle de canales -->
    <div class="hpe-card p-4">
      <div class="mb-3 flex items-center justify-between">
        <div>
          <p class="text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('comms.channel_controls') }}</p>
          <p class="mt-0.5 text-[11px] text-slate-500">{{ t('comms.channel_controls_help') }}</p>
        </div>
        <button
          type="button"
          class="rounded-lg border border-emerald-500/40 bg-emerald-600/15 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-600/30 disabled:opacity-40"
          :disabled="busy._all || (networkStatus.mqtt && networkStatus.p2p && networkStatus.http)"
          @click="restoreAll"
        >
          ↺ {{ t('comms.restore_all') }}
        </button>
      </div>
      <div class="grid gap-3 sm:grid-cols-3">
        <div
          v-for="ch in channelHealth"
          :key="ch.key"
          class="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"
          :class="ch.active
            ? `border-${ch.color}-500/40 bg-${ch.color}-950/25`
            : 'border-slate-700/60 bg-slate-950/40'"
        >
          <div class="flex items-center gap-2">
            <span
              class="h-3 w-3 rounded-full"
              :class="ch.active ? `bg-${ch.color}-400 shadow-sm shadow-${ch.color}-500/40` : 'bg-slate-600'"
            />
            <div>
              <p class="text-sm font-semibold" :class="ch.active ? 'text-slate-100' : 'text-slate-400'">
                {{ ch.name }}
              </p>
              <p class="text-[10px] text-slate-500">prio {{ ch.priority }}</p>
            </div>
          </div>
          <button
            type="button"
            class="rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:opacity-40"
            :class="ch.active
              ? 'border-rose-500/40 bg-rose-600/15 text-rose-300 hover:bg-rose-600/30'
              : 'border-emerald-500/40 bg-emerald-600/15 text-emerald-300 hover:bg-emerald-600/30'"
            :disabled="busy[ch.key]"
            @click="toggleChannel(ch.key)"
          >
            {{ ch.active ? t('comms.toggle_off') : t('comms.toggle_on') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Channel health bar -->
    <div class="hpe-card p-4">
      <p class="mb-3 text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('comms.channel_health') }}</p>
      <div class="flex gap-3">
        <div
          v-for="ch in channelHealth"
          :key="ch.key"
          class="flex flex-1 items-center gap-2 rounded-lg border px-3 py-2"
          :class="ch.active
            ? `border-${ch.color}-500/30 bg-${ch.color}-950/20`
            : 'border-slate-700/50 bg-slate-950/30'"
        >
          <span
            class="h-2.5 w-2.5 rounded-full"
            :class="ch.active ? `bg-${ch.color}-400` : 'bg-slate-600'"
          />
          <span class="text-xs font-medium" :class="ch.active ? 'text-slate-200' : 'text-slate-500'">
            {{ ch.name }}
          </span>
          <span
            class="ml-auto rounded-md px-1.5 py-0.5 text-[9px] font-semibold uppercase"
            :class="ch.active
              ? `bg-${ch.color}-600/20 text-${ch.color}-300`
              : 'bg-slate-800 text-slate-500'"
          >
            {{ ch.active ? 'ON' : 'OFF' }}
          </span>
        </div>
      </div>
    </div>

    <!-- Activity table -->
    <div
      class="max-h-[min(520px,60vh)] overflow-auto rounded-xl border border-slate-700/50 bg-slate-950/80 font-mono text-xs"
    >
      <table class="w-full border-collapse text-left">
        <thead class="sticky top-0 bg-slate-900/95 text-[10px] font-semibold uppercase tracking-wider text-slate-500 backdrop-blur-sm">
          <tr>
            <th class="p-2.5">#</th>
            <th class="p-2.5">{{ t('comms.col_time') }}</th>
            <th class="p-2.5">{{ t('comms.col_channel') }}</th>
            <th class="p-2.5">{{ t('comms.col_ms') }}</th>
            <th class="p-2.5">{{ t('comms.col_tick') }}</th>
            <th class="p-2.5">{{ t('comms.col_summary') }}</th>
            <th class="p-2.5">{{ t('comms.col_ok') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(r, idx) in rows"
            :key="r.seq"
            class="border-t border-slate-800/50 transition-colors hover:bg-slate-900/60"
            :class="idx % 2 === 0 ? 'bg-slate-950/40' : 'bg-transparent'"
          >
            <td class="p-2.5 text-slate-600">{{ r.seq }}</td>
            <td class="p-2.5 text-slate-400">{{ r.at }}</td>
            <td class="p-2.5 font-semibold" :class="r.ok ? 'text-emerald-400' : 'text-rose-400'">{{ r.channel }}</td>
            <td class="p-2.5 text-slate-300">{{ r.latencyMs }}</td>
            <td class="p-2.5 text-slate-500">{{ r.tick }}</td>
            <td class="max-w-[200px] truncate p-2.5 text-slate-400" :title="r.summary">{{ r.summary }}</td>
            <td class="p-2.5">
              <span v-if="r.ok" class="text-emerald-400">✓</span>
              <span v-else class="text-rose-400">✗</span>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="7" class="p-6 text-center text-slate-500">{{ t('comms.no_events') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="hasErrors" class="text-xs text-rose-400">
      {{ t('comms.errors_warn') }}
    </p>
  </div>
</template>
