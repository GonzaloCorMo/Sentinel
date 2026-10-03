"""API FastAPI del motor de simulación.

Expone el estado del gemelo digital por HTTP + SSE al dashboard, a la
PWA ciudadana y al panel de vehículo. Monta también los endpoints de
control (play/pause/speed/reset), gestión de flota y tipos de entidad,
HITL/autónomo y chat RAG.

Lifespan:
    - Siembra flota default y tipos custom desde Supabase al arrancar.
    - Lanza las tareas de fondo: `engine.run_loop`, `_osrm_probe_loop`, `run_event_source`
      y `ai_engine.observe_loop`.
    - Al parar, cancela limpiamente todas y cierra el cliente HTTP.

Canales de streaming:
    - ``GET /api/sim/stream`` — telemetría v2.0 Server-Sent Events.
    - ``POST /api/chat/stream`` — RAG + LLM tokens (text/event-stream).

Ingest HTTP de contingencia:
    - ``POST /api/ingest`` — fallback si MQTT y P2P fallan; guarda el
      último payload recibido en ``_last_http_ingest``.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .ai_decision_engine import AIDecisionEngine
from .chat_service import chat_stream, get_chat_history
from .engine import SimulationEngine, default_spawn
from .knowledge_seeder import seed_knowledge_force, seed_knowledge_if_empty
from .event_source import run_event_source
from .schemas.external_events import ExternalEvent as ExternalEventIn
from .schemas.external_events import WeatherReading as WeatherReadingIn
from .weather_db import upsert_weather_reading
from .inventory_sync import load_aruba_config, sync_aruba_inventory
from .regions import get_active_region, get_active_region_id, list_regions, set_active_region
from .schemas.telemetry import telemetry_schema_json
from .supabase_client import is_supabase_available

engine = SimulationEngine()
ai_engine = AIDecisionEngine(engine)
engine.set_ai_engine(ai_engine)
_last_http_ingest: dict[str, Any] | None = None


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _format_proposal(p: dict[str, Any]) -> dict[str, Any]:
    """Serializa una propuesta IA al contrato snake→camel que espera el frontend."""
    return {
        "id": p["id"],
        "ambulanceId": p["ambulance_id"],
        "anomalyType": p["anomaly_type"],
        "anomalyDetail": p.get("anomaly_detail", {}),
        "matchedProtocolContent": p.get("matched_protocol_content"),
        "llmReasoning": p.get("llm_reasoning"),
        "llmExplanation": p.get("llm_explanation"),
        "similarity": p.get("similarity"),
        "status": p["status"],
        "staleReason": p.get("stale_reason"),
        "createdAt": p.get("created_at", ""),
    }


def _state_merged() -> dict[str, Any]:
    """Payload unificado: estado del motor + ingest HTTP + datos IA (mode, proposals, log)."""
    s = engine.get_state_payload()
    s["lastHttpIngest"] = _last_http_ingest
    s["aiMode"] = ai_engine.mode
    s["aiProposals"] = [_format_proposal(p) for p in ai_engine.get_pending()]
    s["aiLog"] = [_format_proposal(p) for p in ai_engine.get_resolved_recent()]
    return s


async def _osrm_probe_loop() -> None:
    """Actualiza `engine.osrm_routing` y registra en log cuando osrm-routed empieza a responder."""
    import logging

    log = logging.getLogger("uvicorn.error")
    prev = False
    while True:
        try:
            from .routing import osrm_base_url, probe_osrm_routing

            base = osrm_base_url()
            ok, err = await probe_osrm_routing(engine._http, base)
            engine.set_osrm_routing_status(
                ready=ok,
                base_url=base,
                checked_at=_iso(),
                last_error=None if ok else (err or "error"),
            )
            if ok and not prev:
                log.info("OSRM operativo en %s (rutas por calle disponibles).", base)
            elif not ok and prev:
                log.warning(
                    "OSRM dejó de responder en %s; rutas en fallback recto hasta reconexión (%s).",
                    base,
                    err,
                )
            prev = ok
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Sonda OSRM: fallo inesperado")
        try:
            await asyncio.sleep(2.0 if not engine.osrm_routing.get("ready") else 15.0)
        except asyncio.CancelledError:
            raise


async def _aruba_inventory_loop() -> None:
    """Periodic sync loop for Aruba inventory (POIs + roads)."""
    import logging

    loop_log = logging.getLogger("uvicorn.error")
    while True:
        cfg = load_aruba_config()
        if not cfg.enabled:
            engine.set_aruba_sync_status(
                {
                    "ok": False,
                    "enabled": False,
                    "status": "disabled_missing_base_url",
                    "fetchedPois": 0,
                    "fetchedRoads": 0,
                    "updatedPois": 0,
                    "updatedRoads": 0,
                    "lastSyncAt": _iso(),
                }
            )
            await asyncio.sleep(15.0)
            continue
        try:
            summary = await sync_aruba_inventory(engine, cfg=cfg)
            engine.set_aruba_sync_status(summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            loop_log.warning("Aruba inventory sync failed: %s", exc)
            engine.set_aruba_sync_status(
                {
                    "ok": False,
                    "enabled": True,
                    "status": f"error:{type(exc).__name__}",
                    "fetchedPois": 0,
                    "fetchedRoads": 0,
                    "updatedPois": 0,
                    "updatedRoads": 0,
                    "lastSyncAt": _iso(),
                }
            )
        await asyncio.sleep(max(5.0, float(cfg.interval_sec)))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida FastAPI: siembra fleet/tipos/knowledge y lanza tareas de fondo.

    Al arranque siembra la flota default si está vacía, carga tipos
    custom de Supabase, inicializa el RAG y arranca tres tareas de
    fondo (motor, sonda OSRM, observer IA). Al cierre las cancela
    ordenadamente y vacía el buffer de telemetría.
    """
    import logging

    log = logging.getLogger("uvicorn.error")
    if is_supabase_available():
        log.info("Supabase connection active — telemetry persistence and AI observer enabled")
    else:
        log.warning("Supabase not available — running without persistence (AI observer will skip RPC)")

    await engine.seed_default_fleet_if_empty()
    try:
        n_types = await engine.load_custom_entity_types()
        if n_types:
            log.info("Cargados %d tipos de entidad personalizados desde DB", n_types)
    except Exception:
        log.warning("No se pudieron cargar tipos custom desde DB")
    try:
        await seed_knowledge_if_empty()
    except Exception:
        log.warning("Knowledge seed skipped (LLM not ready)")
    loop_task = asyncio.create_task(engine.run_loop())
    osrm_task = asyncio.create_task(_osrm_probe_loop())
    aruba_task = asyncio.create_task(_aruba_inventory_loop())
    event_source_task = asyncio.create_task(run_event_source(engine))
    ai_task = asyncio.create_task(ai_engine.observe_loop())
    yield
    ai_engine.stop()
    ai_task.cancel()
    event_source_task.cancel()
    aruba_task.cancel()
    osrm_task.cancel()
    engine.stop()
    loop_task.cancel()
    try:
        await ai_task
    except asyncio.CancelledError:
        pass
    try:
        await event_source_task
    except asyncio.CancelledError:
        pass
    try:
        await aruba_task
    except asyncio.CancelledError:
        pass
    try:
        await osrm_task
    except asyncio.CancelledError:
        pass
    try:
        await loop_task
    except asyncio.CancelledError:
        pass
    await engine.aclose()


app = FastAPI(
    title="Sentinel Digital Twin API",
    version="2.0.1",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NetworkBody(BaseModel):
    mqtt: bool = Field(default=True)
    p2p: bool = Field(default=True)
    http: bool = Field(default=True)


class SpawnBody(BaseModel):
    """Lat/lon opcionales: si no se indican, se usa el spawn de la región activa."""
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    entityTypeId: str | None = Field(default=None)
    displayLabel: str | None = Field(default=None)
    manualControl: bool = Field(default=False)


class VehiclePositionBody(BaseModel):
    latitude: float
    longitude: float
    headingDeg: float | None = None
    speedKmh: float | None = None


class ControlBody(BaseModel):
    action: Literal["play", "pause", "reset"] | None = None
    speedMultiplier: float | None = Field(default=None, ge=0.1, le=20.0)


class EmergencyBody(BaseModel):
    latitude: float
    longitude: float
    title: str | None = Field(default=None)
    emergencyType: str | None = Field(default="medical")
    description: str | None = Field(default=None)


class JamBody(BaseModel):
    polygon: list[list[float]] = Field(
        ...,
        description="Lista de [lat, lon] cerrando el polígono (mínimo 3 vértices).",
        min_length=3,
    )


class AssignBody(BaseModel):
    ambulanceId: str
    emergencyId: str


class PoiBody(BaseModel):
    """`kind` debe coincidir con un `entityTypes[].id` cuyo `kind` sea `place`."""
    kind: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="POI")
    latitude: float
    longitude: float


class JamPointBody(BaseModel):
    latitude: float
    longitude: float
    radiusM: float = Field(default=70.0, ge=25.0, le=400.0)


class HealthResponse(BaseModel):
    status: str = Field(description="Status of the service")


