<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useEntitiesStore } from "@/stores/entities";
import type { EntityType } from "@/types/simulation";
import { sanitizeSvg } from "@/lib/sanitize";

const { t } = useI18n();
const store = useEntitiesStore();

const activeTab = ref<"vehicles" | "places" | "dispatch">("vehicles");

onMounted(async () => {
  await Promise.all([store.fetchEntityTypes(), store.fetchDispatchConfig()]);
});

const vehicleTypes = computed(() => store.entityTypes.filter((t) => t.kind === "vehicle"));
const placeTypes = computed(() => store.entityTypes.filter((t) => t.kind === "place"));

const newVehicle = ref({
  name: "",
  speedKmh: 60,
  color: "#94a3b8",
  iconSvg: null as string | null,
  description: "",
  capabilities: "",
  powertrain: "combustion" as "combustion" | "electric" | "unique",
  crewMin: 1,
  crewMax: 1,
  costPerMin: 0,
  activationCost: 0,
});

const newPlace = ref({
  name: "",
  color: "#94a3b8",
  iconSvg: null as string | null,
  description: "",
  capabilities: "",
});

const confirmDeleteId = ref<string | null>(null);

// ── Editor (compartido entre vehículos y lugares) ───────────────────────
const editor = ref<{
  open: boolean;
  id: string | null;
  kind: "vehicle" | "place";
  name: string;
  speedKmh: number;
  color: string;
  iconSvg: string | null;
  description: string;
  capabilities: string;
  powertrain: "combustion" | "electric" | "unique";
  crewMin: number;
  crewMax: number;
  costPerMin: number;
  activationCost: number;
  saving: boolean;
}>({
  open: false, id: null, kind: "vehicle", name: "", speedKmh: 60, color: "#94a3b8",
  iconSvg: null, description: "", capabilities: "",
  powertrain: "combustion", crewMin: 1, crewMax: 1, costPerMin: 0, activationCost: 0,
  saving: false,
});

function openEdit(t: EntityType) {
  const caps = Array.isArray(t.capabilities)
    ? (t.capabilities as string[]).join(", ")
    : (typeof t.capabilities === "string" ? t.capabilities : "");
  editor.value = {
    open: true,
    id: t.id,
    kind: (t.kind as "vehicle" | "place"),
    name: t.name,
    speedKmh: t.speedKmh ?? 60,
    color: t.color,
    iconSvg: t.iconSvg ?? null,
    description: t.description || "",
    capabilities: caps,
    powertrain: (t.powertrain as "combustion" | "electric" | "unique") ?? "combustion",
    crewMin: t.crewMin ?? 1,
    crewMax: t.crewMax ?? 1,
    costPerMin: t.costPerMin ?? 0,
    activationCost: t.activationCost ?? 0,
    saving: false,
  };
}

function closeEditor() { editor.value.open = false; }

