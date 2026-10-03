<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";

interface RegionSummary {
  generatedAt: string;
  region?: { id: string; name: string };
  weather: {
    stations: number;
    avgTempC: number | null;
    maxPrecipMmh: number | null;
    maxWindKmh: number | null;
    minVisibilityKm: number | null;
    alerts: Array<{
      stationId: string;
      stationName: string;
      latitude: number | null;
      longitude: number | null;
      precipMm: number;
      windKmh: number;
      visibilityKm: number;
      kind: string;
    }>;
  };
  events: {
    active: number;
    byType: Record<string, number>;
    bySeverity: Record<string, number>;
    items: Array<{
      id: string;
      type: string;
      severity: string;
      title: string;
      latitude: number;
      longitude: number;
      started_at: string;
    }>;
  };
  fleet: {
    totalAmbulances: number;
    activeEmergencies: number;
    fuelLowCount: number;
    avgEtaSeconds: number | null;
    pulseRatePerMin: number;
  };
  weatherImpact: {
    worstFactor: number;
    avgFactor: number;
    affectedMissions: number;
  };
  perZone: Record<string, { ambulances: number; events: number; emergencies: number }>;
  stations: Array<{ id: string; name: string; latitude: number; longitude: number }>;
}

const { t, te } = useI18n();
const summary = ref<RegionSummary | null>(null);
const loading = ref(true);
const errorMsg = ref<string | null>(null);
let pollHandle: number | null = null;

