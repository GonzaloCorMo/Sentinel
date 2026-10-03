# Chatbot RAG — Pipeline de IA

## Resumen

El chatbot de HPE Sentinel usa un pipeline **RAG (Retrieval-Augmented Generation)** para responder preguntas sobre el proyecto, la tecnologia utilizada y los protocolos operativos. Las respuestas se generan combinando busqueda semantica en una base de conocimiento vectorizada con un modelo de lenguaje (LLM).

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
2. Genera un embedding de la pregunta via `llm_provider.embed_text()`.
3. Busca los fragmentos mas relevantes en `ai_knowledge_chunks` via RPC `match_ai_knowledge_chunks`.
4. Construye un prompt con el contexto recuperado.
5. Llama al LLM en modo streaming via `chat_completion_stream()`.
6. Persiste los mensajes (usuario y asistente) en `chat_messages`.

### `simulation/app/llm_provider.py`

Capa de abstraccion del proveedor de LLM:

- **Chat → HPE-vLLM externo** (API OpenAI-compat). Dos endpoints disponibles:
  - **Flash** (default chat + tool-calling): `LLM_FLASH_BASE_URL` → `http://10.10.48.10:8000/v1`, modelo `google/gemma-4-31b-it`.
  - **Flagship** (razonamiento extenso, informes post-turno): `LLM_FLAGSHIP_BASE_URL` → `http://10.10.48.10:8001/v1`, modelo `Qwen/Qwen3-235B-A22B`.
- **Embeddings → Ollama local** (`OLLAMA_BASE_URL=http://ollama:11434/v1`), modelo `nomic-embed-text`.
- Los embeddings se rellenan con ceros hasta 1536 dimensiones para coincidir con el esquema de la base de datos (Postgres pgvector).
- El chat **no** descarga modelos al host: todo va al endpoint HPE-vLLM. Solo los embeddings necesitan GPU/CPU local (perfiles compose `gpu-nvidia`, `gpu-amd`, `cpu`).

### `simulation/app/knowledge_seeder.py`

Script para poblar la base de conocimiento:

- Define 18+ fragmentos de conocimiento sobre la arquitectura, pila tecnologica, FSM, HITL, etc.
- Se ejecuta al inicio del servidor si la tabla `ai_knowledge_chunks` esta vacia.
- Endpoint `POST /api/knowledge/seed` para forzar re-sembrado.

## Base de datos

### Tabla `ai_knowledge_chunks`

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `title` | text | Titulo descriptivo |
| `content` | text | Contenido textual |
| `metadata` | jsonb | Metadatos opcionales |
| `embedding` | vector(1536) | Embedding vectorial |

### Tabla `chat_messages`

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `session_id` | text | Sesion del chat |
| `role` | text | `user` o `assistant` |
| `content` | text | Contenido del mensaje |
| `created_at` | timestamptz | Timestamp |

### Funcion RPC `match_ai_knowledge_chunks`

```sql
match_ai_knowledge_chunks(
  query_embedding vector(1536),
  match_count int,
  filter jsonb
) RETURNS TABLE (id, title, content, metadata, similarity)
```

## Endpoints HTTP

| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/api/chat` | Envia pregunta, recibe respuesta en streaming |
| GET | `/api/chat/history?sessionId=...` | Historial de chat de una sesion |
| POST | `/api/knowledge/seed` | Forzar re-sembrado de conocimiento |

## Frontend

### `ChatPanel.vue`

Panel flotante colapsable en la esquina inferior izquierda:

- Historial de mensajes con renderizado Markdown (`marked`).
- Envio de preguntas con respuestas en streaming via SSE.
- Sesion persistida en `localStorage`.
- Tema oscuro integrado con el diseño HPE.

## Configuracion

### Variables de entorno

| Variable | Descripcion | Valor por defecto |
|----------|-------------|-------------------|
| `LLM_FLASH_BASE_URL` | Endpoint HPE-vLLM para chat | `http://10.10.48.10:8000/v1` |
| `LLM_FLAGSHIP_BASE_URL` | Endpoint HPE-vLLM para razonamiento extenso | `http://10.10.48.10:8001/v1` |
| `LLM_FLASH_MODEL` | Modelo flash (chat + tool-calling) | `google/gemma-4-31b-it` |
| `LLM_FLAGSHIP_MODEL` | Modelo flagship (informes) | `Qwen/Qwen3-235B-A22B` |
| `LLM_API_KEY` | API key del endpoint HPE-vLLM | `not-needed` |
| `OLLAMA_BASE_URL` | URL Ollama (embeddings) | `http://ollama:11434/v1` |
| `OLLAMA_EMBED_MODEL` | Modelo de embeddings | `nomic-embed-text` |
