"""Colocación realista de elementos generados sobre la red viaria.

Todo lo que el simulador inventa (emergencias, unidades, incidencias,
cortes de tráfico) pasa por aquí para que caiga donde puede ocurrir:

- Los puntos se muestrean alrededor del centro urbano con una normal
  (más actividad en el centro, menos en la periferia) y se ajustan a la
  calle más cercana con OSRM ``/nearest``. Si la calle más próxima está
  lejos o no tiene nombre (pistas forestales, monte), se descarta y se
  vuelve a muestrear.
- Un corte de tráfico es un tramo de calle real: se traza la ruta entre
  dos puntos de la misma vía y se ensancha unos metros a cada lado, de
  modo que el polígono sigue la calle en lugar de ser un cuadrado.

Si OSRM no responde, las funciones devuelven el punto sin ajustar (o
``None`` cuando se pide un corte), para que la simulación no se detenga.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

import httpx

from .routing import fetch_route, snap_nearest

_M_PER_DEG_LAT = 111_320.0


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / _M_PER_DEG_LAT
    dlon = east_m / (_M_PER_DEG_LAT * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dn = (b[0] - a[0]) * _M_PER_DEG_LAT
    de = (b[1] - a[1]) * _M_PER_DEG_LAT * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dn, de)


@dataclass
class RoadPoint:
    lat: float
    lon: float
    street: str
    snapped: bool


async def snap_to_road(
    http: httpx.AsyncClient | None,
    lat: float,
    lon: float,
    *,
    max_snap_m: float = 80.0,
    require_named: bool = False,
) -> RoadPoint | None:
    """Ajusta un punto a la calle más cercana.

    ``None`` si no hay calle a menos de ``max_snap_m`` (o sin nombre cuando
    ``require_named``). Si OSRM no responde, devuelve el punto tal cual.
    """
    cands = await snap_nearest(http, lat, lon, number=3)
    if not cands:
        return RoadPoint(lat, lon, "", snapped=False) if http is None else None
    for c in cands:
        if c["distance_m"] > max_snap_m:
            continue
        if require_named and not c["name"].strip():
            continue
        return RoadPoint(c["lat"], c["lon"], c["name"], snapped=True)
    return None


async def random_road_point(
    http: httpx.AsyncClient | None,
    center: tuple[float, float],
    sigma_m: float,
    *,
    rng: random.Random | None = None,
    require_named: bool = True,
    max_snap_m: float = 45.0,
    max_radius_m: float | None = None,
    tries: int = 30,
) -> RoadPoint:
    """Punto aleatorio sobre una calle real, más probable cerca del centro.

    Muestrea con una normal de desviación ``sigma_m`` alrededor de
    ``center`` (recortada a ``max_radius_m``) y acepta el primer punto cuya
    calle más cercana esté a menos de ``max_snap_m`` y tenga nombre. Tras
    ``tries`` intentos fallidos usa la calle más cercana al centro.
    """
    rng = rng or random
    limit = max_radius_m or sigma_m * 2.5
    for _ in range(tries):
        north = rng.gauss(0.0, sigma_m)
        east = rng.gauss(0.0, sigma_m)
        if math.hypot(north, east) > limit:
            continue
        lat, lon = _offset(center[0], center[1], north, east)
        p = await snap_to_road(http, lat, lon, max_snap_m=max_snap_m, require_named=require_named)
        if p is not None:
            return p
        if http is None:
            break
    p = await snap_to_road(http, center[0], center[1], max_snap_m=2000.0)
    return p or RoadPoint(center[0], center[1], "", snapped=False)


def _buffer_polyline(line: Sequence[tuple[float, float]], half_width_m: float) -> list[tuple[float, float]]:
    """Ensancha una polilínea ``half_width_m`` a cada lado → polígono cerrado (lat, lon)."""
    if len(line) < 2:
        return []
    lat0 = line[0][0]
    kx = _M_PER_DEG_LAT * math.cos(math.radians(lat0))
    ky = _M_PER_DEG_LAT
    pts = [((lon - line[0][1]) * kx, (lat - lat0) * ky) for lat, lon in line]
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(pts):
        a = pts[max(0, i - 1)]
        b = pts[min(len(pts) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        left.append((x + nx * half_width_m, y + ny * half_width_m))
        right.append((x - nx * half_width_m, y - ny * half_width_m))
    ring = left + right[::-1]
    return [(lat0 + y / ky, line[0][1] + x / kx) for x, y in ring]


def _truncate(line: Sequence[tuple[float, float]], length_m: float) -> list[tuple[float, float]]:
    out = [line[0]]
    acc = 0.0
    for a, b in zip(line, line[1:]):
        d = _dist_m(a, b)
        if acc + d >= length_m:
            t = (length_m - acc) / d if d > 0 else 0.0
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        acc += d
        out.append(b)
    return out


@dataclass
class RoadSegment:
    polygon: list[tuple[float, float]]
    line: list[tuple[float, float]]
    street: str
    center: tuple[float, float]


async def road_segment(
    http: httpx.AsyncClient | None,
    lat: float,
    lon: float,
    *,
    length_m: float = 220.0,
    half_width_m: float = 12.0,
    rng: random.Random | None = None,
) -> RoadSegment | None:
    """Tramo de la calle más cercana a (lat, lon), como polígono que la sigue.

    Prueba varias direcciones y se queda con la primera cuya ruta sigue la
    misma vía (sin rodeos de más del doble de la distancia). ``None`` si no
    hay calle cerca o OSRM no responde.
    """
    if http is None:
        return None
    rng = rng or random
    start = await snap_to_road(http, lat, lon, max_snap_m=150.0)
    if start is None:
        return None
    bearings = [rng.uniform(0, 360)]
    bearings += [(bearings[0] + k * 60) % 360 for k in range(1, 6)]
    for bearing in bearings:
        north = math.cos(math.radians(bearing)) * length_m
        east = math.sin(math.radians(bearing)) * length_m
        tlat, tlon = _offset(start.lat, start.lon, north, east)
        target = await snap_to_road(http, tlat, tlon, max_snap_m=60.0)
        if target is None:
            continue
        if start.street and target.street and target.street != start.street:
            continue
        route, _, _ = await fetch_route(http, [(start.lat, start.lon), (target.lat, target.lon)])
        if len(route) < 2:
            continue
        total = sum(_dist_m(a, b) for a, b in zip(route, route[1:]))
        if total < length_m * 0.4 or total > length_m * 2.2:
            continue
        line = _truncate([(float(a), float(b)) for a, b in route], length_m)
        poly = _buffer_polyline(line, half_width_m)
        mid = line[len(line) // 2]
        return RoadSegment(polygon=poly, line=line, street=start.street, center=mid)
    return None
