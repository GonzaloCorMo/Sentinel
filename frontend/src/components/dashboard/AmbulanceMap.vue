<script setup lang="ts">
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { storeToRefs } from "pinia";
import { nextTick, onMounted, onUnmounted, toRef, watch } from "vue";
import { useI18n } from "vue-i18n";
import { MAP_DEFAULT_CENTER, MAP_DEFAULT_ZOOM, DEFAULT_SPAWN_LAT, DEFAULT_SPAWN_LON } from "@/lib/mapDefaults";
import { energyOf, operatingCostOf } from "@/lib/energyDisplay";
import { remainingRouteCoords } from "@/lib/routePolyline";
import { cssVar, useTheme } from "@/composables/useTheme";
import { sanitizeSvg } from "@/lib/sanitize";
import { addBasemap, type Basemap } from "@/lib/basemap";
import { useRegionStore } from "@/stores/region";
import { useSimulationStore } from "@/stores/simulation";

const { t: i18nT } = useI18n();
import type { Ambulance, ExternalEvent, WeatherReading, Emergency, EntityType, Jam, MapTool, Poi, Companion } from "@/types/simulation";

type ExternalEventMarker = ExternalEvent;

/** Escapa texto de origen externo (PWA ciudadana, ingesta REST, nombres de usuario)
 *  antes de interpolarlo en HTML de Leaflet (tooltips/popups usan innerHTML). */
