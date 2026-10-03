/**
 * Estilo base de Sentinel, escrito desde cero sobre el esquema OpenMapTiles
 * (teselas vectoriales de OpenFreeMap, sin API key).
 *
 * Objetivo: la lectura de Google Maps de día. Fondo neutro, vías blancas con
 * borde gris fino, autopistas en amarillo, anchos que crecen con el zoom,
 * edificios suaves al acercarse y nombres con halo. Nada de texturas,
 * sombreado de relieve ni iconos repetidos. Es el mismo en tema claro y
 * oscuro; los colores de los marcadores (lib/tacticalMarkers.ts) están
 * pensados para este fondo.
 */
import type {
  DataDrivenPropertyValueSpecification,
  ExpressionSpecification,
  LayerSpecification,
  StyleSpecification,
} from "maplibre-gl";

const C = {
  land: "#f1f1ee",
  urban: "#e9e9e5",
  park: "#cfe8c4",
  wood: "#c4e1b6",
  grass: "#d6ecca",
  sport: "#d3ebcc",
  cemetery: "#dbe7d3",
  hospital: "#fbe4e4",
  school: "#eeede7",
  industrial: "#e6e6e3",
  sand: "#f4ecd5",
  water: "#a8d3f3",
  waterLine: "#a8d3f3",
  waterLabel: "#4f86c6",
  building: "#e2e1dd",
  buildingLine: "#cfcdc7",
  road: "#ffffff",
  roadCasing: "#c9cdd2",
  minorCasing: "#d5d8dc",
  highway: "#fcdc7c",
  highwayCasing: "#e6b84e",
  highwayLow: "#f8cf63",
  path: "#c8ccd1",
  rail: "#bcc1c7",
  aeroway: "#e6e6e4",
  boundary: "#a4a9af",
  label: "#5f6368",
  labelStrong: "#202124",
  labelPlace: "#3c4043",
  labelMuted: "#80868b",
  halo: "#ffffff",
};

const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];
const FONT_ITALIC = ["Noto Sans Italic"];

/** Primera capa de etiquetas: las capas propias se insertan antes para no tapar nombres. */
export const FIRST_LABEL_LAYER = "label-waterway";

const NAME: ExpressionSpecification = ["coalesce", ["get", "name:latin"], ["get", "name"]];

type Stops = number[];

function interp(stops: Stops, base = 1.5): ExpressionSpecification {
  return ["interpolate", ["exponential", base], ["zoom"], ...stops] as ExpressionSpecification;
}

/** Ancho del borde = ancho de la vía + un margen que crece con el zoom. */
function casingStops(stops: Stops): Stops {
  const out: number[] = [];
  for (let i = 0; i < stops.length; i += 2) {
    const z = stops[i];
    const w = stops[i + 1];
    out.push(z, w + (z <= 12 ? 0.8 : z <= 15 ? 1.6 : 2.6));
  }
  return out;
}

const notTunnel: ExpressionSpecification = ["!=", ["get", "brunnel"], "tunnel"];
const isTunnel: ExpressionSpecification = ["==", ["get", "brunnel"], "tunnel"];
const isLine: ExpressionSpecification = ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false];

interface RoadClass {
  id: string;
  filter: ExpressionSpecification;
  fill: string;
  casing: string;
  width: Stops;
  minzoom: number;
  /** Zoom a partir del cual se dibuja el borde. */
  casingFrom: number;
  dash?: number[];
}