class Vehicle(BaseModel):
    id: str
    type: str
    status: str
    latitude: float
    longitude: float
    fuel_level: float
    callsign: str | None = None
    speed_kmh: float | None = None
    last_updated: str | None = None
    metadata: dict[str, Any] | None = None


class VehiclesResponse(BaseModel):
    vehicles: list[Vehicle]


class VehicleStatusSummary(BaseModel):
    total: int
    by_type: dict[str, int]
    by_status: dict[str, int]


class WeatherStation(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float


class WeatherStationsResponse(BaseModel):
    stations: list[WeatherStation]


class WeatherReading(BaseModel):
    temperature: float
    humidity: float
    wind_speed: float
    timestamp: str


class AskResponse(BaseModel):
    answer: str
    confidence: float | None = None
    data: dict[str, Any] | None = None


def _entity_type_meta(et_id: str | None) -> dict[str, Any]:
    """Resuelve metadata económica/operativa del entityType (powertrain, costes, dotación)."""
    if not et_id:
        return {}
    et = next((t for t in engine.get_entity_types() if t.get("id") == et_id), None)
    if not et:
        return {}
    return {
        "powertrain": et.get("powertrain"),
        "crewMin": et.get("crewMin"),
        "crewMax": et.get("crewMax"),
        "costPerMin": et.get("costPerMin"),
        "activationCost": et.get("activationCost"),
    }


def _operating_cost_for_amb(amb: dict[str, Any]) -> dict[str, Any]:
    """Activación + tiempo activo × tarifa, según entityType."""
    meta = _entity_type_meta(amb.get("entityTypeId"))
    rate = float(meta.get("costPerMin") or 0.0)
    activation = float(meta.get("activationCost") or 0.0) if amb.get("activated") else 0.0
    minutes = float(amb.get("activeSeconds") or 0.0) / 60.0
    runtime = minutes * rate
    return {
        "activation": round(activation, 2),
        "runtime": round(runtime, 2),
        "total": round(activation + runtime, 2),
        "activeMinutes": round(minutes, 2),
        "ratePerMin": rate,
    }


def _vehicle_row(unit: dict[str, Any], *, is_companion: bool = False) -> dict[str, Any]:
    """Vehículo serializado segun contrato Aruba (schemas/Vehicle, snake_case).

    Campos requeridos por contrato: id, type, status, latitude, longitude,
    fuel_level. Para vehículos eléctricos `fuel_level` representa la batería
    primaria (semántica "energía disponible" %, 0-100).

    `metadata` opcional se usa como contenedor de telemetría rica
    (powertrain, costes, mecánica, médico, misión, etc.) sin romper el
    contrato base.
    """
    et_id = unit.get("entityTypeId") or unit.get("kind") or ("ambulance" if not is_companion else "companion")
    meta = _entity_type_meta(et_id)
    tele = unit.get("telemetry") or {}
    mech = tele.get("mechanical") or {}
    med = tele.get("medical")
    pos = tele.get("positioning") or {}
    powertrain = meta.get("powertrain") or "combustion"
    energy_kind = "fuel" if powertrain == "combustion" else "battery"
    energy_value = mech.get("fuelLevelPct") if energy_kind == "fuel" else mech.get("batteryPct")
    if energy_value is None:
        energy_value = unit.get("fuelLevel") if energy_kind == "fuel" else unit.get("batteryLevel")
    if energy_value is None:
        energy_value = 0.0
    speed_val = pos.get("speedKmh") if pos.get("speedKmh") is not None else unit.get("speedKmh")
    metadata: dict[str, Any] = {
        "displayLabel": unit.get("displayLabel") or unit.get("callsign") or unit.get("name"),
        "fsmState": unit.get("fsmState"),
        "missionPhase": unit.get("missionPhase"),
        "missionStatus": unit.get("missionStatus"),
        "assignedEmergencyId": unit.get("assignedEmergencyId"),
        "hasPatient": bool(unit.get("hasPatient", False)),
        "patientSeverity": unit.get("patientSeverity"),
        "isCompanion": is_companion,
        "powertrain": powertrain,
        "crew_min": meta.get("crewMin"),
        "crew_max": meta.get("crewMax"),
        "energy": {
            "kind": energy_kind,
            "level_pct": round(float(energy_value), 1),
            "aux_battery_pct": round(float(mech.get("batteryPct")), 1) if mech.get("batteryPct") is not None else None,
        },
        "mechanical": {
            "engine_temp_c": mech.get("engineTempC"),
            "tire_pressure_kpa": mech.get("tirePressureKpa"),
            "oil_temp_c": mech.get("oilTempC"),
            "engine_rpm": mech.get("engineRpm"),
            "odometer_km": mech.get("odometerKm") or unit.get("odometerKm"),
            "sirens_on": mech.get("sirensOn"),
            "power_state": mech.get("powerState"),
            "range_km": mech.get("rangeKm"),
            "engine_health_pct": mech.get("engineHealthPct"),
        },
        "medical": med,
        "powered_off": bool(unit.get("poweredOff", False)),
        "heading_deg": pos.get("headingDeg") or unit.get("headingDeg"),
        "road_speed_limit_kmh": unit.get("roadSpeedLimitKmh") or pos.get("roadSpeedLimitKmh"),
    }
    if not is_companion:
        cost = _operating_cost_for_amb(unit)
        metadata["cost"] = {
            "activation": cost["activation"],
            "runtime": cost["runtime"],
            "total": cost["total"],
            "active_minutes": cost["activeMinutes"],
            "rate_per_min": cost["ratePerMin"],
        }
    return {
        # ── contrato base (required) ──
        "id": str(unit.get("id") or ""),
        "type": str(et_id),
        "status": str(unit.get("missionPhase") or unit.get("status") or unit.get("fsmState") or "idle"),
        "latitude": float(unit.get("latitude") or 0.0),
        "longitude": float(unit.get("longitude") or 0.0),
        "fuel_level": round(float(energy_value), 1),
        # ── opcionales ──
        "callsign": unit.get("displayLabel") or unit.get("callsign"),
        "speed_kmh": float(speed_val) if speed_val is not None else None,
        "last_updated": unit.get("updatedAt"),
        "metadata": metadata,
    }


def _vehicle_public_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [_vehicle_row(a, is_companion=False) for a in engine.ambulances]
    rows.extend(_vehicle_row(c, is_companion=True) for c in engine.companions)
    return rows


async def _weather_station_rows() -> list[dict[str, Any]]:
    """Construye la lista de estaciones conocidas fusionando tres fuentes:

    1. POIs del inventario de la isla (tienen nombre y coordenadas).
    2. Cache in-memory del engine (lecturas recientes, caliente).
    3. Base de datos (weather_stations_latest) — permite reconstruir la lista
       tras un reinicio cuando el engine aún no ha recibido mensajes nuevos.
    """
    stations_by_id: dict[str, dict[str, Any]] = {}
    spawn_lat, spawn_lon = default_spawn()

    # 1. POIs del inventario (fuente de verdad para nombre y coords)
    for poi in engine.pois:
        if poi.get("kind") != "weather_station":
            continue
        sid = str(poi.get("id") or "").strip()
        if not sid:
            continue
        stations_by_id[sid] = {
            "id": sid,
            "name": str(poi.get("name") or "Weather station"),
            "latitude": float(poi.get("latitude") or spawn_lat),
            "longitude": float(poi.get("longitude") or spawn_lon),
        }

    # 2. Cache in-memory (lecturas ya recibidas en esta sesión)
    for sid, reading in engine.weather_by_station.items():
        station_id = str(sid or "").strip()
        if not station_id:
            continue
        if station_id not in stations_by_id:
            stations_by_id[station_id] = {
                "id": station_id,
                "name": f"Station {station_id[:8]}",
                "latitude": float(reading.get("latitude") or spawn_lat),
                "longitude": float(reading.get("longitude") or spawn_lon),
            }

    # 3. Base de datos (cubre el arranque frío: el engine está vacío pero la
    #    DB tiene todas las lecturas históricas).
    try:
        from .weather_db import get_all_latest_readings

        db_rows = await get_all_latest_readings()
        for station_id in db_rows:
            if station_id not in stations_by_id:
                stations_by_id[station_id] = {
                    "id": station_id,
                    "name": f"Station {station_id[:8]}",
                    "latitude": spawn_lat,
                    "longitude": spawn_lon,
                }
    except Exception:
        pass

    stations = list(stations_by_id.values())
    stations.sort(key=lambda s: str(s.get("name") or "").lower())
    return stations


def _square_polygon_around(lat: float, lon: float, half_side_m: float) -> list[list[float]]:
    dlat = half_side_m / 111320.0
    dlon = half_side_m / (111320.0 * max(0.25, math.cos(math.radians(lat))))
    return [
        [lat - dlat, lon - dlon],
        [lat - dlat, lon + dlon],
        [lat + dlat, lon + dlon],
        [lat + dlat, lon - dlon],
    ]


@app.get("/api/sim/state")
async def get_state() -> dict:
    """Snapshot actual del motor + IA para polling fallback del dashboard."""
    return _state_merged()


# ---------------------------------------------------------------------------
# Regiones / mapas seleccionables
# ---------------------------------------------------------------------------


class RegionSwitchBody(BaseModel):
    regionId: str = Field(..., min_length=1, max_length=32)


@app.get("/api/regions")
async def regions_list() -> dict[str, Any]:
    """Lista de regiones disponibles + id de la activa.

    El frontend la usa para pintar el selector y centrar el mapa al cambiar.
    """
    return {
        "active": get_active_region_id(),
        "regions": [r.to_public() for r in list_regions()],
    }


@app.post("/api/regions/active")
async def regions_set_active(body: RegionSwitchBody) -> dict[str, Any]:
    """Cambia la región activa y resetea la simulación.

    El reset es obligatorio: las coordenadas in-memory (POIs, flota, jams)
    no son válidas en el grafo OSRM de la nueva región.
    """
    try:
        region = set_active_region(body.regionId)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Región desconocida: {body.regionId}")
    await engine.reset_simulation()
    # Sondeo inmediato del OSRM de la nueva región: la sonda periódica duerme
    # 15 s cuando todo va bien y el estado mostraría la región anterior.
    from .routing import osrm_base_url, probe_osrm_routing

    base = osrm_base_url()
    ok, err = await probe_osrm_routing(engine._http, base)
    engine.set_osrm_routing_status(ready=ok, base_url=base, checked_at=_iso(), last_error=None if ok else (err or "error"))
    return {
        "ok": True,
        "active": region.id,
        "region": region.to_public(),
        "state": _state_merged(),
    }


@app.get("/api/osrm/{path:path}")
async def osrm_proxy(path: str, request: Request) -> Response:
    """Proxy HTTP al OSRM de la región activa.

    Lo usa el panel del vehículo (`VehicleHomeView`) para pedir steps
    turn-by-turn sin tener que conocer la URL OSRM directa. Al cambiar
    la región activa, el destino del proxy cambia automáticamente.
    """
    from .routing import osrm_base_url
    base = osrm_base_url().rstrip("/")
    qs = request.url.query
    target = f"{base}/{path}" + (f"?{qs}" if qs else "")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(target)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"OSRM no responde: {type(e).__name__}")
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@app.get("/api/sim/telemetry/schema")
async def telemetry_schema() -> dict[str, Any]:
    """JSON Schema del payload de telemetría por unidad (motores mecánico / médico / posicionamiento)."""
    return telemetry_schema_json()