function esc(v: unknown): string {
  return String(v ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Leaflet escribe los colores como atributos SVG, donde var() no resuelve:
 *  se leen los tokens del tema activo y se refrescan al cambiar de tema. */
function readPalette() {
  return {
    strong: cssVar("--n-100"),
    muted: cssVar("--n-400"),
    dim: cssVar("--n-600"),
    warn: cssVar("--warn-400"),
    crit: cssVar("--crit-400"),
  };
}
let palette = readPalette();

const { theme } = useTheme();
let basemap: Basemap | null = null;

const props = withDefaults(
  defineProps<{ mapTool?: MapTool; fullscreen?: boolean }>(),
  { mapTool: "none", fullscreen: false },
);
const emit = defineEmits<{
  mapClick: [lat: number, lng: number];
  deleteObject: [kind: "ambulance" | "companion" | "emergency" | "poi" | "jam", id: string];
}>();

function isDeleteMode() {
  return mapToolRef.value === "delete";
}
const mapToolRef = toRef(props, "mapTool");

const store = useSimulationStore();
const regionStore = useRegionStore();
const { state, selectedAmbulanceId } = storeToRefs(store);
const { active: activeRegion } = storeToRefs(regionStore);

let map: L.Map | null = null;
const markers = new Map<string, L.Marker>();
const routeLines = new Map<string, L.Polyline>();
const routeFingerprints = new Map<string, string>();

function routeFingerprint(coords: [number, number][]) {
  if (!coords.length) return "";
  const a = coords[0];
  const b = coords[coords.length - 1];
  return `${coords.length}:${a[0]}:${a[1]}:${b[0]}:${b[1]}`;
}
let poiLayer: L.LayerGroup | null = null;
const poiMarkerCache = new Map<string, { marker: L.Marker; sig: string }>();
let jamLayer: L.LayerGroup | null = null;
let emergencyLayer: L.LayerGroup | null = null;
let weatherStationsLayer: L.LayerGroup | null = null;
let routeLayer: L.LayerGroup | null = null;
let companionLayer: L.LayerGroup | null = null;
let externalEventsLayer: L.LayerGroup | null = null;

let rafSync = 0;
const markerAnim = new Map<string, { from: L.LatLng; to: L.LatLng; t0: number }>();
let animRaf = 0;

// ── Short ID helper ─────────────────────────────────────────────────────
import { displayId } from "@/lib/vehicleId";

// ── SVG Icon factory ────────────────────────────────────────────────────
const SVG_AMBULANCE = `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M3 13V7a2 2 0 012-2h8l4 4v4h1a2 2 0 012 2v1h-2a3 3 0 01-6 0H9a3 3 0 01-6 0H1v-1a2 2 0 012-2zm3 3.5A1.5 1.5 0 107.5 15 1.5 1.5 0 006 16.5zm9 0a1.5 1.5 0 101.5-1.5 1.5 1.5 0 00-1.5 1.5zM9 7v4h4V7H9zm-1 1H7v2h1V8zm5-1v2h1.59L13 7.41V7z"/></svg>`;
const SVG_HOSPITAL = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M18 3H6a2 2 0 00-2 2v16h16V5a2 2 0 00-2-2zm-5 12h-2v-2H9v-2h2V9h2v2h2v2h-2v2z"/></svg>`;
const SVG_FUEL = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="white"><path d="M19.77 7.23l.01-.01-3.72-3.72L15 4.56l2.11 2.11c-.94.36-1.61 1.26-1.61 2.33a2.5 2.5 0 002.5 2.5c.36 0 .69-.08 1-.21v7.21a1 1 0 01-2 0V14a2 2 0 00-2-2h-1V5a2 2 0 00-2-2H6a2 2 0 00-2 2v16h10v-7.5h1.5v5a2.5 2.5 0 005 0V9c0-.69-.28-1.32-.73-1.77zM12 10H6V5h6v5z"/></svg>`;
const SVG_ALERT = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M12 2L1 21h22L12 2zm0 4l7.53 13H4.47L12 6zm-1 5v4h2v-4h-2zm0 6v2h2v-2h-2z"/></svg>`;
const SVG_HELICOPTER = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M3 4h18v2H3V4zm6 4h4v1h7v2h-7v1H9v-1H2v-2h7V8zm3 5a4 4 0 00-4 4h2a2 2 0 114 0h2a4 4 0 00-4-4zm0 2a2 2 0 00-2 2h4a2 2 0 00-2-2zm-5 4h10v2H7v-2z"/></svg>`;
const SVG_POLICE = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 3l6 2.67V11c0 4.24-2.94 8.23-6 9.5-3.06-1.27-6-5.26-6-9.5V6.67L12 4zm-1 5v2H9v2h2v2h2v-2h2v-2h-2V9h-2z"/></svg>`;
const SVG_PLACE_FALLBACK = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="white"><path d="M12 2a7 7 0 00-7 7c0 5.1 7 13 7 13s7-7.9 7-13a7 7 0 00-7-7zm0 9.5a2.5 2.5 0 110-5 2.5 2.5 0 010 5z"/></svg>`;

function createEntityIcon(
  type: "ambulance" | "hospital" | "gas_station" | "emergency" | "helicopter" | "police_patrol" | string,
  opts: {
    selected?: boolean;
    hasPatient?: boolean;
    emergencyStatus?: string;
    label?: string;
    customSvg?: string | null;
    customColor?: string | null;
    isPulseSource?: boolean;
  } = {},
): L.DivIcon {
  let cls = "map-marker-icon";
  let svg = "";
  let size: [number, number] = [32, 32];

  switch (type) {
    case "ambulance":
      cls += " map-marker-ambulance";
      if (opts.hasPatient) cls += " has-patient";
      if (opts.selected) cls += " is-selected";
      svg = SVG_AMBULANCE;
      size = opts.selected ? [40, 40] : [32, 32];
      break;
    case "hospital":
      cls += " map-marker-hospital";
      svg = SVG_HOSPITAL;
      size = [30, 30];
      break;
    case "gas_station":
      cls += " map-marker-gas";
      svg = SVG_FUEL;
      size = [26, 26];
      break;
    case "emergency":
      cls += " map-marker-emergency";
      if (opts.emergencyStatus === "assigned") cls += " assigned";
      if (opts.isPulseSource) cls += " is-pulse-source";
      svg = SVG_ALERT;
      size = [34, 34];
      break;
    case "helicopter":
      cls += " map-marker-helicopter";
      svg = SVG_HELICOPTER;
      size = [32, 32];
      break;
    case "police_patrol":
      cls += " map-marker-police";
      svg = SVG_POLICE;
      size = [30, 30];
      break;
    default:
      if (opts.customColor) {
        cls += " map-marker-custom";
      }
      // POIs externos/custom pueden no traer iconSvg: evita círculo vacío.
      svg = SVG_PLACE_FALLBACK;
      break;
  }

  if (opts.customSvg) {
    svg = sanitizeSvg(opts.customSvg);
  }

  const labelHtml = opts.label
    ? `<span class="map-marker-label">${opts.label}</span>`
    : "";

  const style = opts.customColor && type !== "ambulance" && type !== "hospital" && type !== "gas_station" && type !== "emergency" && type !== "helicopter" && type !== "police_patrol"
    ? ` style="background:${opts.customColor}"`
    : "";

  return L.divIcon({
    className: "",
    html: `<div class="${cls}"${style}>${svg}${labelHtml}</div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2],
    popupAnchor: [0, -(size[1] / 2 + 4)],
  });
}

function stepMarkerAnim() {
  const now = performance.now();
  const dur = 280;
  let active = false;
  for (const [id, m] of markers) {
    const anim = markerAnim.get(id);
    if (!anim) continue;
    const u = Math.min(1, (now - anim.t0) / dur);
    const lat = anim.from.lat + (anim.to.lat - anim.from.lat) * u;
    const lng = anim.from.lng + (anim.to.lng - anim.from.lng) * u;
    m.setLatLng([lat, lng]);
    if (u >= 1) markerAnim.delete(id);
    else active = true;
  }
  animRaf = active ? requestAnimationFrame(stepMarkerAnim) : 0;
}

function moveMarkerSmooth(id: string, m: L.Marker, lat: number, lon: number) {
  const to = L.latLng(lat, lon);
  const from = m.getLatLng();
  if (from.distanceTo(to) < 0.25) {
    m.setLatLng(to);
    markerAnim.delete(id);
    return;
  }
  markerAnim.set(id, { from, to, t0: performance.now() });
  if (!animRaf) animRaf = requestAnimationFrame(stepMarkerAnim);
}

function companionLabel(c: Companion): string {
  if (c.displayLabel) return c.displayLabel;
  if (c.kind === "helicopter") return "HELI";
  if (c.kind === "police_patrol") return "POL";
  return c.kind.length <= 6 ? c.kind.toUpperCase() : `${c.kind.slice(0, 4).toUpperCase()}…`;
}

