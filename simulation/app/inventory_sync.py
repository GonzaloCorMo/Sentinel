from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .regions import get_active_region_id
from .supabase_client import get_supabase

log = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArubaConfig:
    base_url: str
    api_key: str | None
    interval_sec: float
    enabled: bool


def load_aruba_config() -> ArubaConfig:
    base_url = (os.environ.get("ARUBA_API_BASE_URL") or "http://10.10.48.30:8080").strip().rstrip("/")
    api_key_raw = (os.environ.get("ARUBA_API_KEY") or "").strip()
    api_key = api_key_raw or None
    interval_sec_raw = (os.environ.get("ARUBA_SYNC_INTERVAL_SEC") or "60").strip()
    try:
        interval_sec = max(5.0, float(interval_sec_raw))
    except Exception:
        interval_sec = 60.0
    enabled = bool(base_url)
    return ArubaConfig(
        base_url=base_url,
        api_key=api_key,
        interval_sec=interval_sec,
        enabled=enabled,
    )


def _map_external_poi_kind(poi_type: str, known_place_ids: set[str]) -> str | None:
    t = (poi_type or "").strip().lower()
    if t in {"hospital", "clinic", "medical", "health_center"}:
        return "hospital"
    if t in {"fuel", "gas_station", "gas", "service_station"}:
        return "gas_station"
    # Solo aceptar mapeos directos al dominio del proyecto.
    if t in {"hospital", "gas_station"} and t in known_place_ids:
        return t
    return None


def _is_external_jam_type(poi_type: str) -> bool:
    t = (poi_type or "").strip().lower()
    return t in {
        "jam",
        "traffic_jam",
        "traffic",
        "congestion",
        "road_block",
        "roadblock",
        "closure",
        "accident",
    }


def _square_polygon_around(lat: float, lon: float, half_side_m: float) -> list[list[float]]:
    dlat = half_side_m / 111320.0
    dlon = half_side_m / (111320.0 * max(0.25, abs(math.cos(math.radians(lat)))))
    return [
        [lat - dlat, lon - dlon],
        [lat - dlat, lon + dlon],
        [lat + dlat, lon + dlon],
        [lat + dlat, lon - dlon],
    ]


async def _fetch_paged(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    page_limit: int,
    item_limit_per_page: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"limit": item_limit_per_page, "offset": offset}
        r = await client.get(url, headers=headers, params=params, timeout=12.0)
        r.raise_for_status()
        body = r.json() or {}
        items = body.get("items") or []
        if not isinstance(items, list):
            break
        for row in items:
            if isinstance(row, dict):
                out.append(row)
        has_more = bool(body.get("has_more"))
        if not has_more:
            break
        offset += item_limit_per_page
        if offset >= page_limit:
            break
    return out


