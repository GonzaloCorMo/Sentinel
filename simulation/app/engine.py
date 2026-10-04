"""Motor de simulación del gemelo digital.

Este módulo define `SimulationEngine`, el núcleo que mantiene el estado global
de la simulación (flota, POIs, emergencias, atascos, telemetría) y orquesta
todos los ciclos de vida asíncronos (tick principal, despacho, repostaje,
staging, rerouting por atascos).

Colaboradores:
    - `engines.TelemetryComposite`: genera la telemetría v2.0 por tick.
    - `ai_decision_engine.AIDecisionEngine`: observa anomalías y dispara
      acciones (HITL o autónomo). Inyectado vía `set_ai_engine`.
    - `telemetry_writer.TelemetryWriter`: persiste batches a Supabase.
    - `routing`, `route_nav`: interacción OSRM y avance por polilíneas.

Arquitectura de concurrencia:
    Todas las mutaciones al estado compartido se hacen dentro de
    `async with self._lock`. Las llamadas LLM/HTTP externas se realizan fuera
    del lock para no bloquear el event loop.

Fields typical:
    - `ambulances`: lista dicts (ver `_default_amb_fields`).
    - `companions`: vehículos de apoyo despachados por IA (heli, policía).
    - `emergencies`: incidencias `pending|assigned|resolved`.
    - `pois`: puntos (hospitales, gasolineras, bases custom).
    - `jams`: zonas de atasco (polígonos).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from .ambulance_fsm import (
    AmbulanceState,
    can_accept_mission,
    count_ambs_near_hospital,
    estimate_trip_m,
    infer_fsm_state,
)
from .channels import ChannelRouter
from .engines import TelemetryComposite
from .event_writer import EventWriter
from .telemetry_writer import FLUSH_EVERY_N_TICKS, TelemetryWriter
from .geometry_poly import point_in_polygon, polyline_intersects_polygon
from .route_nav import advance_along_polyline, haversine_m, heading_deg_towards, remaining_route_coords
from .dispatch_scoring import (
    ScoringContext,
    ScoringRegistry,
    compute_weather_factor,
    default_registry,
)
from . import emergency_catalog as ecat
from .regions import get_active_region
from .placement import random_road_point, road_segment, snap_to_road
from .routing import detour_candidates_from_jam, fetch_route, osrm_base_url, table_durations, typical_limit_kmh

FUEL_LOW_PCT = 25.0
IDLE_REFUEL_FUEL_PCT = 20.0
IDLE_REFUEL_COOLDOWN_TICKS = 120
ROUTE_SPEED_MS = 12.0
# Consumo realista: depósito de ~80 L y ~16 L/100 km → ~0,2 % por km;
# batería de ambulancia eléctrica con ~200 km de autonomía → 0,5 % por km.
FUEL_DRAIN_PCT_PER_M = 0.00022
BATTERY_DRAIN_PCT_PER_M = 0.0005
# En parado el motor sigue encendido para el equipamiento sanitario.
FUEL_IDLE_PCT_PER_S = 0.0004
BATTERY_IDLE_PCT_PER_S = 0.0008
# Dinámica del vehículo.
FALLBACK_SPEED_MS = 9.0          # sin datos de vía (ruta en línea recta)
ACCEL_MS2 = 2.0                  # aceleración cómoda de una furgoneta cargada
DECEL_MS2 = 3.0                  # frenada de servicio
TURN_SPEED_MS = 6.0              # ~22 km/h en giros cerrados
JAM_SPEED_MS = 2.5               # circulación dentro de un atasco
JAM_SPEED_URGENT_MS = 5.0        # con sirena, abriéndose paso
REFUEL_SECONDS = 300.0           # repostar o recargar en la estación
# Fases con duración fija: la unidad está parada hasta `phaseUntil` (s simulados).
TIMED_PHASES = ("on_scene", "at_hospital", "refueling")
JAM_REROUTE_COOLDOWN_TICKS = 15
STAGING_RADIUS_M = 1200.0
STAGING_COOLDOWN_TICKS = 100

_logger = logging.getLogger(__name__)


def _heading_delta_deg(prev: float | None, new: float) -> float:
    if prev is None:
        return 0.0
    return (new - prev + 180.0) % 360.0 - 180.0


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Centro de spawn por defecto: derivado dinámicamente de la región activa
# (`regions.get_active_region`). Se usa como centro de offsets para los
# generadores (escenario, crisis, training auto-bootstrap). El stack arranca
# SIN POIs ni flota sembrados: el operador construye el escenario con el
# generador IA (/api/sim/generate-scenario), el constructor manual, o clicks
# en el mapa.
def default_spawn() -> tuple[float, float]:
    """Centro de spawn (lat, lon) de la región activa."""
    from .regions import get_active_region
    r = get_active_region()
    return (r.spawn_lat, r.spawn_lon)

DEFAULT_POIS: list[dict[str, Any]] = []


def default_pois_copy() -> list[dict[str, Any]]:
    """Copia del set por defecto de POIs. Vacío — no se siembra nada al init."""
    return [dict(p) for p in DEFAULT_POIS]


class SimulationEngine:
    """Estado global + bucle principal de la simulación.

    Mantiene la flota, POIs, emergencias, jams y orquesta el tick asíncrono
    que actualiza telemetría, avanza rutas, reencamina, despacha companions
    y coordina con el observador de IA.

    Attributes:
        speed_multiplier (float): Factor x1–x20 aplicado al dt de cada tick.
        paused (bool): Estado play/pause. `run_loop` omite avances si True.
        ambulances (list[dict]): Unidades activas. Cada una con campos tipo
            `id`, `latitude`, `longitude`, `missionPhase`, `routeCoords`,
            `telemetry`, `poweredOff`, etc.
        companions (list[dict]): Vehículos auxiliares despachados por IA.
        emergencies (list[dict]): Incidencias abiertas o resueltas.
        pois (list[dict]): Lugares (hospital, gas_station, tipos custom).
        jams (list[dict]): Zonas con tráfico/atasco (polígonos lat-lon).
        entity_types (list[dict]): Catálogo de tipos (builtin + custom + IA).
        tick (int): Contador monotónico de ticks procesados.
        _lock (asyncio.Lock): Serializa mutaciones del estado.
        _telemetry (TelemetryComposite): Agregador de los 5 motores.
        _tele_writer (TelemetryWriter): Buffer/flush a Supabase.
        _ai_engine (AIDecisionEngine | None): Inyectado vía `set_ai_engine`.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._running = False
        self._stop = asyncio.Event()
        self.speed_multiplier = 1.0
        self.paused = True
        self.network = {"mqtt": True, "p2p": True, "http": True}
        self.ambulances: list[dict[str, Any]] = []
        self.emergencies: list[dict[str, Any]] = []
        self.companions: list[dict[str, Any]] = []
        self.pois: list[dict[str, Any]] = default_pois_copy()
        self.jams: list[dict[str, Any]] = []
        self.external_jams: list[dict[str, Any]] = []
        self._training_spawning = 0
        self.tick = 0
        # Segundos simulados desde el arranque (tiempos en el lugar, entregas…).
        self.sim_time_s = 0.0
        self.dispatch_requires_approval: bool = False
        self._ai_engine: Any = None
        self.entity_types: list[dict[str, Any]] = [
            {"id": "police_combustion", "kind": "vehicle", "name": "Policía Combustión", "speedKmh": 80, "color": "#2563eb", "iconSvg": None, "builtIn": True, "powertrain": "combustion", "crewMin": 2, "crewMax": 2, "costPerMin": 1.20, "activationCost": 15},
            {"id": "police_electric", "kind": "vehicle", "name": "Policía Eléctrico", "speedKmh": 80, "color": "#2563eb", "iconSvg": None, "builtIn": True, "powertrain": "electric", "crewMin": 2, "crewMax": 2, "costPerMin": 0.80, "activationCost": 18},
            {"id": "ambulance_combustion", "kind": "vehicle", "name": "Ambulancia Combustión", "speedKmh": 80, "color": "#8a8f98", "iconSvg": None, "builtIn": True, "powertrain": "combustion", "crewMin": 2, "crewMax": 3, "costPerMin": 2.50, "activationCost": 25},
            {"id": "ambulance_electric", "kind": "vehicle", "name": "Ambulancia Eléctrico", "speedKmh": 80, "color": "#8a8f98", "iconSvg": None, "builtIn": True, "powertrain": "electric", "crewMin": 2, "crewMax": 3, "costPerMin": 1.80, "activationCost": 30},
            {"id": "firetruck_combustion", "kind": "vehicle", "name": "Bomberos Combustión", "speedKmh": 70, "color": "#dc2626", "iconSvg": None, "builtIn": True, "powertrain": "combustion", "crewMin": 4, "crewMax": 6, "costPerMin": 4.00, "activationCost": 50},
            {"id": "firetruck_electric", "kind": "vehicle", "name": "Bomberos Eléctrico", "speedKmh": 70, "color": "#dc2626", "iconSvg": None, "builtIn": True, "powertrain": "electric", "crewMin": 4, "crewMax": 6, "costPerMin": 3.00, "activationCost": 60},
            {"id": "civil_protection_combustion", "kind": "vehicle", "name": "Protección Civil Combustión", "speedKmh": 70, "color": "#f59e0b", "iconSvg": None, "builtIn": True, "powertrain": "combustion", "crewMin": 1, "crewMax": 2, "costPerMin": 0.80, "activationCost": 10},
            {"id": "civil_protection_electric", "kind": "vehicle", "name": "Protección Civil Eléctrico", "speedKmh": 70, "color": "#f59e0b", "iconSvg": None, "builtIn": True, "powertrain": "electric", "crewMin": 1, "crewMax": 2, "costPerMin": 0.50, "activationCost": 12},
            {"id": "drone_unique", "kind": "vehicle", "name": "Dron", "speedKmh": 200, "color": "#06b6d4", "iconSvg": None, "builtIn": True, "powertrain": "unique", "crewMin": 0, "crewMax": 0, "costPerMin": 0.30, "activationCost": 5},
            {"id": "hospital", "kind": "place", "name": "Hospital", "color": "#8b5cf6", "iconSvg": None, "builtIn": True},
            {"id": "gas_station", "kind": "place", "name": "Gasolinera", "color": "#0ea5e9", "iconSvg": None, "builtIn": True},
        ]
        self._comms_log: deque[dict[str, Any]] = deque(maxlen=400)
        # Mensajes de la central a las unidades (más recientes al final).
        self.unit_messages: deque[dict[str, Any]] = deque(maxlen=200)
        self._comms_seq = 0
        self.channels = ChannelRouter(comms_callback=self._record_comms)
        self._telemetry = TelemetryComposite()
        self._http = httpx.AsyncClient(timeout=20.0)
        self._tele_writer = TelemetryWriter()
        self._events = EventWriter()
        # Tracking de lifecycle por emergencia para armar mission_outcomes:
        #   emergency_id → {created_at, dispatched_at, arrived_on_scene_at,
        #                   eta_predicted_s, trip_predicted_m, jams_crossed,
        #                   rerouted_times, fuel_at_dispatch, ambulance_id,
        #                   companions_dispatched, decision_id}
        self._mission_tracking: dict[str, dict[str, Any]] = {}
        # Modo simulación autónoma: generador continuo de emergencias para
        # alimentar el pipeline ML. Controlado por endpoint.
        self.training_mode: bool = False
        self.training_emergencies_per_min: float = 3.0
        self._training_last_spawn_tick: int = 0
        self._resolved_emergencies = 0
        self.dispatch_scoring: ScoringRegistry = default_registry()
        self._external_dispatched_ids: set[str] = set()
        self._external_em_window: deque[float] = deque(maxlen=120)
        self.osrm_routing: dict[str, Any] = {
            "ready": False,
            "baseUrl": osrm_base_url(),
            "checkedAt": None,
            "readySince": None,
            "lastError": None,
        }
        self.event_source_status: dict[str, Any] = {
            "enabled": False,
            "status": "idle",
            "source": None,
            "lastConsumeAt": None,
            "consumedTotal": 0,
            "consumedEvents": 0,
            "consumedWeather": 0,
            "lastError": None,
        }
        self.external_events: list[dict[str, Any]] = []
        self._external_events_index: dict[str, int] = {}
        self._external_event_jam_ids: set[str] = set()
        self.weather_by_station: dict[str, dict[str, Any]] = {}
        self._weather_history: dict[str, deque[dict[str, Any]]] = {}
        self._weather_override_until: float = 0.0

    def set_osrm_routing_status(
        self,
        *,
        ready: bool,
        base_url: str,
        checked_at: str,
        last_error: str | None,
    ) -> None:
        """Actualiza el health check de OSRM que ve el dashboard.

        El health-check corre en background y llama aquí con el resultado.
        Si OSRM vuelve a estar ``ready`` tras un fallo, se conserva el
        instante ``readySince`` para que la UI muestre cuánto lleva OK.

        Args:
            ready: True si OSRM responde correctamente al último check.
            base_url: URL del servicio OSRM (útil cuando hay varios).
            checked_at: ISO-8601 del instante del check.
            last_error: Texto del último error o None si OK.
        """
        prev = bool(self.osrm_routing.get("ready"))
        self.osrm_routing["ready"] = ready
        self.osrm_routing["baseUrl"] = base_url
        self.osrm_routing["checkedAt"] = checked_at
        self.osrm_routing["lastError"] = last_error
        if ready and not prev:
            self.osrm_routing["readySince"] = checked_at
        if not ready:
            self.osrm_routing["readySince"] = None

    def _record_comms(self, entry: dict[str, Any]) -> None:
        self._comms_seq += 1
        entry["seq"] = self._comms_seq
        entry["at"] = _iso()
        self._comms_log.append(entry)

    async def aclose(self) -> None:
        await self._tele_writer.flush()
        await self._events.flush()
        self._events.close_session(
            total_ticks=self.tick,
            total_emergencies=len(self.emergencies),
            total_resolved=self._resolved_emergencies,
        )
        await self._http.aclose()

    def set_ai_engine(self, ai_eng: Any) -> None:
        self._ai_engine = ai_eng

    def _entity_type_by_id(self, tid: str) -> dict[str, Any] | None:
        for t in self.entity_types:
            if t["id"] == tid:
                return t
        return None

    def _powertrain_for_amb(self, amb: dict[str, Any]) -> str:
        """Resuelve propulsión: 'combustion' | 'electric' | 'unique'.

        Falla a 'combustion' si el tipo no la declara (compat IDs legacy).
        """
        et = self._entity_type_by_id(str(amb.get("entityTypeId") or "ambulance"))
        if et and et.get("powertrain") in ("combustion", "electric", "unique"):
            return str(et["powertrain"])
        return "combustion"

    def _refuel_kind_for_amb(self, amb: dict[str, Any]) -> str:
        """POI donde recargar: gas_station (combustion) o charging_station (electric/unique)."""
        return "gas_station" if self._powertrain_for_amb(amb) == "combustion" else "charging_station"

    def _companion_callsign_prefix(self, kind: str) -> str:
        et = self._entity_type_by_id(kind)
        if et and et.get("name"):
            alnum = re.sub(r"[^A-Za-z0-9]", "", str(et["name"]))
            if len(alnum) >= 2:
                return alnum[:4].upper()
        slug = re.sub(r"[^a-zA-Z0-9]", "", kind.replace("_", ""))
        return (slug[:4] if len(slug) >= 2 else "UN").upper()

    def default_ambulance_entity_type_id(self) -> str:
        """Preferred ambulance-like type id in the current catalog."""
        preferred = ("ambulance_combustion", "ambulance_electric", "ambulance")
        ids = {str(t.get("id")) for t in self.entity_types if t.get("kind") == "vehicle"}
        for pid in preferred:
            if pid in ids:
                return pid
        return "ambulance_combustion"

    def _next_companion_serial(self, kind: str) -> int:
        return sum(1 for c in self.companions if c.get("kind") == kind) + 1

    def _powertrain_of(self, amb: dict[str, Any]) -> str:
        et_id = str(amb.get("entityTypeId") or "")
        et = self._entity_type_by_id(et_id) if et_id else None
        return str((et or {}).get("powertrain") or "combustion")

    def _needs_energy_refill(self, amb: dict[str, Any], threshold_pct: float) -> bool:
        pt = self._powertrain_of(amb)
        if pt == "electric":
            return float(amb.get("batteryLevel", 100.0)) < threshold_pct
        return float(amb.get("fuelLevel", 100.0)) < threshold_pct

    def _apply_energy_refill(self, amb: dict[str, Any], tele: dict[str, Any] | None = None) -> None:
        pt = self._powertrain_of(amb)
        mech = tele.get("mechanical") if tele else None
        if pt == "electric":
            amb["batteryLevel"] = 100.0
            amb["fuelLevel"] = 0.0
            if isinstance(mech, dict):
                mech["batteryPct"] = 100.0
                mech["fuelLevelPct"] = 0.0
        else:
            amb["fuelLevel"] = 100.0
            amb["batteryLevel"] = 0.0
            if isinstance(mech, dict):
                mech["fuelLevelPct"] = 100.0
                mech["batteryPct"] = 0.0

    def _normalize_energy_by_powertrain(self, amb: dict[str, Any], tele: dict[str, Any] | None = None) -> None:
        pt = self._powertrain_of(amb)
        mech = tele.get("mechanical") if tele else None
        if pt == "electric":
            amb["fuelLevel"] = 0.0
            if isinstance(mech, dict):
                mech["fuelLevelPct"] = 0.0
        else:
            amb["batteryLevel"] = 0.0
            if isinstance(mech, dict):
                mech["batteryPct"] = 0.0

    def _mission_resources_for_check(self, amb: dict[str, Any]) -> tuple[float, float]:
        pt = self._powertrain_of(amb)
        if pt == "electric":
            return (100.0, float(amb.get("batteryLevel", 100.0)))
        return (float(amb.get("fuelLevel", 100.0)), 100.0)

    def register_entity_type(self, data: dict[str, Any]) -> str:
        """Añade un tipo de entidad custom al catálogo (y persiste en Supabase).

        El frontend lo usa para que operadores o la IA puedan crear
        ambulancias / vehículos / lugares fuera del set builtin.

        Args:
            data: Dict con ``kind`` (``"vehicle"`` | ``"place"``), ``name``,
                ``color``, ``iconSvg`` opcional, ``description``,
                ``capabilities``, ``speedKmh`` (solo vehicles), ``source``
                (``"manual"`` | ``"ai"``).

        Returns:
            Id corto (8 chars) del tipo registrado.
        """
        tid = str(uuid4())[:8]
        entry: dict[str, Any] = {
            "id": tid,
            "kind": data.get("kind", "vehicle"),
            "name": data.get("name", "Custom"),
            "color": data.get("color", "#94a3b8"),
            "iconSvg": data.get("iconSvg"),
            "description": data.get("description") or None,
            "capabilities": data.get("capabilities") or [],
            "builtIn": False,
        }
        if entry["kind"] == "vehicle":
            entry["speedKmh"] = float(data.get("speedKmh", 60))
            # Catálogo económico/operativo (solo vehículos).
            if data.get("powertrain"): entry["powertrain"] = data["powertrain"]
            if data.get("crewMin") is not None: entry["crewMin"] = int(data["crewMin"])
            if data.get("crewMax") is not None: entry["crewMax"] = int(data["crewMax"])
            if data.get("costPerMin") is not None: entry["costPerMin"] = float(data["costPerMin"])
            if data.get("activationCost") is not None: entry["activationCost"] = float(data["activationCost"])
        self.entity_types.append(entry)
        try:
            from .supabase_client import get_supabase
            sb = get_supabase()
            if sb is not None:
                row: dict[str, Any] = {
                    "id": tid,
                    "kind": entry["kind"],
                    "name": entry["name"],
                    "speed_kmh": entry.get("speedKmh"),
                    "color": entry["color"],
                    "icon_svg": entry.get("iconSvg"),
                    "description": entry["description"],
                    "capabilities": entry["capabilities"],
                    "built_in": False,
                    "source": data.get("source") or "manual",
                }
                if entry["kind"] == "vehicle":
                    if "powertrain" in entry: row["powertrain"] = entry["powertrain"]
                    if "crewMin" in entry: row["crew_min"] = entry["crewMin"]
                    if "crewMax" in entry: row["crew_max"] = entry["crewMax"]
                    if "costPerMin" in entry: row["cost_per_min"] = entry["costPerMin"]
                    if "activationCost" in entry: row["activation_cost"] = entry["activationCost"]
                sb.table("fleet_entity_types").upsert(row).execute()
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning("no se pudo persistir entity_type %s: %s", tid, _e)
        return tid

    def update_entity_type(self, type_id: str, patch: dict[str, Any]) -> bool:
        """Parchea un tipo custom existente (no toca builtin).

        Args:
            type_id: Id devuelto por `register_entity_type`.
            patch: Claves subset de ``name``, ``color``, ``iconSvg``,
                ``description``, ``capabilities``, ``speedKmh``.

        Returns:
            True si encontró y actualizó el tipo. False si no existe o es
            builtin (no editable).
        """
        t = self._entity_type_by_id(type_id)
        if not t or t.get("builtIn"):
            return False
        # Actualiza en memoria
        for k in ("name", "color", "iconSvg", "description", "capabilities"):
            if k in patch and patch[k] is not None:
                t[k] = patch[k]
        if "speedKmh" in patch and patch["speedKmh"] is not None and t.get("kind") == "vehicle":
            t["speedKmh"] = float(patch["speedKmh"])
        # Catálogo económico/operativo (solo vehículos)
        if t.get("kind") == "vehicle":
            if "powertrain" in patch and patch["powertrain"] is not None:
                t["powertrain"] = patch["powertrain"]
            if "crewMin" in patch and patch["crewMin"] is not None:
                t["crewMin"] = int(patch["crewMin"])
            if "crewMax" in patch and patch["crewMax"] is not None:
                t["crewMax"] = int(patch["crewMax"])
            if "costPerMin" in patch and patch["costPerMin"] is not None:
                t["costPerMin"] = float(patch["costPerMin"])
            if "activationCost" in patch and patch["activationCost"] is not None:
                t["activationCost"] = float(patch["activationCost"])
        # Persiste
        try:
            from .supabase_client import get_supabase
            sb = get_supabase()
            if sb is not None:
                row_patch: dict[str, Any] = {}
                if "name" in patch: row_patch["name"] = patch["name"]
                if "color" in patch: row_patch["color"] = patch["color"]
                if "iconSvg" in patch: row_patch["icon_svg"] = patch["iconSvg"]
                if "description" in patch: row_patch["description"] = patch["description"]
                if "capabilities" in patch: row_patch["capabilities"] = patch["capabilities"]
                if "speedKmh" in patch and patch["speedKmh"] is not None:
                    row_patch["speed_kmh"] = float(patch["speedKmh"])
                if t.get("kind") == "vehicle":
                    if "powertrain" in patch and patch["powertrain"] is not None:
                        row_patch["powertrain"] = patch["powertrain"]
                    if "crewMin" in patch and patch["crewMin"] is not None:
                        row_patch["crew_min"] = int(patch["crewMin"])
                    if "crewMax" in patch and patch["crewMax"] is not None:
                        row_patch["crew_max"] = int(patch["crewMax"])
                    if "costPerMin" in patch and patch["costPerMin"] is not None:
                        row_patch["cost_per_min"] = float(patch["costPerMin"])
                    if "activationCost" in patch and patch["activationCost"] is not None:
                        row_patch["activation_cost"] = float(patch["activationCost"])
                if row_patch:
                    row_patch["updated_at"] = "now()"
                    sb.table("fleet_entity_types").update(row_patch).eq("id", type_id).execute()
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning("no se pudo actualizar entity_type %s: %s", type_id, _e)
        return True

    async def load_custom_entity_types(self) -> int:
        """Carga tipos persistidos en Supabase: tanto custom (built_in=false)
        como los seeded por migración SQL (built_in=true) que extienden el
        catálogo más allá de `BUILTIN_ENTITY_TYPES` hardcoded.
        """
        from .supabase_client import get_supabase
        sb = get_supabase()
        if sb is None:
            return 0
        try:
            resp = sb.table("fleet_entity_types").select("*").execute()
            rows = resp.data or []
        except Exception:
            return 0
        existing_ids = {t["id"] for t in self.entity_types}
        loaded = 0
        for r in rows:
            if r["id"] in existing_ids:
                continue
            entry: dict[str, Any] = {
                "id": r["id"],
                "kind": r.get("kind", "vehicle"),
                "name": r.get("name", "Custom"),
                "color": r.get("color") or "#94a3b8",
                "iconSvg": r.get("icon_svg"),
                "description": r.get("description"),
                "capabilities": r.get("capabilities") or [],
                "builtIn": bool(r.get("built_in", False)),
            }
            if entry["kind"] == "vehicle":
                entry["speedKmh"] = float(r.get("speed_kmh") or 60)
                if r.get("powertrain") is not None: entry["powertrain"] = r["powertrain"]
                if r.get("crew_min") is not None: entry["crewMin"] = int(r["crew_min"])
                if r.get("crew_max") is not None: entry["crewMax"] = int(r["crew_max"])
                if r.get("cost_per_min") is not None: entry["costPerMin"] = float(r["cost_per_min"])
                if r.get("activation_cost") is not None: entry["activationCost"] = float(r["activation_cost"])
            self.entity_types.append(entry)
            loaded += 1
        return loaded

    def remove_entity_type(self, type_id: str) -> bool:
        """Elimina el tipo del catálogo en memoria.

        No borra en Supabase (la persistencia se hace desde la API). Las
        entidades existentes con ese tipo siguen vivas pero huérfanas de
        metadata — la UI les asigna color/icon por defecto.

        Returns:
            True si el tipo existía y fue retirado.
        """
        t = self._entity_type_by_id(type_id)
        if not t:
            return False
        self.entity_types = [e for e in self.entity_types if e["id"] != type_id]
        return True

    def get_entity_types(self) -> list[dict[str, Any]]:
        """Copia defensiva del catálogo de tipos (builtin + custom + IA)."""
        return [t.copy() for t in self.entity_types]

    def get_state_payload(self) -> dict[str, Any]:
        """Snapshot completo del estado para el dashboard (vía SSE o HTTP).

        Serializa flota, emergencias, POIs, jams, companions, comms
        recientes, stats agregados, estado OSRM y catálogo de tipos en un
        solo dict JSON-friendly. Todas las listas son copias superficiales
        para que el consumidor pueda mutarlas sin afectar al motor.
        """
        return {
            "connected": True,
            "updatedAt": _iso(),
            "isSimulating": self._running,
            "paused": self.paused,
            "simTimeS": round(self.sim_time_s, 1),
            "motorState": "RUNNING" if not self.paused else "PAUSED",
            "networkStatus": self.network.copy(),
            "linkState": self.channels.active_link.value,
            "ambulances": [self._ambulance_public(a) for a in self.ambulances],
            "emergencies": [e.copy() for e in self.emergencies],
            "pois": [p.copy() for p in self.pois],
            "jams": [j.copy() for j in self.jams] + [j.copy() for j in self.external_jams],
            "companions": [c.copy() for c in self.companions],
            "commsRecent": list(self._comms_log)[-80:],
            "unitMessages": list(self.unit_messages)[-60:],
            "stats": {
                "totalAmbulances": len(self.ambulances),
                "activeEmergencies": sum(1 for e in self.emergencies if e.get("status") != "resolved"),
                "resolvedEmergencies": self._resolved_emergencies,
                "simulationSpeed": self.speed_multiplier,
                "tickCount": self.tick,
            },
            "osrmRouting": dict(self.osrm_routing),
            "entityTypes": [t.copy() for t in self.entity_types],
            "dispatchRequiresApproval": self.dispatch_requires_approval,
            "trainingMode": self.training_mode,
            "trainingRatePerMin": self.training_emergencies_per_min,
            "sessionId": self._events.session_id,
            "externalEvents": [e.copy() for e in self.external_events[-200:] if not e.get("resolved_at")],
            "weatherStations": {k: v.copy() for k, v in self.weather_by_station.items()},
            "eventSourceStatus": dict(self.event_source_status),
            "externalEmergencyRatePerMin": round(self._recent_external_emergency_rate_per_min(), 2),
        }

    def _ambulance_public(self, amb: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in amb.items() if not str(k).startswith("_")}
        return out.copy()

    def _dt(self) -> float:
        return max(0.05, 0.5 / max(self.speed_multiplier, 0.1))

    def _has_active_emergency_work(self) -> bool:
        return any(e.get("status") in ("pending", "assigned", "on_scene") for e in self.emergencies)

    def _nearest_poi(self, amb: dict[str, Any], kind: str) -> dict[str, Any] | None:
        lat0 = float(amb.get("latitude") or 0.0)
        lon0 = float(amb.get("longitude") or 0.0)
        best: dict[str, Any] | None = None
        best_d = float("inf")
        for p in self.pois:
            if p.get("kind") != kind:
                continue
            d = haversine_m(lat0, lon0, float(p["latitude"]), float(p["longitude"]))
            if d < best_d:
                best_d = d
                best = p
        return best

    def _emergency_by_id(self, eid: str) -> dict[str, Any] | None:
        for e in self.emergencies:
            if str(e.get("id")) == eid:
                return e
        return None

    def _jam_tuples(self) -> list[list[tuple[float, float]]]:
        out: list[list[tuple[float, float]]] = []
        for jam in (*self.jams, *self.external_jams):
            poly = jam.get("polygon") or []
            if len(poly) >= 3:
                out.append([(float(p[0]), float(p[1])) for p in poly])
        return out

    def _weather_station_coords(self) -> dict[str, tuple[float, float]]:
        """Map station_id (POI id) → (lat, lon) for any POI of kind weather_station."""
        out: dict[str, tuple[float, float]] = {}
        for p in self.pois:
            if p.get("kind") != "weather_station":
                continue
            try:
                out[str(p.get("id"))] = (float(p["latitude"]), float(p["longitude"]))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _dispatch_scoring_context(self) -> ScoringContext:
        return ScoringContext(
            weather=self.weather_by_station,
            weather_station_coords=self._weather_station_coords(),
            external_jams=self.external_jams,
            jams=self.jams,
        )

    def weather_speed_factor(self, lat: float, lon: float) -> float:
        """Public accessor for ETA + per-tick speed degradation.

        Returns 1.0 when no weather data is available so existing
        clean-weather behavior is preserved.
        """
        return compute_weather_factor(lat, lon, self._dispatch_scoring_context())

    def _count_route_jam_crossings(self, route_pts: list[tuple[float, float]]) -> int:
        n = 0
        for tup in self._jam_tuples():
            if polyline_intersects_polygon(route_pts, tup):
                n += 1
        return n

    async def _route_with_jam_avoidance(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> tuple[list[tuple[float, float]], float | None, float | None]:
        """Calcula ruta OSRM intentando evitar atascos registrados.

        Primer intento directo; si la polilínea cruza algún polígono de
        ``self.jams`` o ``self.external_jams``, prueba rutas con
        `waypoints` candidatos alrededor de los polígonos (hasta 5
        iteraciones). Siempre devuelve la ruta con menos cruces aunque
        no consiga cero.

        Returns:
            ``(coords, road_speed_limit_kmh | None, duration_s | None)``.
        """
        coords, lim, duration = await fetch_route(self._http, [start, end])
        if len(coords) < 2:
            return coords, lim, duration
        route_pts = [(float(c[0]), float(c[1])) for c in coords]
        if self._count_route_jam_crossings(route_pts) == 0:
            return coords, lim, duration

        best_coords, best_lim, best_duration = coords, lim, duration
        best_cross = self._count_route_jam_crossings(route_pts)

        for _ in range(5):
            first_hit: list[tuple[float, float]] | None = None
            for tup in self._jam_tuples():
                if polyline_intersects_polygon(route_pts, tup):
                    first_hit = tup
                    break
            if first_hit is None:
                return coords, lim, duration

            improved = False
            for via in detour_candidates_from_jam(first_hit):
                new_coords, new_lim, new_duration = await fetch_route(self._http, [start, via, end])
                if len(new_coords) < 2:
                    continue
                npts = [(float(c[0]), float(c[1])) for c in new_coords]
                crosses = self._count_route_jam_crossings(npts)
                if crosses == 0:
                    return new_coords, new_lim, new_duration
                if crosses < best_cross:
                    best_cross = crosses
                    best_coords = new_coords
                    best_lim = new_lim
                    best_duration = new_duration
                    improved = True

            if not improved:
                return best_coords, best_lim, best_duration
            coords, lim, duration = best_coords, best_lim, best_duration
            route_pts = [(float(c[0]), float(c[1])) for c in coords]

        return best_coords, best_lim, best_duration

    async def _rank_by_eta(
        self,
        scored: list[tuple[float, dict[str, float], dict[str, Any]]],
        dest: tuple[float, float],
        top_n: int = 6,
    ) -> list[tuple[float, dict[str, float], dict[str, Any]]]:
        """Reordena las mejores candidatas por tiempo real de llegada (OSRM ``/table``).

        La puntuación previa (distancia en línea recta, tiempo, atascos)
        preselecciona ``top_n``; entre ellas gana la que antes llega por
        carretera. Si OSRM no responde, se mantiene el orden previo.
        """
        if len(scored) < 2:
            return scored
        head, tail = scored[:top_n], scored[top_n:]
        sources = [(float(a["latitude"]), float(a["longitude"])) for _, _, a in head]
        durations = await table_durations(self._http, sources, dest)
        if not durations:
            return scored
        ranked = sorted(
            zip(head, durations),
            key=lambda pair: pair[1] if pair[1] is not None else float("inf"),
        )
        for (_, breakdown, _), dur in ranked:
            if dur is not None:
                breakdown["eta_s"] = round(dur, 1)
        return [c for c, _ in ranked] + tail

    async def _best_hospital(self, amb: dict[str, Any]) -> dict[str, Any] | None:
        """Hospital al que antes se llega por carretera (``/table``); el más cercano si falla."""
        hospitals = [p for p in self.pois if p.get("kind") == "hospital"]
        if len(hospitals) <= 1:
            return hospitals[0] if hospitals else None
        start = (float(amb["latitude"]), float(amb["longitude"]))
        durations = await table_durations(
            self._http, [(float(h["latitude"]), float(h["longitude"])) for h in hospitals], start,
        )
        # `/table` mide hospital → unidad; en ciudad la asimetría es pequeña.
        if durations and any(d is not None for d in durations):
            best = min(zip(hospitals, durations), key=lambda hd: hd[1] if hd[1] is not None else float("inf"))
            return best[0]
        return self._nearest_poi(amb, "hospital")

    async def _dispatch_emergency(self, eid: str) -> None:
        """Asigna la unidad IDLE más adecuada a una emergencia pending.

        Algoritmo: ordena ambulancias por distancia Haversine al sitio;
        para cada candidata comprueba ``can_accept_mission`` (combustible
        + batería para el viaje estimado). La primera que puede, acepta.
        Si ninguna puede con fuel actual, el fallback envía la mejor a
        repostar y deja ``pendingEmergencyId`` para retomar tras refuel.

        Args:
            eid: UUID de la emergencia. Si no existe o ya no es pending,
                la función no hace nada.
        """
        em = self._emergency_by_id(eid)
        if not em or em.get("status") != "pending":
            return
        lat_e, lon_e = float(em["latitude"]), float(em["longitude"])
        dest = (lat_e, lon_e)
        # Snapshot de candidatas IDLE para el dataset de ranking ML.
        candidates_snap = self._snapshot_dispatch_candidates(lat_e, lon_e)
        ctx = self._dispatch_scoring_context()
        scored: list[tuple[float, dict[str, float], dict[str, Any]]] = []
        for amb in self.ambulances:
            if infer_fsm_state(amb) != AmbulanceState.IDLE:
                continue
            if amb.get("routeCoords"):
                continue
            total, breakdown = self.dispatch_scoring.score_candidate(amb, em, ctx)
            scored.append((total, breakdown, amb))
        scored.sort(key=lambda x: x[0], reverse=True)
        scored = await self._rank_by_eta(scored, dest)
        for total, breakdown, amb in scored:
            start = (float(amb["latitude"]), float(amb["longitude"]))
            trip_m = await estimate_trip_m(self._http, start, dest)
            fuel, batt = self._mission_resources_for_check(amb)
            if can_accept_mission(fuel, batt, trip_m):
                route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
                amb["assignedEmergencyId"] = eid
                amb["missionPhase"] = "to_emergency"
                amb["missionStatus"] = "EN_ROUTE"
                amb["fsmState"] = AmbulanceState.RESPONDING.value
                em["status"] = "assigned"
                em["assignedAmbulanceId"] = amb["id"]
                amb["routeCoords"] = route if len(route) >= 2 else None
                if rlim is not None:
                    amb["roadSpeedLimitKmh"] = rlim
                amb["routeProgressM"] = 0.0
                amb["routeSpeedMs"] = ROUTE_SPEED_MS
                amb["routeDurationS"] = route_duration_s
                amb["refuelPending"] = False
                amb["pendingEmergencyId"] = None
                tr = self._mission_tracking.setdefault(eid, {})
                tr["dispatch_score"] = round(total, 3)
                tr["dispatch_score_breakdown"] = breakdown
                self._track_dispatch(
                    eid, em, amb, trip_m=trip_m,
                    candidates=candidates_snap, source="engine_auto",
                )
                return
        for _, _, amb in scored:
            await self._start_refuel_survival(amb, pending_emergency_id=eid)
            amb["_stagingCooldown"] = self.tick + 40
            em["status"] = "assigned"
            em["assignedAmbulanceId"] = amb["id"]
            return

    def _emit_mission_outcome(self, eid: str, amb: dict[str, Any]) -> None:
        """Construye y emite la fila de `mission_outcomes` para un eid resuelto.

        Calcula response/transport/total time a partir de timestamps del
        tracking, ETA error comparando predicción vs real, y un score
        heurístico de calidad (menor ETA error y jams → mejor).
        """
        tr = self._mission_tracking.pop(eid, None)
        if not tr:
            return
        try:
            from datetime import datetime as _dt

            def _p(s: str | None) -> _dt | None:
                return _dt.fromisoformat(s.replace("Z", "+00:00")) if s else None

            created = _p(tr.get("created_at"))
            dispatched = _p(tr.get("dispatched_at"))
            on_scene = _p(tr.get("arrived_on_scene_at"))
            now = _dt.fromisoformat(_iso().replace("Z", "+00:00"))

            response_s = (on_scene - dispatched).total_seconds() if dispatched and on_scene else None
            transport_s = (now - on_scene).total_seconds() if on_scene else None
            total_s = (now - created).total_seconds() if created else None
            eta_pred = tr.get("eta_predicted_s")
            eta_err = (response_s - eta_pred) if response_s is not None and eta_pred is not None else None

            fuel_burn = None
            if tr.get("fuel_at_dispatch") is not None:
                fuel_burn = max(0.0, float(tr["fuel_at_dispatch"]) - float(amb.get("fuelLevel", 0)))

            # Calidad: 1.0 ideal (ETA perfecta, sin atascos, sin reroutes)
            quality = 1.0
            if eta_err is not None:
                quality -= min(0.5, abs(eta_err) / 600.0)
            quality -= min(0.3, tr.get("jams_crossed", 0) * 0.1)
            quality -= min(0.2, tr.get("rerouted_times", 0) * 0.05)
            quality = max(0.0, round(quality, 3))

            self._events.emit_outcome({
                "emergency_id": eid,
                "ambulance_id": tr.get("ambulance_id"),
                "created_at": tr.get("created_at"),
                "dispatched_at": tr.get("dispatched_at"),
                "arrived_on_scene_at": tr.get("arrived_on_scene_at"),
                "arrived_at_hospital_at": _iso(),
                "resolved_at": _iso(),
                "response_time_s": round(response_s, 1) if response_s else None,
                "transport_time_s": round(transport_s, 1) if transport_s else None,
                "total_time_s": round(total_s, 1) if total_s else None,
                "eta_predicted_s": eta_pred,
                "eta_error_s": round(eta_err, 1) if eta_err is not None else None,
                "trip_distance_m": tr.get("trip_predicted_m"),
                "fuel_burn_pct": round(fuel_burn, 2) if fuel_burn is not None else None,
                "battery_drain_pct": None,
                "emergency_type": tr.get("emergency_type"),
                "patient_severity": amb.get("patientSeverity"),
                "jams_crossed": tr.get("jams_crossed", 0),
                "rerouted_times": tr.get("rerouted_times", 0),
                "companions_dispatched": tr.get("companions_dispatched", []),
                "outcome_quality": quality,
            })
        except Exception:
            _logger.exception("No se pudo construir mission_outcome para %s", eid)

    def _snapshot_dispatch_candidates(
        self, dest_lat: float, dest_lon: float,
    ) -> list[dict[str, Any]]:
        """Resume las unidades IDLE al instante de la decisión de despacho.

        Es el input clave para un dispatch-ranker ML: dado este estado y
        la emergencia, ¿qué unidad es la mejor? El label vendrá luego del
        outcome (response_time_s, jams_crossed).
        """
        out: list[dict[str, Any]] = []
        for amb in self.ambulances:
            if infer_fsm_state(amb) != AmbulanceState.IDLE:
                continue
            d = haversine_m(
                float(amb.get("latitude", 0)), float(amb.get("longitude", 0)),
                dest_lat, dest_lon,
            )
            out.append({
                "id": str(amb["id"]),
                "lat": float(amb.get("latitude", 0)),
                "lon": float(amb.get("longitude", 0)),
                "fuel": float(amb.get("fuelLevel", 0)),
                "battery": float(amb.get("batteryLevel", 0)),
                "phase": str(amb.get("missionPhase", "idle")),
                "entityTypeId": amb.get("entityTypeId", self.default_ambulance_entity_type_id()),
                "distance_m": d,
                "on_route": bool(amb.get("routeCoords")),
            })
        return out

    def _track_dispatch(
        self,
        eid: str,
        em: dict[str, Any],
        amb: dict[str, Any],
        *,
        trip_m: float,
        candidates: list[dict[str, Any]],
        source: str,
    ) -> None:
        """Actualiza mission_tracking al despachar + emite decision + evento."""
        tr = self._mission_tracking.setdefault(eid, {
            "created_at": em.get("createdAt") or _iso(),
            "emergency_type": em.get("emergencyType", "medical"),
            "lat": float(em["latitude"]), "lon": float(em["longitude"]),
            "jams_crossed": 0, "rerouted_times": 0,
            "companions_dispatched": [],
        })
        tr["dispatched_at"] = _iso()
        tr["ambulance_id"] = str(amb["id"])
        weather_factor = self.weather_speed_factor(
            float(em["latitude"]), float(em["longitude"])
        )
        tr["weather_factor"] = round(weather_factor, 3)
        osrm_duration = amb.get("routeDurationS")
        if isinstance(osrm_duration, (int, float)) and osrm_duration > 0:
            baseline_s = float(osrm_duration)
        else:
            baseline_s = float(trip_m) / max(ROUTE_SPEED_MS, 0.1)
        tr["eta_predicted_s"] = round(baseline_s / max(weather_factor, 0.1), 1)
        tr["trip_predicted_m"] = float(trip_m)
        tr["fuel_at_dispatch"] = float(amb.get("fuelLevel", 0))
        route_pts = amb.get("routeCoords") or []
        tr["jams_crossed"] = self._count_route_jam_crossings(
            [(float(c[0]), float(c[1])) for c in route_pts]
        ) if route_pts else 0
        self._events.emit_event(
            "emergency", eid, "dispatched",
            payload={
                "ambulanceId": str(amb["id"]),
                "trip_m": float(trip_m),
                "eta_predicted_s": tr["eta_predicted_s"],
                "source": source,
            },
            actor="engine" if source == "engine_auto" else "operator",
            tick=self.tick,
        )
        self._events.emit_decision({
            "emergency_id": eid,
            "chosen_ambulance_id": str(amb["id"]),
            "candidate_count": len(candidates),
            "emergency_type": em.get("emergencyType", "medical"),
            "emergency_lat": float(em["latitude"]),
            "emergency_lon": float(em["longitude"]),
            "candidates": candidates,
            "decision_source": source,
        })

    async def _start_refuel_survival(
        self, amb: dict[str, Any], *, pending_emergency_id: str | None = None
    ) -> None:
        gas = self._nearest_poi(amb, self._refuel_kind_for_amb(amb))
        if not gas:
            return
        amb["missionPhase"] = "to_refuel"
        amb["refuelPoiId"] = gas["id"]
        amb["pendingEmergencyId"] = pending_emergency_id
        amb["assignedEmergencyId"] = None
        amb["refuelPending"] = True
        amb["fsmState"] = AmbulanceState.REFUELING.value
        start = (float(amb["latitude"]), float(amb["longitude"]))
        dest = (float(gas["latitude"]), float(gas["longitude"]))
        coords, lim, _ = await fetch_route(self._http, [start, dest])
        amb["routeCoords"] = coords if len(coords) >= 2 else None
        if lim is not None:
            amb["roadSpeedLimitKmh"] = lim
        amb["routeProgressM"] = 0.0
        amb["routeSpeedMs"] = ROUTE_SPEED_MS

    async def _maybe_idle_refuel(self, amb: dict[str, Any]) -> None:
        """IDLE sin ruta: si energía baja, ir a recargar (gas o carga eléctrica)."""
        if infer_fsm_state(amb) != AmbulanceState.IDLE:
            return
        if amb.get("routeCoords"):
            return
        if not self._needs_energy_refill(amb, IDLE_REFUEL_FUEL_PCT):
            return
        if int(amb.get("_idleRefuelCooldown", 0)) > self.tick:
            return
        gas = self._nearest_poi(amb, self._refuel_kind_for_amb(amb))
        if not gas:
            return
        amb["missionPhase"] = "to_refuel"
        amb["refuelPoiId"] = gas["id"]
        amb["pendingEmergencyId"] = None
        amb["assignedEmergencyId"] = None
        amb["refuelPending"] = True
        amb["fsmState"] = AmbulanceState.REFUELING.value
        start = (float(amb["latitude"]), float(amb["longitude"]))
        dest = (float(gas["latitude"]), float(gas["longitude"]))
        coords, lim, _ = await fetch_route(self._http, [start, dest])
        amb["routeCoords"] = coords if len(coords) >= 2 else None
        if lim is not None:
            amb["roadSpeedLimitKmh"] = lim
        amb["routeProgressM"] = 0.0
        amb["routeSpeedMs"] = ROUTE_SPEED_MS

    def _dist_to_nearest_hospital_m(self, amb: dict[str, Any]) -> float | None:
        hospitals = [p for p in self.pois if p.get("kind") == "hospital"]
        if not hospitals:
            return None
        lat0 = float(amb.get("latitude") or 0.0)
        lon0 = float(amb.get("longitude") or 0.0)
        return min(
            haversine_m(lat0, lon0, float(h["latitude"]), float(h["longitude"]))
            for h in hospitals
        )

    async def _maybe_staging_idle(self) -> None:
        if self._has_active_emergency_work():
            return
        hospitals = [p for p in self.pois if p.get("kind") == "hospital"]
        if not hospitals:
            return
        for amb in self.ambulances:
            if infer_fsm_state(amb) != AmbulanceState.IDLE:
                continue
            if amb.get("routeCoords"):
                continue
            d_h = self._dist_to_nearest_hospital_m(amb)
            if d_h is not None and d_h <= STAGING_RADIUS_M:
                continue
            if int(amb.get("_stagingCooldown", 0)) > self.tick:
                continue
            best_h = self._pick_staging_hospital(amb)
            if best_h:
                await self._start_staging(amb, best_h)
                amb["_stagingCooldown"] = self.tick + STAGING_COOLDOWN_TICKS

    async def _start_staging(self, amb: dict[str, Any], hospital: dict[str, Any]) -> None:
        start = (float(amb["latitude"]), float(amb["longitude"]))
        dest = (float(hospital["latitude"]), float(hospital["longitude"]))
        route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
        amb["missionPhase"] = "to_staging"
        amb["stagingHospitalId"] = hospital["id"]
        amb["missionStatus"] = "EN_ROUTE"
        amb["fsmState"] = AmbulanceState.STAGING.value
        amb["routeCoords"] = route if len(route) >= 2 else None
        if rlim is not None:
            amb["roadSpeedLimitKmh"] = rlim
        amb["routeProgressM"] = 0.0
        amb["routeSpeedMs"] = ROUTE_SPEED_MS

    async def _reroute_ambulance_if_hits_jam_polygon(
        self, amb: dict[str, Any], jam_tup: list[tuple[float, float]]
    ) -> None:
        """Recalcula ruta si el tramo que queda por recorrer cruza el polígono de atasco."""
        coords = amb.get("routeCoords")
        if not coords or len(coords) < 2:
            return
        prog = float(amb.get("routeProgressM", 0.0))
        remaining = remaining_route_coords(coords, prog)
        if len(remaining) < 2:
            return
        route_pts = [(float(c[0]), float(c[1])) for c in remaining]
        if not polyline_intersects_polygon(route_pts, jam_tup):
            return
        target = self._mission_target_latlon(amb)
        if not target:
            return
        start = (float(amb["latitude"]), float(amb["longitude"]))
        new_route, new_lim, _ = await self._route_with_jam_avoidance(start, target)
        if len(new_route) >= 2:
            amb["routeCoords"] = new_route
            if new_lim is not None:
                amb["roadSpeedLimitKmh"] = new_lim
            amb["routeProgressM"] = 0.0
            amb["_jamCooldown"] = self.tick + JAM_REROUTE_COOLDOWN_TICKS

    async def _maybe_reroute_jam(self, amb: dict[str, Any]) -> None:
        coords = amb.get("routeCoords")
        if not coords or len(coords) < 2:
            return
        prog = float(amb.get("routeProgressM", 0.0))
        remaining = remaining_route_coords(coords, prog)
        if len(remaining) < 2:
            return
        route_pts = [(float(c[0]), float(c[1])) for c in remaining]
        hit = False
        for jam in self.jams:
            poly = jam.get("polygon") or []
            if len(poly) < 3:
                continue
            tup = [(float(p[0]), float(p[1])) for p in poly]
            if polyline_intersects_polygon(route_pts, tup):
                hit = True
                break
        if not hit:
            return
        if int(amb.get("_jamCooldown", 0)) > self.tick:
            return
        target = self._mission_target_latlon(amb)
        if not target:
            return
        start = (float(amb["latitude"]), float(amb["longitude"]))
        new_route, new_lim, _ = await self._route_with_jam_avoidance(start, target)
        if len(new_route) >= 2:
            amb["routeCoords"] = new_route
            if new_lim is not None:
                amb["roadSpeedLimitKmh"] = new_lim
            amb["routeProgressM"] = 0.0
            amb["_jamCooldown"] = self.tick + JAM_REROUTE_COOLDOWN_TICKS

    def _pick_staging_hospital(self, amb: dict[str, Any]) -> dict[str, Any] | None:
        """Hospital con menos unidades en radio; en empate, el más cercano por Haversine."""
        hospitals = [p for p in self.pois if p.get("kind") == "hospital"]
        if not hospitals:
            return None
        lat0 = float(amb.get("latitude") or 0.0)
        lon0 = float(amb.get("longitude") or 0.0)
        ranked: list[tuple[int, float, dict[str, Any]]] = []
        for h in hospitals:
            cnt = count_ambs_near_hospital(
                self.ambulances,
                float(h["latitude"]),
                float(h["longitude"]),
                STAGING_RADIUS_M,
            )
            d = haversine_m(lat0, lon0, float(h["latitude"]), float(h["longitude"]))
            ranked.append((cnt, d, h))
        ranked.sort(key=lambda x: (x[0], x[1]))
        return ranked[0][2] if ranked else None

    def _is_ai_autonomous(self) -> bool:
        ai = getattr(self, "_ai_engine", None)
        return ai is not None and getattr(ai, "mode", "hitl") == "autonomous"

    async def _try_assign_pending_emergency_to_ambulance(self, amb: dict[str, Any]) -> bool:
        """Gate por modo IA delante de la lógica real de asignación.

        Modo autónomo: el motor NO asigna por su cuenta. La IA manda.
        Cualquier emergencia pendiente la gestiona el AI observer vía
        ``dispatch_request`` / ``unattended_emergency`` proposals.

        Returns:
            True si se asignó; False si no procede (autónomo, no hay
            pendientes, o la unidad no puede aceptar).
        """
        if self._is_ai_autonomous():
            return False
        return await self._try_assign_pending_emergency_to_ambulance_impl(amb)

    async def _try_assign_pending_emergency_to_ambulance_impl(self, amb: dict[str, Any]) -> bool:
        """Si hay emergencias pending y la unidad puede aceptar una, asigna la mejor según scoring."""
        if infer_fsm_state(amb) != AmbulanceState.IDLE:
            return False
        if amb.get("routeCoords"):
            return False
        pending = [e for e in self.emergencies if e.get("status") == "pending"]
        if not pending:
            return False
        start = (float(amb["latitude"]), float(amb["longitude"]))
        ctx = self._dispatch_scoring_context()
        scored: list[tuple[float, dict[str, float], dict[str, Any]]] = []
        for em in pending:
            total, breakdown = self.dispatch_scoring.score_candidate(amb, em, ctx)
            scored.append((total, breakdown, em))
        scored.sort(key=lambda x: x[0], reverse=True)
        for total, breakdown, em in scored:
            eid = str(em["id"])
            dest = (float(em["latitude"]), float(em["longitude"]))
            trip_m = await estimate_trip_m(self._http, start, dest)
            fuel, batt = self._mission_resources_for_check(amb)
            if not can_accept_mission(fuel, batt, trip_m):
                continue
            route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
            amb["assignedEmergencyId"] = eid
            amb["missionPhase"] = "to_emergency"
            amb["missionStatus"] = "EN_ROUTE"
            amb["fsmState"] = AmbulanceState.RESPONDING.value
            em["status"] = "assigned"
            em["assignedAmbulanceId"] = amb["id"]
            amb["routeCoords"] = route if len(route) >= 2 else None
            if rlim is not None:
                amb["roadSpeedLimitKmh"] = rlim
            amb["routeProgressM"] = 0.0
            amb["routeSpeedMs"] = ROUTE_SPEED_MS
            amb["routeDurationS"] = route_duration_s
            amb["refuelPending"] = False
            amb["pendingEmergencyId"] = None
            tr = self._mission_tracking.setdefault(eid, {})
            tr["dispatch_score"] = round(total, 3)
            tr["dispatch_score_breakdown"] = breakdown
            return True
        return False

    def _mission_target_latlon(self, amb: dict[str, Any]) -> tuple[float, float] | None:
        phase = amb.get("missionPhase")
        if phase == "to_refuel":
            pid = amb.get("refuelPoiId")
            for p in self.pois:
                if str(p.get("id")) == str(pid):
                    return (float(p["latitude"]), float(p["longitude"]))
            return None
        if phase == "to_emergency":
            eid = amb.get("assignedEmergencyId")
            e = self._emergency_by_id(str(eid)) if eid else None
            if e:
                return (float(e["latitude"]), float(e["longitude"]))
            return None
        if phase in ("to_staging", "to_hospital"):
            hid = amb.get("stagingHospitalId")
            for p in self.pois:
                if str(p.get("id")) == str(hid):
                    return (float(p["latitude"]), float(p["longitude"]))
            return None
        return None

    async def _maybe_divert_refuel(self, amb: dict[str, Any]) -> bool:
        if amb.get("missionPhase") != "to_emergency":
            return False
        if amb.get("refuelPending"):
            return False
        if not self._needs_energy_refill(amb, FUEL_LOW_PCT):
            return False
        gas = self._nearest_poi(amb, self._refuel_kind_for_amb(amb))
        if not gas:
            return False
        amb["pendingEmergencyId"] = amb.get("assignedEmergencyId")
        amb["assignedEmergencyId"] = None
        amb["missionPhase"] = "to_refuel"
        amb["refuelPoiId"] = gas["id"]
        amb["refuelPending"] = True
        amb["fsmState"] = AmbulanceState.REFUELING.value
        start = (float(amb["latitude"]), float(amb["longitude"]))
        dest = (float(gas["latitude"]), float(gas["longitude"]))
        coords, lim, _ = await fetch_route(self._http, [start, dest])
        amb["routeCoords"] = coords if len(coords) >= 2 else None
        if lim is not None:
            amb["roadSpeedLimitKmh"] = lim
        amb["routeProgressM"] = 0.0
        amb["routeSpeedMs"] = ROUTE_SPEED_MS
        return True

    async def _on_route_completed(self, amb: dict[str, Any], tele: dict[str, Any]) -> None:
        """Máquina de estados al completar la polilínea actual.

        Resuelve la siguiente transición según `missionPhase`:
            - ``to_refuel`` → llena depósito, retoma emergencia pendiente
              si la había, o intenta asignación / staging si IDLE.
            - ``to_emergency`` → resuelve la emergencia, carga paciente,
              arranca ruta ``to_hospital`` al hospital más cercano.
            - ``to_hospital`` → entrega paciente y vuelve a IDLE.
            - ``to_staging`` → llena depósito (hospital tiene recarga) y
              queda IDLE en zona de espera.

        Args:
            amb: Dict de la ambulancia. Se muta en sitio.
            tele: Dict de telemetría del tick actual (se escriben
                ``mechanical.fuelLevelPct`` cuando aplica).
        """
        phase = amb.get("missionPhase")
        amb["_prevSpeedMs"] = 0.0
        amb["speedKmh"] = 0.0
        if phase == "to_refuel":
            # Repostar lleva unos minutos: la unidad queda parada en la estación.
            amb["missionPhase"] = "refueling"
            amb["missionStatus"] = "REFUELING"
            amb["fsmState"] = AmbulanceState.REFUELING.value
            amb["routeCoords"] = None
            amb["routeProgressM"] = 0.0
            amb["phaseUntil"] = self.sim_time_s + REFUEL_SECONDS
        elif phase == "to_emergency":
            await self._arrive_on_scene(amb)
        elif phase == "to_hospital":
            await self._arrive_at_hospital(amb)
        elif phase == "to_staging":
            self._apply_energy_refill(amb, tele)
            amb["missionPhase"] = "idle"
            amb["stagingHospitalId"] = None
            amb["routeCoords"] = None
            amb["routeProgressM"] = 0.0
            amb["missionStatus"] = "INACTIVE"
            amb["fsmState"] = AmbulanceState.IDLE.value
            await self._try_assign_pending_emergency_to_ambulance(amb)

    async def _finish_refuel(self, amb: dict[str, Any], tele: dict[str, Any]) -> None:
        """Fin del repostaje: llena el depósito y retoma la emergencia pendiente o queda libre."""
        self._apply_energy_refill(amb, tele)
        amb["refuelPoiId"] = None
        amb["refuelPending"] = False
        pe = amb.pop("pendingEmergencyId", None)
        amb["assignedEmergencyId"] = pe
        amb["routeCoords"] = None
        amb["routeProgressM"] = 0.0
        if pe:
            amb["missionPhase"] = "to_emergency"
            e = self._emergency_by_id(str(pe))
            if e:
                e["status"] = "assigned"
                e["assignedAmbulanceId"] = amb["id"]
                start = (float(amb["latitude"]), float(amb["longitude"]))
                dest = (float(e["latitude"]), float(e["longitude"]))
                route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
                if len(route) < 2:
                    route = [start, dest]
                amb["routeCoords"] = route
                if rlim is not None:
                    amb["roadSpeedLimitKmh"] = rlim
                amb["routeSpeedMs"] = ROUTE_SPEED_MS
                amb["fsmState"] = AmbulanceState.RESPONDING.value
            else:
                amb["assignedEmergencyId"] = None
                amb["missionPhase"] = "idle"
                amb["missionStatus"] = "INACTIVE"
                amb["fsmState"] = AmbulanceState.IDLE.value
                amb["_idleRefuelCooldown"] = self.tick + IDLE_REFUEL_COOLDOWN_TICKS
                await self._try_assign_pending_emergency_to_ambulance(amb)
                if infer_fsm_state(amb) == AmbulanceState.IDLE and not amb.get("routeCoords"):
                    hosp = self._pick_staging_hospital(amb)
                    if hosp:
                        await self._start_staging(amb, hosp)
                        amb["_stagingCooldown"] = self.tick + STAGING_COOLDOWN_TICKS
        else:
            amb["missionPhase"] = "idle"
            amb["missionStatus"] = "INACTIVE"
            amb["fsmState"] = AmbulanceState.IDLE.value
            amb["_idleRefuelCooldown"] = self.tick + IDLE_REFUEL_COOLDOWN_TICKS
            assigned = await self._try_assign_pending_emergency_to_ambulance(amb)
            if not assigned and infer_fsm_state(amb) == AmbulanceState.IDLE:
                hosp = self._pick_staging_hospital(amb)
                if hosp:
                    await self._start_staging(amb, hosp)
                    amb["_stagingCooldown"] = self.tick + STAGING_COOLDOWN_TICKS

    def _local_hour(self) -> int:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(get_active_region().timezone)).hour
        except Exception:
            return datetime.now().hour

    def _emergency_kind(self, em: dict[str, Any]) -> ecat.EmergencyKind:
        """Tipo del catálogo de una emergencia; se fija la primera vez que se pide."""
        kind = ecat.kind_by_key(em.get("kindKey"))
        if kind is None:
            import random as _rng
            kind = ecat.kind_for_type(_rng, em.get("emergencyType"), self._local_hour())
            em["kindKey"] = kind.key
        if not em.get("severity"):
            import random as _rng
            em["severity"] = ecat.pick_severity(_rng, kind)
        return kind

    async def _arrive_on_scene(self, amb: dict[str, Any]) -> None:
        """La unidad llega al lugar: asistencia durante un tiempo según el tipo y la gravedad."""
        import random as _rng
        eid = amb.get("assignedEmergencyId")
        e = self._emergency_by_id(str(eid)) if eid else None
        amb["routeCoords"] = None
        amb["routeProgressM"] = 0.0
        if not e:
            amb["assignedEmergencyId"] = None
            self._set_idle(amb)
            await self._try_assign_pending_emergency_to_ambulance(amb)
            return
        kind = self._emergency_kind(e)
        severity = str(e.get("severity") or "medium")
        e["status"] = "on_scene"
        tr = self._mission_tracking.get(str(eid))
        if tr is not None:
            tr["arrived_on_scene_at"] = _iso()
        self._events.emit_event(
            "emergency", str(eid), "phase_change",
            payload={"phase": "on_scene", "ambulanceId": str(amb["id"])}, actor="engine", tick=self.tick,
        )
        amb["_lastEmergencyId"] = str(eid)
        amb["missionPhase"] = "on_scene"
        amb["missionStatus"] = "ON_SCENE"
        amb["fsmState"] = AmbulanceState.ON_SCENE.value
        amb["phaseUntil"] = self.sim_time_s + ecat.on_scene_seconds(_rng, kind, severity)
        amb["_transportNeeded"] = ecat.needs_transport(_rng, kind, severity)
        amb["_emSeverity"] = severity

    async def _leave_scene(self, amb: dict[str, Any]) -> None:
        """Fin de la asistencia: traslado al hospital o alta en el lugar."""
        eid = str(amb.get("assignedEmergencyId") or amb.get("_lastEmergencyId") or "")
        e = self._emergency_by_id(eid) if eid else None
        if e and e.get("status") != "resolved":
            e["status"] = "resolved"
            self._resolved_emergencies += 1
        tr = self._mission_tracking.get(eid)
        if tr is not None:
            tr["left_scene_at"] = _iso()
        amb["assignedEmergencyId"] = None
        severity = str(amb.pop("_emSeverity", "medium"))
        transport = bool(amb.pop("_transportNeeded", True))
        hosp = await self._best_hospital(amb) if transport else None
        if hosp:
            amb["hasPatient"] = True
            amb["patientSeverity"] = ecat.patient_severity(severity)
            amb["missionPhase"] = "to_hospital"
            amb["stagingHospitalId"] = hosp["id"]
            amb["missionStatus"] = "EN_ROUTE"
            amb["fsmState"] = AmbulanceState.TRANSPORTING.value
            start = (float(amb["latitude"]), float(amb["longitude"]))
            dest = (float(hosp["latitude"]), float(hosp["longitude"]))
            route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
            amb["routeCoords"] = route if len(route) >= 2 else None
            if rlim is not None:
                amb["roadSpeedLimitKmh"] = rlim
            amb["routeProgressM"] = 0.0
            amb["routeSpeedMs"] = ROUTE_SPEED_MS
            amb["routeDurationS"] = route_duration_s
            return
        # Atendido en el lugar sin traslado (o sin hospitales en el mapa).
        eid_done = amb.pop("_lastEmergencyId", None)
        if eid_done:
            self._emit_mission_outcome(eid_done, amb)
            self._events.emit_event(
                "emergency", eid_done, "resolved",
                payload={"ambulanceId": str(amb["id"]), "transported": False},
                actor="engine", tick=self.tick,
            )
        amb["hasPatient"] = False
        amb["patientSeverity"] = None
        self._set_idle(amb)
        await self._try_assign_pending_emergency_to_ambulance(amb)

    async def _arrive_at_hospital(self, amb: dict[str, Any]) -> None:
        """Llegada a urgencias: transferencia del paciente durante unos minutos."""
        import random as _rng
        amb["routeCoords"] = None
        amb["routeProgressM"] = 0.0
        severity = {"critical": "critical", "moderate": "high", "stable": "low"}.get(
            str(amb.get("patientSeverity") or ""), "medium"
        )
        amb["missionPhase"] = "at_hospital"
        amb["missionStatus"] = "HANDOVER"
        amb["fsmState"] = AmbulanceState.HANDOVER.value
        amb["phaseUntil"] = self.sim_time_s + ecat.handover_seconds(_rng, severity)

    async def _finish_handover(self, amb: dict[str, Any]) -> None:
        eid_done = amb.pop("_lastEmergencyId", None)
        if eid_done:
            self._emit_mission_outcome(eid_done, amb)
            self._events.emit_event(
                "emergency", eid_done, "resolved",
                payload={"ambulanceId": str(amb["id"]), "transported": True},
                actor="engine", tick=self.tick,
            )
        amb["hasPatient"] = False
        amb["patientSeverity"] = None
        amb["stagingHospitalId"] = None
        self._set_idle(amb)
        await self._try_assign_pending_emergency_to_ambulance(amb)

    def _set_idle(self, amb: dict[str, Any]) -> None:
        amb["missionPhase"] = "idle"
        amb["missionStatus"] = "INACTIVE"
        amb["fsmState"] = AmbulanceState.IDLE.value
        amb["routeCoords"] = None
        amb["routeProgressM"] = 0.0
        amb.pop("phaseUntil", None)

    async def _complete_timed_phase(self, amb: dict[str, Any], tele: dict[str, Any]) -> None:
        phase = amb.get("missionPhase")
        amb.pop("phaseUntil", None)
        if phase == "on_scene":
            await self._leave_scene(amb)
        elif phase == "at_hospital":
            await self._finish_handover(amb)
        elif phase == "refueling":
            await self._finish_refuel(amb, tele)

    # ── Dinámica del vehículo ─────────────────────────────────────────
    @staticmethod
    def _route_cumulative(coords: Any) -> list[float]:
        cached = getattr(coords, "_cum", None)
        if cached is not None and len(cached) == len(coords):
            return cached
        cum = [0.0]
        for a, b in zip(coords, coords[1:]):
            cum.append(cum[-1] + haversine_m(float(a[0]), float(a[1]), float(b[0]), float(b[1])))
        try:
            coords._cum = cum  # RouteCoords admite atributos; una lista normal no
        except AttributeError:
            pass
        return cum

    def _vehicle_max_ms(self, amb: dict[str, Any]) -> float:
        et = self._entity_type_by_id(str(amb.get("entityTypeId") or ""))
        try:
            kmh = float((et or {}).get("speedKmh") or 120.0)
        except (TypeError, ValueError):
            kmh = 120.0
        return max(10.0, kmh) / 3.6

    def _target_speed_ms(self, amb: dict[str, Any], coords: Any, prog: float, lat: float, lon: float) -> float:
        """Velocidad objetivo en la posición actual de la ruta.

        Parte de la velocidad de la vía (perfil de coche de OSRM), la sube
        con sirena en servicio urgente, la recorta por meteorología, giros
        cerrados próximos, atascos y la distancia de frenada hasta el destino.
        """
        cum = self._route_cumulative(coords)
        n = len(cum)
        idx = 0
        lo, hi = 0, n - 1
        while lo < hi:  # tramo actual por búsqueda binaria
            mid = (lo + hi) // 2
            if cum[mid + 1] <= prog:
                lo = mid + 1
            else:
                hi = mid
        idx = min(lo, n - 2)
        speeds = getattr(coords, "seg_speeds_ms", None)
        base = float(speeds[idx]) if speeds and idx < len(speeds) and speeds[idx] > 0 else FALLBACK_SPEED_MS
        amb["roadSpeedLimitKmh"] = typical_limit_kmh(base) if speeds else None
        phase = amb.get("missionPhase")
        urgent = phase == "to_emergency" or (phase == "to_hospital" and amb.get("patientSeverity") in ("critical", "moderate"))
        target = base * (1.3 if base < 10.0 else 1.2) if urgent else base
        target = min(target, self._vehicle_max_ms(amb))
        try:
            wf = self.weather_speed_factor(lat, lon)
            amb["weatherFactor"] = round(wf, 3)
            target *= wf
        except Exception:
            pass
        # Giro cerrado en los próximos ~30 m → reducir a velocidad de giro.
        if idx + 2 < n:
            h0 = heading_deg_towards(float(coords[idx][0]), float(coords[idx][1]), float(coords[idx + 1][0]), float(coords[idx + 1][1]))
            k = idx + 1
            while k + 1 < n and cum[k] - prog < 30.0:
                h1 = heading_deg_towards(float(coords[k][0]), float(coords[k][1]), float(coords[k + 1][0]), float(coords[k + 1][1]))
                if abs(_heading_delta_deg(h0, h1)) > 50.0:
                    target = min(target, TURN_SPEED_MS)
                    break
                k += 1
        # Dentro de un atasco.
        for poly in self._jam_tuples():
            if point_in_polygon((lat, lon), poly):
                target = min(target, JAM_SPEED_URGENT_MS if urgent else JAM_SPEED_MS)
                break
        # Frenada para detenerse en el destino.
        remaining = max(0.0, cum[-1] - prog)
        target = min(target, math.sqrt(2.0 * DECEL_MS2 * remaining) + 0.5)
        return max(0.5, target)

    async def _tick_one_ambulance(
        self, amb: dict[str, Any], _dt_real: float, dt_sim: float
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Procesa un tick de una unidad: rerouting, avance, telemetría.

        Casos:
            1. ``manualControl=True`` → solo se emite telemetría; la
               posición la empuja el cliente.
            2. Fase ``to_refuel`` sin polilínea → completa refuel in situ.
            3. Con polilínea → intenta reroute por atasco, desvío por
               fuel bajo, avanza por ``advance_along_polyline``, actualiza
               odómetro/heading/navHint y corre los 5 motores de telemetría.
            4. Sin polilínea → solo telemetría IDLE.

        Args:
            amb: Dict de la ambulancia (mutado in-place).
            _dt_real: Reservado, no usado.
            dt_sim: Tiempo simulado por tick (``dt_real * speed_multiplier``).
                Tanto el avance de ruta como los motores reciben este valor.

        Returns:
            Tupla ``(telemetry_v2, payload_unit)``. El primer dict se
            escribe en `TelemetryWriter`; el segundo va al SSE del
            dashboard.
        """
        aid = str(amb["id"])
        coords = amb.get("routeCoords")
        phase = str(amb.get("missionPhase") or "")
        # Sin polilínea válida hacia gasolinera: OSRM vacío o fallo — completar repostaje en sitio.
        if phase == "to_refuel" and (not coords or len(coords) < 2):
            pid = amb.get("refuelPoiId")
            if pid:
                for p in self.pois:
                    if str(p.get("id")) == str(pid) and p.get("kind") == "gas_station":
                        amb["latitude"] = float(p["latitude"])
                        amb["longitude"] = float(p["longitude"])
                        break
            amb["_navHint"] = {
                "on_route": False,
                "road_speed_limit_kmh": amb.get("roadSpeedLimitKmh"),
            }
            tele = self._telemetry.tick(aid, self.tick, dt_sim, amb)
            pt = self._powertrain_of(amb)
            if pt == "electric":
                b = float(amb.get("batteryLevel", tele["mechanical"]["batteryPct"]))
                tele["mechanical"]["batteryPct"] = round(max(0.0, min(100.0, b)), 1)
                amb["batteryLevel"] = tele["mechanical"]["batteryPct"]
            else:
                f = float(amb.get("fuelLevel", tele["mechanical"]["fuelLevelPct"]))
                tele["mechanical"]["fuelLevelPct"] = round(max(0.0, min(100.0, f)), 1)
                amb["fuelLevel"] = tele["mechanical"]["fuelLevelPct"]
            self._normalize_energy_by_powertrain(amb, tele)
            pos = tele["positioning"]
            amb["latitude"] = pos["latitude"]
            amb["longitude"] = pos["longitude"]
            amb["speedKmh"] = pos["speedKmh"]
            await self._on_route_completed(amb, tele)
            amb["telemetry"] = tele
            amb["updatedAt"] = _iso()
            return tele, {"id": aid, "telemetry": tele}

        if coords and len(coords) >= 2:
            await self._maybe_reroute_jam(amb)
            coords = amb.get("routeCoords")
            await self._maybe_divert_refuel(amb)
            coords = amb.get("routeCoords")

        coords = amb.get("routeCoords")
        if coords and len(coords) >= 2:
            cur_lat = float(amb.get("latitude") or coords[0][0])
            cur_lon = float(amb.get("longitude") or coords[0][1])
            target_ms = self._target_speed_ms(amb, coords, float(amb.get("routeProgressM", 0.0)), cur_lat, cur_lon)
            v_prev = float(amb.get("_prevSpeedMs", 0.0))
            dv = target_ms - v_prev
            dv = max(-DECEL_MS2 * dt_sim, min(ACCEL_MS2 * dt_sim, dv))
            speed_ms = max(0.0, v_prev + dv)
            dt_s = max(dt_sim, 1e-6)
            delta_m = 0.5 * (v_prev + speed_ms) * dt_sim
            prev_lat = float(amb.get("latitude") or coords[0][0])
            prev_lon = float(amb.get("longitude") or coords[0][1])
            prog = float(amb.get("routeProgressM", 0.0))
            prev_speed = v_prev
            prev_hdg = amb.get("_prevHeadingDeg")
            prev_hdg_f = float(prev_hdg) if isinstance(prev_hdg, (int, float)) else None
            lat, lon, new_prog, done = advance_along_polyline(coords, prog, delta_m)
            amb["odometerKm"] = float(amb.get("odometerKm", 0.0)) + delta_m / 1000.0
            hdg = heading_deg_towards(prev_lat, prev_lon, lat, lon)
            long_accel = (speed_ms - prev_speed) / dt_s
            delta_h = _heading_delta_deg(prev_hdg_f, hdg)
            lateral_accel = speed_ms * math.radians(delta_h) / dt_s
            road_lim = amb.get("roadSpeedLimitKmh")
            amb["_navHint"] = {
                "on_route": True,
                "speed_ms": speed_ms,
                "heading_deg": hdg,
                "long_accel_ms2": long_accel,
                "longitudinal_g": long_accel / 9.81,
                "lateral_g": lateral_accel / 9.81,
                "road_speed_limit_kmh": road_lim,
                "gps_hdop": 1.0 + 0.001 * speed_ms,
                "brake_heat": max(0.0, -long_accel) * 0.02,
            }
            amb["latitude"] = lat
            amb["longitude"] = lon
            tele = self._telemetry.tick(aid, self.tick, dt_sim, amb)
            pt = self._powertrain_of(amb)
            if pt == "electric":
                b = float(amb.get("batteryLevel", tele["mechanical"]["batteryPct"]))
                # Descarga visible en simulación: prioriza distancia real recorrida.
                b = max(0.0, b - (delta_m * BATTERY_DRAIN_PCT_PER_M) - (max(0.0, long_accel) * dt_sim * 0.0004))
                tele["mechanical"]["batteryPct"] = round(b, 1)
                amb["batteryLevel"] = tele["mechanical"]["batteryPct"]
            else:
                fu = float(amb.get("fuelLevel", tele["mechanical"]["fuelLevelPct"]))
                fu = max(0.0, fu - (delta_m * FUEL_DRAIN_PCT_PER_M) - (max(0.0, long_accel) * dt_sim * 0.0003))
                tele["mechanical"]["fuelLevelPct"] = round(fu, 1)
                amb["fuelLevel"] = tele["mechanical"]["fuelLevelPct"]
            self._normalize_energy_by_powertrain(amb, tele)
            # Tracking coste operativo: vehículo en route ⇒ "activado" + tiempo
            # acumulado para multiplicar por costPerMin.
            amb["activated"] = True
            amb["activeSeconds"] = float(amb.get("activeSeconds", 0.0)) + float(dt_sim)
            amb["_prevSpeedMs"] = speed_ms
            amb["_prevHeadingDeg"] = hdg
            amb["routeProgressM"] = new_prog
            speed_kmh = speed_ms * 3.6
            amb["speedKmh"] = speed_kmh
            amb["telemetry"] = tele
            amb["updatedAt"] = _iso()
            if done:
                await self._on_route_completed(amb, tele)
                amb["telemetry"] = tele
            return tele, {"id": aid, "telemetry": tele}

        amb["_navHint"] = {
            "on_route": False,
            "road_speed_limit_kmh": amb.get("roadSpeedLimitKmh"),
        }
        amb["_prevSpeedMs"] = 0.0
        tele = self._telemetry.tick(aid, self.tick, dt_sim, amb)
        if amb.get("missionPhase") in TIMED_PHASES:
            until = amb.get("phaseUntil")
            if until is None or self.sim_time_s >= float(until):
                await self._complete_timed_phase(amb, tele)
        pt = self._powertrain_of(amb)
        if pt == "electric":
            b = float(amb.get("batteryLevel", tele["mechanical"]["batteryPct"]))
            # Consumo basal en reposo.
            b = max(0.0, b - dt_sim * BATTERY_IDLE_PCT_PER_S)
            tele["mechanical"]["batteryPct"] = round(b, 1)
            amb["batteryLevel"] = tele["mechanical"]["batteryPct"]
        else:
            f = float(amb.get("fuelLevel", tele["mechanical"]["fuelLevelPct"]))
            f = max(0.0, f - dt_sim * FUEL_IDLE_PCT_PER_S)
            tele["mechanical"]["fuelLevelPct"] = round(f, 1)
            amb["fuelLevel"] = tele["mechanical"]["fuelLevelPct"]
        self._normalize_energy_by_powertrain(amb, tele)
        pos = tele["positioning"]
        amb["latitude"] = pos["latitude"]
        amb["longitude"] = pos["longitude"]
        amb["speedKmh"] = pos["speedKmh"]
        amb["telemetry"] = tele
        amb["updatedAt"] = _iso()
        return tele, {"id": aid, "telemetry": tele}

    # ── Companion (helicopter / police patrol) management ───────────────────

    async def dispatch_companion(self, kind: str, emergency_id: str) -> str | None:
        """Despacha un vehículo de apoyo (helicóptero, patrulla) a la emergencia.

        Crea un `companion` nuevo en `self.companions` con ruta OSRM desde
        el hospital más cercano hasta el lugar del incidente. Si ya hay un
        companion del mismo tipo asignado a esa emergencia, devuelve su id
        sin duplicar.

        Args:
            kind: Id de `entity_types` de tipo ``"vehicle"`` (por ejemplo
                ``"helicopter"`` o ``"police_patrol"``).
            emergency_id: UUID de la emergencia destino.

        Returns:
            UUID del companion creado/reutilizado, o None si la emergencia
            no existe, está resuelta, o no hay hospital base disponible.
        """
        em = self._emergency_by_id(emergency_id)
        if not em:
            return None
        for c in self.companions:
            if c.get("assignedEmergencyId") == emergency_id and c.get("kind") == kind:
                return c["id"]

        hospitals = [p for p in self.pois if p.get("kind") == "hospital"]
        if not hospitals:
            return None
        e_lat, e_lon = float(em["latitude"]), float(em["longitude"])
        base = min(hospitals, key=lambda h: haversine_m(e_lat, e_lon, h["latitude"], h["longitude"]))

        cid = str(uuid4())
        etype = self._entity_type_by_id(kind)
        speed = float(etype["speedKmh"]) if etype and "speedKmh" in etype else (200.0 if kind == "helicopter" else 80.0)
        serial = self._next_companion_serial(kind)
        prefix = self._companion_callsign_prefix(kind)
        type_name = str(etype["name"]) if etype and etype.get("name") else kind
        companion: dict[str, Any] = {
            "id": cid,
            "kind": kind,
            "displayLabel": f"{prefix}-{serial:03d}",
            "typeName": type_name,
            "latitude": float(base["latitude"]),
            "longitude": float(base["longitude"]),
            "assignedEmergencyId": emergency_id,
            "status": "dispatched",
            "baseLatitude": float(base["latitude"]),
            "baseLongitude": float(base["longitude"]),
            "speedKmh": speed,
            "routeCoords": None,
            "routeProgressM": 0.0,
        }

        if kind == "helicopter":
            companion["routeCoords"] = [
                [float(base["latitude"]), float(base["longitude"])],
                [e_lat, e_lon],
            ]
        else:
            start = (float(base["latitude"]), float(base["longitude"]))
            dest = (e_lat, e_lon)
            route, _, _ = await fetch_route(self._http, [start, dest])
            if len(route) >= 2:
                companion["routeCoords"] = route
            else:
                companion["routeCoords"] = [[start[0], start[1]], [dest[0], dest[1]]]

        self.companions.append(companion)
        _logger.info("Dispatched %s %s to emergency %s", kind, cid, emergency_id)
        return cid

    def _tick_companions(self, dt_sim: float) -> None:
        """Avanza los companions (heli/policía) un tick.

        Estados: ``dispatched`` → avanza hasta destino; al llegar pasa a
        ``on_scene``. ``on_scene`` → si la emergencia se resolvió, genera
        ruta de vuelta a la base y pasa a ``returning``. ``returning`` →
        al llegar, se elimina de ``self.companions``.

        Args:
            dt_sim: Tiempo simulado del tick.
        """
        to_remove: list[str] = []
        for comp in self.companions:
            status = comp.get("status", "dispatched")
            coords = comp.get("routeCoords")
            if not coords or len(coords) < 2:
                continue

            speed_ms = comp["speedKmh"] / 3.6
            delta_m = speed_ms * dt_sim

            if status == "dispatched":
                prog = float(comp.get("routeProgressM", 0.0))
                lat, lon, new_prog, done = advance_along_polyline(coords, prog, delta_m)
                comp["latitude"] = lat
                comp["longitude"] = lon
                comp["routeProgressM"] = new_prog
                if done:
                    comp["status"] = "on_scene"
                    comp["routeCoords"] = None

            elif status == "on_scene":
                eid = comp.get("assignedEmergencyId")
                em = self._emergency_by_id(str(eid)) if eid else None
                if not em or em.get("status") == "resolved":
                    base_lat = comp["baseLatitude"]
                    base_lon = comp["baseLongitude"]
                    comp["routeCoords"] = [
                        [comp["latitude"], comp["longitude"]],
                        [base_lat, base_lon],
                    ]
                    comp["routeProgressM"] = 0.0
                    comp["status"] = "returning"

            elif status == "returning":
                prog = float(comp.get("routeProgressM", 0.0))
                lat, lon, new_prog, done = advance_along_polyline(coords, prog, delta_m)
                comp["latitude"] = lat
                comp["longitude"] = lon
                comp["routeProgressM"] = new_prog
                if done:
                    to_remove.append(comp["id"])

        if to_remove:
            self.companions = [c for c in self.companions if c["id"] not in to_remove]

    async def run_loop(self) -> None:
        """Bucle principal de la simulación.

        Se ejecuta como tarea de fondo al arrancar FastAPI (vía lifespan).
        Cada tick:
            1. Calcula ``dt_real`` desde el último tick y ``dt_sim`` aplicando
               `speed_multiplier`.
            2. Procesa todas las ambulancias con `_tick_one_ambulance`,
               devolviendo telemetría v2.0.
            3. Avanza companions (heli, policía) con `_tick_companions`.
            4. Persiste el batch cada `FLUSH_EVERY_N_TICKS`.
            5. Propaga el payload por el `ChannelRouter` para suscriptores SSE.

        Finaliza solo al `stop()` o por señal SIGTERM del proceso. Un tick
        usa ``dt_real`` entre iteraciones; los motores reciben
        ``dt_sim = dt_real * speed_multiplier``.
        """
        self._running = True
        while not self._stop.is_set():
            dt_real = self._dt()
            await asyncio.sleep(dt_real)
            if self.paused:
                continue
            async with self._lock:
                self.tick += 1
                dt_sim = dt_real * self.speed_multiplier
                self.sim_time_s += dt_sim
                for amb in self.ambulances:
                    if infer_fsm_state(amb) == AmbulanceState.IDLE and not amb.get(
                        "routeCoords"
                    ):
                        await self._try_assign_pending_emergency_to_ambulance(amb)
                for amb in self.ambulances:
                    await self._maybe_idle_refuel(amb)
                await self._maybe_staging_idle()
                payload_units: list[dict[str, Any]] = []
                for amb in self.ambulances:
                    tele_out, pu = await self._tick_one_ambulance(amb, dt_real, dt_sim)
                    payload_units.append(pu)
                    self._tele_writer.append(
                        str(amb["id"]),
                        tele_out.get("positioning"),
                        tele_out.get("mechanical"),
                        tele_out.get("medical"),
                        environmental=tele_out.get("environmental"),
                        network=tele_out.get("network"),
                        derived=tele_out.get("derived"),
                        meta=tele_out.get("meta"),
                    )
                self._tick_companions(dt_sim)
                if self.training_mode:
                    await self._training_mode_tick(dt_sim)
                if self.tick % FLUSH_EVERY_N_TICKS == 0:
                    await self._tele_writer.flush()
                    await self._events.maybe_flush()
                payload = {
                    "tick": self.tick,
                    "units": len(self.ambulances),
                    "telemetryBatch": payload_units,
                }
            await self.channels.route_payload(self.network, payload, self.tick)
            self._after_comms_round()
        self._running = False

    # ── Comunicaciones con las unidades ───────────────────────────────
    def _unit_reachable(self, amb: dict[str, Any]) -> bool:
        net = ((amb.get("telemetry") or {}).get("network") or {})
        return net.get("networkType") != "none"

    def _after_comms_round(self) -> None:
        """Tras cada envío: último contacto por unidad y entrega de mensajes en cola."""
        from .channels import LinkState
        link = self.channels.active_link
        if link == LinkState.DEGRADED:
            return
        now = _iso()
        channel = {"mqtt_active": "mqtt", "p2p_active": "p2p", "http_fallback": "http"}.get(link.value, link.value)
        reachable: set[str] = set()
        for amb in self.ambulances:
            if self._unit_reachable(amb):
                amb["lastContactAt"] = now
                reachable.add(str(amb["id"]))
        for msg in self.unit_messages:
            if msg["status"] != "queued":
                continue
            if msg["unitId"] is None or msg["unitId"] in reachable:
                msg["status"] = "delivered"
                msg["deliveredAt"] = now
                msg["channel"] = channel

    async def send_unit_message(self, unit_id: str | None, text: str) -> dict[str, Any]:
        """Encola un mensaje de la central para una unidad (o todas con ``None``).

        Se entrega en la siguiente ronda de comunicaciones si hay algún
        canal activo y la unidad tiene cobertura; si no, queda en cola.
        """
        if unit_id is not None and not any(str(a.get("id")) == unit_id for a in self.ambulances):
            raise ValueError("unidad desconocida")
        label = None
        if unit_id is not None:
            amb = next(a for a in self.ambulances if str(a.get("id")) == unit_id)
            label = amb.get("displayLabel")
        msg = {
            "id": str(uuid4()),
            "unitId": unit_id,
            "unitLabel": label,
            "text": text,
            "status": "queued",
            "createdAt": _iso(),
            "deliveredAt": None,
            "readAt": None,
            "channel": None,
        }
        self.unit_messages.append(msg)
        self._events.emit_event(
            "comms", msg["id"], "message_sent",
            payload={"unitId": unit_id, "text": text}, actor="operator", tick=self.tick,
        )
        return dict(msg)

    def ack_unit_message(self, message_id: str) -> bool:
        """La unidad confirma la lectura de un mensaje entregado."""
        for msg in self.unit_messages:
            if msg["id"] == message_id:
                if msg["status"] == "queued":
                    return False
                msg["status"] = "read"
                msg["readAt"] = _iso()
                return True
        return False

    def get_comms_since(self, after_seq: int) -> list[dict[str, Any]]:
        return [dict(x) for x in self._comms_log if int(x.get("seq", 0)) > after_seq]

    def stop(self) -> None:
        self._stop.set()

    def _default_amb_fields(self) -> dict[str, Any]:
        return {
            "missionPhase": "idle",
            "assignedEmergencyId": None,
            "pendingEmergencyId": None,
            "refuelPoiId": None,
            "stagingHospitalId": None,
            "refuelPending": False,
            "routeCoords": None,
            "routeProgressM": 0.0,
            "routeSpeedMs": ROUTE_SPEED_MS,
            "fsmState": AmbulanceState.IDLE.value,
            "batteryLevel": 100.0,
            "_jamCooldown": 0,
            "_stagingCooldown": 0,
            "roadSpeedLimitKmh": None,
            "hasPatient": False,
            "patientSeverity": None,
            "odometerKm": 0.0,
            "_idleRefuelCooldown": 0,
            # Tracking coste operativo: tiempo (sim-segundos) en estado activo
            # acumulado, y flag de si ya fue activada (para cobrar activationCost
            # una sola vez por sesión de uso).
            "activeSeconds": 0.0,
            "activated": False,
        }

    async def spawn_ambulance(
        self,
        lat: float,
        lon: float,
        entity_type_id: str | None = None,
        display_label: str | None = None,
    ) -> str:
        """Crea una unidad nueva en el mapa y la registra en la flota.

        Args:
            lat: Latitud en grados decimales.
            lon: Longitud en grados decimales.
            entity_type_id: Id de `entity_types` (p.ej. ``"ambulance"``,
                ``"helicopter"`` o un id custom). Si es None se usa
                ``"ambulance"``.
            display_label: Etiqueta visible en UI (``"AMB-042"``, ``"DRON-01"``).
                Si es None, la UI genera uno a partir del tipo + índice.

        Returns:
            UUID de la nueva unidad (string).
        """
        async with self._lock:
            aid = str(uuid4())
            row: dict[str, Any] = {
                "id": aid,
                "missionStatus": "INACTIVE",
                "speedKmh": 0.0,
                "fuelLevel": 100.0,
                "patientStatus": "none",
                "locationLabel": display_label or "Simulado",
                "latitude": lat,
                "longitude": lon,
                "updatedAt": _iso(),
            }
            row.update(self._default_amb_fields())
            if entity_type_id:
                row["entityTypeId"] = entity_type_id
            if display_label:
                row["displayLabel"] = display_label
            self._normalize_energy_by_powertrain(row, None)
            self.ambulances.append(row)
        self._events.emit_event(
            "ambulance", aid, "created",
            payload={
                "lat": lat, "lon": lon,
                "entityTypeId": entity_type_id or self.default_ambulance_entity_type_id(),
                "displayLabel": display_label,
            },
            actor="operator", tick=self.tick,
        )
        return aid

    async def _training_mode_tick(self, dt_sim: float) -> None:
        """Genera emergencias automáticas mientras ``training_mode`` está activo.

        Las llegadas siguen un proceso de Poisson cuya tasa media es la
        configurada (emergencias por minuto simulado), modulada por la hora
        local: menos de madrugada, picos a media mañana y por la tarde. Cada
        emergencia sale del catálogo realista y se sitúa en una calle real.
        Si falta infraestructura, se coloca la real de la región.
        """
        import random as _rng
        await self._ensure_region_infrastructure()
        if len(self.ambulances) < 3:
            await self._spawn_unit_at_base()
        target_rate = max(self.training_emergencies_per_min, 0.1)
        ext_wall_per_min = self._recent_external_emergency_rate_per_min()
        ext_sim_per_min = ext_wall_per_min / max(self.speed_multiplier, 0.1)
        rate_per_min = max(0.0, target_rate - ext_sim_per_min) * ecat.demand_factor(self._local_hour())
        if _rng.random() >= rate_per_min / 60.0 * dt_sim:
            return
        if self._training_spawning >= 3:
            return  # no acumular colocaciones pendientes si OSRM va lento
        self._training_spawning += 1
        asyncio.create_task(self._spawn_catalog_emergency("training"))

    async def _spawn_catalog_emergency(
        self, source: str, center: tuple[float, float] | None = None,
    ) -> str | None:
        """Crea una emergencia del catálogo en una calle real y la despacha.

        Las de ``training`` se asignan directamente; el resto pasa por la
        misma puerta que una emergencia manual (aprobación o IA autónoma).
        """
        import random as _rng
        try:
            region = get_active_region()
            hour = self._local_hour()
            kind = ecat.pick_kind(_rng, hour)
            severity = ecat.pick_severity(_rng, kind)
            point = None
            for _ in range(4 if kind.major_road else 1):
                point = await random_road_point(self._http, center or region.center, region.urban_sigma_m, rng=_rng)
                if not kind.major_road or ecat.is_major_road(point.street):
                    break
            assert point is not None
            eid = str(uuid4())
            now_iso = _iso()
            row = {
                "id": eid,
                "title": kind.title,
                "description": ecat.describe(_rng, kind),
                "latitude": point.lat,
                "longitude": point.lon,
                "street": point.street,
                "status": "pending",
                "assignedAmbulanceId": None,
                "emergencyType": kind.type,
                "kindKey": kind.key,
                "severity": severity,
                "source": source,
                "createdAt": now_iso,
            }
            async with self._lock:
                self.emergencies.append(row)
                self._mission_tracking[eid] = {
                    "created_at": now_iso, "emergency_type": kind.type,
                    "lat": point.lat, "lon": point.lon,
                    "jams_crossed": 0, "rerouted_times": 0, "companions_dispatched": [],
                    "source": source,
                }
            self._events.emit_event(
                "emergency", eid, "created",
                payload={"source": source, "emergencyType": kind.type, "severity": severity,
                         "lat": point.lat, "lon": point.lon, "title": kind.title},
                actor="engine", tick=self.tick,
            )
            if source == "training":
                await self._dispatch_emergency_safe(eid)
            else:
                await self._route_external_emergency_through_hitl(eid)
            return eid
        except Exception:
            _logger.exception("No se pudo generar una emergencia automática")
            return None
        finally:
            if source == "training":
                self._training_spawning = max(0, self._training_spawning - 1)

    async def _ensure_region_infrastructure(self) -> None:
        """Si no hay hospitales o gasolineras, coloca los reales de la región."""
        from .regions import region_places
        region = get_active_region()
        if not any(p.get("kind") == "hospital" for p in self.pois):
            for name, lat, lon in region.hospitals[:3]:
                self._add_auto_poi("hospital", name, lat, lon)
        if not any(p.get("kind") == "gas_station" for p in self.pois):
            stations = sorted(
                region_places(region.id, "fuel_stations"),
                key=lambda r: haversine_m(region.center_lat, region.center_lon, r[1], r[2]),
            )
            for name, lat, lon in stations[:3]:
                self._add_auto_poi("gas_station", name or "Gasolinera", lat, lon)

    def _add_auto_poi(self, kind: str, name: str, lat: float, lon: float) -> None:
        pid = str(uuid4())
        self.pois.append({"id": pid, "kind": kind, "name": name, "latitude": lat, "longitude": lon})
        self._events.emit_event(
            "poi", pid, "created",
            payload={"kind": kind, "auto": True, "lat": lat, "lon": lon},
            actor="engine", tick=self.tick,
        )

    async def _spawn_unit_at_base(self) -> None:
        """Añade una unidad en un hospital o base real, ajustada a la calle de salida."""
        import random as _rng
        from .regions import region_places
        region = get_active_region()
        bases = [(n, la, lo) for n, la, lo in region.hospitals] + region_places(region.id, "ambulance_bases")
        if bases:
            _, lat, lon = _rng.choice(bases)
        else:
            lat, lon = default_spawn()
        point = await snap_to_road(self._http, lat, lon, max_snap_m=300.0)
        if point is not None:
            lat, lon = point.lat, point.lon
        aid = str(uuid4())
        row: dict[str, Any] = {
            "id": aid,
            "missionStatus": "INACTIVE",
            "speedKmh": 0.0,
            "fuelLevel": 100.0,
            "patientStatus": "none",
            "locationLabel": "Base",
            "latitude": lat,
            "longitude": lon,
            "updatedAt": _iso(),
            "entityTypeId": self.default_ambulance_entity_type_id(),
            "displayLabel": f"AMB-{len(self.ambulances) + 1:03d}",
        }
        row.update(self._default_amb_fields())
        self.ambulances.append(row)
        self._events.emit_event(
            "ambulance", aid, "created",
            payload={"auto": True, "displayLabel": row["displayLabel"]},
            actor="engine", tick=self.tick,
        )

    def _recent_external_emergency_rate_per_min(self) -> float:
        """Wall-clock rate of external-feed emergencies created in last 60s."""
        now = time.monotonic()
        cutoff = now - 60.0
        while self._external_em_window and self._external_em_window[0] < cutoff:
            self._external_em_window.popleft()
        return float(len(self._external_em_window))

    async def set_training_mode(self, enabled: bool, rate_per_min: float | None = None) -> dict[str, Any]:
        """Activa/desactiva generador continuo de emergencias.

        Args:
            enabled: True arranca modo autónomo.
            rate_per_min: Emergencias/min. Si None usa valor actual.

        Returns:
            Dict con estado actual + nuevo session_id si se activó.
        """
        async with self._lock:
            self.training_mode = bool(enabled)
            if rate_per_min is not None:
                self.training_emergencies_per_min = max(0.1, min(60.0, float(rate_per_min)))
            self._training_last_spawn_tick = self.tick
            if enabled:
                new_session = self._events.new_session(
                    mode="training_autonomous",
                    speed=self.speed_multiplier,
                )
                # En training mode forzamos play y desactivamos HITL approval
                # para que el generador no se bloquee.
                self.paused = False
                self.dispatch_requires_approval = False
                return {
                    "trainingMode": True,
                    "rate": self.training_emergencies_per_min,
                    "sessionId": new_session,
                }
            return {
                "trainingMode": False,
                "rate": self.training_emergencies_per_min,
            }

    async def seed_default_fleet_if_empty(self) -> None:
        """No-op: el stack arranca sin escenario sembrado.

        Antes creaba 2 ambulancias junto a la base de la región activa. Retirado
        para que el operador construya el escenario desde cero con el
        generador IA (`/api/sim/generate-scenario`) o a mano. El método
        se mantiene para no romper callers (lifespan / reset).
        """
        return

    async def set_network(self, mqtt: bool, p2p: bool, http: bool) -> None:
        """Activa/desactiva canales de comms (MQTT, P2P mesh, HTTP fallback)."""
        async with self._lock:
            self.network = {"mqtt": mqtt, "p2p": p2p, "http": http}

    async def reset_simulation(self) -> None:
        """Borra flota, emergencias, companions, jams; repuebla POIs default.

        Pausa el motor y re-siembra la flota base (dos ambulancias junto
        al hub de la región activa) para que el dashboard quede en un estado funcional
        tras el reset.
        """
        async with self._lock:
            for a in self.ambulances:
                self._telemetry.reset_ambulance(str(a["id"]))
            self.ambulances.clear()
            self.emergencies.clear()
            self.companions.clear()
            self.jams.clear()
            self.external_jams.clear()
            self.pois = default_pois_copy()
            self.tick = 0
            self._resolved_emergencies = 0
            self._comms_log.clear()
            self.unit_messages.clear()
            self.paused = True
        await self.seed_default_fleet_if_empty()

    _EXTERNAL_EVENT_JAM_TYPES = {"lane_closure", "accident", "construction", "hazmat_spill"}
    _EXTERNAL_EVENT_DISPATCH_TYPES = {
        "fire": "fire",
        "medical_emergency": "medical",
        "hazmat_spill": "hazmat",
        "accident": "trauma",
        "flood": "flood",
    }
    _EXTERNAL_EVENT_DISPATCH_MAX_AGE_S = 600.0
    _EXTERNAL_EVENT_DISPATCH_DEDUPE_CAP = 1000
    _EXTERNAL_EVENT_CAP = 500
    _WEATHER_HISTORY = 20

    @staticmethod
    def _square_polygon_around(lat: float, lon: float, half_side_m: float) -> list[list[float]]:
        dlat = half_side_m / 111320.0
        dlon = half_side_m / (111320.0 * max(0.25, abs(math.cos(math.radians(lat)))))
        return [
            [lat - dlat, lon - dlon],
            [lat - dlat, lon + dlon],
            [lat + dlat, lon + dlon],
            [lat + dlat, lon - dlon],
        ]

    async def ingest_external_event(self, event: dict[str, Any]) -> None:
        """Upsert un evento externo (mock local o `POST /api/events/ingest`) en el estado runtime.

        Dedupe por id; eventos resueltos se mantienen pero pierden su
        jam asociado. Para tipos viales con `radius_m`, se materializa
        un polígono cuadrado en `external_jams` para que el routing OSRM
        lo evite.
        """
        eid = str(event.get("id") or "").strip()
        if not eid:
            return
        async with self._lock:
            existing_idx = self._external_events_index.get(eid)
            stored = dict(event)
            stored["receivedAt"] = _iso()
            if existing_idx is not None:
                self.external_events[existing_idx] = stored
            else:
                self.external_events.append(stored)
                self._external_events_index[eid] = len(self.external_events) - 1
                if len(self.external_events) > self._EXTERNAL_EVENT_CAP:
                    drop = self.external_events[:-self._EXTERNAL_EVENT_CAP]
                    self.external_events = self.external_events[-self._EXTERNAL_EVENT_CAP:]
                    self._external_events_index = {
                        str(e.get("id")): i for i, e in enumerate(self.external_events)
                    }
                    for d in drop:
                        did = str(d.get("id") or "")
                        if did in self._external_event_jam_ids:
                            self._external_event_jam_ids.discard(did)
                            self.external_jams = [
                                j for j in self.external_jams if j.get("id") != f"ext-event:{did}"
                            ]

            ev_type = str(stored.get("type") or "").lower()
            resolved = stored.get("resolved_at") is not None
            jam_id = f"ext-event:{eid}"
            should_jam = (
                not resolved
                and ev_type in self._EXTERNAL_EVENT_JAM_TYPES
                and isinstance(stored.get("latitude"), (int, float))
                and isinstance(stored.get("longitude"), (int, float))
            )
            if should_jam:
                radius = stored.get("radius_m")
                half_side = float(radius) if isinstance(radius, (int, float)) and radius > 0 else 30.0
                polygon = self._square_polygon_around(
                    float(stored["latitude"]), float(stored["longitude"]), half_side
                )
                jam_entry = {
                    "id": jam_id,
                    "polygon": polygon,
                    "source": "external_events",
                    "eventType": ev_type,
                    "severity": stored.get("severity"),
                }
                self.external_jams = [j for j in self.external_jams if j.get("id") != jam_id]
                self.external_jams.append(jam_entry)
                self._external_event_jam_ids.add(eid)
                # El cuadrado es provisional: se sustituye por el tramo real de calle.
                asyncio.create_task(self._refine_external_jam(
                    jam_id, float(stored["latitude"]), float(stored["longitude"]), max(120.0, half_side * 2),
                ))
            elif eid in self._external_event_jam_ids:
                self._external_event_jam_ids.discard(eid)
                self.external_jams = [j for j in self.external_jams if j.get("id") != jam_id]

            now = _iso()
            self.event_source_status.update(
                {
                    "lastConsumeAt": now,
                    "consumedTotal": int(self.event_source_status.get("consumedTotal") or 0) + 1,
                    "consumedEvents": int(self.event_source_status.get("consumedEvents") or 0) + 1,
                    "lastError": None,
                }
            )

            new_emergency_id = self._maybe_create_emergency_from_external_event(stored)

        if new_emergency_id is not None:
            await self._route_external_emergency_through_hitl(new_emergency_id)

    async def _refine_external_jam(self, jam_id: str, lat: float, lon: float, length_m: float) -> None:
        """Convierte el corte provisional (cuadrado) en un tramo de la calle real."""
        try:
            seg = await road_segment(self._http, lat, lon, length_m=min(400.0, length_m))
        except Exception:
            _logger.debug("No se pudo trazar el corte %s sobre la calle", jam_id, exc_info=True)
            return
        if seg is None:
            return
        async with self._lock:
            for jam in self.external_jams:
                if jam.get("id") == jam_id:
                    jam["polygon"] = [[a, b] for a, b in seg.polygon]
                    jam["street"] = seg.street
                    break

    def _maybe_create_emergency_from_external_event(
        self, event: dict[str, Any]
    ) -> str | None:
        """Convert qualifying external events into dispatchable emergencies.

        Caller already holds ``self._lock``. Returns the new emergency id or
        None if the event was filtered out.
        """
        ev_id = str(event.get("id") or "").strip()
        if not ev_id or ev_id in self._external_dispatched_ids:
            return None
        ev_type = str(event.get("type") or "").lower()
        emergency_type = self._EXTERNAL_EVENT_DISPATCH_TYPES.get(ev_type)
        if emergency_type is None:
            return None
        if event.get("resolved_at"):
            return None
        try:
            lat = float(event["latitude"])
            lon = float(event["longitude"])
        except (KeyError, TypeError, ValueError):
            return None
        started_at = event.get("started_at")
        if started_at:
            try:
                started_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - started_dt).total_seconds()
                if age > self._EXTERNAL_EVENT_DISPATCH_MAX_AGE_S:
                    return None
            except (TypeError, ValueError):
                pass

        eid = f"ext-{ev_id}"
        self._external_dispatched_ids.add(ev_id)
        if len(self._external_dispatched_ids) > self._EXTERNAL_EVENT_DISPATCH_DEDUPE_CAP:
            # Trim oldest by recreating set; cap is a soft guard, exact LRU not needed.
            self._external_dispatched_ids = set(list(self._external_dispatched_ids)[-self._EXTERNAL_EVENT_DISPATCH_DEDUPE_CAP:])

        title_raw = str(event.get("title") or "External event").strip() or "External event"
        title = title_raw
        description = str(event.get("description") or "").strip() or None
        severity = event.get("severity") or "medium"
        now_iso = _iso()
        self.emergencies.append(
            {
                "id": eid,
                "title": title,
                "description": description,
                "latitude": lat,
                "longitude": lon,
                "status": "pending",
                "assignedAmbulanceId": None,
                "emergencyType": emergency_type,
                "severity": severity,
                "source": "external_feed",
                "createdAt": now_iso,
            }
        )
        self._mission_tracking[eid] = {
            "created_at": now_iso,
            "emergency_type": emergency_type,
            "lat": lat, "lon": lon,
            "jams_crossed": 0,
            "rerouted_times": 0,
            "companions_dispatched": [],
            "source": "external_feed",
        }
        self._external_em_window.append(time.monotonic())
        self._events.emit_event(
            "emergency", eid, "created",
            payload={
                "lat": lat, "lon": lon, "title": title,
                "emergencyType": emergency_type, "severity": severity,
                "source": "external_feed", "externalEventId": ev_id,
            },
            actor="external_feed", tick=self.tick,
        )
        return eid

    async def _route_external_emergency_through_hitl(self, eid: str) -> None:
        """Dispatch gate identical to ``add_emergency``."""
        if self._is_ai_autonomous() and self._ai_engine is not None:
            asyncio.create_task(self._create_dispatch_proposal(eid))
        elif self.dispatch_requires_approval and self._ai_engine is not None:
            asyncio.create_task(self._create_dispatch_proposal(eid))
        else:
            asyncio.create_task(self._dispatch_emergency_safe(eid))

    async def ingest_weather_reading(self, reading: dict[str, Any]) -> None:
        """Upsert de una lectura meteorológica. Mantiene latest + ring de 20.

        While an operator weather override is active (`_weather_override_until`),
        live readings are appended to history but do not displace the
        overridden `weather_by_station[station_id]` values used by the dispatch
        scoring + ETA pipelines.
        """
        station_id = str(reading.get("station_id") or "").strip()
        if not station_id:
            return
        async with self._lock:
            stored = dict(reading)
            stored["receivedAt"] = _iso()
            override_active = time.monotonic() < self._weather_override_until
            if not override_active or station_id not in self.weather_by_station:
                self.weather_by_station[station_id] = stored
            history = self._weather_history.get(station_id)
            if history is None:
                history = deque(maxlen=self._WEATHER_HISTORY)
                self._weather_history[station_id] = history
            history.append(stored)
            now = _iso()
            self.event_source_status.update(
                {
                    "lastConsumeAt": now,
                    "consumedTotal": int(self.event_source_status.get("consumedTotal") or 0) + 1,
                    "consumedWeather": int(self.event_source_status.get("consumedWeather") or 0) + 1,
                    "lastError": None,
                }
            )

    def weather_station_history(self, station_id: str) -> list[dict[str, Any]]:
        history = self._weather_history.get(station_id)
        return [r.copy() for r in (history or [])]

    def set_event_source_status(self, status: dict[str, Any]) -> None:
        merged = dict(self.event_source_status)
        for key in (
            "enabled",
            "status",
            "source",
            "lastError",
        ):
            if key in status:
                merged[key] = status[key]
        if "consumedTotal" in status:
            merged["consumedTotal"] = int(status["consumedTotal"] or 0)
        if "consumedEvents" in status:
            merged["consumedEvents"] = int(status["consumedEvents"] or 0)
        if "consumedWeather" in status:
            merged["consumedWeather"] = int(status["consumedWeather"] or 0)
        if "lastConsumeAt" in status:
            merged["lastConsumeAt"] = status["lastConsumeAt"]
        self.event_source_status = merged

    async def apply_sim_control(self, action: str | None, speed: float | None) -> None:
        """Punto de entrada único del operador para controlar el motor.

        Args:
            action: ``"play"`` | ``"pause"`` | ``"reset"`` o None.
            speed: Factor de velocidad en ``[0.1, 20.0]`` o None.
        """
        did_reset = False
        async with self._lock:
            if action == "play":
                self.paused = False
            elif action == "pause":
                self.paused = True
            elif action == "reset":
                for a in self.ambulances:
                    self._telemetry.reset_ambulance(str(a["id"]))
                self.ambulances.clear()
                self.emergencies.clear()
                self.companions.clear()
                self.jams.clear()
                self.external_jams.clear()
                self.pois = default_pois_copy()
                self.tick = 0
                self._resolved_emergencies = 0
                self._comms_log.clear()
                self.unit_messages.clear()
                self.paused = True
                did_reset = True
            if speed is not None:
                self.speed_multiplier = max(0.1, min(20.0, speed))
        if did_reset:
            await self.seed_default_fleet_if_empty()

    async def add_emergency(
        self,
        lat: float,
        lon: float,
        title: str | None,
        emergency_type: str = "medical",
        description: str | None = None,
    ) -> str:
        """Registra una emergencia nueva y arranca el despacho.

        Lógica de ruteo según configuración:
            - **IA autónoma**: siempre se crea `dispatch_request` para la
              IA, que auto-aprueba y ejecuta.
            - **HITL con approval**: `dispatch_request` pendiente de
              operador.
            - **HITL sin approval**: auto-dispatch clásico por proximidad.

        Args:
            lat: Latitud del incidente.
            lon: Longitud del incidente.
            title: Título breve o None (→ ``"Emergencia"``).
            emergency_type: ``"medical"``, ``"traffic"``, ``"fire"``, etc.
                Usado por la IA para priorizar recursos y companions.
            description: Texto libre del reporte (PWA ciudadana lo rellena
                con la transcripción de voz).

        Returns:
            UUID de la emergencia creada.
        """
        # La dirección del aviso: se ajusta a la calle más cercana (máx. 80 m).
        snapped = await snap_to_road(self._http, lat, lon, max_snap_m=80.0)
        if snapped is not None and snapped.snapped:
            lat, lon = snapped.lat, snapped.lon
        async with self._lock:
            eid = str(uuid4())
            now_iso = _iso()
            em_row: dict[str, Any] = {
                "id": eid,
                "title": title or "Emergencia",
                "description": description,
                "latitude": lat,
                "longitude": lon,
                "status": "pending",
                "assignedAmbulanceId": None,
                "emergencyType": emergency_type or "medical",
                "createdAt": now_iso,
            }
            if snapped is not None and snapped.street:
                em_row["street"] = snapped.street
            self._emergency_kind(em_row)
            self.emergencies.append(em_row)
            self._mission_tracking[eid] = {
                "created_at": now_iso,
                "emergency_type": emergency_type or "medical",
                "lat": lat, "lon": lon,
                "jams_crossed": 0,
                "rerouted_times": 0,
                "companions_dispatched": [],
            }
        self._events.emit_event(
            "emergency", eid, "created",
            payload={
                "lat": lat, "lon": lon, "title": title,
                "emergencyType": emergency_type, "description": description,
            },
            actor="citizen", tick=self.tick,
        )
        # Autónomo → siempre via propuesta IA (que se auto-aprueba y ejecuta).
        # HITL con dispatch_requires_approval → propuesta pendiente de operador.
        # HITL sin approval requirement → auto-dispatch clásico.
        if self._is_ai_autonomous() and self._ai_engine is not None:
            asyncio.create_task(self._create_dispatch_proposal(eid))
        elif self.dispatch_requires_approval and self._ai_engine is not None:
            asyncio.create_task(self._create_dispatch_proposal(eid))
        else:
            asyncio.create_task(self._dispatch_emergency_safe(eid))
        return eid

    async def _create_dispatch_proposal(self, eid: str) -> None:
        """In HITL dispatch mode, create a proposal instead of auto-dispatching."""
        try:
            em = self._emergency_by_id(eid)
            if not em:
                return
            await self._ai_engine._create_proposal(
                "", "dispatch_request",
                {"emergencyId": eid, "title": em.get("title", ""), "description": em.get("description", "")},
                "despacho",
            )
        except Exception:
            _logger.exception("Failed to create dispatch proposal for %s, falling back to auto-dispatch", eid)
            await self._dispatch_emergency_safe(eid)

    async def _dispatch_emergency_safe(self, eid: str) -> None:
        try:
            async with self._lock:
                await self._dispatch_emergency(eid)
        except Exception:
            _logger.exception("Fallo al despachar emergencia %s", eid)

    async def add_jam(self, polygon: list[list[float]]) -> str:
        """Registra una zona de atasco y reencamina ambulancias afectadas.

        Al añadir el polígono recorre las unidades en fase de ruta activa
        (RESPONDING, REFUELING, STAGING, TRANSPORTING) y recalcula la ruta
        si el tramo restante cruza el polígono.

        Args:
            polygon: Lista de pares ``[lat, lon]`` con al menos 3 vértices.

        Returns:
            UUID del atasco creado.
        """
        async with self._lock:
            jid = str(uuid4())
            poly_ll = [[float(p[0]), float(p[1])] for p in polygon]
            self.jams.append({"id": jid, "polygon": poly_ll})
            jam_tup = [(float(p[0]), float(p[1])) for p in poly_ll]
            for amb in self.ambulances:
                st = infer_fsm_state(amb)
                if st not in (
                    AmbulanceState.RESPONDING,
                    AmbulanceState.REFUELING,
                    AmbulanceState.STAGING,
                    AmbulanceState.TRANSPORTING,
                ):
                    continue
                if not amb.get("routeCoords"):
                    continue
                await self._reroute_ambulance_if_hits_jam_polygon(amb, jam_tup)
        self._events.emit_event(
            "jam", jid, "created",
            payload={"polygon": poly_ll, "vertices": len(poly_ll)},
            actor="operator", tick=self.tick,
        )
        return jid

    async def add_poi(self, kind: str, name: str, lat: float, lon: float) -> str:
        """Crea un POI (hospital, gasolinera, tipo custom 'place').

        Args:
            kind: Id de `entity_types` de tipo ``"place"``.
            name: Nombre visible (p.ej. ``"Hospital Gregorio Marañón"``).
            lat: Latitud.
            lon: Longitud.

        Returns:
            UUID del POI creado.

        Raises:
            ValueError: Si ``kind`` no está registrado o no es
                ``kind == "place"``.
        """
        async with self._lock:
            et = self._entity_type_by_id(kind)
            if not et or et.get("kind") != "place":
                raise ValueError(f"Tipo de lugar no registrado o no es un lugar: {kind}")
            pid = str(uuid4())
            self.pois.append(
                {
                    "id": pid,
                    "kind": kind,
                    "name": name,
                    "latitude": lat,
                    "longitude": lon,
                }
            )
        self._events.emit_event(
            "poi", pid, "created",
            payload={"kind": kind, "name": name, "lat": lat, "lon": lon},
            actor="operator", tick=self.tick,
        )
        return pid

    async def assign_emergency(self, ambulance_id: str, emergency_id: str) -> bool:
        """Fuerza la asignación manual (operador o IA) de una unidad.

        Saltándose el ranking por proximidad/fuel. Útil desde el panel de
        fleet o propuestas HITL aprobadas donde la IA eligió la unidad.

        Args:
            ambulance_id: UUID de la unidad.
            emergency_id: UUID de la emergencia.

        Returns:
            True si la asignación se ejecutó. False si alguno no existe
            o la emergencia ya está resuelta.
        """
        async with self._lock:
            amb = next((a for a in self.ambulances if str(a["id"]) == ambulance_id), None)
            em = self._emergency_by_id(emergency_id)
            if not amb or not em:
                return False
            if em.get("status") == "resolved":
                return False
            start = (float(amb["latitude"]), float(amb["longitude"]))
            dest = (float(em["latitude"]), float(em["longitude"]))
            amb["assignedEmergencyId"] = emergency_id
            amb["missionPhase"] = "to_emergency"
            amb["missionStatus"] = "EN_ROUTE"
            amb["refuelPending"] = False
            amb["pendingEmergencyId"] = None
            amb["fsmState"] = AmbulanceState.RESPONDING.value
            em["status"] = "assigned"
            em["assignedAmbulanceId"] = ambulance_id
            route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
            amb["routeCoords"] = route if len(route) >= 2 else None
            if rlim is not None:
                amb["roadSpeedLimitKmh"] = rlim
            amb["routeProgressM"] = 0.0
            amb["routeSpeedMs"] = ROUTE_SPEED_MS
            amb["routeDurationS"] = route_duration_s
            return True

    async def remove_ambulance(self, aid: str) -> bool:
        """Elimina una unidad del mapa.

        Si llevaba una emergencia asignada, ésta vuelve a ``pending`` para
        que otra unidad (o la IA) la retome. Si llevaba paciente, se
        descarta (coherente con que la unidad deja de existir).

        Returns:
            True si se eliminó; False si el id no existe.
        """
        async with self._lock:
            amb = next((a for a in self.ambulances if str(a["id"]) == aid), None)
            if amb is None:
                return False
            eid = amb.get("assignedEmergencyId")
            if eid:
                em = self._emergency_by_id(str(eid))
                if em and em.get("status") != "resolved":
                    em["status"] = "pending"
                    em["assignedAmbulanceId"] = None
            self._telemetry.reset_ambulance(aid)
            self.ambulances = [a for a in self.ambulances if str(a["id"]) != aid]
        self._events.emit_event("ambulance", aid, "deleted", actor="operator", tick=self.tick)
        return True

    async def remove_companion(self, cid: str) -> bool:
        """Elimina un companion (heli, policía) del mapa."""
        async with self._lock:
            before = len(self.companions)
            self.companions = [c for c in self.companions if str(c["id"]) != cid]
            removed = len(self.companions) < before
        if removed:
            self._events.emit_event("companion", cid, "deleted", actor="operator", tick=self.tick)
        return removed

    async def remove_emergency(self, eid: str) -> bool:
        """Cancela y elimina una emergencia del mapa.

        Si tenía una unidad asignada, la libera a IDLE para que quede
        disponible.
        """
        async with self._lock:
            em = self._emergency_by_id(eid)
            if em is None:
                return False
            aid = em.get("assignedAmbulanceId")
            if aid:
                amb = next((a for a in self.ambulances if str(a["id"]) == str(aid)), None)
                if amb is not None:
                    amb["assignedEmergencyId"] = None
                    amb["missionPhase"] = "idle"
                    amb["missionStatus"] = "INACTIVE"
                    amb["fsmState"] = AmbulanceState.IDLE.value
                    amb["routeCoords"] = None
                    amb["routeProgressM"] = 0.0
            # También libera companions asignados
            self.companions = [
                c for c in self.companions if str(c.get("assignedEmergencyId")) != eid
            ]
            self.emergencies = [e for e in self.emergencies if str(e["id"]) != eid]
        self._events.emit_event("emergency", eid, "deleted", actor="operator", tick=self.tick)
        # Descartar tracking sin outcome (cancelación).
        self._mission_tracking.pop(eid, None)
        return True

    async def remove_poi(self, pid: str) -> bool:
        """Elimina un POI del mapa.

        Si alguna unidad lo tenía como destino (``refuelPoiId`` o
        ``stagingHospitalId``), se limpia para evitar rutas colgadas.
        """
        async with self._lock:
            before = len(self.pois)
            self.pois = [p for p in self.pois if str(p["id"]) != pid]
            if len(self.pois) == before:
                return False
            for amb in self.ambulances:
                if str(amb.get("refuelPoiId")) == pid:
                    amb["refuelPoiId"] = None
                    amb["refuelPending"] = False
                    if amb.get("missionPhase") == "to_refuel":
                        amb["missionPhase"] = "idle"
                        amb["routeCoords"] = None
                        amb["routeProgressM"] = 0.0
                        amb["fsmState"] = AmbulanceState.IDLE.value
                if str(amb.get("stagingHospitalId")) == pid:
                    amb["stagingHospitalId"] = None
                    if amb.get("missionPhase") in ("to_staging", "to_hospital"):
                        amb["missionPhase"] = "idle"
                        amb["routeCoords"] = None
                        amb["routeProgressM"] = 0.0
                        amb["fsmState"] = AmbulanceState.IDLE.value
        self._events.emit_event("poi", pid, "deleted", actor="operator", tick=self.tick)
        return True

    async def remove_jam(self, jid: str) -> bool:
        """Elimina una zona de atasco del mapa."""
        async with self._lock:
            before = len(self.jams)
            self.jams = [j for j in self.jams if str(j["id"]) != jid]
            removed = len(self.jams) < before
        if removed:
            self._events.emit_event("jam", jid, "deleted", actor="operator", tick=self.tick)
        return removed
