<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { getSupabase } from "@/lib/supabase";
import { cssVar, useTheme } from "@/composables/useTheme";
import { addBasemap, type Basemap } from "@/lib/basemap";
import { DEFAULT_SPAWN_LAT, DEFAULT_SPAWN_LON } from "@/lib/mapDefaults";

const { t, te } = useI18n();
const { theme } = useTheme();

/* Color por defecto de un tipo nuevo: es un dato que se envía al backend y
   alimenta <input type="color">, que solo acepta hex. La UI de esta vista no
   lo pinta (estética monocroma). */
const DEFAULT_TYPE_COLOR = "#8a8f98";

type Stage = "picker" | "register" | "locating" | "registering" | "on-duty" | "error";

interface SavedVehicle {
  id: string;
  ownerUserId: string;
  entityTypeId: string;
  displayLabel: string;
  lastLat: number | null;
  lastLon: number | null;
  createdAt?: string;
}

interface Vehicle {
  id: string;
  entityTypeId: string;
  displayLabel?: string;
  latitude: number;
  longitude: number;
  assignedEmergencyId?: string | null;
  missionPhase?: string | null;
  missionStatus?: string;
  routeCoords?: [number, number][] | null;
  targetHospitalId?: string | null;
  fsmState?: string;
  manualControl?: boolean;
}
interface Emergency {
  id: string;
  latitude: number;
  longitude: number;
  title?: string;
  description?: string;
  emergencyType?: string;
  severity?: string;
  status?: string;
}
interface Step {
  distance: number;      // metros
  duration: number;      // seg
  maneuver: {
    type: string;
    modifier?: string;
    bearing_before?: number;
    bearing_after?: number;
    location: [number, number]; // [lon, lat]
  };
  name?: string;
  geometry?: any;
}
interface Route {
  distance: number;
  duration: number;
  geometry: { coordinates: [number, number][] };
  legs: Array<{ steps: Step[] }>;
}

const router = useRouter();

interface VehicleType {
  id: string;
  name: string;
  icon: string;
  color: string;
  disabled?: boolean;
  builtIn?: boolean;
  description?: string | null;
  capabilities?: string[];
  speedKmh?: number | null;
  powertrain?: "combustion" | "electric" | "unique" | null;
  crewMin?: number;
  crewMax?: number;
  costPerMin?: number;
  activationCost?: number;
}

// Fallback icon por id/kind cuando el tipo no tiene iconSvg.
function iconForType(id: string, name?: string): string {
  const s = `${id} ${name || ""}`.toLowerCase();
  if (s.includes("drone") || s.includes("dron"))   return "🛸";
  if (s.includes("heli"))        return "🚁";
  if (s.includes("civil"))       return "🛻";
  if (s.includes("police") || s.includes("patrol") || s.includes("polic")) return "🚓";
  if (s.includes("fire") || s.includes("bomb"))    return "🚒";
  if (s.includes("moto"))        return "🏍️";
  if (s.includes("boat") || s.includes("barco"))   return "🚤";
  return "🚑";
}

// Catálogo de tipos: carga desde /api/fleet/types (incluye builtin, manual y IA).
const VEHICLE_TYPES = ref<VehicleType[]>([]);

const LS_KEY = "sentinel.vehicle";

const stage = ref<Stage>("picker");
const errorMsg = ref<string | null>(null);
const userEmail = ref<string | null>(null);
const userId = ref<string | null>(null);
const savedVehicles = ref<SavedVehicle[]>([]);

const form = ref({
  vehicleType: "ambulance",
  locationSource: "geo" as "geo" | "manual",
  latInput: String(DEFAULT_SPAWN_LAT),
  lonInput: String(DEFAULT_SPAWN_LON),
  callsign: "",
});
const myCoords = ref<{ lat: number; lon: number } | null>(null);
const myVehicleId = ref<string | null>(null);
const myVehicle = ref<Vehicle | null>(null);
const assignedEmergency = ref<Emergency | null>(null);

// Navegación
const navigating = ref(false);
const route = ref<Route | null>(null);
const stepIndex = ref(0);
const distanceRemaining = ref(0); // metros
const etaSeconds = ref(0);
const lastHeading = ref(0);
const lastCoord = ref<[number, number] | null>(null);

let mapInstance: L.Map | null = null;
let myMarker: L.Marker | null = null;
let emergencyMarker: L.Marker | null = null;
let routeLine: L.Polyline | null = null;
let routeLineShadow: L.Polyline | null = null;
let pollTimer: number | null = null;

onMounted(async () => {
  const sb = getSupabase();
  if (sb) {
    const { data } = await sb.auth.getUser();
    userEmail.value = data.user?.email ?? null;
    userId.value = data.user?.id ?? null;
  }
  await loadVehicleTypes();

  // Si hay una sesión activa runtime (reload de página), restaurar on-duty
  const saved = localStorage.getItem(LS_KEY);
  if (saved) {
    try {
      const obj = JSON.parse(saved);
      if (obj.vehicleId) {
        myVehicleId.value = obj.vehicleId;
        stage.value = "on-duty";
        await nextTick();
        initMap(obj.lastLat || DEFAULT_SPAWN_LAT, obj.lastLon || DEFAULT_SPAWN_LON);
        startPolling();
        return;
      }
    } catch { /* ignore */ }
  }

  // Carga unidades persistidas del usuario (si hay sesión). Si tiene → picker,
  // si no → salta directo al formulario de registro.
  await loadSavedVehicles();
  stage.value = savedVehicles.value.length > 0 ? "picker" : "register";
});

async function loadVehicleTypes() {
  try {
    const r = await fetch("/api/fleet/types");
    if (!r.ok) return;
    const raw = await r.json() as Array<{
      id: string; kind: string; name: string; color?: string; iconSvg?: string | null;
      builtIn?: boolean; description?: string | null; capabilities?: string[]; speedKmh?: number;
    }>;
    const vehicles = raw.filter((t) => t.kind === "vehicle");
    VEHICLE_TYPES.value = vehicles.map((t) => ({
      id: t.id,
      name: t.name,
      icon: iconForType(t.id, t.name),
      color: t.color || DEFAULT_TYPE_COLOR,
      builtIn: Boolean(t.builtIn),
      description: t.description ?? null,
      capabilities: t.capabilities || [],
      speedKmh: t.speedKmh ?? null,
    }));
    if (!VEHICLE_TYPES.value.find((t) => t.id === form.value.vehicleType)) {
      form.value.vehicleType = VEHICLE_TYPES.value[0]?.id || "ambulance";
    }
  } catch {
    VEHICLE_TYPES.value = [{ id: "ambulance", name: "Ambulancia", icon: "🚑", color: DEFAULT_TYPE_COLOR }];
  }
}

