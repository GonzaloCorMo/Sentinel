<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import AmbulanceMap from "@/components/dashboard/AmbulanceMap.vue";
import SimulationControls from "@/components/dashboard/SimulationControls.vue";
import StatusChip from "@/components/ui/StatusChip.vue";
import { unitStatus } from "@/lib/unitStatus";
import { energyOf, operatingCostOf, powertrainOf } from "@/lib/energyDisplay";
import { useBuilderStore, type BuilderTool } from "@/stores/builder";
import { useEntitiesStore } from "@/stores/entities";
import { useSimulationStore } from "@/stores/simulation";
import type { EntityType, MapTool } from "@/types/simulation";

const { t } = useI18n();
const store = useSimulationStore();
const builder = useBuilderStore();
const entities = useEntitiesStore();
const { state, selectedAmbulanceId, selectedCompanionId, uiFilters } = storeToRefs(store);

// Cálculo reactivo de unidades que cumplen filtros ⌘ comando
function ambMatchesUi(a: any): boolean {
  const ui = uiFilters.value as Record<string, unknown>;
  if (!ui || !Object.keys(ui).length) return true;
  const type = a.entityTypeId || "ambulance";
  if (ui.entityTypeId && type !== ui.entityTypeId) return false;
  if (ui.fuelBelow != null && (a.fuelLevel ?? 100) >= Number(ui.fuelBelow)) return false;
  if (ui.fuelAbove != null && (a.fuelLevel ?? 0) <= Number(ui.fuelAbove)) return false;
  if (ui.batteryBelow != null && (a.telemetry?.mechanical?.batteryPct ?? 100) >= Number(ui.batteryBelow)) return false;
  if (ui.hasPatient != null && !!a.hasPatient !== ui.hasPatient) return false;
  if (ui.severity && a.patientSeverity !== ui.severity) return false;
  if (ui.missionPhase && (a.missionPhase || "idle") !== ui.missionPhase) return false;
  if (ui.poweredOff != null && !!a.poweredOff !== ui.poweredOff) return false;
  return true;
}
const filteredMatches = computed(() => {
  const ambs = state.value?.ambulances ?? [];
  if (!Object.keys(uiFilters.value || {}).length) return [];
  return ambs.filter(ambMatchesUi);
});
const filterTotal = computed(() => state.value?.ambulances?.length ?? 0);

// Coste operativo total flota (suma activación + runtime de cada vehículo activado).
const fleetCost = computed(() => {
  const ambs = state.value?.ambulances ?? [];
  const types = state.value?.entityTypes;
  let activation = 0;
  let runtime = 0;
  let activeUnits = 0;
  for (const a of ambs) {
    const c = operatingCostOf(a, types);
    if (!c.available) continue;
    activation += c.activation;
    runtime += c.runtime;
    if (c.activation > 0 || c.runtime > 0) activeUnits++;
  }
  return { activation, runtime, total: activation + runtime, activeUnits };
});
function focusMatch(id: string) { store.selectAmbulance(id); }
const { currentBuilderTool, placeEntityId } = storeToRefs(builder);

const incidentCount = ref(5);
const aiGenTab = ref<"incidents" | "scenario">("incidents");
const generatingScenario = ref(false);

// ── Modo de colocación: unidad única o base con N unidades ──────────────
const placementMode = ref<"single" | "base">("single");
const baseCount = ref<number>(3);

async function spawnVehicleBatch(
  entityTypeId: string,
  typeName: string,
  lat: number,
  lng: number,
  count: number,
) {
  // Dispersa las unidades en un círculo pequeño (~60-120m) para verlas
  // separadas en el mapa aunque compartan base.
  const prefix = (typeName || "UNID").slice(0, 8).toUpperCase().replace(/\s+/g, "");
  let placed = 0;
  for (let i = 0; i < count; i++) {
    let lat_i = lat, lng_i = lng;
    if (count > 1) {
      const r = 0.0008 + Math.random() * 0.0012;          // ~90-225m
      const theta = (i / count) * 2 * Math.PI + Math.random() * 0.5;
      lat_i = lat + r * Math.cos(theta);
      lng_i = lng + r * Math.sin(theta);
    }
    const label = count > 1 ? `${prefix}-${String(i + 1).padStart(2, "0")}` : typeName;
    try {
      await store.spawnAmbulance(
        lat_i,
        lng_i,
        entityTypeId === "ambulance" ? undefined : entityTypeId,
        label,
      );
      placed++;
    } catch {
      /* continuar con los siguientes */
    }
  }
  toast.success(
    count === 1
      ? t("operations.single_placed", { name: typeName })
      : t("operations.base_placed", { name: typeName, placed, count }),
  );
}

const scenarioForm = ref<{
  hospitals: number;
  gasStations: number;
  ambulances: number;
  incidents: number;
  clearExisting: boolean;
  extraByType: Record<string, number>;
}>({
  hospitals: 2,
  gasStations: 2,
  ambulances: 4,
  incidents: 3,
  clearExisting: false,
  extraByType: {},
});

async function generateFullScenario() {
  generatingScenario.value = true;
  try {
    const extras: Record<string, number> = {};
    for (const [k, v] of Object.entries(scenarioForm.value.extraByType)) {
      const n = Number(v) || 0;
      if (n > 0) extras[k] = n;
    }
    const body = {
      hospitals: Math.max(0, scenarioForm.value.hospitals | 0),
      gasStations: Math.max(0, scenarioForm.value.gasStations | 0),
      ambulances: Math.max(0, scenarioForm.value.ambulances | 0),
      incidents: Math.max(0, scenarioForm.value.incidents | 0),
      clearExisting: scenarioForm.value.clearExisting,
      extraByType: extras,
    };
    const r = await fetch("/api/sim/generate-scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    toast.success(t("operations.scenario_generated"), {
      description: t("operations.scenario_generated_desc", {
        hospitals: j.placed?.hospitals ?? 0,
        stations: j.placed?.gasStations ?? 0,
        units: j.placed?.ambulances ?? 0,
        incidents: j.placed?.incidents ?? 0,
      }),
    });
    openMenu.value = null;
    await store.fetchState();
  } catch (e) {
    toast.error(t("operations.scenario_error"), { description: (e as Error).message });
  } finally {
    generatingScenario.value = false;
  }
}

async function generateIncidents() {
  try {
    await entities.generateIncidents(incidentCount.value);
    openMenu.value = null;
    await store.fetchState();
  } catch {
    /* handled in store */
  }
}

