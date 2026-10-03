/**
 * Motor de mapas de Sentinel: MapLibre GL nativo con teselas vectoriales de
 * OpenFreeMap (OpenMapTiles, sin API key).
 *
 * Un único estilo, igual en tema claro y oscuro, con la legibilidad de Google
 * Maps: calles con borde, carreteras principales en amarillo, edificios
 * visibles al acercarse y nombres con halo. Las coordenadas de la app van en
 * [lat, lon]; MapLibre usa [lon, lat] — usa `toLngLat` en la frontera.
 */
import * as maplibregl from "maplibre-gl";
import type { LayerSpecification, StyleSpecification } from "maplibre-gl";
import type * as GeoJSON from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
// El worker de MapLibre importa un chunk compartido por ruta relativa y Vite lo
// rompe al pre-empaquetar: se compila aparte y se registra su URL.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

export { maplibregl };
export type LatLon = [number, number];

const BASE_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

/** Paleta inspirada en Google Maps (día). */
const P = {
  land: "#f2f1ed",
  residential: "#ebeae6",
  park: "#c6e8b3",
  wood: "#cfe6c1",
  hospital: "#f7e1e0",
  school: "#efecd9",
  water: "#9fd0f5",
  waterLabel: "#4a7fb3",
  building: "#e2e0da",
  buildingOutline: "#cfccc5",
  minorRoad: "#ffffff",
  minorCasing: "#d6d8dc",
  arterial: "#ffffff",
  arterialCasing: "#bfc3c9",
  highway: "#fcd877",
  highwayCasing: "#e8b44a",
  rail: "#b9bcc1",
  aeroway: "#e5e5e3",
  boundary: "#9aa0a6",
  label: "#5f6368",
  labelStrong: "#202124",
  halo: "#ffffff",
};

/** Capas que restan claridad a un mapa operativo. */
// Fuera también las paradas de transporte y los POI menores: saturan el mapa
// y tapan lo que importa (calles, edificios, carreteras y nuestros marcadores).
const DROP_LAYERS = new Set(["building-3d", "natural_earth", "road_area_pattern", "poi_transit", "poi_r20", "poi_r7"]);

function lineKind(id: string): "highway" | "arterial" | "minor" | "rail" | null {
  if (id.includes("rail")) return "rail";
  if (id.includes("motorway") || id.includes("trunk")) return "highway";
  if (id.includes("primary") || id.includes("secondary") || id.includes("tertiary") || id.includes("link")) return "arterial";
  if (id.includes("minor") || id.includes("street") || id.includes("service") || id.includes("path")) return "minor";
  return null;
}

function tune(layer: LayerSpecification): LayerSpecification | null {
  if (DROP_LAYERS.has(layer.id)) return null;
  const id = layer.id;
  const paint = { ...((layer as { paint?: Record<string, unknown> }).paint ?? {}) } as Record<string, unknown>;
  const out = { ...layer } as LayerSpecification & { minzoom?: number };

  switch (layer.type) {
    case "background":
      paint["background-color"] = P.land;
      break;
    case "fill":
      if (id === "water") paint["fill-color"] = P.water;
      else if (id.startsWith("park")) {
        paint["fill-color"] = P.park;
        paint["fill-outline-color"] = P.park;
      } else if (id.startsWith("landcover_wood") || id === "landcover_grass" || id === "landcover_wetland") paint["fill-color"] = P.wood;
      else if (id === "landuse_residential") paint["fill-color"] = P.residential;
      else if (id === "landuse_hospital") paint["fill-color"] = P.hospital;
      else if (id === "landuse_school") paint["fill-color"] = P.school;
      else if (id.startsWith("landuse_") || id === "landcover_sand" || id === "landcover_ice") paint["fill-color"] = P.residential;
      else if (id === "aeroway_fill") paint["fill-color"] = P.aeroway;
      else if (id === "building") {
        // Edificios desde zoom 14, cada vez más definidos al acercarse.
        out.minzoom = 14;
        paint["fill-color"] = P.building;
        paint["fill-outline-color"] = P.buildingOutline;
        paint["fill-opacity"] = ["interpolate", ["linear"], ["zoom"], 14, 0.4, 16, 1];
      }
      break;
    case "line": {
      if (id.startsWith("waterway")) {
        paint["line-color"] = P.water;
        break;
      }
      if (id.startsWith("boundary")) {
        paint["line-color"] = P.boundary;
        break;
      }
      if (id.startsWith("aeroway")) {
        paint["line-color"] = "#ffffff";
        break;
      }
      if (id === "park_outline") {
        paint["line-color"] = P.park;
        break;
      }
      const kind = lineKind(id);
      if (!kind) break;
      const casing = id.includes("casing");
      if (kind === "rail") paint["line-color"] = P.rail;
      else if (kind === "highway") paint["line-color"] = casing ? P.highwayCasing : P.highway;
      else if (kind === "arterial") paint["line-color"] = casing ? P.arterialCasing : P.arterial;
      else paint["line-color"] = casing ? P.minorCasing : P.minorRoad;
      break;
    }
    case "symbol": {
      // En los POI principales se ocultan las paradas de autobús (iconos
      // repetidos por toda la ciudad que no aportan a la operación).
      if (id === "poi_r1") {
        (out as { filter?: unknown }).filter = ["all", (layer as { filter?: unknown }).filter, ["!=", ["get", "class"], "bus"]];
      }
      const isWater = id.startsWith("water");
      const isPlace = id.startsWith("label_");
      if (isWater) paint["text-color"] = P.waterLabel;
      else if (isPlace) paint["text-color"] = id === "label_city" || id === "label_town" || id === "label_village" ? P.labelStrong : P.label;
      else paint["text-color"] = P.label;
      paint["text-halo-color"] = P.halo;
      paint["text-halo-width"] = 1.4;
      paint["text-halo-blur"] = 0.2;
      break;
    }
    default:
      break;
  }
  return { ...out, paint } as LayerSpecification;
}

