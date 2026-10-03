<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import AIProposalPanel from "@/components/dashboard/AIProposalPanel.vue";
import ChatPanel from "@/components/dashboard/ChatPanel.vue";
import InfoModal from "@/components/dashboard/InfoModal.vue";
import LanguageSelector from "@/components/dashboard/LanguageSelector.vue";
import RegionSelector from "@/components/dashboard/RegionSelector.vue";
import { getSupabase } from "@/lib/supabase";
import { useTheme } from "@/composables/useTheme";
import { useRegionStore } from "@/stores/region";
import { useSimulationStore } from "@/stores/simulation";

const { t } = useI18n();

const router = useRouter();
const route = useRoute();
const store = useSimulationStore();
const regionStore = useRegionStore();
const { state, streamStatus } = storeToRefs(store);
const { active: activeRegion } = storeToRefs(regionStore);

const { theme, toggleTheme } = useTheme();
const infoOpen = ref(false);
const nowTs = ref(Date.now());
let clockTimer: ReturnType<typeof setInterval> | null = null;

function onKey(e: KeyboardEvent) {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
  if (e.key === "i" || e.key === "I") {
    e.preventDefault();
    infoOpen.value = !infoOpen.value;
  }
}

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  clockTimer = setInterval(() => {
    nowTs.value = Date.now();
  }, 1000);
  await Promise.all([
    store.bootstrapSimulation(),
    regionStore.fetchRegions(),
  ]);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  if (clockTimer) clearInterval(clockTimer);
  store.teardownStreams();
});

async function logout() {
  await getSupabase()?.auth.signOut();
  await router.replace("/login");
}

const vehicleCount = computed(() => (state.value?.ambulances?.length ?? 0) + (state.value?.companions?.length ?? 0));

// ── Pill bar de filtros activos (vienen del chatbot /api/ai/command) ─────
const uiFilters = computed(() => store.uiFilters || {});
interface FilterPill { key: string; label: string; }
const activePills = computed<FilterPill[]>(() => {
  const f = uiFilters.value;
  const pills: FilterPill[] = [];
  if (f.fuelBelow != null) pills.push({ key: "fuelBelow", label: `⛽ ${t("filters.fuel_below", { value: f.fuelBelow })}` });
  if (f.fuelAbove != null) pills.push({ key: "fuelAbove", label: `⛽ ${t("filters.fuel_above", { value: f.fuelAbove })}` });
  if (f.batteryBelow != null) pills.push({ key: "batteryBelow", label: `🔋 ${t("filters.battery_below", { value: f.batteryBelow })}` });
  if (f.hasPatient === true) pills.push({ key: "hasPatient", label: `🚑 ${t("filters.with_patient")}` });
  if (f.hasPatient === false) pills.push({ key: "hasPatient", label: t("filters.without_patient") });
  if (f.severity) pills.push({ key: "severity", label: t("filters.severity", { value: f.severity }) });
  if (f.entityTypeId) pills.push({ key: "entityTypeId", label: t("filters.type", { value: f.entityTypeId }) });
  if (f.missionPhase) pills.push({ key: "missionPhase", label: t("filters.phase", { value: f.missionPhase }) });
  if (f.poweredOff === true) pills.push({ key: "poweredOff", label: t("filters.powered_off") });
  if (f.poweredOff === false) pills.push({ key: "poweredOff", label: t("filters.powered_on") });
  return pills;
});
function removePill(key: string) {
  // Clonar sin esa key + reemplazar atomicamente el ref del store
  const current = { ...store.uiFilters } as Record<string, unknown>;
  delete current[key];
  store.clearUiFilters();
  store.setUiFilters(current);
}
function clearAllPills() { store.clearUiFilters(); }
const activeEmergencies = computed(() => state.value?.emergencies?.filter((e) => e.status === "pending" || e.status === "assigned").length ?? 0);
const resolvedCount = computed(() => state.value?.emergencies?.filter((e) => e.status === "resolved").length ?? 0);
const aiMode = computed(() => state.value?.aiMode ?? "hitl");
const activeTimezone = computed(() => activeRegion.value?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
const localTime = computed(() => {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: activeTimezone.value,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(nowTs.value);
  } catch {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(nowTs.value);
  }
});