const emergencyTitle = ref("Emergencia");
const emergencyType = ref<"medical" | "altercation" | "mass_casualty">("medical");
const emergencyDescription = ref("");
const hospitalName = ref("Hospital");
const gasStationName = ref("Gasolinera");
const customPlaceName = ref("");

const objectSearch = ref("");

import { displayId } from "@/lib/vehicleId";
function shortId(id: string, idx?: number): string {
  const ambs = state.value?.ambulances;
  const amb = ambs?.find((a) => a.id === id);
  if (!amb) return id;
  return displayId(amb, idx, state.value?.entityTypes);
}

function ambIndex(id: string): number {
  const ambs = state.value?.ambulances;
  if (!ambs) return -1;
  return ambs.findIndex((a) => a.id === id);
}
const mapFullscreen = ref(false);
const mapBlockRef = ref<HTMLElement | null>(null);

let lastSpo2Alert = 0;

const selectedAmbulance = computed(() => {
  const ambs = state.value?.ambulances;
  if (!ambs?.length) return undefined;
  const id = selectedAmbulanceId.value;
  if (!id) return undefined;
  return ambs.find((a) => a.id === id);
});

const selectedCompanion = computed(() => {
  const comps = state.value?.companions;
  if (!comps?.length) return undefined;
  const id = selectedCompanionId.value;
  if (!id) return undefined;
  return comps.find((c) => c.id === id);
});

const entityTypesList = computed(() => state.value?.entityTypes ?? []);

function powertrainOfAmb(a: { entityTypeId?: string | null }): "combustion" | "electric" | "unique" {
  const etId = a.entityTypeId || "ambulance";
  const et = entityTypesList.value.find((e) => e.id === etId);
  return (et?.powertrain as "combustion" | "electric" | "unique") || "combustion";
}

function energyPctOfAmb(a: { entityTypeId?: string | null; fuelLevel?: number | null; batteryLevel?: number | null; telemetry?: { mechanical?: { batteryPct?: number | null; fuelLevelPct?: number | null } } }): number | null {
  const pt = powertrainOfAmb(a);
  if (pt === "electric") {
    return a.telemetry?.mechanical?.batteryPct ?? a.batteryLevel ?? null;
  }
  return a.telemetry?.mechanical?.fuelLevelPct ?? a.fuelLevel ?? null;
}

function energyLabelOfAmb(a: { entityTypeId?: string | null }): string {
  return powertrainOfAmb(a) === "electric" ? t("operations.battery") : t("operations.fuel");
}

const vehicleEntityTypes = computed(() => entityTypesList.value.filter((e) => e.kind === "vehicle"));

const placeEntityTypes = computed(() => entityTypesList.value.filter((e) => e.kind === "place"));

function isVehicleEntityActive(id: string): boolean {
  if (id === "ambulance") return currentBuilderTool.value === "add_ambulance";
  return currentBuilderTool.value === "add_vehicle" && builder.vehicleEntityId === id;
}

const unitsCountByType = computed<Record<string, number>>(() => {
  const acc: Record<string, number> = {};
  for (const a of state.value?.ambulances ?? []) {
    const id = a.entityTypeId || "ambulance";
    acc[id] = (acc[id] || 0) + 1;
  }
  for (const c of state.value?.companions ?? []) {
    const id = c.kind || "unknown";
    acc[id] = (acc[id] || 0) + 1;
  }
  return acc;
});
function countFor(typeId: string): number { return unitsCountByType.value[typeId] || 0; }
const placesCountByType = computed<Record<string, number>>(() => {
  const acc: Record<string, number> = {};
  for (const p of state.value?.pois ?? []) {
    acc[p.kind] = (acc[p.kind] || 0) + 1;
  }
  return acc;
});
function placeCountFor(typeId: string): number { return placesCountByType.value[typeId] || 0; }

function paletteSearchMatches(text: string): boolean {
  const q = objectSearch.value.trim().toLowerCase();
  if (!q) return true;
  return text.toLowerCase().includes(q);
}

const filteredVehicleEntities = computed(() =>
  vehicleEntityTypes.value.filter(
    (e) => paletteSearchMatches(`${e.name} ${e.id} ${e.description ?? ""} ${e.capabilities ?? ""}`),
  ),
);

const filteredPlaceEntities = computed(() =>
  placeEntityTypes.value.filter(
    (e) => paletteSearchMatches(`${e.name} ${e.id} ${e.description ?? ""}`),
  ),
);

function isPlacePaletteActive(etId: string): boolean {
  if (etId === "hospital") return currentBuilderTool.value === "add_hospital";
  if (etId === "gas_station") return currentBuilderTool.value === "add_gas_station";
  return currentBuilderTool.value === "add_place" && placeEntityId.value === etId;
}

function onPaletteStaticVehicle(id: "nav" | "emg" | "jam" | "del") {
  if (id === "nav") {
    builder.clearTool();
    return;
  }
  if (id === "emg") builder.setTool("add_emergency");
  else if (id === "jam") builder.setTool("add_traffic");
  else if (id === "del") builder.setTool("delete");
}

function onPaletteVehicleEntity(et: EntityType) {
  // Activa "colocar unidad": el siguiente clic en el mapa la instancia.
  builder.selectVehicleEntity(et.id);
}

// ── Menús desplegables de la barra (unidad / lugar / generar) ───────────
const openMenu = ref<null | "unit" | "place" | "generate">(null);
function toggleMenu(menu: "unit" | "place" | "generate") {
  openMenu.value = openMenu.value === menu ? null : menu;
}
function pickTool(id: "nav" | "emg" | "jam" | "del") {
  openMenu.value = null;
  onPaletteStaticVehicle(id);
}
function pickVehicle(et: EntityType) {
  openMenu.value = null;
  onPaletteVehicleEntity(et);
}
function pickPlace(et: EntityType) {
  openMenu.value = null;
  onPalettePlaceEntity(et);
}

function onPalettePlaceEntity(et: EntityType) {
  builder.selectPlaceEntity(et.id);
}

const missionPhaseLabel = computed<Record<string, string>>(() => ({
  idle: t("operations.phase_idle"),
  to_emergency: t("operations.phase_to_emergency"),
  to_refuel: t("operations.phase_to_refuel"),
  to_staging: t("operations.phase_to_staging"),
  to_hospital: t("operations.phase_to_hospital"),
  on_scene: t("status.on_scene"),
  at_hospital: t("status.handover"),
  refueling: t("status.refueling"),
}));

/** Minutos simulados que faltan para terminar la fase actual (en el lugar, transferencia, repostaje). */
function phaseMinutesLeft(amb: { phaseUntil?: number | null }): number | null {
  const now = state.value?.simTimeS;
  if (amb.phaseUntil == null || now == null) return null;
  return Math.max(1, Math.ceil((amb.phaseUntil - now) / 60));
}

