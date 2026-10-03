<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import AmbulanceMap from "@/components/dashboard/AmbulanceMap.vue";
import SimulationControls from "@/components/dashboard/SimulationControls.vue";
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
const showIncidentGen = ref(false);
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
    toast.success(
      t("operations.scenario_generated"),
      { description: `${j.placed?.hospitals ?? 0} hospitales · ${j.placed?.gasStations ?? 0} gasolineras · ${j.placed?.ambulances ?? 0} ambulancias · ${j.placed?.incidents ?? 0} incidencias` },
    );
    showIncidentGen.value = false;
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
    showIncidentGen.value = false;
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

const objectPaletteTab = ref<"vehicles" | "places">("vehicles");
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

function energyIconOfAmb(a: { entityTypeId?: string | null }): string {
  return powertrainOfAmb(a) === "electric" ? "🔋" : "⛽";
}

const vehicleEntityTypes = computed(() => entityTypesList.value.filter((e) => e.kind === "vehicle"));

const placeEntityTypes = computed(() => entityTypesList.value.filter((e) => e.kind === "place"));

const staticVehiclePalette = computed(() => [
  { id: "nav", label: t("operations.tool_navigate"), hint: t("operations.tool_navigate_hint"), keywords: "mapa cursor map" },
  { id: "emg", label: t("operations.tool_emergency"), hint: t("operations.tool_emergency_hint"), keywords: "incidente 112 emergency" },
  { id: "jam", label: t("operations.tool_jam"), hint: t("operations.tool_jam_hint"), keywords: "trafico bloqueo traffic" },
  { id: "del", label: t("operations.tool_delete"), hint: t("operations.tool_delete_hint"), keywords: "borrar delete remove quitar x eliminar" },
] as const);

function iconForVehicleType(id: string, name?: string): string {
  const s = `${id} ${name || ""}`.toLowerCase();
  if (s.includes("heli"))   return "🚁";
  if (s.includes("polic") || s.includes("patrol")) return "🚓";
  if (s.includes("fire") || s.includes("bomb"))    return "🚒";
  if (s.includes("moto"))   return "🏍️";
  if (s.includes("drone"))  return "🛸";
  if (s.includes("boat") || s.includes("barco"))   return "🚤";
  return "🚑";
}

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

const filteredStaticVehicles = computed(() =>
  staticVehiclePalette.value.filter((row) => paletteSearchMatches(`${row.label} ${row.hint} ${row.keywords}`)),
);

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
  // Click en cualquier tipo de vehículo del catálogo → activa la herramienta
  // "colocar unidad" para que el siguiente click en el mapa la instancie.
  builder.selectVehicleEntity(et.id);
  toast.message(t("operations.place_palette_msg", { name: et.name }), {
    description: t("operations.place_palette_desc"),
  });
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
}));

function companionsForEmergency(emergencyId: string | null | undefined) {
  if (!emergencyId) return [];
  return state.value?.companions?.filter((c) => c.assignedEmergencyId === emergencyId) ?? [];
}

