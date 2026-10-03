<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useSimulationStore } from "@/stores/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const { state, selectedAmbulanceId } = storeToRefs(store);

const busy = ref(false);
const emergencyTitle = ref("");
const assignEmergencyId = ref("");

const pendingEmergencies = computed(() =>
  (state.value?.emergencies ?? []).filter((e) => e.status === "pending"),
);

async function withBusy<T>(fn: () => Promise<T>) {
  busy.value = true;
  try {
    await fn();
  } finally {
    busy.value = false;
  }
}

async function addEmergencyDemo() {
  await withBusy(async () => {
    await store.createEmergency(40.421, -3.695, emergencyTitle.value || t("scenario_panel.emergency_demo_default"));
    toast.success(t("scenario_panel.emergency_created"));
  });
}

const DEMO_JAM: [number, number][] = [
  [40.415, -3.708],
  [40.418, -3.708],
  [40.418, -3.704],
  [40.415, -3.704],
];

async function addJamDemo() {
  await withBusy(async () => {
    await store.createJamFromPolygon(DEMO_JAM);
    toast.success(t("scenario_panel.jam_added"));
  });
}

async function assignSelected() {
  const amb = selectedAmbulanceId.value;
  const em = assignEmergencyId.value;
  if (!amb || !em) {
    toast.message(t("scenario_panel.select_amb_em"));
    return;
  }
  await withBusy(async () => {
    await store.assignEmergency(amb, em);
    toast.success(t("scenario_panel.route_assigned"));
  });
}
</script>

<template>
  <div class="space-y-3 rounded-xl border border-slate-700 bg-slate-900/90 p-4">
    <h3 class="text-sm font-semibold text-slate-200">{{ t('scenario_panel.title') }}</h3>
    <p class="text-xs text-slate-500">
      {{ t('scenario_panel.desc') }}
    </p>
    <div class="flex flex-col gap-2">
      <label class="text-xs text-slate-500">{{ t('scenario_panel.emergency_title_label') }}</label>
      <input
        v-model="emergencyTitle"
        type="text"
        class="rounded-lg border border-slate-600 bg-slate-950 px-3 py-2 text-sm text-slate-200"
        :placeholder="t('scenario_panel.emergency_demo_default')"
      />
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="rounded-lg bg-rose-600 px-3 py-2 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-50"
          :disabled="busy"
          :title="t('scenario_panel.add_emergency_tooltip')"
          @click="addEmergencyDemo"
        >
          {{ t('scenario_panel.add_emergency') }}
        </button>
        <button
          type="button"
          class="rounded-lg border border-rose-500/60 px-3 py-2 text-sm text-rose-300 hover:bg-rose-500/10 disabled:opacity-50"
          :disabled="busy"
          :title="t('scenario_panel.add_jam_tooltip')"
          @click="addJamDemo"
        >
          {{ t('scenario_panel.add_jam') }}
        </button>
      </div>
    </div>
    <div class="border-t border-slate-800 pt-3">
      <p class="mb-2 text-xs text-slate-500">{{ t('scenario_panel.assign_label') }}</p>
      <select
        v-model="assignEmergencyId"
        class="mb-2 w-full rounded-lg border border-slate-600 bg-slate-950 px-3 py-2 text-sm text-slate-200"
      >
        <option disabled value="">{{ t('scenario_panel.select_emergency') }}</option>
        <option v-for="e in pendingEmergencies" :key="e.id" :value="e.id">
          {{ e.id }} · {{ e.title }} ({{ e.status }})
        </option>
      </select>
      <button
        type="button"
        class="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        :disabled="busy || !assignEmergencyId || !selectedAmbulanceId"
        @click="assignSelected"
      >
        {{ t('scenario_panel.calc_assign') }}
      </button>
    </div>
  </div>
</template>