// De menor a mayor: las vías principales se dibujan encima.
const ROADS: RoadClass[] = [
  {
    id: "path",
    filter: ["match", ["get", "class"], ["path", "pedestrian"], true, false],
    fill: C.path,
    casing: C.path,
    width: [15, 0.8, 18, 2],
    minzoom: 15,
    casingFrom: 99,
    dash: [2, 1.5],
  },
  {
    id: "service",
    filter: ["match", ["get", "class"], ["service", "track"], true, false],
    fill: C.road,
    casing: C.minorCasing,
    width: [14, 0.6, 16, 2.4, 18, 7],
    minzoom: 14,
    casingFrom: 15,
  },
  {
    id: "minor",
    filter: ["all", ["match", ["get", "class"], ["minor", "street", "street_limited", "busway"], true, false], ["!=", ["get", "ramp"], 1]],
    fill: C.road,
    casing: C.minorCasing,
    width: [12, 0.5, 13, 1, 14, 2.4, 16, 6, 18, 15],
    minzoom: 12,
    casingFrom: 14,
  },
  {
    id: "link",
    filter: ["all", ["==", ["get", "ramp"], 1], ["match", ["get", "class"], ["primary", "secondary", "tertiary"], true, false]],
    fill: C.road,
    casing: C.roadCasing,
    width: [13, 1, 16, 4, 18, 9],
    minzoom: 13,
    casingFrom: 13,
  },
  {
    id: "secondary",
    filter: ["all", ["match", ["get", "class"], ["secondary", "tertiary"], true, false], ["!=", ["get", "ramp"], 1]],
    fill: C.road,
    casing: C.roadCasing,
    width: [9, 0.4, 11, 0.9, 12, 1.5, 14, 4, 16, 8.5, 18, 19],
    minzoom: 9,
    casingFrom: 11,
  },
  {
    id: "primary",
    filter: ["all", ["==", ["get", "class"], "primary"], ["!=", ["get", "ramp"], 1]],
    fill: C.road,
    casing: C.roadCasing,
    width: [7, 0.4, 10, 1.2, 12, 2.2, 14, 5, 16, 10, 18, 22],
    minzoom: 7,
    casingFrom: 10,
  },
  {
    id: "motorway-link",
    filter: ["all", ["==", ["get", "ramp"], 1], ["match", ["get", "class"], ["motorway", "trunk"], true, false]],
    fill: C.highway,
    casing: C.highwayCasing,
    width: [11, 0.8, 14, 2.6, 16, 5.5, 18, 11],
    minzoom: 11,
    casingFrom: 12,
  },
  {
    id: "motorway",
    filter: ["all", ["match", ["get", "class"], ["motorway", "trunk"], true, false], ["!=", ["get", "ramp"], 1]],
    fill: C.highway,
    casing: C.highwayCasing,
    width: [5, 0.6, 8, 1.2, 10, 2, 12, 3.2, 14, 6, 16, 12, 18, 26],
    minzoom: 5,
    casingFrom: 8,
  },
];

function roadLayers(): LayerSpecification[] {
  const layers: LayerSpecification[] = [];
  const base = { type: "line", source: "openmaptiles", "source-layer": "transportation" } as const;
  const round = { "line-cap": "round", "line-join": "round" } as const;

  // Túneles: mismo trazado, atenuados y con borde discontinuo.
  for (const r of ROADS) {
    if (r.dash) continue;
    layers.push({
      ...base,
      id: `tunnel-${r.id}-casing`,
      minzoom: Math.max(r.minzoom, 13),
      filter: ["all", isLine, isTunnel, r.filter],
      paint: { "line-color": r.casing, "line-width": interp(casingStops(r.width)), "line-dasharray": [1.5, 1] },
    });
    layers.push({
      ...base,
      id: `tunnel-${r.id}`,
      minzoom: Math.max(r.minzoom, 13),
      filter: ["all", isLine, isTunnel, r.filter],
      layout: round,
      paint: { "line-color": r.fill, "line-width": interp(r.width), "line-opacity": 0.55 },
    });
  }

  // Bordes de todas las clases primero y rellenos después: los cruces quedan limpios.
  for (const r of ROADS) {
    if (r.casingFrom >= 99) continue;
    layers.push({
      ...base,
      id: `road-${r.id}-casing`,
      minzoom: Math.max(r.minzoom, r.casingFrom),
      filter: ["all", isLine, notTunnel, r.filter],
      layout: round,
      paint: { "line-color": r.casing, "line-width": interp(casingStops(r.width)) },
    });
  }
  for (const r of ROADS) {
    layers.push({
      ...base,
      id: `road-${r.id}`,
      minzoom: r.minzoom,
      filter: ["all", isLine, notTunnel, r.filter],
      layout: r.dash ? { "line-join": "round" } : round,
      paint: {
        // A zoom bajo las autopistas van algo más saturadas para leerse sin borde.
        "line-color": r.id === "motorway" ? ["interpolate", ["linear"], ["zoom"], 7, C.highwayLow, 10, C.highway] : r.fill,
        "line-width": interp(r.width),
        ...(r.dash ? { "line-dasharray": r.dash } : {}),
      },
    });
  }

  // Ferrocarril: línea gris con traviesas blancas al acercarse.
  const rail: ExpressionSpecification = ["all", isLine, notTunnel, ["match", ["get", "class"], ["rail", "transit"], true, false]];
  layers.push({
    ...base,
    id: "rail",
    minzoom: 10,
    filter: rail,
    paint: { "line-color": C.rail, "line-width": interp([10, 0.5, 14, 1.4, 18, 3]) },
  });
  layers.push({
    ...base,
    id: "rail-hatch",
    minzoom: 14,
    filter: rail,
    paint: { "line-color": "#ffffff", "line-width": interp([14, 0.6, 18, 1.6]), "line-dasharray": [2, 2.5] },
  });
  return layers;
}

