"""Genera descripción + capacidades para tipos de flota a partir del nombre.

Usa `chat_completion_json` (structured output) para que el modelo devuelva
directamente un objeto JSON sin parseo frágil.
"""
from __future__ import annotations

import logging

from .llm_provider import chat_completion_json

_logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Eres un despachador experto de emergencias. Recibes el nombre de un tipo "
    "de unidad (vehículo, especialista, etc.) y devuelves un objeto JSON con "
    "una descripción breve (1-2 frases) y 3-6 capacidades clave, todo en español."
)

_FLEET_META_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "minLength": 5},
        "capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 6,
        },
    },
    "required": ["description", "capabilities"],
}


async def generate_description_and_capabilities(
    name: str, kind: str = "vehicle",
) -> tuple[str, list[str]]:
    """Genera descripción + capacidades del tipo a partir del nombre.

    Args:
        name: Nombre del tipo (p.ej. ``"Unidad de triaje avanzada"``).
        kind: ``"vehicle"`` o ``"place"``.

    Returns:
        Tupla ``(description, capabilities)``. Si el LLM falla o devuelve
        datos vacíos, cae a un fallback genérico.
    """
    prompt = f'Tipo: {kind}\nNombre: "{name}"\nGenera description y capabilities.'
    try:
        data = await chat_completion_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            schema=_FLEET_META_SCHEMA,
            temperature=0.4,
            max_tokens=400,
        )
        desc = str(data.get("description") or "").strip()
        caps = [str(c).strip() for c in (data.get("capabilities") or []) if str(c).strip()]
        if desc and caps:
            return desc, caps[:8]
    except Exception:
        _logger.exception("LLM describe falló para %s", name)
    return (
        f"Unidad de tipo {name}.",
        ["respuesta rápida", "equipo básico", "comunicación por radio"],
    )