function readEditorSvg(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  if (!file.name.endsWith(".svg") && file.type !== "image/svg+xml") {
    toast.error("Solo se permiten archivos SVG");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => { editor.value.iconSvg = reader.result as string; };
  reader.readAsText(file);
}

async function saveEditor() {
  const ed = editor.value;
  if (!ed.id || !ed.name.trim()) return;
  ed.saving = true;
  const capsList = ed.capabilities
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  // Enviamos "" / [] explícitos cuando el usuario vacía los campos: backend
  // los interpreta como "pide regenerar con IA".
  const patch: any = {
    name: ed.name.trim(),
    color: ed.color,
    iconSvg: ed.iconSvg,
    description: ed.description.trim(),
    capabilities: capsList,
  };
  if (ed.kind === "vehicle") {
    if (ed.crewMax < ed.crewMin) {
      toast.error(t("scenario.crew_invalid"));
      ed.saving = false;
      return;
    }
    patch.speedKmh = ed.speedKmh;
    patch.powertrain = ed.powertrain;
    patch.crewMin = ed.crewMin;
    patch.crewMax = ed.crewMax;
    patch.costPerMin = ed.costPerMin;
    patch.activationCost = ed.activationCost;
  }
  try {
    await store.updateEntityType(ed.id, patch);
    toast.success(`"${ed.name}" actualizado`);
    closeEditor();
  } catch {
    toast.error("Error al actualizar");
  } finally {
    ed.saving = false;
  }
}

function readSvgFile(e: Event, target: "vehicle" | "place") {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  if (!file.name.endsWith(".svg") && file.type !== "image/svg+xml") {
    toast.error("Solo se permiten archivos SVG");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const svg = reader.result as string;
    if (target === "vehicle") newVehicle.value.iconSvg = svg;
    else newPlace.value.iconSvg = svg;
  };
  reader.readAsText(file);
}

async function addVehicle() {
  if (!newVehicle.value.name.trim()) {
    toast.error("El nombre es obligatorio");
    return;
  }
  if (newVehicle.value.crewMax < newVehicle.value.crewMin) {
    toast.error(t("scenario.crew_invalid"));
    return;
  }
  try {
    await store.createEntityType({
      name: newVehicle.value.name,
      kind: "vehicle",
      speedKmh: newVehicle.value.speedKmh,
      color: newVehicle.value.color,
      iconSvg: newVehicle.value.iconSvg,
      description: newVehicle.value.description.trim() || null,
      capabilities: newVehicle.value.capabilities.trim() || null,
      powertrain: newVehicle.value.powertrain,
      crewMin: newVehicle.value.crewMin,
      crewMax: newVehicle.value.crewMax,
      costPerMin: newVehicle.value.costPerMin,
      activationCost: newVehicle.value.activationCost,
    });
    toast.success(`Vehiculo "${newVehicle.value.name}" creado`);
    newVehicle.value = {
      name: "", speedKmh: 60, color: "#94a3b8", iconSvg: null, description: "", capabilities: "",
      powertrain: "combustion", crewMin: 1, crewMax: 1, costPerMin: 0, activationCost: 0,
    };
  } catch {
    toast.error("Error al crear vehiculo");
  }
}

async function addPlace() {
  if (!newPlace.value.name.trim()) {
    toast.error("El nombre es obligatorio");
    return;
  }
  try {
    await store.createEntityType({
      name: newPlace.value.name,
      kind: "place",
      color: newPlace.value.color,
      iconSvg: newPlace.value.iconSvg,
      description: newPlace.value.description.trim() || null,
      capabilities: newPlace.value.capabilities.trim() || null,
    });
    toast.success(`Lugar "${newPlace.value.name}" creado`);
    newPlace.value = { name: "", color: "#94a3b8", iconSvg: null, description: "", capabilities: "" };
  } catch {
    toast.error(t("scenario.create_place_failed"));
  }
}

async function removeType(et: EntityType) {
  if (confirmDeleteId.value !== et.id) {
    confirmDeleteId.value = et.id;
    return;
  }
  confirmDeleteId.value = null;
  try {
    await store.removeEntityType(et.id);
    toast.success(t("scenario.delete_ok", { name: et.name }));
  } catch {
    toast.error(t("scenario.delete_failed"));
  }
}

function cancelDelete() {
  confirmDeleteId.value = null;
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h2 class="text-lg font-semibold tracking-tight text-slate-100">{{ t('scenario.config_title') }}</h2>
      <p class="mt-1 text-xs text-slate-500">{{ t('scenario.config_subtitle') }}</p>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 rounded-xl border border-slate-700/50 bg-slate-950 p-1">
      <button
        v-for="tab in [
          { key: 'vehicles', label: t('scenario.tab_vehicles') },
          { key: 'places', label: t('scenario.tab_places') },
          { key: 'dispatch', label: t('scenario.tab_dispatch') },
        ]"
        :key="tab.key"
        class="flex-1 rounded-lg px-3 py-2 text-xs font-medium transition-colors"
        :class="activeTab === tab.key
          ? 'bg-slate-100 text-slate-950'
          : 'text-slate-400 hover:bg-slate-800/80 hover:text-slate-200'"
        @click="activeTab = tab.key as 'vehicles' | 'places' | 'dispatch'"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- Vehicle Types Tab -->
    <div v-if="activeTab === 'vehicles'" class="space-y-4">
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div
          v-for="v in vehicleTypes"
          :key="v.id"
          class="relative rounded border bg-slate-900 p-3 transition-colors"
          :class="confirmDeleteId === v.id ? 'border-red-500/60' : 'border-slate-800'"
        >
          <div class="flex items-center gap-3">
            <div class="relative flex h-9 w-9 shrink-0 items-center justify-center rounded border border-slate-700 bg-slate-950 text-slate-200">
              <span v-if="v.iconSvg" class="h-4 w-4 [&>svg]:h-full [&>svg]:w-full" v-html="sanitizeSvg(v.iconSvg)" />
              <span v-else class="font-mono text-sm">{{ v.name.charAt(0) }}</span>
              <span class="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full" :style="{ background: v.color }" />
            </div>
            <div class="flex-1 min-w-0">
              <p class="text-sm font-semibold text-slate-200 truncate">{{ v.name }}</p>
              <p class="text-[10px] text-slate-500">
                {{ v.speedKmh ?? '—' }} km/h · {{ v.builtIn ? t('scenario.builtin') : t('scenario.custom') }}
              </p>
              <p v-if="v.powertrain || v.crewMin != null" class="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
                <span v-if="v.powertrain" class="inline-flex items-center gap-1 rounded-md bg-slate-800/80 px-1.5 py-0.5">
                  <span>{{ v.powertrain === 'electric' ? '⚡' : v.powertrain === 'unique' ? '🛸' : '⛽' }}</span>
                  <span class="text-slate-300">{{ t('scenario.powertrain_' + v.powertrain) }}</span>
                </span>
                <span v-if="v.crewMin != null" class="inline-flex items-center gap-1 rounded-md bg-slate-800/80 px-1.5 py-0.5 text-slate-300">
                  <span class="text-slate-500">{{ t('scenario.crew_label') }}:</span>
                  <span class="font-mono">{{ v.crewMin === v.crewMax ? v.crewMin : `${v.crewMin}-${v.crewMax}` }}</span>
                </span>
              </p>
              <p v-if="v.costPerMin != null || v.activationCost != null" class="text-[10px] text-emerald-300/80 font-mono">
                <span v-if="v.costPerMin != null">{{ v.costPerMin.toFixed(2) }} {{ t('scenario.cost_per_min_short') }}</span>
                <span v-if="v.costPerMin != null && v.activationCost != null" class="text-slate-600"> · </span>
                <span v-if="v.activationCost != null">{{ v.activationCost.toFixed(0) }}€ {{ t('scenario.activation_cost_short') }}</span>
              </p>
              <p v-if="v.description" class="text-[10px] text-slate-500 truncate mt-0.5" :title="v.description">{{ v.description }}</p>
              <p v-if="v.capabilities" class="text-[10px] text-cyan-500/70 truncate" :title="v.capabilities">{{ v.capabilities }}</p>
            </div>
            <div class="flex flex-col gap-1 shrink-0">
              <template v-if="confirmDeleteId === v.id">
                <button
                  class="rounded-lg bg-rose-600 px-2 py-1 text-[10px] font-semibold text-white transition hover:bg-rose-500"
                  @click="removeType(v)"
                >{{ t('scenario.confirm') }}</button>
                <button
                  class="rounded-lg border border-slate-600 px-2 py-1 text-[10px] font-medium text-slate-400 transition hover:bg-slate-800"
                  @click="cancelDelete"
                >{{ t('scenario.cancel') }}</button>
              </template>
              <template v-else>
                <button
                  v-if="!v.builtIn"
                  class="rounded-lg border border-emerald-600/30 bg-emerald-950/20 p-1.5 text-emerald-400 transition hover:bg-emerald-900/40"
                  :title="t('scenario.edit')"
                  @click="openEdit(v)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                </button>
                <button
                  class="rounded-lg border border-rose-600/30 bg-rose-950/20 p-1.5 text-rose-400 transition hover:bg-rose-900/40"
                  :title="t('scenario.delete')"
                  @click="removeType(v)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- Add vehicle form -->
      <div class="rounded-xl border border-slate-700/50 bg-slate-900/70 p-5 space-y-4">
        <h3 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">Nuevo vehiculo / unidad</h3>
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Nombre *</label>
            <input
              v-model="newVehicle.name"
              type="text"
              :placeholder="t('scenario.vehicle_name_ph')"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Velocidad (km/h)</label>
            <input
              v-model.number="newVehicle.speedKmh"
              type="number"
              min="5"
              max="900"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.color') }}</label>
            <div class="flex items-center gap-2">
              <input
                v-model="newVehicle.color"
                type="color"
                class="h-9 w-14 cursor-pointer rounded-lg border border-slate-700 bg-slate-800 p-1"
              />
              <span class="text-xs text-slate-500 font-mono">{{ newVehicle.color }}</span>
            </div>
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Icono SVG (opcional)</label>
            <div class="flex items-center gap-2">
              <label class="cursor-pointer rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-400 transition hover:bg-slate-700 hover:text-slate-200">
                <span>{{ newVehicle.iconSvg ? '✓ SVG cargado' : 'Subir .svg' }}</span>
                <input type="file" accept=".svg,image/svg+xml" class="hidden" @change="readSvgFile($event, 'vehicle')" />
              </label>
              <button
                v-if="newVehicle.iconSvg"
                class="text-xs text-rose-400 hover:text-rose-300"
                @click="newVehicle.iconSvg = null"
              >Quitar</button>
            </div>
            <div
              v-if="newVehicle.iconSvg"
              class="mt-2 flex h-10 w-10 items-center justify-center rounded-full border border-slate-700 bg-slate-800 p-1.5 [&>svg]:h-full [&>svg]:w-full"
              v-html="sanitizeSvg(newVehicle.iconSvg)"
            />
          </div>
          <div class="sm:col-span-2">
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.description') }}</label>
            <textarea
              v-model="newVehicle.description"
              rows="2"
              :placeholder="t('scenario.vehicle_desc_ph')"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none resize-none"
            />
          </div>
          <div class="sm:col-span-2">
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Capacidades / Equipamiento</label>
            <input
              v-model="newVehicle.capabilities"
              type="text"
              :placeholder="t('scenario.vehicle_caps_ph')"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.powertrain') }}</label>
            <select
              v-model="newVehicle.powertrain"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
            >
              <option value="combustion">⛽ {{ t('scenario.powertrain_combustion') }}</option>
              <option value="electric">⚡ {{ t('scenario.powertrain_electric') }}</option>
              <option value="unique">🛸 {{ t('scenario.powertrain_unique') }}</option>
            </select>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.crew_min') }}</label>
              <input
                v-model.number="newVehicle.crewMin"
                type="number" min="0" max="20"
                class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div>
              <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.crew_max') }}</label>
              <input
                v-model.number="newVehicle.crewMax"
                type="number" min="0" max="20"
                class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.cost_per_min') }}</label>
            <input
              v-model.number="newVehicle.costPerMin"
              type="number" min="0" step="0.01"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.activation_cost') }}</label>
            <input
              v-model.number="newVehicle.activationCost"
              type="number" min="0" step="0.01"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
            />
          </div>
        </div>
        <button
          class="rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-slate-300"
          @click="addVehicle"
        >
          Añadir vehiculo
        </button>
      </div>
    </div>

    <!-- Place Types Tab -->
    <div v-if="activeTab === 'places'" class="space-y-4">
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div
          v-for="p in placeTypes"
          :key="p.id"
          class="relative rounded border bg-slate-900 p-3 transition-colors"
          :class="confirmDeleteId === p.id ? 'border-red-500/60' : 'border-slate-800'"
        >
          <div class="flex items-center gap-3">
            <div class="relative flex h-9 w-9 shrink-0 items-center justify-center rounded border border-slate-700 bg-slate-950 text-slate-200">
              <span v-if="p.iconSvg" class="h-4 w-4 [&>svg]:h-full [&>svg]:w-full" v-html="sanitizeSvg(p.iconSvg)" />
              <span v-else class="font-mono text-sm">{{ p.name.charAt(0) }}</span>
              <span class="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full" :style="{ background: p.color }" />
            </div>
            <div class="flex-1 min-w-0">
              <p class="text-sm font-semibold text-slate-200 truncate">{{ p.name }}</p>
              <p class="text-[10px] text-slate-500">{{ p.builtIn ? 'Integrado' : 'Personalizado' }}</p>
              <p v-if="p.description" class="text-[10px] text-slate-500 truncate mt-0.5" :title="p.description">{{ p.description }}</p>
              <p v-if="p.capabilities" class="text-[10px] text-cyan-500/70 truncate" :title="p.capabilities">{{ p.capabilities }}</p>
            </div>
            <div class="flex flex-col gap-1 shrink-0">
              <template v-if="confirmDeleteId === p.id">
                <button
                  class="rounded-lg bg-rose-600 px-2 py-1 text-[10px] font-semibold text-white transition hover:bg-rose-500"
                  @click="removeType(p)"
                >{{ t('scenario.confirm') }}</button>
                <button
                  class="rounded-lg border border-slate-600 px-2 py-1 text-[10px] font-medium text-slate-400 transition hover:bg-slate-800"
                  @click="cancelDelete"
                >{{ t('scenario.cancel') }}</button>
              </template>
              <template v-else>
                <button
                  v-if="!p.builtIn"
                  class="rounded-lg border border-emerald-600/30 bg-emerald-950/20 p-1.5 text-emerald-400 transition hover:bg-emerald-900/40"
                  :title="t('scenario.edit')"
                  @click="openEdit(p)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                </button>
                <button
                  class="rounded-lg border border-rose-600/30 bg-rose-950/20 p-1.5 text-rose-400 transition hover:bg-rose-900/40"
                  :title="t('scenario.delete')"
                  @click="removeType(p)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- Add place form -->
      <div class="rounded-xl border border-slate-700/50 bg-slate-900/70 p-5 space-y-4">
        <h3 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">{{ t('scenario.new_place_title') }}</h3>
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Nombre *</label>
            <input
              v-model="newPlace.name"
              type="text"
              :placeholder="t('scenario.place_name_ph')"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.color') }}</label>
            <div class="flex items-center gap-2">
              <input
                v-model="newPlace.color"
                type="color"
                class="h-9 w-14 cursor-pointer rounded-lg border border-slate-700 bg-slate-800 p-1"
              />
              <span class="text-xs text-slate-500 font-mono">{{ newPlace.color }}</span>
            </div>
          </div>
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Icono SVG (opcional)</label>
            <div class="flex items-center gap-2">
              <label class="cursor-pointer rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-400 transition hover:bg-slate-700 hover:text-slate-200">
                <span>{{ newPlace.iconSvg ? '✓ SVG cargado' : 'Subir .svg' }}</span>
                <input type="file" accept=".svg,image/svg+xml" class="hidden" @change="readSvgFile($event, 'place')" />
              </label>
              <button
                v-if="newPlace.iconSvg"
                class="text-xs text-rose-400 hover:text-rose-300"
                @click="newPlace.iconSvg = null"
              >Quitar</button>
            </div>
            <div
              v-if="newPlace.iconSvg"
              class="mt-2 flex h-10 w-10 items-center justify-center rounded-lg border border-slate-700 bg-slate-800 p-1.5 [&>svg]:h-full [&>svg]:w-full"
              v-html="newPlace.iconSvg"
            />
          </div>
          <div class="sm:col-span-2">
            <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.description') }}</label>
            <textarea
              v-model="newPlace.description"
              rows="2"
              :placeholder="t('scenario.place_desc_ph')"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none resize-none"
            />
          </div>
          <div class="sm:col-span-2">
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Capacidades / Servicios</label>
            <input
              v-model="newPlace.capabilities"
              type="text"
              :placeholder="t('scenario.place_caps_ph')"
              class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none"
            />
          </div>
        </div>
        <button
          class="rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-slate-300"
          @click="addPlace"
        >
          Añadir lugar
        </button>
      </div>
    </div>

    <!-- Editor modal (vehículos / lugares) -->
    <div
      v-if="editor.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      @click.self="closeEditor"
    >
      <div class="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl overflow-hidden">
        <div class="flex items-center justify-between border-b border-slate-800 px-5 py-3">
          <h3 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">
            Editar {{ editor.kind === "vehicle" ? "vehículo / unidad" : "lugar" }}
          </h3>
          <button class="text-slate-500 hover:text-slate-200" @click="closeEditor">✕</button>
        </div>
        <div class="p-5 space-y-4">
          <div class="grid gap-4 sm:grid-cols-2">
            <div :class="editor.kind === 'place' ? 'sm:col-span-2' : ''">
              <label class="block text-[11px] font-medium text-slate-400 mb-1">Nombre *</label>
              <input
                v-model="editor.name"
                type="text"
                class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div v-if="editor.kind === 'vehicle'">
              <label class="block text-[11px] font-medium text-slate-400 mb-1">Velocidad (km/h)</label>
              <input
                v-model.number="editor.speedKmh"
                type="number"
                min="5"
                max="900"
                class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div>
              <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.color') }}</label>
              <div class="flex items-center gap-2">
                <input
                  v-model="editor.color"
                  type="color"
                  class="h-9 w-14 cursor-pointer rounded-lg border border-slate-700 bg-slate-800 p-1"
                />
                <span class="text-xs text-slate-500 font-mono">{{ editor.color }}</span>
              </div>
            </div>
            <div :class="editor.kind === 'vehicle' ? '' : 'sm:col-span-1'">
              <label class="block text-[11px] font-medium text-slate-400 mb-1">Icono SVG</label>
              <div class="flex items-center gap-2">
                <label class="cursor-pointer rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-400 transition hover:bg-slate-700 hover:text-slate-200">
                  <span>{{ editor.iconSvg ? "✓ SVG cargado" : "Subir .svg" }}</span>
                  <input type="file" accept=".svg,image/svg+xml" class="hidden" @change="readEditorSvg" />
                </label>
                <button
                  v-if="editor.iconSvg"
                  class="text-xs text-rose-400 hover:text-rose-300"
                  @click="editor.iconSvg = null"
                >Quitar</button>
              </div>
            </div>
            <div class="sm:col-span-2">
              <label class="block text-[11px] font-medium text-slate-400 mb-1">
                Descripción
                <span class="ml-1 text-slate-600 font-normal">— vacío = IA la regenera</span>
              </label>
              <textarea
                v-model="editor.description"
                rows="2"
                class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none resize-none"
              />
            </div>
            <div class="sm:col-span-2">
              <label class="block text-[11px] font-medium text-slate-400 mb-1">
                Capacidades (separadas por coma)
                <span class="ml-1 text-slate-600 font-normal">— vacío = IA las regenera</span>
              </label>
              <input
                v-model="editor.capabilities"
                type="text"
                class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <template v-if="editor.kind === 'vehicle'">
              <div>
                <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.powertrain') }}</label>
                <select
                  v-model="editor.powertrain"
                  class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none"
                >
                  <option value="combustion">⛽ {{ t('scenario.powertrain_combustion') }}</option>
                  <option value="electric">⚡ {{ t('scenario.powertrain_electric') }}</option>
                  <option value="unique">🛸 {{ t('scenario.powertrain_unique') }}</option>
                </select>
              </div>
              <div class="grid grid-cols-2 gap-2">
                <div>
                  <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.crew_min') }}</label>
                  <input v-model.number="editor.crewMin" type="number" min="0" max="20"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
                </div>
                <div>
                  <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.crew_max') }}</label>
                  <input v-model.number="editor.crewMax" type="number" min="0" max="20"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
                </div>
              </div>
              <div>
                <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.cost_per_min') }}</label>
                <input v-model.number="editor.costPerMin" type="number" min="0" step="0.01"
                  class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
              </div>
              <div>
                <label class="block text-[11px] font-medium text-slate-400 mb-1">{{ t('scenario.activation_cost') }}</label>
                <input v-model.number="editor.activationCost" type="number" min="0" step="0.01"
                  class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-emerald-500 focus:outline-none" />
              </div>
            </template>
          </div>
        </div>
        <div class="flex justify-end gap-2 border-t border-slate-800 px-5 py-3">
          <button
            class="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-400 transition hover:bg-slate-800"
            @click="closeEditor"
          >{{ t('scenario.cancel') }}</button>
          <button
            class="rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-slate-300 disabled:opacity-50"
            :disabled="editor.saving || !editor.name.trim()"
            @click="saveEditor"
          >{{ editor.saving ? "…" : t('common.save') }}</button>
        </div>
      </div>
    </div>

    <!-- Dispatch Config Tab -->
    <div v-if="activeTab === 'dispatch'" class="space-y-4">
      <div class="rounded-xl border border-slate-700/50 bg-slate-900/70 p-5 space-y-4">
        <h3 class="text-[11px] font-medium uppercase tracking-wider text-slate-400">{{ t('scenario.dispatch_config') }}</h3>
        <div class="flex items-start gap-3">
          <button
            class="relative mt-0.5 inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
            :class="store.dispatchConfig.dispatchRequiresApproval ? 'bg-emerald-500' : 'bg-slate-700'"
            @click="store.setDispatchConfig(!store.dispatchConfig.dispatchRequiresApproval)"
          >
            <span
              class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out"
              :class="store.dispatchConfig.dispatchRequiresApproval ? 'translate-x-5' : 'translate-x-0'"
            />
          </button>
          <div>
            <p class="text-sm font-medium text-slate-200">Despacho requiere aprobacion (HITL)</p>
            <p class="mt-1 text-xs text-slate-500 leading-relaxed">
              Cuando esta activo, las nuevas emergencias <strong class="text-slate-400">no</strong> se asignan automaticamente.
              En su lugar, la IA crea una propuesta que el operador debe aprobar antes de despachar la ambulancia mas cercana.
              Esto se aplica solo al <em>despacho inicial</em>; las acciones reactivas (helicoptero, policia) siguen el modo IA configurado.
            </p>
            <div class="mt-2 inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[10px] font-semibold uppercase"
              :class="store.dispatchConfig.dispatchRequiresApproval
                ? 'border-amber-600/40 bg-amber-950/30 text-amber-300'
                : 'border-green-600/40 bg-green-950/30 text-green-300'"
            >
              <span class="h-1.5 w-1.5 rounded-full" :class="store.dispatchConfig.dispatchRequiresApproval ? 'bg-amber-400' : 'bg-green-400'" />
              {{ store.dispatchConfig.dispatchRequiresApproval ? 'Aprobacion manual' : 'Despacho automatico' }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
