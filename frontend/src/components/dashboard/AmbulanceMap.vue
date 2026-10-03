<script setup lang="ts">
import { storeToRefs } from "pinia";
import { onMounted, onUnmounted, ref, toRef, watch } from "vue";
import { useI18n } from "vue-i18n";
import { MAP_DEFAULT_CENTER, MAP_DEFAULT_ZOOM, DEFAULT_SPAWN_LAT, DEFAULT_SPAWN_LON } from "@/lib/mapDefaults";
import { energyOf, operatingCostOf } from "@/lib/energyDisplay";
import { remainingRouteCoords } from "@/lib/routePolyline";
import { sanitizeSvg } from "@/lib/sanitize";
import { unitStatus } from "@/lib/unitStatus";
import { displayId } from "@/lib/vehicleId";
import {
  circleRing,
  createHoverTooltip,
  createMap,
  maplibregl,
  setGeoJson,
  toLngLat,
  type LatLon,
} from "@/lib/mapEngine";
import type * as GeoJSON from "geojson";
import { useRegionStore } from "@/stores/region";
import { useSimulationStore } from "@/stores/simulation";
import type { Ambulance, Companion, Emergency, EntityType, ExternalEvent, Jam, MapTool, Poi, WeatherReading } from "@/types/simulation";

const { t: i18nT, te: i18nTe } = useI18n();

const props = withDefaults(
  defineProps<{ mapTool?: MapTool; fullscreen?: boolean }>(),
  { mapTool: "none", fullscreen: false },
);
const emit = defineEmits<{
  mapClick: [lat: number, lng: number];
  deleteObject: [kind: "ambulance" | "companion" | "emergency" | "poi" | "jam", id: string];
}>();

const mapToolRef = toRef(props, "mapTool");
const isDeleteMode = () => mapToolRef.value === "delete";

const store = useSimulationStore();
const regionStore = useRegionStore();
const { state, selectedAmbulanceId } = storeToRefs(store);
const { active: activeRegion } = storeToRefs(regionStore);

const container = ref<HTMLDivElement | null>(null);
let map: maplibregl.Map | null = null;
let tooltip: ReturnType<typeof createHoverTooltip> | null = null;

/** El mapa base es claro en ambos temas: los trazos usan colores fijos legibles sobre él. */
const MAP_COLORS = {
  routeSelected: "#1a73e8",
  route: "#5f6368",
  warn: "#e37400",
  crit: "#d93025",
  muted: "#80868b",
  casing: "#ffffff",
};