// ── Editor inline de tipos ──────────────────────────────────────────────
const typeEditor = ref<{
  open: boolean;
  mode: "create" | "edit";
  id: string | null;
  name: string;
  speedKmh: string;
  color: string;
  description: string;
  capabilitiesStr: string;
  saving: boolean;
}>({
  open: false, mode: "create", id: null, name: "", speedKmh: "80", color: DEFAULT_TYPE_COLOR,
  description: "", capabilitiesStr: "", saving: false,
});

function openTypeCreate() {
  typeEditor.value = {
    open: true, mode: "create", id: null, name: "", speedKmh: "80", color: DEFAULT_TYPE_COLOR,
    description: "", capabilitiesStr: "", saving: false,
  };
}
function openTypeEdit(t: VehicleType) {
  typeEditor.value = {
    open: true, mode: "edit", id: t.id, name: t.name,
    speedKmh: t.speedKmh ? String(t.speedKmh) : "80",
    color: t.color,
    description: t.description || "",
    capabilitiesStr: (t.capabilities || []).join(", "),
    saving: false,
  };
}
function closeTypeEditor() { typeEditor.value.open = false; }

async function saveTypeEditor() {
  const ed = typeEditor.value;
  if (!ed.name.trim()) return;
  ed.saving = true;
  const payload: any = {
    name: ed.name.trim(),
    kind: "vehicle",
    speedKmh: Number(ed.speedKmh) || 80,
    color: ed.color,
    description: ed.description.trim() || null,
    capabilities: ed.capabilitiesStr
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
  // capabilities vacío → null para que backend regenere
  if (payload.capabilities.length === 0) payload.capabilities = null;
  try {
    if (ed.mode === "create") {
      await fetch("/api/sim/entity-types", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else if (ed.id) {
      await fetch(`/api/sim/entity-types/${ed.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    await loadVehicleTypes();
    closeTypeEditor();
    if (ed.mode === "create" && VEHICLE_TYPES.value.length > 0) {
      form.value.vehicleType = VEHICLE_TYPES.value[VEHICLE_TYPES.value.length - 1].id;
    }
  } catch (e) {
    errorMsg.value = `Error guardando tipo: ${(e as Error).message}`;
  } finally {
    ed.saving = false;
  }
}

async function deleteType(t: VehicleType) {
  if (t.builtIn) return;
  if (!confirm(`¿Eliminar el tipo "${t.name}"? Las unidades que usen este tipo dejarán de funcionar.`)) return;
  try {
    await fetch(`/api/sim/entity-types/${t.id}`, { method: "DELETE" });
    await loadVehicleTypes();
  } catch { /* ignore */ }
}

async function loadSavedVehicles() {
  if (!userId.value) { savedVehicles.value = []; return; }
  try {
    const r = await fetch(`/api/fleet/vehicles?ownerUserId=${encodeURIComponent(userId.value)}`);
    if (!r.ok) { savedVehicles.value = []; return; }
    savedVehicles.value = await r.json();
  } catch {
    savedVehicles.value = [];
  }
}

onBeforeUnmount(() => {
  stopPolling();
  basemap?.remove(); basemap = null;
  if (mapInstance) { mapInstance.remove(); mapInstance = null; }
});

async function useGeoLocation(): Promise<void> {
  if (!navigator.geolocation) throw new Error("Geolocalización no soportada");
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        myCoords.value = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        resolve();
      },
      (err) => reject(new Error(`Ubicación: ${err.message}`)),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  });
}

function useManualLocation() {
  const lat = Number(form.value.latInput);
  const lon = Number(form.value.lonInput);
  if (!isFinite(lat) || !isFinite(lon)) throw new Error("Coordenadas no válidas");
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) throw new Error("Rango de coordenadas inválido");
  myCoords.value = { lat, lon };
}

async function activateSavedVehicle(sv: SavedVehicle) {
  errorMsg.value = null;
  stage.value = "locating";
  try {
    // Prefiere geo actual; si falla, usa la última posición conocida.
    try {
      await useGeoLocation();
    } catch {
      if (sv.lastLat != null && sv.lastLon != null) {
        myCoords.value = { lat: sv.lastLat, lon: sv.lastLon };
      } else {
        throw new Error("Sin ubicación y sin última conocida");
      }
    }
  } catch (e) {
    errorMsg.value = (e as Error).message;
    stage.value = "error";
    return;
  }
  if (!myCoords.value) { stage.value = "error"; errorMsg.value = "Sin ubicación"; return; }

  stage.value = "registering";
  try {
    const r = await fetch("/api/sim/spawn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        latitude: myCoords.value.lat,
        longitude: myCoords.value.lon,
        entityTypeId: sv.entityTypeId,
        displayLabel: sv.displayLabel,
      }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    myVehicleId.value = String(j.id);
    localStorage.setItem(LS_KEY, JSON.stringify({
      vehicleId: myVehicleId.value,
      persistedId: sv.id,
      lastLat: myCoords.value.lat,
      lastLon: myCoords.value.lon,
    }));
    stage.value = "on-duty";
    await nextTick();
    initMap(myCoords.value.lat, myCoords.value.lon);
    startPolling();
  } catch (e) {
    errorMsg.value = `Error activando: ${(e as Error).message}`;
    stage.value = "error";
  }
}

async function deleteSavedVehicle(sv: SavedVehicle) {
  if (!confirm(`¿Eliminar la unidad "${sv.displayLabel}"?`)) return;
  try {
    await fetch(`/api/fleet/vehicles/${sv.id}`, { method: "DELETE" });
    await loadSavedVehicles();
    if (savedVehicles.value.length === 0) stage.value = "register";
  } catch { /* ignore */ }
}

async function register() {
  errorMsg.value = null;
  try {
    stage.value = "locating";
    if (form.value.locationSource === "geo") await useGeoLocation();
    else useManualLocation();
  } catch (e) {
    errorMsg.value = (e as Error).message;
    stage.value = "error";
    return;
  }
  if (!myCoords.value) {
    errorMsg.value = "Sin ubicación";
    stage.value = "error";
    return;
  }
  stage.value = "registering";
  try {
    const label = form.value.callsign.trim() || `UNID-${Math.floor(Math.random() * 900 + 100)}`;
    const r = await fetch("/api/sim/spawn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        latitude: myCoords.value.lat,
        longitude: myCoords.value.lon,
        entityTypeId: form.value.vehicleType,
        displayLabel: label,
      }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    myVehicleId.value = String(j.id);

    // Persistir al catálogo para poder elegirla en futuros logins
    let persistedId: string | null = null;
    if (userId.value) {
      try {
        const pr = await fetch("/api/fleet/vehicles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ownerUserId: userId.value,
            entityTypeId: form.value.vehicleType,
            displayLabel: label,
            lastLat: myCoords.value.lat,
            lastLon: myCoords.value.lon,
          }),
        });
        if (pr.ok) {
          const pj = await pr.json();
          persistedId = pj.id || null;
        }
      } catch { /* no-db es aceptable */ }
    }

    localStorage.setItem(LS_KEY, JSON.stringify({
      vehicleId: myVehicleId.value,
      persistedId,
      lastLat: myCoords.value.lat,
      lastLon: myCoords.value.lon,
    }));
    stage.value = "on-duty";
    await nextTick();
    initMap(myCoords.value.lat, myCoords.value.lon);
    startPolling();
  } catch (e) {
    errorMsg.value = `Error registrando: ${(e as Error).message}`;
    stage.value = "error";
  }
}

let basemap: Basemap | null = null;

function initMap(centerLat: number, centerLon: number) {
  if (mapInstance) return;
  const el = document.getElementById("vehicle-map");
  if (!el) return;
  mapInstance = L.map(el, { zoomControl: false, attributionControl: false })
    .setView([centerLat, centerLon], 15);
  L.control.zoom({ position: "topright" }).addTo(mapInstance);
  L.control.attribution({ position: "bottomright", prefix: false })
    .addAttribution("OSRM").addTo(mapInstance);
  basemap = addBasemap(mapInstance, theme.value);
}

const vehicleIcon = computed(
  () => VEHICLE_TYPES.value.find((t) => t.id === (myVehicle.value?.entityTypeId || form.value.vehicleType))?.icon || "🚑",
);

// Marcador monocromo; pasa a ámbar (estado "en ruta") con incidencia asignada.
function vehicleDivIcon(icon: string, heading: number, assigned: boolean): L.DivIcon {
  return L.divIcon({
    className: assigned ? "vh-marker is-assigned" : "vh-marker",
    html: `
      <div class="vh-marker-pin">${icon}</div>
      <div class="vh-marker-arrow" style="transform:rotate(${heading}deg)"></div>
    `,
    iconSize: [40, 40],
    iconAnchor: [20, 20],
  });
}

function emergencyDivIcon(): L.DivIcon {
  return L.divIcon({
    className: "vh-marker vh-marker-emergency",
    html: `<div class="vh-marker-pin">!</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });
}

// Métricas auxiliares ─────────────────────────────────────────────────────
function haversine(a: [number, number], b: [number, number]): number {
  const toRad = (x: number) => (x * Math.PI) / 180;
  const R = 6371000;
  const dLat = toRad(b[0] - a[0]);
  const dLon = toRad(b[1] - a[1]);
  const s1 = Math.sin(dLat / 2) ** 2;
  const s2 = Math.sin(dLon / 2) ** 2;
  const h = s1 + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * s2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function bearing(a: [number, number], b: [number, number]): number {
  const toRad = (x: number) => (x * Math.PI) / 180;
  const φ1 = toRad(a[0]); const φ2 = toRad(b[0]);
  const λ1 = toRad(a[1]); const λ2 = toRad(b[1]);
  const y = Math.sin(λ2 - λ1) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(λ2 - λ1);
  const deg = (Math.atan2(y, x) * 180) / Math.PI;
  return (deg + 360) % 360;
}
function fmtDistance(m: number): string {
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  return `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`;
}
function fmtEta(secs: number): string {
  if (secs < 60) return `${Math.round(secs)} s`;
  const m = Math.round(secs / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60); const mm = m % 60;
  return `${h}h ${mm}m`;
}

const maneuverText = computed((): { text: string; icon: string } => {
  if (!route.value) return { text: "—", icon: "↑" };
  const steps = route.value.legs[0]?.steps || [];
  const s = steps[stepIndex.value];
  if (!s) return { text: "Continúa", icon: "↑" };
  const m = s.maneuver;
  const mod = m.modifier || "";
  const dir: Record<string, { text: string; icon: string }> = {
    left:          { text: "Gira a la izquierda",       icon: "↰" },
    right:         { text: "Gira a la derecha",         icon: "↱" },
    "slight left":  { text: "Ligera izquierda",          icon: "↖" },
    "slight right": { text: "Ligera derecha",            icon: "↗" },
    "sharp left":   { text: "Giro cerrado izquierda",    icon: "⬅" },
    "sharp right":  { text: "Giro cerrado derecha",      icon: "➡" },
    straight:      { text: "Continúa recto",             icon: "↑" },
    uturn:         { text: "Cambia de sentido",          icon: "↺" },
  };
  if (m.type === "depart") return { text: "Comienza la ruta", icon: "↑" };
  if (m.type === "arrive") return { text: "Has llegado", icon: "◎" };
  if (m.type === "roundabout" || m.type === "rotary") return { text: "Toma la rotonda", icon: "⟳" };
  if (mod && dir[mod]) return dir[mod];
  return { text: "Continúa", icon: "↑" };
});

const nextStepDistance = computed(() => {
  const steps = route.value?.legs[0]?.steps || [];
  const s = steps[stepIndex.value];
  return s ? s.distance : 0;
});

// Navegación ──────────────────────────────────────────────────────────────
async function fetchRoute(origin: [number, number], dest: [number, number]): Promise<Route | null> {
  const url = `/api/osrm/route/v1/driving/${origin[1]},${origin[0]};${dest[1]},${dest[0]}?steps=true&overview=full&geometries=geojson&annotations=false`;
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    const j = await r.json();
    if (j.code !== "Ok" || !j.routes?.length) return null;
    return j.routes[0] as Route;
  } catch { return null; }
}

async function startNavigation() {
  if (!myVehicle.value || !assignedEmergency.value) return;
  const v = myVehicle.value;
  const e = assignedEmergency.value;
  const rt = await fetchRoute([v.latitude, v.longitude], [e.latitude, e.longitude]);
  if (!rt) {
    errorMsg.value = "No se pudo calcular la ruta (OSRM)";
    return;
  }
  route.value = rt;
  stepIndex.value = 0;
  distanceRemaining.value = rt.distance;
  etaSeconds.value = rt.duration;
  navigating.value = true;
  updateMap(true);
}

function stopNavigation() {
  navigating.value = false;
  route.value = null;
  stepIndex.value = 0;
  distanceRemaining.value = 0;
  etaSeconds.value = 0;
  updateMap();
}

function advanceStepIfClose(vPos: [number, number]) {
  if (!route.value) return;
  const steps = route.value.legs[0]?.steps || [];
  while (stepIndex.value < steps.length - 1) {
    const next = steps[stepIndex.value + 1];
    if (!next) break;
    const mloc = next.maneuver.location; // [lon, lat]
    const d = haversine(vPos, [mloc[1], mloc[0]]);
    if (d < 25) stepIndex.value++;
    else break;
  }
}

function recomputeNavMetrics(vPos: [number, number]) {
  if (!route.value || !assignedEmergency.value) return;
  advanceStepIfClose(vPos);
  const e = assignedEmergency.value;
  distanceRemaining.value = haversine(vPos, [e.latitude, e.longitude]);
  // ETA proporcional: mantiene ratio velocidad original del motor
  const ratio = route.value.distance > 0
    ? distanceRemaining.value / route.value.distance
    : 0;
  etaSeconds.value = route.value.duration * ratio;
}

// Render mapa ─────────────────────────────────────────────────────────────
function updateMap(autoCenter = false) {
  if (!mapInstance) return;
  const v = myVehicle.value;
  if (!v) return;
  const vtype = VEHICLE_TYPES.value.find((t) => t.id === v.entityTypeId)
    || VEHICLE_TYPES.value[0]
    || { id: "ambulance", name: "Ambulancia", icon: "🚑", color: DEFAULT_TYPE_COLOR };
  const vPos: [number, number] = [v.latitude, v.longitude];

  // Heading desde último coord → actual
  if (lastCoord.value) {
    const d = haversine(lastCoord.value, vPos);
    if (d > 1) lastHeading.value = bearing(lastCoord.value, vPos);
  }
  lastCoord.value = vPos;

  if (!myMarker) {
    myMarker = L.marker(vPos, { icon: vehicleDivIcon(vtype.icon, lastHeading.value, !!assignedEmergency.value) }).addTo(mapInstance);
  } else {
    myMarker.setLatLng(vPos);
    myMarker.setIcon(vehicleDivIcon(vtype.icon, lastHeading.value, !!assignedEmergency.value));
  }

  // Emergencia
  const em = assignedEmergency.value;
  if (em) {
    const ePos: [number, number] = [em.latitude, em.longitude];
    if (!emergencyMarker) {
      emergencyMarker = L.marker(ePos, { icon: emergencyDivIcon() }).addTo(mapInstance);
      emergencyMarker.bindPopup(`<b>${em.title || "Emergencia"}</b><br/>${em.description || ""}`);
    } else {
      emergencyMarker.setLatLng(ePos);
    }
  } else if (emergencyMarker) {
    mapInstance.removeLayer(emergencyMarker);
    emergencyMarker = null;
  }

  // Route polyline — prefiere OSRM (steps geojson) si navegamos, si no la del motor
  const routeFromOsrm = navigating.value && route.value
    ? route.value.geometry.coordinates.map((c) => [c[1], c[0]] as [number, number])
    : null;
  const routeCoords = routeFromOsrm || (v.routeCoords && v.routeCoords.length >= 2 ? v.routeCoords : null);

  if (routeCoords) {
    // Ruta de máximo contraste con el mapa (se invierte con el tema) sobre un
    // ribete del color de fondo para separarla de las calles.
    const ROUTE_COLOR = cssVar("--n-100");
    const CASING_COLOR = cssVar("--n-950");
    if (!routeLineShadow) {
      routeLineShadow = L.polyline(routeCoords, { color: CASING_COLOR, weight: 11, opacity: 0.9 }).addTo(mapInstance);
    } else {
      routeLineShadow.setLatLngs(routeCoords);
      routeLineShadow.setStyle({ color: CASING_COLOR });
    }
    if (!routeLine) {
      routeLine = L.polyline(routeCoords, { color: ROUTE_COLOR, weight: 7, opacity: 1 }).addTo(mapInstance);
    } else {
      routeLine.setLatLngs(routeCoords);
      routeLine.setStyle({ color: ROUTE_COLOR, weight: 7, opacity: 1 });
    }
  } else {
    if (routeLine) { mapInstance.removeLayer(routeLine); routeLine = null; }
    if (routeLineShadow) { mapInstance.removeLayer(routeLineShadow); routeLineShadow = null; }
  }

  // Centrado
  if (navigating.value) {
    mapInstance.setView(vPos, 17, { animate: true });
  } else if (autoCenter && routeCoords) {
    const bounds = L.latLngBounds(routeCoords);
    bounds.extend(vPos);
    if (em) bounds.extend([em.latitude, em.longitude]);
    mapInstance.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
  }

  if (navigating.value) recomputeNavMetrics(vPos);
}

async function poll() {
  if (!myVehicleId.value) return;
  try {
    const r = await fetch("/api/sim/state", { cache: "no-store" });
    if (!r.ok) return;
    const s = await r.json();
    const ambs: Vehicle[] = s.ambulances || [];
    const v = ambs.find((a) => a.id === myVehicleId.value) || null;
    myVehicle.value = v;
    if (v?.assignedEmergencyId) {
      const ems: Emergency[] = s.emergencies || [];
      const newEm = ems.find((e) => e.id === v.assignedEmergencyId) || null;
      // si cambia de emergencia, reset navegación
      if (assignedEmergency.value?.id && newEm?.id !== assignedEmergency.value.id && navigating.value) {
        stopNavigation();
      }
      assignedEmergency.value = newEm;
    } else {
      if (navigating.value) stopNavigation();
      assignedEmergency.value = null;
    }
    updateMap();
    if (v) {
      localStorage.setItem(LS_KEY, JSON.stringify({
        vehicleId: myVehicleId.value,
        lastLat: v.latitude, lastLon: v.longitude,
      }));
    }
  } catch { /* ignore */ }
}

function startPolling() {
  stopPolling();
  poll();
  pollTimer = window.setInterval(poll, 2000);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function unregister() {
  stopNavigation();
  stopPolling();
  localStorage.removeItem(LS_KEY);
  myVehicleId.value = null;
  myVehicle.value = null;
  assignedEmergency.value = null;
  basemap?.remove(); basemap = null;
  if (mapInstance) { mapInstance.remove(); mapInstance = null; }
  myMarker = emergencyMarker = routeLine = routeLineShadow = null;
  await Promise.all([loadVehicleTypes(), loadSavedVehicles()]);
  stage.value = savedVehicles.value.length > 0 ? "picker" : "register";
}

async function signOut() {
  stopPolling();
  const sb = getSupabase();
  if (sb) await sb.auth.signOut();
  localStorage.removeItem(LS_KEY);
  router.replace("/login");
}

const phaseLabels: Record<string, string> = {
  to_emergency: "En ruta",
  on_scene:     "En escena",
  to_hospital:  "Hospital",
  at_hospital:  "En hospital",
  returning:    "Regresando",
};
const currentPhaseLabel = computed(() => {
  const p = myVehicle.value?.missionPhase;
  return (p && phaseLabels[p]) || myVehicle.value?.missionStatus || "Disponible";
});

watch(() => myVehicle.value, () => updateMap(), { deep: true });
watch(theme, (next) => {
  basemap?.setTheme(next);
  updateMap();
});
</script>

<template>
  <div class="vh-shell" :class="{ nav: navigating }">
    <!-- HEADER (se oculta en modo nav) -->
    <header v-if="!navigating" class="vh-header">
      <div class="vh-brand-wrap">
        <svg class="vh-brand-mark" viewBox="0 0 64 64" width="28" height="28" aria-hidden="true"><rect x="17" y="17" width="30" height="30" fill="none" stroke="currentColor" stroke-width="3"/><rect x="28" y="28" width="8" height="8" fill="currentColor"/></svg>
        <div class="vh-brand-text">
          <div class="vh-brand">Sentinel <span class="vh-brand-sep">/</span> Vehículo</div>
          <div class="vh-brand-sub">{{ userEmail ?? "Sin sesión" }}</div>
        </div>
      </div>
      <button class="vh-logout" @click="signOut">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="square" aria-hidden="true"><path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/></svg>
        Salir
      </button>
    </header>

    <!-- PICKER: unidades guardadas del usuario -->
    <section v-if="stage === 'picker'" class="vh-register">
      <h1 class="vh-title">{{ t('vehicle.choose_unit') }}</h1>
      <p class="vh-desc">Selecciona la unidad con la que entras en servicio o registra una nueva.</p>

      <div class="vh-saved-list">
        <div v-for="sv in savedVehicles" :key="sv.id" class="vh-saved-item">
          <div class="vh-saved-main" @click="activateSavedVehicle(sv)">
            <div class="vh-saved-icon vh-glyph">
              {{ VEHICLE_TYPES.find(t => t.id === sv.entityTypeId)?.icon || "🚑" }}
            </div>
            <div class="vh-saved-info">
              <div class="vh-saved-label">{{ sv.displayLabel }}</div>
              <div class="vh-saved-sub">
                {{ VEHICLE_TYPES.find(t => t.id === sv.entityTypeId)?.name || sv.entityTypeId }}
              </div>
            </div>
            <div class="vh-saved-arrow">→</div>
          </div>
          <button class="vh-saved-del" @click.stop="deleteSavedVehicle(sv)" aria-label="Eliminar">✕</button>
        </div>
      </div>

      <button class="vh-btn-secondary-outline" @click="stage = 'register'">
        + Registrar nueva unidad
      </button>
    </section>

    <!-- REGISTER -->
    <section v-else-if="stage === 'register'" class="vh-register">
      <h1 class="vh-title">{{ t('vehicle.register_unit') }}</h1>
      <p class="vh-desc">Indica el tipo y la ubicación actual para entrar en servicio.</p>

      <div class="vh-card">
        <div class="vh-card-label">
          Tipo de vehículo
          <button class="vh-type-new" @click="openTypeCreate">+ Nuevo tipo</button>
        </div>
        <div class="vh-type-list">
          <div
            v-for="vt in VEHICLE_TYPES"
            :key="vt.id"
            :class="['vh-type-row', { active: form.vehicleType === vt.id }]"
            @click="form.vehicleType = vt.id"
          >
            <div class="vh-type-row-icon vh-glyph">{{ vt.icon }}</div>
            <div class="vh-type-row-info">
              <div class="vh-type-row-name">
                {{ vt.name }}
                <span v-if="vt.speedKmh" class="vh-type-row-speed vh-num">{{ vt.speedKmh }} km/h</span>
              </div>
              <div v-if="vt.powertrain || vt.crewMin != null" class="vh-type-row-caps">
                <span v-if="vt.powertrain" class="vh-cap-chip">
                  {{ t('scenario.powertrain_' + vt.powertrain) }}
                </span>
                <span v-if="vt.crewMin != null" class="vh-cap-chip">
                  {{ t('scenario.crew_label') }}: <span class="vh-num">{{ vt.crewMin === vt.crewMax ? vt.crewMin : `${vt.crewMin}-${vt.crewMax}` }}</span>
                </span>
                <span v-if="vt.costPerMin != null" class="vh-cap-chip">
                  <span class="vh-num">{{ vt.costPerMin.toFixed(2) }}</span> {{ t('scenario.cost_per_min_short') }}
                </span>
                <span v-if="vt.activationCost != null" class="vh-cap-chip">
                  <span class="vh-num">{{ vt.activationCost.toFixed(0) }}€</span> {{ t('scenario.activation_cost_short') }}
                </span>
              </div>
              <div v-if="vt.description" class="vh-type-row-desc">{{ vt.description }}</div>
              <div v-if="vt.capabilities && vt.capabilities.length" class="vh-type-row-caps">
                <span v-for="c in vt.capabilities" :key="c" class="vh-cap-chip">{{ c }}</span>
              </div>
            </div>
            <div class="vh-type-row-actions" v-if="!vt.builtIn" @click.stop>
              <button class="vh-type-act" :title="t('vehicle.edit')" @click="openTypeEdit(vt)">✎</button>
              <button class="vh-type-act danger" :title="t('vehicle.delete')" @click="deleteType(vt)">✕</button>
            </div>
            <div v-else class="vh-type-row-badge">{{ t('scenario.builtin') }}</div>
          </div>
        </div>
      </div>

      <!-- Editor modal (overlay inline) -->
      <div v-if="typeEditor.open" class="vh-modal-bg" @click.self="closeTypeEditor">
        <div class="vh-modal">
          <div class="vh-modal-head">
            <div class="vh-modal-title">{{ typeEditor.mode === "create" ? "Nuevo tipo" : "Editar tipo" }}</div>
            <button class="vh-modal-close" @click="closeTypeEditor">✕</button>
          </div>
          <div class="vh-modal-body">
            <label class="vh-field">
              <span>Nombre <span class="req">*</span></span>
              <input v-model="typeEditor.name" class="vh-input" :placeholder="t('vehicle.type_name_ph')" />
            </label>
            <div class="vh-field-row">
              <label class="vh-field">
                <span>Velocidad (km/h)</span>
                <input v-model="typeEditor.speedKmh" class="vh-input vh-num" inputmode="numeric" />
              </label>
              <label class="vh-field">
                <span>Color</span>
                <input v-model="typeEditor.color" type="color" class="vh-color-input" />
              </label>
            </div>
            <label class="vh-field">
              <span>
                Descripción
                <span class="vh-field-hint">— si lo dejas vacío, la IA la calcula según el nombre</span>
              </span>
              <textarea v-model="typeEditor.description" class="vh-input" rows="2" :placeholder="t('vehicle.type_desc_ph')" />
            </label>
            <label class="vh-field">
              <span>
                Capacidades (separadas por coma)
                <span class="vh-field-hint">— si lo dejas vacío, la IA las calcula según el nombre</span>
              </span>
              <input v-model="typeEditor.capabilitiesStr" class="vh-input" :placeholder="t('vehicle.type_caps_ph')" />
            </label>
          </div>
          <div class="vh-modal-foot">
            <button class="vh-btn-ghost" @click="closeTypeEditor">{{ t('vehicle.cancel') }}</button>
            <button class="vh-btn-primary" :disabled="typeEditor.saving || !typeEditor.name.trim()" @click="saveTypeEditor">
              {{ typeEditor.saving ? "Guardando…" : "Guardar" }}
            </button>
          </div>
        </div>
      </div>

      <div class="vh-card">
        <div class="vh-card-label">Identificador (opcional)</div>
        <input v-model="form.callsign" class="vh-input vh-num" :placeholder="t('vehicle.callsign_ph')" maxlength="30" />
      </div>

      <div class="vh-card">
        <div class="vh-card-label">{{ t('vehicle.location') }}</div>
        <div class="vh-toggle">
          <button :class="['vh-toggle-btn', { active: form.locationSource === 'geo' }]" @click="form.locationSource = 'geo'">Usar mi ubicación</button>
          <button :class="['vh-toggle-btn', { active: form.locationSource === 'manual' }]" @click="form.locationSource = 'manual'">Manual</button>
        </div>
        <div v-if="form.locationSource === 'manual'" class="vh-latlon">
          <input v-model="form.latInput" class="vh-input vh-num" :placeholder="t('vehicle.lat_ph')" inputmode="decimal" />
          <input v-model="form.lonInput" class="vh-input vh-num" :placeholder="t('vehicle.lon_ph')" inputmode="decimal" />
        </div>
        <div v-else class="vh-hint">Aceptaremos el permiso al pulsar "Entrar en servicio".</div>
      </div>

      <button class="vh-btn-primary big" @click="register">{{ t('vehicle.enter_service') }}</button>
    </section>

    <section v-else-if="stage === 'locating'" class="vh-center">
      <div class="vh-spinner" /><h2>Obteniendo ubicación…</h2>
    </section>
    <section v-else-if="stage === 'registering'" class="vh-center">
      <div class="vh-spinner" /><h2>Registrando vehículo…</h2>
    </section>

    <!-- ON DUTY -->
    <section v-else-if="stage === 'on-duty'" class="vh-duty">
      <div id="vehicle-map" class="vh-map" />

      <!-- MODO NAVEGACIÓN: card superior con siguiente maniobra -->
      <div v-if="navigating" class="vh-nav-top">
        <div class="vh-nav-maneuver">
          <div class="vh-nav-icon">{{ maneuverText.icon }}</div>
          <div class="vh-nav-text">
            <div class="vh-nav-distance">{{ fmtDistance(nextStepDistance) }}</div>
            <div class="vh-nav-instr">{{ maneuverText.text }}</div>
          </div>
          <button class="vh-nav-close" @click="stopNavigation" aria-label="Cancelar navegación">✕</button>
        </div>
      </div>

      <!-- MODO NORMAL: status card -->
      <div v-else class="vh-overlay">
        <div class="vh-status-card" :class="{ assigned: !!assignedEmergency }">
          <div class="vh-status-row">
            <div class="vh-status-badge">
              <span class="vh-glyph">{{ vehicleIcon }}</span>
              <span class="vh-num">{{ myVehicle?.displayLabel || (myVehicleId?.slice(0, 6)) }}</span>
            </div>
            <div class="vh-status-phase">
              <span class="vh-phase-dot" :class="{ active: !!assignedEmergency }" />
              {{ currentPhaseLabel }}
            </div>
          </div>
          <div v-if="assignedEmergency" class="vh-emergency-info">
            <div class="vh-emergency-title">
              <span class="vh-emergency-dot" /> {{ assignedEmergency.title || "Emergencia asignada" }}
            </div>
            <div v-if="assignedEmergency.description" class="vh-emergency-desc">
              {{ assignedEmergency.description }}
            </div>
            <div class="vh-emergency-meta">
              <span v-if="assignedEmergency.emergencyType">{{ te(`emergency_type.${assignedEmergency.emergencyType}`) ? t(`emergency_type.${assignedEmergency.emergencyType}`) : assignedEmergency.emergencyType }}</span>
              <span v-if="assignedEmergency.severity">· {{ t('map.severity') }} {{ te(`events.severity.${assignedEmergency.severity}`) ? t(`events.severity.${assignedEmergency.severity}`) : assignedEmergency.severity }}</span>
            </div>
            <button class="vh-btn-primary nav-start" @click="startNavigation">▶ Iniciar ruta</button>
          </div>
          <div v-else class="vh-idle">Sin incidencia asignada. A la espera.</div>
        </div>
      </div>

      <!-- MODO NAVEGACIÓN: card inferior con ETA + distancia -->
      <div v-if="navigating" class="vh-nav-bottom">
        <div class="vh-nav-metric">
          <div class="vh-nav-metric-value vh-num">{{ fmtEta(etaSeconds) }}</div>
          <div class="vh-nav-metric-label">ETA</div>
        </div>
        <div class="vh-nav-metric">
          <div class="vh-nav-metric-value vh-num">{{ fmtDistance(distanceRemaining) }}</div>
          <div class="vh-nav-metric-label">{{ t('vehicle.remaining') }}</div>
        </div>
        <div class="vh-nav-metric">
          <div class="vh-nav-metric-value is-text">{{ currentPhaseLabel }}</div>
          <div class="vh-nav-metric-label">{{ t('vehicle.status') }}</div>
        </div>
      </div>

      <!-- Botón finalizar servicio (solo fuera de nav) -->
      <button v-if="!navigating" class="vh-btn-float" @click="unregister">{{ t('vehicle.end_service') }}</button>
    </section>

    <section v-else-if="stage === 'error'" class="vh-center">
      <div class="vh-cross">!</div>
      <h2>Algo falló</h2>
      <p class="vh-err">{{ errorMsg }}</p>
      <button class="vh-btn-primary" @click="stage = 'register'">{{ t('vehicle.retry') }}</button>
    </section>
  </div>
</template>

<style scoped>
.vh-shell {
  font-family: var(--font-sans);
  font-size: 13px;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh; min-height: 100dvh;
  display: flex; flex-direction: column;
  -webkit-font-smoothing: antialiased;
}
.vh-shell * { box-sizing: border-box; }
.vh-shell h2 { margin: 0; font-size: 15px; font-weight: 500; color: var(--text-2); }

/* Datos numéricos / telemetría */
.vh-num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
/* Los iconos de tipo son emoji: se neutralizan a escala de grises. */
.vh-glyph { filter: grayscale(1); }

/* Cabecera */
.vh-header {
  height: 52px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
  position: sticky; top: 0; z-index: 1000;
}
.vh-brand-wrap { display: flex; align-items: center; gap: 10px; min-width: 0; color: var(--text); }
.vh-brand-mark { flex-shrink: 0; }
.vh-brand-text { min-width: 0; line-height: 1.25; }
.vh-brand { font-weight: 600; font-size: 14px; letter-spacing: -0.01em; }
.vh-brand-sep { color: var(--text-4); font-weight: 400; margin: 0 2px; }
.vh-brand-sub {
  font-size: 11px; color: var(--text-3);
  max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.vh-logout {
  display: inline-flex; align-items: center; gap: 6px;
  height: 28px; padding: 0 10px;
  background: transparent;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  color: var(--text-2); font-family: inherit; font-size: 12px; cursor: pointer;
}
.vh-logout:hover { color: var(--text); background: var(--surface-2); }

/* Formularios (picker / registro) */
.vh-register {
  padding: 24px 16px 80px;
  max-width: 520px; width: 100%; margin: 0 auto;
  display: flex; flex-direction: column; gap: 12px;
}
.vh-title {
  font-size: 22px; font-weight: 600;
  text-align: center; letter-spacing: -0.02em; margin: 8px 0 0;
}
.vh-desc { color: var(--text-3); text-align: center; font-size: 13px; margin: 0 0 8px; }
.vh-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
}
.vh-card-label {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-3); margin-bottom: 8px; font-weight: 500;
}
.vh-type-new {
  height: 24px; padding: 0 8px;
  font-size: 11px; text-transform: none; letter-spacing: 0;
  background: transparent; color: var(--text-2);
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  cursor: pointer; font-family: inherit;
}
.vh-type-new:hover { color: var(--text); background: var(--surface-2); }

.vh-type-list {
  display: flex; flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.vh-type-row {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 12px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.vh-type-row:last-child { border-bottom: none; }
.vh-type-row:hover { background: var(--surface-2); }
.vh-type-row.active {
  background: var(--surface-2);
  box-shadow: inset 2px 0 0 var(--n-100);
}
.vh-type-row-icon {
  width: 28px; height: 28px; flex-shrink: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  display: flex; align-items: center; justify-content: center;
  font-size: 15px;
}
.vh-type-row-info { flex: 1; min-width: 0; }
.vh-type-row-name { font-weight: 500; font-size: 13px; color: var(--text); }
.vh-type-row-speed { color: var(--text-3); font-size: 12px; margin-left: 6px; }
.vh-type-row-desc {
  color: var(--text-3); font-size: 12px; line-height: 1.4;
  margin-top: 2px;
}
.vh-type-row-caps { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.vh-cap-chip {
  font-size: 11px; padding: 1px 6px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  color: var(--text-2);
}
.vh-type-row-actions { display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }
.vh-type-act {
  width: 24px; height: 24px;
  background: transparent;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm); color: var(--text-3);
  cursor: pointer; font-size: 11px; padding: 0;
  display: flex; align-items: center; justify-content: center;
}
.vh-type-act:hover { color: var(--text); background: var(--surface-2); }
.vh-type-act.danger:hover { color: var(--crit); border-color: var(--crit); }
.vh-type-row-badge {
  font-family: var(--font-mono);
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--text-4); align-self: center; padding: 1px 6px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
}

/* Modal editor */
.vh-modal-bg {
  position: fixed; inset: 0; z-index: 2000;
  background: color-mix(in oklab, var(--n-950) 70%, transparent);
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
}
.vh-modal {
  width: 100%; max-width: 460px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  overflow: hidden;
}
.vh-modal-head {
  height: 44px; padding: 0 14px;
  border-bottom: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
}
.vh-modal-title { font-weight: 600; font-size: 14px; }
.vh-modal-close {
  width: 26px; height: 26px; border: 1px solid transparent; border-radius: var(--radius-sm);
  background: transparent; color: var(--text-3);
  cursor: pointer; font-size: 12px;
}
.vh-modal-close:hover { background: var(--surface-2); color: var(--text); }
.vh-modal-body {
  padding: 14px; display: flex; flex-direction: column; gap: 12px;
}
.vh-modal-foot {
  padding: 10px 14px;
  border-top: 1px solid var(--border);
  display: flex; gap: 8px; justify-content: flex-end;
}
.vh-field { display: flex; flex-direction: column; gap: 6px; }
.vh-field > span { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-3); font-weight: 500; }
.vh-field .req { color: var(--text-4); }
.vh-field-hint { text-transform: none; letter-spacing: 0; color: var(--text-4); font-weight: 400; margin-left: 4px; }
.vh-field-row { display: grid; grid-template-columns: 2fr 1fr; gap: 10px; }
.vh-color-input {
  width: 100%; height: 34px; padding: 3px; cursor: pointer;
  background: var(--bg);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
}
.vh-btn-ghost {
  height: 32px; padding: 0 14px; background: transparent;
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg);
  color: var(--text-2); font-family: inherit; font-size: 13px; cursor: pointer;
}
.vh-btn-ghost:hover { color: var(--text); background: var(--surface-2); }
.vh-modal-foot .vh-btn-primary { width: auto; height: 32px; padding: 0 16px; font-size: 13px; }

.vh-input {
  width: 100%; height: 36px; padding: 0 10px;
  background: var(--bg);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  color: var(--text); font-family: inherit; font-size: 13px;
  outline: none;
  transition: border-color 0.12s ease;
}
textarea.vh-input { height: auto; padding: 8px 10px; resize: vertical; }
.vh-input.vh-num { font-family: var(--font-mono); }
.vh-input::placeholder { color: var(--text-4); }
.vh-input:focus { border-color: var(--focus); }

.vh-toggle {
  display: grid; grid-template-columns: 1fr 1fr;
  margin-bottom: 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.vh-toggle-btn {
  height: 32px;
  background: transparent;
  border: none;
  color: var(--text-3); font-family: inherit; font-size: 13px; cursor: pointer;
}
.vh-toggle-btn + .vh-toggle-btn { border-left: 1px solid var(--border-strong); }
.vh-toggle-btn:hover { color: var(--text); }
.vh-toggle-btn.active { background: var(--surface-2); color: var(--text); font-weight: 500; }
.vh-latlon { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.vh-hint { font-size: 12px; color: var(--text-4); }

/* Botones */
.vh-btn-primary {
  width: 100%; height: 40px; padding: 0 16px;
  border: 1px solid var(--n-100); border-radius: var(--radius-lg);
  background: var(--n-100);
  color: var(--n-950); font-family: inherit;
  font-weight: 500; font-size: 14px;
  cursor: pointer;
}
.vh-btn-primary:hover:not(:disabled) { background: var(--n-200); border-color: var(--n-200); }
.vh-btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
.vh-btn-primary.big { height: 44px; margin-top: 8px; }
.vh-btn-primary.nav-start { margin-top: 10px; }
.vh-btn-secondary-outline {
  height: 40px; margin-top: 4px;
  border-radius: var(--radius-lg);
  border: 1px dashed var(--border-strong);
  background: transparent;
  color: var(--text-2);
  font-family: inherit; font-weight: 500; font-size: 13px;
  cursor: pointer;
}
.vh-btn-secondary-outline:hover {
  border-color: var(--n-500);
  color: var(--text);
  background: var(--surface-2);
}

/* Picker de unidades guardadas */
.vh-saved-list {
  display: flex; flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.vh-saved-item {
  display: flex; align-items: stretch;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  transition: background-color 0.12s ease;
}
.vh-saved-item:last-child { border-bottom: none; }
.vh-saved-item:hover { background: var(--surface-2); }
.vh-saved-main {
  flex: 1;
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px;
  cursor: pointer;
}
.vh-saved-icon {
  width: 32px; height: 32px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.vh-saved-info { flex: 1; min-width: 0; }
.vh-saved-label {
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: 13px; font-weight: 500; color: var(--text);
}
.vh-saved-sub { font-size: 12px; color: var(--text-3); margin-top: 1px; }
.vh-saved-arrow { font-size: 16px; color: var(--text-4); flex-shrink: 0; }
.vh-saved-item:hover .vh-saved-arrow { color: var(--text); }
.vh-saved-del {
  background: transparent;
  border: none; border-left: 1px solid var(--border);
  color: var(--text-4);
  padding: 0 14px;
  font-size: 12px; cursor: pointer;
}
.vh-saved-del:hover { color: var(--crit); }

/* Estados intermedios */
.vh-center {
  flex: 1;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 14px; padding: 40px 24px;
}
.vh-spinner {
  width: 32px; height: 32px;
  border: 2px solid var(--border-strong);
  border-top-color: var(--text);
  border-radius: 50%;
  animation: vhspin 0.8s linear infinite;
}
@keyframes vhspin { to { transform: rotate(360deg); } }
.vh-cross {
  width: 56px; height: 56px; border-radius: var(--radius-lg);
  background: color-mix(in oklab, var(--crit-500) 10%, transparent);
  border: 1px solid color-mix(in oklab, var(--crit-500) 40%, transparent);
  color: var(--crit);
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono); font-size: 26px; font-weight: 600;
}
.vh-err { color: var(--text-3); font-size: 13px; text-align: center; max-width: 380px; margin: 0; }

/* En servicio */
.vh-duty {
  flex: 1; position: relative;
  display: flex; flex-direction: column; min-height: 0;
}
.vh-map { flex: 1; min-height: 0; width: 100%; }

.vh-overlay {
  position: absolute; left: 12px; right: 12px; bottom: 68px;
  z-index: 900; pointer-events: none;
}
.vh-status-card {
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
  box-shadow: var(--shadow-lg);
  pointer-events: auto;
}
.vh-status-card.assigned { border-color: var(--crit); }
.vh-status-row {
  display: flex; align-items: center; gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.vh-status-badge {
  display: inline-flex; align-items: center; gap: 6px;
  height: 24px; padding: 0 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  font-size: 12px; font-weight: 500;
  color: var(--text);
}
.vh-status-phase {
  display: flex; align-items: center; justify-content: flex-end; gap: 6px;
  color: var(--text-2); font-size: 12px; flex: 1;
}
.vh-phase-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--text-4);
}
.vh-phase-dot.active { background: var(--warn); }
.vh-emergency-info { padding-top: 10px; }
.vh-emergency-title {
  font-weight: 500; font-size: 14px;
  display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
}
/* Alerta: incidencia asignada (único parpadeo permitido). */
.vh-emergency-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  background: var(--crit);
  animation: vhblink 1s ease-in-out infinite;
}
@keyframes vhblink { 50% { opacity: 0.35; } }
.vh-emergency-desc { font-size: 13px; color: var(--text-3); line-height: 1.5; margin-bottom: 4px; }
.vh-emergency-meta { font-size: 11px; color: var(--text-4); text-transform: uppercase; letter-spacing: 0.06em; }
.vh-idle { color: var(--text-3); font-size: 13px; padding-top: 10px; }

.vh-btn-float {
  position: absolute; left: 12px; right: 12px; bottom: 16px;
  z-index: 900;
  height: 40px; border-radius: var(--radius-lg);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  color: var(--text);
  font-family: inherit; font-weight: 500; font-size: 13px;
  cursor: pointer;
  box-shadow: var(--shadow-md);
}
.vh-btn-float:hover { background: var(--surface-2); }

/* ── Modo navegación ──────────────────────────────────────────────────── */
.vh-nav-top {
  position: absolute; left: 0; right: 0; top: 0;
  z-index: 1100;
  padding: env(safe-area-inset-top, 0) 12px 0;
  pointer-events: none;
}
.vh-nav-maneuver {
  display: flex; align-items: center; gap: 12px;
  margin-top: 12px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  pointer-events: auto;
}
.vh-nav-icon {
  width: 52px; height: 52px; flex-shrink: 0;
  background: var(--n-100);
  color: var(--n-950);
  border-radius: var(--radius-lg);
  display: flex; align-items: center; justify-content: center;
  font-size: 30px; font-weight: 600; line-height: 1;
}
.vh-nav-text { flex: 1; min-width: 0; }
.vh-nav-distance {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 24px; font-weight: 600; letter-spacing: -0.02em;
  color: var(--text);
}
.vh-nav-instr {
  font-size: 14px; color: var(--text-2);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  margin-top: 1px;
}
.vh-nav-close {
  width: 32px; height: 32px;
  background: transparent;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  color: var(--text-2); font-size: 13px; cursor: pointer;
  flex-shrink: 0;
}
.vh-nav-close:hover { background: var(--surface-2); color: var(--text); }

.vh-nav-bottom {
  position: absolute; left: 12px; right: 12px; bottom: 16px;
  z-index: 1100;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  display: grid; grid-template-columns: 1fr 1fr 1fr;
  pointer-events: auto;
}
.vh-nav-metric { text-align: center; padding: 10px 8px; }
.vh-nav-metric + .vh-nav-metric { border-left: 1px solid var(--border); }
.vh-nav-metric-value {
  font-size: 18px; font-weight: 600;
  color: var(--text);
}
.vh-nav-metric-value.is-text {
  font-size: 14px; line-height: 27px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.vh-nav-metric-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-3); margin-top: 1px;
}
</style>

<style>
/* Marcadores globales (Leaflet divIcon) */
.vh-marker { position: relative; }
.vh-marker-pin {
  width: 100%; height: 100%;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: var(--n-100);
  color: var(--n-950);
  font-size: 18px; font-weight: 700;
  border: 2px solid var(--n-950);
  box-shadow: 0 0 0 1px var(--n-500);
  filter: grayscale(1);
  position: relative; z-index: 2;
}
.vh-marker.is-assigned .vh-marker-pin { background: var(--warn); filter: none; }
.vh-marker-arrow {
  position: absolute;
  top: -6px; left: 50%;
  margin-left: -7px;
  width: 0; height: 0;
  border-left: 7px solid transparent;
  border-right: 7px solid transparent;
  border-bottom: 12px solid var(--n-100);
  transform-origin: 50% calc(100% + 20px);
  transition: transform 0.4s ease;
  z-index: 1;
}
.vh-marker.is-assigned .vh-marker-arrow { border-bottom-color: var(--warn); }
.vh-marker-emergency .vh-marker-pin {
  background: var(--crit);
  color: var(--n-950);
  font-family: var(--font-mono);
  border-radius: var(--radius-sm);
  filter: none;
  animation: vh-pulse 1s ease-in-out infinite;
}
@keyframes vh-pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.12); }
}
</style>