async function fetchSummary() {
  try {
    const r = await fetch("/api/region/summary");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    summary.value = (await r.json()) as RegionSummary;
    errorMsg.value = null;
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

function startPolling() {
  pollHandle = window.setInterval(fetchSummary, 4000);
}

onMounted(() => {
  fetchSummary().then(startPolling);
});

onBeforeUnmount(() => {
  if (pollHandle != null) window.clearInterval(pollHandle);
});

const weatherImpactColor = computed(() => {
  const w = summary.value?.weatherImpact.worstFactor ?? 1;
  if (w < 0.6) return "text-rose-400";
  if (w < 0.85) return "text-amber-300";
  return "text-green-300";
});

const sortedZones = computed(() => {
  if (!summary.value) return [] as Array<{ name: string; counts: { ambulances: number; events: number; emergencies: number } }>;
  const labelMap: Record<string, string> = {
    north_west: t("island.zone_nw"),
    north_east: t("island.zone_ne"),
    south_west: t("island.zone_sw"),
    south_east: t("island.zone_se"),
  };
  return Object.entries(summary.value.perZone).map(([k, v]) => ({
    name: labelMap[k] ?? k,
    counts: v,
  }));
});

/** Segundos → "8 min 32 s" (o "45 s"). */
function formatDuration(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const total = Math.round(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m} min ${s} s` : `${s} s`;
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <header class="flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 class="text-lg font-semibold tracking-tight text-slate-100">
          {{ t("island.title") }}<span v-if="summary?.region" class="text-slate-500"> · {{ summary.region.name }}</span>
        </h1>
        <p class="text-sm text-slate-500">{{ t("island.subtitle") }}</p>
      </div>
      <span v-if="summary" class="font-mono text-xs text-slate-500">
        {{ t("island.generated") }} {{ new Date(summary.generatedAt).toLocaleTimeString() }}
      </span>
    </header>

    <p v-if="errorMsg" class="rounded border border-red-500/40 px-3 py-2 text-sm text-red-300">
      {{ errorMsg }}
    </p>

    <div v-if="loading && !summary" class="text-sm text-slate-400">{{ t('common.loading') }}</div>

    <div v-if="summary" class="grid grid-cols-1 gap-3 md:grid-cols-3">
      <!-- Weather panel -->
      <section class="rounded border border-slate-800 bg-slate-900 p-3">
        <h2 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">{{ t("island.weather") }}</h2>
        <div class="mt-2 grid grid-cols-2 gap-2 text-xs">
          <div>
            <div class="text-slate-400">{{ t("island.stations") }}</div>
            <div class="font-mono text-lg">{{ summary.weather.stations }}</div>
          </div>
          <div>
            <div class="text-slate-400">{{ t("island.avgTemp") }}</div>
            <div class="font-mono text-lg">{{ summary.weather.avgTempC ?? "—" }}°C</div>
          </div>
          <div>
            <div class="text-slate-400">{{ t("island.maxPrecip") }}</div>
            <div class="font-mono text-lg">{{ summary.weather.maxPrecipMmh ?? "—" }} mm/h</div>
          </div>
          <div>
            <div class="text-slate-400">{{ t("island.maxWind") }}</div>
            <div class="font-mono text-lg">{{ summary.weather.maxWindKmh ?? "—" }} km/h</div>
          </div>
        </div>
        <div class="mt-3 text-xs">
          <div class="text-slate-400">{{ t("island.alerts") }}: {{ summary.weather.alerts.length }}</div>
          <ul class="mt-1 max-h-40 overflow-y-auto space-y-1">
            <li v-for="a in summary.weather.alerts" :key="a.stationId" class="rounded-sm border border-red-500/30 px-2 py-1">
              <span class="font-mono text-red-300">{{ a.kind.toUpperCase() }}</span>
              <span class="ml-1">{{ a.stationName }}</span>
              <span class="ml-1 text-slate-400">— precip {{ a.precipMm }}mm, wind {{ a.windKmh }}km/h, vis {{ a.visibilityKm }}km</span>
            </li>
            <li v-if="summary.weather.alerts.length === 0" class="text-slate-500">{{ t("island.allClear") }}</li>
          </ul>
        </div>
      </section>

      <!-- Incidencias externas activas -->
      <section class="rounded border border-slate-800 bg-slate-900 p-3">
        <h2 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">{{ t("island.activeEvents") }}</h2>
        <div class="mt-2 text-xs">
          <div class="flex justify-between">
            <span class="text-slate-400">{{ t("island.active") }}:</span>
            <span class="font-mono font-semibold" :class="summary.events.active > 0 ? 'text-amber-300' : ''">{{ summary.events.active }}</span>
          </div>
          <div class="mt-2">
            <div class="text-slate-400">{{ t("island.byType") }}</div>
            <div class="mt-1 flex flex-wrap gap-1">
              <span v-for="(count, type) in summary.events.byType" :key="type"
                    class="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-200">
                {{ te(`events.type.${type}`) ? t(`events.type.${type}`) : type }} <span class="font-mono text-slate-400">{{ count }}</span>
              </span>
            </div>
          </div>
          <div class="mt-2">
            <div class="text-slate-400">{{ t("island.bySeverity") }}</div>
            <div class="mt-1 flex flex-wrap gap-1">
              <span v-for="(count, sev) in summary.events.bySeverity" :key="sev"
                    class="rounded-sm border px-1.5 py-0.5 text-[11px]"
                    :class="{
                      'border-red-500/40 text-red-300': sev === 'critical' || sev === 'high',
                      'border-amber-500/40 text-amber-300': sev === 'medium',
                      'border-slate-700 text-slate-300': sev === 'low',
                    }">
                {{ te(`events.severity.${sev}`) ? t(`events.severity.${sev}`) : sev }} <span class="font-mono opacity-70">{{ count }}</span>
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- Fleet KPIs -->
      <section class="rounded border border-slate-800 bg-slate-900 p-3">
        <h2 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">{{ t("island.fleet") }}</h2>
        <div class="mt-2 grid grid-cols-2 gap-2 text-xs">
          <div>
            <div class="text-slate-400">{{ t("island.totalAmbs") }}</div>
            <div class="font-mono text-lg">{{ summary.fleet.totalAmbulances }}</div>
          </div>
          <div>
            <div class="text-slate-400">{{ t("island.activeEmergencies") }}</div>
            <div class="font-mono text-lg">{{ summary.fleet.activeEmergencies }}</div>
          </div>
          <div>
            <div class="text-slate-400">{{ t("island.fuelLow") }}</div>
            <div class="font-mono text-lg">{{ summary.fleet.fuelLowCount }}</div>
          </div>
          <div>
            <div class="text-slate-400">{{ t("island.avgEta") }}</div>
            <div class="font-mono text-lg">
              {{ formatDuration(summary.fleet.avgEtaSeconds) }}
            </div>
          </div>
          <div class="col-span-2">
            <div class="text-slate-400">{{ t("island.pulseRate") }}</div>
            <div class="font-mono text-base">{{ summary.fleet.pulseRatePerMin }} /min</div>
          </div>
        </div>
        <div class="mt-3 rounded border border-slate-800 bg-slate-950 p-2 text-xs">
          <div class="text-slate-400">{{ t("island.weatherImpact") }}</div>
          <div class="mt-1 flex items-center justify-between">
            <span :class="weatherImpactColor" class="font-mono text-base">
              ×{{ summary.weatherImpact.worstFactor.toFixed(2) }}
            </span>
            <span class="text-slate-400">
              {{ t("island.affectedMissions") }}: {{ summary.weatherImpact.affectedMissions }}
            </span>
          </div>
        </div>
      </section>
    </div>

    <!-- Zone breakdown (mapa removido — disponible en dashboard de operaciones) -->
    <section v-if="summary" class="rounded border border-slate-800 bg-slate-900 p-3">
      <h2 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">{{ t("island.zones") }}</h2>
      <div class="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
        <div v-for="z in sortedZones" :key="z.name" class="rounded-sm border border-slate-800 bg-slate-950 p-2">
          <div class="text-slate-400">{{ z.name }}</div>
          <div class="mt-1 grid grid-cols-3 gap-1">
            <div>
              <div class="text-[10px] text-slate-500">U</div>
              <div class="font-mono text-sm text-slate-100">{{ z.counts.ambulances }}</div>
            </div>
            <div>
              <div class="text-[10px] text-slate-500">I</div>
              <div class="font-mono text-sm" :class="z.counts.events > 0 ? 'text-amber-300' : 'text-slate-500'">{{ z.counts.events }}</div>
            </div>
            <div>
              <div class="text-[10px] text-slate-500">E</div>
              <div class="font-mono text-sm" :class="z.counts.emergencies > 0 ? 'text-red-300' : 'text-slate-500'">{{ z.counts.emergencies }}</div>
            </div>
          </div>
        </div>
      </div>
      <p class="mt-2 text-[11px] text-slate-500">{{ t("island.zone_legend") }}</p>
    </section>
  </div>
</template>