@app.get("/api/sim/stream")
async def stream() -> StreamingResponse:
    """Stream SSE del estado completo a ~2.5 Hz.

    Cadencia fija (``0.4`` s): el ``speedMultiplier`` solo afecta al
    motor interno, no al ritmo de publicación del stream.
    """
    async def gen() -> AsyncIterator[str]:
        while True:
            # Cadencia fija (~2.5 Hz): el multiplicador de velocidad solo afecta al motor, no al ritmo SSE.
            await asyncio.sleep(0.4)
            payload = json.dumps({"state": _state_merged()})
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )




@app.get("/health", tags=["base"], response_model=HealthResponse)
async def health() -> HealthResponse:
    """Schema HealthResponse: {status: string}."""
    return HealthResponse(status="ok")


@app.get("/openapi.yaml", tags=["base"], response_class=Response)
async def get_contract_openapi_yaml() -> Response:
    yaml_spec = yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=False)
    return Response(content=yaml_spec, media_type="text/plain; charset=utf-8")


@app.get("/vehicles/status", tags=["base"], response_model=VehicleStatusSummary)
async def vehicle_status() -> VehicleStatusSummary:
    """Schema VehicleStatusSummary: {total, by_type, by_status}."""
    vehicles = _vehicle_public_rows()
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for v in vehicles:
        ty = str(v.get("type", "unknown"))
        by_type[ty] = by_type.get(ty, 0) + 1
        st = str(v.get("status", "idle"))
        by_status[st] = by_status.get(st, 0) + 1
    return VehicleStatusSummary(
        total=len(vehicles),
        by_type=by_type,
        by_status=by_status,
    )


def _amb_matches_filter(amb: dict[str, Any], args: dict[str, Any]) -> bool:
    """Aplica args de filter_units a una ambulancia. True si cumple TODOS."""
    et_id = amb.get("entityTypeId") or "ambulance"
    if args.get("entityTypeId") and et_id != args["entityTypeId"]:
        return False
    if args.get("hasPatient") is not None and bool(amb.get("hasPatient", False)) != bool(args["hasPatient"]):
        return False
    if args.get("severity") and amb.get("patientSeverity") != args["severity"]:
        return False
    if args.get("missionPhase") and (amb.get("missionPhase") or "idle") != args["missionPhase"]:
        return False
    if args.get("poweredOff") is not None and bool(amb.get("poweredOff", False)) != bool(args["poweredOff"]):
        return False
    tele = amb.get("telemetry") or {}
    mech = tele.get("mechanical") or {}
    fuel = mech.get("fuelLevelPct")
    if fuel is None:
        fuel = amb.get("fuelLevel")
    fuel = float(fuel) if fuel is not None else 100.0
    batt = mech.get("batteryPct")
    if batt is None:
        batt = amb.get("batteryLevel")
    batt = float(batt) if batt is not None else 100.0
    if args.get("fuelBelow") is not None and fuel >= float(args["fuelBelow"]):
        return False
    if args.get("fuelAbove") is not None and fuel <= float(args["fuelAbove"]):
        return False
    if args.get("batteryBelow") is not None and batt >= float(args["batteryBelow"]):
        return False
    return True