function companionPopupHtml(c: Companion, emergencyTitle: string | null) {
  const title = c.displayLabel ?? companionLabel(c);
  const typeLine = c.typeName ?? c.kind;
  const em = emergencyTitle ? `<p style="color:var(--text-3);margin:0">Emergencia: <b>${esc(emergencyTitle)}</b></p>` : "";
  return `<div style="font-size:11px;min-width:180px;line-height:1.5;font-variant-numeric:tabular-nums">
    <p style="font-weight:600;margin:0 0 2px">${esc(title)}</p>
    <p style="color:var(--text-3);margin:0">${esc(typeLine)} · ${esc(c.status)}</p>
    ${em}
    <p style="color:var(--text-3);margin:0">Vel: ${c.speedKmh} km/h</p>
    <p style="color:var(--text-4);margin:4px 0 0;font-size:10px">GPS: ${c.latitude.toFixed(5)}, ${c.longitude.toFixed(5)}</p>
  </div>`;
}

function popupHtml(label: string, amb: Ambulance) {
  const tel = amb.telemetry;
  const lat = amb.latitude ?? 0;
  const lon = amb.longitude ?? 0;
  const fsm = amb.fsmState ?? "—";
  const severity = amb.patientSeverity ?? "";
  const sevBadge = severity
    ? `<span style="display:inline-block;padding:0 5px;border-radius:2px;font-size:10px;font-weight:500;font-family:var(--font-mono);text-transform:uppercase;${severity === "critical" ? "border:1px solid var(--crit-600);color:var(--crit-300)" : severity === "moderate" ? "border:1px solid var(--warn-600);color:var(--warn-300)" : "border:1px solid var(--ok-600);color:var(--ok-300)"}">${severity}</span>`
    : "";
  // Energía dinámica según powertrain del entityType
  const types = state.value?.entityTypes;
  const info = energyOf(amb, types);
  const v = info.value ?? 0;
  const energyLabel = i18nT(info.labelKey);
  const energyBar = `<div style="width:100%;height:3px;background:var(--n-700);overflow:hidden"><div style="width:${v}%;height:100%;background:${v < 20 ? "var(--crit)" : "var(--n-300)"}"></div></div>`;
  const noPatient = i18nT("operations.no_amb_focus");
  const cost = operatingCostOf(amb, types);
  const costRow = cost.available
    ? `<p style="color:var(--text);margin:2px 0 0;font-family:var(--font-mono)">${i18nT("operations.operating_cost")}: ${cost.total.toFixed(2)} €
        <span style="color:var(--text-4);font-weight:400">(${cost.activeMinutes.toFixed(1)} min · ${cost.ratePerMin.toFixed(2)}€/min)</span>
       </p>`
    : "";

  return `<div style="font-size:11px;min-width:180px;line-height:1.5;font-variant-numeric:tabular-nums">
    <p style="font-weight:600;margin:0 0 2px">${esc(label)} ${sevBadge}</p>
    <p style="color:var(--text-3);margin:0">Estado: <b>${esc(fsm)}</b></p>
    <p style="color:var(--text-3);margin:0">GPS: ${lat.toFixed(5)}, ${lon.toFixed(5)}</p>
    <p style="color:var(--text-3);margin:0">${i18nT("operations.speed")}: ${tel?.positioning?.speedKmh ?? "—"} km/h</p>
    <p style="color:var(--text-3);margin:2px 0 0">${info.icon} ${energyLabel}: ${v.toFixed(0)}%</p>
    ${energyBar}
    ${costRow}
    <p style="color:var(--text-3);margin:4px 0 0">${amb.hasPatient && tel?.medical ? `BPM ${tel.medical.heartRateBpm} · SpO₂ ${tel.medical.spo2Pct}%` : noPatient}</p>
  </div>`;
}

// Clustering: agrupa unidades del MISMO tipo en celdas de ~140m. Si hay
// CLUSTER_THRESHOLD+ en la misma celda, se renderizan como un solo marker
// "base" con contador para evitar saturar el mapa (bases de 20 drones, etc.).
const CLUSTER_THRESHOLD = 3;
const CLUSTER_GRID = 800; // 1/0.00125º ≈ cada ~140m

const clusterMarkers = new Map<string, L.Marker>();

function iconEmojiForType(typeId: string, typeName?: string): string {
  const s = `${typeId} ${typeName || ""}`.toLowerCase();
  if (s.includes("heli")) return "🚁";
  if (s.includes("polic") || s.includes("patrol")) return "🚓";
  if (s.includes("fire") || s.includes("bomb")) return "🚒";
  if (s.includes("moto")) return "🏍️";
  if (s.includes("drone") || s.includes("dron")) return "🛸";
  if (s.includes("boat") || s.includes("barco")) return "🚤";
  return "🚑";
}

