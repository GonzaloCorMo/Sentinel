/**
 * Motor de mapas de Sentinel: MapLibre GL nativo con el estilo Liberty de
 * OpenFreeMap (teselas vectoriales OpenMapTiles, sin API key), usado tal cual
 * e igual en tema claro y oscuro. Las coordenadas de la app van en
 * [lat, lon]; MapLibre usa [lon, lat] — usa `toLngLat` en la frontera.
 */
import * as maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import type * as GeoJSON from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
// El worker de MapLibre importa un chunk compartido por ruta relativa y Vite lo
// rompe al pre-empaquetar: se compila aparte y se registra su URL.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

export { maplibregl };
export type LatLon = [number, number];

const BASE_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

let stylePromise: Promise<StyleSpecification> | null = null;

/** Estilo Liberty sin modificar (se descarga una vez y se reutiliza). */
export function sentinelStyle(): Promise<StyleSpecification> {
  stylePromise ??= fetch(BASE_STYLE_URL)
    .then((r) => {
      if (!r.ok) throw new Error(`Estilo de mapa no disponible (${r.status})`);
      return r.json() as Promise<StyleSpecification>;
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
    { id: "bg", type: "background", paint: { "background-color": "#f8f4f0" } },
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