/** Escapa texto de origen externo (PWA ciudadana, ingesta REST) antes de meterlo en HTML. */
function esc(v: unknown): string {
  return String(v ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// ── Iconos ───────────────────────────────────────────────────────────────
const SVG_AMBULANCE = `<svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24" fill="currentColor"><path d="M3 13V7a2 2 0 012-2h8l4 4v4h1a2 2 0 012 2v1h-2a3 3 0 01-6 0H9a3 3 0 01-6 0H1v-1a2 2 0 012-2zm3 3.5A1.5 1.5 0 107.5 15 1.5 1.5 0 006 16.5zm9 0a1.5 1.5 0 101.5-1.5 1.5 1.5 0 00-1.5 1.5zM9 7v4h4V7H9zm-1 1H7v2h1V8zm5-1v2h1.59L13 7.41V7z"/></svg>`;
const SVG_HOSPITAL = `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M18 3H6a2 2 0 00-2 2v16h16V5a2 2 0 00-2-2zm-5 12h-2v-2H9v-2h2V9h2v2h2v2h-2v2z"/></svg>`;
const SVG_FUEL = `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M19.77 7.23l.01-.01-3.72-3.72L15 4.56l2.11 2.11c-.94.36-1.61 1.26-1.61 2.33a2.5 2.5 0 002.5 2.5c.36 0 .69-.08 1-.21v7.21a1 1 0 01-2 0V14a2 2 0 00-2-2h-1V5a2 2 0 00-2-2H6a2 2 0 00-2 2v16h10v-7.5h1.5v5a2.5 2.5 0 005 0V9c0-.69-.28-1.32-.73-1.77zM12 10H6V5h6v5z"/></svg>`;
const SVG_ALERT = `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L1 21h22L12 2zm0 4l7.53 13H4.47L12 6zm-1 5v4h2v-4h-2zm0 6v2h2v-2h-2z"/></svg>`;
const SVG_HELICOPTER = `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M3 4h18v2H3V4zm6 4h4v1h7v2h-7v1H9v-1H2v-2h7V8zm3 5a4 4 0 00-4 4h2a2 2 0 114 0h2a4 4 0 00-4-4zm0 2a2 2 0 00-2 2h4a2 2 0 00-2-2zm-5 4h10v2H7v-2z"/></svg>`;
const SVG_POLICE = `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 3l6 2.67V11c0 4.24-2.94 8.23-6 9.5-3.06-1.27-6-5.26-6-9.5V6.67L12 4zm-1 5v2H9v2h2v2h2v-2h2v-2h-2V9h-2z"/></svg>`;
const SVG_PLACE = `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a7 7 0 00-7 7c0 5.1 7 13 7 13s7-7.9 7-13a7 7 0 00-7-7zm0 9.5a2.5 2.5 0 110-5 2.5 2.5 0 010 5z"/></svg>`;

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

function entityIconHtml(
  type: string,
  opts: { selected?: boolean; hasPatient?: boolean; emergencyStatus?: string; label?: string; customSvg?: string | null; customColor?: string | null; external?: boolean } = {},
): string {
  let cls = "map-marker-icon";
  let svg = SVG_PLACE;
  switch (type) {
    case "ambulance":
      cls += " map-marker-ambulance" + (opts.hasPatient ? " has-patient" : "") + (opts.selected ? " is-selected" : "");
      svg = SVG_AMBULANCE;
      break;
    case "hospital":
      cls += " map-marker-hospital";
      svg = SVG_HOSPITAL;
      break;
    case "gas_station":
      cls += " map-marker-gas";
      svg = SVG_FUEL;
      break;
    case "emergency":
      cls += " map-marker-emergency" + (opts.emergencyStatus === "assigned" ? " assigned" : "") + (opts.external ? " is-pulse-source" : "");
      svg = SVG_ALERT;
      break;
    case "helicopter":
      cls += " map-marker-helicopter";
      svg = SVG_HELICOPTER;
      break;
    case "police_patrol":
      cls += " map-marker-police";
      svg = SVG_POLICE;
      break;
    default:
      if (opts.customColor) cls += " map-marker-custom";
  }
  if (opts.customSvg) svg = sanitizeSvg(opts.customSvg);
  const builtIn = ["ambulance", "hospital", "gas_station", "emergency", "helicopter", "police_patrol"].includes(type);
  const style = opts.customColor && !builtIn ? ` style="background:${esc(opts.customColor)}"` : "";
  const label = opts.label ? `<span class="map-marker-label">${esc(opts.label)}</span>` : "";
  return `<div class="${cls}"${style}>${svg}${label}</div>`;
}

/** Icono SVG por familia de tipo (ambulancia, policía, bomberos…); null si no hay uno claro. */
function svgForType(typeId: string, typeName?: string): string | null {
  const s = `${typeId} ${typeName || ""}`.toLowerCase();
  if (s.includes("heli")) return SVG_HELICOPTER;
  if (s.includes("polic") || s.includes("patrol")) return SVG_POLICE;
  if (s.includes("ambul") || s.includes("bomb") || s.includes("fire") || s.includes("civil")) return SVG_AMBULANCE;
  return null;
}

function customVehicleHtml(typeId: string, typeName: string | undefined, color: string, opts: { selected?: boolean; label?: string; hasPatient?: boolean; customSvg?: string | null }): string {
  const cls = ["map-marker-icon map-marker-generic-vehicle", opts.selected ? "is-selected" : "", opts.hasPatient ? "has-patient" : ""].filter(Boolean).join(" ");
  const builtInSvg = svgForType(typeId, typeName);
  const inner = opts.customSvg
    ? sanitizeSvg(opts.customSvg)
    : builtInSvg ?? `<span class="map-marker-emoji">${iconEmojiForType(typeId, typeName)}</span>`;
  const label = opts.label ? `<span class="map-marker-label">${esc(opts.label)}</span>` : "";
  return `<div class="${cls}" style="background:${esc(color)}">${inner}${label}</div>`;
}

function clusterHtml(emoji: string, count: number, color: string): string {
  return `<div class="map-marker-cluster" style="background:${esc(color)}">
    <span class="map-marker-cluster-icon">${emoji}</span>
    <span class="map-marker-cluster-count">${count}</span>
  </div>`;
}

// ── Popups / tooltips ────────────────────────────────────────────────────
function companionLabel(c: Companion): string {
  if (c.displayLabel) return c.displayLabel;
  if (c.kind === "helicopter") return "HELI";
  if (c.kind === "police_patrol") return "POL";
  return c.kind.length <= 6 ? c.kind.toUpperCase() : `${c.kind.slice(0, 4).toUpperCase()}…`;
}

function companionPopupHtml(c: Companion, emergencyTitle: string | null): string {
  const em = emergencyTitle ? `<p class="mp-row">${esc(i18nT("operations.destination"))}: <b>${esc(emergencyTitle)}</b></p>` : "";
  return `<div class="mp">
    <p class="mp-title">${esc(c.displayLabel ?? companionLabel(c))}</p>
    <p class="mp-row">${esc(c.typeName ?? c.kind)}</p>
    ${em}
    <p class="mp-row">${esc(i18nT("operations.speed"))}: <span class="mp-num">${Math.round(c.speedKmh)} km/h</span></p>
  </div>`;
}

function ambulancePopupHtml(label: string, amb: Ambulance): string {
  const tel = amb.telemetry;
  const types = state.value?.entityTypes;
  const info = energyOf(amb, types);
  const v = info.value ?? 0;
  const cost = operatingCostOf(amb, types);
  const severity = amb.patientSeverity
    ? `<span class="mp-badge severity-${esc(amb.patientSeverity)}">${esc(i18nT(`operations.severity_${amb.patientSeverity}`))}</span>`
    : "";
  const patient = amb.hasPatient && tel?.medical
    ? `<p class="mp-row">${esc(i18nT("operations.pulse"))} <span class="mp-num">${tel.medical.heartRateBpm}</span> · SpO₂ <span class="mp-num">${tel.medical.spo2Pct} %</span></p>`
    : `<p class="mp-row mp-dim">${esc(i18nT("operations.no_patient"))}</p>`;
  const costRow = cost.available
    ? `<p class="mp-row">${esc(i18nT("operations.operating_cost"))}: <span class="mp-num">${cost.total.toFixed(2)} €</span></p>`
    : "";
  return `<div class="mp">
    <p class="mp-title">${esc(label)} ${severity}</p>
    <p class="mp-row"><b>${esc(i18nT(`status.${unitStatus(amb).key}`))}</b></p>
    <p class="mp-row">${esc(i18nT("operations.speed"))}: <span class="mp-num">${Math.round(tel?.positioning?.speedKmh ?? 0)} km/h</span></p>
    <p class="mp-row">${esc(i18nT(info.labelKey))}: <span class="mp-num">${v.toFixed(0)} %</span></p>
    <div class="mp-bar"><div style="width:${Math.max(0, Math.min(100, v))}%;background:${v < 20 ? "var(--crit)" : "var(--n-300)"}"></div></div>
    ${costRow}
    ${patient}
  </div>`;
}

function eventTypeLabel(type: string | undefined): string {
  const key = `events.type.${type ?? ""}`;
  return i18nTe(key) ? i18nT(key) : String(type ?? "");
}

function eventSeverityLabel(severity: string | undefined): string {
  const key = `events.severity.${severity ?? ""}`;
  return i18nTe(key) ? i18nT(key) : String(severity ?? "");
}

function externalEventPopup(ev: ExternalEvent): string {
  const started = new Date(ev.started_at);
  return `<div class="mp">
    <p class="mp-title">${esc(ev.title)}</p>
    <p class="mp-row mp-dim">${esc(eventTypeLabel(ev.type))} · ${esc(i18nT("map.severity"))} ${esc(eventSeverityLabel(ev.severity))}</p>
    <p class="mp-row">${esc(ev.description)}</p>
    <p class="mp-row mp-dim">${esc(Number.isNaN(started.getTime()) ? "" : started.toLocaleTimeString())}${ev.radius_m ? ` · ${esc(i18nT("map.radius"))} ${Number(ev.radius_m).toFixed(0)} m` : ""}</p>
  </div>`;
}

function severityColor(severity: string | undefined): string {
  switch ((severity ?? "").toLowerCase()) {
    case "critical":
    case "high":
      return MAP_COLORS.crit;
    case "medium":
      return MAP_COLORS.warn;
    default:
      return MAP_COLORS.muted;
  }
}

// ── Registro de marcadores HTML (se actualizan en su sitio, sin recrear) ──
interface MarkerEntry {
  marker: maplibregl.Marker;
  el: HTMLDivElement;
  html: string;
  tip: string;
  popup: maplibregl.Popup | null;
  onClick: (() => void) | null;
  group: string;
}
const htmlMarkers = new Map<string, MarkerEntry>();

interface UpsertOpts {
  tip?: string;
  popupHtml?: string;
  onClick?: () => void;
  zIndex?: number;
  animate?: boolean;
  matched?: boolean;
}

function upsertMarker(group: string, id: string, pos: LatLon, html: string, opts: UpsertOpts, seen: Set<string>): void {
  if (!map) return;
  const key = `${group}:${id}`;
  seen.add(key);
  let entry = htmlMarkers.get(key);
  if (!entry) {
    const el = document.createElement("div");
    el.className = "map-marker-host";
    const marker = new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(toLngLat(pos)).addTo(map);
    const created: MarkerEntry = { marker, el, html: "", tip: "", popup: null, onClick: null, group };
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      created.onClick?.();
      if (created.popup) {
        if (created.popup.isOpen()) created.popup.remove();
        else created.popup.setLngLat(created.marker.getLngLat()).addTo(map!);
      }
    });
    el.addEventListener("mouseenter", () => {
      if (created.tip && !created.popup?.isOpen()) tooltip?.show(created.marker.getLngLat().toArray() as [number, number], created.tip);
    });
    el.addEventListener("mouseleave", () => tooltip?.hide());
    entry = created;
    htmlMarkers.set(key, entry);
  } else if (opts.animate) {
    moveMarkerSmooth(key, entry.marker, pos);
  } else {
    entry.marker.setLngLat(toLngLat(pos));
  }
  if (entry.html !== html) {
    entry.el.innerHTML = html;
    entry.html = html;
  }
  entry.el.style.zIndex = String(opts.zIndex ?? 1);
  entry.el.classList.toggle("map-marker-matched", !!opts.matched);
  entry.tip = opts.tip ?? "";
  entry.onClick = opts.onClick ?? null;
  if (opts.popupHtml) {
    entry.popup ??= new maplibregl.Popup({ offset: 18, maxWidth: "260px", className: "map-popup" });
    entry.popup.setHTML(opts.popupHtml);
  } else if (entry.popup) {
    entry.popup.remove();
    entry.popup = null;
  }
}