def _execute_command_against_fleet(cmd: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta el comando interpretado contra estado actual y devuelve resultados.

    Solo lectura: cuenta y lista matches sin mutar estado. Para `filter_units`
    devuelve `matched_count`, `total`, `matches` (primeros 20). Para
    `focus_unit`/`reset_filters`/`set_ai_mode`/`spawn_units`/`create_emergency`
    no ejecuta efectos colaterales (solo lo describe).
    """
    name = cmd.get("command") or "explain"
    args = cmd.get("args") or {}
    ambs = engine.ambulances
    total = len(ambs)
    if name == "filter_units":
        matches = [a for a in ambs if _amb_matches_filter(a, args)]
        sample = []
        for a in matches[:20]:
            tele = a.get("telemetry") or {}
            mech = tele.get("mechanical") or {}
            sample.append({
                "id": str(a.get("id") or ""),
                "type": a.get("entityTypeId"),
                "callsign": a.get("displayLabel"),
                "fuel_level": float(mech.get("fuelLevelPct") or a.get("fuelLevel") or 0.0),
                "battery_pct": float(mech.get("batteryPct") or a.get("batteryLevel") or 0.0),
                "status": a.get("missionPhase") or a.get("fsmState") or "idle",
            })
        return {"matched_count": len(matches), "total": total, "matches": sample}
    if name == "focus_unit":
        q = str(args.get("query") or "").lower()
        hit = next(
            (a for a in ambs if q in str(a.get("id") or "").lower() or q in str(a.get("displayLabel") or "").lower()),
            None,
        )
        return {"found": hit is not None, "id": hit.get("id") if hit else None}
    if name == "set_ai_mode":
        return {"current_mode": ai_engine.mode, "requested_mode": args.get("mode")}
    if name == "reset_filters":
        return {"applied": True}
    return {"total": total}


@app.get("/ask", tags=["base"], response_model=AskResponse)
async def ask_fleet(q: str = Query(..., min_length=3)) -> AskResponse:
    """Schema AskResponse: {answer, confidence?, data?}.

    Pipeline: command_service.interpret (regex fast-path → LLM tool-calling
    → heurística) → ejecuta el comando contra la flota → responde con conteo
    real + sample. Para filter_units: `answer` es texto humano del estilo
    "5 vehículos cumplen el filtro (combustible > 50%)".
    """
    snapshot = _state_merged()
    state_summary = {
        "units": len(snapshot.get("ambulances") or []),
        "companions": len(snapshot.get("companions") or []),
        "emergencies": len(snapshot.get("emergencies") or []),
        "pois": len(snapshot.get("pois") or []),
        "link_state": snapshot.get("linkState"),
        "paused": snapshot.get("paused", True),
        "ai_mode": snapshot.get("aiMode"),
    }
    try:
        from .command_service import interpret
        ctx = {
            "pois": [
                {"name": p.get("name"), "kind": p.get("kind"), "latitude": p.get("latitude"), "longitude": p.get("longitude")}
                for p in engine.pois
            ],
            "entityTypes": engine.get_entity_types(),
        }
        cmd = await interpret(q, ctx)
    except Exception as e:
        return AskResponse(
            answer=f"Error interpretando: {type(e).__name__}",
            confidence=0.0,
            data={"state_summary": state_summary, "query": q, "error": str(e)},
        )

    name = cmd.get("command") or "explain"
    summary = cmd.get("summary") or ""
    confidence = 0.85 if cmd.get("ok") else 0.35

    # Ejecuta el comando contra estado actual para devolver respuesta concreta.
    exec_result: dict[str, Any] = {}
    answer: str
    if name == "filter_units":
        exec_result = _execute_command_against_fleet(cmd)
        n = exec_result.get("matched_count", 0)
        tot = exec_result.get("total", 0)
        criterio = summary.replace("Filtrando: ", "") if summary.startswith("Filtrando:") else summary
        answer = f"{n} de {tot} vehículos cumplen el filtro ({criterio})."
    elif name == "focus_unit":
        exec_result = _execute_command_against_fleet(cmd)
        answer = (
            f"Unidad encontrada: {exec_result.get('id')}"
            if exec_result.get("found")
            else f"No encontré ninguna unidad que coincida con «{cmd['args'].get('query', '')}»."
        )
    elif name == "set_ai_mode":
        exec_result = _execute_command_against_fleet(cmd)
        answer = f"IA actualmente en modo «{exec_result.get('current_mode')}». Solicitado: «{exec_result.get('requested_mode')}». Usa POST /api/ai/mode para aplicar."
    elif name == "explain":
        answer = cmd.get("args", {}).get("text") or summary or "Sin comando accionable."
    else:
        exec_result = _execute_command_against_fleet(cmd)
        answer = summary

    return AskResponse(
        answer=answer,
        confidence=confidence,
        data={
            "query": q,
            "command": cmd,
            "execution": exec_result,
            "state_summary": state_summary,
        },
    )


@app.get("/vehicles", tags=["vehicles"], response_model=VehiclesResponse)
async def list_vehicles(type: str | None = Query(default=None)) -> VehiclesResponse:
    """Schema VehiclesResponse: {vehicles: Vehicle[]}."""
    vehicles = _vehicle_public_rows()
    if type is not None:
        target = type.strip().lower()
        vehicles = [v for v in vehicles if str(v.get("type", "")).lower() == target]
    return VehiclesResponse(vehicles=[Vehicle.model_validate(v) for v in vehicles])


@app.get("/vehicles/{vehicle_id}", tags=["vehicles"], response_model=Vehicle)
async def get_vehicle(vehicle_id: str) -> Vehicle:
    """Schema Vehicle (single)."""
    vehicles = _vehicle_public_rows()
    row = next((v for v in vehicles if str(v.get("id")) == vehicle_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="vehicle not found")
    return Vehicle.model_validate(row)


@app.get("/weather-stations", tags=["weather"], response_model=WeatherStationsResponse)
async def list_weather_stations() -> WeatherStationsResponse:
    """Schema WeatherStationsResponse: {stations: WeatherStation[]}."""
    return WeatherStationsResponse(stations=[WeatherStation.model_validate(s) for s in await _weather_station_rows()])


@app.get("/weather-stations/{station_id}/reading", tags=["weather"], response_model=WeatherReading)
async def get_reading(station_id: str) -> WeatherReading:
    """Última lectura de una estación, servida desde la base de datos
    (fuente primaria) con fallback al cache in-memory del engine."""
    station = next((s for s in await _weather_station_rows() if str(s.get("id")) == station_id), None)
    if station is None:
        raise HTTPException(status_code=404, detail="station not found")

    # Fuente primaria: base de datos (sobrevive reinicios, historial completo)
    reading: dict[str, Any] | None = None
    try:
        from .weather_db import get_latest_reading

        reading = await get_latest_reading(station_id)
    except Exception:
        pass

    # Fallback: cache in-memory (lectura reciente aún no persistida, o DB off)
    if reading is None:
        reading = engine.weather_by_station.get(station_id)

    if reading is None:
        raise HTTPException(status_code=503, detail="no reading available yet for this station")

    return WeatherReading(
        temperature=round(float(reading["temperature_c"]), 1),
        humidity=round(float(reading["humidity_pct"]), 1),
        wind_speed=round(float(reading["wind_speed_kmh"]), 2),
        timestamp=str(reading["timestamp"]),
    )


@app.post("/api/events/ingest", tags=["events"], status_code=202)
async def ingest_event(event: ExternalEventIn) -> dict[str, Any]:
    """Ingesta REST de un evento externo (sustituye al antiguo consumer de mensajería)."""
    await engine.ingest_external_event(event.model_dump())
    return {"ok": True, "id": event.id}


@app.post("/api/weather/ingest", tags=["events"], status_code=202)
async def ingest_weather(reading: WeatherReadingIn) -> dict[str, Any]:
    """Ingesta REST de una lectura meteorológica; se persiste en `weather_readings` si hay Supabase."""
    row = reading.model_dump()
    await engine.ingest_weather_reading(row)
    asyncio.create_task(upsert_weather_reading(row))
    return {"ok": True, "id": reading.id}


@app.get("/api/events", tags=["events"])
async def list_external_events(
    type: str | None = Query(default=None, description="Filtra por tipo de evento"),
    severity: str | None = Query(default=None, description="Filtra por severidad"),
    only_active: bool = Query(default=False, description="Solo eventos sin resolved_at"),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """Lista los últimos eventos externos recibidos (mock o ingesta REST)."""
    rows = list(engine.external_events)
    if type:
        rows = [e for e in rows if str(e.get("type")) == type]
    if severity:
        rows = [e for e in rows if str(e.get("severity")) == severity]
    if only_active:
        rows = [e for e in rows if not e.get("resolved_at")]
    rows = rows[-limit:]
    return {"items": [e.copy() for e in rows], "count": len(rows)}


@app.get("/api/weather", tags=["events"])
async def list_weather() -> dict[str, Any]:
    """Última lectura recibida por estación."""
    return {
        "stations": {k: v.copy() for k, v in engine.weather_by_station.items()},
        "count": len(engine.weather_by_station),
    }


@app.get("/api/weather/{station_id}/history", tags=["events"])
async def weather_station_history(station_id: str) -> dict[str, Any]:
    history = engine.weather_station_history(station_id)
    if not history and station_id not in engine.weather_by_station:
        raise HTTPException(status_code=404, detail="station not found")
    return {"stationId": station_id, "items": history, "count": len(history)}


@app.get("/api/events/status", tags=["events"])
async def event_source_status() -> dict[str, Any]:
    """Estado de la fuente de eventos (mock/REST) y contadores de ingesta."""
    return dict(engine.event_source_status)


class WeatherOverrideRequest(BaseModel):
    """Manual weather override for demos / what-if. Apply to all stations or a subset.

    `holdSeconds` (default 600) freezes the engine's `weather_by_station` snapshot
    so live readings don't immediately overwrite the override values.
    Set 0 to apply once and let live readings reclaim within ~5s.
    """
    precipitationMm: float = 0.0
    windKmh: float = 0.0
    visibilityKm: float = 10.0
    temperatureC: float | None = None
    stationIds: list[str] | None = None
    holdSeconds: float = 600.0


@app.post("/api/weather/override", tags=["events"])
async def weather_override(req: WeatherOverrideRequest) -> dict[str, Any]:
    """Push a synthetic reading into engine.weather_by_station for stress testing.

    Lets operators force a storm/fog scenario to validate ETA degradation,
    routing avoidance, and scoring shifts without waiting for live conditions.
    """
    if req.stationIds:
        targets = req.stationIds
    else:
        # Broadcast across both already-seen stations and POI registry so an
        # override hits every station regardless of feed pacing.
        seen = set(engine.weather_by_station.keys())
        for poi in engine.pois:
            if poi.get("kind") == "weather_station":
                seen.add(str(poi.get("id")))
        targets = list(seen)
        if not targets:
            targets = [s["id"] for s in await _weather_station_rows()]
    import time as _time
    if req.holdSeconds > 0:
        engine._weather_override_until = _time.monotonic() + float(req.holdSeconds)
    now_iso = datetime.now(timezone.utc).isoformat()
    applied = 0
    for sid in targets:
        existing = engine.weather_by_station.get(sid) or {}
        merged = dict(existing)
        merged.update({
            "id": existing.get("id") or f"override-{sid}",
            "station_id": sid,
            "timestamp": now_iso,
            "temperature_c": req.temperatureC if req.temperatureC is not None else existing.get("temperature_c", 28.0),
            "humidity_pct": existing.get("humidity_pct", 70.0),
            "wind_speed_kmh": req.windKmh,
            "wind_direction_deg": existing.get("wind_direction_deg", 90.0),
            "pressure_hpa": existing.get("pressure_hpa", 1013.0),
            "precipitation_mm": req.precipitationMm,
            "visibility_km": req.visibilityKm,
            "uv_index": existing.get("uv_index", 5.0),
            "receivedAt": now_iso,
            "source": "operator_override",
        })
        engine.weather_by_station[sid] = merged
        applied += 1
    return {
        "ok": True,
        "appliedToStations": applied,
        "precipitationMm": req.precipitationMm,
        "windKmh": req.windKmh,
        "visibilityKm": req.visibilityKm,
    }


@app.get("/api/island/summary", tags=["island"])
async def island_summary() -> dict[str, Any]:
    """Aggregated island-wide situational awareness for the global monitor.

    Combines weather aggregates, active external events grouped by type/severity,
    fleet KPIs, dispatch ETA averages, weather impact, and a per-zone
    breakdown bucketing assets into four quadrants around the active region centre.
    """
    weather = engine.weather_by_station
    stations_meta = await _weather_station_rows()
    coord_by_id = {s["id"]: (s.get("latitude"), s.get("longitude")) for s in stations_meta}
    temps: list[float] = []
    precips: list[float] = []
    winds: list[float] = []
    visibilities: list[float] = []
    alerts: list[dict[str, Any]] = []
    for sid, reading in weather.items():
        try:
            t = float(reading.get("temperature_c", 0.0))
            p = float(reading.get("precipitation_mm", 0.0))
            w = float(reading.get("wind_speed_kmh", 0.0))
            v = float(reading.get("visibility_km", 10.0))
        except (TypeError, ValueError):
            continue
        temps.append(t)
        precips.append(p)
        winds.append(w)
        visibilities.append(v)
        if p > 5.0 or w > 25.0 or v < 5.0:
            lat, lon = coord_by_id.get(sid, (None, None))
            alerts.append({
                "stationId": sid,
                "stationName": next((s["name"] for s in stations_meta if s["id"] == sid), sid),
                "latitude": lat, "longitude": lon,
                "precipMm": p, "windKmh": w, "visibilityKm": v,
                "kind": "storm" if p > 5.0 else ("wind" if w > 25.0 else "fog"),
            })
    weather_aggregates = {
        "stations": len(weather),
        "avgTempC": round(sum(temps) / len(temps), 1) if temps else None,
        "maxPrecipMmh": round(max(precips), 1) if precips else None,
        "maxWindKmh": round(max(winds), 1) if winds else None,
        "minVisibilityKm": round(min(visibilities), 2) if visibilities else None,
        "alerts": alerts,
    }

    events = engine.external_events
    unresolved = [e for e in events if not e.get("resolved_at")]
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for ev in unresolved:
        t = str(ev.get("type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        s = str(ev.get("severity") or "unknown")
        by_severity[s] = by_severity.get(s, 0) + 1

    ambs = engine.ambulances
    ems = engine.emergencies
    active = [e for e in ems if e.get("status") in ("pending", "assigned")]
    fuel_low = sum(1 for a in ambs if float(a.get("fuelLevel") or 100.0) < 25.0)
    etas = [
        float(tr["eta_predicted_s"])
        for tr in engine._mission_tracking.values()
        if isinstance(tr.get("eta_predicted_s"), (int, float))
    ]
    weather_factors = [
        float(a.get("weatherFactor"))
        for a in ambs
        if isinstance(a.get("weatherFactor"), (int, float)) and a.get("missionStatus") == "EN_ROUTE"
    ]
    fleet = {
        "totalAmbulances": len(ambs),
        "activeEmergencies": len(active),
        "fuelLowCount": fuel_low,
        "avgEtaSeconds": round(sum(etas) / len(etas), 1) if etas else None,
        "pulseRatePerMin": round(engine._recent_external_emergency_rate_per_min(), 2),
    }
    weather_impact = {
        "worstFactor": round(min(weather_factors), 3) if weather_factors else 1.0,
        "avgFactor": round(sum(weather_factors) / len(weather_factors), 3) if weather_factors else 1.0,
        "affectedMissions": sum(1 for f in weather_factors if f < 0.85),
    }

    # Per-zone bucketing: cuatro cuadrantes alrededor del centro de la región activa.
    region = get_active_region()
    cen_lat, cen_lon = region.center
    zones: dict[str, dict[str, int]] = {
        "north_west": {"ambulances": 0, "events": 0, "emergencies": 0},
        "north_east": {"ambulances": 0, "events": 0, "emergencies": 0},
        "south_west": {"ambulances": 0, "events": 0, "emergencies": 0},
        "south_east": {"ambulances": 0, "events": 0, "emergencies": 0},
    }
    def _zone(lat: float, lon: float) -> str:
        ns = "north" if lat >= cen_lat else "south"
        ew = "east" if lon >= cen_lon else "west"
        return f"{ns}_{ew}"
    for a in ambs:
        try:
            zones[_zone(float(a["latitude"]), float(a["longitude"]))]["ambulances"] += 1
        except (KeyError, TypeError, ValueError):
            continue
    for ev in unresolved:
        try:
            zones[_zone(float(ev["latitude"]), float(ev["longitude"]))]["events"] += 1
        except (KeyError, TypeError, ValueError):
            continue
    for em in active:
        try:
            zones[_zone(float(em["latitude"]), float(em["longitude"]))]["emergencies"] += 1
        except (KeyError, TypeError, ValueError):
            continue

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "region": {"id": region.id, "name": region.name},
        "weather": weather_aggregates,
        "events": {
            "active": len(unresolved),
            "byType": by_type,
            "bySeverity": by_severity,
            "items": [{
                "id": e.get("id"), "type": e.get("type"), "severity": e.get("severity"),
                "title": e.get("title"), "latitude": e.get("latitude"), "longitude": e.get("longitude"),
                "started_at": e.get("started_at"),
            } for e in unresolved[:50]],
        },
        "fleet": fleet,
        "weatherImpact": weather_impact,
        "perZone": zones,
        "stations": stations_meta,
    }


@app.get("/api/sim/comms/stream")
async def comms_stream(since: int = Query(0, ge=0)) -> StreamingResponse:
    """Stream SSE del canal de comms desde ``since`` (seq monotónico)."""
    async def gen() -> AsyncIterator[str]:
        last = since
        while True:
            await asyncio.sleep(0.28)
            for it in engine.get_comms_since(last):
                last = max(last, int(it.get("seq", 0)))
                yield f"data: {json.dumps(it)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/sim/network")
async def set_network(body: NetworkBody) -> dict:
    """Toggle canales MQTT / P2P mesh / HTTP fallback."""
    await engine.set_network(body.mqtt, body.p2p, body.http)
    return {"ok": True, "networkStatus": engine.network.copy(), "state": _state_merged()}


@app.post("/api/sim/spawn")
async def spawn(body: SpawnBody) -> dict:
    """Instancia una unidad nueva en el mapa (dashboard o panel vehículo)."""
    spawn_lat, spawn_lon = default_spawn()
    lat = body.latitude if body.latitude is not None else spawn_lat
    lon = body.longitude if body.longitude is not None else spawn_lon
    aid = await engine.spawn_ambulance(
        lat, lon,
        entity_type_id=body.entityTypeId,
        display_label=body.displayLabel,
        manual_control=body.manualControl,
    )
    return {"ok": True, "id": aid, "state": _state_merged()}


@app.post("/api/sim/vehicle/{vehicle_id}/position")
async def vehicle_position(vehicle_id: str, body: VehiclePositionBody) -> dict:
    """Empuja posición desde el panel del vehículo (GPS / arrastre manual)."""
    ok = await engine.update_vehicle_position(
        vehicle_id, body.latitude, body.longitude,
        heading_deg=body.headingDeg, speed_kmh=body.speedKmh,
    )
    return {"ok": ok}


@app.post("/api/sim/vehicle/{vehicle_id}/advance")
async def vehicle_advance(vehicle_id: str) -> dict:
    """Avanza fase de misión en vehículos manuales (botón "He llegado")."""
    res = await engine.advance_manual_phase(vehicle_id)
    return res


# ═══════════════════════════════════════════════════════════════════════════
# Fleet catalog — persistencia de unidades y tipos del rol "vehicle"
# ═══════════════════════════════════════════════════════════════════════════

class FleetVehicleBody(BaseModel):
    ownerUserId: str
    entityTypeId: str
    displayLabel: str
    lastLat: float | None = None
    lastLon: float | None = None


def _fleet_supabase():
    from .supabase_client import get_supabase
    return get_supabase()


@app.get("/api/fleet/types")
async def fleet_list_types() -> list[dict[str, Any]]:
    """Catálogo in-memory (builtin + custom ya cargados) — fuente primaria."""
    return engine.get_entity_types()


@app.get("/api/fleet/vehicles")
async def fleet_list_vehicles(ownerUserId: str | None = Query(default=None)) -> list[dict[str, Any]]:
    """Lista unidades persistidas; filtra por propietario si se indica."""
    sb = _fleet_supabase()
    if sb is None:
        return []
    try:
        q = sb.table("fleet_vehicles").select("*").order("updated_at", desc=True)
        if ownerUserId:
            q = q.eq("owner_user_id", ownerUserId)
        resp = q.execute()
        rows = resp.data or []
    except Exception:
        return []
    return [
        {
            "id": r["id"],
            "ownerUserId": r.get("owner_user_id"),
            "entityTypeId": r.get("entity_type_id"),
            "displayLabel": r.get("display_label"),
            "lastLat": r.get("last_lat"),
            "lastLon": r.get("last_lon"),
            "createdAt": r.get("created_at"),
        }
        for r in rows
    ]


@app.post("/api/fleet/vehicles")
async def fleet_upsert_vehicle(body: FleetVehicleBody) -> dict[str, Any]:
    """Registra la unidad del piloto en la tabla ``fleet_vehicles`` de Supabase."""
    sb = _fleet_supabase()
    if sb is None:
        return {"ok": False, "reason": "no-db"}
    payload = {
        "owner_user_id": body.ownerUserId,
        "entity_type_id": body.entityTypeId,
        "display_label": body.displayLabel,
        "last_lat": body.lastLat,
        "last_lon": body.lastLon,
    }
    try:
        resp = sb.table("fleet_vehicles").insert(payload).execute()
        row = (resp.data or [{}])[0]
        return {"ok": True, "id": row.get("id")}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


@app.delete("/api/fleet/vehicles/{vehicle_id}")
async def fleet_delete_vehicle(vehicle_id: str) -> dict[str, Any]:
    """Borra una unidad persistida (no afecta al estado in-memory del motor)."""
    sb = _fleet_supabase()
    if sb is None:
        return {"ok": False, "reason": "no-db"}
    try:
        sb.table("fleet_vehicles").delete().eq("id", vehicle_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


@app.post("/api/sim/control")
async def sim_control(body: ControlBody) -> dict:
    """Play / Pause / Reset + speed multiplier del motor."""
    await engine.apply_sim_control(body.action, body.speedMultiplier)
    return {"ok": True, "state": _state_merged()}


@app.post("/api/sim/emergency")
async def create_emergency(body: EmergencyBody) -> dict:
    """Crea una emergencia (desde dashboard o PWA ciudadana)."""
    eid = await engine.add_emergency(
        body.latitude, body.longitude, body.title, body.emergencyType or "medical",
        description=body.description,
    )
    return {"ok": True, "id": eid, "state": _state_merged()}


@app.post("/api/sim/jam")
async def create_jam(body: JamBody) -> dict:
    """Registra zona de atasco (polígono con ≥3 vértices)."""
    jid = await engine.add_jam(body.polygon)
    return {"ok": True, "id": jid, "state": _state_merged()}


@app.post("/api/sim/jam/point")
async def create_jam_point(body: JamPointBody) -> dict:
    """Crea un atasco cuadrado ``radiusM × radiusM`` centrado en ``(lat, lon)``."""
    half = body.radiusM / 2.0
    poly = _square_polygon_around(body.latitude, body.longitude, half)
    jid = await engine.add_jam(poly)
    return {"ok": True, "id": jid, "state": _state_merged()}


@app.post("/api/sim/assign")
async def assign_emergency(body: AssignBody) -> dict:
    """Asignación manual (operador) unidad ↔ emergencia."""
    ok = await engine.assign_emergency(body.ambulanceId, body.emergencyId)
    return {"ok": ok, "state": _state_merged()}


@app.post("/api/sim/poi")
async def add_poi(body: PoiBody) -> dict:
    """Añade POI de tipo ``place`` al mapa (hospital, gasolinera, custom)."""
    try:
        pid = await engine.add_poi(body.kind, body.name, body.latitude, body.longitude)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": pid, "state": _state_merged()}


@app.delete("/api/sim/ambulance/{ambulance_id}")
async def delete_ambulance(ambulance_id: str) -> dict[str, Any]:
    """Elimina una unidad del mapa; libera su emergencia asignada si la hubiere."""
    ok = await engine.remove_ambulance(ambulance_id)
    return {"ok": ok, "state": _state_merged()}


@app.delete("/api/sim/companion/{companion_id}")
async def delete_companion(companion_id: str) -> dict[str, Any]:
    """Elimina un companion (heli, policía) del mapa."""
    ok = await engine.remove_companion(companion_id)
    return {"ok": ok, "state": _state_merged()}


@app.delete("/api/sim/emergency/{emergency_id}")
async def delete_emergency(emergency_id: str) -> dict[str, Any]:
    """Cancela y elimina una emergencia; libera unidades y companions asociados."""
    ok = await engine.remove_emergency(emergency_id)
    return {"ok": ok, "state": _state_merged()}


@app.delete("/api/sim/poi/{poi_id}")
async def delete_poi(poi_id: str) -> dict[str, Any]:
    """Elimina un POI (hospital/gasolinera/lugar custom) del mapa."""
    ok = await engine.remove_poi(poi_id)
    return {"ok": ok, "state": _state_merged()}


@app.delete("/api/sim/jam/{jam_id}")
async def delete_jam(jam_id: str) -> dict[str, Any]:
    """Elimina una zona de atasco del mapa."""
    ok = await engine.remove_jam(jam_id)
    return {"ok": ok, "state": _state_merged()}


class TrainingModeBody(BaseModel):
    enabled: bool
    ratePerMin: float | None = Field(default=None, ge=0.1, le=60.0)


@app.post("/api/sim/training-mode")
async def set_training_mode(body: TrainingModeBody) -> dict[str, Any]:
    """Activa/desactiva el modo simulación autónoma para entrenar ML.

    Con modo ON el motor:
      - Fuerza `paused=False` y `dispatchRequiresApproval=False`.
      - Genera emergencias periódicas (rate configurable).
      - Bootstrap auto de POIs + flota mínima si el mapa está vacío.
      - Abre una nueva ``simulation_session`` para particionar el dataset.
    """
    res = await engine.set_training_mode(body.enabled, body.ratePerMin)
    return {"ok": True, **res, "state": _state_merged()}


@app.post("/api/telemetry/ingest")
async def telemetry_ingest(body: dict[str, Any]) -> dict[str, Any]:
    """Fallback Nivel 3: recibe telemetría por HTTPS cuando MQTT/P2P no están disponibles."""
    global _last_http_ingest
    _last_http_ingest = {"receivedAt": _iso(), "payload": body}
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entity types CRUD
# ---------------------------------------------------------------------------


class EntityTypeBody(BaseModel):
    name: str
    kind: str = Field(default="vehicle")
    speedKmh: float | None = Field(default=None)
    color: str = Field(default="#94a3b8")
    iconSvg: str | None = Field(default=None)
    description: str | None = Field(default=None)
    capabilities: list[str] | str | None = Field(default=None)
    source: str | None = Field(default=None)
    # Catálogo económico/operativo (solo vehículos)
    powertrain: Literal["combustion", "electric", "unique"] | None = Field(default=None)
    crewMin: int | None = Field(default=None, ge=0, le=20)
    crewMax: int | None = Field(default=None, ge=0, le=20)
    costPerMin: float | None = Field(default=None, ge=0)
    activationCost: float | None = Field(default=None, ge=0)


class EntityTypePatchBody(BaseModel):
    name: str | None = None
    speedKmh: float | None = None
    color: str | None = None
    iconSvg: str | None = None
    description: str | None = None
    capabilities: list[str] | None = None
    powertrain: Literal["combustion", "electric", "unique"] | None = None
    crewMin: int | None = Field(default=None, ge=0, le=20)
    crewMax: int | None = Field(default=None, ge=0, le=20)
    costPerMin: float | None = Field(default=None, ge=0)
    activationCost: float | None = Field(default=None, ge=0)


@app.get("/api/sim/entity-types")
async def list_entity_types() -> list[dict[str, Any]]:
    """Catálogo completo de tipos (builtin + custom + IA)."""
    return engine.get_entity_types()


def _coerce_capabilities(caps: Any) -> list[str]:
    """Normaliza ``capabilities`` (list, CSV string, None) a ``list[str]``."""
    if caps is None: return []
    if isinstance(caps, list): return [str(c).strip() for c in caps if str(c).strip()]
    if isinstance(caps, str):
        return [p.strip() for p in caps.split(",") if p.strip()]
    return []


@app.post("/api/sim/entity-types")
async def create_entity_type(body: EntityTypeBody) -> dict[str, Any]:
    """Registra tipo custom; si falta ``description``/``capabilities`` las genera la IA."""
    data = body.model_dump()
    data["capabilities"] = _coerce_capabilities(data.get("capabilities"))
    # Si falta descripción o capacidades, la IA las calcula a partir del nombre.
    if not data.get("description") or not data.get("capabilities"):
        from .fleet_meta import generate_description_and_capabilities
        desc, caps = await generate_description_and_capabilities(data["name"], data.get("kind", "vehicle"))
        if not data.get("description"): data["description"] = desc
        if not data.get("capabilities"): data["capabilities"] = caps
    tid = engine.register_entity_type(data)
    return {"ok": True, "id": tid, "description": data["description"], "capabilities": data["capabilities"]}


@app.patch("/api/sim/entity-types/{type_id}")
async def update_entity_type(type_id: str, body: EntityTypePatchBody) -> dict[str, Any]:
    """Parchea un tipo custom; regenera desc/caps con IA si se vacían explícitamente."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "capabilities" in patch:
        patch["capabilities"] = _coerce_capabilities(patch["capabilities"])
    # Si el usuario vacía desc/caps explícitamente, también los regeneramos
    # basándonos en el (posible nuevo) nombre del tipo.
    needs_desc = ("description" in patch and not patch["description"]) or ("description" not in patch and False)
    needs_caps = ("capabilities" in patch and not patch["capabilities"])
    if needs_desc or needs_caps:
        t = next((x for x in engine.get_entity_types() if x["id"] == type_id), None)
        name = patch.get("name") or (t["name"] if t else type_id)
        kind = (t or {}).get("kind", "vehicle")
        from .fleet_meta import generate_description_and_capabilities
        desc, caps = await generate_description_and_capabilities(name, kind)
        if needs_desc: patch["description"] = desc
        if needs_caps: patch["capabilities"] = caps
    ok = engine.update_entity_type(type_id, patch)
    return {"ok": ok, "patch": patch}


@app.delete("/api/sim/entity-types/{type_id}")
async def delete_entity_type(type_id: str) -> dict[str, Any]:
    """Elimina tipo in-memory y de Supabase (si estaba persistido)."""
    try:
        from .supabase_client import get_supabase
        sb = get_supabase()
        if sb is not None:
            sb.table("fleet_entity_types").delete().eq("id", type_id).execute()
    except Exception:
        pass
    ok = engine.remove_entity_type(type_id)
    return {"ok": ok}


# ---------------------------------------------------------------------------
# Dispatch config
# ---------------------------------------------------------------------------


class DispatchConfigBody(BaseModel):
    dispatchRequiresApproval: bool


@app.get("/api/sim/dispatch-config")
async def get_dispatch_config() -> dict[str, Any]:
    """Devuelve si el auto-dispatch requiere aprobación previa del operador."""
    return {"dispatchRequiresApproval": engine.dispatch_requires_approval}


@app.post("/api/sim/dispatch-config")
async def set_dispatch_config(body: DispatchConfigBody) -> dict[str, Any]:
    """Activa/desactiva que nuevas emergencias generen propuesta HITL."""
    engine.dispatch_requires_approval = body.dispatchRequiresApproval
    return {"ok": True, "dispatchRequiresApproval": engine.dispatch_requires_approval}


# ---------------------------------------------------------------------------
# AI bulk incident generator
# ---------------------------------------------------------------------------


class GenerateIncidentsBody(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    areaLatCenter: float | None = Field(default=None)
    areaLonCenter: float | None = Field(default=None)


class GenerateScenarioBody(BaseModel):
    hospitals: int = Field(default=0, ge=0, le=30)
    gasStations: int = Field(default=0, ge=0, le=30)
    ambulances: int = Field(default=0, ge=0, le=40)
    incidents: int = Field(default=0, ge=0, le=30)
    clearExisting: bool = Field(default=False)
    extraByType: dict[str, int] | None = Field(default=None)
    areaLatCenter: float | None = None
    areaLonCenter: float | None = None


@app.post("/api/sim/generate-scenario")
async def generate_scenario(body: GenerateScenarioBody) -> dict[str, Any]:
    """Genera un escenario completo: estructuras + flota + incidencias iniciales."""
    import random as _rnd

    if body.clearExisting:
        await engine.reset_simulation()

    spawn_lat, spawn_lon = default_spawn()
    lat_c = body.areaLatCenter or spawn_lat
    lon_c = body.areaLonCenter or spawn_lon

    def _rand_pos(spread: float) -> tuple[float, float]:
        return lat_c + _rnd.uniform(-spread, spread), lon_c + _rnd.uniform(-spread, spread)

    placed = {"hospitals": 0, "gasStations": 0, "ambulances": 0, "incidents": 0, "extras": {}}

    # Estructuras: primero los hospitales reales de la región (si los hay).
    known_hospitals = list(get_active_region().hospitals) if body.areaLatCenter is None else []
    for i in range(body.hospitals):
        if i < len(known_hospitals):
            name, lat, lon = known_hospitals[i]
        else:
            name = f"Hospital {i + 1}"
            lat, lon = _rand_pos(0.025)
        try:
            await engine.add_poi("hospital", name, lat, lon)
            placed["hospitals"] += 1
        except Exception:
            pass
    for i in range(body.gasStations):
        lat, lon = _rand_pos(0.03)
        try:
            await engine.add_poi("gas_station", f"Gasolinera {i + 1}", lat, lon)
            placed["gasStations"] += 1
        except Exception:
            pass

    # Ambulancias estándar
    for i in range(body.ambulances):
        lat, lon = _rand_pos(0.015)
        try:
            await engine.spawn_ambulance(
                lat, lon,
                entity_type_id=engine.default_ambulance_entity_type_id(),
                display_label=f"AMB-{i + 1:03d}",
            )
            placed["ambulances"] += 1
        except Exception:
            pass

    # Unidades extra por tipo personalizado
    if body.extraByType:
        for type_id, count in body.extraByType.items():
            n = int(count or 0)
            if n <= 0:
                continue
            et = next((t for t in engine.get_entity_types() if t["id"] == type_id and t.get("kind") == "vehicle"), None)
            if not et:
                continue
            name_prefix = et["name"][:10].upper().replace(" ", "")
            for i in range(n):
                lat, lon = _rand_pos(0.02)
                try:
                    await engine.spawn_ambulance(
                        lat, lon,
                        entity_type_id=type_id,
                        display_label=f"{name_prefix}-{i + 1:02d}",
                    )
                    placed["extras"][type_id] = placed["extras"].get(type_id, 0) + 1
                except Exception:
                    pass

    # Incidencias iniciales (reutiliza el generador existente)
    if body.incidents > 0:
        try:
            res = await generate_incidents(GenerateIncidentsBody(
                count=body.incidents,
                areaLatCenter=lat_c,
                areaLonCenter=lon_c,
            ))
            placed["incidents"] = len(res.get("incidents", []))
        except Exception:
            pass

    return {"ok": True, "placed": placed, "state": _state_merged()}


@app.post("/api/sim/generate-incidents")
async def generate_incidents(body: GenerateIncidentsBody) -> dict[str, Any]:
    """Crea ``count`` emergencias realistas (LLM si disponible, sino pool fallback)."""
    import random as _rnd

    spawn_lat, spawn_lon = default_spawn()
    lat_c = body.areaLatCenter or spawn_lat
    lon_c = body.areaLonCenter or spawn_lon
    count = body.count

    incidents: list[dict[str, Any]] = []

    try:
        from . import llm_provider
        if llm_provider.is_llm_available():
            schema = {
                "type": "object",
                "properties": {
                    "incidents": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": count,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "emergencyType": {
                                    "type": "string",
                                    "enum": ["medical", "altercation", "mass_casualty"],
                                },
                                "latOffset": {"type": "number", "minimum": -0.015, "maximum": 0.015},
                                "lonOffset": {"type": "number", "minimum": -0.015, "maximum": 0.015},
                            },
                            "required": ["title", "description", "emergencyType", "latOffset", "lonOffset"],
                        },
                    },
                },
                "required": ["incidents"],
            }
            prompt = (
                f"Genera exactamente {count} incidencias realistas para una simulación de "
                f"ambulancias cerca de ({lat_c:.4f}, {lon_c:.4f}). Varía severidad y tipo."
            )
            data = await llm_provider.chat_completion_json(
                [
                    {"role": "system", "content": "Generador de incidencias de emergencia en español."},
                    {"role": "user", "content": prompt},
                ],
                schema=schema,
                temperature=0.8,
                max_tokens=1024,
            )
            for item in (data.get("incidents") or [])[:count]:
                lat = lat_c + float(item.get("latOffset", _rnd.uniform(-0.01, 0.01)))
                lon = lon_c + float(item.get("lonOffset", _rnd.uniform(-0.01, 0.01)))
                eid = await engine.add_emergency(
                    lat, lon,
                    item.get("title", "Incidencia IA"),
                    item.get("emergencyType", "medical"),
                    description=item.get("description"),
                )
                incidents.append({
                    "id": eid,
                    "title": item.get("title"),
                    "emergencyType": item.get("emergencyType"),
                })
    except Exception:
        import logging as _log
        _log.getLogger(__name__).exception("LLM incidents generation failed; using fallback")

    if not incidents:
        _fallback_titles = [
            ("Accidente de trafico", "Colision multiple en interseccion concurrida", "medical"),
            ("Persona inconsciente", "Viandante desplomado en la acera sin respuesta", "medical"),
            ("Altercado callejero", "Pelea con arma blanca, varios heridos leves", "altercation"),
            ("Incendio residencial", "Vivienda en llamas con posibles atrapados", "mass_casualty"),
            ("Atropello peatonal", "Peaton atropellado en paso de cebra", "medical"),
            ("Caida desde altura", "Trabajador caido desde andamio, traumatismo severo", "medical"),
            ("Intoxicacion masiva", "Intoxicacion alimentaria en evento con 20+ afectados", "mass_casualty"),
            ("Riña multitudinaria", "Altercado con heridos en zona de ocio nocturno", "altercation"),
            ("Paro cardiaco", "Hombre de 55 anios en parada cardiorespiratoria en gimnasio", "medical"),
            ("Accidente laboral", "Electrocucion en obra, victima inconsciente", "medical"),
            ("Derrumbe parcial", "Derrumbe de fachada con heridos bajo escombros", "mass_casualty"),
            ("Agresion con arma", "Disparo en via publica, herido grave", "altercation"),
        ]
        selected = _rnd.sample(_fallback_titles, min(count, len(_fallback_titles)))
        if count > len(selected):
            selected = selected + [_rnd.choice(_fallback_titles) for _ in range(count - len(selected))]
        for title, desc, etype in selected[:count]:
            lat = lat_c + _rnd.uniform(-0.012, 0.012)
            lon = lon_c + _rnd.uniform(-0.012, 0.012)
            eid = await engine.add_emergency(lat, lon, title, etype, description=desc)
            incidents.append({"id": eid, "title": title, "emergencyType": etype})

    return {"ok": True, "generated": len(incidents), "incidents": incidents}