async def sync_aruba_inventory(engine: Any, *, cfg: ArubaConfig) -> dict[str, Any]:
    """Fetch Aruba inventory and merge in-memory engine state.

    Aruba-only sync: when active region is not aruba, it returns a skipped status.
    """
    region_id = get_active_region_id()
    region_id_norm = str(region_id or "").strip().lower()
    is_aruba_region = region_id_norm == "aruba" or "aruba" in region_id_norm
    if not is_aruba_region:
        return {
            "ok": True,
            "enabled": True,
            "status": "idle_non_aruba_region",
            "regionId": region_id,
            "fetchedPois": 0,
            "fetchedRoads": 0,
            "updatedPois": 0,
            "updatedRoads": 0,
            "startedAt": _iso_now(),
            "finishedAt": _iso_now(),
        }
    if not cfg.enabled:
        return {
            "ok": False,
            "enabled": False,
            "status": "missing_base_url",
            "regionId": region_id,
            "fetchedPois": 0,
            "fetchedRoads": 0,
            "updatedPois": 0,
            "updatedRoads": 0,
            "startedAt": _iso_now(),
            "finishedAt": _iso_now(),
        }

    started = _iso_now()
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["X-API-Key"] = cfg.api_key
    pois: list[dict[str, Any]] = []
    roads: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        # light retries with backoff
        for attempt in range(3):
            try:
                pois = await _fetch_paged(
                    client,
                    f"{cfg.base_url}/api/v1/pois",
                    headers=headers,
                    page_limit=5000,
                    item_limit_per_page=50,
                )
                roads = await _fetch_paged(
                    client,
                    f"{cfg.base_url}/api/v1/roads",
                    headers=headers,
                    page_limit=5000,
                    item_limit_per_page=20,
                )
                break
            except Exception:
                if attempt >= 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))

    place_ids = {
        str(t.get("id"))
        for t in engine.get_entity_types()
        if str(t.get("kind")) == "place"
    }
    normalized_pois: list[dict[str, Any]] = []
    normalized_jams: list[dict[str, Any]] = []
    for row in pois:
        try:
            ext_id = str(row.get("id") or "").strip()
            if not ext_id:
                continue
            lat = float(row.get("latitude"))
            lon = float(row.get("longitude"))
            poi_type = str(row.get("type") or "")
            if _is_external_jam_type(poi_type):
                normalized_jams.append(
                    {
                        "id": f"aruba-jam-{ext_id}",
                        "polygon": _square_polygon_around(lat, lon, 45.0),
                        "name": str(row.get("name") or "Atasco"),
                        "source": "aruba_api",
                    }
                )
                continue
            mapped_kind = _map_external_poi_kind(poi_type, place_ids)
            if mapped_kind is None:
                continue
            normalized_pois.append(
                {
                    "externalId": ext_id,
                    "kind": mapped_kind,
                    "name": str(row.get("name") or "POI Aruba"),
                    "latitude": lat,
                    "longitude": lon,
                    "source": "aruba_api",
                    "rawType": row.get("type"),
                    "address": row.get("address"),
                    "capacity": row.get("capacity"),
                }
            )
        except Exception:
            continue

    normalized_roads: list[dict[str, Any]] = []
    for row in roads:
        try:
            ext_id = str(row.get("id") or "").strip()
            if not ext_id:
                continue
            start_lat = float(row.get("start_lat"))
            start_lon = float(row.get("start_lon"))
            end_lat = float(row.get("end_lat"))
            end_lon = float(row.get("end_lon"))
            speed_limit = row.get("speed_limit_kmh")
            speed_limit_kmh = float(speed_limit) if speed_limit is not None else None
            lanes = row.get("lanes")
            lanes_i = int(lanes) if lanes is not None else None
            geometry = row.get("geometry")
            normalized_roads.append(
                {
                    "externalId": ext_id,
                    "name": str(row.get("name") or "Road segment"),
                    "roadType": str(row.get("road_type") or "unknown"),
                    "startLat": start_lat,
                    "startLon": start_lon,
                    "endLat": end_lat,
                    "endLon": end_lon,
                    "speedLimitKmh": speed_limit_kmh,
                    "lanes": lanes_i,
                    "lengthM": float(row.get("length_m") or 0.0),
                    "geometry": geometry if isinstance(geometry, list) else None,
                    "source": "aruba_api",
                }
            )
        except Exception:
            continue

    updated_pois, updated_roads = await engine.apply_aruba_inventory_sync(
        normalized_pois=normalized_pois,
        normalized_roads=normalized_roads,
        normalized_jams=normalized_jams,
    )

    finished = _iso_now()
    summary = {
        "ok": True,
        "enabled": True,
        "status": "synced",
        "regionId": region_id,
        "fetchedPois": len(pois),
        "fetchedRoads": len(roads),
        "updatedPois": updated_pois,
        "updatedRoads": updated_roads,
        "startedAt": started,
        "finishedAt": finished,
    }
    _persist_sync_snapshot(summary, normalized_pois, normalized_roads)
    return summary


def _persist_sync_snapshot(
    summary: dict[str, Any],
    pois: list[dict[str, Any]],
    roads: list[dict[str, Any]],
) -> None:
    sb = get_supabase()
    if sb is None:
        return
    try:
        sb.table("inventory_sync_events").insert(
            {
                "region_id": summary.get("regionId"),
                "status": summary.get("status"),
                "ok": bool(summary.get("ok")),
                "fetched_pois": int(summary.get("fetchedPois") or 0),
                "fetched_roads": int(summary.get("fetchedRoads") or 0),
                "updated_pois": int(summary.get("updatedPois") or 0),
                "updated_roads": int(summary.get("updatedRoads") or 0),
                "started_at": summary.get("startedAt"),
                "finished_at": summary.get("finishedAt"),
            }
        ).execute()
    except Exception:
        log.exception("No se pudo persistir inventory_sync_events")

    try:
        poi_rows = []
        for p in pois:
            poi_rows.append(
                {
                    "region_id": "aruba",
                    "external_id": p.get("externalId"),
                    "kind": p.get("kind"),
                    "name": p.get("name"),
                    "latitude": p.get("latitude"),
                    "longitude": p.get("longitude"),
                    "source": "aruba_api",
                    "raw": p,
                    "updated_at": _iso_now(),
                }
            )
        if poi_rows:
            sb.table("external_pois_inventory").upsert(
                poi_rows,
                on_conflict="region_id,external_id",
            ).execute()
    except Exception:
        log.exception("No se pudo persistir external_pois_inventory")

    try:
        road_rows = []
        for r in roads:
            road_rows.append(
                {
                    "region_id": "aruba",
                    "external_id": r.get("externalId"),
                    "name": r.get("name"),
                    "road_type": r.get("roadType"),
                    "start_lat": r.get("startLat"),
                    "start_lon": r.get("startLon"),
                    "end_lat": r.get("endLat"),
                    "end_lon": r.get("endLon"),
                    "speed_limit_kmh": r.get("speedLimitKmh"),
                    "lanes": r.get("lanes"),
                    "length_m": r.get("lengthM"),
                    "geometry": r.get("geometry"),
                    "source": "aruba_api",
                    "raw": r,
                    "updated_at": _iso_now(),
                }
            )
        if road_rows:
            sb.table("external_roads_inventory").upsert(
                road_rows,
                on_conflict="region_id,external_id",
            ).execute()
    except Exception:
        log.exception("No se pudo persistir external_roads_inventory")
