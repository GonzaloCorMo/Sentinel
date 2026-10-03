"""Generador de informes post-turno con LLM estructurado.

Cruza datos de:
  - operation_events (audit trail del motor)
  - ai_hitl_proposals (decisiones IA + resoluciones)
  - emergency_cases / simulation snapshots si están
  - estado en memoria del engine (telemetría actual)

Produce resumen + KPIs + highlights + recomendaciones en JSON.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import llm_provider
from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _fetch_window_data(window_minutes: int) -> dict[str, Any]:
    """Extrae actividad reciente de Supabase para la ventana pedida.

    Consulta ``operation_events``, ``ai_hitl_proposals``, conteo de
    ``telemetry_logs`` y ``chat_messages`` desde ``now - window_minutes``.

    Args:
        window_minutes: Ventana temporal en minutos (5..1440).

    Returns:
        Dict con las 4 colecciones/contadores. Si Supabase no está
        disponible, devuelve estructura vacía sin errores.
    """
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=window_minutes)
    data: dict[str, Any] = {
        "operation_events": [],
        "ai_hitl_proposals": [],
        "telemetry_logs_count": 0,
        "chat_messages_count": 0,
    }
    if sb is None:
        return data
    try:
        r = sb.table("operation_events").select("*").gte("created_at", _iso(since)).order("created_at", desc=True).limit(200).execute()
        data["operation_events"] = r.data or []
    except Exception:
        _logger.exception("fetch operation_events failed")
    try:
        r = sb.table("ai_hitl_proposals").select("*").gte("created_at", _iso(since)).order("created_at", desc=True).limit(100).execute()
        data["ai_hitl_proposals"] = r.data or []
    except Exception:
        _logger.exception("fetch ai_hitl_proposals failed")
    try:
        r = sb.table("telemetry_logs").select("id", count="exact").gte("timestamp", _iso(since)).execute()
        data["telemetry_logs_count"] = r.count or 0
    except Exception:
        pass
    try:
        r = sb.table("chat_messages").select("id", count="exact").gte("created_at", _iso(since)).execute()
        data["chat_messages_count"] = r.count or 0
    except Exception:
        pass
    return data


def _compute_kpis(engine_snapshot: dict[str, Any], window_data: dict[str, Any]) -> dict[str, Any]:
    """KPIs crudos para el informe: flota, emergencias, IA, eventos.

    Args:
        engine_snapshot: `engine.get_state_payload()` actual.
        window_data: Resultado de `_fetch_window_data`.

    Returns:
        Dict plano con contadores (``idle``, ``resolved``, ``aiApproved``,
        ``etaExceeded``...) que el LLM usa como base del análisis.
    """
    ambs = engine_snapshot.get("ambulances") or []
    emgs = engine_snapshot.get("emergencies") or []
    props = window_data.get("ai_hitl_proposals") or []
    events = window_data.get("operation_events") or []

    # Estado flota
    with_patient = sum(1 for a in ambs if a.get("hasPatient"))
    low_fuel = sum(1 for a in ambs if (a.get("fuelLevel") or 100) < 25)
    idle = sum(1 for a in ambs if (a.get("missionPhase") or "idle") == "idle")
    powered_off = sum(1 for a in ambs if a.get("poweredOff"))

    # Emergencias resueltas/abiertas
    resolved = sum(1 for e in emgs if e.get("status") == "resolved")
    active = sum(1 for e in emgs if e.get("status") in ("pending", "assigned"))

    # IA
    approved = sum(1 for p in props if p.get("status") in ("approved", "auto_approved"))
    rejected = sum(1 for p in props if p.get("status") == "rejected")
    stale = sum(1 for p in props if p.get("status") == "stale")
    autonomous = sum(1 for p in props if p.get("status") == "auto_approved")

    return {
        "fleetTotal": len(ambs),
        "fleetWithPatient": with_patient,
        "fleetLowFuel": low_fuel,
        "fleetIdle": idle,
        "fleetPoweredOff": powered_off,
        "emergenciesActive": active,
        "emergenciesResolvedInWindow": resolved,
        "aiProposalsTotal": len(props),
        "aiProposalsApproved": approved,
        "aiProposalsAutonomous": autonomous,
        "aiProposalsRejected": rejected,
        "aiProposalsStale": stale,
        "operationEventsCount": len(events),
        "telemetryRecords": window_data.get("telemetry_logs_count", 0),
        "chatMessages": window_data.get("chat_messages_count", 0),
    }


_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Resumen ejecutivo 2-4 frases en español."},
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                },
                "required": ["title", "detail", "severity"],
            },
            "minItems": 1,
            "maxItems": 6,
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["summary", "highlights", "recommendations"],
}


async def generate_shift_report(window_minutes: int, engine_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Genera un informe post-turno y lo persiste en ``shift_reports``.

    Flujo: `_fetch_window_data` → `_compute_kpis` → LLM JSON con schema
    (summary + highlights + recommendations). Si el LLM falla, usa un
    fallback determinista basado solo en KPIs.

    Args:
        window_minutes: Ventana temporal del informe (5..1440).
        engine_snapshot: `engine.get_state_payload()` actual.

    Returns:
        Dict completo del informe (``started_at``, ``ended_at``, ``kpis``,
        ``summary``, ``highlights``, ``recommendations``, ``id`` si se
        persistió).
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=window_minutes)
    window_data = await _fetch_window_data(window_minutes)
    kpis = _compute_kpis(engine_snapshot, window_data)

    # Contexto compacto para el LLM
    anomalies = [
        {
            "type": p.get("anomaly_type"),
            "status": p.get("status"),
            "detail": p.get("anomaly_detail") or {},
        }
        for p in (window_data.get("ai_hitl_proposals") or [])[:30]
    ]
    ctx = (
        f"VENTANA: últimos {window_minutes} minutos\n"
        f"KPIs: {kpis}\n"
        f"Anomalías detectadas por IA (muestra): {anomalies[:10]}\n"
        f"Eventos de operación: {len(window_data.get('operation_events') or [])}\n"
        f"Emergencias activas: {kpis['emergenciesActive']}\n"
        f"Emergencias resueltas en ventana: {kpis['emergenciesResolvedInWindow']}\n"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Eres el analista de operaciones del sistema Sentinel. "
                "Escribe un informe de cambio de turno conciso y profesional, "
                "en español. El informe debe ser accionable: highlights con "
                "severidad (info/warning/critical) y recomendaciones concretas."
            ),
        },
        {"role": "user", "content": ctx + "\nGenera el informe."},
    ]

    summary = ""
    highlights: list[dict[str, Any]] = []
    recommendations: list[str] = []
    try:
        data = await llm_provider.chat_completion_json(
            messages, schema=_SCHEMA, temperature=0.3, max_tokens=900,
        )
        summary = str(data.get("summary") or "").strip()
        hls = data.get("highlights") or []
        for h in hls:
            if isinstance(h, dict):
                sev = h.get("severity", "info")
                if sev not in ("info", "warning", "critical"):
                    sev = "info"
                highlights.append({
                    "title": str(h.get("title") or "")[:120],
                    "detail": str(h.get("detail") or "")[:500],
                    "severity": sev,
                })
        recommendations = [str(r) for r in (data.get("recommendations") or []) if str(r).strip()][:8]
    except Exception:
        _logger.exception("LLM shift report falló, usando fallback determinista")

    # Fallback mínimo si LLM falló o dio nada
    if not summary:
        summary = (
            f"Turno de {window_minutes} min: {kpis['emergenciesResolvedInWindow']} emergencias resueltas, "
            f"{kpis['aiProposalsTotal']} propuestas IA ({kpis['aiProposalsApproved']} aprobadas, "
            f"{kpis['aiProposalsRejected']} rechazadas). "
            f"Flota: {kpis['fleetTotal']} unidades, {kpis['fleetLowFuel']} con combustible bajo."
        )
    if not highlights:
        if kpis["fleetLowFuel"]:
            highlights.append({
                "title": "Combustible bajo",
                "detail": f"{kpis['fleetLowFuel']} unidades por debajo del 25%. Considerar rotación a repostaje.",
                "severity": "warning",
            })
        if kpis["emergenciesActive"]:
            highlights.append({
                "title": "Emergencias abiertas",
                "detail": f"{kpis['emergenciesActive']} incidencias sin resolver al cierre de ventana.",
                "severity": "info",
            })
        if kpis["aiProposalsStale"]:
            highlights.append({
                "title": "Propuestas HITL obsoletas",
                "detail": f"{kpis['aiProposalsStale']} propuestas de IA quedaron stale (contexto cambió antes de aprobarse).",
                "severity": "info",
            })
        if not highlights:
            highlights.append({
                "title": "Sin incidencias destacables",
                "detail": "La ventana transcurrió sin anomalías reseñables.",
                "severity": "info",
            })
    if not recommendations:
        recommendations = ["Continuar monitorización estándar."]

    record = {
        "started_at": _iso(start),
        "ended_at": _iso(now),
        "window_minutes": window_minutes,
        "kpis": kpis,
        "summary": summary,
        "highlights": highlights,
        "recommendations": recommendations,
        "generated_by": "ai",
    }

    # Persistir
    sb = get_supabase()
    report_id: str | None = None
    if sb is not None:
        try:
            res = sb.table("shift_reports").insert(record).execute()
            if res.data:
                report_id = res.data[0].get("id")
        except Exception:
            _logger.exception("persist shift_report failed")

    record["id"] = report_id
    return record


async def list_recent_reports(limit: int = 10) -> list[dict[str, Any]]:
    """Devuelve los últimos ``limit`` informes guardados (descendente por fecha)."""
    sb = get_supabase()
    if sb is None:
        return []
    try:
        r = sb.table("shift_reports").select("*").order("created_at", desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        _logger.exception("list_recent_reports failed")
        return []
