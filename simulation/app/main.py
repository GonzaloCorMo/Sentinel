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
from .weather_source import run_weather_source
from .schemas.external_events import ExternalEvent as ExternalEventIn
from .schemas.external_events import WeatherReading as WeatherReadingIn
from .weather_db import upsert_weather_reading
from .regions import get_active_region, get_active_region_id, list_regions, set_active_region
from .route_nav import haversine_m
from .schemas.telemetry import telemetry_schema_json
from .supabase_client import is_supabase_available

engine = SimulationEngine()
ai_engine = AIDecisionEngine(engine)
engine.set_ai_engine(ai_engine)
_last_http_ingest: dict[str, Any] | None = None


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    event_source_task = asyncio.create_task(run_event_source(engine))
    weather_task = asyncio.create_task(run_weather_source(engine))
    ai_task = asyncio.create_task(ai_engine.observe_loop())
    yield
    ai_engine.stop()
    ai_task.cancel()
    event_source_task.cancel()
    weather_task.cancel()
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
        await weather_task
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


class UnitMessageBody(BaseModel):
    """Mensaje de la central a una unidad (``unitId`` vacío = todas)."""

    model_config = {"extra": "forbid"}
    unitId: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=280)


def _station_display_name(station_id: str) -> str:
    """Nombre legible de una estación sin POI (las simuladas se llaman ``mock-ws-N``)."""
    sid = str(station_id)
    if sid.startswith("mock-ws-"):
        return f"Estación meteorológica {sid.removeprefix('mock-ws-')}"
    return f"Estación {sid[:8]}"


class JamPointBody(BaseModel):
    latitude: float
    longitude: float
    radiusM: float = Field(default=70.0, ge=25.0, le=400.0)


class HealthResponse(BaseModel):
    status: str = Field(description="Status of the service")


async def _weather_station_rows() -> list[dict[str, Any]]:
    """Construye la lista de estaciones conocidas fusionando tres fuentes:

    1. POIs `weather_station` del mapa (tienen nombre y coordenadas).
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
            "name": str(poi.get("name") or "Estación meteorológica"),
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
                "name": _station_display_name(station_id),
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
                    "name": _station_display_name(station_id),
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
async def openapi_yaml() -> Response:
    yaml_spec = yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=False)
    return Response(content=yaml_spec, media_type="text/plain; charset=utf-8")


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


@app.get("/api/region/summary", tags=["region"])
async def region_summary() -> dict[str, Any]:
    """Resumen operativo de la región activa para el monitor regional.

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
        # De dónde salen las lecturas: "meteogalicia" (reales) o "mock" (sintéticas).
        "source": "meteogalicia" if getattr(engine, "weather_source_status", {}).get("ok") else "mock",
        "lastReadingAt": max((str(r.get("timestamp") or "") for r in weather.values()), default=None) or None,
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
    active = [e for e in ems if e.get("status") in ("pending", "assigned", "on_scene")]
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
    )
    return {"ok": True, "id": aid, "state": _state_merged()}


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


