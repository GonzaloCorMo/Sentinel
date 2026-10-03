/**
 * Mapa base vectorial con aspecto de Google Maps en tema claro y oscuro.
 *
 * Teselas: OpenFreeMap (OpenMapTiles, sin API key). Se parte del estilo
 * `liberty` y se recolorea capa a capa con las paletas estándar de Google
 * Maps (día y noche). Se renderiza con MapLibre GL dentro de Leaflet para
 * conservar todos los marcadores, popups y polilíneas existentes.
 */
import L from "leaflet";
import "maplibre-gl/dist/maplibre-gl.css";
import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import { setWorkerUrl, type StyleSpecification, type LayerSpecification } from "maplibre-gl";
// El worker de MapLibre se importa por ruta relativa: Vite lo rompe al
// pre-empaquetar, así que se compila aparte y se registra su URL.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(maplibreWorkerUrl);
import type { Theme } from "@/composables/useTheme";

const BASE_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

interface Palette {
  land: string;
  residential: string;
  park: string;
  wood: string;
  hospital: string;
  school: string;
  water: string;
  waterLabel: string;
  building: string;
  buildingOutline: string;
  minorRoad: string;
  minorCasing: string;
  arterial: string;
  arterialCasing: string;
  highway: string;
  highwayCasing: string;
  rail: string;
  aeroway: string;
  boundary: string;
  label: string;
  labelStrong: string;
  labelHalo: string;
  roadLabel: string;
  poiLabel: string;
}

// Paleta "día" de Google Maps.
const LIGHT: Palette = {
  land: "#f5f5f3",
  residential: "#eeeeec",
  park: "#c3ecb2",
  wood: "#cde8c0",
  hospital: "#fbe3e2",
  school: "#f1eedc",
  water: "#aadaff",
  waterLabel: "#5b8fc2",
  building: "#e8e7e3",
  buildingOutline: "#dcdad5",
  minorRoad: "#ffffff",
  minorCasing: "#dadce0",
  arterial: "#ffffff",
  arterialCasing: "#c9ccd1",
  highway: "#fde293",
  highwayCasing: "#f3c25b",
  rail: "#c4c7cc",
  aeroway: "#e6e6e6",
  boundary: "#9aa0a6",
  label: "#5f6368",
  labelStrong: "#202124",
  labelHalo: "#ffffff",
  roadLabel: "#5f6368",
  poiLabel: "#70757a",
};

// Paleta "noche" de Google Maps.
const DARK: Palette = {
  land: "#242f3e",
  residential: "#28323f",
  park: "#263c3f",
  wood: "#25383a",
  hospital: "#3a2f3a",
  school: "#2c3442",
  water: "#17263c",
  waterLabel: "#515c6d",
  building: "#2b3544",
  buildingOutline: "#323d4d",
  minorRoad: "#38414e",
  minorCasing: "#212a37",
  arterial: "#4a5463",
  arterialCasing: "#212a37",
  highway: "#746855",
  highwayCasing: "#1f2835",
  rail: "#2f3948",
  aeroway: "#2b3544",
  boundary: "#4b6878",
  label: "#9ca5b3",
  labelStrong: "#d59563",
  labelHalo: "#17263c",
  roadLabel: "#9ca5b3",
  poiLabel: "#8a93a2",
};

/** Capas que sobran para un mapa operativo limpio. */
const DROP_LAYERS = new Set(["building-3d", "natural_earth", "road_area_pattern"]);

function lineKind(id: string): "highway" | "arterial" | "minor" | "rail" | null {
  if (id.includes("rail")) return "rail";
  if (id.includes("motorway") || id.includes("trunk")) return "highway";
  if (id.includes("primary") || id.includes("secondary") || id.includes("tertiary") || id.includes("link")) return "arterial";
  if (id.includes("minor") || id.includes("street") || id.includes("service") || id.includes("path")) return "minor";
  return null;
}