function pruneMarkers(group: string, seen: Set<string>): void {
  for (const [key, entry] of htmlMarkers) {
    if (entry.group === group && !seen.has(key)) {
      entry.popup?.remove();
      entry.marker.remove();
      htmlMarkers.delete(key);
      markerAnim.delete(key);
    }
  }
}

// Interpolación suave de la posición entre ticks del motor.
const markerAnim = new Map<string, { from: [number, number]; to: [number, number]; t0: number; marker: maplibregl.Marker }>();
let animRaf = 0;

function stepMarkerAnim() {
  const now = performance.now();
  let active = false;
  for (const [key, a] of markerAnim) {
    const u = Math.min(1, (now - a.t0) / 280);
    a.marker.setLngLat([a.from[0] + (a.to[0] - a.from[0]) * u, a.from[1] + (a.to[1] - a.from[1]) * u]);
    if (u >= 1) markerAnim.delete(key);
    else active = true;
  }
  animRaf = active ? requestAnimationFrame(stepMarkerAnim) : 0;
}

function moveMarkerSmooth(key: string, marker: maplibregl.Marker, pos: LatLon) {
  const to = toLngLat(pos);
  const from = marker.getLngLat().toArray() as [number, number];
  if (Math.abs(from[0] - to[0]) + Math.abs(from[1] - to[1]) < 2e-6) {
    marker.setLngLat(to);
    markerAnim.delete(key);
    return;
  }
  markerAnim.set(key, { from, to, t0: performance.now(), marker });
  if (!animRaf) animRaf = requestAnimationFrame(stepMarkerAnim);
}

