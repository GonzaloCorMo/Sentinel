"""Singleton del cliente Supabase para el backend de simulación.

Usa ``SUPABASE_SERVICE_ROLE_KEY`` (bypass RLS) — el backend actúa como
administrador sobre tablas internas (``telemetry_logs``,
``ai_hitl_proposals``, ``ai_knowledge_chunks``, etc.). El frontend usa
otra clave (anon) con RLS activa; no mezclar.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_logger = logging.getLogger(__name__)

_client: Any | None = None
_checked = False


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def get_supabase() -> Any | None:
    """Devuelve el cliente singleton o ``None`` si faltan env vars.

    Fallos (import, URL inválida) se loguean y se cachean como None; la
    función no lanza — el código cliente trata ``None`` como "DB off".
    """
    global _client, _checked
    if _checked:
        return _client
    _checked = True

    url = _env("SUPABASE_URL", _env("VITE_SUPABASE_URL", "http://127.0.0.1:54321"))
    key = _env("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        _logger.warning("SUPABASE_SERVICE_ROLE_KEY not set — Supabase integration disabled")
        return None

    try:
        from supabase import create_client

        _client = create_client(url, key)
        _logger.info("Supabase client ready (%s)", url)
    except Exception:
        _logger.exception("Failed to create Supabase client")
        _client = None
    return _client


def is_supabase_available() -> bool:
    """True si el cliente está listo (útil para logs y feature-flags)."""
    return get_supabase() is not None
