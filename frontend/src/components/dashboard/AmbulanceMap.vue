<script setup lang="ts">
import { storeToRefs } from "pinia";
import { onMounted, onUnmounted, ref, toRef, watch } from "vue";
import { useI18n } from "vue-i18n";
import { MAP_DEFAULT_CENTER, MAP_DEFAULT_ZOOM, DEFAULT_SPAWN_LAT, DEFAULT_SPAWN_LON } from "@/lib/mapDefaults";
import { energyOf, operatingCostOf } from "@/lib/energyDisplay";
import { remainingRouteCoords } from "@/lib/routePolyline";
import { unitStatus } from "@/lib/unitStatus";
import { displayId, prefixForType } from "@/lib/vehicleId";
import { TONE_RANK, clusterNodeHtml, tacticalNodeHtml, type NodeGlyph, type NodeTone } from "@/lib/tacticalMarkers";
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
  routeSelected: "#0891b2",
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

// ── Nodos tácticos ───────────────────────────────────────────────────────
// Cada objeto del mapa se describe como un nodo; `renderNodes` decide en
// función del zoom si se pinta solo o agrupado con los que tiene cerca.
type NodeKind = "unit" | "emergency" | "place";
interface NodeSpec {
  group: string;
  id: string;
  pos: LatLon;
  html: string;
  tone: NodeTone;
  kind: NodeKind;
  /** Al agrupar, el nodo de mayor prioridad hace de semilla. */
  priority: number;
  ping: boolean;
  clusterable: boolean;
  opts: UpsertOpts;
}

/** Por debajo de este zoom se ocultan las etiquetas. */
const LABEL_MIN_ZOOM = 13.5;
/** A partir de este zoom ya no se agrupa (todo cabe sin solaparse). */
const CLUSTER_MAX_ZOOM = 17;
/** Distancia en pantalla por debajo de la cual dos nodos se agrupan. */
const CLUSTER_RADIUS_PX = 30;

let lastNodes: NodeSpec[] = [];

function unitTone(amb: Ambulance): NodeTone {
  if (amb.poweredOff) return "off";
  if (unitStatus(amb).tone === "alert") return "warn";
  const energy = energyOf(amb, state.value?.entityTypes).value;
  if (energy != null && energy < 20) return "warn";
  return "unit";
}

function unitGlyph(amb: Ambulance): NodeGlyph {
  if (amb.hasPatient) return "cross";
  return amb.missionPhase === "to_emergency" ? "pip" : "none";
}

function isCriticalEmergency(e: Emergency): boolean {
  if (e.status !== "pending") return false;
  const sev = String(e.severity ?? "").toLowerCase();
  return sev === "" || sev === "critical" || sev === "high";
}

