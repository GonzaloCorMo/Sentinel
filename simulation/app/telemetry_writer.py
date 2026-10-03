"""Writer batch best-effort: bufferea telemetría y la vuelca a Supabase.

Cada ``FLUSH_EVERY_N_TICKS`` ticks el motor llama a `flush` que hace un
único ``insert`` masivo sobre ``telemetry_logs``. Si la DB no está
disponible, el buffer se descarta (best-effort: no bloqueamos la
simulación por persistencia).

Schema v2.0 guarda ``gps_data`` / ``mechanical_data`` / ``medical_data``
como columnas tipadas y el resto (``derived`` / ``environmental`` /
``network`` / ``meta``) como jsonb extras.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

FLUSH_EVERY_N_TICKS = 5


class TelemetryWriter:
    """Buffer en memoria + flush batch a ``telemetry_logs``."""

    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []

    def append(
        self,
        ambulance_id: str,
        positioning: dict[str, Any] | None,
        mechanical: dict[str, Any] | None,
        medical: dict[str, Any] | None,
        environmental: dict[str, Any] | None = None,
        network: dict[str, Any] | None = None,
        derived: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Añade una fila de telemetría al buffer (no escribe todavía)."""
        self._buffer.append(
            {
                "ambulance_id": ambulance_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "gps_data": positioning,
                "mechanical_data": mechanical,
                "medical_data": medical,
                "environmental_data": environmental,
                "network_data": network,
                "derived_data": derived,
                "meta": meta,
            }
        )

    async def flush(self) -> None:
        """Vuelca el buffer a Supabase. Sin DB disponible, descarta y sigue."""
        if not self._buffer:
            return
        sb = get_supabase()
        if sb is None:
            self._buffer.clear()
            return
        batch = self._buffer[:]
        self._buffer.clear()
        try:
            sb.table("telemetry_logs").insert(batch).execute()
        except Exception:
            _logger.exception("Telemetry flush failed (%d rows dropped)", len(batch))