function createClusterIcon(emoji: string, count: number, color: string): L.DivIcon {
  const size: [number, number] = [44, 44];
  return L.divIcon({
    className: "",
    html: `<div class="map-marker-cluster" style="background:${color}">
      <span class="map-marker-cluster-icon">${emoji}</span>
      <span class="map-marker-cluster-count">${count}</span>
    </div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2],
    popupAnchor: [0, -(size[1] / 2 + 4)],
  });
}

function createCustomVehicleIcon(
  typeId: string,
  typeName: string | undefined,
  color: string,
  opts: { selected?: boolean; label?: string; hasPatient?: boolean; customSvg?: string | null } = {},
): L.DivIcon {
  const emoji = iconEmojiForType(typeId, typeName);
  const size: [number, number] = opts.selected ? [36, 36] : [28, 28];
  const cls = [
    "map-marker-icon map-marker-generic-vehicle",
    opts.selected ? "is-selected" : "",
    opts.hasPatient ? "has-patient" : "",
  ].filter(Boolean).join(" ");
  const svg = opts.customSvg ? sanitizeSvg(opts.customSvg) : `<span class="map-marker-emoji">${emoji}</span>`;
  const labelHtml = opts.label ? `<span class="map-marker-label">${esc(opts.label)}</span>` : "";
  return L.divIcon({
    className: "",
    html: `<div class="${cls}" style="background:${color}">${svg}${labelHtml}</div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2],
    popupAnchor: [0, -(size[1] / 2 + 4)],
  });
}

// Aplica uiFilters del store: devuelve true si la unidad debe mostrarse normal.
function passesUiFilters(amb: Ambulance): boolean {
  const ui = store.uiFilters || {};
  const type = amb.entityTypeId || "ambulance";
  if (ui.entityTypeId && type !== ui.entityTypeId) return false;
  if (ui.fuelBelow != null && (amb.fuelLevel ?? 100) >= ui.fuelBelow) return false;
  if (ui.fuelAbove != null && (amb.fuelLevel ?? 0) <= ui.fuelAbove) return false;
  if (ui.batteryBelow != null && (amb.telemetry?.mechanical?.batteryPct ?? 100) >= ui.batteryBelow) return false;
  if (ui.hasPatient != null && !!amb.hasPatient !== ui.hasPatient) return false;
  if (ui.severity && amb.patientSeverity !== ui.severity) return false;
  if (ui.missionPhase && (amb.missionPhase || "idle") !== ui.missionPhase) return false;
  if (ui.poweredOff != null && !!amb.poweredOff !== ui.poweredOff) return false;
  return true;
}
function hasActiveUiFilters(): boolean {
  const ui = store.uiFilters || {};
  return Object.keys(ui).some((k) => (ui as Record<string, unknown>)[k] !== undefined);
}

