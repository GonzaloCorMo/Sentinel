<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { getSupabase } from "@/lib/supabase";

const { t } = useI18n();

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

const LS_KEY = "hpe-sentinel.vehicle";

const stage = ref<Stage>("picker");
const errorMsg = ref<string | null>(null);
const userEmail = ref<string | null>(null);
const userId = ref<string | null>(null);
const savedVehicles = ref<SavedVehicle[]>([]);

const form = ref({
  vehicleType: "ambulance",
  locationSource: "geo" as "geo" | "manual",
  latInput: "40.4924",
  lonInput: "-3.8736",
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
        initMap(obj.lastLat || 40.48, obj.lastLon || -3.69);
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
      color: t.color || "#01a982",
      builtIn: Boolean(t.builtIn),
      description: t.description ?? null,
      capabilities: t.capabilities || [],
      speedKmh: t.speedKmh ?? null,
    }));
    if (!VEHICLE_TYPES.value.find((t) => t.id === form.value.vehicleType)) {
      form.value.vehicleType = VEHICLE_TYPES.value[0]?.id || "ambulance";
    }
  } catch {
    VEHICLE_TYPES.value = [{ id: "ambulance", name: "Ambulancia", icon: "🚑", color: "#01a982" }];
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
  open: false, mode: "create", id: null, name: "", speedKmh: "80", color: "#01a982",
  description: "", capabilitiesStr: "", saving: false,
});