# ---------------------------------------------------------------------------
# Crisis trigger endpoint (demo)
# ---------------------------------------------------------------------------


class CrisisBody(BaseModel):
    kind: str


@app.post("/api/sim/crisis")
async def trigger_crisis(body: CrisisBody) -> dict[str, Any]:
    """Desencadena crisis de demo: ``altercation``, ``mass_casualty``, ``eta_exceeded``."""
    import random

    kind = body.kind
    ambs = engine.ambulances
    emergencies = engine.emergencies

    spawn_lat, spawn_lon = default_spawn()

    if kind == "altercation":
        lat = spawn_lat + random.uniform(-0.008, 0.008)
        lon = spawn_lon + random.uniform(-0.008, 0.008)
        eid = await engine.add_emergency(lat, lon, "Altercado con heridos", "altercation")
        return {"ok": True, "message": f"Altercado creado ({eid[:8]}). IA despachara policia + ambulancia."}

    elif kind == "mass_casualty":
        lat = spawn_lat + random.uniform(-0.01, 0.01)
        lon = spawn_lon + random.uniform(-0.01, 0.01)
        eid = await engine.add_emergency(lat, lon, "Victimas masivas", "mass_casualty")
        return {"ok": True, "message": f"Victimas masivas ({eid[:8]}). IA evaluara helicoptero."}

    elif kind == "eta_exceeded":
        active = [e for e in emergencies if e.get("status") == "assigned"]
        if not active:
            lat = spawn_lat + random.uniform(-0.02, 0.02)
            lon = spawn_lon + random.uniform(-0.02, 0.02)
            eid = await engine.add_emergency(lat, lon, "Emergencia lejana (ETA critico)", "medical")
            return {"ok": True, "message": f"Emergencia lejana creada ({eid[:8]}). IA despachara helicoptero si ETA > 5 min."}
        em = random.choice(active)
        async with engine._lock:
            for amb in ambs:
                if str(amb.get("id")) == str(em.get("assignedAmbulanceId")):
                    amb["routeProgressM"] = 0.0
                    lat_far = float(em.get("latitude", 0)) + random.uniform(0.03, 0.06)
                    lon_far = float(em.get("longitude", 0)) + random.uniform(0.03, 0.06)
                    amb["latitude"] = lat_far
                    amb["longitude"] = lon_far
                    amb["routeCoords"] = [[lat_far, lon_far], [float(em["latitude"]), float(em["longitude"])]]
                    break
        return {"ok": True, "message": f"ETA de ambulancia para {em.get('title', '?')} artificialmente extendido."}

    return {"ok": False, "message": f"Tipo de crisis desconocido: {kind}"}


