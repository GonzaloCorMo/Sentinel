/**
 * Motor de mapas de Sentinel: MapLibre GL nativo con teselas vectoriales de
 * OpenFreeMap (OpenMapTiles, sin API key) y el estilo propio de
 * `lib/mapStyle.ts`, igual en tema claro y oscuro. Las coordenadas de la app
 * van en [lat, lon]; MapLibre usa [lon, lat] — usa `toLngLat` en la frontera.
 */
import * as maplibregl from "maplibre-gl";
import type * as GeoJSON from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
// El worker de MapLibre importa un chunk compartido por ruta relativa y Vite lo
// rompe al pre-empaquetar: se compila aparte y se registra su URL.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

import { buildSentinelStyle, FIRST_LABEL_LAYER } from "./mapStyle";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

export { maplibregl, FIRST_LABEL_LAYER };
export type LatLon = [number, number];

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
  const map = new maplibregl.Map({
    container,
    style: buildSentinelStyle(),
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
