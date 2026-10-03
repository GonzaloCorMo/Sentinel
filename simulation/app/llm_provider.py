"""Abstracción del proveedor LLM: vLLM externo (chat) + Ollama local (embeddings).

Chat → endpoints OpenAI-compat servidos por un vLLM externo en red interna:

- Flash (default): ``LLM_FLASH_MODEL`` en ``LLM_FLASH_BASE_URL`` (Gemma).
- Flagship (opt-in por llamada): ``LLM_FLAGSHIP_MODEL`` en ``LLM_FLAGSHIP_BASE_URL`` (Qwen).

Embeddings → siguen en Ollama local (``OLLAMA_BASE_URL``, ``OLLAMA_EMBED_MODEL``;
por defecto ``nomic-embed-text``). Los endpoints vLLM listados son chat-only.

Ambos comparten la API OpenAI-compatible, así que el cliente ``AsyncOpenAI``
sirve para los tres y el resto del código es agnóstico del provider.
Los embeddings se rellenan con ceros o se truncan a ``EMBEDDING_DIM`` (1536)
para mantener la dimensión esperada por la tabla pgvector.

Restricciones por modelo aplicadas en este módulo:

- Gemma no soporta ``enable_thinking`` ni
  ``extra_body.chat_template_kwargs.enable_thinking`` → se eliminan antes de enviar.
- Qwen no soporta input de imagen → se rechaza con ``ValueError`` cualquier
  mensaje con ``image_url`` cuando el target es Flagship.
"""
from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Literal

from openai import AsyncOpenAI

_logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1536

ChatTier = Literal["flash", "flagship"]

_flash_client: AsyncOpenAI | None = None
_flagship_client: AsyncOpenAI | None = None
_embed_client: AsyncOpenAI | None = None
_flash_model: str = ""
_flagship_model: str = ""
_embed_model: str = ""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _init() -> None:
    """Inicializa singletons de chat (Flash + Flagship) y embeddings (Ollama).

    Idempotente: ya inicializado, retorna sin hacer nada.
    """
    global _flash_client, _flagship_client, _embed_client
    global _flash_model, _flagship_model, _embed_model
    if _flash_client is not None:
        return

    api_key = _env("LLM_API_KEY", "not-needed")

    flash_url = _env("LLM_FLASH_BASE_URL", "http://10.10.48.10:8000/v1")
    _flash_model = _env("LLM_FLASH_MODEL", "google/gemma-4-31b-it")
    _flash_client = AsyncOpenAI(base_url=flash_url, api_key=api_key)

    flagship_url = _env("LLM_FLAGSHIP_BASE_URL", "http://10.10.48.10:8001/v1")
    _flagship_model = _env("LLM_FLAGSHIP_MODEL", "Qwen/Qwen3-235B-A22B")
    _flagship_client = AsyncOpenAI(base_url=flagship_url, api_key=api_key)

    embed_url = _env("OLLAMA_BASE_URL", "http://ollama:11434/v1")
    _embed_model = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    _embed_client = AsyncOpenAI(base_url=embed_url, api_key="ollama")

    _logger.info(
        "LLM provider: chat flash=%s @ %s | flagship=%s @ %s | embeddings=%s @ %s",
        _flash_model, flash_url, _flagship_model, flagship_url, _embed_model, embed_url,
    )


def _resolve_chat(tier: ChatTier) -> tuple[AsyncOpenAI, str]:
    _init()
    if tier == "flagship":
        assert _flagship_client is not None
        return _flagship_client, _flagship_model
    assert _flash_client is not None
    return _flash_client, _flash_model


def _is_gemma(model: str) -> bool:
    return "gemma" in model.lower()


def _is_qwen(model: str) -> bool:
    return "qwen" in model.lower()


def _strip_thinking(kwargs: dict[str, Any]) -> None:
    """Quita `enable_thinking` y `extra_body.chat_template_kwargs.enable_thinking`."""
    kwargs.pop("enable_thinking", None)
    extra = kwargs.get("extra_body")
    if isinstance(extra, dict):
        ctk = extra.get("chat_template_kwargs")
        if isinstance(ctk, dict):
            ctk.pop("enable_thinking", None)
            if not ctk:
                extra.pop("chat_template_kwargs", None)
        if not extra:
            kwargs.pop("extra_body", None)


