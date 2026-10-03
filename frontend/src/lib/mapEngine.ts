/**
 * Motor de mapas de Sentinel: MapLibre GL nativo con los estilos Alidade
 * Smooth de Stadia Maps, usados tal cual: «Alidade Smooth» en tema claro y
 * «Alidade Smooth Dark» en oscuro. El mapa cambia de estilo al cambiar el
 * tema sin perder las capas propias. Las coordenadas de la app van en
 * [lat, lon]; MapLibre usa [lon, lat] — usa `toLngLat` en la frontera.
 *
 * Stadia no pide clave desde localhost; en un dominio público hay que dar de
 * alta el dominio en Stadia o definir `VITE_STADIA_API_KEY`.
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

export type MapTheme = "light" | "dark";

const STYLE_URLS: Record<MapTheme, string> = {
  light: "https://tiles.stadiamaps.com/styles/alidade_smooth.json",
  dark: "https://tiles.stadiamaps.com/styles/alidade_smooth_dark.json",
};
const STADIA_KEY = (import.meta.env.VITE_STADIA_API_KEY as string | undefined)?.trim();

/** Tema activo de la app (`data-theme` en <html>, oscuro por defecto). */
export function currentMapTheme(): MapTheme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

const styleCache = new Map<MapTheme, Promise<StyleSpecification>>();

/** Estilo de Stadia para el tema (se descarga una vez y se reutiliza). */
export function sentinelStyle(theme: MapTheme = currentMapTheme()): Promise<StyleSpecification> {
  let promise = styleCache.get(theme);
  if (!promise) {
    const url = STADIA_KEY ? `${STYLE_URLS[theme]}?api_key=${encodeURIComponent(STADIA_KEY)}` : STYLE_URLS[theme];
    promise = fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`Estilo de mapa no disponible (${r.status})`);
        return r.json() as Promise<StyleSpecification>;
      })
      .catch((err) => {
        styleCache.delete(theme);
        throw err;
      });
    styleCache.set(theme, promise);
  }
  return promise;
}

async function styleOrOffline(theme: MapTheme): Promise<StyleSpecification> {
  try {
    return await sentinelStyle(theme);
  } catch {
    return OFFLINE_STYLE;
  }
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
  let theme = currentMapTheme();
  const style = await styleOrOffline(theme);
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
  // Cambio de tema: nuevo estilo base conservando fuentes y capas propias
  // (rutas, áreas…). Los marcadores HTML no dependen del estilo.
  const themeObserver = new MutationObserver(async () => {
    const next = currentMapTheme();
    if (next === theme) return;
    theme = next;
    const nextStyle = await styleOrOffline(next);
    if (theme !== next) return;
    map.setStyle(nextStyle, { transformStyle: keepOverlays });
  });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  map.once("remove", () => themeObserver.disconnect());
  await new Promise<void>((resolve) => {
    if (map.isStyleLoaded()) resolve();
    else map.once("load", () => resolve());
  });
  return map;
}

/** Copia al estilo nuevo las fuentes que no son del mapa base y las capas que las usan. */
function keepOverlays(prev: StyleSpecification | undefined, next: StyleSpecification): StyleSpecification {
  if (!prev) return next;
  const own = Object.keys(prev.sources).filter((id) => !(id in next.sources));
  const sources = { ...next.sources };
  for (const id of own) sources[id] = prev.sources[id];
  const layers = prev.layers.filter((l) => "source" in l && own.includes(l.source as string));
  return { ...next, sources, layers: [...next.layers, ...layers] };
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
