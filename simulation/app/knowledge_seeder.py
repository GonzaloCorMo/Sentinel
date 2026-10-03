"""Siembra ``ai_knowledge_chunks`` con la documentación del proyecto.

Cada chunk describe una pieza del sistema (arquitectura, FSM, HITL,
comunicaciones, telemetría, etc.). Al arrancar, el lifespan llama a
`seed_knowledge_if_empty`. Si la tabla está vacía calcula embeddings con
`llm_provider.embed_texts` (batch) y los inserta para que el chat RAG
pueda responder preguntas sobre el proyecto.
"""
from __future__ import annotations

import logging
from typing import Any

from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

KNOWLEDGE_CHUNKS: list[dict[str, str]] = [
    {
        "source_type": "project_docs",
        "source_ref": "architecture",
        "content": (
            "Sentinel es un gemelo digital de flota de ambulancias desarrollado para "
            "operaciones de emergencias. Consta de un frontend Vue 3 + Vite + TypeScript "
            "(en frontend/) y un motor de simulacion en Python con FastAPI (en simulation/). "
            "La persistencia y autenticacion usan Supabase local. La comunicacion en tiempo real "
            "entre backend y frontend es via Server-Sent Events (SSE)."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "tech_stack",
        "content": (
            "El stack tecnologico incluye: Frontend con Vue 3, Pinia (gestion de estado), "
            "Vue Router, Leaflet (mapas), ECharts (graficas), Tailwind CSS y vue-sonner (toasts). "
            "Backend con Python 3.11+, FastAPI, asyncio, httpx, aiomqtt y supabase-py. "
            "Base de datos con Supabase (PostgreSQL + pgvector para RAG). "
            "Enrutamiento con OSRM (Open Source Routing Machine) para rutas realistas por calle."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "simulation_fsm",
        "content": (
            "Las ambulancias tienen una maquina de estados (FSM) con los estados: IDLE (en espera), "
            "RESPONDING (yendo a emergencia), TRANSPORTING (llevando paciente al hospital), "
            "REFUELING (repostando), STAGING (reposicionandose en hospital) y UNAVAILABLE. "
            "Las transiciones ocurren al completar rutas: IDLE -> RESPONDING al asignar emergencia, "
            "RESPONDING -> TRANSPORTING al recoger paciente, TRANSPORTING -> IDLE al entregar "
            "paciente, y IDLE -> REFUELING cuando el combustible baja del 20%."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "hitl_system",
        "content": (
            "El sistema Human-in-the-Loop (HITL) permite que la IA detecte anomalias "
            "(combustible critico <=10%, vitales peligrosas) y proponga intervenciones al operador. "
            "Las propuestas se almacenan en la tabla ai_hitl_proposals y se muestran en un panel "
            "flotante en la UI con botones de Aprobar/Rechazar. El operador puede configurar "
            "el modo entre HITL (requiere aprobacion humana) y Autonomo (la IA actua y notifica)."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "comms_channels",
        "content": (
            "La comunicacion ambulancia-central usa tres canales con failover: MQTT (prioridad 1, "
            "broker local), P2P mesh (prioridad 2, entre ambulancias directamente) y HTTP "
            "(prioridad 3, fallback REST). El sistema automaticamente desciende al siguiente "
            "canal si el actual falla (patron Channel abstracto con connect/send/disconnect). "
            "El frontend muestra el estado de cada canal en tiempo real."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "telemetry_schema",
        "content": (
            "La telemetria de cada ambulancia tiene tres dimensiones: Posicionamiento (GPS con "
            "latitud, longitud, velocidad, rumbo, HDOP, precision), Mecanica (nivel de combustible, "
            "RPM, presion neumaticos, temperatura aceite/liquido frenos, G-loads, estado sirenas) "
            "y Medica (solo con paciente: frecuencia cardiaca, presion arterial, SpO2, GCS, "
            "EtCO2, glucosa, temperatura corporal, ritmo ECG, frecuencia respiratoria, "
            "estado desfibrilador). Todo se guarda en la tabla telemetry_logs de Supabase."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "osrm_routing",
        "content": (
            "El enrutamiento usa OSRM (Open Source Routing Machine) para calcular rutas "
            "realistas por calles. Las ambulancias siguen polylines reales y el motor detecta "
            "atascos (zonas poligonales jam) que bloquean tramos de ruta, activando rerouting "
            "automatico. Si OSRM no esta disponible, se usa fallback de linea recta. "
            "La simulacion soporta multiples regiones seleccionables (Aruba, Santiago de Compostela, Bogota, CDMX) "
            "y arranca centrada en la region por defecto (DEFAULT_REGION)."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "ui_operations",
        "content": (
            "La vista principal (MapOperationsView) muestra un mapa Leaflet con ambulancias, "
            "emergencias, POIs (hospitales y gasolineras) y atascos. Incluye controles para: "
            "play/pause/reset de simulacion, ajuste de velocidad (1x-50x), constructor de "
            "escenarios (anadir emergencias, atascos, POIs), asignacion manual ambulancia-emergencia, "
            "y modo pantalla completa. La telemetria detallada se muestra en un aside lateral."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "ui_fleet_telemetry",
        "content": (
            "La vista de Telemetria de Flota (FleetTelemetryView) muestra tarjetas para cada "
            "ambulancia con graficas ECharts de vitales del paciente (BPM, SpO2, presion arterial) "
            "y gauges mecanicos (combustible, RPM). Se actualiza en tiempo real via SSE. "
            "La vista de Comunicaciones (CommsDashboardView) muestra una tabla de log de todos "
            "los mensajes enviados por cada canal con latencia y estado."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "emergency_management",
        "content": (
            "Las emergencias se crean desde el constructor de escenario (click en mapa o formulario). "
            "El motor de simulacion asigna automaticamente la ambulancia mas cercana que tenga "
            "suficiente combustible y bateria para el viaje ida y vuelta (heuristica de round-trip). "
            "Si ninguna ambulancia esta disponible, la emergencia queda pendiente y se reintenta "
            "cada tick hasta que una ambulancia quede libre. Las emergencias tienen estados: "
            "pending, assigned, resolved."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "fuel_model",
        "content": (
            "El modelo de combustible drena ~0.01% por cada 100 metros recorridos en ruta. "
            "Cuando el nivel baja del 20% en IDLE, la ambulancia se dirige automaticamente a "
            "la gasolinera mas cercana. Si baja del 25% durante una mision activa, se desvia "
            "a repostar antes de continuar. Al completar el repostaje, el tanque se llena al 100%. "
            "La IA genera alertas criticas cuando el combustible llega al 10%."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "patient_severity",
        "content": (
            "Los pacientes tienen perfiles de severidad: stable (vitales normales), moderate "
            "(HR elevada 100-120, SpO2 92-96, GCS 9-12) y critical (HR 120-150, SpO2 75-88, "
            "GCS 3-8, presion arterial inestable). La severidad afecta la telemetria medica "
            "simulada y puede disparar alertas de la IA cuando los valores cruzan umbrales "
            "peligrosos (SpO2 < 90, BPM > 140, GCS <= 8)."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "supabase_integration",
        "content": (
            "Supabase local se usa para: autenticacion (login con Google OAuth), persistencia "
            "de telemetria en telemetry_logs (batch insert cada 5 ticks), almacenamiento de "
            "protocolos medicos con embeddings vectoriales (protocols_knowledge), base de "
            "conocimiento del proyecto (ai_knowledge_chunks con pgvector), propuestas HITL "
            "(ai_hitl_proposals), escenarios guardados (saved_scenarios) y auditorias de "
            "operaciones (operation_events). El backend usa supabase-py con service role key."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "ai_chatbot",
        "content": (
            "El chatbot de Sentinel usa RAG (Retrieval-Augmented Generation) para responder "
            "preguntas sobre el proyecto. Funciona asi: 1) el usuario escribe una pregunta, "
            "2) se genera un embedding de la pregunta, 3) se buscan los chunks mas relevantes "
            "en ai_knowledge_chunks usando similitud coseno, 4) se construye un prompt con el "
            "contexto recuperado, 5) un LLM (Ollama local o OpenAI cloud) genera la respuesta "
            "en streaming. El historial se guarda en la tabla chat_messages."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "ai_decision_engine",
        "content": (
            "El motor de decision IA (AIDecisionEngine) funciona como observador en segundo plano "
            "que escanea el estado de todas las ambulancias cada 5 segundos. Detecta anomalias "
            "(combustible critico, vitales peligrosas, emergencias desatendidas), busca protocolos "
            "relevantes en la base de datos vectorial, genera un razonamiento con LLM explicando "
            "la situacion y la accion propuesta, y crea una propuesta HITL. En modo autonomo "
            "ejecuta las acciones directamente y notifica al operador."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "how_to_use",
        "content": (
            "Para usar Sentinel: 1) Iniciar Supabase local (supabase start), 2) Arrancar "
            "el motor de simulacion (uvicorn en simulation/), 3) Arrancar el frontend (npm run dev "
            "en frontend/). La simulacion empieza pausada y vacia; usa el selector de mapa para elegir region. "
            "Pulsa Play para iniciar, usa el constructor de escenario para anadir emergencias "
            "y atascos, observa la telemetria en tiempo real, y usa el panel de IA para "
            "gestionar propuestas de intervencion."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "jams_traffic",
        "content": (
            "Los atascos (jams) se modelan como poligonos geograficos que bloquean tramos de "
            "ruta. Se pueden crear desde la UI dibujando un poligono o haciendo click en un punto "
            "(genera un cuadrado de ~200m). Cuando una ambulancia detecta que su ruta cruza un "
            "atasco, el motor solicita un desvio alternativo a OSRM (con waypoints que rodean "
            "el poligono). Hay un cooldown de rerouting de 15 ticks para evitar oscilaciones. "
            "Los atascos simulan condiciones de trafico reales que afectan los tiempos de respuesta."
        ),
    },
    {
        "source_type": "project_docs",
        "source_ref": "pois_staging",
        "content": (
            "Los POIs (Points of Interest) incluyen hospitales y gasolineras. Los hospitales "
            "sirven como destino para transportar pacientes y como puntos de staging (las "
            "ambulancias IDLE se reposicionan cerca del hospital mas cercano si estan lejos). "
            "Las gasolineras son destinos de repostaje. POIs por defecto se siembran segun la region activa "
            "(hospital y gasolineras cerca del centro de cada ciudad). "
            "Se pueden anadir POIs adicionales desde el constructor de escenario."
        ),
    },
]


async def seed_knowledge_if_empty() -> bool:
    """Siembra la tabla solo si está vacía (idempotente).

    Returns:
        True si se sembraron filas nuevas; False si ya había contenido
        o Supabase/LLM no disponibles.
    """
    sb = get_supabase()
    if sb is None:
        _logger.warning("Supabase not available — skipping knowledge seed")
        return False

    try:
        existing = sb.table("ai_knowledge_chunks").select("id").limit(1).execute()
        if existing.data:
            _logger.info("ai_knowledge_chunks already populated (%d+ rows), skipping seed", len(existing.data))
            return False
    except Exception:
        _logger.exception("Failed to check ai_knowledge_chunks")
        return False

    _logger.info("Seeding %d knowledge chunks with real embeddings...", len(KNOWLEDGE_CHUNKS))

    try:
        from . import llm_provider

        texts = [c["content"] for c in KNOWLEDGE_CHUNKS]
        embeddings = await llm_provider.embed_texts(texts)

        rows: list[dict[str, Any]] = []
        for chunk, emb in zip(KNOWLEDGE_CHUNKS, embeddings):
            rows.append({
                "source_type": chunk["source_type"],
                "source_ref": chunk.get("source_ref"),
                "content": chunk["content"],
                "metadata": {"lang": "es"},
                "embedding": emb,
            })

        sb.table("ai_knowledge_chunks").insert(rows).execute()
        _logger.info("Seeded %d knowledge chunks successfully", len(rows))
        return True
    except Exception:
        _logger.exception("Failed to seed knowledge chunks (LLM may not be available yet)")
        return False


async def seed_knowledge_force() -> int:
    """Truncate + re-seed. Útil tras cambiar los chunks.

    Returns:
        Número de filas insertadas.

    Raises:
        RuntimeError: Si Supabase no está disponible.
    """
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase not available")

    from . import llm_provider

    sb.table("ai_knowledge_chunks").delete().gte("created_at", "1970-01-01").execute()

    texts = [c["content"] for c in KNOWLEDGE_CHUNKS]
    embeddings = await llm_provider.embed_texts(texts)

    rows: list[dict[str, Any]] = []
    for chunk, emb in zip(KNOWLEDGE_CHUNKS, embeddings):
        rows.append({
            "source_type": chunk["source_type"],
            "source_ref": chunk.get("source_ref"),
            "content": chunk["content"],
            "metadata": {"lang": "es"},
            "embedding": emb,
        })

    sb.table("ai_knowledge_chunks").insert(rows).execute()
    return len(rows)