# ---------------------------------------------------------------------------
# AI HITL endpoints
# ---------------------------------------------------------------------------


@app.get("/api/ai/proposals")
async def get_ai_proposals() -> list[dict[str, Any]]:
    """Lista propuestas IA pendientes (solo se usan en modo HITL)."""
    return [_format_proposal(p) for p in ai_engine.get_pending()]


class ResolveBody(BaseModel):
    action: Literal["approved", "rejected"]


@app.post("/api/ai/proposals/{proposal_id}/resolve")
async def resolve_proposal(proposal_id: str, body: ResolveBody) -> dict[str, Any]:
    """El operador aprueba o rechaza una propuesta HITL."""
    ok = await ai_engine.resolve_proposal(proposal_id, body.action)
    return {"ok": ok}


# ---------------------------------------------------------------------------
# AI mode endpoints
# ---------------------------------------------------------------------------


class AIModeBody(BaseModel):
    mode: Literal["hitl", "autonomous"]


@app.get("/api/ai/mode")
async def get_ai_mode() -> dict[str, str]:
    """Modo actual de la IA (``"hitl"`` o ``"autonomous"``)."""
    return {"mode": ai_engine.mode}


@app.post("/api/ai/mode")
async def set_ai_mode(body: AIModeBody) -> dict[str, Any]:
    """Cambia modo IA; al pasar a autónomo auto-ejecuta las propuestas pendientes."""
    prev = ai_engine.mode
    ai_engine.mode = body.mode
    auto_resolved = 0
    # Al pasar a autónomo: ejecuta inmediatamente todas las pendientes que había
    # en HITL para que no queden "en espera". IA asume el control total.
    if prev != "autonomous" and body.mode == "autonomous":
        pending = [p for p in ai_engine.pending_proposals if p.get("status") == "pending"]
        for p in pending:
            try:
                await ai_engine.resolve_proposal(p["id"], "approved")
                auto_resolved += 1
            except Exception:
                pass
    return {"ok": True, "mode": ai_engine.mode, "autoResolved": auto_resolved}


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