function fill(id: string, sourceLayer: string, color: string, filter?: ExpressionSpecification, minzoom?: number): LayerSpecification {
  return {
    id,
    type: "fill",
    source: "openmaptiles",
    "source-layer": sourceLayer,
    ...(filter ? { filter } : {}),
    ...(minzoom != null ? { minzoom } : {}),
    paint: { "fill-color": color, "fill-antialias": true },
  } as LayerSpecification;
}

const classIs = (...v: string[]): ExpressionSpecification => ["match", ["get", "class"], v, true, false];

function labelPaint(color: string, halo = 1.6) {
  return { "text-color": color, "text-halo-color": C.halo, "text-halo-width": halo, "text-halo-blur": 0.3 };
}

/** Color del nombre de un POI según su categoría, como en Google Maps. */
const POI_COLOR: DataDrivenPropertyValueSpecification<string> = [
  "match",
  ["get", "class"],
  ["hospital", "doctors", "dentist", "pharmacy"],
  "#c5221f",
  ["park", "garden", "playground", "pitch", "golf", "campsite", "picnic_site", "dog_park"],
  "#3b7d3e",
  ["railway", "bus", "airport", "ferry_terminal", "harbor"],
  "#1a73e8",
  ["school", "college", "library", "museum", "art_gallery", "town_hall", "place_of_worship", "castle", "monument", "attraction"],
  "#5f6368",
  "#70757a",
];

