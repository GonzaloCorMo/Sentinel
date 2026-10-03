"""Geometría esférica y avance por polilíneas (Haversine).

Funciones puras, sin estado. Las usa el motor para calcular distancias
reales entre puntos, avanzar la ambulancia ``delta_m`` metros a lo largo
de la ruta OSRM y deducir heading.

La versión TypeScript equivalente vive en
`frontend/src/lib/routePolyline.ts` y se mantiene alineada.
"""
from __future__ import annotations

import math
from typing import Sequence

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos coordenadas (radio medio terrestre)."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def polyline_length_m(coords: Sequence[tuple[float, float]]) -> float:
    """Longitud total acumulada de la polilínea en metros."""
    if len(coords) < 2:
        return 0.0
    s = 0.0
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        s += haversine_m(a[0], a[1], b[0], b[1])
    return s


def point_at_distance_m(
    coords: Sequence[tuple[float, float]], dist_m: float
) -> tuple[float, float]:
    """Punto sobre la polilínea a `dist_m` metros desde el inicio."""
    if not coords:
        return 0.0, 0.0
    if len(coords) == 1:
        return coords[0][0], coords[0][1]
    if dist_m <= 0:
        return coords[0][0], coords[0][1]
    cum = 0.0
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        seg = haversine_m(a[0], a[1], b[0], b[1])
        if cum + seg >= dist_m - 1e-9:
            t = (dist_m - cum) / seg if seg > 1e-9 else 1.0
            t = min(1.0, max(0.0, t))
            lat = a[0] + t * (b[0] - a[0])
            lon = a[1] + t * (b[1] - a[1])
            return lat, lon
        cum += seg
    return coords[-1][0], coords[-1][1]


def advance_along_polyline(
    coords: Sequence[tuple[float, float]],
    progress_m: float,
    delta_m: float,
) -> tuple[float, float, float, bool]:
    """
    Avanza `delta_m` desde `progress_m` (metros desde el inicio de la polilínea).
    Devuelve (lat, lon, nuevo_progress_m, finished).
    """
    if len(coords) < 2:
        return coords[0][0], coords[0][1], progress_m, True
    total = polyline_length_m(coords)
    target = progress_m + max(0.0, delta_m)
    if target >= total - 0.5:
        return coords[-1][0], coords[-1][1], total, True
    lat, lon = point_at_distance_m(coords, target)
    return lat, lon, target, False


def remaining_route_coords(
    coords: Sequence[tuple[float, float]], progress_m: float
) -> list[tuple[float, float]]:
    """Tramo de ruta que queda por recorrer desde `progress_m` (m desde el inicio)."""
    if len(coords) < 2:
        return list(coords)
    total = polyline_length_m(coords)
    if progress_m <= 0:
        return list(coords)
    if progress_m >= total - 0.5:
        last = coords[-1]
        return [last]
    head = point_at_distance_m(coords, progress_m)
    out: list[tuple[float, float]] = [head]
    cum = 0.0
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        seg = haversine_m(a[0], a[1], b[0], b[1])
        if cum + seg >= progress_m - 1e-9:
            for j in range(i + 1, len(coords)):
                out.append(coords[j])
            break
        cum += seg
    return out


def heading_deg_towards(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float
) -> float:
    """Rumbo inicial (bearing) en grados 0-360 desde A hacia B."""
    dlon = math.radians(to_lon - from_lon)
    lat1 = math.radians(from_lat)
    lat2 = math.radians(to_lat)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