function syncLayers() {
  const s = state.value;
  if (!map || !s) return;

  const entityTypeById = new Map<string, EntityType>();
  for (const t of s.entityTypes || []) entityTypeById.set(t.id, t);

  const ambs = s.ambulances;

  // ── Agrupa por celda (lat,lon) × tipo
  type Cell = { typeId: string; items: Ambulance[]; latSum: number; lonSum: number };
  const cells = new Map<string, Cell>();
  for (const amb of ambs) {
    const typeId = amb.entityTypeId || "ambulance";
    const lat = amb.latitude ?? 0;
    const lon = amb.longitude ?? 0;
    const key = `${typeId}|${Math.round(lat * CLUSTER_GRID)}|${Math.round(lon * CLUSTER_GRID)}`;
    let c = cells.get(key);
    if (!c) { c = { typeId, items: [], latSum: 0, lonSum: 0 }; cells.set(key, c); }
    c.items.push(amb);
    c.latSum += lat;
    c.lonSum += lon;
  }

  const renderedSinglesIds = new Set<string>();
  const liveClusterKeys = new Set<string>();

  // Si la unidad está seleccionada, NO se colapsa aunque forme parte del cluster.
  for (const [key, c] of cells) {
    const selectedInCell = c.items.find((a) => selectedAmbulanceId.value === a.id);
    if (c.items.length >= CLUSTER_THRESHOLD && !selectedInCell) {
      liveClusterKeys.add(key);
      const et = entityTypeById.get(c.typeId);
      const color = (et?.color as string) || palette.muted;
      const emoji = iconEmojiForType(c.typeId, et?.name);
      const lat = c.latSum / c.items.length;
      const lon = c.lonSum / c.items.length;
      const icon = createClusterIcon(emoji, c.items.length, color);
      const typeName = et?.name || c.typeId;
      const popup = `<div style="font-size:11px;line-height:1.5;min-width:180px">
        <p style="font-weight:600;margin:0 0 2px">${esc(typeName)} — base</p>
        <p style="color:var(--text-3);margin:0">${c.items.length} unidades agrupadas</p>
        <p style="color:var(--text-4);margin:4px 0 0;font-size:10px">Acércate para verlas individualmente</p>
      </div>`;
      const existing = clusterMarkers.get(key);
      if (existing) {
        existing.setLatLng([lat, lon]);
        existing.setIcon(icon);
        existing.bindPopup(popup);
      } else {
        const m = L.marker([lat, lon], { icon, zIndexOffset: 400 }).addTo(map).bindPopup(popup);
        m.on("click", () => {
          if (!map) return;
          map.setView([lat, lon], Math.max(map.getZoom() + 2, 16), { animate: true });
        });
        clusterMarkers.set(key, m);
      }
    } else {
      // Renderiza todos individuales
      for (const amb of c.items) renderedSinglesIds.add(amb.id);
    }
  }
  // Limpia clusters obsoletos
  for (const [k, m] of clusterMarkers) {
    if (!liveClusterKeys.has(k)) {
      map.removeLayer(m);
      clusterMarkers.delete(k);
    }
  }

  // Limpia markers individuales obsoletos
  for (const [id, m] of markers) {
    if (!renderedSinglesIds.has(id)) {
      map.removeLayer(m);
      markers.delete(id);
      markerAnim.delete(id);
    }
  }
  for (let idx = 0; idx < ambs.length; idx++) {
    const amb = ambs[idx];
    if (!renderedSinglesIds.has(amb.id)) continue;
    const lat = amb.latitude ?? activeRegion.value?.spawn[0] ?? DEFAULT_SPAWN_LAT;
    const lon = amb.longitude ?? activeRegion.value?.spawn[1] ?? DEFAULT_SPAWN_LON;
    const id = amb.id;
    const sel = selectedAmbulanceId.value === id;
    const label = displayId(amb, idx, s.entityTypes);
    const typeId = amb.entityTypeId || "ambulance";
    const et = entityTypeById.get(typeId);
    const builtInIcon = typeId === "ambulance" || typeId === "helicopter" || typeId === "police_patrol";
    const icon = builtInIcon
      ? createEntityIcon(typeId, { selected: sel, hasPatient: !!amb.hasPatient, label })
      : createCustomVehicleIcon(typeId, et?.name, (et?.color as string) || palette.muted, {
          selected: sel,
          hasPatient: !!amb.hasPatient,
          label,
          customSvg: et?.iconSvg ?? null,
        });
    const popup = popupHtml(label, amb);
    // Filtros UI: ocultar no-matching + halo sobre matching
    const filtersActive = hasActiveUiFilters();
    const matches = !filtersActive || passesUiFilters(amb);
    const existing = markers.get(id);
    if (existing) {
      if (!matches) {
        // Oculta marker quitándolo; entra de nuevo si cumple en futuro tick
        map.removeLayer(existing);
        markers.delete(id);
      } else {
        moveMarkerSmooth(id, existing, lat, lon);
        existing.setIcon(icon);
        (existing.getElement() as HTMLElement | null)?.classList.toggle("map-marker-matched", filtersActive);
        existing.bindPopup(popup);
      }
    } else if (matches) {
      const m = L.marker([lat, lon], { icon, zIndexOffset: sel ? 1000 : 500 })
        .addTo(map)
        .bindPopup(popup);
      m.on("click", () => {
        if (isDeleteMode()) {
          emit("deleteObject", "ambulance", id);
          return;
        }
        store.selectAmbulance(id);
      });
      (m.getElement() as HTMLElement | null)?.classList.toggle("map-marker-matched", filtersActive);
      markers.set(id, m);
    }
  }

  // Auto-fit a los matching cuando hay filtros activos y no se está siguiendo una unidad
  if (hasActiveUiFilters() && !selectedAmbulanceId.value) {
    const matching = ambs.filter(passesUiFilters);
    if (matching.length >= 1 && matching.length <= 30) {
      const coords = matching
        .filter((a) => a.latitude != null && a.longitude != null)
        .map((a) => [a.latitude as number, a.longitude as number] as [number, number]);
      if (coords.length) {
        const bounds = L.latLngBounds(coords);
        map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15, animate: true });
      }
    }
  }

  // Routes
  if (routeLayer) {
    for (const [id, line] of routeLines) {
      if (!ambs.find((a) => a.id === id && a.routeCoords?.length)) {
        routeLayer.removeLayer(line);
        routeLines.delete(id);
        routeFingerprints.delete(id);
      }
    }
    for (const amb of ambs) {
      const raw = amb.routeCoords;
      if (!raw || raw.length < 2) {
        const old = routeLines.get(amb.id);
        if (old && routeLayer) {
          routeLayer.removeLayer(old);
          routeLines.delete(amb.id);
        }
        routeFingerprints.delete(amb.id);
        continue;
      }
      const prog = Number(amb.routeProgressM ?? 0);
      const coords = remainingRouteCoords(raw as [number, number][], prog);
      if (coords.length < 2) {
        const last = coords[0] ?? raw[raw.length - 1];
        const fp = `done:${amb.id}`;
        const latlngs = [
          [last[0], last[1]],
          [last[0], last[1]],
        ] as L.LatLngExpression[];
        const existing = routeLines.get(amb.id);
        const sel = selectedAmbulanceId.value === amb.id;
        const wf = (amb as Ambulance & { weatherFactor?: number }).weatherFactor;
        const baseColor = sel
          ? palette.strong
          : wf != null && wf < 0.65
            ? palette.crit
            : wf != null && wf < 0.85
              ? palette.warn
              : palette.muted;
        if (existing) {
          existing.setLatLngs(latlngs);
          routeFingerprints.set(amb.id, fp);
          existing.setStyle({ color: baseColor, opacity: sel ? 0.95 : 0.65, weight: sel ? 5 : 3 });
        } else if (routeLayer) {
          const pl = L.polyline(latlngs, {
            color: baseColor,
            weight: sel ? 5 : 3,
            opacity: sel ? 0.95 : 0.65,
          }).addTo(routeLayer);
          routeLines.set(amb.id, pl);
          routeFingerprints.set(amb.id, fp);
        }
        continue;
      }
      const fp = `${Math.round(prog)}:${routeFingerprint(coords)}`;
      const latlngs = coords.map((c) => [c[0], c[1]] as L.LatLngExpression);
      const sel = selectedAmbulanceId.value === amb.id;
      const wf = (amb as Ambulance & { weatherFactor?: number }).weatherFactor;
      const baseColor = sel
        ? palette.strong
        : wf != null && wf < 0.65
          ? palette.crit
          : wf != null && wf < 0.85
            ? palette.warn
            : palette.muted;
      const existing = routeLines.get(amb.id);
      const prevFp = routeFingerprints.get(amb.id);
      if (existing) {
        if (fp !== prevFp) {
          existing.setLatLngs(latlngs);
          routeFingerprints.set(amb.id, fp);
        }
        existing.setStyle({ color: baseColor, opacity: sel ? 0.95 : 0.65, weight: sel ? 5 : 3 });
      } else {
        const pl = L.polyline(latlngs, {
          color: baseColor,
          weight: sel ? 5 : 3,
          opacity: sel ? 0.95 : 0.65,
        }).addTo(routeLayer);
        routeLines.set(amb.id, pl);
        routeFingerprints.set(amb.id, fp);
      }
    }
  }

  // POIs (tipos built-in y lugares del registro / IA) — cache por id para
  // que tooltips no parpadeen al re-renderizar el mapa cada tick.
  if (poiLayer) {
    const entityTypes = (s.entityTypes ?? []) as EntityType[];
    const seen = new Set<string>();
    for (const p of s.pois as Poi[]) {
      seen.add(p.id);
      const et = entityTypes.find((t) => t.id === p.kind);
      const builtIn =
        p.kind === "hospital" ? "hospital" : p.kind === "gas_station" ? "gas_station" : "custom";
      const shortName = p.name.length > 8 ? `${p.name.slice(0, 7)}…` : p.name;
      const emoji = p.kind === "gas_station" ? "⛽" : p.kind === "hospital" ? "🏥" : "📍";
      const sourceTag = p.source === "aruba_api" ? " · Aruba API" : "";
      const tipText = `${emoji} ${esc(p.name)} (${esc(et?.name ?? p.kind)})${sourceTag}`;
      const sig = `${p.latitude}|${p.longitude}|${p.kind}|${shortName}|${et?.iconSvg ?? ""}|${et?.color ?? ""}|${tipText}`;
      const cached = poiMarkerCache.get(p.id);
      if (cached && cached.sig === sig) continue;
      if (cached) {
        cached.marker.remove();
        poiMarkerCache.delete(p.id);
      }
      const icon = createEntityIcon(builtIn, {
        customSvg: et?.iconSvg ?? undefined,
        customColor: et?.color ?? undefined,
        label: shortName,
      });
      const pm = L.marker([p.latitude, p.longitude], { icon, zIndexOffset: 100 })
        .bindTooltip(tipText, { permanent: false, direction: "top" })
        .addTo(poiLayer);
      pm.on("click", () => {
        if (isDeleteMode()) emit("deleteObject", "poi", p.id);
      });
      poiMarkerCache.set(p.id, { marker: pm, sig });
    }
    for (const [id, entry] of poiMarkerCache) {
      if (!seen.has(id)) {
        entry.marker.remove();
        poiMarkerCache.delete(id);
      }
    }
  }

  // Weather stations
  if (weatherStationsLayer) {
    weatherStationsLayer.clearLayers();
    const readings = (s.weatherStations ?? {}) as Record<string, WeatherReading>;
    const stations = (s.pois as Poi[]).filter((p) => p.kind === "weather_station");
    for (const st of stations) {
      const r = readings[st.id];
      const precip = r?.precipitation_mm ?? 0;
      const wind = r?.wind_speed_kmh ?? 0;
      const vis = r?.visibility_km ?? 10;
      const isAlert = precip > 5 || wind > 25 || vis < 5;
      const color = isAlert ? palette.crit : palette.muted;
      const radius = isAlert ? 8 : 5;
      const tip = r
        ? `${isAlert ? "⚠️ " : "📡 "}${esc(st.name ?? "Weather")} — ${r.temperature_c}°C, precip ${precip}mm, wind ${wind}km/h, vis ${vis}km`
        : `📡 ${esc(st.name ?? "Weather")} (no reading)`;
      L.circleMarker([st.latitude, st.longitude], {
        radius,
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.4,
      }).bindTooltip(tip).addTo(weatherStationsLayer);
    }
  }

  // Emergencies
  if (emergencyLayer) {
    emergencyLayer.clearLayers();
    for (const e of s.emergencies as Emergency[]) {
      if (e.status === "resolved") continue;
      const isPulse = e.source === "external_feed";
      const icon = createEntityIcon("emergency", {
        emergencyStatus: e.status,
        isPulseSource: isPulse,
      });
      const typeLabel = (e as Emergency & { emergencyType?: string }).emergencyType;
      const typeTag = typeLabel && typeLabel !== "medical" ? ` [${typeLabel}]` : "";
      const sourceTag = isPulse ? " 📡" : "";
      const em = L.marker([e.latitude, e.longitude], { icon, zIndexOffset: 900 })
        .bindTooltip(`${sourceTag}🚨 ${esc(e.title)}${esc(typeTag)} (${esc(e.status)})`, { permanent: false })
        .addTo(emergencyLayer);
      em.on("click", () => {
        if (isDeleteMode()) emit("deleteObject", "emergency", e.id);
      });
    }
  }

  // Jams
  if (jamLayer) {
    jamLayer.clearLayers();
    for (const j of s.jams as Jam[]) {
      const latlngs = j.polygon.map((pt) => [pt[0], pt[1]] as L.LatLngExpression);
      const jp = L.polygon(latlngs, {
        color: palette.crit,
        weight: 1.5,
        fillColor: palette.crit,
        fillOpacity: 0.15,
      })
        .bindTooltip("Atasco / bloqueo", { permanent: false })
        .addTo(jamLayer);
      jp.on("click", () => {
        if (isDeleteMode() && j.source !== "aruba_api") emit("deleteObject", "jam", j.id);
      });
    }
  }

  // Companions (helicopter, police_patrol, custom / IA)
  if (companionLayer) {
    companionLayer.clearLayers();
    const companions = s.companions ?? [];
    const entityTypes = (s.entityTypes ?? []) as EntityType[];
    if (companions?.length) {
      for (const c of companions) {
        const builtIn = c.kind === "helicopter" || c.kind === "police_patrol";
        const kind = builtIn ? c.kind : c.kind;
        const et = entityTypes.find((t) => t.id === c.kind);
        const icon = createEntityIcon(builtIn ? kind : "custom", {
          label: companionLabel(c),
          customSvg: et?.iconSvg ?? undefined,
          customColor: et?.color ?? undefined,
        });
        const em = s.emergencies.find((e) => e.id === c.assignedEmergencyId);
        const popup = companionPopupHtml(c, em?.title ?? null);
        const m = L.marker([c.latitude, c.longitude], { icon, zIndexOffset: 800 })
          .bindPopup(popup)
          .bindTooltip(
            `${c.kind === "helicopter" ? "🚁" : c.kind === "police_patrol" ? "🛡️" : "🔷"} ${esc(companionLabel(c))} · ${esc(c.typeName ?? c.kind)} (${esc(c.status)})`,
            { permanent: false },
          );
        m.on("click", () => {
          if (isDeleteMode()) {
            emit("deleteObject", "companion", c.id);
            return;
          }
          store.selectCompanion(c.id);
        });
        m.addTo(companionLayer);

        if (c.routeCoords && c.routeCoords.length >= 2) {
          const latlngs = c.routeCoords.map((pt: [number, number]) => [pt[0], pt[1]] as L.LatLngExpression);
          L.polyline(latlngs, {
            color: et?.color ?? palette.muted,
            weight: 3,
            opacity: 0.7,
            dashArray: "8 6",
          }).addTo(companionLayer);
        }
      }
    }
  }

  // Eventos externos (mock local o ingesta REST) — incidentes fuera de banda.
  if (externalEventsLayer) {
    externalEventsLayer.clearLayers();
    const externalEvents = (s.externalEvents ?? []) as ExternalEventMarker[];
    for (const ev of externalEvents) {
      if (ev.resolved_at) continue;
      const lat = Number(ev.latitude);
      const lon = Number(ev.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const color = severityColor(ev.severity);
      const radius = Number.isFinite(Number(ev.radius_m)) && Number(ev.radius_m) > 0
        ? Math.min(2000, Math.max(40, Number(ev.radius_m)))
        : 80;
      const circle = L.circle([lat, lon], {
        radius,
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.18,
      }).addTo(externalEventsLayer);
      const tip = `${eventTypeIcon(ev.type)} ${esc(ev.title ?? ev.type)} · ${esc(ev.severity)}`;
      circle.bindTooltip(tip, { permanent: false });
      circle.bindPopup(externalEventPopup(ev));
    }
  }
}