function labelLayers(): LayerSpecification[] {
  const sym = { type: "symbol", source: "openmaptiles" } as const;
  return [
    {
      ...sym,
      id: FIRST_LABEL_LAYER,
      "source-layer": "waterway",
      minzoom: 13,
      filter: isLine,
      layout: { "symbol-placement": "line", "text-field": NAME, "text-font": FONT_ITALIC, "text-size": 11, "text-letter-spacing": 0.05 },
      paint: labelPaint(C.waterLabel, 1.2),
    },
    {
      ...sym,
      id: "label-water",
      "source-layer": "water_name",
      layout: { "text-field": NAME, "text-font": FONT_ITALIC, "text-size": interp([8, 11, 14, 13], 1.2), "text-max-width": 8, "text-letter-spacing": 0.05 },
      paint: labelPaint(C.waterLabel, 1.2),
    },
    {
      ...sym,
      id: "label-road-minor",
      "source-layer": "transportation_name",
      minzoom: 15,
      filter: ["all", isLine, classIs("minor", "street", "street_limited", "service", "track", "busway")],
      layout: {
        "symbol-placement": "line",
        "text-field": NAME,
        "text-font": FONT,
        "text-size": interp([15, 10, 18, 12.5], 1.2),
        "text-max-angle": 30,
        "text-padding": 2,
      },
      paint: labelPaint(C.label),
    },
    {
      ...sym,
      id: "label-road-major",
      "source-layer": "transportation_name",
      minzoom: 12.5,
      filter: ["all", isLine, classIs("primary", "secondary", "tertiary", "trunk", "motorway")],
      layout: {
        "symbol-placement": "line",
        "text-field": NAME,
        "text-font": FONT,
        "text-size": interp([13, 10.5, 16, 12, 18, 13.5], 1.2),
        "text-max-angle": 30,
        "text-padding": 2,
      },
      paint: labelPaint(C.labelPlace),
    },
    {
      ...sym,
      id: "label-road-shield",
      "source-layer": "transportation_name",
      minzoom: 9,
      filter: ["all", isLine, ["<=", ["get", "ref_length"], 6], classIs("motorway", "trunk", "primary")],
      layout: {
        "icon-image": ["concat", "road_", ["get", "ref_length"]],
        "icon-rotation-alignment": "viewport",
        "symbol-placement": ["step", ["zoom"], "point", 11, "line"],
        "symbol-spacing": 320,
        "text-field": ["to-string", ["get", "ref"]],
        "text-font": FONT,
        "text-size": 9.5,
        "text-rotation-alignment": "viewport",
      },
      paint: { "text-color": C.labelPlace },
    },
    {
      ...sym,
      id: "label-poi",
      "source-layer": "poi",
      minzoom: 16,
      filter: ["all", ["match", ["geometry-type"], ["Point", "MultiPoint"], true, false], ["<=", ["get", "rank"], 14], ["!=", ["get", "class"], "bus"]],
      layout: {
        "icon-image": ["get", "class"],
        "icon-size": 0.8,
        "text-field": NAME,
        "text-font": FONT,
        "text-size": 11,
        "text-anchor": "left",
        "text-offset": [0.9, 0],
        "text-max-width": 9,
        "text-optional": true,
        "symbol-sort-key": ["get", "rank"],
      },
      paint: { ...labelPaint("#70757a", 1.4), "text-color": POI_COLOR, "icon-opacity": 0.75 },
    },
    {
      ...sym,
      id: "label-airport",
      "source-layer": "aerodrome_label",
      minzoom: 10,
      layout: { "icon-image": "airport_11", "text-field": NAME, "text-font": FONT, "text-size": 11, "text-anchor": "top", "text-offset": [0, 0.8] },
      paint: labelPaint("#1a73e8"),
    },
    {
      ...sym,
      id: "label-hamlet",
      "source-layer": "place",
      minzoom: 13,
      filter: classIs("hamlet", "isolated_dwelling", "locality", "farm"),
      layout: { "text-field": NAME, "text-font": FONT, "text-size": 10.5, "text-max-width": 8 },
      paint: labelPaint(C.labelMuted),
    },
    {
      ...sym,
      id: "label-neighbourhood",
      "source-layer": "place",
      minzoom: 12,
      filter: classIs("suburb", "quarter", "neighbourhood"),
      layout: { "text-field": NAME, "text-font": FONT, "text-size": interp([12, 10.5, 16, 12.5], 1.2), "text-max-width": 8, "text-letter-spacing": 0.03 },
      paint: labelPaint("#70757a"),
    },
    {
      ...sym,
      id: "label-village",
      "source-layer": "place",
      minzoom: 10,
      filter: classIs("village"),
      layout: { "text-field": NAME, "text-font": FONT, "text-size": interp([10, 11, 14, 13.5], 1.2), "text-max-width": 8 },
      paint: labelPaint(C.labelPlace),
    },
    {
      ...sym,
      id: "label-town",
      "source-layer": "place",
      minzoom: 7,
      maxzoom: 16,
      filter: classIs("town"),
      layout: { "text-field": NAME, "text-font": FONT, "text-size": interp([7, 11, 11, 14, 14, 17], 1.2), "text-max-width": 8 },
      paint: labelPaint(C.labelPlace, 1.8),
    },
    {
      ...sym,
      id: "label-city",
      "source-layer": "place",
      minzoom: 4,
      maxzoom: 15,
      filter: classIs("city"),
      layout: { "text-field": NAME, "text-font": FONT, "text-size": interp([4, 11, 8, 14, 12, 19], 1.2), "text-max-width": 8 },
      paint: labelPaint(C.labelStrong, 2),
    },
    {
      ...sym,
      id: "label-state",
      "source-layer": "place",
      minzoom: 5,
      maxzoom: 9,
      filter: classIs("state"),
      layout: { "text-field": NAME, "text-font": FONT, "text-size": 11, "text-transform": "uppercase", "text-letter-spacing": 0.1 },
      paint: labelPaint(C.labelMuted),
    },
    {
      ...sym,
      id: "label-country",
      "source-layer": "place",
      maxzoom: 7,
      filter: classIs("country"),
      layout: { "text-field": NAME, "text-font": FONT_BOLD, "text-size": interp([2, 11, 6, 15], 1.2), "text-max-width": 7 },
      paint: labelPaint(C.labelPlace, 1.8),
    },
  ] as LayerSpecification[];
}