const nav = computed(() => [
  {
    to: "/map",
    label: t("nav.operations"),
    icon: "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z M15 11a3 3 0 11-6 0 3 3 0 016 0z",
  },
  {
    to: "/island",
    label: t("nav.island"),
    icon: "M3 12h18 M12 3v18 M5 5l14 14 M19 5L5 19",
  },
  {
    to: "/fleet",
    label: t("nav.telemetry"),
    icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
  },
  {
    to: "/comms",
    label: t("nav.comms"),
    icon: "M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.858 15.355-5.858 21.213 0",
  },
  {
    to: "/reports",
    label: t("nav.reports"),
    icon: "M9 17v-2a4 4 0 014-4h4m0 0V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2v-4m0 0l-3-3m3 3l-3 3",
  },
  {
    to: "/config",
    label: t("nav.config"),
    icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z",
  },
]);
</script>

<template>
  <div class="min-h-screen bg-slate-950 text-slate-100">
    <header class="sticky top-0 z-[600] border-b border-slate-800 bg-slate-950">
      <div class="mx-auto flex h-11 max-w-[1800px] items-stretch whitespace-nowrap">
        <!-- Marca + estado de enlaces -->
        <div class="flex items-center gap-2.5 border-r border-slate-800 pl-4 pr-4">
          <svg viewBox="0 0 64 64" class="h-5 w-5 text-slate-100" aria-hidden="true">
            <rect x="17" y="17" width="30" height="30" fill="none" stroke="currentColor" stroke-width="4" />
            <rect x="28" y="28" width="8" height="8" fill="currentColor" />
          </svg>
          <span class="text-[13px] font-semibold tracking-tight text-slate-100">Sentinel</span>
          <span
            class="ml-1 h-1.5 w-1.5 rounded-full"
            :class="streamStatus === 'connected' ? 'bg-green-400' : streamStatus === 'connecting' ? 'bg-amber-400 animate-pulse' : 'bg-red-400'"
            :title="streamStatus === 'connected' ? t('header.sse_connected') : streamStatus === 'connecting' ? t('header.sse_connecting') : t('header.sse_disconnected')"
          />
          <span
            v-if="state?.osrmRouting"
            class="hidden font-mono text-[10px] uppercase tracking-wider lg:inline"
            :class="state.osrmRouting.ready ? 'text-slate-500' : 'text-amber-400'"
          >
            {{ state.osrmRouting.ready ? t('header.osrm_ok') : t('header.osrm_loading') }}
          </span>
        </div>

        <!-- Navegación -->
        <nav class="flex min-w-0 items-stretch overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <RouterLink
            v-for="n in nav"
            :key="n.to"
            :to="n.to"
            class="flex items-center gap-1.5 border-b-2 px-3 text-xs font-medium no-underline"
            :class="
              route.path === n.to
                ? 'border-slate-100 text-slate-100'
                : 'border-transparent text-slate-400 hover:text-slate-100'
            "
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.75">
              <path stroke-linecap="round" stroke-linejoin="round" :d="n.icon" />
            </svg>
            {{ n.label }}
          </RouterLink>
        </nav>

        <!-- KPIs -->
        <div class="ml-auto hidden items-stretch min-[1700px]:flex">
          <div class="flex items-center gap-2 border-l border-slate-800 px-3">
            <span class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('header.vehicles') }}</span>
            <span class="font-mono text-xs text-slate-100">{{ vehicleCount }}</span>
          </div>
          <div class="flex items-center gap-2 border-l border-slate-800 px-3">
            <span class="h-1.5 w-1.5 rounded-full" :class="activeEmergencies > 0 ? 'bg-red-400' : 'bg-slate-600'" />
            <span class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('header.active') }}</span>
            <span class="font-mono text-xs" :class="activeEmergencies > 0 ? 'text-red-300' : 'text-slate-100'">{{ activeEmergencies }}</span>
          </div>
          <div class="flex items-center gap-2 border-l border-slate-800 px-3">
            <span class="text-[10px] uppercase tracking-wider text-slate-500">{{ t('header.resolved') }}</span>
            <span class="font-mono text-xs text-slate-100">{{ resolvedCount }}</span>
          </div>
          <div class="flex items-center gap-2 border-l border-slate-800 px-3" :title="activeTimezone">
            <span class="text-[10px] uppercase tracking-wider text-slate-500">{{ t("header.local_time") }}</span>
            <span class="font-mono text-xs text-slate-100">{{ localTime }}</span>
          </div>
        </div>

        <!-- Controles -->
        <div class="ml-auto flex items-center gap-1.5 border-l border-slate-800 px-3 min-[1700px]:ml-0">
          <span
            class="flex items-center gap-1.5 rounded border px-2 py-1 font-mono text-[10px] uppercase tracking-wider"
            :class="aiMode === 'autonomous' ? 'border-slate-600 text-slate-100' : 'border-amber-500/40 text-amber-300'"
          >
            <span class="h-1.5 w-1.5 rounded-full" :class="aiMode === 'autonomous' ? 'bg-slate-300' : 'bg-amber-400'" />
            {{ aiMode === 'autonomous' ? t('header.ai_auto') : t('header.ai_hitl') }}
          </span>
          <LanguageSelector />
          <RegionSelector />
          <button
            type="button"
            class="rounded border border-slate-800 p-1.5 text-slate-400 hover:border-slate-700 hover:text-slate-100"
            :title="theme === 'dark' ? t('header.theme_light') : t('header.theme_dark')"
            :aria-label="theme === 'dark' ? t('header.theme_light') : t('header.theme_dark')"
            @click="toggleTheme"
          >
            <svg v-if="theme === 'dark'" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.75">
              <circle cx="12" cy="12" r="4" />
              <path stroke-linecap="round" d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
            </svg>
            <svg v-else xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.75">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          </button>
          <button
            type="button"
            class="rounded border border-slate-800 p-1.5 text-slate-400 hover:border-slate-700 hover:text-slate-100"
            :title="t('header.help_tooltip')"
            @click="infoOpen = true"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.75">
              <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </button>
          <button
            type="button"
            class="rounded border border-slate-800 p-1.5 text-slate-400 hover:border-slate-700 hover:text-slate-100"
            :title="t('header.logout_tooltip')"
            :aria-label="t('common.logout')"
            @click="logout"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.75">
              <path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
          </button>
        </div>
      </div>
    </header>

    <!-- Filtros activos desde ⌘ comando -->
    <Transition name="pills">
      <div v-if="activePills.length" class="border-b border-slate-800 bg-slate-900">
        <div class="mx-auto flex max-w-[1800px] flex-wrap items-center gap-1.5 px-4 py-1.5">
          <span class="mr-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
            {{ t('filters.title') }}
          </span>
          <span
            v-for="p in activePills"
            :key="p.key"
            class="inline-flex items-center gap-1.5 rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-200"
          >
            {{ p.label }}
            <button
              type="button"
              class="text-slate-500 hover:text-slate-100"
              :title="`Quitar ${p.label}`"
              @click="removePill(p.key)"
            >✕</button>
          </span>
          <button
            class="ml-auto rounded px-2 py-0.5 text-[11px] text-slate-400 hover:text-slate-100"
            @click="clearAllPills"
          >Limpiar todo</button>
        </div>
      </div>
    </Transition>

    <main class="mx-auto max-w-[1800px] p-4">
      <RouterView />
    </main>

    <InfoModal :open="infoOpen" @close="infoOpen = false" />
    <AIProposalPanel />
    <ChatPanel />
  </div>
</template>

<style scoped>
.pills-enter-active, .pills-leave-active { transition: opacity 0.15s ease, transform 0.15s ease; }
.pills-enter-from, .pills-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