function severityColor(severity: string | undefined): string {
  switch ((severity ?? "").toLowerCase()) {
    case "critical":
    case "high": return palette.crit;
    case "medium": return palette.warn;
    case "low":
    default: return palette.muted;
  }
}

function eventTypeIcon(type: string | undefined): string {
  switch ((type ?? "").toLowerCase()) {
    case "storm": return "⛈️";
    case "fire": return "🔥";
    case "flood": return "🌊";
    case "accident": return "💥";
    case "lane_closure": return "🚧";
    case "power_outage": return "⚡";
    case "medical_emergency": return "🚑";
    case "hazmat_spill": return "☣️";
    case "construction": return "🏗️";
    case "public_event": return "🎪";
    default: return "📍";
  }
}

function externalEventPopup(ev: ExternalEventMarker): string {
  return `
    <div style="min-width:220px;max-width:300px">
      <div style="font-weight:600">${eventTypeIcon(ev.type)} ${esc(ev.title)}</div>
      <div style="font-size:11px;color:var(--text-4);margin-bottom:4px">
        ${esc(ev.type)} · severidad ${esc(ev.severity)}
      </div>
      <div style="font-size:12px">${esc(ev.description)}</div>
      <div style="font-size:11px;color:var(--text-4);margin-top:6px">
        ${esc(ev.started_at)}${ev.radius_m ? ` · radio ${Number(ev.radius_m).toFixed(0)} m` : ""}
      </div>
    </div>
  `;
}