class ChatBody(BaseModel):
    message: str
    sessionId: str


@app.post("/api/chat")
async def chat_endpoint(body: ChatBody) -> StreamingResponse:
    """Stream SSE del chat RAG: responde tokens LLM sobre la base protocolos."""
    history = get_chat_history(body.sessionId, limit=20)
    hist_msgs = [{"role": h["role"], "content": h["content"]} for h in history]

    async def gen() -> AsyncIterator[str]:
        async for chunk in chat_stream(body.message, body.sessionId, hist_msgs):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: {\"done\": true}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/chat/history")
async def chat_history_endpoint(sessionId: str = Query(...)) -> list[dict[str, Any]]:
    """Devuelve el historial completo de la sesión del chat."""
    return get_chat_history(sessionId)


class CommandBody(BaseModel):
    text: str


class ShiftReportBody(BaseModel):
    windowMinutes: int = Field(default=180, ge=5, le=1440)


@app.post("/api/ai/shift-report")
async def ai_shift_report(body: ShiftReportBody) -> dict[str, Any]:
    """Genera un informe operativo LLM para la ventana indicada."""
    from .shift_report_service import generate_shift_report
    snapshot = engine.get_state_payload()
    return await generate_shift_report(body.windowMinutes, snapshot)


@app.get("/api/ai/shift-reports")
async def ai_shift_reports(limit: int = Query(default=10, ge=1, le=50)) -> list[dict[str, Any]]:
    """Lista los últimos informes guardados en ``ai_shift_reports``."""
    from .shift_report_service import list_recent_reports
    return await list_recent_reports(limit)