function companionsForEmergency(emergencyId: string | null | undefined) {
  if (!emergencyId) return [];
  return state.value?.companions?.filter((c) => c.assignedEmergencyId === emergencyId) ?? [];
}

watch(
  () =>
    selectedAmbulance.value?.telemetry?.medical?.spo2Pct,
  (spo2) => {
    if (spo2 == null) return;
    if (spo2 < 93) {
      const now = Date.now();
      if (now - lastSpo2Alert > 25000) {
        lastSpo2Alert = now;
        toast.warning(t("operations.spo2_low_warn"), { description: `${spo2}%` });
      }
    }
  },
);

const destinationLabel = computed(() => {
  const amb = selectedAmbulance.value;
  const s = state.value;
  if (!amb || !s) return "—";
  const phase = amb.missionPhase;
  if (phase === "to_emergency" && amb.assignedEmergencyId) {
    const e = s.emergencies.find((x) => x.id === amb.assignedEmergencyId);
    return e ? e.title : String(amb.assignedEmergencyId);
  }
  const left = phaseMinutesLeft(amb);
  const leftTxt = left != null ? ` · ${t("operations.minutes_left", { n: left }, left)}` : "";
  if (phase === "on_scene") {
    const e = s.emergencies.find((x) => x.id === amb.assignedEmergencyId);
    return `${t("status.on_scene")}${e ? `: ${e.title}` : ""}${leftTxt}`;
  }
  if (phase === "at_hospital") {
    const p = s.pois.find((x) => x.id === amb.stagingHospitalId);
    return `${t("status.handover")}${p ? `: ${p.name}` : ""}${leftTxt}`;
  }
  if (phase === "refueling") {
    return `${t("status.refueling")}${leftTxt}`;
  }
  if (phase === "to_refuel" && amb.refuelPoiId) {
    const p = s.pois.find((x) => x.id === amb.refuelPoiId);
    return p ? t("operations.fuel_at_poi", { name: p.name }) : t("operations.fuel_no_poi");
  }
  if ((phase === "to_staging" || phase === "to_hospital") && amb.stagingHospitalId) {
    const p = s.pois.find((x) => x.id === amb.stagingHospitalId);
    if (phase === "to_hospital") {
      return p ? `${t("operations.phase_to_hospital")} → ${p.name}` : t("operations.phase_to_hospital");
    }
    return p ? t("operations.staging_at", { name: p.name }) : t("operations.staging_default");
  }
  return t("operations.no_destination");
});

const selectedPowertrain = computed(() => (selectedAmbulance.value ? powertrainOfAmb(selectedAmbulance.value) : "combustion"));
const selectedEnergyLabel = computed(() => (selectedAmbulance.value ? energyLabelOfAmb(selectedAmbulance.value) : t("operations.fuel")));
const selectedEnergyPct = computed(() => (selectedAmbulance.value ? energyPctOfAmb(selectedAmbulance.value) : null));

const severityLabel = computed<Record<string, string>>(() => ({
  stable: t("operations.severity_stable"),
  moderate: t("operations.severity_moderate"),
  critical: t("operations.severity_critical"),
}));

const mapTool = computed<MapTool>(() => {
  const tool = currentBuilderTool.value;
  if (!tool) return "none";
  switch (tool) {
    case "add_hospital":
      return "hospital";
    case "add_gas_station":
      return "gas";
    case "add_place":
      return "place";
    case "add_ambulance":
      return "ambulance";
    case "add_vehicle":
      return "vehicle";
    case "add_emergency":
      return "emergency";
    case "add_traffic":
      return "jam";
    case "delete":
      return "delete";
    default:
      return "none";
  }
});

function onTelemetryAmbulanceChange(ev: Event) {
  const v = (ev.target as HTMLSelectElement).value;
  store.selectAmbulance(v || null);
}

function onTelemetryCompanionChange(ev: Event) {
  const v = (ev.target as HTMLSelectElement).value;
  store.selectCompanion(v || null);
}

function onKeydown(ev: KeyboardEvent) {
  if (ev.key === "Escape") {
    openMenu.value = null;
    builder.clearTool();
  }
}

async function syncFullscreenFlag() {
  mapFullscreen.value = !!document.fullscreenElement;
  await nextTick();
  window.dispatchEvent(new Event("resize"));
}

async function toggleMapFullscreen() {
  const el = mapBlockRef.value;
  if (!el) return;
  try {
    if (!document.fullscreenElement) {
      await el.requestFullscreen();
    } else {
      await document.exitFullscreen();
    }
  } catch {
    /* ignore */
  }
}

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  document.addEventListener("fullscreenchange", syncFullscreenFlag);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown);
  document.removeEventListener("fullscreenchange", syncFullscreenFlag);
});

const toolbarItems = computed<{ tool: BuilderTool; label: string; iconPath: string; cls: string; activeCls: string; titleHint: string }[]>(() => [
  {
    tool: null,
    label: t("operations.tool_navigate"),
    iconPath: "M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122",
    cls: "border-slate-600 text-slate-400",
    activeCls: "border-emerald-500/50 bg-emerald-950/30 text-emerald-300",
    titleHint: t("operations.tool_navigate_hint"),
  },
  {
    tool: "add_hospital",
    label: t("operations.tool_hospital"),
    iconPath: "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4",
    cls: "border-violet-500/50 text-violet-400",
    activeCls: "border-violet-400 bg-violet-950/40 text-violet-300",
    titleHint: t("operations.tool_hospital_hint"),
  },
  {
    tool: "add_gas_station",
    label: t("operations.tool_gas"),
    iconPath: "M13 10V3L4 14h7v7l9-11h-7z",
    cls: "border-sky-500/50 text-sky-400",
    activeCls: "border-sky-400 bg-sky-950/40 text-sky-300",
    titleHint: t("operations.tool_gas_hint"),
  },
  {
    tool: "add_ambulance",
    label: t("operations.tool_ambulance"),
    iconPath: "M9 17a2 2 0 11-4 0 2 2 0 014 0zM19 17a2 2 0 11-4 0 2 2 0 014 0z M13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10a1 1 0 001 1h1m8-1a1 1 0 01-1 1H9m4-1V8a1 1 0 011-1h2.586a1 1 0 01.707.293l3.414 3.414a1 1 0 01.293.707V16a1 1 0 01-1 1h-1m-6-1a1 1 0 001 1h1M5 17a2 2 0 104 0m-4 0a2 2 0 114 0m6 0a2 2 0 104 0m-4 0a2 2 0 114 0",
    cls: "border-emerald-500/50 text-emerald-400",
    activeCls: "border-emerald-400 bg-emerald-950/40 text-emerald-300",
    titleHint: t("operations.tool_ambulance_hint"),
  },
  {
    tool: "add_emergency",
    label: t("operations.tool_emergency"),
    iconPath: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z",
    cls: "border-rose-500/50 text-rose-400",
    activeCls: "border-rose-400 bg-rose-950/40 text-rose-300",
    titleHint: t("operations.tool_emergency_hint"),
  },
  {
    tool: "add_traffic",
    label: t("operations.tool_jam"),
    iconPath: "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
    cls: "border-red-600/50 text-red-400",
    activeCls: "border-red-400 bg-red-950/40 text-red-300",
    titleHint: t("operations.tool_jam_hint"),
  },
  {
    tool: "delete",
    label: t("operations.tool_delete"),
    iconPath: "M6 18L18 6M6 6l12 12",
    cls: "border-rose-600/50 text-rose-400",
    activeCls: "border-rose-400 bg-rose-950/50 text-rose-200",
    titleHint: t("operations.tool_delete_hint"),
  },
]);

