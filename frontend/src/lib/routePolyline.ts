/**
 * Utilidades de geometría sobre polilíneas — cliente-side.
 *
 * Réplica TypeScript de `simulation/app/route_nav.py`. El dashboard las
 * usa para dibujar el tramo restante de la ruta OSRM (ambulancia ya no
 * pinta el recorrido hecho, solo lo que queda) sin round-trip al backend.
 */

const EARTH_R_M = 6_371_000;

/** Distancia en metros entre dos coordenadas (fórmula de Haversine). */
export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dphi = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_R_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

function polylineLengthM(coords: [number, number][]): number {
  if (coords.length < 2) return 0;
  let s = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    const a = coords[i];
    const b = coords[i + 1];
    s += haversineM(a[0], a[1], b[0], b[1]);
  }
  return s;
}

/** Punto `[lat, lon]` sobre la polilínea a `distM` metros desde el inicio. */
export function pointAtDistanceM(coords: [number, number][], distM: number): [number, number] {
  if (!coords.length) return [0, 0];
  if (coords.length === 1) return [coords[0][0], coords[0][1]];
  if (distM <= 0) return [coords[0][0], coords[0][1]];
  let cum = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    const a = coords[i];
    const b = coords[i + 1];
    const seg = haversineM(a[0], a[1], b[0], b[1]);
    if (cum + seg >= distM - 1e-9) {
      const t = seg > 1e-9 ? (distM - cum) / seg : 1;
      const tt = Math.min(1, Math.max(0, t));
      const lat = a[0] + tt * (b[0] - a[0]);
      const lon = a[1] + tt * (b[1] - a[1]);
      return [lat, lon];
    }
    cum += seg;
  }
  const last = coords[coords.length - 1];
  return [last[0], last[1]];
}

/**
 * Polilínea desde la posición actual (progressM metros desde el inicio) hasta el final.
 */
export function remainingRouteCoords(coords: [number, number][], progressM: number): [number, number][] {
  if (!coords.length || coords.length < 2) return coords;
  const total = polylineLengthM(coords);
  if (progressM <= 0) return [...coords];
  if (progressM >= total - 0.5) {
    const last = coords[coords.length - 1];
    return [[last[0], last[1]]];
  }
  const head = pointAtDistanceM(coords, progressM);
  const out: [number, number][] = [head];
  let cum = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    const a = coords[i];
    const b = coords[i + 1];
    const seg = haversineM(a[0], a[1], b[0], b[1]);
    if (cum + seg >= progressM - 1e-9) {
      for (let j = i + 1; j < coords.length; j++) {
        out.push([coords[j][0], coords[j][1]]);
      }
      break;
    }
    cum += seg;
  }
  return out;
}