// ── Filtros del asistente ────────────────────────────────────────────────
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

// Unidades del mismo tipo en la misma celda (~140 m) se agrupan en un marcador.
const CLUSTER_THRESHOLD = 3;
const CLUSTER_GRID = 800;

// ── Capas vectoriales ────────────────────────────────────────────────────
function addOverlayLayers(m: maplibregl.Map) {
  for (const id of ["events", "jams", "routes", "companion-routes", "weather"]) setGeoJson(m, id, []);
  m.addLayer({ id: "events-fill", type: "fill", source: "events", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.14 } });
  m.addLayer({ id: "events-line", type: "line", source: "events", paint: { "line-color": ["get", "color"], "line-width": 1.5 } });
  m.addLayer({ id: "jams-fill", type: "fill", source: "jams", paint: { "fill-color": MAP_COLORS.crit, "fill-opacity": 0.16 } });
  m.addLayer({ id: "jams-line", type: "line", source: "jams", paint: { "line-color": MAP_COLORS.crit, "line-width": 1.5, "line-dasharray": [2, 1.5] } });
  m.addLayer({
    id: "routes-casing",
    type: "line",
    source: "routes",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": MAP_COLORS.casing, "line-width": ["+", ["get", "width"], 3], "line-opacity": ["get", "opacity"] },
  });
  m.addLayer({
    id: "routes-line",
    type: "line",
    source: "routes",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["get", "color"], "line-width": ["get", "width"], "line-opacity": ["get", "opacity"] },
  });
  m.addLayer({
    id: "companion-routes-line",
    type: "line",
    source: "companion-routes",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.8, "line-dasharray": [1.5, 1.5] },
  });
  m.addLayer({
    id: "weather-circle",
    type: "circle",
    source: "weather",
    paint: {
      "circle-radius": ["get", "radius"],
      "circle-color": ["get", "color"],
      "circle-opacity": 0.35,
      "circle-stroke-color": ["get", "color"],
      "circle-stroke-width": 2,
    },
  });

  // Tooltips y clics sobre las capas vectoriales.
  for (const layer of ["events-fill", "jams-fill", "weather-circle"]) {
    m.on("mousemove", layer, (e) => {
      const tip = e.features?.[0]?.properties?.tip as string | undefined;
      m.getCanvas().style.cursor = "pointer";
      if (tip) tooltip?.show([e.lngLat.lng, e.lngLat.lat], tip);
    });
    m.on("mouseleave", layer, () => {
      m.getCanvas().style.cursor = "";
      tooltip?.hide();
    });
  }
  m.on("click", "jams-fill", (e) => {
    const id = e.features?.[0]?.properties?.id as string | undefined;
    if (id && isDeleteMode()) emit("deleteObject", "jam", id);
  });
  m.on("click", "events-fill", (e) => {
    const html = e.features?.[0]?.properties?.popup as string | undefined;
    if (html && !isDeleteMode() && mapToolRef.value === "none") {
      new maplibregl.Popup({ offset: 8, maxWidth: "300px", className: "map-popup" }).setLngLat(e.lngLat).setHTML(html).addTo(m);
    }
  });
}

