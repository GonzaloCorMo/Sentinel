"""AI Observer: detecta anomalías, razona con LLM y propone acciones.

Este módulo implementa `AIDecisionEngine`, colaborador de `SimulationEngine`.
Observa el estado del mundo cada ``OBSERVE_INTERVAL_S`` y emite propuestas
(``pending_proposals``) que el operador aprueba en modo HITL, o que la IA
auto-ejecuta en modo autónomo.

Tipos de anomalía:
    - ``dispatch_request`` / ``unattended_emergency`` — emergencia sin unidad
      asignada.
    - ``critical_response_needed`` — emergencia grave; propone companion
      (heli/policía).
    - ``eta_exceeded`` — la ambulancia asignada tarda demasiado.
    - ``fuel_critical`` — combustible por debajo de ``FUEL_CRITICAL_PCT``.
    - ``vitals_critical`` — SpO₂/BPM/GCS fuera de umbrales clínicos.
    - ``smart_dispatch`` — la IA decide crear un tipo nuevo de vehículo.

Modos:
    - ``hitl`` (default): propuesta → operador aprueba → ejecución.
    - ``autonomous``: propuesta → auto-aprobada → ejecución sin revisión.

Para despachar, la IA usa todos los ``entity_types`` registrados (builtin +
custom). Si con LLM disponible ningún tipo encaja, puede registrar uno nuevo
al vuelo vía `engine.register_entity_type`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from uuid import uuid4

from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

OBSERVE_INTERVAL_S = 5.0
COOLDOWN_S = 60.0

FUEL_CRITICAL_PCT = 10.0
SPO2_CRITICAL = 90
BPM_CRITICAL = 140
GCS_CRITICAL = 8
UNATTENDED_EMERGENCY_S = 30.0
ETA_EXCEEDED_S = 300.0

MAX_RESOLVED_LOG = 30


class AIDecisionEngine:
    """Observer IA: vigila el estado y emite propuestas ejecutables.

    Instanciado con una referencia a `SimulationEngine`. Se inyecta en el
    motor vía `engine.set_ai_engine(ai_eng)` durante el lifespan FastAPI.

    Attributes:
        engine: Referencia al `SimulationEngine`.
        pending_proposals: Cola de propuestas (pending/approved/rejected/
            stale/autoExecuted). El dashboard HITL las consume.
        mode: ``"hitl"`` o ``"autonomous"``. Toggle desde el UI.
        _cooldowns: Anti-flapping por tipo+unidad para no proponer lo mismo
            cada tick.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.pending_proposals: list[dict[str, Any]] = []
        self._resolved_log: list[dict[str, Any]] = []
        self._cooldowns: dict[str, float] = {}
        self._stop = asyncio.Event()
        # Las llamadas de fondo al LLM se serializan: con un modelo local, decenas
        # de propuestas simultáneas ocuparían todos los huecos y el chat o los
        # informes del operador quedarían esperando en cola.
        self._llm_slot = asyncio.Semaphore(max(1, int(os.environ.get("AI_OBSERVER_LLM_CONCURRENCY", "1") or "1")))
        self.mode: str = "hitl"

    def stop(self) -> None:
        """Detiene el bucle de observación."""
        self._stop.set()

    def _on_cooldown(self, key: str) -> bool:
        return time.monotonic() < self._cooldowns.get(key, 0.0)

    def _set_cooldown(self, key: str) -> None:
        self._cooldowns[key] = time.monotonic() + COOLDOWN_S

    def get_pending(self) -> list[dict[str, Any]]:
        """Propuestas pendientes de aprobación (solo modo HITL las usa)."""
        return [p for p in self.pending_proposals if p["status"] == "pending"]

    def get_resolved_recent(self) -> list[dict[str, Any]]:
        """Últimas ``MAX_RESOLVED_LOG`` propuestas ya resueltas (any status)."""
        return list(self._resolved_log)

    async def resolve_proposal(self, proposal_id: str, action: str) -> bool:
        """Aprueba/rechaza una propuesta y la ejecuta si procede.

        Antes de ejecutar un ``approved`` en HITL, valida que el estado
        del mundo no haya cambiado (``_is_stale``); si sí, marca la
        propuesta como ``stale`` y no ejecuta.

        Args:
            proposal_id: UUID de la propuesta.
            action: ``"approved"`` | ``"rejected"``.

        Returns:
            True si la propuesta existía y se resolvió.
        """
        for p in self.pending_proposals:
            if p["id"] == proposal_id and p["status"] == "pending":
                # Revalidar estado del mundo antes de ejecutar un approve SOLO
                # en modo HITL. En autónomo la IA gestiona todo, no hay stale:
                # las funciones de ejecución validan internamente.
                if action == "approved" and self.mode != "autonomous":
                    async with self.engine._lock:
                        stale, reason = self._is_stale(p)
                    if stale:
                        p["status"] = "stale"
                        p["stale_reason"] = reason
                        p["resolved_at"] = time.time()
                        self._persist_resolution(proposal_id, "stale")
                        self._push_to_log(p)
                        _logger.info(
                            "AI proposal %s descartada al aprobar (stale): %s",
                            proposal_id, reason,
                        )
                        return True
                p["status"] = action
                p["resolved_at"] = time.time()
                self._persist_resolution(proposal_id, action)
                self._push_to_log(p)
                if action == "approved":
                    await self._execute_approved(p)
                return True
        return False

    def _is_stale(self, proposal: dict[str, Any]) -> tuple[bool, str | None]:
        """Comprueba si la propuesta ya no es accionable por cambio del estado
        del mundo (emergencia resuelta, ambulancia ya no ociosa, etc.).

        Debe llamarse con engine._lock tomado para evitar lecturas inconsistentes.
        """
        atype = proposal.get("anomaly_type")
        detail = proposal.get("anomaly_detail") or {}
        eng = self.engine
        eid = detail.get("emergencyId")

        if atype in (
            "dispatch_request",
            "critical_response_needed",
            "eta_exceeded",
            "smart_dispatch",
            "unattended_emergency",
        ):
            if not eid:
                return False, None
            em = eng._emergency_by_id(eid)
            if em is None:
                return True, "La emergencia ya no existe"
            st = em.get("status")
            if st in ("resolved", "cancelled"):
                return True, f"La emergencia ya fue {st}"
            # Para propuestas que piden asignar ambulancia, si ya hay una asignada
            # a esa emergencia, se considera obsoleta.
            if atype in ("unattended_emergency", "dispatch_request"):
                for amb in eng.ambulances:
                    if amb.get("assignedEmergencyId") == eid:
                        return True, "La emergencia ya tiene ambulancia asignada"
            # Para propuestas de despachar un companion concreto (helicopter, policia),
            # si ya hay uno de ese tipo en ruta, obsoleta.
            dispatch_kind = detail.get("dispatchKind")
            if dispatch_kind:
                for c in eng.companions:
                    if (
                        c.get("assignedEmergencyId") == eid
                        and c.get("kind") == dispatch_kind
                        and c.get("status") != "resolved"
                    ):
                        return True, f"Ya hay {dispatch_kind} despachado a la emergencia"
            return False, None

        if atype == "fuel_critical":
            amb_id = proposal.get("ambulance_id")
            if not amb_id:
                return False, None
            amb = next((a for a in eng.ambulances if str(a["id"]) == amb_id), None)
            if amb is None:
                return True, "La ambulancia ya no existe"
            from .ambulance_fsm import AmbulanceState, infer_fsm_state

            if infer_fsm_state(amb) != AmbulanceState.IDLE:
                return True, "La ambulancia ya no está en reposo"
            if float(amb.get("fuelLevel", 0)) > FUEL_CRITICAL_PCT + 10:
                return True, "El combustible ya se repuso"
            return False, None

        if atype == "vitals_critical":
            amb_id = proposal.get("ambulance_id")
            if not amb_id:
                return False, None
            amb = next((a for a in eng.ambulances if str(a["id"]) == amb_id), None)
            if amb is None:
                return True, "La ambulancia ya no existe"
            if not amb.get("hasPatient"):
                return True, "La ambulancia ya no lleva paciente"
            if str(amb.get("missionPhase") or "") == "to_hospital":
                return True, "La ambulancia ya va al hospital"
            return False, None

        return False, None

    def _push_to_log(self, proposal: dict[str, Any]) -> None:
        self._resolved_log.append(proposal)
        if len(self._resolved_log) > MAX_RESOLVED_LOG:
            self._resolved_log = self._resolved_log[-MAX_RESOLVED_LOG:]

    def _persist_resolution(self, proposal_id: str, action: str) -> None:
        sb = get_supabase()
        if sb is None:
            return
        try:
            sb.table("ai_hitl_proposals").update(
                {"status": action, "resolved_at": "now()"}
            ).eq("id", proposal_id).execute()
        except Exception:
            _logger.exception("Failed to persist HITL resolution %s", proposal_id)

    async def _execute_approved(self, proposal: dict[str, Any]) -> None:
        """Materializa una propuesta aprobada en acciones del motor.

        Dispatch según ``anomaly_type``:
            - ``dispatch_request`` → `engine._dispatch_emergency_safe`.
            - ``critical_response_needed`` / ``eta_exceeded`` /
              ``smart_dispatch`` → `engine.dispatch_companion` (registrando
              primero un nuevo entity_type si el LLM creó uno al vuelo).
            - ``fuel_critical`` → fuerza refuel idle.
            - ``vitals_critical`` → desvía al hospital más cercano.
            - ``unattended_emergency`` → asigna cualquier IDLE libre.
        """
        atype = proposal.get("anomaly_type")
        amb_id = proposal.get("ambulance_id")
        eng = self.engine

        if atype == "dispatch_request":
            eid = proposal.get("anomaly_detail", {}).get("emergencyId")
            if eid:
                await eng._dispatch_emergency_safe(eid)
                _logger.info("AI dispatch_request approved for emergency %s", eid)
            return

        if atype in ("critical_response_needed", "eta_exceeded", "smart_dispatch"):
            detail = proposal.get("anomaly_detail", {})
            eid = detail.get("emergencyId")
            dispatch_kind = detail.get("dispatchKind")
            new_type_def = detail.get("newTypeDef")

            if eid and (dispatch_kind or new_type_def):
                if new_type_def and isinstance(new_type_def, dict):
                    # Marca la fuente como IA para que quede trazado en el catálogo.
                    new_type_def = {**new_type_def, "source": "ai"}
                    kind_id = eng.register_entity_type(new_type_def)
                    dispatch_kind = kind_id
                    _logger.info("AI auto-created entity type '%s' (id=%s)", new_type_def.get("name"), kind_id)

                if dispatch_kind:
                    async with eng._lock:
                        cid = await eng.dispatch_companion(dispatch_kind, eid)
                        if cid:
                            _logger.info("AI dispatched %s (%s) to emergency %s", dispatch_kind, cid, eid)
            elif eid and detail.get("action"):
                action = detail["action"]
                kind_map = {"dispatch_police": "police_patrol", "dispatch_helicopter": "helicopter"}
                kind = kind_map.get(action, action)
                async with eng._lock:
                    cid = await eng.dispatch_companion(kind, eid)
                    if cid:
                        _logger.info("AI dispatched %s (%s) to emergency %s (legacy action)", kind, cid, eid)
            return

        if not amb_id:
            return

        from .ambulance_fsm import AmbulanceState, infer_fsm_state

        if atype == "fuel_critical":
            async with eng._lock:
                amb = next((a for a in eng.ambulances if str(a["id"]) == amb_id), None)
                if amb and infer_fsm_state(amb) == AmbulanceState.IDLE:
                    await eng._maybe_idle_refuel.__func__(eng, amb)  # type: ignore[attr-defined]

        elif atype == "vitals_critical":
            async with eng._lock:
                amb = next((a for a in eng.ambulances if str(a["id"]) == amb_id), None)
                if amb and amb.get("hasPatient"):
                    phase = str(amb.get("missionPhase") or "")
                    if phase != "to_hospital":
                        hospitals = [p for p in eng.pois if p.get("kind") == "hospital"]
                        if hospitals:
                            from .route_nav import haversine_m
                            alat = float(amb.get("latitude", 0))
                            alon = float(amb.get("longitude", 0))
                            nearest = min(
                                hospitals,
                                key=lambda h: haversine_m(alat, alon, h["latitude"], h["longitude"]),
                            )
                            from .routing import fetch_route
                            route, _, _ = await fetch_route(
                                eng._http,
                                [(alat, alon), (nearest["latitude"], nearest["longitude"])],
                            )
                            if len(route) >= 2:
                                amb["routeCoords"] = route
                                amb["routeProgressM"] = 0.0
                                amb["missionPhase"] = "to_hospital"
                                amb["fsmState"] = AmbulanceState.TRANSPORTING.value
                                amb["targetHospitalId"] = nearest["id"]
                                _logger.info("AI rerouted %s to hospital %s (vitals critical)", amb_id, nearest["name"])

        elif atype == "unattended_emergency":
            eid = proposal.get("anomaly_detail", {}).get("emergencyId")
            if eid:
                async with eng._lock:
                    for amb in eng.ambulances:
                        if infer_fsm_state(amb) == AmbulanceState.IDLE and not amb.get("routeCoords"):
                            # Usa _impl directo: la versión pública devuelve
                            # False en autonomous (diseño intencional), pero
                            # aquí la IA SÍ decide asignar.
                            ok = await eng._try_assign_pending_emergency_to_ambulance_impl(amb)
                            if ok:
                                _logger.info("AI auto-dispatched ambulance to emergency %s", eid)
                                break

    async def observe_loop(self) -> None:
        """Bucle de fondo: cada ``OBSERVE_INTERVAL_S`` llama a `_scan`.

        Se arranca junto al motor en el lifespan FastAPI. Captura y loguea
        excepciones para no matar la tarea al primer fallo.
        """
        _logger.info("AI Observer started (mode=%s)", self.mode)
        while not self._stop.is_set():
            await asyncio.sleep(OBSERVE_INTERVAL_S)
            try:
                await self._scan()
            except Exception:
                _logger.exception("AI Observer scan error")
        _logger.info("AI Observer stopped")

    async def _scan(self) -> None:
        """Observa ambulancias y emergencias; crea propuestas nuevas.

        Fases:
            1. Revalida pendientes previas. En autónomo auto-aprueba todas;
               en HITL marca como ``stale`` las que ya no aplican.
            2. Detecta fuel/vitals críticos por unidad.
            3. Detecta emergencias unattended y evalúa con LLM si conviene
               despachar companion (``smart_dispatch``) o un tipo nuevo.
        """
        eng = self.engine
        staled: list[str] = []
        auto_approve_ids: list[str] = []
        async with eng._lock:
            ambulances = list(eng.ambulances)
            emergencies = list(eng.emergencies)
            for p in list(self.pending_proposals):
                if p.get("status") != "pending":
                    continue
                # En modo autónomo, toda pendiente debe ejecutarse YA — la IA
                # manda. No generamos stale.
                if self.mode == "autonomous":
                    auto_approve_ids.append(p["id"])
                    continue
                # HITL: marca stale las que ya no son accionables
                stale, reason = self._is_stale(p)
                if stale:
                    p["status"] = "stale"
                    p["stale_reason"] = reason
                    p["resolved_at"] = time.time()
                    self._push_to_log(p)
                    staled.append(p["id"])
                    _logger.info("AI proposal %s auto-stale: %s", p["id"], reason)
        # Fuera del lock: persist stale + auto-approve en modo autónomo
        for pid in staled:
            try:
                await asyncio.to_thread(self._persist_resolution, pid, "stale")
            except Exception:
                _logger.exception("persist stale async failed")
        for pid in auto_approve_ids:
            try:
                await self.resolve_proposal(pid, "approved")
            except Exception:
                _logger.exception("auto-approve (autonomous) failed for %s", pid)

        for amb in ambulances:
            aid = str(amb.get("id", ""))
            fuel = float(amb.get("fuelLevel", 100))
            tele = amb.get("telemetry") or {}
            med = tele.get("medical")

            if fuel <= FUEL_CRITICAL_PCT:
                key = f"{aid}:fuel_critical"
                if not self._on_cooldown(key):
                    await self._create_proposal(
                        aid, "fuel_critical", {"fuelLevel": fuel}, "combustible",
                    )
                    self._set_cooldown(key)

            if med and amb.get("hasPatient"):
                spo2 = float(med.get("spo2Pct", 100))
                bpm = float(med.get("heartRateBpm", 70))
                gcs = int(med.get("gcsScore", 15))

                if spo2 < SPO2_CRITICAL or bpm > BPM_CRITICAL or gcs <= GCS_CRITICAL:
                    key = f"{aid}:vitals_critical"
                    if not self._on_cooldown(key):
                        await self._create_proposal(
                            aid, "vitals_critical",
                            {"spo2": spo2, "bpm": bpm, "gcs": gcs},
                            "vitales_criticos",
                        )
                        self._set_cooldown(key)

        now = time.time()
        for em in emergencies:
            eid = str(em.get("id", ""))
            em_status = em.get("status")
            em_type = em.get("emergencyType", "medical")

            if em_status == "pending":
                created = em.get("createdAt")
                if created:
                    try:
                        from datetime import datetime
                        if isinstance(created, str):
                            ct = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                        else:
                            ct = float(created)
                    except (ValueError, TypeError):
                        ct = now
                    age_s = now - ct
                    if age_s >= UNATTENDED_EMERGENCY_S:
                        key = f"{eid}:unattended"
                        if not self._on_cooldown(key):
                            await self._create_proposal(
                                "", "unattended_emergency",
                                {"emergencyId": eid, "ageSeconds": round(age_s, 1), "description": em.get("description")},
                                "redistribucion",
                            )
                            self._set_cooldown(key)

            if em_status in ("pending", "assigned"):
                key = f"{eid}:smart_dispatch"
                if not self._on_cooldown(key):
                    existing_kinds = {
                        c.get("kind")
                        for c in eng.companions
                        if c.get("assignedEmergencyId") == eid
                    }
                    eta_s: float | None = None
                    assigned_amb_id_for_eta: str | None = None
                    if em_status == "assigned":
                        _aid = em.get("assignedAmbulanceId")
                        if _aid:
                            _amb = next((a for a in ambulances if str(a.get("id")) == str(_aid)), None)
                            if _amb:
                                from .route_nav import haversine_m
                                _dist = haversine_m(
                                    float(_amb.get("latitude", 0)), float(_amb.get("longitude", 0)),
                                    float(em.get("latitude", 0)), float(em.get("longitude", 0)),
                                )
                                _spd = float(_amb.get("speedKmh", 40)) / 3.6
                                eta_s = _dist / _spd if _spd > 0.1 else _dist / 10.0
                                assigned_amb_id_for_eta = str(_aid)

                    dispatches = await self._evaluate_smart_response(
                        em, existing_kinds, eta_s, eng.entity_types,
                    )
                    if dispatches:
                        for disp in dispatches:
                            await self._create_proposal(
                                assigned_amb_id_for_eta or "", "smart_dispatch",
                                {
                                    "emergencyId": eid,
                                    "emergencyType": em_type,
                                    "description": em.get("description"),
                                    "dispatchKind": disp.get("kind"),
                                    "reason": disp.get("reason", ""),
                                    "newTypeDef": disp.get("newTypeDef"),
                                    "etaSeconds": round(eta_s, 1) if eta_s else None,
                                },
                                "respuesta_inteligente",
                            )
                        self._set_cooldown(key)

    async def _create_proposal(
        self,
        ambulance_id: str,
        anomaly_type: str,
        detail: dict[str, Any],
        protocol_category: str,
    ) -> None:
        """Crea una propuesta HITL con RAG + razonamiento LLM.

        Flujo: vector search sobre `protocols` (pgvector) para el chunk
        más relevante → LLM razona (JSON estructurado con summary,
        keyFactors, urgency...) → se persiste la propuesta en Supabase →
        si el motor está en modo autónomo, se ejecuta inmediatamente.

        Args:
            ambulance_id: UUID de la unidad implicada (o ``""`` si la
                propuesta es a nivel sistema/emergencia).
            anomaly_type: Tipo de anomalía (ver módulo docstring).
            detail: Dict libre con el contexto (emergencyId, fuelLevel, etc.).
            protocol_category: Categoría para el log (no afecta la lógica).
        """
        matched_content: str | None = None
        matched_id: str | None = None
        similarity: float | None = None
        llm_reasoning: str | None = None
        llm_explanation: dict[str, Any] | None = None

        sb = get_supabase()

        # --- Vector search for relevant protocol ---
        if sb is not None:
            try:
                from . import llm_provider
                desc = self._anomaly_description(anomaly_type, detail)
                emb = await llm_provider.embed_text(desc)
                res = sb.rpc(
                    "match_protocols",
                    {"query_embedding": emb, "match_threshold": 0.0, "match_count": 3},
                ).execute()
                rows = res.data or []
                if rows:
                    best = rows[0]
                    matched_content = best.get("content")
                    matched_id = best.get("id")
                    similarity = best.get("similarity")
            except Exception:
                _logger.exception("Protocol search failed, falling back to no-context")

        # --- LLM reasoning ---
        try:
            from . import llm_provider
            if llm_provider.is_llm_available():
                async with self._llm_slot:
                    llm_explanation = await self._generate_reasoning(anomaly_type, detail, matched_content)
                llm_reasoning = llm_explanation.get("text") if isinstance(llm_explanation, dict) else None
        except Exception:
            _logger.exception("LLM reasoning generation failed")

        pid = str(uuid4())
        status = "pending"

        if self.mode == "autonomous":
            status = "auto_approved"

        proposal: dict[str, Any] = {
            "id": pid,
            "ambulance_id": ambulance_id,
            "anomaly_type": anomaly_type,
            "anomaly_detail": detail,
            "matched_protocol_id": matched_id,
            "matched_protocol_content": matched_content,
            "llm_reasoning": llm_reasoning,
            "llm_explanation": llm_explanation,
            "similarity": similarity,
            "status": status,
            "created_at": time.time(),
        }
        self.pending_proposals.append(proposal)
        _logger.info("AI proposal %s: %s for %s (mode=%s)", pid, anomaly_type, ambulance_id or "system", self.mode)

        if sb is not None:
            try:
                sb.table("ai_hitl_proposals").insert({
                    "id": pid,
                    "ambulance_id": ambulance_id or "system",
                    "anomaly_type": anomaly_type,
                    "anomaly_detail": detail,
                    "matched_protocol_id": matched_id,
                    "matched_protocol_content": matched_content,
                    "similarity": similarity,
                    "status": status,
                }).execute()
            except Exception:
                _logger.exception("Failed to persist HITL proposal %s", pid)

        if self.mode == "autonomous":
            # Autonomous: IA se encarga de TODAS las emergencias. No marcamos
            # stale: las funciones internas (dispatch_companion, add_emergency)
            # ya validan que el mundo permita la acción y devuelven None si no.
            # Si falla, se registra y seguimos — sin bloquear ni congelar nada.
            try:
                await self._execute_approved(proposal)
                proposal["resolved_at"] = time.time()
                self._push_to_log(proposal)
            except Exception:
                _logger.exception("Autonomous execution failed for %s", pid)
                proposal["resolved_at"] = time.time()
                self._push_to_log(proposal)

    async def _evaluate_smart_response(
        self,
        emergency: dict[str, Any],
        existing_companion_kinds: set[str | None],
        eta_seconds: float | None,
        entity_types: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Elige qué unidades adicionales enviar (LLM o reglas fallback).

        Llama a LLM si está disponible y la emergencia tiene señales
        suficientes (descripción, tipo no medical, o ETA alta). Si no,
        usa `_fallback_dispatch_rules`.

        Args:
            emergency: Dict de la emergencia.
            existing_companion_kinds: Kinds ya despachados (evita repetir).
            eta_seconds: ETA estimada de la ambulancia o None.
            entity_types: Catálogo completo; la IA puede proponer crear
                uno nuevo si nada encaja.

        Returns:
            Lista de dispatches con claves ``kind`` (id entity_type o
            None), ``reason`` y ``newTypeDef`` opcional.
        """
        em_type = emergency.get("emergencyType", "medical")
        em_desc = emergency.get("description") or ""
        em_title = emergency.get("title") or ""

        vehicle_types = [
            t for t in entity_types
            if t.get("kind") == "vehicle" and t["id"] != "ambulance"
        ]

        try:
            from . import llm_provider
            if llm_provider.is_llm_available() and (em_desc or em_type != "medical" or (eta_seconds and eta_seconds > ETA_EXCEEDED_S)):
                async with self._llm_slot:
                    return await self._llm_decide_dispatch(
                        em_type, em_title, em_desc, existing_companion_kinds, eta_seconds, vehicle_types,
                    )
        except Exception:
            _logger.exception("LLM smart dispatch evaluation failed, using fallback rules")

        return self._fallback_dispatch_rules(em_type, existing_companion_kinds, eta_seconds)

    def _fallback_dispatch_rules(
        self,
        em_type: str,
        existing_kinds: set[str | None],
        eta_seconds: float | None,
    ) -> list[dict[str, Any]]:
        """Reglas hardcoded si LLM no está disponible.

        - ``altercation`` → policía.
        - ``mass_casualty`` → helicóptero.
        - ETA > ``ETA_EXCEEDED_S`` → helicóptero.
        """
        dispatches: list[dict[str, Any]] = []
        if em_type == "altercation" and "police_patrol" not in existing_kinds:
            dispatches.append({"kind": "police_patrol", "reason": "Altercado requiere presencia policial"})
        if em_type == "mass_casualty" and "helicopter" not in existing_kinds:
            dispatches.append({"kind": "helicopter", "reason": "Victimas masivas requieren helicoptero"})
        if eta_seconds and eta_seconds > ETA_EXCEEDED_S and "helicopter" not in existing_kinds:
            dispatches.append({"kind": "helicopter", "reason": f"ETA ambulancia {eta_seconds:.0f}s supera umbral"})
        return dispatches

    async def _llm_decide_dispatch(
        self,
        em_type: str,
        em_title: str,
        em_desc: str,
        existing_kinds: set[str | None],
        eta_seconds: float | None,
        vehicle_types: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pide al LLM en JSON qué entity_types despachar.

        El prompt lista la flota actual con descripciones y capacidades.
        Si ninguno encaja, el modelo devuelve ``kind: "NEW"`` con un
        ``newTypeDef`` para registrar al vuelo.

        Returns:
            Lista de dispatches validados; excluye los ya despachados.
        """
        from . import llm_provider

        fleet_list = "\n".join(
            f"- id={t['id']}, nombre={t['name']}, velocidad={t.get('speedKmh', '?')} km/h"
            + (f", descripcion={t.get('description')}" if t.get("description") else "")
            + (f", capacidades={t.get('capabilities')}" if t.get("capabilities") else "")
            for t in vehicle_types
        )
        already = ", ".join(str(k) for k in existing_kinds if k) if existing_kinds else "ninguna"
        eta_info = f"ETA ambulancia asignada: {eta_seconds:.0f} segundos." if eta_seconds else "Sin ambulancia asignada o ETA desconocido."

        schema = {
            "type": "object",
            "properties": {
                "dispatches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "description": "id_del_vehiculo existente o 'NEW'"},
                            "reason": {"type": "string"},
                            "newTypeDef": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "speedKmh": {"type": "number"},
                                    "color": {"type": "string"},
                                    "description": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["vehicle"]},
                                },
                            },
                        },
                        "required": ["kind", "reason"],
                    },
                }
            },
            "required": ["dispatches"],
        }

        system = (
            "Motor IA de emergencias. Decides qué unidades adicionales enviar "
            "(aparte de la ambulancia ya asignada). Reglas:\n"
            "1. NO repitas unidades ya despachadas.\n"
            "2. Si ninguna existente encaja, pon kind='NEW' y rellena `newTypeDef` "
            "   (name, speedKmh, color hex, description, kind='vehicle').\n"
            "3. Para emergencias médicas simples sin descripción especial, devuelve dispatches=[].\n"
            "4. Solo pon unidades realmente necesarias."
        )
        user = (
            f"EMERGENCIA\n- Tipo: {em_type}\n- Título: {em_title}\n"
            f"- Descripción: {em_desc or 'sin descripción'}\n- {eta_info}\n"
            f"- Ya despachadas: {already}\n\n"
            f"FLOTA DISPONIBLE:\n{fleet_list or 'ninguna extra'}\n"
        )
        try:
            data = await llm_provider.chat_completion_json(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                schema=schema,
                temperature=0.3,
                max_tokens=512,
            )
        except Exception:
            _logger.exception("LLM dispatch (json) falló")
            return []
        items = data.get("dispatches") or []

        dispatches: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind", "")
            reason = item.get("reason", "")
            if kind == "NEW" and isinstance(item.get("newTypeDef"), dict):
                dispatches.append({
                    "kind": None,
                    "reason": reason,
                    "newTypeDef": item["newTypeDef"],
                })
            elif kind and kind not in existing_kinds:
                dispatches.append({"kind": kind, "reason": reason})
        return dispatches

    def _anomaly_description(self, anomaly_type: str, detail: dict[str, Any]) -> str:
        """Texto en español para el embedding del vector search + prompt LLM."""
        desc_extra = ""
        em_desc = detail.get("description")
        if em_desc:
            desc_extra = f" Descripcion del incidente: {em_desc}."

        if anomaly_type == "fuel_critical":
            return f"Ambulancia con nivel de combustible critico: {detail.get('fuelLevel', '?')}%. Necesita repostaje urgente."
        elif anomaly_type == "vitals_critical":
            return (
                f"Paciente con constantes vitales criticas: SpO2={detail.get('spo2', '?')}%, "
                f"BPM={detail.get('bpm', '?')}, GCS={detail.get('gcs', '?')}. "
                "Requiere intervencion medica inmediata."
            )
        elif anomaly_type == "unattended_emergency":
            return f"Emergencia sin atender durante {detail.get('ageSeconds', '?')} segundos. Necesita despacho inmediato.{desc_extra}"
        elif anomaly_type == "dispatch_request":
            title = detail.get("title", "")
            return f"Nueva emergencia '{title}' pendiente de aprobacion para despacho.{desc_extra}"
        elif anomaly_type == "smart_dispatch":
            kind = detail.get("dispatchKind") or "nueva unidad"
            reason = detail.get("reason", "")
            eta = detail.get("etaSeconds")
            eta_part = f" ETA ambulancia: {eta}s." if eta else ""
            return f"Despacho inteligente: enviar '{kind}' a emergencia tipo '{detail.get('emergencyType', '?')}'.{eta_part} {reason}.{desc_extra}"
        elif anomaly_type == "critical_response_needed":
            action = detail.get("action", "desconocida")
            em_type = detail.get("emergencyType", "desconocido")
            if action == "dispatch_police":
                return f"Emergencia tipo '{em_type}' requiere respuesta policial. Altercado con heridos detectado.{desc_extra}"
            elif action == "dispatch_helicopter":
                return f"Emergencia tipo '{em_type}' requiere helicóptero. Victimas masivas o ETA crítico.{desc_extra}"
            return f"Respuesta critica necesaria: {action} para emergencia tipo {em_type}.{desc_extra}"
        elif anomaly_type == "eta_exceeded":
            return (
                f"Ambulancia asignada con ETA de {detail.get('etaSeconds', '?')} segundos "
                f"(distancia: {detail.get('distanceM', '?')} m). Supera el umbral de 5 minutos. "
                f"Se recomienda despachar helicóptero.{desc_extra}"
            )
        return f"Anomalia {anomaly_type}: {detail}"

    async def _generate_reasoning(
        self,
        anomaly_type: str,
        detail: dict[str, Any],
        protocol_content: str | None,
    ) -> dict[str, Any]:
        """Devuelve explicabilidad estructurada para la propuesta HITL.

        Schema: {
          summary: str,                       # 2-3 frases en lenguaje llano
          keyFactors: [str],                  # bullets de factores clínicos/operativos
          recommendedAction: str,             # acción propuesta, 1 frase
          protocolReferences: [str],          # citas literales del chunk de protocolo
          confidence: "low"|"medium"|"high",
          urgency: "routine"|"urgent"|"critical"
        }
        """
        from . import llm_provider

        desc = self._anomaly_description(anomaly_type, detail)
        protocol_ctx = (
            f"\nPROTOCOLO RECUPERADO (vía RAG):\n{protocol_content}"
            if protocol_content else "\nNo se encontró protocolo en el corpus."
        )

        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "keyFactors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                },
                "recommendedAction": {"type": "string"},
                "protocolReferences": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                },
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "urgency": {"type": "string", "enum": ["routine", "urgent", "critical"]},
            },
            "required": ["summary", "keyFactors", "recommendedAction", "confidence", "urgency"],
        }

        messages = [
            {
                "role": "system",
                "content": (
                    "Motor IA de Sentinel (gemelo digital de emergencias). "
                    "Genera explicabilidad estructurada para operador humano: "
                    "por qué la anomalía es relevante, qué factores la disparan, "
                    "qué acción recomienda y con qué confianza. Si hay protocolo "
                    "RAG, incluye citas literales breves en protocolReferences."
                ),
            },
            {
                "role": "user",
                "content": f"Anomalía detectada: {desc}{protocol_ctx}",
            },
        ]

        try:
            data = await llm_provider.chat_completion_json(
                messages, schema=schema, temperature=0.4, max_tokens=600,
            )
            # Normaliza enums fuera de rango
            if data.get("confidence") not in ("low", "medium", "high"):
                data["confidence"] = "medium"
            if data.get("urgency") not in ("routine", "urgent", "critical"):
                data["urgency"] = "urgent"

            # Limpia markdown del summary (asteriscos, fences)
            summ = str(data.get("summary") or "").strip()
            summ = summ.replace("**", "").replace("```", "")
            data["summary"] = summ

            # Si el modelo no llenó keyFactors, extraer bullets del summary
            if not data.get("keyFactors"):
                sentences = [s.strip(" .-•*") for s in summ.replace("\n", ". ").split(".") if s.strip()]
                data["keyFactors"] = [s for s in sentences[:4] if 5 <= len(s) <= 140]

            # Fallback recommended action desde descripción heurística
            if not data.get("recommendedAction"):
                data["recommendedAction"] = self._anomaly_description(anomaly_type, detail)

            # Si summary quedó vacío, reconstruir desde factores / acción
            if not data["summary"]:
                data["summary"] = (
                    data["recommendedAction"]
                    or (". ".join(data.get("keyFactors") or []) + ".")
                )

            data.setdefault("protocolReferences", [])

            # Texto plano (compat UI antigua que solo lee llmReasoning)
            data["text"] = (
                data["summary"]
                + ("\n\nAcción recomendada: " + data["recommendedAction"] if data["recommendedAction"] else "")
            )
            return data
        except Exception:
            _logger.exception("LLM reasoning estructurado falló; usando texto plano")
            try:
                raw = await llm_provider.chat_completion(
                    messages, temperature=0.4, max_tokens=256,
                )
            except Exception:
                raw = desc
            # Limpieza y construcción de estructura desde texto libre
            summary = str(raw or "").replace("**", "").replace("```", "").strip()
            sentences = [s.strip(" .-•*") for s in summary.replace("\n", ". ").split(".") if s.strip()]
            factors = [s for s in sentences[:4] if 5 <= len(s) <= 140]
            return {
                "summary": summary or desc,
                "keyFactors": factors,
                "recommendedAction": self._anomaly_description(anomaly_type, detail),
                "protocolReferences": [],
                "confidence": "low",
                "urgency": "urgent",
                "text": summary or desc,
            }