function selectToolbarTool(tool: BuilderTool) {
  builder.setTool(tool);
}

async function triggerCrisis(kind: "altercation" | "eta_exceeded" | "mass_casualty") {
  try {
    const r = await fetch("/api/sim/crisis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = (await r.json()) as { ok: boolean; message?: string };
    if (j.ok) {
      toast.success(j.message ?? `Crisis ${kind} activada`);
    } else {
      toast.warning(j.message ?? t("operations.crisis_failed"));
    }
  } catch (e) {
    toast.error(e instanceof Error ? e.message : t("operations.crisis_error"));
  }
}

async function onDeleteObject(
  kind: "ambulance" | "companion" | "emergency" | "poi" | "jam",
  id: string,
) {
  const labels: Record<typeof kind, string> = {
    ambulance: "unidad",
    companion: "vehículo de apoyo",
    emergency: "emergencia",
    poi: "lugar",
    jam: "atasco",
  };
  await toast.promise(store.deleteMapObject(kind, id), {
    loading: `Eliminando ${labels[kind]}...`,
    success: `${labels[kind]} eliminado`,
    error: (e: unknown) => (e instanceof Error ? e.message : t("operations.delete_failed")),
  });
}

// Returns false if OSRM snaps the click >MAX_SNAP_M from any drivable road
// (i.e. point is in the sea or otherwise inaccessible). Fails open if OSRM
// is unreachable so a transient outage doesn't block the builder.
const MAX_SNAP_M = 500;
async function isPointOnLand(lat: number, lng: number): Promise<boolean> {
  try {
    const r = await fetch(`/api/osrm/nearest/v1/driving/${lng},${lat}?number=1`);
    if (!r.ok) return true;
    const j = await r.json();
    const dist = j?.waypoints?.[0]?.distance;
    if (typeof dist !== "number") return true;
    return dist < MAX_SNAP_M;
  } catch {
    return true;
  }
}

async function onMapClick(lat: number, lng: number) {
  const tool = currentBuilderTool.value;
  if (!tool) return;
  if (tool === "add_hospital" || tool === "add_gas_station" || tool === "add_place") {
    const onLand = await isPointOnLand(lat, lng);
    if (!onLand) {
      toast.error(t("operations.poi_in_sea"));
      return;
    }
  }
  if (tool === "add_emergency") {
    const title = emergencyTitle.value.trim() || undefined;
    const eType = emergencyType.value;
    const desc = emergencyDescription.value.trim() || undefined;
    await toast.promise(store.createEmergency(lat, lng, title, eType, desc), {
      loading: t("operations.tool_emergency_hint") + "…",
      success: t("operations.emergency_default"),
      error: (e: unknown) => (e instanceof Error ? e.message : t("operations.register_failed")),
    });
    return;
  }
  try {
    if (tool === "add_hospital") {
      const name = hospitalName.value.trim() || t("operations.hospital_default");
      await store.createPoi("hospital", lat, lng, name);
      toast.success(`${t("operations.tool_hospital")} «${name}»`);
    } else if (tool === "add_gas_station") {
      const name = gasStationName.value.trim() || t("operations.gas_default");
      await store.createPoi("gas_station", lat, lng, name);
      toast.success(`${t("operations.tool_gas")} «${name}»`);
    } else if (tool === "add_traffic") {
      await store.createJamAtPoint(lat, lng, 75);
      toast.success(t("operations.jam_added"));
    } else if (tool === "add_place" && placeEntityId.value) {
      const et = state.value?.entityTypes?.find((x) => x.id === placeEntityId.value);
      const name = customPlaceName.value.trim() || et?.name || t("operations.place_default");
      await store.createPoi(placeEntityId.value, lat, lng, name);
      toast.success(`«${name}» (${et?.name ?? placeEntityId.value})`);
    } else if (tool === "add_ambulance") {
      const n = placementMode.value === "base" ? Math.max(1, baseCount.value | 0) : 1;
      await spawnVehicleBatch("ambulance", t("operations.tool_ambulance"), lat, lng, n);
    } else if (tool === "add_vehicle" && builder.vehicleEntityId) {
      const et = state.value?.entityTypes?.find((x) => x.id === builder.vehicleEntityId);
      const name = et?.name ?? t("operations.unit_default");
      const n = placementMode.value === "base" ? Math.max(1, baseCount.value | 0) : 1;
      await spawnVehicleBatch(builder.vehicleEntityId, name, lat, lng, n);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : t("operations.place_failed");
    toast.error(msg);
  }
}
</script>

<template>
  <div class="relative flex flex-col gap-3 pb-6">
    <!-- Título de página -->
    <div v-if="!mapFullscreen" class="flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 class="text-lg font-semibold tracking-tight text-slate-100">{{ t('operations.title') }}</h1>
        <p class="text-sm text-slate-500">{{ t('operations.subtitle') }}</p>
      </div>
    </div>

    <div
      ref="mapBlockRef"
      class="flex min-h-0 flex-col overflow-hidden rounded border border-slate-800 bg-slate-900"
      :class="mapFullscreen ? 'h-full w-full min-h-0 rounded-none border-0' : ''"
    >
      <!-- Barra: simulación + acciones -->
      <div class="flex flex-wrap items-center gap-3 border-b border-slate-800 px-3 py-2">
        <SimulationControls />
        <div class="ml-auto flex items-center gap-1.5">
          <div class="relative">
            <button
              type="button"
              class="flex h-8 items-center gap-1.5 rounded border border-slate-700 px-2.5 text-xs text-slate-200 hover:bg-slate-800"
              :class="openMenu === 'generate' ? 'bg-slate-800' : ''"
              :title="t('operations.ai_incidents_tooltip')"
              @click="toggleMenu('generate')"
            >
              {{ t('operations.generate_menu') }}
              <svg class="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" stroke-width="1.5" fill="none" /></svg>
            </button>
            <div v-if="openMenu === 'generate'" class="menu right-0 w-80 p-3">
              <div class="mb-3 flex rounded border border-slate-800 p-0.5 text-xs">
                <button
                  type="button"
                  class="flex-1 rounded-sm px-2 py-1"
                  :class="aiGenTab === 'incidents' ? 'bg-slate-800 text-slate-100' : 'text-slate-500 hover:text-slate-200'"
                  @click="aiGenTab = 'incidents'"
                >{{ t('operations.incidents_btn') }}</button>
                <button
                  type="button"
                  class="flex-1 rounded-sm px-2 py-1"
                  :class="aiGenTab === 'scenario' ? 'bg-slate-800 text-slate-100' : 'text-slate-500 hover:text-slate-200'"
                  @click="aiGenTab = 'scenario'"
                >{{ t('operations.full_scenario_btn') }}</button>
              </div>

              <div v-if="aiGenTab === 'incidents'">
                <p class="mb-3 text-xs text-slate-400">{{ t('operations.generate_incidents_desc') }}</p>
                <div class="flex items-end gap-2">
                  <label class="flex-1">
                    <span class="field-label">{{ t('operations.amount') }}</span>
                    <input v-model.number="incidentCount" type="number" min="1" max="20" class="field font-mono" />
                  </label>
                  <button class="btn-primary" :disabled="entities.loading" @click="generateIncidents">
                    {{ entities.loading ? t('operations.generating') : t('operations.generate') }}
                  </button>
                </div>
              </div>

              <div v-else>
                <p class="mb-3 text-xs text-slate-400">{{ t('operations.generate_scenario_desc') }}</p>
                <div class="mb-2 grid grid-cols-2 gap-2">
                  <label><span class="field-label">{{ t('scenario.hospitals') }}</span><input v-model.number="scenarioForm.hospitals" type="number" min="0" max="20" class="field font-mono" /></label>
                  <label><span class="field-label">{{ t('scenario.gas_stations') }}</span><input v-model.number="scenarioForm.gasStations" type="number" min="0" max="20" class="field font-mono" /></label>
                  <label><span class="field-label">{{ t('scenario.ambulances') }}</span><input v-model.number="scenarioForm.ambulances" type="number" min="0" max="30" class="field font-mono" /></label>
                  <label><span class="field-label">{{ t('scenario.incidents') }}</span><input v-model.number="scenarioForm.incidents" type="number" min="0" max="30" class="field font-mono" /></label>
                </div>
                <details v-if="vehicleEntityTypes.some((v) => v.id !== 'ambulance')" class="mb-2 rounded border border-slate-800 text-xs">
                  <summary class="cursor-pointer px-2 py-1.5 text-slate-400 hover:text-slate-200">{{ t('operations.extra_units') }}</summary>
                  <div class="max-h-36 space-y-1.5 overflow-y-auto border-t border-slate-800 p-2">
                    <label
                      v-for="et in vehicleEntityTypes.filter((v) => v.id !== 'ambulance')"
                      :key="et.id"
                      class="flex items-center justify-between gap-2 text-slate-300"
                    >
                      <span class="truncate">{{ et.name }}</span>
                      <input v-model.number="scenarioForm.extraByType[et.id]" type="number" min="0" max="15" placeholder="0" class="field w-16 py-0.5 text-right font-mono" />
                    </label>
                  </div>
                </details>
                <label class="mb-3 flex items-center gap-2 text-xs text-slate-400">
                  <input v-model="scenarioForm.clearExisting" type="checkbox" class="h-3.5 w-3.5" />
                  {{ t('operations.reset_before') }}
                </label>
                <button class="btn-primary w-full" :disabled="generatingScenario" @click="generateFullScenario">
                  {{ generatingScenario ? t('operations.generating_scenario') : t('operations.generate_scenario') }}
                </button>
              </div>
            </div>
          </div>
          <button
            type="button"
            class="flex h-8 w-8 items-center justify-center rounded border border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            :title="mapFullscreen ? t('operations.fullscreen_exit') : t('operations.fullscreen_expand')"
            :aria-label="mapFullscreen ? t('operations.fullscreen_exit') : t('operations.fullscreen_expand')"
            @click="toggleMapFullscreen"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path v-if="!mapFullscreen" stroke-linecap="round" stroke-linejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Añadir al mapa -->
      <div class="flex flex-wrap items-center gap-1.5 border-b border-slate-800 px-3 py-2">
        <span class="mr-1 text-[11px] text-slate-500">{{ t('operations.scenario_builder') }}</span>
        <button type="button" class="tool" :class="{ 'tool-active': !currentBuilderTool }" :title="t('operations.tool_navigate_hint')" @click="pickTool('nav')">
          {{ t('operations.tool_navigate') }}
        </button>
        <button type="button" class="tool" :class="{ 'tool-active': currentBuilderTool === 'add_emergency' }" :title="t('operations.tool_emergency_hint')" @click="pickTool('emg')">
          <span class="h-1.5 w-1.5 rotate-45 bg-red-400" aria-hidden="true" />
          {{ t('operations.tool_emergency') }}
        </button>

        <!-- Unidad ▾ -->
        <div class="relative">
          <button type="button" class="tool" :class="{ 'tool-active': currentBuilderTool === 'add_vehicle' || currentBuilderTool === 'add_ambulance' }" @click="toggleMenu('unit')">
            {{ t('operations.tool_vehicles') }}
            <svg class="h-3 w-3" viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" stroke-width="1.5" fill="none" /></svg>
          </button>
          <div v-if="openMenu === 'unit'" class="menu left-0 w-72">
            <div class="flex items-center gap-2 border-b border-slate-800 p-2">
              <input v-model="objectSearch" type="search" :placeholder="t('operations.search_palette')" class="field flex-1 py-1" />
              <div class="flex rounded border border-slate-800 p-0.5 text-[11px]">
                <button type="button" class="rounded-sm px-1.5 py-0.5" :class="placementMode === 'single' ? 'bg-slate-800 text-slate-100' : 'text-slate-500'" :title="t('operations.single_unit')" @click="placementMode = 'single'">1</button>
                <button type="button" class="rounded-sm px-1.5 py-0.5" :class="placementMode === 'base' ? 'bg-slate-800 text-slate-100' : 'text-slate-500'" :title="t('operations.base_tooltip')" @click="placementMode = 'base'">{{ t('operations.base_unit') }}</button>
              </div>
              <input
                v-if="placementMode === 'base'"
                v-model.number="baseCount"
                type="number"
                min="1"
                max="20"
                class="field w-12 py-1 text-center font-mono"
                :title="t('operations.base_count_tooltip')"
              />
            </div>
            <ul class="max-h-64 overflow-y-auto py-1">
              <li v-for="et in filteredVehicleEntities" :key="et.id">
                <button
                  type="button"
                  class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-800"
                  :class="isVehicleEntityActive(et.id) ? 'text-slate-100' : 'text-slate-300'"
                  :title="et.description ?? undefined"
                  @click="pickVehicle(et)"
                >
                  <span class="h-2 w-2 shrink-0 rounded-full" :style="{ background: et.color || 'var(--n-500)' }" aria-hidden="true" />
                  <span class="flex-1 truncate">{{ et.name }}</span>
                  <span v-if="countFor(et.id) > 0" class="font-mono text-[11px] text-slate-500" :title="t('operations.active_count_tooltip')">{{ countFor(et.id) }}</span>
                </button>
              </li>
              <li v-if="!filteredVehicleEntities.length" class="px-3 py-2 text-xs text-slate-500">{{ t('operations.no_results') }}</li>
            </ul>
          </div>
        </div>

        <!-- Lugar ▾ -->
        <div class="relative">
          <button
            type="button"
            class="tool"
            :class="{ 'tool-active': ['add_hospital', 'add_gas_station', 'add_place'].includes(currentBuilderTool ?? '') }"
            @click="toggleMenu('place')"
          >
            {{ t('operations.tool_places') }}
            <svg class="h-3 w-3" viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" stroke-width="1.5" fill="none" /></svg>
          </button>
          <div v-if="openMenu === 'place'" class="menu left-0 w-64">
            <ul class="max-h-64 overflow-y-auto py-1">
              <li v-for="et in filteredPlaceEntities" :key="et.id">
                <button
                  type="button"
                  class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-800"
                  :class="isPlacePaletteActive(et.id) ? 'text-slate-100' : 'text-slate-300'"
                  :title="et.description ?? undefined"
                  @click="pickPlace(et)"
                >
                  <span class="flex-1 truncate">{{ et.name }}</span>
                  <span v-if="placeCountFor(et.id) > 0" class="font-mono text-[11px] text-slate-500">{{ placeCountFor(et.id) }}</span>
                </button>
              </li>
              <li v-if="!filteredPlaceEntities.length" class="px-3 py-2 text-xs text-slate-500">{{ t('operations.no_results') }}</li>
            </ul>
          </div>
        </div>

        <button type="button" class="tool" :class="{ 'tool-active': currentBuilderTool === 'add_traffic' }" :title="t('operations.tool_jam_hint')" @click="pickTool('jam')">
          {{ t('operations.tool_jam') }}
        </button>
        <button type="button" class="tool" :class="{ 'tool-danger': currentBuilderTool === 'delete' }" :title="t('operations.tool_delete_hint')" @click="pickTool('del')">
          {{ t('operations.tool_delete') }}
        </button>

        <p v-if="currentBuilderTool" class="ml-auto text-[11px] text-slate-500">
          {{ currentBuilderTool === 'delete' ? t('operations.tool_delete_hint') : t('map.click_to_place') }}
          · <kbd class="rounded border border-slate-700 px-1 font-mono text-[10px]">Esc</kbd> {{ t('operations.esc_to_cancel') }}
        </p>
      </div>

      <!-- Campos según la herramienta -->
      <div v-if="currentBuilderTool === 'add_emergency'" class="grid gap-3 border-b border-slate-800 px-3 py-3 sm:grid-cols-[1fr_12rem]">
        <label>
          <span class="field-label">{{ t('operations.emergency_title_label') }}</span>
          <input v-model="emergencyTitle" type="text" class="field" />
        </label>
        <label>
          <span class="field-label">{{ t('operations.emergency_type_label') }}</span>
          <select v-model="emergencyType" class="field">
            <option value="medical">{{ t('operations.emergency_type_medical') }}</option>
            <option value="altercation">{{ t('operations.emergency_type_altercation') }}</option>
            <option value="mass_casualty">{{ t('operations.emergency_type_mass') }}</option>
          </select>
        </label>
        <label class="sm:col-span-2">
          <span class="field-label">{{ t('operations.emergency_desc_label') }}</span>
          <textarea v-model="emergencyDescription" rows="2" :placeholder="t('operations.emergency_desc_placeholder')" class="field resize-none" />
        </label>
      </div>
      <div v-else-if="currentBuilderTool === 'add_hospital'" class="border-b border-slate-800 px-3 py-3">
        <label class="block max-w-xs">
          <span class="field-label">{{ t('operations.hospital_name_label') }}</span>
          <input v-model="hospitalName" type="text" :placeholder="t('operations.hospital_name_placeholder')" class="field" />
        </label>
      </div>
      <div v-else-if="currentBuilderTool === 'add_gas_station'" class="border-b border-slate-800 px-3 py-3">
        <label class="block max-w-xs">
          <span class="field-label">{{ t('operations.gas_name_label') }}</span>
          <input v-model="gasStationName" type="text" :placeholder="t('operations.gas_name_placeholder')" class="field" />
        </label>
      </div>
      <div v-else-if="currentBuilderTool === 'add_place' && placeEntityId" class="border-b border-slate-800 px-3 py-3">
        <label class="block max-w-xs">
          <span class="field-label">{{ t('operations.custom_place_name') }}</span>
          <input
            v-model="customPlaceName"
            type="text"
            :placeholder="state?.entityTypes?.find((x) => x.id === placeEntityId)?.name ?? t('operations.place_name_placeholder')"
            class="field"
          />
        </label>
      </div>

      <!-- Mapa + panel lateral -->
      <div class="flex min-h-0 flex-1 flex-col lg:flex-row" :class="mapFullscreen ? 'overflow-hidden' : ''">
        <div class="relative min-h-0 min-w-0 flex-1">
          <AmbulanceMap
            :map-tool="mapTool"
            :fullscreen="mapFullscreen"
            :class="mapFullscreen ? 'h-full min-h-0 flex-1' : 'h-[min(640px,70vh)]'"
            @map-click="onMapClick"
            @delete-object="onDeleteObject"
          />
          <!-- Resultados del filtro del asistente -->
          <div
            v-if="filteredMatches.length || (Object.keys(uiFilters || {}).length && filterTotal)"
            class="pointer-events-none absolute bottom-4 left-1/2 z-[400] w-[min(560px,calc(100%-2rem))] -translate-x-1/2"
          >
            <div class="pointer-events-auto rounded border border-slate-700 bg-slate-950 shadow-lg">
              <div class="flex items-center justify-between gap-3 border-b border-slate-800 px-3 py-2">
                <span class="text-xs text-slate-200">{{ t('operations.matches', { n: filteredMatches.length, total: filterTotal }) }}</span>
                <button class="text-xs text-slate-400 hover:text-slate-100" @click="store.clearUiFilters()">{{ t('operations.clear_filter') }}</button>
              </div>
              <div class="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto px-3 py-2">
                <button
                  v-for="a in filteredMatches.slice(0, 30)"
                  :key="a.id"
                  type="button"
                  class="flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs"
                  :class="selectedAmbulanceId === a.id ? 'border-slate-400 text-slate-100' : 'border-slate-700 text-slate-300 hover:border-slate-500'"
                  @click="focusMatch(a.id)"
                >
                  <span class="font-mono">{{ a.displayLabel || a.id.slice(0, 6) }}</span>
                  <span v-if="energyOf(a, state?.entityTypes).value != null" class="font-mono text-[11px] text-slate-500">
                    {{ Math.round(energyOf(a, state?.entityTypes).value!) }}%
                  </span>
                </button>
                <span v-if="filteredMatches.length > 30" class="self-center text-[11px] text-slate-500">+{{ filteredMatches.length - 30 }}</span>
                <span v-if="!filteredMatches.length" class="text-xs text-slate-500">{{ t('operations.no_matches') }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Panel lateral: unidad seleccionada -->
        <aside
          class="flex w-full shrink-0 flex-col overflow-y-auto border-t border-slate-800 lg:w-80 lg:border-l lg:border-t-0"
          :class="mapFullscreen ? 'max-h-[40vh] lg:max-h-none' : 'lg:h-[min(640px,70vh)]'"
        >
          <div v-if="fleetCost.total > 0" class="flex items-baseline justify-between border-b border-slate-800 px-4 py-3">
            <div>
              <p class="text-[11px] text-slate-500">{{ t('operations.operating_cost_fleet') }}</p>
              <p class="text-[11px] text-slate-500">{{ t('operations.active_units', { n: fleetCost.activeUnits }) }}</p>
            </div>
            <span class="font-mono text-lg text-slate-100">{{ fleetCost.total.toFixed(2) }} €</span>
          </div>

          <template v-if="state?.ambulances?.length || (state?.companions?.length ?? 0) > 0">
            <div v-if="state?.ambulances?.length" class="border-b border-slate-800 px-4 py-3">
              <label class="field-label" for="unit-select">{{ t('operations.ops_summary') }}</label>
              <select id="unit-select" :value="selectedAmbulanceId ?? ''" class="field" @change="onTelemetryAmbulanceChange">
                <option value="">{{ t('operations.no_amb_focus') }}</option>
                <option v-for="(a, idx) in state.ambulances" :key="a.id" :value="a.id">
                  {{ shortId(a.id, idx) }} · {{ t(`status.${unitStatus(a).key}`) }}
                </option>
              </select>
            </div>

            <div v-if="selectedAmbulance" class="flex flex-col">
              <!-- Cabecera de la unidad -->
              <div class="flex items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
                <span class="font-mono text-sm text-slate-100">{{ shortId(selectedAmbulance.id, ambIndex(selectedAmbulance.id)) }}</span>
                <div class="flex items-center gap-1.5">
                  <span
                    v-if="selectedAmbulance.patientSeverity"
                    :class="['rounded-sm px-1.5 py-px text-[11px]', `severity-${selectedAmbulance.patientSeverity}`]"
                  >{{ severityLabel[selectedAmbulance.patientSeverity] ?? selectedAmbulance.patientSeverity }}</span>
                  <StatusChip :unit="selectedAmbulance" />
                </div>
              </div>

              <!-- Misión -->
              <dl class="space-y-2 border-b border-slate-800 px-4 py-3 text-xs">
                <div>
                  <dt class="text-[11px] text-slate-500">{{ t('operations.destination') }}</dt>
                  <dd class="text-slate-200">{{ destinationLabel }}</dd>
                </div>
                <div v-if="selectedAmbulance.latitude != null && selectedAmbulance.longitude != null">
                  <dt class="text-[11px] text-slate-500">{{ t('operations.location') }}</dt>
                  <dd class="font-mono text-slate-300">{{ selectedAmbulance.latitude.toFixed(5) }}, {{ selectedAmbulance.longitude.toFixed(5) }}</dd>
                </div>
                <div v-if="companionsForEmergency(selectedAmbulance.assignedEmergencyId).length">
                  <dt class="text-[11px] text-slate-500">{{ t('operations.support_same_incident') }}</dt>
                  <dd>
                    <ul class="text-slate-300">
                      <li v-for="c in companionsForEmergency(selectedAmbulance.assignedEmergencyId)" :key="c.id">
                        <span class="font-mono">{{ c.displayLabel ?? c.kind }}</span> · {{ c.typeName ?? c.kind }}
                      </li>
                    </ul>
                  </dd>
                </div>
              </dl>

              <!-- Paciente -->
              <div class="border-b border-slate-800 px-4 py-3">
                <p class="field-label">{{ t('operations.vitals') }}</p>
                <p v-if="selectedAmbulance.telemetry?.medical == null" class="text-xs text-slate-500">
                  {{ t('operations.no_patient') }}
                </p>
                <div v-else class="grid grid-cols-2 gap-px overflow-hidden rounded border border-slate-800 bg-slate-800">
                  <div class="bg-slate-900 px-2.5 py-2">
                    <p class="text-[11px] text-slate-500">{{ t('operations.pulse') }}</p>
                    <p class="font-mono text-sm text-slate-100">{{ selectedAmbulance.telemetry?.medical?.heartRateBpm != null ? Math.round(selectedAmbulance.telemetry.medical.heartRateBpm) : '—' }} <span class="text-[11px] text-slate-500">{{ t('operations.bpm') }}</span></p>
                  </div>
                  <div class="bg-slate-900 px-2.5 py-2">
                    <p class="text-[11px] text-slate-500">SpO₂</p>
                    <p class="font-mono text-sm" :class="(selectedAmbulance.telemetry?.medical?.spo2Pct ?? 100) < 90 ? 'text-red-300' : 'text-slate-100'">
                      {{ selectedAmbulance.telemetry?.medical?.spo2Pct ?? '—' }} <span class="text-[11px] text-slate-500">%</span>
                    </p>
                  </div>
                  <template v-if="selectedAmbulance.telemetry?.medical?.gcsScore != null">
                    <div class="bg-slate-900 px-2.5 py-2">
                      <p class="text-[11px] text-slate-500">{{ t('operations.glasgow') }}</p>
                      <p class="font-mono text-sm text-slate-100">{{ selectedAmbulance.telemetry.medical.gcsScore }}</p>
                    </div>
                    <div class="bg-slate-900 px-2.5 py-2">
                      <p class="text-[11px] text-slate-500">ECG</p>
                      <p class="text-sm text-slate-100">{{ selectedAmbulance.telemetry.medical.ecgRhythm ?? '—' }}</p>
                    </div>
                  </template>
                </div>
              </div>

              <!-- Vehículo -->
              <dl class="grid grid-cols-2 gap-x-3 gap-y-2 px-4 py-3 text-xs">
                <div>
                  <dt class="text-[11px] text-slate-500">{{ t(energyOf(selectedAmbulance, state?.entityTypes).labelKey) }}</dt>
                  <dd class="font-mono" :class="(energyOf(selectedAmbulance, state?.entityTypes).value ?? 100) < 25 ? 'text-amber-300' : 'text-slate-100'">
                    {{ energyOf(selectedAmbulance, state?.entityTypes).value?.toFixed(0) ?? '—' }} %
                  </dd>
                </div>
                <div>
                  <dt class="text-[11px] text-slate-500">{{ t('operations.speed') }}</dt>
                  <dd class="font-mono text-slate-100">{{ selectedAmbulance.telemetry?.positioning?.speedKmh != null ? Math.round(selectedAmbulance.telemetry.positioning.speedKmh) : '—' }} <span class="text-slate-500">km/h</span></dd>
                </div>
                <div>
                  <dt class="text-[11px] text-slate-500">{{ t('operations.speed_limit') }}</dt>
                  <dd class="font-mono text-slate-100">{{ selectedAmbulance.roadSpeedLimitKmh ?? selectedAmbulance.telemetry?.positioning?.roadSpeedLimitKmh ?? '—' }} <span class="text-slate-500">km/h</span></dd>
                </div>
                <div>
                  <dt class="text-[11px] text-slate-500">{{ t('operations.odometer') }}</dt>
                  <dd class="font-mono text-slate-100">{{ selectedAmbulance.telemetry?.mechanical?.odometerKm?.toFixed(1) ?? selectedAmbulance.odometerKm?.toFixed(1) ?? '—' }} <span class="text-slate-500">km</span></dd>
                </div>
                <template v-if="operatingCostOf(selectedAmbulance, state?.entityTypes).available">
                  <div>
                    <dt class="text-[11px] text-slate-500">{{ t('operations.operating_cost') }}</dt>
                    <dd class="font-mono text-slate-100">{{ operatingCostOf(selectedAmbulance, state?.entityTypes).total.toFixed(2) }} €</dd>
                  </div>
                  <div>
                    <dt class="text-[11px] text-slate-500">{{ t('operations.active_time') }}</dt>
                    <dd class="font-mono text-slate-100">{{ operatingCostOf(selectedAmbulance, state?.entityTypes).activeMinutes.toFixed(1) }} <span class="text-slate-500">min</span></dd>
                  </div>
                </template>
              </dl>
            </div>
            <p v-else-if="state?.ambulances?.length" class="px-4 py-3 text-xs text-slate-500">{{ t('operations.pick_unit_hint') }}</p>

            <!-- Unidades de apoyo -->
            <div v-if="state?.companions?.length" class="border-t border-slate-800 px-4 py-3">
              <label class="field-label" for="companion-select">{{ t('operations.support_unit') }}</label>
              <select id="companion-select" :value="selectedCompanionId ?? ''" class="field" @change="onTelemetryCompanionChange">
                <option value="">{{ t('operations.no_companion_focus') }}</option>
                <option v-for="c in state.companions" :key="c.id" :value="c.id">
                  {{ c.displayLabel ?? c.kind }} · {{ c.typeName ?? c.kind }}
                </option>
              </select>
              <dl v-if="selectedCompanion" class="mt-2 space-y-1 text-xs">
                <div class="flex justify-between"><dt class="text-slate-500">{{ t('operations.speed') }}</dt><dd class="font-mono text-slate-200">{{ selectedCompanion.speedKmh }} km/h</dd></div>
                <div v-if="state.emergencies.find((e) => e.id === selectedCompanion?.assignedEmergencyId)" class="flex justify-between gap-2">
                  <dt class="text-slate-500">{{ t('operations.destination') }}</dt>
                  <dd class="truncate text-slate-200">{{ state.emergencies.find((e) => e.id === selectedCompanion?.assignedEmergencyId)?.title }}</dd>
                </div>
              </dl>
            </div>
          </template>
          <p v-else class="px-4 py-6 text-sm text-slate-500">{{ t('operations.empty_sidebar') }}</p>
        </aside>
      </div>
    </div>

    <!-- Cierra los menús al hacer clic fuera -->
    <div v-if="openMenu" class="fixed inset-0 z-[440]" aria-hidden="true" @click="openMenu = null" />
  </div>
</template>

<style scoped>
.tool {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  height: 1.75rem;
  padding: 0 0.625rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--text-2);
}
.tool:hover { background: var(--surface-2); color: var(--text); }
.tool-active { border-color: var(--n-400); color: var(--text); background: var(--surface-2); }
.tool-danger { border-color: color-mix(in oklab, var(--crit) 55%, transparent); color: var(--crit-300); }
.menu {
  position: absolute;
  top: calc(100% + 4px);
  z-index: 450;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lg);
}
.field-label {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 11px;
  color: var(--text-3);
}
.field {
  width: 100%;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--bg);
  padding: 0.375rem 0.5rem;
  font-size: 13px;
  color: var(--text);
}
.field:focus { outline: none; border-color: var(--n-400); }
.btn-primary {
  border-radius: var(--radius-sm);
  background: var(--n-100);
  color: var(--n-950);
  padding: 0.4rem 0.75rem;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.btn-primary:hover { background: var(--n-300); }
.btn-primary:disabled { opacity: 0.5; }
</style>
