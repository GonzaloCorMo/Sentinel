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
/** Emergencias por hora; vacío = equilibrada según la flota. */
const rateInput = ref<number | "">("");
const balancedPerHour = computed(() => state.value?.balancedRatePerHour ?? null);
const fixedRate = computed(() => typeof rateInput.value === "number" && rateInput.value > 0);

const presets = [1, 2, 5, 10, 20];

async function toggleTrainingMode() {
  busy.value = true;
  try {
    const next = !trainingMode.value;
    const perMin = next && fixedRate.value ? Number(rateInput.value) / 60 : undefined;
    await store.setTrainingMode(next, perMin);
    toast[next ? "success" : "info"](
      next
        ? fixedRate.value
          ? t("sim.training_on", { rate: rateInput.value })
          : t("sim.training_on_balanced", { rate: balancedPerHour.value ?? "—" })
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
  <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
    <!-- Reproducir / pausar + reiniciar -->
    <div class="flex items-center gap-1">
      <button
        type="button"
        class="flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-medium"
        :class="paused
          ? 'bg-slate-100 text-slate-950 hover:bg-slate-300'
          : 'border border-slate-700 text-slate-100 hover:bg-slate-800'"
        :disabled="busy"
        :title="paused ? t('sim.resume_tooltip') : t('sim.pause_tooltip')"
        @click="toggle"
      >
        <svg v-if="paused" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6zM14 4h4v16h-4z" /></svg>
        {{ paused ? t('sim.resume_tooltip') : t('sim.pause_tooltip') }}
      </button>
      <button
        type="button"
        class="flex h-8 w-8 items-center justify-center rounded border border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-slate-100 disabled:opacity-40"
        :title="t('sim.reset_tooltip')"
        :aria-label="t('sim.reset_tooltip')"
        :disabled="busy"
        @click="reset"
      >
        <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      </button>
    </div>

    <!-- Velocidad -->
    <div class="flex items-center gap-2">
      <span class="text-[11px] text-slate-500">{{ t('sim.speed') }}</span>
      <div class="flex rounded border border-slate-800 p-0.5">
        <button
          v-for="p in presets"
          :key="p"
          type="button"
          class="rounded-sm px-2 py-0.5 font-mono text-xs"
          :class="Math.abs(speed - p) < 0.01 ? 'bg-slate-100 text-slate-950' : 'text-slate-400 hover:text-slate-100'"
          :title="t('sim.speed_n', { n: p })"
          :disabled="busy"
          @click="setSpeed(p)"
        >{{ p }}×</button>
      </div>
      <span class="font-mono text-[11px] text-slate-500" :title="t('sim.tick')">{{ t('sim.tick') }} {{ tick }}</span>
    </div>

    <!-- Emergencias automáticas -->
    <div class="flex items-center gap-2" :title="t('sim.training_24_7')">
      <span class="text-[11px] text-slate-500">{{ t('sim.auto_ml') }}</span>
      <div class="flex items-center gap-1">
        <input
          v-model.number="rateInput"
          type="number"
          min="1"
          max="600"
          step="1"
          :placeholder="t('sim.rate_balanced_short')"
          :disabled="busy || trainingMode"
          class="w-14 rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 text-center font-mono text-xs text-slate-200 focus:border-slate-600 focus:outline-none disabled:opacity-50"
          :title="trainingMode ? t('sim.training_change_off_first') : t('sim.training_rate_tooltip')"
          :aria-label="t('sim.training_rate_tooltip')"
        />
        <span class="text-[11px] text-slate-500">/h</span>
        <span
          v-if="!fixedRate && balancedPerHour != null"
          class="font-mono text-[11px] text-slate-500"
          :title="t('sim.rate_balanced_tooltip')"
        >≈{{ balancedPerHour }}</span>
        <button
          type="button"
          class="ml-1 flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs"
          :class="trainingMode ? 'border-amber-500/40 text-amber-300' : 'border-slate-700 text-slate-300 hover:bg-slate-800'"
          :disabled="busy"
          @click="toggleTrainingMode"
        >
          <span v-if="trainingMode" class="h-1.5 w-1.5 rounded-full bg-amber-400" />
          {{ trainingMode ? t('sim.stop_generator') : t('sim.auto') }}
        </button>
      </div>
    </div>
  </div>
</template>