def _reject_images(messages: list[dict[str, Any]]) -> None:
    """Lanza ValueError si algún mensaje contiene `image_url` (Qwen no soporta imagen)."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    raise ValueError(
                        "Qwen flagship endpoint does not support image input; "
                        "use flash (Gemma) for multimodal calls."
                    )


def _prepare(model: str, kwargs: dict[str, Any]) -> None:
    """Aplica restricciones por modelo sobre kwargs+messages in-place."""
    if _is_gemma(model):
        _strip_thinking(kwargs)
    if _is_qwen(model):
        _reject_images(kwargs.get("messages", []))


def get_provider_name() -> str:
    """Identificador del provider activo."""
    _init()
    return "vllm"


def is_llm_available() -> bool:
    """True si los clientes se inicializaron correctamente (suele ser siempre)."""
    _init()
    return _flash_client is not None


def _pad_embedding(vec: list[float], dim: int = EMBEDDING_DIM) -> list[float]:
    """Rellena con ceros o trunca un vector al tamaño ``dim`` exigido por pgvector."""
    if len(vec) >= dim:
        return vec[:dim]
    return vec + [0.0] * (dim - len(vec))


async def embed_text(text: str) -> list[float]:
    """Calcula el embedding de un texto y lo ajusta a 1536 dimensiones."""
    _init()
    assert _embed_client is not None, "LLM provider not initialised"
    resp = await _embed_client.embeddings.create(input=text, model=_embed_model)
    raw = resp.data[0].embedding
    return _pad_embedding(raw)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed en batch; evita N round-trips al provider.

    Args:
        texts: Lista de strings (sin None).

    Returns:
        Lista de vectores 1536-D en el mismo orden que la entrada.
    """
    _init()
    assert _embed_client is not None, "LLM provider not initialised"
    resp = await _embed_client.embeddings.create(input=texts, model=_embed_model)
    return [_pad_embedding(d.embedding) for d in resp.data]


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    tier: ChatTier = "flash",
) -> str:
    """Chat completion no-streaming; devuelve el mensaje completo del asistente."""
    client, model = _resolve_chat(tier)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    _prepare(model, kwargs)
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def chat_completion_json(
    messages: list[dict[str, str]],
    *,
    schema: dict[str, Any] | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    tier: ChatTier = "flash",
) -> dict[str, Any]:
    """Structured chat completion — fuerza al modelo a devolver JSON parseable.

    Usa `response_format={"type":"json_object"}` (vLLM/OpenAI-compat).
    Si se proporciona `schema`, se inyecta como system prompt como hint extra
    (el server ignora el schema nativo, pero el modelo lo respeta mejor
    viéndolo en el contexto).
    Devuelve dict ya parseado (lanza `ValueError` si no es JSON válido).
    """
    import json as _json

    msgs = list(messages)
    if schema is not None:
        schema_hint = (
            "Devuelve ÚNICAMENTE un objeto JSON que cumpla este schema "
            "(sin texto adicional, sin markdown, sin fences):\n"
            f"{_json.dumps(schema, ensure_ascii=False)}"
        )
        if msgs and msgs[0].get("role") == "system":
            msgs[0] = {"role": "system", "content": msgs[0]["content"] + "\n\n" + schema_hint}
        else:
            msgs.insert(0, {"role": "system", "content": schema_hint})

    client, model = _resolve_chat(tier)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    _prepare(model, kwargs)

    resp = await client.chat.completions.create(**kwargs)
    raw = resp.choices[0].message.content or "{}"
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        s = raw.strip()
        if s.startswith("```"):
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:].strip()
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end > start:
            return _json.loads(s[start : end + 1])
        raise ValueError(f"LLM no devolvió JSON válido: {raw[:200]}")


async def chat_completion_with_tools(
    messages: list[dict[str, str]],
    *,
    tools: list[dict[str, Any]],
    tool_choice: str | dict[str, Any] = "auto",
    temperature: float = 0.2,
    max_tokens: int = 1024,
    tier: ChatTier = "flash",
) -> dict[str, Any]:
    """Chat completion con function calling estructurado.

    `tools` sigue el formato OpenAI:
    ```
    [{"type":"function","function":{"name":"dispatch_unit","description":"...",
      "parameters":{"type":"object","properties":{...},"required":[...]}}}]
    ```
    Devuelve `{"tool_calls":[{name, arguments:dict}], "content": str|None}`.
    """
    import json as _json

    client, model = _resolve_chat(tier)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": tool_choice,
    }
    _prepare(model, kwargs)

    resp = await client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    calls: list[dict[str, Any]] = []
    for tc in (msg.tool_calls or []):
        try:
            args = _json.loads(tc.function.arguments or "{}")
        except Exception:
            args = {}
        calls.append({"name": tc.function.name, "arguments": args})
    return {"tool_calls": calls, "content": msg.content}


async def chat_completion_stream(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    tier: ChatTier = "flash",
) -> AsyncIterator[str]:
    """Chat completion en streaming; emite chunks de texto según llegan.

    Útil para el SSE del chat RAG al operador: los tokens se propagan al
    frontend sin esperar a que termine la respuesta completa.
    """
    client, model = _resolve_chat(tier)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    _prepare(model, kwargs)
    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