function scheduleSync() {
  if (rafSync) return;
  rafSync = requestAnimationFrame(() => {
    rafSync = 0;
    syncLayers();
  });
}

onMounted(() => {
  const el = document.getElementById("ambulance-map-canvas");
  if (!el) return;
  const initCenter = activeRegion.value?.center ?? MAP_DEFAULT_CENTER;
  const initZoom = activeRegion.value?.zoom ?? MAP_DEFAULT_ZOOM;
  map = L.map(el, { preferCanvas: true }).setView(initCenter, initZoom);
  basemap = addBasemap(map, theme.value);

  watch(theme, (next) => {
    basemap?.setTheme(next);
    palette = readPalette();
    // Fuerza a repintar rutas/polígonos con la nueva paleta.
    routeFingerprints.clear();
    scheduleSync();
  });

  watch(
    activeRegion,
    (r) => {
      if (!map || !r) return;
      // flyTo entre regiones lejanas (p. ej. Aruba → Santiago) deja la capa de
      // teselas con el origen de píxeles corrupto: saltos largos sin animación.
      const far = map.getCenter().distanceTo(r.center) > 50_000;
      if (far) map.setView(r.center, r.zoom, { animate: false });
      else map.flyTo(r.center, r.zoom, { duration: 0.8 });
    },
  );

  map.on("click", (e: L.LeafletMouseEvent) => {
    const t = mapToolRef.value;
    // Modo borrar: solo clicks sobre markers/polígonos cuentan (gestionados
    // por sus propios handlers). Click en vacío del mapa: no-op.
    if (t && t !== "none" && t !== "delete") {
      emit("mapClick", e.latlng.lat, e.latlng.lng);
    }
  });

  poiLayer = L.layerGroup().addTo(map);
  jamLayer = L.layerGroup().addTo(map);
  emergencyLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);
  companionLayer = L.layerGroup().addTo(map);
  externalEventsLayer = L.layerGroup().addTo(map);
  weatherStationsLayer = L.layerGroup().addTo(map);

  watch(
    () => state.value,
    () => {
      scheduleSync();
    },
    { flush: "post" },
  );

  watch(
    selectedAmbulanceId,
    () => {
      scheduleSync();
    },
    { flush: "post" },
  );

  // Re-sync cuando los filtros UI (chatbot comando) cambian → markers
  // cambian opacidad sin esperar al próximo tick de polling.
  watch(
    () => store.uiFilters,
    () => { scheduleSync(); },
    { deep: true, flush: "post" },
  );

  watch(
    () => props.fullscreen,
    async () => {
      await nextTick();
      requestAnimationFrame(() => {
        map?.invalidateSize();
        requestAnimationFrame(() => map?.invalidateSize());
      });
    },
  );

  scheduleSync();
});