function routeStyle(amb: Ambulance) {
  const sel = selectedAmbulanceId.value === amb.id;
  const wf = (amb as Ambulance & { weatherFactor?: number }).weatherFactor;
  const color = sel
    ? MAP_COLORS.routeSelected
    : wf != null && wf < 0.65
      ? MAP_COLORS.crit
      : wf != null && wf < 0.85
        ? MAP_COLORS.warn
        : MAP_COLORS.route;
  return { color, width: sel ? 5 : 3, opacity: sel ? 0.95 : 0.7 };
}

let fittedFilterKey = "";

function syncLayers() {
  const s = state.value;
  const m = map;
  if (!m || !s) return;

  const entityTypeById = new Map<string, EntityType>();
  for (const et of s.entityTypes || []) entityTypeById.set(et.id, et);

  // ── Unidades (con agrupación por celda y tipo) ──
  const unitSeen = new Set<string>();
  const ambs = s.ambulances;
  type Cell = { typeId: string; items: Ambulance[]; latSum: number; lonSum: number };
  const cells = new Map<string, Cell>();
  for (const amb of ambs) {
    const typeId = amb.entityTypeId || "ambulance";
    const lat = amb.latitude ?? 0;
    const lon = amb.longitude ?? 0;
    const key = `${typeId}|${Math.round(lat * CLUSTER_GRID)}|${Math.round(lon * CLUSTER_GRID)}`;
    let c = cells.get(key);
    if (!c) {
      c = { typeId, items: [], latSum: 0, lonSum: 0 };
      cells.set(key, c);
    }
    c.items.push(amb);
    c.latSum += lat;
    c.lonSum += lon;
  }

  const singles = new Set<string>();
  for (const [key, c] of cells) {
    const selectedInCell = c.items.some((a) => selectedAmbulanceId.value === a.id);
    if (c.items.length >= CLUSTER_THRESHOLD && !selectedInCell) {
      const et = entityTypeById.get(c.typeId);
      const lat = c.latSum / c.items.length;
      const lon = c.lonSum / c.items.length;
      const typeName = et?.name || c.typeId;
      upsertMarker(
        "cluster",
        key,
        [lat, lon],
        clusterHtml(iconEmojiForType(c.typeId, et?.name), c.items.length, (et?.color as string) || MAP_COLORS.muted),
        {
          tip: `<b>${esc(typeName)}</b><br>${esc(i18nT("map.cluster_count", { n: c.items.length }))}<br><span class="mp-dim">${esc(i18nT("map.cluster_hint"))}</span>`,
          zIndex: 4,
          onClick: () => map?.easeTo({ center: [lon, lat], zoom: Math.max(map.getZoom() + 2, 16) }),
        },
        unitSeen,
      );
    } else {
      for (const amb of c.items) singles.add(amb.id);
    }
  }

  const filtersActive = hasActiveUiFilters();
  for (let idx = 0; idx < ambs.length; idx++) {
    const amb = ambs[idx];
    if (!singles.has(amb.id)) continue;
    if (filtersActive && !passesUiFilters(amb)) continue;
    const pos: LatLon = [
      amb.latitude ?? activeRegion.value?.spawn[0] ?? DEFAULT_SPAWN_LAT,
      amb.longitude ?? activeRegion.value?.spawn[1] ?? DEFAULT_SPAWN_LON,
    ];
    const sel = selectedAmbulanceId.value === amb.id;
    const label = displayId(amb, idx, s.entityTypes);
    const typeId = amb.entityTypeId || "ambulance";
    const et = entityTypeById.get(typeId);
    const builtIn = typeId === "ambulance" || typeId === "helicopter" || typeId === "police_patrol";
    const html = builtIn
      ? entityIconHtml(typeId, { selected: sel, hasPatient: !!amb.hasPatient, label })
      : customVehicleHtml(typeId, et?.name, (et?.color as string) || MAP_COLORS.muted, { selected: sel, hasPatient: !!amb.hasPatient, label, customSvg: et?.iconSvg ?? null });
    upsertMarker(
      "unit",
      amb.id,
      pos,
      html,
      {
        popupHtml: ambulancePopupHtml(label, amb),
        zIndex: sel ? 10 : 5,
        animate: true,
        matched: filtersActive,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "ambulance", amb.id);
          else store.selectAmbulance(amb.id);
        },
      },
      unitSeen,
    );
  }
  pruneMarkers("unit", unitSeen);
  pruneMarkers("cluster", unitSeen);

  // Encuadra las unidades que cumplen el filtro del asistente (una vez por filtro).
  if (filtersActive && !selectedAmbulanceId.value) {
    const filterKey = JSON.stringify(store.uiFilters);
    const matching = ambs.filter((a) => passesUiFilters(a) && a.latitude != null && a.longitude != null);
    if (filterKey !== fittedFilterKey && matching.length >= 1 && matching.length <= 30) {
      const bounds = new maplibregl.LngLatBounds();
      for (const a of matching) bounds.extend([a.longitude as number, a.latitude as number]);
      m.fitBounds(bounds, { padding: 80, maxZoom: 15 });
      fittedFilterKey = filterKey;
    }
  } else {
    fittedFilterKey = "";
  }

  // ── Rutas ──
  const routeFeatures: GeoJSON.Feature[] = [];
  for (const amb of ambs) {
    const raw = amb.routeCoords;
    if (!raw || raw.length < 2) continue;
    const coords = remainingRouteCoords(raw as [number, number][], Number(amb.routeProgressM ?? 0));
    if (coords.length < 2) continue;
    routeFeatures.push({
      type: "Feature",
      properties: routeStyle(amb),
      geometry: { type: "LineString", coordinates: coords.map((c) => toLngLat(c as LatLon)) },
    });
  }
  // La ruta seleccionada se dibuja la última (encima).
  routeFeatures.sort((a, b) => Number(a.properties?.width) - Number(b.properties?.width));
  setGeoJson(m, "routes", routeFeatures);

  // ── Lugares (hospitales, gasolineras, tipos personalizados) ──
  const poiSeen = new Set<string>();
  for (const p of s.pois as Poi[]) {
    if (p.kind === "weather_station") continue;
    const et = entityTypeById.get(p.kind);
    const builtIn = p.kind === "hospital" ? "hospital" : p.kind === "gas_station" ? "gas_station" : "custom";
    const shortName = p.name.length > 14 ? `${p.name.slice(0, 13)}…` : p.name;
    upsertMarker(
      "poi",
      p.id,
      [p.latitude, p.longitude],
      entityIconHtml(builtIn, { customSvg: et?.iconSvg ?? undefined, customColor: et?.color ?? undefined, label: shortName }),
      {
        tip: `<b>${esc(p.name)}</b><br><span class="mp-dim">${esc(et?.name ?? p.kind)}</span>`,
        zIndex: 2,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "poi", p.id);
        },
      },
      poiSeen,
    );
  }
  pruneMarkers("poi", poiSeen);

  // ── Estaciones meteorológicas ──
  const readings = (s.weatherStations ?? {}) as Record<string, WeatherReading>;
  const weatherFeatures: GeoJSON.Feature[] = (s.pois as Poi[])
    .filter((p) => p.kind === "weather_station")
    .map((st) => {
      const r = readings[st.id];
      const precip = r?.precipitation_mm ?? 0;
      const wind = r?.wind_speed_kmh ?? 0;
      const vis = r?.visibility_km ?? 10;
      const alert = precip > 5 || wind > 25 || vis < 5;
      const name = esc(st.name ?? i18nT("map.weather_station"));
      const tip = r
        ? `<b>${alert ? `${esc(i18nT("map.weather_alert"))} · ` : ""}${name}</b><br>${r.temperature_c} °C · ${esc(i18nT("map.rain"))} ${precip} mm · ${esc(i18nT("map.wind"))} ${wind} km/h`
        : `<b>${name}</b><br>${esc(i18nT("map.no_reading"))}`;
      return {
        type: "Feature",
        properties: { tip, color: alert ? MAP_COLORS.crit : MAP_COLORS.muted, radius: alert ? 8 : 5 },
        geometry: { type: "Point", coordinates: [st.longitude, st.latitude] },
      } as GeoJSON.Feature;
    });
  setGeoJson(m, "weather", weatherFeatures);

  // ── Emergencias ──
  const emSeen = new Set<string>();
  for (const e of s.emergencies as Emergency[]) {
    if (e.status === "resolved") continue;
    const external = e.source === "external_feed";
    upsertMarker(
      "emergency",
      e.id,
      [e.latitude, e.longitude],
      entityIconHtml("emergency", { emergencyStatus: e.status, external }),
      {
        tip: `<b>${esc(e.title)}</b><br><span class="mp-dim">${esc(i18nT(`map.emergency_status.${e.status}`))}${external ? ` · ${esc(i18nT("map.external_source"))}` : ""}</span>`,
        zIndex: 8,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "emergency", e.id);
        },
      },
      emSeen,
    );
  }
  pruneMarkers("emergency", emSeen);

  // ── Cortes de tráfico ──
  setGeoJson(
    m,
    "jams",
    (s.jams as Jam[])
      .filter((j) => j.polygon?.length >= 3)
      .map((j) => {
        const ring = j.polygon.map((pt) => toLngLat(pt as LatLon));
        ring.push(ring[0]);
        return {
          type: "Feature",
          properties: { id: j.id, tip: `<b>${esc(i18nT("operations.tool_jam"))}</b>` },
          geometry: { type: "Polygon", coordinates: [ring] },
        } as GeoJSON.Feature;
      }),
  );

  // ── Unidades de apoyo ──
  const compSeen = new Set<string>();
  const compRoutes: GeoJSON.Feature[] = [];
  for (const c of s.companions ?? []) {
    const builtIn = c.kind === "helicopter" || c.kind === "police_patrol";
    const et = entityTypeById.get(c.kind);
    const em = s.emergencies.find((e) => e.id === c.assignedEmergencyId);
    upsertMarker(
      "companion",
      c.id,
      [c.latitude, c.longitude],
      entityIconHtml(builtIn ? c.kind : "custom", { label: companionLabel(c), customSvg: et?.iconSvg ?? undefined, customColor: et?.color ?? undefined }),
      {
        popupHtml: companionPopupHtml(c, em?.title ?? null),
        zIndex: 6,
        animate: true,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "companion", c.id);
          else store.selectCompanion(c.id);
        },
      },
      compSeen,
    );
    if (c.routeCoords && c.routeCoords.length >= 2) {
      compRoutes.push({
        type: "Feature",
        properties: { color: (et?.color as string) || MAP_COLORS.route },
        geometry: { type: "LineString", coordinates: c.routeCoords.map((pt) => toLngLat(pt as LatLon)) },
      });
    }
  }
  pruneMarkers("companion", compSeen);
  setGeoJson(m, "companion-routes", compRoutes);

  // ── Incidencias externas (áreas) ──
  setGeoJson(
    m,
    "events",
    ((s.externalEvents ?? []) as ExternalEvent[])
      .filter((ev) => !ev.resolved_at && Number.isFinite(Number(ev.latitude)) && Number.isFinite(Number(ev.longitude)))
      .map((ev) => {
        const radius = Number(ev.radius_m) > 0 ? Math.min(2000, Math.max(40, Number(ev.radius_m))) : 80;
        return {
          type: "Feature",
          properties: {
            color: severityColor(ev.severity),
            tip: `<b>${esc(eventTypeLabel(ev.type))}</b><br>${esc(ev.title ?? "")} · ${esc(eventSeverityLabel(ev.severity))}`,
            popup: externalEventPopup(ev),
          },
          geometry: { type: "Polygon", coordinates: [circleRing([Number(ev.latitude), Number(ev.longitude)], radius)] },
        } as GeoJSON.Feature;
      }),
  );
}

