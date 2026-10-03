<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useSimulationStore } from "@/stores/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const { state } = storeToRefs(store);
const busy = ref(false);
const osrmPlayHintShown = ref(false);

const paused = computed(() => state.value?.paused ?? false);
const speed = computed(() => state.value?.stats.simulationSpeed ?? 1);
const tick = computed(() => state.value?.stats.tickCount ?? 0);
const osrm = computed(() => state.value?.osrmRouting);
const trainingMode = computed(() => state.value?.trainingMode ?? false);
const trainingRate = computed(() => state.value?.trainingRatePerMin ?? 3);
const sessionShort = computed(() => (state.value?.sessionId ?? "").slice(0, 8));
const rateInput = ref<number>(20);

const presets = [1, 2, 5, 10, 20];

async function toggleTrainingMode() {
  busy.value = true;
  try {
    const next = !trainingMode.value;
    await store.setTrainingMode(next, next ? rateInput.value : undefined);
    toast[next ? "success" : "info"](
      next
        ? t("sim.training_on", { rate: rateInput.value })
        : t("sim.training_off"),
    );
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t("sim.mode_change_error"));
  } finally {
    busy.value = false;
  }
}

async function toggle() {
  busy.value = true;
  try {
    const action = paused.value ? "play" : "pause";
    if (action === "play" && osrm.value && !osrm.value.ready && !osrmPlayHintShown.value) {
      toast.info(t("sim.osrm_warn"), {
        duration: 6000,
      });
      osrmPlayHintShown.value = true;
    }
    await store.postControl({ action });
  } finally {
    busy.value = false;
  }
}

async function reset() {
  busy.value = true;
  try {
    osrmPlayHintShown.value = false;
    await store.postControl({ action: "reset" });
  } finally {
    busy.value = false;
  }
}

async function setSpeed(mult: number) {
  busy.value = true;
  try {
    await store.postControl({ speedMultiplier: mult });
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="hpe-card p-4">
    <div class="flex flex-wrap items-center gap-4">
      <!-- Play/Pause toggle + Reset -->
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="flex h-10 w-10 items-center justify-center rounded-xl transition-all"
          :class="paused
            ? 'bg-emerald-600 text-white hover:bg-emerald-500 shadow-sm shadow-emerald-900/30'
            : 'bg-amber-600 text-white hover:bg-amber-500 shadow-sm shadow-amber-900/30'"
          :disabled="busy"
          :title="paused ? t('sim.resume_tooltip') : t('sim.pause_tooltip')"
          @click="toggle"
        >
          <!-- Play icon -->
          <svg v-if="paused" xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
          <!-- Pause icon -->
          <svg v-else xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M6 4h4v16H6zM14 4h4v16h-4z" />
          </svg>
        </button>

        <button
          type="button"
          class="flex h-10 w-10 items-center justify-center rounded-xl border border-slate-700 bg-slate-800/80 text-slate-300 transition hover:bg-slate-700 hover:text-white disabled:opacity-40"
          :title="t('sim.reset_tooltip')"
          :disabled="busy"
          @click="reset"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>

      <!-- Divider -->
      <div class="hidden h-8 w-px bg-slate-700/50 lg:block" />

      <!-- Speed selector (segmented) -->
      <div class="flex items-center gap-2">
        <span class="text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('sim.speed') }}</span>
        <div class="flex rounded-lg border border-slate-700/50 bg-slate-950/60 p-0.5">
          <button
            v-for="p in presets"
            :key="p"
            type="button"
            class="rounded-md px-2.5 py-1 text-xs font-medium transition-all"
            :class="
              Math.abs(speed - p) < 0.01
                ? 'bg-emerald-600 text-white shadow-sm'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            "
            :title="t('sim.speed_n', { n: p })"
            :disabled="busy"
            @click="setSpeed(p)"
          >
            {{ p }}x
          </button>
        </div>
      </div>

      <!-- Divider -->
      <div class="hidden h-8 w-px bg-slate-700/50 lg:block" />

      <!-- Tick counter -->
      <div class="flex items-center gap-2">
        <span class="text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('sim.tick') }}</span>
        <span class="rounded-lg border border-slate-700/50 bg-slate-950/60 px-2.5 py-1 font-mono text-sm font-semibold text-emerald-400">
          {{ tick }}
        </span>
      </div>

      <!-- Divider -->
      <div class="hidden h-8 w-px bg-slate-700/50 lg:block" />

      <!-- Training mode toggle (alimenta pipeline ML) -->
      <div class="flex items-center gap-2">
        <span class="text-[10px] font-medium uppercase tracking-wider text-slate-500">{{ t('sim.auto_ml') }}</span>
        <div class="flex items-center gap-1.5 rounded-lg border border-slate-700/50 bg-slate-950/60 p-0.5">
          <input
            v-model.number="rateInput"
            type="number"
            min="1"
            max="60"
            step="1"
            :disabled="busy || trainingMode"
            class="w-12 rounded-md border border-slate-700/80 bg-slate-900/80 px-1.5 py-0.5 text-center text-xs text-slate-200 focus:border-purple-500/50 focus:outline-none disabled:opacity-50"
            :title="trainingMode ? t('sim.training_change_off_first') : t('sim.training_rate_tooltip')"
          />
          <button
            type="button"
            class="rounded-md px-2 py-1 text-xs font-semibold transition-all"
            :class="trainingMode
              ? 'bg-purple-600 text-white shadow-sm shadow-purple-900/40 animate-pulse'
              : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'"
            :disabled="busy"
            :title="trainingMode
              ? t('sim.stop_generator', { session: sessionShort })
              : t('sim.training_24_7')"
            @click="toggleTrainingMode"
          >
            <span v-if="trainingMode">🤖 ON · {{ trainingRate }}/min</span>
            <span v-else>🤖 {{ t('sim.auto') }}</span>
          </button>
        </div>
      </div>

      <!-- OSRM status -->
      <div v-if="osrm" class="ml-auto hidden items-center gap-1.5 text-[11px] lg:flex">
        <span class="h-2 w-2 rounded-full" :class="osrm.ready ? 'bg-emerald-400' : 'bg-amber-400 animate-pulse'" />
        <span :class="osrm.ready ? 'text-emerald-500/80' : 'text-amber-500/80'">
          {{ osrm.ready ? t('sim.routes_street') : t('sim.osrm_loading') }}
        </span>
      </div>
    </div>
    <div v-if="trainingMode" class="mt-2 flex items-center gap-2 text-[11px] text-purple-300/80">
      <span class="h-1.5 w-1.5 rounded-full bg-purple-400 animate-pulse" />
      <span>{{ t('sim.training_pulse') }} <code class="text-purple-200">{{ sessionShort }}</code></span>
    </div>
  </div>
</template>