class MLPredictBody(BaseModel):
    ambulanceId: str


@app.get("/api/ml/health")
async def ml_health_endpoint() -> dict[str, Any]:
    """Healthcheck del servicio ONNX (``ml-service``)."""
    from .ml_client import ml_health
    return await ml_health()


@app.post("/api/ml/predict/fleet-anomaly")
async def ml_predict_fleet(body: MLPredictBody) -> dict[str, Any]:
    """Puntuación de anomalía (modelo ``fleet_anomaly``) para una unidad."""
    from .ml_client import predict_fleet_anomaly
    amb = next((a for a in engine.ambulances if str(a["id"]) == body.ambulanceId), None)
    if amb is None:
        raise HTTPException(404, "ambulance not found")
    return await predict_fleet_anomaly(amb)


@app.post("/api/ml/predict/fleet-anomaly/all")
async def ml_predict_fleet_all() -> dict[str, Any]:
    """Puntuación de anomalía para toda la flota (bulk)."""
    from .ml_client import predict_fleet_anomaly
    results = []
    for amb in engine.ambulances:
        res = await predict_fleet_anomaly(amb)
        results.append({"ambulanceId": amb["id"], **res})
    return {"ok": True, "count": len(results), "results": results}


@app.get("/api/ml/export")
async def ml_export(
    hours: int = Query(default=6, ge=1, le=168),
    format: str = Query(default="csv", pattern="^(csv|parquet|jsonl)$"),
) -> StreamingResponse:
    from .ml_client import export_features
    data, content_type = await export_features(hours=hours, format=format)
    ext = {"csv": "csv", "parquet": "parquet", "jsonl": "jsonl"}[format]
    filename = f"sentinel_features_{hours}h.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(iter([data]), media_type=content_type, headers=headers)


@app.get("/api/ml/predictions")
async def ml_recent_predictions(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    from .supabase_client import get_supabase
    sb = get_supabase()
    if sb is None:
        return []
    try:
        r = sb.table("ml_predictions").select("*").order("predicted_at", desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        return []


@app.post("/api/ai/command")
async def ai_command(body: CommandBody) -> dict[str, Any]:
    """Interpreta un comando en lenguaje natural con tool-calling estructurado."""
    from .command_service import interpret
    context = {
        "pois": [
            {"name": p.get("name"), "kind": p.get("kind"), "latitude": p.get("latitude"), "longitude": p.get("longitude")}
            for p in engine.pois
        ],
        "entityTypes": engine.get_entity_types(),
    }
    return await interpret(body.text, context)


@app.post("/api/knowledge/seed")
async def knowledge_seed_endpoint() -> dict[str, Any]:
    count = await seed_knowledge_force()
    return {"ok": True, "chunksSeeded": count}