export function buildSentinelStyle(): StyleSpecification {
  const layers: LayerSpecification[] = [
    { id: "background", type: "background", paint: { "background-color": C.land } },
    fill("landuse-urban", "landuse", C.urban, classIs("residential", "commercial", "retail")),
    fill("landuse-industrial", "landuse", C.industrial, classIs("industrial", "railway", "garages")),
    fill("landcover-wood", "landcover", C.wood, classIs("wood", "forest")),
    fill("landcover-grass", "landcover", C.grass, classIs("grass", "farmland", "wetland")),
    fill("landcover-sand", "landcover", C.sand, classIs("sand", "beach")),
    fill("park", "park", C.park),
    fill("landuse-sport", "landuse", C.sport, classIs("pitch", "stadium", "playground", "track")),
    fill("landuse-cemetery", "landuse", C.cemetery, classIs("cemetery")),
    fill("landuse-hospital", "landuse", C.hospital, classIs("hospital")),
    fill("landuse-school", "landuse", C.school, classIs("school", "college", "university", "kindergarten")),
    {
      id: "waterway",
      type: "line",
      source: "openmaptiles",
      "source-layer": "waterway",
      filter: notTunnel,
      layout: { "line-cap": "round" },
      paint: {
        "line-color": C.waterLine,
        // Ríos más anchos que arroyos y canales (el `match` va dentro de cada parada).
        "line-width": [
          "interpolate",
          ["exponential", 1.5],
          ["zoom"],
          8, ["match", ["get", "class"], "river", 0.8, 0],
          12, ["match", ["get", "class"], "river", 1.8, 0.5],
          14, ["match", ["get", "class"], "river", 3, 1],
          18, ["match", ["get", "class"], "river", 8, 3],
        ],
      },
    },
    fill("water", "water", C.water, notTunnel),
    fill("aeroway", "aeroway", C.aeroway, ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false], 11),
    {
      id: "aeroway-runway",
      type: "line",
      source: "openmaptiles",
      "source-layer": "aeroway",
      minzoom: 11,
      filter: ["all", isLine, classIs("runway", "taxiway")],
      paint: { "line-color": "#ffffff", "line-width": interp([11, 2, 14, 10, 18, 40]) },
    },
    {
      id: "building",
      type: "fill",
      source: "openmaptiles",
      "source-layer": "building",
      minzoom: 15,
      paint: {
        "fill-color": C.building,
        "fill-outline-color": C.buildingLine,
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 15, 0, 16, 1],
      },
    },
    ...roadLayers(),
    {
      id: "boundary",
      type: "line",
      source: "openmaptiles",
      "source-layer": "boundary",
      filter: ["all", ["<=", ["get", "admin_level"], 6], ["!=", ["get", "maritime"], 1]],
      paint: { "line-color": C.boundary, "line-width": interp([4, 0.6, 10, 1, 14, 1.4]), "line-dasharray": [3, 2] },
    },
    ...labelLayers(),
  ];

  return {
    version: 8,
    name: "Sentinel",
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sprite: "https://tiles.openfreemap.org/sprites/ofm_f384/ofm",
    sources: {
      openmaptiles: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
    },
    layers,
  };
}