function recolor(layer: LayerSpecification, p: Palette): LayerSpecification | null {
  if (DROP_LAYERS.has(layer.id)) return null;
  const id = layer.id;
  const paint = { ...((layer as { paint?: Record<string, unknown> }).paint ?? {}) } as Record<string, unknown>;
  const set = (k: string, v: unknown) => {
    paint[k] = v;
  };

  switch (layer.type) {
    case "background":
      set("background-color", p.land);
      break;
    case "fill": {
      if (id === "water") set("fill-color", p.water);
      else if (id.startsWith("park")) {
        set("fill-color", p.park);
        set("fill-outline-color", p.park);
      } else if (id === "landcover_wood" || id === "landcover_grass" || id === "landcover_wetland") set("fill-color", p.wood);
      else if (id === "landuse_residential") set("fill-color", p.residential);
      else if (id === "landuse_hospital") set("fill-color", p.hospital);
      else if (id === "landuse_school") set("fill-color", p.school);
      else if (id.startsWith("landuse_") || id === "landcover_sand" || id === "landcover_ice") set("fill-color", p.residential);
      else if (id === "aeroway_fill") set("fill-color", p.aeroway);
      else if (id === "building") {
        set("fill-color", p.building);
        set("fill-outline-color", p.buildingOutline);
      }
      break;
    }
    case "line": {
      if (id.startsWith("waterway")) {
        set("line-color", p.water);
        break;
      }
      if (id.startsWith("boundary")) {
        set("line-color", p.boundary);
        break;
      }
      if (id.startsWith("aeroway")) {
        set("line-color", p.aeroway);
        break;
      }
      if (id === "park_outline") {
        set("line-color", p.park);
        break;
      }
      const kind = lineKind(id);
      if (!kind) break;
      const casing = id.includes("casing");
      if (kind === "rail") set("line-color", p.rail);
      else if (kind === "highway") set("line-color", casing ? p.highwayCasing : p.highway);
      else if (kind === "arterial") set("line-color", casing ? p.arterialCasing : p.arterial);
      else set("line-color", casing ? p.minorCasing : p.minorRoad);
      break;
    }
    case "symbol": {
      const isWater = id.startsWith("water");
      const isRoad = id.startsWith("highway-name") || id.startsWith("road_");
      const isPlace = id.startsWith("label_");
      const isPoi = id.startsWith("poi") || id === "airport";
      if (isWater) set("text-color", p.waterLabel);
      else if (isRoad) set("text-color", p.roadLabel);
      else if (isPlace) set("text-color", id === "label_city" || id === "label_town" ? p.labelStrong : p.label);
      else if (isPoi) set("text-color", p.poiLabel);
      else set("text-color", p.label);
      set("text-halo-color", p.labelHalo);
      set("text-halo-width", 1.2);
      break;
    }
    default:
      break;
  }
  return { ...layer, paint } as LayerSpecification;
}

let baseStylePromise: Promise<StyleSpecification> | null = null;

function loadBaseStyle(): Promise<StyleSpecification> {
  baseStylePromise ??= fetch(BASE_STYLE_URL).then((r) => {
    if (!r.ok) throw new Error(`Estilo de mapa no disponible (${r.status})`);
    return r.json() as Promise<StyleSpecification>;
  });
  return baseStylePromise;
}

export async function googleLikeStyle(theme: Theme): Promise<StyleSpecification> {
  const base = await loadBaseStyle();
  const palette = theme === "dark" ? DARK : LIGHT;
  const layers = base.layers
    .map((l) => recolor(l, palette))
    .filter((l): l is LayerSpecification => l !== null);
  const sources = { ...base.sources };
  delete (sources as Record<string, unknown>).ne2_shaded;
  return { ...base, sources, layers };
}

export interface Basemap {
  setTheme(theme: Theme): void;
  remove(): void;
}

/**
 * Añade el mapa base a un mapa Leaflet. Si el estilo vectorial no carga
 * (sin red hacia OpenFreeMap), cae a teselas raster de OpenStreetMap.
 */
export function addBasemap(map: L.Map, theme: Theme): Basemap {
  let current: Theme = theme;
  let gl: L.MaplibreGL | null = null;
  let fallback: L.TileLayer | null = null;
  let removed = false;

  const attribution =
    '&copy; <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>';

  googleLikeStyle(current)
    .then((style) => {
      if (removed) return;
      gl = maplibreGL({ style, attributionControl: false } as L.LeafletMaplibreGLOptions);
      gl.addTo(map);
      map.attributionControl?.addAttribution(attribution);
    })
    .catch(() => {
      if (removed) return;
      fallback = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
        maxZoom: 19,
      }).addTo(map);
    });

  return {
    setTheme(next: Theme) {
      if (next === current) return;
      current = next;
      if (!gl) return;
      googleLikeStyle(next).then((style) => {
        if (!removed) gl?.getMaplibreMap().setStyle(style);
      });
    },
    remove() {
      removed = true;
      gl?.remove();
      fallback?.remove();
    },
  };
}