watch(
  () =>
    selectedAmbulance.value?.hasPatient
      ? selectedAmbulance.value?.telemetry?.medical?.spo2Pct
      : undefined,
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
    return e ? `${e.title} (${e.status})` : String(amb.assignedEmergencyId);
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
  return "—";
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
  <div class="relative flex flex-col gap-4 pb-6">
    <div
      ref="mapBlockRef"
      class="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-800/60 bg-slate-950/30"
      :class="mapFullscreen ? 'h-full w-full min-h-0 flex-col rounded-none border-0' : ''"
    >
      <!-- Scenario builder header -->
      <div class="shrink-0 border-b border-slate-800/60 bg-slate-900/60 p-4">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-sm font-semibold text-slate-200">{{ t('operations.scenario_builder') }}</h2>
            <p class="mt-0.5 text-[11px] text-slate-500">
              {{ t('map.click_to_place') }}.
              <kbd class="ml-1 rounded bg-slate-800 px-1 py-0.5 text-[10px] text-slate-400">Esc</kbd> {{ t('operations.esc_to_cancel') }}
            </p>
          </div>
        </div>

        <!-- Paleta: todo lo que puede aparecer en el mapa (registro + IA) -->
        <div class="mt-4 rounded-xl border border-slate-800/80 bg-slate-950/40 p-3">
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{{ t('operations.objects_in_map') }}</span>
            <div class="flex rounded-lg border border-slate-700/80 p-0.5 text-[10px] font-medium">
              <button
                type="button"
                class="rounded-md px-2.5 py-1 transition"
                :class="
                  objectPaletteTab === 'vehicles'
                    ? 'bg-slate-700 text-slate-100'
                    : 'text-slate-500 hover:text-slate-300'
                "
                @click="objectPaletteTab = 'vehicles'"
              >
                {{ t('operations.tool_vehicles') }}
              </button>
              <button
                type="button"
                class="rounded-md px-2.5 py-1 transition"
                :class="
                  objectPaletteTab === 'places'
                    ? 'bg-slate-700 text-slate-100'
                    : 'text-slate-500 hover:text-slate-300'
                "
                @click="objectPaletteTab = 'places'"
              >
                {{ t('operations.tool_hospital') }} / {{ t('operations.tool_gas') }}
              </button>
            </div>
            <input
              v-model="objectSearch"
              type="search"
              :placeholder="t('operations.search_palette')"
              class="ml-auto min-w-[7rem] flex-1 rounded-lg border border-slate-700/80 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-emerald-500/40"
            />
          </div>
          <!-- Modo de colocación: 1 unidad | base N -->
          <div
            v-if="objectPaletteTab === 'vehicles'"
            class="mb-2 flex items-center gap-2 text-[10px] text-slate-400"
          >
            <div class="flex rounded-md border border-slate-700/80 p-0.5">
              <button
                type="button"
                class="rounded-sm px-2 py-0.5 transition"
                :class="placementMode === 'single' ? 'bg-emerald-600/30 text-emerald-200' : 'text-slate-500 hover:text-slate-300'"
                :title="t('operations.single_unit')"
                @click="placementMode = 'single'"
              >1</button>
              <button
                type="button"
                class="rounded-sm px-2 py-0.5 transition"
                :class="placementMode === 'base' ? 'bg-emerald-600/30 text-emerald-200' : 'text-slate-500 hover:text-slate-300'"
                :title="t('operations.base_tooltip')"
                @click="placementMode = 'base'"
              >{{ t('operations.base_unit') }}</button>
            </div>
            <input
              v-if="placementMode === 'base'"
              v-model.number="baseCount"
              type="number"
              min="1"
              max="20"
              class="w-14 rounded-md border border-slate-700 bg-slate-900 px-2 py-0.5 text-center text-xs text-slate-200 focus:border-emerald-500/40 focus:outline-none"
              :title="t('operations.base_count_tooltip')"
            />
            <span v-if="placementMode === 'base'" class="text-slate-500">{{ t('controls.tools.ambulance').toLowerCase() }}</span>
          </div>
          <div
            v-if="objectPaletteTab === 'vehicles'"
            class="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto"
          >
            <button
              v-for="row in filteredStaticVehicles"
              :key="row.id"
              type="button"
              class="rounded-lg border px-2.5 py-1.5 text-left text-[10px] font-medium transition hover:bg-slate-800/80"
              :class="[
                (row.id === 'nav' && !currentBuilderTool) ||
                (row.id === 'emg' && currentBuilderTool === 'add_emergency') ||
                (row.id === 'jam' && currentBuilderTool === 'add_traffic')
                  ? 'border-emerald-500/50 bg-emerald-950/25 text-emerald-200'
                  : (row.id === 'del' && currentBuilderTool === 'delete')
                    ? 'border-rose-500/60 bg-rose-950/30 text-rose-200'
                    : 'border-slate-700/80 text-slate-400',
              ]"
              :title="row.hint"
              @click="onPaletteStaticVehicle(row.id)"
            >
              <span class="block text-slate-200">{{ row.label }}</span>
              <span class="block font-normal text-slate-600">{{ row.hint }}</span>
            </button>
            <!-- Chips de unidades: icono + nombre, compacto, resalta activa -->
            <button
              v-for="et in filteredVehicleEntities"
              :key="et.id"
              type="button"
              class="flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] transition hover:bg-slate-800/80"
              :class="
                isVehicleEntityActive(et.id)
                  ? 'border-emerald-500/60 bg-emerald-950/30 text-emerald-100'
                  : 'border-slate-700/80 text-slate-300'
              "
              :title="et.description ?? `Colocar unidad: ${et.name}`"
              @click="onPaletteVehicleEntity(et)"
            >
              <span class="text-sm leading-none">{{ iconForVehicleType(et.id, et.name) }}</span>
              <span class="font-medium">{{ et.name }}</span>
              <span
                v-if="countFor(et.id) > 0"
                class="ml-0.5 rounded-sm bg-slate-800/90 px-1.5 text-[9px] font-bold text-emerald-300 ring-1 ring-emerald-700/40"
                :title="t('operations.active_count_tooltip')"
              >{{ countFor(et.id) }}</span>
              <span
                v-if="!et.builtIn"
                class="ml-0.5 rounded-sm bg-purple-500/15 px-1 text-[9px] font-semibold text-purple-300"
                title="Tipo personalizado o generado por IA"
              >·</span>
            </button>
            <p v-if="!filteredStaticVehicles.length && !filteredVehicleEntities.length" class="w-full py-2 text-center text-[10px] text-slate-600">
              Sin coincidencias
            </p>
          </div>
          <div v-else class="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto">
            <button
              v-for="et in filteredPlaceEntities"
              :key="et.id"
              type="button"
              class="rounded-lg border px-2.5 py-1.5 text-left text-[10px] transition hover:bg-slate-800/80"
              :class="
                isPlacePaletteActive(et.id)
                  ? 'border-violet-500/50 bg-violet-950/25 text-violet-100'
                  : 'border-slate-700/80 text-slate-400'
              "
              :title="et.description ?? 'Clic para colocar en el mapa'"
              @click="onPalettePlaceEntity(et)"
            >
              <span class="font-medium text-slate-200">{{ et.name }}</span>
              <span
                v-if="placeCountFor(et.id) > 0"
                class="ml-1 rounded-sm bg-slate-800/90 px-1.5 text-[9px] font-bold text-violet-300 ring-1 ring-violet-700/40"
                title="Lugares activos de este tipo"
              >{{ placeCountFor(et.id) }}</span>
              <span v-if="!et.builtIn" class="ml-1 text-[9px] text-purple-400">IA</span>
            </button>
            <p v-if="!filteredPlaceEntities.length" class="w-full py-2 text-center text-[10px] text-slate-600">
              Sin coincidencias
            </p>
          </div>
        </div>

        <!-- Context input fields -->
        <div v-if="currentBuilderTool === 'add_emergency'" class="mt-3 flex max-w-lg flex-wrap gap-3">
          <div class="flex-1 min-w-[10rem]">
            <label class="text-[11px] font-medium text-slate-400">{{ t('operations.emergency_title_label') }}</label>
            <input
              v-model="emergencyTitle"
              type="text"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-emerald-500/50"
            />
          </div>
          <div class="min-w-[9rem]">
            <label class="text-[11px] font-medium text-slate-400">{{ t('operations.emergency_type_label') }}</label>
            <select
              v-model="emergencyType"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-emerald-500/50"
            >
              <option value="medical">🏥 Medica</option>
              <option value="altercation">⚔️ Altercado</option>
              <option value="mass_casualty">💥 Victimas masivas</option>
            </select>
          </div>
          <div class="w-full">
            <label class="text-[11px] font-medium text-slate-400">Descripcion (contexto para la IA)</label>
            <textarea
              v-model="emergencyDescription"
              rows="2"
              :placeholder="t('operations.emergency_desc_placeholder')"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-emerald-500/50 resize-none"
            />
          </div>
        </div>
        <div v-if="currentBuilderTool === 'add_hospital'" class="mt-3 max-w-xs">
          <label class="text-[11px] font-medium text-slate-400">{{ t('operations.hospital_name_label') }}</label>
          <input
            v-model="hospitalName"
            type="text"
            :placeholder="t('operations.hospital_name_placeholder')"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-emerald-500/50"
          />
        </div>
        <div v-if="currentBuilderTool === 'add_gas_station'" class="mt-3 max-w-xs">
          <label class="text-[11px] font-medium text-slate-400">{{ t('operations.gas_name_label') }}</label>
          <input
            v-model="gasStationName"
            type="text"
            :placeholder="t('operations.gas_name_placeholder')"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-emerald-500/50"
          />
        </div>
        <div v-if="currentBuilderTool === 'add_place' && placeEntityId" class="mt-3 max-w-md">
          <label class="text-[11px] font-medium text-slate-400">Nombre en mapa (tipo personalizado)</label>
          <input
            v-model="customPlaceName"
            type="text"
            :placeholder="state?.entityTypes?.find((x) => x.id === placeEntityId)?.name ?? t('operations.place_name_placeholder')"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-emerald-500/50"
          />
        </div>
      </div>

      <!-- Simulation controls -->
      <div class="shrink-0 border-b border-slate-800/60 bg-slate-950/60 px-3 py-3 lg:px-4">
        <SimulationControls />
      </div>

      <!-- Map + Telemetry sidebar -->
      <div
        class="flex min-h-0 flex-1 flex-col gap-0 lg:flex-row"
        :class="mapFullscreen ? 'min-h-0 overflow-hidden' : ''"
      >
        <!-- Map area -->
        <div class="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <button
            type="button"
            class="absolute right-3 top-3 z-[420] flex items-center gap-1.5 rounded-xl border border-slate-600/50 bg-slate-950 px-3 py-1.5 text-xs font-medium text-slate-300 shadow-lg transition hover:bg-slate-800 hover:text-slate-100"
            @click="toggleMapFullscreen"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path v-if="!mapFullscreen" stroke-linecap="round" stroke-linejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" />
            </svg>
            {{ mapFullscreen ? t('operations.fullscreen_exit') : t('operations.fullscreen_expand') }}
          </button>
          <div class="relative min-h-0 flex-1"
               :class="mapFullscreen ? '' : ''">
            <AmbulanceMap
              :map-tool="mapTool"
              :fullscreen="mapFullscreen"
              :class="
                mapFullscreen
                  ? 'min-h-0 flex-1 rounded-lg border border-slate-800 h-full'
                  : 'h-[min(520px,62vh)]'
              "
              @map-click="onMapClick"
              @delete-object="onDeleteObject"
            />
            <!-- Panel flotante resultados ⌘ comando -->
            <div
              v-if="filteredMatches.length || (Object.keys(uiFilters || {}).length && filterTotal)"
              class="pointer-events-none absolute bottom-4 left-1/2 z-[400] w-[min(560px,calc(100%-2rem))] -translate-x-1/2"
            >
              <div class="pointer-events-auto rounded-2xl border border-purple-500/40 bg-slate-950 shadow-2xl">
                <div class="flex items-center justify-between gap-3 border-b border-purple-500/20 px-4 py-2">
                  <div class="flex items-center gap-2">
                    <span class="relative flex h-2.5 w-2.5">
                      <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-purple-400 opacity-70"></span>
                      <span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-purple-500"></span>
                    </span>
                    <span class="text-xs font-semibold text-purple-200">
                      {{ filteredMatches.length }}
                      <span class="text-purple-400/70">/ {{ filterTotal }}</span>
                      unidades coinciden
                    </span>
                  </div>
                  <button
                    class="text-[11px] text-purple-300/80 hover:text-slate-100"
                    @click="store.clearUiFilters()"
                  >Limpiar filtro ✕</button>
                </div>
                <div class="flex flex-wrap gap-1.5 px-3 py-2 max-h-24 overflow-y-auto">
                  <button
                    v-for="a in filteredMatches.slice(0, 30)"
                    :key="a.id"
                    type="button"
                    class="group flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-600/10 px-2 py-1 text-[11px] transition hover:border-purple-400 hover:bg-purple-500/20"
                    :class="selectedAmbulanceId === a.id ? 'ring-1 ring-purple-300 bg-purple-500/30' : ''"
                    @click="focusMatch(a.id)"
                  >
                    <span class="font-mono font-semibold text-purple-100">{{ a.displayLabel || a.id.slice(0, 6) }}</span>
                    <span v-if="energyOf(a, state?.entityTypes).value != null" class="text-[10px] text-purple-300/70">
                      {{ energyOf(a, state?.entityTypes).icon }}{{ Math.round(energyOf(a, state?.entityTypes).value!) }}%
                    </span>
                    <span v-if="a.hasPatient" class="text-[10px] text-rose-300">🚑</span>
                  </button>
                  <span v-if="filteredMatches.length > 30" class="self-center px-2 text-[10px] text-purple-400/70">
                    +{{ filteredMatches.length - 30 }} más
                  </span>
                  <span v-if="!filteredMatches.length" class="px-2 py-1 text-[11px] text-purple-300/60 italic">
                    Ninguna unidad cumple. Ajusta criterios o quita el filtro.
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Telemetry sidebar -->
        <aside
          class="flex w-full shrink-0 flex-col gap-3 overflow-y-auto border-l border-slate-800/60 bg-slate-900/40 p-4 lg:w-[min(100%,22rem)]"
          :class="mapFullscreen ? 'max-h-[40vh] min-h-0 lg:max-h-none lg:w-72' : 'min-h-[min(520px,62vh)]'"
        >
          <!-- Coste flota total (sticky arriba). Suma activación + runtime
               de todas las unidades con powertrain catalogado. -->
          <div
            v-if="fleetCost.total > 0"
            class="rounded border border-slate-800 bg-slate-950 px-3 py-2.5"
          >
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-medium uppercase tracking-wider text-slate-500">
                {{ t('operations.operating_cost_fleet') }}
              </span>
              <span class="font-mono text-base font-semibold text-slate-100">
                {{ fleetCost.total.toFixed(2) }} €
              </span>
            </div>
            <div class="mt-1 flex items-center justify-between font-mono text-[10px] text-slate-500">
              <span>{{ t('operations.active_units', { n: fleetCost.activeUnits }) }}</span>
              <span>
                {{ fleetCost.activation.toFixed(0) }}€ act · {{ fleetCost.runtime.toFixed(2) }}€ tiempo
              </span>
            </div>
          </div>

          <h3 class="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-emerald-400/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            Telemetría rápida
          </h3>

          <template v-if="state?.ambulances?.length || (state?.companions?.length ?? 0) > 0">
            <div v-if="state?.ambulances?.length" class="space-y-2">
              <div>
                <label class="text-[10px] font-medium text-slate-500">Ambulancia (SVB)</label>
                <select
                  :value="selectedAmbulanceId ?? ''"
                  class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none"
                  @change="onTelemetryAmbulanceChange"
                >
                  <option value="">{{ t('operations.no_amb_focus') }}</option>
                  <option v-for="(a, idx) in state.ambulances" :key="a.id" :value="a.id">
                    {{ shortId(a.id, idx) }} · {{ a.fsmState ?? "IDLE" }}{{ a.hasPatient ? " · Paciente" : "" }}
                  </option>
                </select>
              </div>

              <div v-if="selectedAmbulance" class="space-y-2.5 border-t border-slate-800/60 pt-3">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ t('operations.ops_summary') }}</p>
                <div class="rounded-lg border border-slate-800/80 bg-slate-950/50 px-2.5 py-2 text-[10px] leading-relaxed text-slate-400">
                  <p>
                    <span class="text-slate-500">Unidad:</span>
                    <span class="ml-1 font-mono text-slate-200">{{
                      shortId(selectedAmbulance.id, ambIndex(selectedAmbulance.id))
                    }}</span>
                  </p>
                  <p v-if="selectedAmbulance.missionPhase">
                    <span class="text-slate-500">Fase mision:</span>
                    <span class="ml-1 text-slate-200">{{
                      missionPhaseLabel[selectedAmbulance.missionPhase] ?? selectedAmbulance.missionPhase
                    }}</span>
                  </p>
                  <p v-if="selectedAmbulance.missionStatus">
                    <span class="text-slate-500">Estado mision:</span>
                    <span class="ml-1 text-slate-200">{{ selectedAmbulance.missionStatus }}</span>
                  </p>
                  <p v-if="selectedAmbulance.locationLabel">
                    <span class="text-slate-500">Ubicacion (motor):</span>
                    <span class="ml-1 text-slate-200">{{ selectedAmbulance.locationLabel }}</span>
                  </p>
                  <p v-if="selectedAmbulance.latitude != null && selectedAmbulance.longitude != null">
                    <span class="text-slate-500">GPS:</span>
                    <span class="ml-1 font-mono text-slate-300"
                      >{{ selectedAmbulance.latitude.toFixed(5) }}, {{ selectedAmbulance.longitude.toFixed(5) }}</span
                    >
                  </p>
                  <p>
                    <span class="text-slate-500">Enlace simulado:</span>
                    <span class="ml-1 font-medium text-slate-200">{{ state?.linkState ?? "—" }}</span>
                  </p>
                  <div
                    v-if="companionsForEmergency(selectedAmbulance.assignedEmergencyId).length"
                    class="mt-2 border-t border-slate-800/60 pt-2"
                  >
                    <p class="text-slate-500">{{ t('operations.support_same_incident') }}</p>
                    <ul class="mt-1 list-inside list-disc text-slate-300">
                      <li
                        v-for="c in companionsForEmergency(selectedAmbulance.assignedEmergencyId)"
                        :key="c.id"
                        class="font-mono text-[10px]"
                      >
                        {{ c.displayLabel ?? c.kind }} · {{ c.typeName ?? c.kind }} ({{ c.status }})
                      </li>
                    </ul>
                  </div>
                </div>

                <div class="flex flex-wrap items-center gap-2">
                  <span :class="['fsm-chip', `fsm-${selectedAmbulance.fsmState ?? 'idle'}`]">
                    <span class="fsm-dot" />
                    {{ selectedAmbulance.fsmState ?? "idle" }}
                  </span>
                  <span
                    v-if="selectedAmbulance.patientSeverity"
                    :class="['rounded-md px-2 py-0.5 text-[10px] font-semibold', `severity-${selectedAmbulance.patientSeverity}`]"
                  >
                    {{ severityLabel[selectedAmbulance.patientSeverity] ?? selectedAmbulance.patientSeverity }}
                  </span>
                </div>

                <p
                  v-if="!selectedAmbulance.hasPatient || selectedAmbulance.telemetry?.medical == null"
                  class="rounded-lg border border-dashed border-slate-700 bg-slate-950/60 px-2 py-3 text-center text-[11px] text-slate-500"
                >
                  Unidad vacia — sin paciente a bordo
                </p>

                <template v-else>
                  <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ t('operations.vitals') }}</p>
                  <div class="grid grid-cols-2 gap-2">
                    <div class="rounded-lg border border-slate-700/50 bg-slate-950/60 px-2.5 py-2">
                      <p class="text-[9px] font-medium uppercase tracking-wider text-slate-500">BPM</p>
                      <p class="text-sm font-semibold text-slate-200">{{ selectedAmbulance.telemetry?.medical?.heartRateBpm ?? "—" }}</p>
                    </div>
                    <div class="rounded-lg border border-slate-700/50 bg-slate-950/60 px-2.5 py-2">
                      <p class="text-[9px] font-medium uppercase tracking-wider text-slate-500">SpO2</p>
                      <p class="text-sm font-semibold" :class="(selectedAmbulance.telemetry?.medical?.spo2Pct ?? 100) < 90 ? 'text-rose-400' : 'text-slate-200'">
                        {{ selectedAmbulance.telemetry?.medical?.spo2Pct ?? "—" }}%
                      </p>
                    </div>
                  </div>
                  <div v-if="selectedAmbulance.telemetry?.medical?.gcsScore != null" class="grid grid-cols-2 gap-2">
                    <div class="rounded-lg border border-slate-700/50 bg-slate-950/60 px-2.5 py-2">
                      <p class="text-[9px] font-medium uppercase tracking-wider text-slate-500">GCS</p>
                      <p class="text-sm font-semibold text-slate-200">{{ selectedAmbulance.telemetry.medical.gcsScore }}</p>
                    </div>
                    <div class="rounded-lg border border-slate-700/50 bg-slate-950/60 px-2.5 py-2">
                      <p class="text-[9px] font-medium uppercase tracking-wider text-slate-500">ECG</p>
                      <p class="text-sm font-semibold text-slate-200">{{ selectedAmbulance.telemetry.medical.ecgRhythm ?? "—" }}</p>
                    </div>
                  </div>
                </template>

                <div class="space-y-1.5 border-t border-slate-800/60 pt-2.5">
                  <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Mecanica / ruta</p>
                  <div class="flex items-center justify-between text-xs">
                    <span class="text-slate-500">
                      {{ energyOf(selectedAmbulance, state?.entityTypes).icon }}
                      {{ t(energyOf(selectedAmbulance, state?.entityTypes).labelKey) }}
                    </span>
                    <span class="font-medium" :class="(energyOf(selectedAmbulance, state?.entityTypes).value ?? 100) < 25 ? 'text-amber-400' : 'text-slate-300'">
                      {{ energyOf(selectedAmbulance, state?.entityTypes).value?.toFixed(0) ?? "—" }}%
                    </span>
                  </div>
                  <template v-if="operatingCostOf(selectedAmbulance, state?.entityTypes).available">
                    <div class="flex items-center justify-between text-xs">
                      <span class="text-slate-500">💶 {{ t('operations.operating_cost') }}</span>
                      <span class="font-mono font-semibold text-emerald-300">
                        {{ operatingCostOf(selectedAmbulance, state?.entityTypes).total.toFixed(2) }} €
                      </span>
                    </div>
                    <div class="flex items-center justify-between text-[10px] text-slate-600">
                      <span>{{ t('operations.active_time') }}: {{ operatingCostOf(selectedAmbulance, state?.entityTypes).activeMinutes.toFixed(1) }} min</span>
                      <span>
                        {{ operatingCostOf(selectedAmbulance, state?.entityTypes).activation.toFixed(0) }}€ +
                        {{ operatingCostOf(selectedAmbulance, state?.entityTypes).ratePerMin.toFixed(2) }}€/min
                      </span>
                    </div>
                  </template>
                  <div class="flex items-center justify-between text-xs">
                    <span class="text-slate-500">{{ t('operations.odometer') }}</span>
                    <span class="font-medium text-slate-300">{{ selectedAmbulance.telemetry?.mechanical?.odometerKm?.toFixed(2) ?? selectedAmbulance.odometerKm?.toFixed(2) ?? "—" }} km</span>
                  </div>
                  <div class="flex items-center justify-between text-xs">
                    <span class="text-slate-500">{{ t('operations.speed') }}</span>
                    <span class="font-medium text-slate-300">{{ selectedAmbulance.telemetry?.positioning?.speedKmh ?? "—" }} km/h</span>
                  </div>
                  <div class="flex items-center justify-between text-xs">
                    <span class="text-slate-500">{{ t('operations.speed_limit') }}</span>
                    <span class="font-medium text-slate-300">{{ selectedAmbulance.roadSpeedLimitKmh ?? selectedAmbulance.telemetry?.positioning?.roadSpeedLimitKmh ?? "—" }} km/h</span>
                  </div>
                  <div class="flex flex-col gap-0.5 text-xs">
                    <span class="text-slate-500">Destino / objetivo</span>
                    <span class="font-medium leading-snug text-slate-300">{{ destinationLabel }}</span>
                  </div>
                </div>
              </div>
              <p v-else-if="state.ambulances.length" class="text-xs text-slate-500">Elige una ambulancia o haz clic en el mapa.</p>
            </div>

            <div v-if="state?.companions?.length" class="space-y-2 border-t border-slate-800/60 pt-3">
              <label class="text-[10px] font-medium text-slate-500">Unidad de apoyo (mapa)</label>
              <select
                :value="selectedCompanionId ?? ''"
                class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none"
                @change="onTelemetryCompanionChange"
              >
                <option value="">{{ t('operations.no_companion_focus') }}</option>
                <option v-for="c in state.companions" :key="c.id" :value="c.id">
                  {{ c.displayLabel ?? c.kind }} · {{ c.status }} · {{ c.typeName ?? c.kind }}
                </option>
              </select>
              <div v-if="selectedCompanion" class="rounded-lg border border-slate-800/80 bg-slate-950/50 px-2.5 py-2 text-[10px] leading-relaxed text-slate-400">
                <p class="font-mono text-sm text-emerald-200">{{ selectedCompanion.displayLabel ?? selectedCompanion.kind }}</p>
                <p>{{ selectedCompanion.typeName ?? selectedCompanion.kind }} — {{ selectedCompanion.status }}</p>
                <p>Velocidad: {{ selectedCompanion.speedKmh }} km/h</p>
                <p v-if="state.emergencies.find((e) => e.id === selectedCompanion?.assignedEmergencyId)">
                  Emergencia:
                  <span class="text-slate-200">{{
                    state.emergencies.find((e) => e.id === selectedCompanion?.assignedEmergencyId)?.title
                  }}</span>
                </p>
                <p class="font-mono text-slate-500">
                  GPS {{ selectedCompanion.latitude.toFixed(5) }}, {{ selectedCompanion.longitude.toFixed(5) }}
                </p>
              </div>
            </div>
          </template>
          <p v-else class="text-xs text-slate-500">Anade ambulancias o despacha apoyo para ver telemetria.</p>
        </aside>
      </div>

      <!-- Toolbar: solo acciones rápidas (colocación de objetos va en la paleta superior) -->
      <div
        class="flex shrink-0 flex-wrap items-center justify-center gap-1.5 border-t border-slate-800/60 bg-slate-950 px-3 py-2.5"
      >
        <span class="mr-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{{ t('operations.quick_actions') }}</span>

        <!-- Incident generator -->
        <div class="relative">
          <button
            type="button"
            :title="t('operations.ai_incidents_tooltip')"
            class="flex items-center gap-1.5 rounded-xl border border-purple-600/40 px-3 py-2 text-[11px] font-medium text-purple-400 transition-colors hover:bg-purple-950/30"
            :class="showIncidentGen ? 'bg-purple-950/40 ring-1 ring-purple-500/30' : ''"
            @click="showIncidentGen = !showIncidentGen"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
{{ t('operations.generate') }}
          </button>
          <div
            v-if="showIncidentGen"
            class="absolute bottom-full right-0 mb-2 z-[500] w-80 rounded-xl border border-slate-700/70 bg-slate-900 p-4 shadow-2xl"
          >
            <h4 class="text-xs font-semibold text-purple-400 mb-2">{{ t('controls.generate_scenario') }}</h4>
            <div class="mb-3 flex rounded-lg border border-slate-700/80 p-0.5 text-[10px] font-medium">
              <button
                type="button"
                class="flex-1 rounded-md px-2 py-1 transition"
                :class="aiGenTab === 'incidents' ? 'bg-purple-700/40 text-purple-100' : 'text-slate-500 hover:text-slate-300'"
                @click="aiGenTab = 'incidents'"
              >{{ t('operations.incidents_btn') }}</button>
              <button
                type="button"
                class="flex-1 rounded-md px-2 py-1 transition"
                :class="aiGenTab === 'scenario' ? 'bg-purple-700/40 text-purple-100' : 'text-slate-500 hover:text-slate-300'"
                @click="aiGenTab = 'scenario'"
              >{{ t('operations.full_scenario_btn') }}</button>
            </div>

            <div v-if="aiGenTab === 'incidents'">
              <p class="text-[10px] text-slate-500 mb-3">
                Crea emergencias realistas y variadas en la zona de simulación.
              </p>
              <div class="flex items-end gap-2">
                <div class="flex-1">
                  <label class="block text-[10px] font-medium text-slate-400 mb-1">{{ t('operations.amount') }}</label>
                  <input
                    v-model.number="incidentCount"
                    type="number" min="1" max="20"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-200 focus:border-purple-500 focus:outline-none"
                  />
                </div>
                <button
                  class="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-slate-300 disabled:opacity-50 whitespace-nowrap"
                  :disabled="entities.loading"
                  @click="generateIncidents"
                >
                  {{ entities.loading ? t('operations.generating') : t('operations.generate') }}
                </button>
              </div>
            </div>

            <div v-else>
              <p class="text-[10px] text-slate-500 mb-3">
                Genera un escenario entero: estructuras (hospitales, gasolineras…), flota y opcionalmente incidencias iniciales.
              </p>
              <div class="grid grid-cols-2 gap-2 mb-2">
                <label class="block">
                  <span class="block text-[10px] font-medium text-slate-400 mb-1">{{ t('scenario.hospitals') }}</span>
                  <input v-model.number="scenarioForm.hospitals" type="number" min="0" max="20"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-200 focus:border-purple-500 focus:outline-none" />
                </label>
                <label class="block">
                  <span class="block text-[10px] font-medium text-slate-400 mb-1">{{ t('scenario.gas_stations') }}</span>
                  <input v-model.number="scenarioForm.gasStations" type="number" min="0" max="20"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-200 focus:border-purple-500 focus:outline-none" />
                </label>
                <label class="block">
                  <span class="block text-[10px] font-medium text-slate-400 mb-1">{{ t('scenario.ambulances') }}</span>
                  <input v-model.number="scenarioForm.ambulances" type="number" min="0" max="30"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-200 focus:border-purple-500 focus:outline-none" />
                </label>
                <label class="block">
                  <span class="block text-[10px] font-medium text-slate-400 mb-1">{{ t('scenario.incidents') }}</span>
                  <input v-model.number="scenarioForm.incidents" type="number" min="0" max="30"
                    class="w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-200 focus:border-purple-500 focus:outline-none" />
                </label>
              </div>
              <details class="mb-2 rounded-lg border border-slate-800/60 bg-slate-950/40 text-[11px]">
                <summary class="cursor-pointer px-2 py-1.5 text-slate-400 hover:text-slate-200">
                  Unidades extra (tipos personalizados)
                </summary>
                <div class="max-h-36 overflow-y-auto border-t border-slate-800/60 p-2 space-y-1.5">
                  <div
                    v-for="et in vehicleEntityTypes.filter(v => v.id !== 'ambulance')"
                    :key="et.id"
                    class="flex items-center justify-between gap-2 text-slate-300"
                  >
                    <span class="truncate">{{ iconForVehicleType(et.id, et.name) }} {{ et.name }}</span>
                    <input
                      v-model.number="scenarioForm.extraByType[et.id]"
                      type="number" min="0" max="15"
                      class="w-14 rounded border border-slate-700 bg-slate-800 px-2 py-0.5 text-right text-xs text-slate-200 focus:border-purple-500 focus:outline-none"
                      placeholder="0"
                    />
                  </div>
                  <p v-if="!vehicleEntityTypes.filter(v => v.id !== 'ambulance').length" class="text-center text-[10px] text-slate-600 py-1">
                    Sin tipos personalizados
                  </p>
                </div>
              </details>
              <label class="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
                <input v-model="scenarioForm.clearExisting" type="checkbox"
                  class="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-purple-500 focus:ring-purple-500/30" />
                Resetear escenario antes de generar
              </label>
              <button
                class="w-full rounded-lg bg-slate-100 px-3 py-2 text-xs font-semibold text-slate-950 transition hover:bg-slate-300 disabled:opacity-50"
                :disabled="generatingScenario"
                @click="generateFullScenario"
              >
                {{ generatingScenario ? t('operations.generating_scenario') : t('operations.generate_scenario') }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
