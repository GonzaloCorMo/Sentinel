"""Writer append-only de lifecycle + outcomes para el pipeline de ML.

Buffer batch best-effort (igual patrón que `telemetry_writer`): las escrituras
a Supabase se acumulan y se flushean periódicamente o al parar el motor.

Tablas destino:
    - ``entity_events``     — lifecycle de todos los objetos del mapa.
    - ``mission_outcomes``  — KPIs por emergencia resuelta (labels ML).
    - ``route_decisions``   — snapshot features + decisión de despacho.
    - ``simulation_sessions`` — metadata por ejecución.

Referencias:
    - Event sourcing pattern: https://martinfowler.com/eaaDev/EventSourcing.html
    - Feature store idea:     https://www.featurestore.org/what-is-a-feature-store
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

FLUSH_EVERY_N_EVENTS = 25
FLUSH_INTERVAL_S = 10.0


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventWriter:
    """Buffer de eventos + outcomes + decisions; flush batch a Supabase."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._outcomes: list[dict[str, Any]] = []
        self._decisions: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self.session_id: str = str(uuid4())
        self._last_flush_at: float = 0.0

    # ── Sesión ──────────────────────────────────────────────────────────────
    def new_session(self, mode: str = "interactive", speed: float | None = None) -> str:
        """Empieza una nueva sesión de simulación (llamar al arrancar y al reset).

        Args:
            mode: ``"interactive"`` | ``"training_autonomous"``.
            speed: Multiplicador de velocidad inicial.

        Returns:
            UUID de la sesión.
        """
        self.session_id = str(uuid4())
        sb = get_supabase()
        if sb is not None:
            try:
                sb.table("simulation_sessions").insert({
                    "id": self.session_id,
                    "mode": mode,
                    "speed_multiplier": speed,
                }).execute()
            except Exception:
                _logger.exception("No se pudo crear simulation_session")
        return self.session_id

    def close_session(self, total_ticks: int, total_emergencies: int, total_resolved: int) -> None:
        """Marca la sesión como terminada con contadores finales."""
        sb = get_supabase()
        if sb is None:
            return
        try:
            sb.table("simulation_sessions").update({
                "ended_at": "now()",
                "total_ticks": total_ticks,
                "total_emergencies": total_emergencies,
                "total_resolved": total_resolved,
            }).eq("id", self.session_id).execute()
        except Exception:
            _logger.exception("No se pudo cerrar simulation_session")

    # ── Eventos lifecycle ───────────────────────────────────────────────────
    def emit_event(
        self,
        kind: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        actor: str | None = None,
        tick: int | None = None,
    ) -> None:
        """Emite un evento de lifecycle al buffer.

        Args:
            kind: ``"ambulance"`` | ``"companion"`` | ``"poi"`` | ``"emergency"``
                | ``"jam"`` | ``"entity_type"``.
            entity_id: UUID del objeto.
            event_type: ``"created"`` | ``"updated"`` | ``"deleted"``
                | ``"dispatched"`` | ``"resolved"`` | ``"rerouted"``
                | ``"phase_change"``.
            payload: Dict libre con campos específicos del evento.
            actor: Origen del cambio (``"operator"``, ``"ai_observer"``,
                ``"engine"``, ``"citizen"``, ``"vehicle"``).
            tick: Tick del motor al emitir.
        """
        self._events.append({
            "kind": kind,
            "entity_id": str(entity_id),
            "event_type": event_type,
            "actor": actor,
            "payload": payload or {},
            "tick": tick,
            "simulation_session_id": self.session_id,
        })

    # ── Mission outcomes ────────────────────────────────────────────────────
    def emit_outcome(self, data: dict[str, Any]) -> None:
        """Añade una fila de mission_outcomes al buffer."""
        row = {**data, "simulation_session_id": self.session_id}
        self._outcomes.append(row)

    # ── Route decisions ─────────────────────────────────────────────────────
    def emit_decision(self, data: dict[str, Any]) -> None:
        """Snapshot de una decisión de despacho al buffer."""
        row = {**data, "simulation_session_id": self.session_id}
        self._decisions.append(row)

    # ── Flush ───────────────────────────────────────────────────────────────
    async def maybe_flush(self) -> None:
        """Flush si buffer superó umbral o pasaron ``FLUSH_INTERVAL_S``."""
        import time
        total = len(self._events) + len(self._outcomes) + len(self._decisions)
        if total == 0:
            return
        elapsed = time.monotonic() - self._last_flush_at
        if total < FLUSH_EVERY_N_EVENTS and elapsed < FLUSH_INTERVAL_S:
            return
        await self.flush()

    async def flush(self) -> None:
        """Vuelca buffers a Supabase. Descarta si DB no disponible."""
        sb = get_supabase()
        async with self._lock:
            events = self._events[:]
            outcomes = self._outcomes[:]
            decisions = self._decisions[:]
            self._events.clear()
            self._outcomes.clear()
            self._decisions.clear()
        import time
        self._last_flush_at = time.monotonic()
        if sb is None:
            return
        if events:
            try:
                sb.table("entity_events").insert(events).execute()
            except Exception:
                _logger.exception("Flush entity_events falló (%d rows dropped)", len(events))
        if outcomes:
            try:
                sb.table("mission_outcomes").insert(outcomes).execute()
            except Exception:
                _logger.exception("Flush mission_outcomes falló (%d rows dropped)", len(outcomes))
        if decisions:
            try:
                sb.table("route_decisions").insert(decisions).execute()
            except Exception:
                _logger.exception("Flush route_decisions falló (%d rows dropped)", len(decisions))
