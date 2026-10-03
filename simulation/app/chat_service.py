"""Chat RAG del operador: preguntas sobre Sentinel con embeddings.

Pipeline por pregunta:
    1. `embed_text` de la pregunta.
    2. `match_ai_knowledge_chunks` (RPC pgvector) devuelve top-k chunks.
    3. Se inyectan al system prompt como contexto recuperado.
    4. LLM en streaming; cada chunk se reenvía al cliente vía SSE.
    5. Se persisten user+assistant en ``chat_messages`` para continuidad.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from . import llm_provider
from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres Sentinel AI, el asistente del gemelo digital de flota de ambulancias. "
    "REGLAS ESTRICTAS:\n"
    "1. SOLO respondes preguntas relacionadas con: el sistema Sentinel, ambulancias, "
    "emergencias medicas, la simulacion, la arquitectura tecnica, telemetria, IA del sistema, "
    "protocolos de emergencia, y el uso de la aplicacion.\n"
    "2. Si el usuario pregunta algo NO relacionado (ocio, cocina, deportes, opinion personal, etc.), "
    "responde UNICAMENTE: 'Lo siento, solo puedo ayudarte con temas relacionados con Sentinel "
    "y el sistema de gestion de ambulancias.'\n"
    "3. Responde en el mismo idioma que el usuario.\n"
    "4. Usa el contexto RAG proporcionado para dar respuestas precisas.\n"
    "5. Si no tienes informacion suficiente, dilo honestamente.\n"
    "6. Se conciso, profesional y tecnico."
)

MAX_CONTEXT_CHUNKS = 5


async def _retrieve_context(query: str) -> list[dict[str, Any]]:
    """Vector search de chunks relevantes para la pregunta del operador."""
    sb = get_supabase()
    if sb is None:
        return []

    try:
        query_emb = await llm_provider.embed_text(query)
        res = sb.rpc(
            "match_ai_knowledge_chunks",
            {
                "query_embedding": query_emb,
                "match_count": MAX_CONTEXT_CHUNKS,
            },
        ).execute()
        return res.data or []
    except Exception:
        _logger.exception("RAG context retrieval failed")
        return []


def _build_messages(
    query: str,
    context_chunks: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Compone la lista de mensajes OpenAI-format con system+RAG+history+user."""
    context_text = ""
    if context_chunks:
        parts = []
        for i, ch in enumerate(context_chunks, 1):
            sim = ch.get("similarity", 0)
            parts.append(f"[{i}] (sim={sim:.2f}) {ch.get('content', '')}")
        context_text = "\n".join(parts)

    system_msg = SYSTEM_PROMPT
    if context_text:
        system_msg += (
            "\n\n--- CONTEXTO RECUPERADO (base de conocimiento) ---\n"
            + context_text
            + "\n--- FIN CONTEXTO ---"
        )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_msg}]

    if history:
        for h in history[-10:]:
            messages.append({"role": h["role"], "content": h["content"]})

    messages.append({"role": "user", "content": query})
    return messages


async def chat_stream(
    message: str,
    session_id: str,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Responde al operador con RAG + LLM streaming y persiste la conversación.

    Args:
        message: Pregunta del operador.
        session_id: UUID de la sesión (persiste el hilo).
        history: Mensajes previos de la sesión (opcional; se usa ventana
            de los últimos 10).

    Yields:
        Chunks de texto según los emite el LLM.
    """
    context = await _retrieve_context(message)
    messages = _build_messages(message, context, history)

    _persist_message(session_id, "user", message)

    full_response: list[str] = []
    try:
        async for chunk in llm_provider.chat_completion_stream(messages):
            full_response.append(chunk)
            yield chunk
    except Exception:
        _logger.exception("Chat LLM stream error")
        error_msg = "Lo siento, hubo un error al generar la respuesta. Verifica que el proveedor LLM esta activo."
        yield error_msg
        full_response.append(error_msg)

    assistant_text = "".join(full_response)
    if assistant_text:
        _persist_message(session_id, "assistant", assistant_text)


def _persist_message(session_id: str, role: str, content: str) -> None:
    sb = get_supabase()
    if sb is None:
        return
    try:
        sb.table("chat_messages").insert({
            "session_id": session_id,
            "role": role,
            "content": content,
        }).execute()
    except Exception:
        _logger.exception("Failed to persist chat message")


def get_chat_history(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Carga el histórico ordenado ascendente de la sesión."""
    sb = get_supabase()
    if sb is None:
        return []
    try:
        res = (
            sb.table("chat_messages")
            .select("id, role, content, created_at")
            .eq("session_id", session_id)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception:
        _logger.exception("Failed to load chat history")
        return []
