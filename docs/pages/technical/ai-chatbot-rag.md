# Chatbot RAG

## Resumen

El chatbot de Sentinel usa un pipeline **RAG (Retrieval-Augmented Generation)** para responder preguntas sobre el proyecto, la tecnología utilizada y los protocolos operativos. Las respuestas se generan combinando búsqueda semántica en una base de conocimiento vectorizada con un modelo de lenguaje (LLM).

## Arquitectura del pipeline

```
Pregunta del usuario
        │
        ▼
┌───────────────────┐
│  Embedding de la  │  llm_provider.embed_text()
│  pregunta         │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  Busqueda RPC     │  match_ai_knowledge_chunks()
│  en Supabase      │  (similitud coseno)
└────────┬──────────┘
         │  Top-K fragmentos relevantes
         ▼
┌───────────────────┐
│  Prompt con       │  system prompt + contexto + pregunta
│  contexto RAG     │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  LLM (streaming)  │  chat_completion_stream()
│  genera respuesta │
└────────┬──────────┘
         │
         ▼
  Respuesta al usuario (SSE chunks)
```

## Componentes

### `simulation/app/chat_service.py`

Orquestador del pipeline:

1. Recibe la pregunta del usuario y el `session_id`.
2. Genera un embedding de la pregunta vía `llm_provider.embed_text()`.
3. Busca los fragmentos más relevantes en `ai_knowledge_chunks` vía RPC `match_ai_knowledge_chunks`.
4. Construye un prompt con el contexto recuperado.
5. Llama al LLM en modo streaming vía `chat_completion_stream()`.
6. Persiste los mensajes (usuario y asistente) en `chat_messages`.

### `simulation/app/llm_provider.py`

Capa de abstracción del proveedor de LLM:

- **Chat → endpoint vLLM externo** (API compatible con OpenAI). Dos endpoints disponibles:
  - **Flash** (default chat + tool-calling): `LLM_FLASH_BASE_URL` → `http://10.10.48.10:8000/v1`, modelo `google/gemma-4-31b-it`.
  - **Flagship** (razonamiento extenso, informes post-turno): `LLM_FLAGSHIP_BASE_URL` → `http://10.10.48.10:8001/v1`, modelo `Qwen/Qwen3-235B-A22B`.
- **Embeddings → Ollama local** (`OLLAMA_BASE_URL=http://ollama:11434/v1`), modelo `nomic-embed-text`.
- Los embeddings se rellenan con ceros hasta 1536 dimensiones para coincidir con el esquema de la base de datos (Postgres pgvector).
- El chat **no** descarga modelos al host: todo va al endpoint vLLM externo. Solo los embeddings necesitan GPU/CPU local (perfiles compose `gpu-nvidia`, `gpu-amd`, `cpu`).

### `simulation/app/knowledge_seeder.py`

Script para poblar la base de conocimiento:

- Define 18+ fragmentos de conocimiento sobre la arquitectura, pila tecnologica, FSM, HITL, etc.
- Se ejecuta al inicio del servidor si la tabla `ai_knowledge_chunks` está vacía.
- Endpoint `POST /api/knowledge/seed` para forzar re-sembrado.

## Base de datos

### Tabla `ai_knowledge_chunks`

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | uuid | PK |
| `title` | text | Título descriptivo |
| `content` | text | Contenido textual |
| `metadata` | jsonb | Metadatos opcionales |
| `embedding` | vector(1536) | Embedding vectorial |

### Tabla `chat_messages`

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | uuid | PK |
| `session_id` | text | Sesión del chat |
| `role` | text | `user` o `assistant` |
| `content` | text | Contenido del mensaje |
| `created_at` | timestamptz | Timestamp |

### Función RPC `match_ai_knowledge_chunks`

```sql
match_ai_knowledge_chunks(
  query_embedding vector(1536),
  match_count int,
  filter jsonb
) RETURNS TABLE (id, title, content, metadata, similarity)
```

## Endpoints HTTP

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/chat` | Envía pregunta, recibe respuesta en streaming |
| GET | `/api/chat/history?sessionId=...` | Historial de chat de una sesión |
| POST | `/api/knowledge/seed` | Forzar re-sembrado de conocimiento |

## Frontend

### `ChatPanel.vue`

Panel flotante colapsable en la esquina inferior izquierda (uso desde el punto de vista del operador en [Chatbot IA](../guia-usuario/chatbot-ia.md)):

- Historial de mensajes con renderizado Markdown (`marked`).
- Envío de preguntas con respuestas en streaming vía SSE.
- Sesión persistida en `localStorage`.
- Tema oscuro integrado con el resto del dashboard.

## Configuración

### Variables de entorno

| Variable | Descripción | Valor por defecto |
|----------|-------------|-------------------|
| `LLM_FLASH_BASE_URL` | Endpoint vLLM externo para chat | `http://10.10.48.10:8000/v1` |
| `LLM_FLAGSHIP_BASE_URL` | Endpoint vLLM externo para razonamiento extenso | `http://10.10.48.10:8001/v1` |
| `LLM_FLASH_MODEL` | Modelo flash (chat + tool-calling) | `google/gemma-4-31b-it` |
| `LLM_FLAGSHIP_MODEL` | Modelo flagship (informes) | `Qwen/Qwen3-235B-A22B` |
| `LLM_API_KEY` | API key del endpoint vLLM externo | `not-needed` |
| `OLLAMA_BASE_URL` | URL Ollama (embeddings) | `http://ollama:11434/v1` |
| `OLLAMA_EMBED_MODEL` | Modelo de embeddings | `nomic-embed-text` |