function openTypeCreate() {
  typeEditor.value = {
    open: true, mode: "create", id: null, name: "", speedKmh: "80", color: "#01a982",
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

function initMap(centerLat: number, centerLon: number) {
  if (mapInstance) return;
  const el = document.getElementById("vehicle-map");
  if (!el) return;
  mapInstance = L.map(el, { zoomControl: false, attributionControl: false })
    .setView([centerLat, centerLon], 15);
  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { maxZoom: 19 },
  ).addTo(mapInstance);
  L.control.zoom({ position: "topright" }).addTo(mapInstance);
  L.control.attribution({ position: "bottomright", prefix: false })
    .addAttribution("© OSM · CARTO · OSRM").addTo(mapInstance);
}

const vehicleColor = computed(
  () => VEHICLE_TYPES.value.find((t) => t.id === (myVehicle.value?.entityTypeId || form.value.vehicleType))?.color || "#01a982",
);
const vehicleIcon = computed(
  () => VEHICLE_TYPES.value.find((t) => t.id === (myVehicle.value?.entityTypeId || form.value.vehicleType))?.icon || "🚑",
);

function vehicleDivIcon(color: string, icon: string, heading: number): L.DivIcon {
  return L.divIcon({
    className: "vh-marker",
    html: `
      <div class="vh-marker-pin" style="background:${color};box-shadow:0 0 20px ${color}">${icon}</div>
      <div class="vh-marker-arrow" style="transform:rotate(${heading}deg);border-bottom-color:${color}"></div>
    `,
    iconSize: [40, 40],
    iconAnchor: [20, 20],
  });
}

function emergencyDivIcon(): L.DivIcon {
  return L.divIcon({
    className: "vh-marker vh-marker-emergency",
    html: `<div class="vh-marker-pin" style="background:#e53e3e;box-shadow:0 0 24px rgba(229,62,62,0.8)">!</div>`,
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
  if (m.type === "depart") return { text: "Comienza la ruta", icon: "🚗" };
  if (m.type === "arrive") return { text: "Has llegado", icon: "🏁" };
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
    || { id: "ambulance", name: "Ambulancia", icon: "🚑", color: "#01a982" };
  const vPos: [number, number] = [v.latitude, v.longitude];

  // Heading desde último coord → actual
  if (lastCoord.value) {
    const d = haversine(lastCoord.value, vPos);
    if (d > 1) lastHeading.value = bearing(lastCoord.value, vPos);
  }
  lastCoord.value = vPos;

  if (!myMarker) {
    myMarker = L.marker(vPos, { icon: vehicleDivIcon(vtype.color, vtype.icon, lastHeading.value) }).addTo(mapInstance);
  } else {
    myMarker.setLatLng(vPos);
    myMarker.setIcon(vehicleDivIcon(vtype.color, vtype.icon, lastHeading.value));
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
    const ROUTE_COLOR = "#22c55e"; // verde vivo para que sea obvia a dónde ir
    if (!routeLineShadow) {
      routeLineShadow = L.polyline(routeCoords, { color: "#000", weight: 11, opacity: 0.4 }).addTo(mapInstance);
    } else {
      routeLineShadow.setLatLngs(routeCoords);
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
</script>

<template>
  <div class="vh-shell" :class="{ nav: navigating }">
    <!-- HEADER (se oculta en modo nav) -->
    <header v-if="!navigating" class="vh-header">
      <div class="vh-brand-wrap">
        <div class="vh-brand-icon">🚑</div>
        <div>
          <div class="vh-brand">HPE Sentinel · Vehículo</div>
          <div class="vh-brand-sub">{{ userEmail ?? "Sin sesión" }}</div>
        </div>
      </div>
      <button class="vh-logout" @click="signOut">⎋ Salir</button>
    </header>

    <!-- PICKER: unidades guardadas del usuario -->
    <section v-if="stage === 'picker'" class="vh-register">
      <h1 class="vh-title">{{ t('vehicle.choose_unit') }}</h1>
      <p class="vh-desc">Selecciona la unidad con la que entras en servicio o registra una nueva.</p>

      <div class="vh-saved-list">
        <div v-for="sv in savedVehicles" :key="sv.id" class="vh-saved-item">
          <div class="vh-saved-main" @click="activateSavedVehicle(sv)">
            <div class="vh-saved-icon" :style="{ color: VEHICLE_TYPES.find(t => t.id === sv.entityTypeId)?.color || '#01a982' }">
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
            <div class="vh-type-row-icon" :style="{ color: vt.color }">{{ vt.icon }}</div>
            <div class="vh-type-row-info">
              <div class="vh-type-row-name">
                {{ vt.name }}
                <span v-if="vt.speedKmh" class="vh-type-row-speed">· {{ vt.speedKmh }} km/h</span>
              </div>
              <div v-if="vt.powertrain || vt.crewMin != null" class="vh-type-row-caps">
                <span v-if="vt.powertrain" class="vh-cap-chip">
                  {{ vt.powertrain === 'electric' ? '⚡' : vt.powertrain === 'unique' ? '🛸' : '⛽' }}
                  {{ t('scenario.powertrain_' + vt.powertrain) }}
                </span>
                <span v-if="vt.crewMin != null" class="vh-cap-chip">
                  {{ t('scenario.crew_label') }}: {{ vt.crewMin === vt.crewMax ? vt.crewMin : `${vt.crewMin}-${vt.crewMax}` }}
                </span>
                <span v-if="vt.costPerMin != null" class="vh-cap-chip">
                  {{ vt.costPerMin.toFixed(2) }} {{ t('scenario.cost_per_min_short') }}
                </span>
                <span v-if="vt.activationCost != null" class="vh-cap-chip">
                  {{ vt.activationCost.toFixed(0) }}€ {{ t('scenario.activation_cost_short') }}
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
            <div v-else class="vh-type-row-badge">Built-in</div>
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
                <input v-model="typeEditor.speedKmh" class="vh-input" inputmode="numeric" />
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
        <input v-model="form.callsign" class="vh-input" :placeholder="t('vehicle.callsign_ph')" maxlength="30" />
      </div>

      <div class="vh-card">
        <div class="vh-card-label">{{ t('vehicle.location') }}</div>
        <div class="vh-toggle">
          <button :class="['vh-toggle-btn', { active: form.locationSource === 'geo' }]" @click="form.locationSource = 'geo'">📍 Usar mi ubicación</button>
          <button :class="['vh-toggle-btn', { active: form.locationSource === 'manual' }]" @click="form.locationSource = 'manual'">✏️ Manual</button>
        </div>
        <div v-if="form.locationSource === 'manual'" class="vh-latlon">
          <input v-model="form.latInput" class="vh-input" :placeholder="t('vehicle.lat_ph')" inputmode="decimal" />
          <input v-model="form.lonInput" class="vh-input" :placeholder="t('vehicle.lon_ph')" inputmode="decimal" />
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
            <div class="vh-status-badge" :style="{ background: vehicleColor }">
              {{ vehicleIcon }} {{ myVehicle?.displayLabel || (myVehicleId?.slice(0, 6)) }}
            </div>
            <div class="vh-status-phase">{{ currentPhaseLabel }}</div>
          </div>
          <div v-if="assignedEmergency" class="vh-emergency-info">
            <div class="vh-emergency-title">
              <span class="vh-emergency-dot" /> {{ assignedEmergency.title || "Emergencia asignada" }}
            </div>
            <div v-if="assignedEmergency.description" class="vh-emergency-desc">
              {{ assignedEmergency.description }}
            </div>
            <div class="vh-emergency-meta">
              <span v-if="assignedEmergency.emergencyType">{{ assignedEmergency.emergencyType }}</span>
              <span v-if="assignedEmergency.severity">· severidad {{ assignedEmergency.severity }}</span>
            </div>
            <button class="vh-btn-primary nav-start" @click="startNavigation">▶ Iniciar ruta</button>
          </div>
          <div v-else class="vh-idle">Sin incidencia asignada. A la espera.</div>
        </div>
      </div>

      <!-- MODO NAVEGACIÓN: card inferior con ETA + distancia -->
      <div v-if="navigating" class="vh-nav-bottom">
        <div class="vh-nav-metric">
          <div class="vh-nav-metric-value">{{ fmtEta(etaSeconds) }}</div>
          <div class="vh-nav-metric-label">ETA</div>
        </div>
        <div class="vh-nav-metric">
          <div class="vh-nav-metric-value">{{ fmtDistance(distanceRemaining) }}</div>
          <div class="vh-nav-metric-label">{{ t('vehicle.remaining') }}</div>
        </div>
        <div class="vh-nav-metric">
          <div class="vh-nav-metric-value">{{ currentPhaseLabel }}</div>
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
@import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap");

.vh-shell {
  --green: #01a982;
  --green-glow: rgba(1, 169, 130, 0.25);
  --navy: #0f1b2d;
  --red: #e53e3e;
  --muted: rgba(255, 255, 255, 0.55);
  --dim: rgba(255, 255, 255, 0.3);
  font-family: "IBM Plex Sans", sans-serif;
  background: var(--navy);
  color: #fff;
  min-height: 100vh; min-height: 100dvh;
  display: flex; flex-direction: column;
  -webkit-font-smoothing: antialiased;
}
.vh-shell * { box-sizing: border-box; }

.vh-header {
  padding: 12px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.02);
  backdrop-filter: blur(10px);
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
  position: sticky; top: 0; z-index: 1000;
}
.vh-brand-wrap { display: flex; align-items: center; gap: 12px; min-width: 0; }
.vh-brand-icon {
  width: 40px; height: 40px; border-radius: 10px;
  background: rgba(1, 169, 130, 0.12); border: 1px solid rgba(1, 169, 130, 0.25);
  display: flex; align-items: center; justify-content: center; font-size: 22px;
}
.vh-brand { font-family: "Space Grotesk"; font-weight: 700; font-size: 15px; }
.vh-brand-sub {
  font-size: 11px; color: var(--muted);
  max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.vh-logout {
  padding: 7px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  color: var(--muted); font-family: inherit; font-size: 12px; cursor: pointer;
  transition: 0.2s;
}
.vh-logout:hover { border-color: var(--red); color: var(--red); }

.vh-register {
  padding: 24px 20px 80px;
  max-width: 520px; width: 100%; margin: 0 auto;
  display: flex; flex-direction: column; gap: 14px;
}
.vh-title {
  font-family: "Space Grotesk"; font-size: 24px; font-weight: 700;
  text-align: center; letter-spacing: -0.3px; margin-top: 10px;
}
.vh-desc { color: var(--muted); text-align: center; font-size: 14px; margin-bottom: 10px; }
.vh-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 12px;
  padding: 14px 16px;
}
.vh-card-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--muted); margin-bottom: 10px; font-weight: 600;
}
.vh-type-new {
  float: right; font-size: 11px; padding: 4px 10px;
  background: rgba(1, 169, 130, 0.1); color: var(--green);
  border: 1px solid rgba(1, 169, 130, 0.3); border-radius: 6px;
  cursor: pointer; font-family: inherit; letter-spacing: 0;
}
.vh-type-new:hover { background: rgba(1, 169, 130, 0.18); }

.vh-type-list { display: flex; flex-direction: column; gap: 8px; }
.vh-type-row {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  cursor: pointer; transition: 0.2s;
}
.vh-type-row.active {
  background: rgba(1, 169, 130, 0.1);
  border-color: var(--green);
  box-shadow: 0 0 0 3px var(--green-glow);
}
.vh-type-row-icon {
  width: 36px; height: 36px; flex-shrink: 0;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px;
}
.vh-type-row-info { flex: 1; min-width: 0; }
.vh-type-row-name { font-family: "Space Grotesk"; font-weight: 700; font-size: 14px; }
.vh-type-row-speed { color: var(--dim); font-weight: 500; margin-left: 4px; }
.vh-type-row-desc {
  color: var(--muted); font-size: 12px; line-height: 1.4;
  margin-top: 3px;
}
.vh-type-row-caps { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.vh-cap-chip {
  font-size: 10px; padding: 2px 7px;
  background: rgba(1, 169, 130, 0.08);
  border: 1px solid rgba(1, 169, 130, 0.2);
  border-radius: 10px;
  color: #a7f3d0;
}
.vh-type-row-actions { display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }
.vh-type-act {
  width: 26px; height: 26px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px; color: var(--muted);
  cursor: pointer; font-size: 11px; padding: 0;
  display: flex; align-items: center; justify-content: center;
}
.vh-type-act:hover { color: var(--green); border-color: var(--green); }
.vh-type-act.danger:hover { color: var(--red); border-color: var(--red); }
.vh-type-row-badge {
  font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--dim); align-self: center; padding: 2px 6px;
  border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 4px;
}

/* Modal editor */
.vh-modal-bg {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
}
.vh-modal {
  width: 100%; max-width: 460px;
  background: var(--navy);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 14px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
  overflow: hidden;
}
.vh-modal-head {
  padding: 14px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  display: flex; justify-content: space-between; align-items: center;
}
.vh-modal-title { font-family: "Space Grotesk"; font-weight: 700; font-size: 16px; }
.vh-modal-close {
  width: 28px; height: 28px; border: none; border-radius: 50%;
  background: rgba(255, 255, 255, 0.05); color: var(--muted);
  cursor: pointer; font-size: 13px;
}
.vh-modal-close:hover { background: rgba(255, 255, 255, 0.12); color: #fff; }
.vh-modal-body {
  padding: 16px 18px; display: flex; flex-direction: column; gap: 12px;
}
.vh-modal-foot {
  padding: 14px 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  display: flex; gap: 8px; justify-content: flex-end;
}
.vh-field { display: flex; flex-direction: column; gap: 6px; }
.vh-field > span { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); font-weight: 600; }
.vh-field .req { color: var(--green); }
.vh-field-hint { text-transform: none; letter-spacing: 0; color: var(--dim); font-weight: 400; margin-left: 4px; }
.vh-field-row { display: grid; grid-template-columns: 2fr 1fr; gap: 10px; }
.vh-color-input {
  width: 100%; height: 40px; padding: 3px; cursor: pointer;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
}
.vh-btn-ghost {
  padding: 10px 16px; background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px;
  color: var(--muted); font-family: inherit; font-size: 14px; cursor: pointer;
}
.vh-btn-ghost:hover { color: #fff; border-color: rgba(255, 255, 255, 0.2); }
.vh-modal-foot .vh-btn-primary { width: auto; padding: 10px 20px; font-size: 14px; }

.vh-type-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.vh-type-btn {
  padding: 14px 8px; border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.02);
  color: #fff; font-family: inherit;
  cursor: pointer; text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  transition: 0.2s; position: relative;
}
.vh-type-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.vh-type-btn.active {
  background: rgba(1, 169, 130, 0.1);
  border-color: var(--green);
  box-shadow: 0 0 0 3px var(--green-glow);
}
.vh-type-icon { font-size: 30px; line-height: 1; }
.vh-type-name { font-size: 12px; font-weight: 600; }
.vh-type-soon { font-size: 9px; text-transform: uppercase; color: var(--dim); margin-top: 2px; }

.vh-input {
  width: 100%; padding: 11px 12px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  color: #fff; font-family: inherit; font-size: 14px;
  outline: none;
}
.vh-input:focus { border-color: var(--green); box-shadow: 0 0 0 3px var(--green-glow); }
.vh-toggle { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
.vh-toggle-btn {
  padding: 12px 8px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  color: var(--muted); font-family: inherit; font-size: 13px; cursor: pointer;
  transition: 0.2s;
}
.vh-toggle-btn.active {
  background: rgba(1, 169, 130, 0.12);
  border-color: var(--green);
  color: var(--green);
}
.vh-latlon { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.vh-hint { font-size: 12px; color: var(--dim); }

.vh-btn-primary {
  width: 100%; padding: 14px;
  border: none; border-radius: 12px;
  background: linear-gradient(135deg, var(--green), #00c9a1);
  color: #fff; font-family: inherit;
  font-weight: 600; font-size: 15px;
  cursor: pointer;
  box-shadow: 0 4px 20px var(--green-glow);
  transition: 0.2s;
}
.vh-btn-primary.big { padding: 18px; font-size: 16px; margin-top: 10px; }
.vh-btn-primary.nav-start { margin-top: 12px; }
.vh-btn-primary:active { transform: scale(0.98); }
.vh-btn-secondary-outline {
  padding: 14px; margin-top: 4px;
  border-radius: 12px;
  border: 1px dashed rgba(255, 255, 255, 0.2);
  background: transparent;
  color: var(--muted);
  font-family: inherit; font-weight: 500; font-size: 14px;
  cursor: pointer; transition: 0.2s;
}
.vh-btn-secondary-outline:hover {
  border-color: var(--green);
  color: var(--green);
  background: rgba(1, 169, 130, 0.04);
}

/* Picker de unidades guardadas */
.vh-saved-list { display: flex; flex-direction: column; gap: 8px; }
.vh-saved-item {
  display: flex; align-items: stretch;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  overflow: hidden;
  transition: 0.2s;
}
.vh-saved-item:hover {
  border-color: var(--green);
  background: rgba(1, 169, 130, 0.06);
  box-shadow: 0 0 0 3px var(--green-glow);
}
.vh-saved-main {
  flex: 1;
  display: flex; align-items: center; gap: 14px;
  padding: 14px 16px;
  cursor: pointer;
}
.vh-saved-icon {
  width: 44px; height: 44px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
}
.vh-saved-info { flex: 1; min-width: 0; }
.vh-saved-label { font-family: "Space Grotesk"; font-size: 15px; font-weight: 700; color: #fff; }
.vh-saved-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.vh-saved-arrow { font-size: 22px; color: var(--dim); flex-shrink: 0; }
.vh-saved-item:hover .vh-saved-arrow { color: var(--green); }
.vh-saved-del {
  background: transparent;
  border: none; border-left: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.3);
  padding: 0 14px;
  font-size: 14px; cursor: pointer; transition: 0.2s;
}
.vh-saved-del:hover { background: rgba(229, 62, 62, 0.15); color: var(--red); }

.vh-center {
  flex: 1;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 18px; padding: 40px 24px;
}
.vh-spinner {
  width: 70px; height: 70px;
  border: 6px solid rgba(255, 255, 255, 0.1);
  border-top-color: var(--green);
  border-radius: 50%;
  animation: vhspin 0.8s linear infinite;
}
@keyframes vhspin { to { transform: rotate(360deg); } }
.vh-cross {
  width: 100px; height: 100px; border-radius: 50%;
  background: rgba(229, 62, 62, 0.15); color: var(--red);
  border: 3px solid var(--red);
  display: flex; align-items: center; justify-content: center;
  font-size: 56px; font-weight: 700;
}
.vh-err { color: var(--muted); font-size: 13px; text-align: center; max-width: 380px; }

.vh-duty {
  flex: 1; position: relative;
  display: flex; flex-direction: column; min-height: 0;
}
.vh-map { flex: 1; min-height: 0; width: 100%; }

.vh-overlay {
  position: absolute; left: 12px; right: 12px; bottom: 84px;
  z-index: 900; pointer-events: none;
}
.vh-status-card {
  background: rgba(15, 27, 45, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(12px);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
  pointer-events: auto;
}
.vh-status-card.assigned { border-color: var(--red); box-shadow: 0 10px 30px rgba(229, 62, 62, 0.25); }
.vh-status-row {
  display: flex; align-items: center; gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.vh-status-badge {
  padding: 5px 10px; border-radius: 6px;
  font-family: "Space Grotesk"; font-weight: 700; font-size: 12px;
  color: #fff; letter-spacing: 0.5px;
}
.vh-status-phase { color: var(--muted); font-size: 13px; flex: 1; text-align: right; }
.vh-emergency-info { padding-top: 10px; }
.vh-emergency-title {
  font-family: "Space Grotesk"; font-weight: 600; font-size: 14px;
  display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
}
.vh-emergency-dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--red); box-shadow: 0 0 10px var(--red);
  animation: vhblink 1s ease-in-out infinite;
}
@keyframes vhblink { 50% { opacity: 0.4; } }
.vh-emergency-desc { font-size: 13px; color: var(--muted); line-height: 1.5; margin-bottom: 4px; }
.vh-emergency-meta { font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; }
.vh-idle { color: var(--muted); font-size: 13px; padding-top: 10px; }

.vh-btn-float {
  position: absolute; left: 12px; right: 12px; bottom: 16px;
  z-index: 900;
  padding: 14px; border-radius: 12px;
  background: rgba(229, 62, 62, 0.15);
  border: 1px solid var(--red);
  color: #fca5a5;
  font-family: inherit; font-weight: 600; font-size: 14px;
  cursor: pointer; backdrop-filter: blur(12px);
}

/* ── MODO NAVEGACIÓN (estilo Google Maps) ─────────────────────────────── */
.vh-nav-top {
  position: absolute; left: 0; right: 0; top: 0;
  z-index: 1100;
  padding: env(safe-area-inset-top, 0) 12px 12px;
  background: linear-gradient(to bottom, rgba(30, 80, 70, 0.98) 60%, rgba(30, 80, 70, 0));
  pointer-events: none;
}
.vh-nav-maneuver {
  display: flex; align-items: center; gap: 14px;
  background: linear-gradient(135deg, #038a6b, #01a982);
  border-radius: 14px;
  padding: 14px 14px;
  box-shadow: 0 8px 30px rgba(1, 169, 130, 0.4);
  pointer-events: auto;
  margin-top: 10px;
}
.vh-nav-icon {
  width: 60px; height: 60px;
  background: rgba(255, 255, 255, 0.14);
  border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  font-size: 38px; font-weight: 700; flex-shrink: 0;
}
.vh-nav-text { flex: 1; min-width: 0; }
.vh-nav-distance {
  font-family: "Space Grotesk";
  font-size: 24px; font-weight: 700; letter-spacing: -0.5px;
}
.vh-nav-instr {
  font-size: 14px; color: rgba(255, 255, 255, 0.85);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  margin-top: 2px;
}
.vh-nav-close {
  width: 34px; height: 34px;
  background: rgba(255, 255, 255, 0.15);
  border: none;
  border-radius: 50%;
  color: #fff; font-size: 14px; cursor: pointer;
  flex-shrink: 0;
}
.vh-nav-close:hover { background: rgba(255, 255, 255, 0.25); }

.vh-nav-bottom {
  position: absolute; left: 12px; right: 12px; bottom: 16px;
  z-index: 1100;
  background: rgba(15, 27, 45, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(12px);
  border-radius: 14px;
  padding: 14px 18px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;
  pointer-events: auto;
}
.vh-nav-metric { text-align: center; }
.vh-nav-metric-value {
  font-family: "Space Grotesk";
  font-size: 20px; font-weight: 700;
  color: #fff;
}
.vh-nav-metric-label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--muted); margin-top: 2px;
}

.vh-shell.nav .vh-map { filter: saturate(1.1); }
</style>

<style>
/* Markers globales */
.vh-marker { position: relative; }
.vh-marker-pin {
  width: 100%; height: 100%;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 18px; font-weight: 700;
  border: 3px solid rgba(255, 255, 255, 0.95);
  position: relative; z-index: 2;
}
.vh-marker-arrow {
  position: absolute;
  top: -6px; left: 50%;
  width: 0; height: 0;
  border-left: 7px solid transparent;
  border-right: 7px solid transparent;
  border-bottom: 12px solid #01a982;
  transform-origin: 50% calc(100% + 20px);
  transition: transform 0.4s ease;
  z-index: 1;
}
.vh-marker-emergency .vh-marker-pin {
  animation: vh-pulse 1s ease-in-out infinite;
}
@keyframes vh-pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.15); }
}
</style>
