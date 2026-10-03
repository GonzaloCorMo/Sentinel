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

- **Chat → Ollama local** (API compatible con OpenAI). Hay dos "ranuras" de modelo, que por defecto apuntan al mismo sitio:
  - **Flash** (chat y comandos con tool-calling): `LLM_FLASH_BASE_URL` → `http://ollama:11434/v1`, modelo `qwen2.5:3b`.
  - **Flagship** (informes de turno y razonamiento de la IA observadora): `LLM_FLAGSHIP_BASE_URL` → `http://ollama:11434/v1`, modelo `qwen2.5:3b`.
- **Embeddings → Ollama local** (`OLLAMA_BASE_URL=http://ollama:11434/v1`), modelo `nomic-embed-text`.
- Los embeddings se rellenan con ceros hasta 1536 dimensiones para coincidir con el esquema de la base de datos (Postgres pgvector).
- Cada llamada tiene un tiempo máximo de `LLM_TIMEOUT_SEC` (60 s; 5 s para conectar) y un reintento.

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
| `OLLAMA_CHAT_MODEL` | Modelo de chat que `ollama-init` descarga | `qwen2.5:3b` |
| `OLLAMA_EMBED_MODEL` | Modelo de embeddings | `nomic-embed-text` |
| `OLLAMA_CONTEXT_LENGTH` | Ventana de contexto de Ollama (fijada en el compose) | `8192` |
| `LLM_FLASH_BASE_URL` | Endpoint para chat y comandos | `http://ollama:11434/v1` |
| `LLM_FLAGSHIP_BASE_URL` | Endpoint para informes y razonamiento | `http://ollama:11434/v1` |
| `LLM_FLASH_MODEL` | Modelo flash (chat + tool-calling) | `qwen2.5:3b` |
| `LLM_FLAGSHIP_MODEL` | Modelo flagship (informes, IA observadora) | `qwen2.5:3b` |
| `LLM_API_KEY` | API key del endpoint (Ollama no la usa) | `not-needed` |
| `LLM_TIMEOUT_SEC` | Tiempo máximo por llamada (conexión 5 s, 1 reintento) | `60` |
| `AI_OBSERVER_LLM_CONCURRENCY` | Llamadas simultáneas de la IA observadora en segundo plano | `1` |
| `OLLAMA_BASE_URL` | URL de Ollama para embeddings | `http://ollama:11434/v1` |

## IA local con Ollama

Toda la IA corre dentro del stack de Docker, sin servicios externos: chat, comandos, informes de turno y razonamiento de la IA observadora.

### Modelos

| Uso | Modelo por defecto | Tamaño aproximado |
|-----|--------------------|-------------------|
| Chat, comandos, informes, IA observadora | `qwen2.5:3b` | ~1,9 GB |
| Embeddings (RAG) | `nomic-embed-text` | ~270 MB |

El servicio `ollama-init` descarga ambos modelos en el primer arranque y los guarda en el volumen `ollama-data`, así que no se vuelven a bajar. La ventana de contexto es de 8k tokens (`OLLAMA_CONTEXT_LENGTH=8192`).

### Perfiles según el hardware

Ollama corre en un contenedor distinto según el perfil de compose; todos se registran en la red con el alias `ollama`:

| Perfil | Contenedor | Cuándo usarlo |
|--------|------------|---------------|
| `gpu-nvidia` | `ollama-nvidia` | GPU NVIDIA con NVIDIA Container Toolkit (recomendado) |
| `gpu-amd` | `ollama-amd` | GPU AMD con ROCm |
| `cpu` | `ollama-cpu` | Sin GPU; funciona, pero el chat es bastante más lento |

```bash
docker compose --profile gpu-nvidia up -d
```

### Cambiar de modelo

1. En `.env`, indica el modelo nuevo para la descarga y para las dos ranuras:
   ```bash
   OLLAMA_CHAT_MODEL=llama3.2:3b
   LLM_FLASH_MODEL=llama3.2:3b
   LLM_FLAGSHIP_MODEL=llama3.2:3b
   ```
2. Descarga el modelo y reinicia el backend:
   ```bash
   docker compose up -d ollama-init
   docker compose up -d simulation
   ```

Para usar otro servidor compatible con OpenAI (vLLM, LM Studio…), cambia `LLM_FLASH_BASE_URL` / `LLM_FLAGSHIP_BASE_URL` (y, si hace falta, `LLM_API_KEY`). Los embeddings siguen en Ollama.

### Rendimiento orientativo

- **GPU NVIDIA RTX 3050 (4 GB)**: `qwen2.5:3b` cabe en torno al 87 % en la GPU; una respuesta del chat tarda unos 10 s.
- **CPU**: válido para probar; cuenta con respuestas bastante más lentas.
- La IA observadora hace llamadas en segundo plano. `AI_OBSERVER_LLM_CONCURRENCY=1` limita cuántas van a la vez, para que el chat del operador no tenga que esperar.