let stylePromise: Promise<StyleSpecification> | null = null;

/** Estilo único de Sentinel (se descarga una vez y se reutiliza). */
export function sentinelStyle(): Promise<StyleSpecification> {
  stylePromise ??= fetch(BASE_STYLE_URL)
    .then((r) => {
      if (!r.ok) throw new Error(`Estilo de mapa no disponible (${r.status})`);
      return r.json() as Promise<StyleSpecification>;
    })
    .then((base) => {
      const sources = { ...base.sources };
      delete (sources as Record<string, unknown>).ne2_shaded;
      const layers = base.layers.map(tune).filter((l): l is LayerSpecification => l !== null);
      return { ...base, sources, layers };
    })
    .catch((err) => {
      stylePromise = null;
      throw err;
    });
  return stylePromise;
}

/** Estilo mínimo si no hay red hacia el servidor de teselas: al menos no queda en blanco. */
const OFFLINE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": P.land } },
    { id: "osm", type: "raster", source: "osm" },
  ],
};

export function toLngLat([lat, lon]: LatLon): [number, number] {
  return [lon, lat];
}

export interface CreateMapOptions {
  center: LatLon;
  zoom: number;
  interactive?: boolean;
  /** Muestra los botones de zoom (arriba a la derecha). */
  controls?: boolean;
}

/**
 * Crea un mapa con el estilo de Sentinel. Resuelve cuando el estilo está
 * cargado, de modo que se pueden añadir fuentes y capas propias.
 */
export async function createMap(container: HTMLElement, opts: CreateMapOptions): Promise<maplibregl.Map> {
  let style: StyleSpecification;
  try {
    style = await sentinelStyle();
  } catch {
    style = OFFLINE_STYLE;
  }
  const map = new maplibregl.Map({
    container,
    style,
    center: toLngLat(opts.center),
    zoom: opts.zoom,
    minZoom: 3,
    maxZoom: 19,
    interactive: opts.interactive ?? true,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    attributionControl: { compact: true },
    fadeDuration: 150,
  });
  map.touchZoomRotate.disableRotation();
  if (opts.controls !== false && (opts.interactive ?? true)) {
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  }
  // Si el contenedor cambia de tamaño (pantalla completa, paneles), se ajusta.
  const ro = new ResizeObserver(() => map.resize());
  ro.observe(container);
  map.once("remove", () => ro.disconnect());
  // Algún icono del sprite remoto puede faltar: se sustituye por uno vacío.
  map.on("styleimagemissing", (e) => {
    if (!map.hasImage(e.id)) map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
  });
  await new Promise<void>((resolve) => {
    if (map.isStyleLoaded()) resolve();
    else map.once("load", () => resolve());
  });
  return map;
}

/** Polígono circular (anillo [lon, lat]) de radio en metros, para áreas de incidencia. */
export function circleRing(center: LatLon, radiusM: number, steps = 48): [number, number][] {
  const [lat, lon] = center;
  const dLat = radiusM / 111_320;
  const dLon = radiusM / (111_320 * Math.max(0.2, Math.cos((lat * Math.PI) / 180)));
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI;
    ring.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return ring;
}

/** Fuente GeoJSON (crea o actualiza) — evita repetir la comprobación en cada sincronía. */
export function setGeoJson(map: maplibregl.Map, id: string, features: GeoJSON.Feature[]): void {
  const data: GeoJSON.FeatureCollection = { type: "FeatureCollection", features };
  const src = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  if (src) src.setData(data);
  else map.addSource(id, { type: "geojson", data });
}

/**
 * Tooltip ligero (un único popup reutilizado) para mostrar al pasar el ratón
 * por marcadores HTML o por elementos de capas vectoriales.
 */
export function createHoverTooltip(map: maplibregl.Map) {
  const popup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    className: "map-tooltip",
    offset: 14,
    maxWidth: "280px",
  });
  return {
    show(lngLat: [number, number], html: string) {
      popup.setLngLat(lngLat).setHTML(html).addTo(map);
    },
    hide() {
      popup.remove();
    },
  };
}