function seq(prefix: string, n: number, pad = 3): string {
  return `${prefix}-${String(n).padStart(pad, "0")}`;
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
    <p class="mp-title"><span class="mp-id">[${esc(c.displayLabel ?? companionLabel(c))}]</span></p>
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
    <p class="mp-title"><span class="mp-id">[${esc(label)}]</span> ${severity}</p>
    <p class="mp-row"><b>${esc(i18nT(`status.${unitStatus(amb).key}`))}</b></p>
    <p class="mp-row">${esc(i18nT("operations.speed"))}: <span class="mp-num">${Math.round(tel?.positioning?.speedKmh ?? 0)} km/h</span></p>
    <p class="mp-row">${esc(i18nT(info.labelKey))}: <span class="mp-num">${v.toFixed(0)} %</span></p>
    <div class="mp-bar"><div style="width:${Math.max(0, Math.min(100, v))}%;background:${v < 20 ? "#ff2a2a" : "#c3c9d0"}"></div></div>
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

  const nodes: NodeSpec[] = [];

  // ── Unidades ──
  const ambs = s.ambulances;
  const filtersActive = hasActiveUiFilters();
  for (let idx = 0; idx < ambs.length; idx++) {
    const amb = ambs[idx];
    if (filtersActive && !passesUiFilters(amb)) continue;
    const pos: LatLon = [
      amb.latitude ?? activeRegion.value?.spawn[0] ?? DEFAULT_SPAWN_LAT,
      amb.longitude ?? activeRegion.value?.spawn[1] ?? DEFAULT_SPAWN_LON,
    ];
    const sel = selectedAmbulanceId.value === amb.id;
    const label = displayId(amb, idx, s.entityTypes);
    const tone = unitTone(amb);
    nodes.push({
      group: "unit",
      id: amb.id,
      pos,
      html: tacticalNodeHtml({ shape: "rect", tone, glyph: unitGlyph(amb), label, selected: sel, matched: filtersActive && !sel }),
      tone,
      kind: "unit",
      priority: 50,
      ping: false,
      // La unidad seleccionada nunca se esconde dentro de un grupo.
      clusterable: !sel,
      opts: {
        popupHtml: ambulancePopupHtml(label, amb),
        zIndex: sel ? 10 : 5,
        animate: true,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "ambulance", amb.id);
          else store.selectAmbulance(amb.id);
        },
      },
    });
  }

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
  const placeSeq = new Map<string, number>();
  for (const p of s.pois as Poi[]) {
    if (p.kind === "weather_station") continue;
    const et = entityTypeById.get(p.kind);
    const prefix = p.kind === "hospital" ? "HOSP" : p.kind === "gas_station" ? "FUEL" : prefixForType(p.kind, et?.name);
    const n = (placeSeq.get(prefix) ?? 0) + 1;
    placeSeq.set(prefix, n);
    const glyph: NodeGlyph = p.kind === "hospital" ? "cross" : p.kind === "gas_station" ? "dot" : "none";
    nodes.push({
      group: "poi",
      id: p.id,
      pos: [p.latitude, p.longitude],
      html: tacticalNodeHtml({ shape: "square", tone: "infra", glyph, label: seq(prefix, n, 2) }),
      tone: "infra",
      kind: "place",
      priority: 10,
      ping: false,
      clusterable: true,
      opts: {
        tip: `<b>${esc(p.name)}</b><br><span class="mp-dim">${esc(et?.name ?? p.kind)}</span>`,
        zIndex: 2,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "poi", p.id);
        },
      },
    });
  }

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
  (s.emergencies as Emergency[]).forEach((e, i) => {
    if (e.status === "resolved") return;
    const external = e.source === "external_feed";
    const critical = isCriticalEmergency(e);
    const tone: NodeTone = e.status === "assigned" ? "warn" : "crit";
    nodes.push({
      group: "emergency",
      id: e.id,
      pos: [e.latitude, e.longitude],
      html: tacticalNodeHtml({ shape: "diamond", tone, dashed: external, ping: critical, label: seq("EMR", i + 1) }),
      tone,
      kind: "emergency",
      priority: critical ? 100 : 90,
      ping: critical,
      clusterable: true,
      opts: {
        tip: `<b>${esc(e.title)}</b><br><span class="mp-dim">${esc(i18nT(`map.emergency_status.${e.status}`))}${external ? ` · ${esc(i18nT("map.external_source"))}` : ""}</span>`,
        zIndex: 8,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "emergency", e.id);
        },
      },
    });
  });

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
  const compRoutes: GeoJSON.Feature[] = [];
  for (const c of s.companions ?? []) {
    const et = entityTypeById.get(c.kind);
    const em = s.emergencies.find((e) => e.id === c.assignedEmergencyId);
    nodes.push({
      group: "companion",
      id: c.id,
      pos: [c.latitude, c.longitude],
      html: tacticalNodeHtml({ shape: "rect", tone: "unit", glyph: "dot", label: companionLabel(c) }),
      tone: "unit",
      kind: "unit",
      priority: 45,
      ping: false,
      clusterable: true,
      opts: {
        popupHtml: companionPopupHtml(c, em?.title ?? null),
        zIndex: 6,
        animate: true,
        onClick: () => {
          if (isDeleteMode()) emit("deleteObject", "companion", c.id);
          else store.selectCompanion(c.id);
        },
      },
    });
    if (c.routeCoords && c.routeCoords.length >= 2) {
      compRoutes.push({
        type: "Feature",
        properties: { color: MAP_COLORS.route },
        geometry: { type: "LineString", coordinates: c.routeCoords.map((pt) => toLngLat(pt as LatLon)) },
      });
    }
  }
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

  lastNodes = nodes;
  renderNodes();
}