let rafSync = 0;
function scheduleSync() {
  if (rafSync) return;
  rafSync = requestAnimationFrame(() => {
    rafSync = 0;
    syncLayers();
  });
}

const stops: Array<() => void> = [];

onMounted(async () => {
  if (!container.value) return;
  const r = activeRegion.value;
  const m = await createMap(container.value, {
    center: (r?.center as LatLon | undefined) ?? MAP_DEFAULT_CENTER,
    zoom: r?.zoom ?? MAP_DEFAULT_ZOOM,
  });
  map = m;
  tooltip = createHoverTooltip(m);
  addOverlayLayers(m);

  m.on("click", (e) => {
    const tool = mapToolRef.value;
    // En modo borrar solo cuentan los clics sobre elementos (sus propios manejadores).
    if (tool && tool !== "none" && tool !== "delete") emit("mapClick", e.lngLat.lat, e.lngLat.lng);
  });

  stops.push(
    watch(activeRegion, (reg) => {
      if (!map || !reg) return;
      const center = toLngLat(reg.center as LatLon);
      const far = map.getCenter().distanceTo(new maplibregl.LngLat(center[0], center[1])) > 50_000;
      if (far) map.jumpTo({ center, zoom: reg.zoom });
      else map.flyTo({ center, zoom: reg.zoom, duration: 800 });
    }),
    watch(() => state.value, scheduleSync, { flush: "post" }),
    watch(selectedAmbulanceId, scheduleSync, { flush: "post" }),
    watch(() => store.uiFilters, scheduleSync, { deep: true, flush: "post" }),
    watch(() => props.fullscreen, () => requestAnimationFrame(() => map?.resize())),
  );
  scheduleSync();
});

