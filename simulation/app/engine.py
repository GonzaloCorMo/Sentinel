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
from uuid import NAMESPACE_DNS, uuid4, uuid5

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
from .geometry_poly import polyline_intersects_polygon
from .route_nav import advance_along_polyline, haversine_m, heading_deg_towards, remaining_route_coords
from .dispatch_scoring import (
    ScoringContext,
    ScoringRegistry,
    compute_weather_factor,
    default_registry,
)
from .synthetic_titles import SYNTHETIC_TITLES
from .routing import detour_candidates_from_jam, fetch_route, osrm_base_url

FUEL_LOW_PCT = 25.0
IDLE_REFUEL_FUEL_PCT = 20.0
IDLE_REFUEL_COOLDOWN_TICKS = 120
ROUTE_SPEED_MS = 12.0
FUEL_DRAIN_PCT_PER_M = 0.0032
BATTERY_DRAIN_PCT_PER_M = 0.0038
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


def _seed_poi_id(seed: str) -> str:
    """UUID determinista para POIs por defecto (misma forma que str(uuid4()))."""
    return str(uuid5(NAMESPACE_DNS, f"hpe-sentinel-default-poi-{seed}"))


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
        self.tick = 0
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
        self.aruba_inventory: dict[str, Any] = {
            "enabled": False,
            "status": "idle",
            "lastSyncAt": None,
            "fetchedPois": 0,
            "fetchedRoads": 0,
            "updatedPois": 0,
            "updatedRoads": 0,
            "itemsUpdated": 0,
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
        self.external_roads: list[dict[str, Any]] = []

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

    def _sync_energy_from_tele(self, amb: dict[str, Any], tele: dict[str, Any]) -> None:
        """Sincroniza energía amb↔tele preservando el recurso primario drenado.

        Combustion → fuel primario, battery secundario cosmético.
        Electric/unique → battery primario, fuel constante 100.
        El wobble del motor mecánico (clamp [5,100]) no debe sobrescribir el drenaje real.
        """
        mech = tele.get("mechanical") or {}
        if self._powertrain_for_amb(amb) == "combustion":
            amb["batteryLevel"] = float(mech.get("batteryPct", amb.get("batteryLevel", 100.0)))
            real_fuel = float(amb.get("fuelLevel", 100.0))
            mech["fuelLevelPct"] = round(real_fuel, 1)
        else:
            real_batt = float(amb.get("batteryLevel", 100.0))
            mech["batteryPct"] = round(real_batt, 1)
            mech["fuelLevelPct"] = 100.0
            amb["fuelLevel"] = 100.0

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
            "motorState": "RUNNING" if not self.paused else "PAUSED",
            "networkStatus": self.network.copy(),
            "linkState": self.channels.active_link.value,
            "ambulances": [self._ambulance_public(a) for a in self.ambulances],
            "emergencies": [e.copy() for e in self.emergencies],
            "pois": [p.copy() for p in self.pois],
            "jams": [j.copy() for j in self.jams] + [j.copy() for j in self.external_jams],
            "companions": [c.copy() for c in self.companions],
            "commsRecent": list(self._comms_log)[-80:],
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
            "arubaInventory": dict(self.aruba_inventory),
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
        return any(e.get("status") in ("pending", "assigned") for e in self.emergencies)

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
        lim = self._effective_road_limit_for_route(coords, lim)
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
                new_lim = self._effective_road_limit_for_route(new_coords, new_lim)
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

        return best_coords, best_lim

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
        if phase == "to_refuel":
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
        elif phase == "to_emergency":
            eid = amb.get("assignedEmergencyId")
            if eid:
                e = self._emergency_by_id(str(eid))
                if e:
                    e["status"] = "resolved"
                    self._resolved_emergencies += 1
                tr = self._mission_tracking.get(str(eid))
                if tr is not None:
                    tr["arrived_on_scene_at"] = _iso()
                self._events.emit_event(
                    "emergency", str(eid), "phase_change",
                    payload={"phase": "on_scene"}, actor="engine", tick=self.tick,
                )
                # Recuerda el eid para cerrar el outcome cuando llegue al hospital.
                amb["_lastEmergencyId"] = str(eid)
            amb["assignedEmergencyId"] = None
            amb["hasPatient"] = True
            import random as _rng
            amb["patientSeverity"] = _rng.choices(
                ["stable", "moderate", "critical"], weights=[0.5, 0.3, 0.2],
            )[0]
            hosp = self._nearest_poi(amb, "hospital")
            if hosp:
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
            else:
                amb["hasPatient"] = False
                amb["patientSeverity"] = None
                amb["missionPhase"] = "idle"
                amb["routeCoords"] = None
                amb["routeProgressM"] = 0.0
                amb["missionStatus"] = "INACTIVE"
                amb["fsmState"] = AmbulanceState.IDLE.value
                await self._try_assign_pending_emergency_to_ambulance(amb)
        elif phase == "to_hospital":
            # Cierra outcome de la misión (label ML) y emite evento resolved.
            eid_done = amb.pop("_lastEmergencyId", None)
            if eid_done:
                self._emit_mission_outcome(eid_done, amb)
                self._events.emit_event(
                    "emergency", eid_done, "resolved",
                    payload={"ambulanceId": str(amb["id"])},
                    actor="engine", tick=self.tick,
                )
            amb["hasPatient"] = False
            amb["patientSeverity"] = None
            amb["missionPhase"] = "idle"
            amb["stagingHospitalId"] = None
            amb["routeCoords"] = None
            amb["routeProgressM"] = 0.0
            amb["missionStatus"] = "INACTIVE"
            amb["fsmState"] = AmbulanceState.IDLE.value
            await self._try_assign_pending_emergency_to_ambulance(amb)
        elif phase == "to_staging":
            self._apply_energy_refill(amb, tele)
            amb["missionPhase"] = "idle"
            amb["stagingHospitalId"] = None
            amb["routeCoords"] = None
            amb["routeProgressM"] = 0.0
            amb["missionStatus"] = "INACTIVE"
            amb["fsmState"] = AmbulanceState.IDLE.value
            await self._try_assign_pending_emergency_to_ambulance(amb)

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
        # Vehículos bajo control manual (teclado desde la PWA): el motor NO
        # mueve. Posición la empuja el cliente vía /api/sim/vehicle/{id}/position.
        # Solo actualizamos telemetría para que el stream siga vivo.
        if amb.get("manualControl"):
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
            amb["telemetry"] = tele
            amb["updatedAt"] = _iso()
            return tele, {"id": aid, "telemetry": tele}
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
            local_limit_kmh = self._road_limit_for_position(
                float(amb.get("latitude") or coords[0][0]),
                float(amb.get("longitude") or coords[0][1]),
            )
            if local_limit_kmh is not None:
                amb["roadSpeedLimitKmh"] = local_limit_kmh
            speed_ms = float(amb.get("routeSpeedMs", ROUTE_SPEED_MS))
            road_limit_kmh = amb.get("roadSpeedLimitKmh")
            if road_limit_kmh is not None:
                try:
                    speed_ms = min(speed_ms, max(2.5, float(road_limit_kmh) / 3.6))
                except Exception:
                    pass
            try:
                weather_factor = self.weather_speed_factor(
                    float(amb.get("latitude") or coords[0][0]),
                    float(amb.get("longitude") or coords[0][1]),
                )
                amb["weatherFactor"] = round(weather_factor, 3)
                speed_ms = max(2.5, speed_ms * weather_factor)
            except Exception:
                pass
            dt_s = max(dt_sim, 1e-6)
            delta_m = speed_ms * dt_sim
            prev_lat = float(amb.get("latitude") or coords[0][0])
            prev_lon = float(amb.get("longitude") or coords[0][1])
            prog = float(amb.get("routeProgressM", 0.0))
            prev_speed = float(amb.get("_prevSpeedMs", speed_ms))
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
                b = max(0.0, b - (delta_m * BATTERY_DRAIN_PCT_PER_M) - (abs(long_accel) * 0.012))
                tele["mechanical"]["batteryPct"] = round(b, 1)
                amb["batteryLevel"] = tele["mechanical"]["batteryPct"]
            else:
                fu = float(amb.get("fuelLevel", tele["mechanical"]["fuelLevelPct"]))
                fu = max(0.0, fu - (delta_m * FUEL_DRAIN_PCT_PER_M) - (abs(long_accel) * 0.01))
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
        tele = self._telemetry.tick(aid, self.tick, dt_sim, amb)
        pt = self._powertrain_of(amb)
        if pt == "electric":
            b = float(amb.get("batteryLevel", tele["mechanical"]["batteryPct"]))
            # Consumo basal en reposo.
            b = max(0.0, b - dt_sim * 0.003)
            tele["mechanical"]["batteryPct"] = round(b, 1)
            amb["batteryLevel"] = tele["mechanical"]["batteryPct"]
        else:
            f = float(amb.get("fuelLevel", tele["mechanical"]["fuelLevelPct"]))
            f = max(0.0, f - dt_sim * 0.002)
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
                    await self._training_mode_tick()
                if self.tick % FLUSH_EVERY_N_TICKS == 0:
                    await self._tele_writer.flush()
                    await self._events.maybe_flush()
                payload = {
                    "tick": self.tick,
                    "units": len(self.ambulances),
                    "telemetryBatch": payload_units,
                }
            await self.channels.route_payload(self.network, payload, self.tick)
        self._running = False

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
        manual_control: bool = False,
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
            manual_control: Si True, el motor omite la lógica automática de
                este vehículo (posición la empuja el cliente vía
                ``/api/sim/vehicle/{id}/position``).

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
            if manual_control:
                row["manualControl"] = True
            self._normalize_energy_by_powertrain(row, None)
            self.ambulances.append(row)
        self._events.emit_event(
            "ambulance", aid, "created",
            payload={
                "lat": lat, "lon": lon,
                "entityTypeId": entity_type_id or self.default_ambulance_entity_type_id(),
                "displayLabel": display_label,
                "manualControl": manual_control,
            },
            actor="operator", tick=self.tick,
        )
        return aid

    async def update_vehicle_position(
        self, aid: str, lat: float, lon: float,
        heading_deg: float | None = None, speed_kmh: float | None = None,
    ) -> bool:
        """Actualiza la posición de un vehículo bajo control manual (ignorado
        para los auto-dirigidos por el motor)."""
        async with self._lock:
            amb = next((a for a in self.ambulances if str(a["id"]) == aid), None)
            if amb is None or not amb.get("manualControl"):
                return False
            amb["latitude"] = float(lat)
            amb["longitude"] = float(lon)
            if heading_deg is not None:
                amb["headingDeg"] = float(heading_deg)
            if speed_kmh is not None:
                amb["speedKmh"] = float(speed_kmh)
            amb["updatedAt"] = _iso()
            return True

    async def advance_manual_phase(self, aid: str) -> dict[str, Any]:
        """Avanza la fase de misión de un vehículo bajo control manual.

        Para vehículos con `manualControl=True`, el motor no avanza la FSM
        por su cuenta. Este helper lo hace explícitamente al pulsar el
        operador "He llegado" / "Entregar paciente" desde la PWA.

        Transiciones:
            - ``to_emergency`` → resuelve emergencia, activa `hasPatient`,
              calcula ruta a hospital más cercano, fase ``to_hospital``.
            - ``to_hospital`` → entrega paciente, vuelve a ``idle``.
            - ``idle`` u otra → intenta asignar una emergencia pendiente.

        Args:
            aid: UUID del vehículo manual.

        Returns:
            Dict con claves ``ok`` (bool), ``phase`` (siguiente fase) y,
            opcionalmente, ``hospitalId``. Si el vehículo no existe o no
            es manual, ``{"ok": False, "reason": "no-manual-vehicle"}``.
        """
        from .ambulance_fsm import AmbulanceState

        async with self._lock:
            amb = next((a for a in self.ambulances if str(a["id"]) == aid), None)
            if amb is None or not amb.get("manualControl"):
                return {"ok": False, "reason": "no-manual-vehicle"}

            phase = str(amb.get("missionPhase") or "")

            if phase == "to_emergency":
                eid = amb.get("assignedEmergencyId")
                if eid:
                    e = self._emergency_by_id(str(eid))
                    if e:
                        e["status"] = "resolved"
                        self._resolved_emergencies += 1
                amb["assignedEmergencyId"] = None
                amb["hasPatient"] = True
                import random as _rng
                amb["patientSeverity"] = _rng.choices(
                    ["stable", "moderate", "critical"], weights=[0.5, 0.3, 0.2],
                )[0]
                hosp = self._nearest_poi(amb, "hospital")
                if hosp:
                    amb["missionPhase"] = "to_hospital"
                    amb["stagingHospitalId"] = hosp["id"]
                    amb["targetHospitalId"] = hosp["id"]
                    amb["missionStatus"] = "EN_ROUTE"
                    amb["fsmState"] = AmbulanceState.TRANSPORTING.value
                    start = (float(amb["latitude"]), float(amb["longitude"]))
                    dest = (float(hosp["latitude"]), float(hosp["longitude"]))
                    route, rlim, route_duration_s = await self._route_with_jam_avoidance(start, dest)
                    amb["routeCoords"] = route if len(route) >= 2 else None
                    if rlim is not None:
                        amb["roadSpeedLimitKmh"] = rlim
                    amb["routeProgressM"] = 0.0
                return {"ok": True, "phase": "to_hospital", "hospitalId": amb.get("targetHospitalId")}

            if phase == "to_hospital":
                amb["hasPatient"] = False
                amb["patientSeverity"] = None
                amb["missionPhase"] = "idle"
                amb["routeCoords"] = None
                amb["routeProgressM"] = 0.0
                amb["missionStatus"] = "INACTIVE"
                amb["fsmState"] = AmbulanceState.IDLE.value
                return {"ok": True, "phase": "idle"}

            # Sin misión: intenta recoger una emergencia pendiente
            await self._try_assign_pending_emergency_to_ambulance(amb)
            return {"ok": True, "phase": amb.get("missionPhase") or "idle"}

    async def _training_mode_tick(self) -> None:
        """Genera emergencias periódicas mientras ``training_mode`` está activo.

        Objetivo: alimentar el pipeline ML sin intervención humana. Cadencia
        controlada por ``training_emergencies_per_min``. Si no hay POIs
        (hospitales/gasolineras) los crea automáticamente. Si no hay
        ambulancias libres, spawnea más hasta un tope razonable.
        """
        import random as _rng
        # Bootstrap infra si falta.
        hospitals = [p for p in self.pois if p.get("kind") == "hospital"]
        gas = [p for p in self.pois if p.get("kind") == "gas_station"]
        spawn_lat, spawn_lon = default_spawn()
        if not hospitals:
            for i in range(2):
                lat = spawn_lat + _rng.uniform(-0.02, 0.02)
                lon = spawn_lon + _rng.uniform(-0.02, 0.02)
                pid = str(uuid4())
                self.pois.append({
                    "id": pid, "kind": "hospital",
                    "name": f"Hospital Auto {i + 1}",
                    "latitude": lat, "longitude": lon,
                })
                self._events.emit_event(
                    "poi", pid, "created",
                    payload={"kind": "hospital", "auto": True, "lat": lat, "lon": lon},
                    actor="engine", tick=self.tick,
                )
        if not gas:
            for i in range(2):
                lat = spawn_lat + _rng.uniform(-0.025, 0.025)
                lon = spawn_lon + _rng.uniform(-0.025, 0.025)
                pid = str(uuid4())
                self.pois.append({
                    "id": pid, "kind": "gas_station",
                    "name": f"Gasolinera Auto {i + 1}",
                    "latitude": lat, "longitude": lon,
                })
                self._events.emit_event(
                    "poi", pid, "created",
                    payload={"kind": "gas_station", "auto": True, "lat": lat, "lon": lon},
                    actor="engine", tick=self.tick,
                )
        # Flota mínima: 3 ambulancias activas para generar variedad.
        if len(self.ambulances) < 3:
            aid = str(uuid4())
            row: dict[str, Any] = {
                "id": aid,
                "missionStatus": "INACTIVE",
                "speedKmh": 0.0,
                "fuelLevel": 100.0,
                "patientStatus": "none",
                "locationLabel": "AutoFleet",
                "latitude": spawn_lat + _rng.uniform(-0.003, 0.003),
                "longitude": spawn_lon + _rng.uniform(-0.003, 0.003),
                "updatedAt": _iso(),
                "entityTypeId": self.default_ambulance_entity_type_id(),
                "displayLabel": f"AUTO-{len(self.ambulances) + 1:03d}",
            }
            row.update(self._default_amb_fields())
            self.ambulances.append(row)
            self._events.emit_event(
                "ambulance", aid, "created",
                payload={"auto": True, "displayLabel": row["displayLabel"]},
                actor="engine", tick=self.tick,
            )
        # Hybrid spawner: external-feed events count toward target; synthetic fills gap.
        # External rate is wall-clock; sim ticks at sim-time → scale it by 1/speed_mul
        # so accelerated sims still get a full target rate of synthetic emergencies.
        target_rate = max(self.training_emergencies_per_min, 0.1)
        ext_wall_per_min = self._recent_external_emergency_rate_per_min()
        ext_sim_per_min = ext_wall_per_min / max(self.speed_multiplier, 0.1)
        synth_rate = max(0.1, target_rate - ext_sim_per_min)
        ticks_per_sec = max(self.speed_multiplier * 2.0, 1.0)
        ticks_between = max(1, int(60.0 * ticks_per_sec / synth_rate))
        if self.tick - self._training_last_spawn_tick < ticks_between:
            return
        self._training_last_spawn_tick = self.tick
        # Crea emergencia random cerca del hub
        etypes = ["medical", "medical", "medical", "altercation", "mass_casualty"]
        etype = _rng.choice(etypes)
        lat = spawn_lat + _rng.uniform(-0.02, 0.02)
        lon = spawn_lon + _rng.uniform(-0.02, 0.02)
        eid = str(uuid4())
        now_iso = _iso()
        title = _rng.choice(SYNTHETIC_TITLES.get(etype, SYNTHETIC_TITLES["medical"]))
        self.emergencies.append({
            "id": eid, "title": title,
            "description": "Emergencia generada en modo entrenamiento",
            "latitude": lat, "longitude": lon,
            "status": "pending", "assignedAmbulanceId": None,
            "emergencyType": etype,
            "severity": "medium",
            "source": "training",
            "createdAt": now_iso,
        })
        self._mission_tracking[eid] = {
            "created_at": now_iso, "emergency_type": etype,
            "lat": lat, "lon": lon,
            "jams_crossed": 0, "rerouted_times": 0, "companions_dispatched": [],
            "source": "training",
        }
        self._events.emit_event(
            "emergency", eid, "created",
            payload={"source": "training", "emergencyType": etype, "lat": lat, "lon": lon, "title": title},
            actor="engine", tick=self.tick,
        )
        # Dispatch inmediato (bypass de HITL en training mode)
        asyncio.create_task(self._dispatch_emergency_safe(eid))

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

    async def set_control(self, *, paused: bool | None = None, speed: float | None = None) -> None:
        """Variante fine-grained de `apply_sim_control` sin reset.

        Args:
            paused: Valor nuevo para ``self.paused`` o None.
            speed: Nuevo multiplicador en ``[0.1, 20.0]`` o None.
        """
        async with self._lock:
            if paused is not None:
                self.paused = paused
            if speed is not None:
                self.speed_multiplier = max(0.1, min(20.0, speed))

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
            self.paused = True
        await self.seed_default_fleet_if_empty()

    async def apply_aruba_inventory_sync(
        self,
        *,
        normalized_pois: list[dict[str, Any]],
        normalized_roads: list[dict[str, Any]],
        normalized_jams: list[dict[str, Any]] | None = None,
    ) -> tuple[int, int]:
        """Upsert inventory rows from Aruba API into runtime state."""
        updated_pois = 0
        updated_roads = 0
        async with self._lock:
            existing_poi_idx: dict[str, dict[str, Any]] = {
                str(p.get("externalId")): p
                for p in self.pois
                if p.get("source") == "aruba_api" and p.get("externalId")
            }
            for row in normalized_pois:
                ext_id = str(row.get("externalId") or "")
                if not ext_id:
                    continue
                current = existing_poi_idx.get(ext_id)
                if current is None:
                    current = {
                        "id": str(uuid4()),
                        "source": "aruba_api",
                        "externalId": ext_id,
                    }
                    self.pois.append(current)
                current["kind"] = row.get("kind") or "place"
                current["name"] = row.get("name") or "POI Aruba"
                current["latitude"] = float(row.get("latitude"))
                current["longitude"] = float(row.get("longitude"))
                current["rawType"] = row.get("rawType")
                current["address"] = row.get("address")
                current["capacity"] = row.get("capacity")
                current["updatedAt"] = _iso()
                existing_poi_idx[ext_id] = current
                updated_pois += 1

            existing_poi_ids = {str(p.get("externalId") or "") for p in normalized_pois}
            self.external_roads = [
                {
                    "externalId": str(r.get("externalId")),
                    "name": r.get("name") or "Road segment",
                    "roadType": r.get("roadType") or "unknown",
                    "startLat": float(r.get("startLat")),
                    "startLon": float(r.get("startLon")),
                    "endLat": float(r.get("endLat")),
                    "endLon": float(r.get("endLon")),
                    "speedLimitKmh": float(r["speedLimitKmh"]) if r.get("speedLimitKmh") is not None else None,
                    "lanes": int(r["lanes"]) if r.get("lanes") is not None else None,
                    "lengthM": float(r.get("lengthM") or 0.0),
                    "geometry": r.get("geometry") if isinstance(r.get("geometry"), list) else None,
                    "source": "aruba_api",
                    "updatedAt": _iso(),
                }
                for r in normalized_roads
                if str(r.get("externalId") or "")
            ]
            updated_roads = len(self.external_roads)
            self.external_jams = [
                {
                    "id": str(j.get("id")),
                    "polygon": j.get("polygon") or [],
                    "source": "aruba_api",
                }
                for j in (normalized_jams or [])
                if str(j.get("id") or "") and isinstance(j.get("polygon"), list) and len(j.get("polygon") or []) >= 3
            ]

            # prune removed external pois
            self.pois = [
                p
                for p in self.pois
                if not (
                    p.get("source") == "aruba_api"
                    and p.get("externalId")
                    and str(p.get("externalId")) not in existing_poi_ids
                    and p.get("kind") != "hospital"
                    and p.get("kind") != "gas_station"
                )
            ]
            self.aruba_inventory.update(
                {
                    "status": "synced",
                    "lastSyncAt": _iso(),
                    "fetchedPois": len(normalized_pois),
                    "fetchedRoads": len(normalized_roads),
                    "updatedPois": updated_pois,
                    "updatedRoads": updated_roads,
                    "itemsUpdated": updated_pois + updated_roads + len(self.external_jams),
                }
            )
        return updated_pois, updated_roads

    def set_aruba_sync_status(self, status: dict[str, Any]) -> None:
        merged = dict(self.aruba_inventory)
        merged.update(
            {
                "status": status.get("status") or merged.get("status") or "idle",
                "lastSyncAt": status.get("finishedAt") or status.get("lastSyncAt") or _iso(),
                "fetchedPois": int(status.get("fetchedPois") or 0),
                "fetchedRoads": int(status.get("fetchedRoads") or 0),
                "updatedPois": int(status.get("updatedPois") or 0),
                "updatedRoads": int(status.get("updatedRoads") or 0),
                "itemsUpdated": int(status.get("updatedPois") or 0) + int(status.get("updatedRoads") or 0),
                "enabled": bool(status.get("enabled", True)),
                "ok": bool(status.get("ok", True)),
            }
        )
        self.aruba_inventory = merged

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
        title = f"{title_raw} (Pulse)"
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

    def _road_limit_for_position(self, lat: float, lon: float) -> float | None:
        best_d = float("inf")
        best_lim: float | None = None
        for r in self.external_roads:
            lim = r.get("speedLimitKmh")
            if lim is None:
                continue
            d1 = haversine_m(lat, lon, float(r["startLat"]), float(r["startLon"]))
            d2 = haversine_m(lat, lon, float(r["endLat"]), float(r["endLon"]))
            d = min(d1, d2)
            if d < best_d:
                best_d = d
                best_lim = float(lim)
        # only trust nearby road segments
        if best_d <= 350.0:
            return best_lim
        return None

    def _effective_road_limit_for_route(
        self,
        coords: list[tuple[float, float]],
        osrm_limit_kmh: float | None,
    ) -> float | None:
        if not coords:
            return osrm_limit_kmh
        sampled_limits: list[float] = []
        step = max(1, len(coords) // 8)
        for i in range(0, len(coords), step):
            lat, lon = coords[i]
            lim = self._road_limit_for_position(float(lat), float(lon))
            if lim is not None:
                sampled_limits.append(lim)
        ext_limit = min(sampled_limits) if sampled_limits else None
        if osrm_limit_kmh is None:
            return ext_limit
        if ext_limit is None:
            return osrm_limit_kmh
        return min(float(osrm_limit_kmh), float(ext_limit))

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
        async with self._lock:
            eid = str(uuid4())
            now_iso = _iso()
            self.emergencies.append(
                {
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
            )
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