onUnmounted(() => {
  if (animRaf) cancelAnimationFrame(animRaf);
  animRaf = 0;
  markerAnim.clear();
  map?.remove();
  map = null;
  markers.clear();
  routeLines.clear();
  routeFingerprints.clear();
  poiMarkerCache.clear();
  poiLayer = null;
  jamLayer = null;
  emergencyLayer = null;
  weatherStationsLayer = null;
  routeLayer = null;
  companionLayer = null;
});
</script>

<template>
  <div
    class="relative h-full w-full overflow-hidden rounded-xl border border-slate-700 bg-slate-900"
    :class="[
      { 'cursor-crosshair': mapTool !== 'none' && mapTool !== 'delete' },
      { 'map-cursor-delete': mapTool === 'delete' },
      fullscreen ? 'min-h-0 flex-1' : 'min-h-[320px]',
    ]"
  >
    <div id="ambulance-map-canvas" class="absolute inset-0 z-0 h-full w-full" />
    <p
      v-if="mapTool === 'delete'"
      class="pointer-events-none absolute right-3 top-3 z-[400] rounded border border-red-500/50 bg-slate-950 px-3 py-1.5 text-[11px] font-medium leading-snug text-red-300"
    >
      Modo borrar · clica un objeto para eliminarlo (ESC sale)
    </p>
  </div>
</template>

<style scoped>
/* Cursor con X roja en modo borrar (data URI SVG). */
.map-cursor-delete,
.map-cursor-delete :deep(.leaflet-container),
.map-cursor-delete :deep(.leaflet-interactive) {
  cursor: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'><circle cx='14' cy='14' r='12' fill='rgba(244,63,94,0.18)' stroke='%23f43f5e' stroke-width='2'/><path d='M9 9l10 10M19 9l-10 10' stroke='%23f43f5e' stroke-width='2.5' stroke-linecap='round'/></svg>") 14 14, crosshair;
}
</style>