@app.post("/api/comms/messages")
async def send_unit_message(body: UnitMessageBody) -> dict[str, Any]:
    """Envía un mensaje de la central a una unidad o a toda la flota."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="El mensaje está vacío")
    try:
        msg = await engine.send_unit_message(body.unitId or None, text)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True, "message": msg}


@app.get("/api/comms/messages")
async def list_unit_messages(unitId: str | None = None) -> dict[str, Any]:
    """Mensajes recientes; con ``unitId`` solo los de esa unidad y los generales."""
    msgs = [dict(m) for m in engine.unit_messages]
    if unitId:
        msgs = [m for m in msgs if m["unitId"] in (None, unitId)]
    return {"messages": msgs[-100:]}


@app.post("/api/comms/messages/{message_id}/ack")
async def ack_unit_message(message_id: str) -> dict[str, Any]:
    """La unidad confirma que ha leído el mensaje."""
    return {"ok": engine.ack_unit_message(message_id)}


@app.post("/api/sim/jam/point")
async def create_jam_point(body: JamPointBody) -> dict:
    """Corta el tramo de la calle pulsada (unos ``radiusM × 3`` metros a lo largo de la vía).

    Si no hay calle cerca o OSRM no responde, cae al cuadrado clásico
    ``radiusM × radiusM`` centrado en el punto.
    """
    from .placement import road_segment

    seg = await road_segment(engine._http, body.latitude, body.longitude, length_m=max(80.0, body.radiusM * 3.0))
    if seg is not None:
        poly = [[a, b] for a, b in seg.polygon]
    else:
        poly = _square_polygon_around(body.latitude, body.longitude, body.radiusM / 2.0)
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
    # Sin valor: tasa equilibrada según la flota. Con valor: tasa fija.
    ratePerMin: float | None = Field(default=None, ge=0.01, le=60.0)


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


def _callsign_prefix(type_id: str, type_name: str) -> str:
    """Prefijo corto del indicativo según el tipo de unidad (AMB, BOMB, POL…)."""
    t = f"{type_id} {type_name}".lower()
    for keys, prefix in (
        (("ambul",), "AMB"), (("fire", "bomb"), "BOMB"), (("polic", "patrol"), "POL"),
        (("civil",), "PC"), (("heli",), "HELI"), (("dron",), "DRON"), (("moto",), "MOTO"),
    ):
        if any(k in t for k in keys):
            return prefix
    letters = "".join(ch for ch in type_name.upper() if ch.isalnum())
    return letters[:4] or "UNID"


@app.post("/api/sim/generate-scenario")
async def generate_scenario(body: GenerateScenarioBody) -> dict[str, Any]:
    """Genera un escenario completo con lugares reales y elementos sobre calles.

    - Hospitales y gasolineras: primero los reales de la región
      (OpenStreetMap); si se piden más, en calles reales cerca del centro.
    - Unidades: salen de hospitales y bases reales (bomberos del parque de
      bomberos, policía de comisarías), ajustadas a la calle de salida.
    - Incidencias: catálogo realista, en calles con nombre.
    """
    import random as _rnd
    from .placement import random_road_point, snap_to_road
    from .regions import region_places

    if body.clearExisting:
        await engine.reset_simulation()

    region = get_active_region()
    custom_area = body.areaLatCenter is not None and body.areaLonCenter is not None
    center = (body.areaLatCenter, body.areaLonCenter) if custom_area else region.center
    http = engine._http
    placed: dict[str, Any] = {"hospitals": 0, "gasStations": 0, "ambulances": 0, "incidents": 0, "extras": {}}

    def _by_distance(rows: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
        return sorted(rows, key=lambda r: haversine_m(center[0], center[1], r[1], r[2]))

    async def _road_near(lat: float, lon: float, spread_m: float = 0.0) -> tuple[float, float]:
        """Punto de calle junto a un lugar (con algo de dispersión si se pide)."""
        if spread_m > 0:
            p = await random_road_point(http, (lat, lon), spread_m, rng=_rnd, require_named=False, max_snap_m=60.0, tries=8)
            return p.lat, p.lon
        p = await snap_to_road(http, lat, lon, max_snap_m=300.0)
        return (p.lat, p.lon) if p else (lat, lon)

    # Estructuras.
    hospitals = _by_distance(list(region.hospitals)) if not custom_area else []
    for i in range(body.hospitals):
        if i < len(hospitals):
            name, lat, lon = hospitals[i]
        else:
            pt = await random_road_point(http, center, region.urban_sigma_m, rng=_rnd)
            name, lat, lon = f"Centro sanitario {i + 1}", pt.lat, pt.lon
        try:
            await engine.add_poi("hospital", name, lat, lon)
            placed["hospitals"] += 1
        except Exception:
            pass
    fuel = _by_distance(region_places(region.id, "fuel_stations"))
    for i in range(body.gasStations):
        if i < len(fuel):
            name, lat, lon = fuel[i]
        else:
            pt = await random_road_point(http, center, region.urban_sigma_m, rng=_rnd)
            name, lat, lon = "", pt.lat, pt.lon
        try:
            await engine.add_poi("gas_station", name or f"Gasolinera {i + 1}", lat, lon)
            placed["gasStations"] += 1
        except Exception:
            pass

    # Bases de salida de las unidades.
    placed_hospitals = [(p.get("name", ""), float(p["latitude"]), float(p["longitude"]))
                        for p in engine.pois if p.get("kind") == "hospital"]
    ems_bases = placed_hospitals + _by_distance(region_places(region.id, "ambulance_bases"))
    fire_bases = _by_distance(region_places(region.id, "fire_stations")) or ems_bases
    police_bases = _by_distance(region_places(region.id, "police_stations"))[:6] or ems_bases

    def _bases_for(type_id: str, type_name: str) -> list[tuple[str, float, float]]:
        t = f"{type_id} {type_name}".lower()
        if "fire" in t or "bomb" in t:
            return fire_bases
        if "polic" in t or "patrol" in t:
            return police_bases
        return ems_bases

    async def _spawn(type_id: str, label: str, bases: list[tuple[str, float, float]], k: int) -> bool:
        if bases:
            _, blat, blon = bases[k % len(bases)]
            # La primera unidad de cada base en la puerta; el resto, en calles próximas.
            lat, lon = await _road_near(blat, blon, spread_m=0.0 if k < len(bases) else 250.0)
        else:
            pt = await random_road_point(http, center, region.urban_sigma_m * 0.5, rng=_rnd)
            lat, lon = pt.lat, pt.lon
        try:
            await engine.spawn_ambulance(lat, lon, entity_type_id=type_id, display_label=label)
            return True
        except Exception:
            return False

    for i in range(body.ambulances):
        if await _spawn(engine.default_ambulance_entity_type_id(), f"AMB-{i + 1:03d}", ems_bases, i):
            placed["ambulances"] += 1

    if body.extraByType:
        for type_id, count in body.extraByType.items():
            n = int(count or 0)
            if n <= 0:
                continue
            et = next((t for t in engine.get_entity_types() if t["id"] == type_id and t.get("kind") == "vehicle"), None)
            if not et:
                continue
            name_prefix = _callsign_prefix(type_id, et.get("name", ""))
            bases = _bases_for(type_id, et.get("name", ""))
            for i in range(n):
                if await _spawn(type_id, f"{name_prefix}-{i + 1:02d}", bases, i):
                    placed["extras"][type_id] = placed["extras"].get(type_id, 0) + 1

    if body.incidents > 0:
        res = await generate_incidents(GenerateIncidentsBody(
            count=body.incidents,
            areaLatCenter=center[0] if custom_area else None,
            areaLonCenter=center[1] if custom_area else None,
        ))
        placed["incidents"] = len(res.get("incidents", []))

    return {"ok": True, "placed": placed, "state": _state_merged()}


@app.post("/api/sim/generate-incidents")
async def generate_incidents(body: GenerateIncidentsBody) -> dict[str, Any]:
    """Crea ``count`` emergencias del catálogo realista, cada una en una calle real."""
    center = None
    if body.areaLatCenter is not None and body.areaLonCenter is not None:
        center = (body.areaLatCenter, body.areaLonCenter)
    incidents: list[dict[str, Any]] = []
    for _ in range(body.count):
        eid = await engine._spawn_catalog_emergency("scenario", center=center)
        if eid:
            em = next((e for e in engine.emergencies if e.get("id") == eid), {})
            incidents.append({"id": eid, "title": em.get("title"), "emergencyType": em.get("emergencyType")})
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


class PatientReportBody(BaseModel):
    model_config = {"extra": "forbid"}
    ambulanceId: str = Field(min_length=1, max_length=64)


@app.post("/api/ai/patient-report")
async def ai_patient_report(body: PatientReportBody) -> dict[str, Any]:
    """Informe ISBAR del paciente que atiende una unidad (datos simulados), con la IA local."""
    from .patient_report_service import generate_patient_report

    ctx = engine.patient_context(body.ambulanceId)
    if ctx is None:
        raise HTTPException(status_code=404, detail="La unidad no atiende a ningún paciente")
    return await generate_patient_report(ctx)


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
