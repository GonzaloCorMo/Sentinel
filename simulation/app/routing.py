"""Enrutamiento por calle con OSRM + fallback a polilínea recta.

Envolvente del servicio OSRM local (``osrm-routed`` en Docker) usado por
`engine._route_with_jam_avoidance` y por el staging. Si OSRM no responde
(arrancando o caído) se devuelve una polilínea recta entre waypoints,
calidad degradada pero simulación sigue.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Sequence

import httpx

logger = logging.getLogger(__name__)

from .regions import get_active_region

# Fallback histórico si no hay regiones configuradas (desarrollo local sin docker compose).
DEFAULT_OSRM_BASE_URL = "http://127.0.0.1:5000"
OSRM_ATTEMPTS = 2


def osrm_base_url() -> str:
    """URL base del servicio osrm-routed de la región activa.

    Permite sobrescribir con la env var legacy `OSRM_BASE_URL` (apunta a un
    OSRM externo único) — útil para entornos no-docker.
    """
    override = (os.environ.get("OSRM_BASE_URL") or "").strip()
    if override:
        return override
    region = get_active_region()
    return region.osrm_url or DEFAULT_OSRM_BASE_URL


async def probe_osrm_routing(
    client: httpx.AsyncClient, base_url: str | None = None
) -> tuple[bool, str | None]:
    """
    Comprueba si OSRM responde con una ruta válida en el extracto de la región activa.
    Devuelve (ok, mensaje_error_o_None).
    """
    base = (base_url or "").strip() or osrm_base_url()
    region = get_active_region()
    url = f"{base.rstrip('/')}/{region.probe_url_path}"
    try:
        r = await client.get(url, params={"overview": "false"}, timeout=5.0)
        r.raise_for_status()
        data = r.json()
        code = str(data.get("code") or "")
        routes = data.get("routes") or []
        if code == "Ok" and isinstance(routes, list) and len(routes) > 0:
            return True, None
        return False, code or "sin rutas en la respuesta"
    except httpx.HTTPStatusError as e:
        return False, f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        return False, type(e).__name__
    except Exception as e:
        return False, str(e)


class RouteCoords(list):
    """Polilínea ``[(lat, lon), ...]`` con datos de OSRM adjuntos.

    Es una lista normal (se serializa y se recorre igual que antes), pero
    lleva ``seg_speeds_ms``: velocidad de circulación de cada tramo entre
    puntos consecutivos según el perfil de coche de OSRM (tipo de vía,
    límite, giros). El motor la usa para mover las unidades a la velocidad
    de cada calle en lugar de a una velocidad fija.
    """

    seg_speeds_ms: list[float] | None = None
    duration_s: float | None = None


def _segment_speeds(r0: dict[str, Any], n_coords: int) -> list[float] | None:
    """Concatena `annotation.speed` de todas las piernas (uno por tramo)."""
    speeds: list[float] = []
    for leg in r0.get("legs") or []:
        ann = (leg or {}).get("annotation") or {}
        vals = ann.get("speed")
        if not isinstance(vals, list):
            return None
        speeds.extend(float(v) if isinstance(v, (int, float)) and v > 0 else 0.0 for v in vals)
    if len(speeds) != n_coords - 1:
        return None
    return speeds


def typical_limit_kmh(speed_ms: float) -> float:
    """Velocidad de tramo OSRM → límite de vía orientativo (múltiplo de 10 km/h)."""
    kmh = speed_ms * 3.6 / 0.8  # el perfil de coche circula ~80 % del límite
    return float(max(20, min(120, round(kmh / 10.0) * 10)))


def _straight_route(waypoints: Sequence[tuple[float, float]], segments_per_leg: int = 8) -> list[tuple[float, float]]:
    """Polilínea simple entre waypoints consecutivos (lat, lon)."""
    if len(waypoints) < 2:
        return list(waypoints)
    out: list[tuple[float, float]] = []
    for i in range(len(waypoints) - 1):
        a, b = waypoints[i], waypoints[i + 1]
        for s in range(segments_per_leg + 1):
            t = s / float(segments_per_leg)
            if i > 0 and s == 0:
                continue
            lat = a[0] + t * (b[0] - a[0])
            lon = a[1] + t * (b[1] - a[1])
            out.append((lat, lon))
    return out


async def fetch_route_osrm(
    client: httpx.AsyncClient,
    base_url: str,
    waypoints: Sequence[tuple[float, float]],
) -> tuple[list[tuple[float, float]] | None, float | None, float | None]:
    """
    waypoints: (lat, lon) en orden.
    Devuelve (lista de (lat, lon) o None, límite vía km/h o None, duración s o None).
    """
    if len(waypoints) < 2:
        return list(waypoints), None, None
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = f"{base_url.rstrip('/')}/route/v1/driving/{coords_str}"
    base_params: dict[str, str] = {
        "overview": "full",
        "geometries": "geojson",
        "continue_straight": "false",
    }
    param_variants = (
        {**base_params, "annotations": "speed"},
        base_params,
    )
    last_err: Exception | None = None
    for attempt in range(OSRM_ATTEMPTS):
        for params in param_variants:
            try:
                r = await client.get(url, params=params, timeout=12.0)
                r.raise_for_status()
                data = r.json()
                routes = data.get("routes") or []
                if not routes:
                    continue
                r0 = routes[0] if isinstance(routes[0], dict) else {}
                geom = r0.get("geometry") or {}
                gcoords = geom.get("coordinates") or []
                if not gcoords:
                    continue
                duration_raw = r0.get("duration")
                duration: float | None = None
                if isinstance(duration_raw, (int, float)) and duration_raw >= 0:
                    duration = float(duration_raw)
                coords = RouteCoords((float(c[1]), float(c[0])) for c in gcoords if len(c) >= 2)
                coords.seg_speeds_ms = _segment_speeds(r0, len(coords))
                coords.duration_s = duration
                lim = typical_limit_kmh(coords.seg_speeds_ms[0]) if coords.seg_speeds_ms else None
                return coords, lim, duration
            except Exception as e:
                last_err = e
        if attempt + 1 < OSRM_ATTEMPTS:
            await asyncio.sleep(0.08 * (attempt + 1))
    if last_err and logger.isEnabledFor(logging.DEBUG):
        logger.debug("OSRM route failed after retries", exc_info=last_err)
    else:
        logger.warning("OSRM route failed: %s", last_err)
    return None, None, None


async def fetch_route(
    client: httpx.AsyncClient | None,
    waypoints: Sequence[tuple[float, float]],
) -> tuple[list[tuple[float, float]], float | None, float | None]:
    """
    Usa OSRM (URL en OSRM_BASE_URL o `http://127.0.0.1:5000` por defecto) con reintentos.
    Devuelve (coords, road_speed_limit_kmh, duration_s). Si OSRM falla,
    polilínea recta y duración/límite desconocidos (None).
    """
    if len(waypoints) < 2:
        return list(waypoints), None, None
    base = osrm_base_url()
    if client:
        got, lim, duration = await fetch_route_osrm(client, base, waypoints)
        if got and len(got) >= 2:
            return got, lim, duration
    logger.warning(
        "Ruta: fallback a polilínea recta (OSRM no respondió o cliente HTTP ausente); base=%s",
        base,
    )
    return _straight_route(waypoints), None, None


def detour_point_from_jam_centroid(
    jam_polygon: Sequence[tuple[float, float]], offset_lat: float = 0.0025
) -> tuple[float, float]:
    """Punto de paso para desviar (al norte del centro del polígono, ~200–300 m)."""
    if not jam_polygon:
        region = get_active_region()
        return (region.center_lat, region.center_lon)
    n = len(jam_polygon)
    clat = sum(p[0] for p in jam_polygon) / n
    clon = sum(p[1] for p in jam_polygon) / n
    return (clat + offset_lat, clon)


def detour_candidates_from_jam(
    jam_polygon: Sequence[tuple[float, float]],
    *,
    pad_deg: float = 0.0022,
) -> list[tuple[float, float]]:
    """
    Varios waypoints fuera del bounding box del atasco (~250 m más allá del borde).
    OSRM elige el trayecto; si uno falla, se prueba el siguiente.
    """
    if len(jam_polygon) < 3:
        return [detour_point_from_jam_centroid(jam_polygon)]
    lats = [float(p[0]) for p in jam_polygon]
    lons = [float(p[1]) for p in jam_polygon]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    mid_lat = (min_lat + max_lat) / 2.0
    mid_lon = (min_lon + max_lon) / 2.0
    p = pad_deg
    n = len(jam_polygon)
    clat = sum(lats) / n
    clon = sum(lons) / n
    return [
        (clat + 0.0025, clon),
        (clat - 0.0025, clon),
        (max_lat + p, mid_lon),
        (min_lat - p, mid_lon),
        (mid_lat, max_lon + p),
        (mid_lat, min_lon - p),
        (max_lat + p, max_lon + p),
        (max_lat + p, min_lon - p),
        (min_lat - p, max_lon + p),
        (min_lat - p, min_lon - p),
    ]


async def snap_nearest(
    client: httpx.AsyncClient | None, lat: float, lon: float, number: int = 1,
) -> list[dict[str, Any]]:
    """Puntos de calle más cercanos (OSRM ``/nearest``).

    Devuelve ``[{"lat", "lon", "distance_m", "name"}]`` ordenado por distancia;
    lista vacía si OSRM no responde.
    """
    if client is None:
        return []
    url = f"{osrm_base_url().rstrip('/')}/nearest/v1/driving/{lon:.6f},{lat:.6f}"
    try:
        r = await client.get(url, params={"number": str(max(1, number))}, timeout=6.0)
        r.raise_for_status()
        out = []
        for w in r.json().get("waypoints") or []:
            loc = w.get("location") or []
            if len(loc) >= 2:
                out.append({
                    "lat": float(loc[1]),
                    "lon": float(loc[0]),
                    "distance_m": float(w.get("distance") or 0.0),
                    "name": str(w.get("name") or ""),
                })
        return out
    except Exception as e:
        logger.debug("OSRM nearest falló: %s", e)
        return []


async def table_durations(
    client: httpx.AsyncClient | None,
    sources: Sequence[tuple[float, float]],
    destination: tuple[float, float],
) -> list[float | None] | None:
    """Duración (s) de cada origen al destino con OSRM ``/table`` (una sola petición).

    ``None`` si OSRM no responde; ``None`` en una posición si ese par no
    tiene ruta.
    """
    if client is None or not sources:
        return None
    pts = list(sources) + [destination]
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in pts)
    url = f"{osrm_base_url().rstrip('/')}/table/v1/driving/{coords}"
    params = {
        "sources": ";".join(str(i) for i in range(len(sources))),
        "destinations": str(len(sources)),
    }
    try:
        r = await client.get(url, params=params, timeout=8.0)
        r.raise_for_status()
        rows = r.json().get("durations") or []
        return [float(row[0]) if row and isinstance(row[0], (int, float)) else None for row in rows]
    except Exception as e:
        logger.debug("OSRM table falló: %s", e)
        return None