onUnmounted(() => {
  stops.forEach((stop) => stop());
  if (animRaf) cancelAnimationFrame(animRaf);
  if (rafSync) cancelAnimationFrame(rafSync);
  markerAnim.clear();
  htmlMarkers.clear();
  map?.remove();
  map = null;
});
</script>

<template>
  <div
    class="sentinel-map relative h-full w-full overflow-hidden"
    :class="[
      { 'map-tool-place': mapTool !== 'none' && mapTool !== 'delete' },
      { 'map-cursor-delete': mapTool === 'delete' },
      fullscreen ? 'min-h-0 flex-1' : 'min-h-[320px]',
    ]"
  >
    <div ref="container" class="h-full w-full" />
    <p
      v-if="mapTool === 'delete'"
      class="pointer-events-none absolute left-3 top-3 z-[5] rounded border border-red-500/50 bg-white px-3 py-1.5 text-[11px] font-medium text-red-700 shadow"
    >
      {{ i18nT('operations.tool_delete_hint') }} · Esc
    </p>
  </div>
</template>

<style scoped>
.map-tool-place :deep(.maplibregl-canvas) {
  cursor: crosshair;
}
.map-cursor-delete :deep(.maplibregl-canvas),
.map-cursor-delete :deep(.map-marker-host) {
  cursor: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'><circle cx='14' cy='14' r='12' fill='rgba(217,48,37,0.18)' stroke='%23d93025' stroke-width='2'/><path d='M9 9l10 10M19 9l-10 10' stroke='%23d93025' stroke-width='2.5' stroke-linecap='round'/></svg>") 14 14, crosshair;
}
</style>