// ── Agrupación en pantalla ───────────────────────────────────────────────
/** Agrupa nodos a menos de CLUSTER_RADIUS_PX en pantalla (voraz, por prioridad). */
function clusterNodes(m: maplibregl.Map, list: NodeSpec[]): NodeSpec[][] {
  const pts = list.map((n) => m.project(toLngLat(n.pos)));
  const order = list
    .map((_, i) => i)
    .sort((a, b) => list[b].priority - list[a].priority || (list[a].id < list[b].id ? -1 : 1));
  const used = new Uint8Array(list.length);
  const r2 = CLUSTER_RADIUS_PX * CLUSTER_RADIUS_PX;
  const groups: NodeSpec[][] = [];
  for (const i of order) {
    if (used[i]) continue;
    used[i] = 1;
    const g = [list[i]];
    if (list[i].clusterable) {
      for (const j of order) {
        if (used[j] || !list[j].clusterable) continue;
        const dx = pts[i].x - pts[j].x;
        const dy = pts[i].y - pts[j].y;
        if (dx * dx + dy * dy <= r2) {
          used[j] = 1;
          g.push(list[j]);
        }
      }
    }
    groups.push(g);
  }
  return groups;
}

function clusterTip(g: NodeSpec[]): string {
  const count = (k: NodeKind) => g.filter((n) => n.kind === k).length;
  const parts = [
    [count("emergency"), "map.cluster_emergencies"],
    [count("unit"), "map.cluster_units"],
    [count("place"), "map.cluster_places"],
  ] as const;
  const rows = parts
    .filter(([n]) => n > 0)
    .map(([n, key]) => `<span class="mp-num">${n}</span> ${esc(i18nT(key, { n }, n))}`)
    .join("<br>");
  return `${rows}<br><span class="mp-dim">${esc(i18nT("map.cluster_hint"))}</span>`;
}

function renderNodes() {
  const m = map;
  if (!m) return;
  const zoom = m.getZoom();
  container.value?.classList.toggle("tn-labels-off", zoom < LABEL_MIN_ZOOM);
  const groups = zoom < CLUSTER_MAX_ZOOM ? clusterNodes(m, lastNodes) : lastNodes.map((n) => [n]);
  const seen = new Set<string>();
  for (const g of groups) {
    const seed = g[0];
    if (g.length === 1) {
      upsertMarker(seed.group, seed.id, seed.pos, seed.html, seed.opts, seen);
      continue;
    }
    let tone: NodeTone = "off";
    let lat = 0;
    let lon = 0;
    for (const n of g) {
      if (TONE_RANK[n.tone] > TONE_RANK[tone]) tone = n.tone;
      lat += n.pos[0];
      lon += n.pos[1];
    }
    const center: LatLon = [lat / g.length, lon / g.length];
    upsertMarker(
      "cluster",
      `${seed.group}:${seed.id}`,
      center,
      clusterNodeHtml(g.length, tone, g.some((n) => n.ping)),
      {
        tip: clusterTip(g),
        zIndex: tone === "crit" ? 9 : 4,
        onClick: () => {
          const bounds = new maplibregl.LngLatBounds();
          for (const n of g) bounds.extend(toLngLat(n.pos));
          m.fitBounds(bounds, { padding: 80, maxZoom: CLUSTER_MAX_ZOOM + 0.5, duration: 450 });
        },
      },
      seen,
    );
  }
  for (const group of ["unit", "poi", "emergency", "companion", "cluster"]) pruneMarkers(group, seen);
}

let rafNodes = 0;
function scheduleNodes() {
  if (rafNodes) return;
  rafNodes = requestAnimationFrame(() => {
    rafNodes = 0;
    renderNodes();
  });
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
  m.on("zoom", scheduleNodes);

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
  if (rafNodes) cancelAnimationFrame(rafNodes);
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
      class="pointer-events-none absolute left-3 top-3 z-[5] border border-[#ff2a2a] bg-[#101317]/90 px-3 py-1.5 font-mono text-[11px] text-[#ff2a2a]"
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
  cursor: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'><rect x='4' y='4' width='20' height='20' fill='none' stroke='%23ff2a2a' stroke-width='1.5'/><path d='M9 9l10 10M19 9l-10 10' stroke='%23ff2a2a' stroke-width='1.5'/></svg>") 14 14, crosshair;
}
</style>
