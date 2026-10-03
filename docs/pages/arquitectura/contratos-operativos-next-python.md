# Contratos operativos — API SSE/HTTP

> **Estado (marzo 2026):** El frontend de producto es **Vue 3** (`frontend/`). El motor vive en **FastAPI** (`simulation/app/main.py`) y se consume directamente via proxy Vite (`/api` → `http://127.0.0.1:8000`). No hay facade intermediaria.

## Comunicacion principal: SSE

El frontend se suscribe a `GET /api/stream` via `EventSource`. El motor emite un evento `state` cada tick con el payload completo de la simulacion:

```json
{
  "running": true,
  "tick": 142,
  "speed": 2,
  "ambulances": [...],
  "emergencies": [...],
  "pois": [...],
  "jams": [...],
  "comms": {...},
  "aiProposals": [...],
  "aiMode": "hitl",
  "aiLog": [...]
}
```

## Endpoints de simulacion (FastAPI directo)

| Metodo | Ruta | Body | Descripcion |
|--------|------|------|-------------|
| GET | `/api/state` | — | Estado completo (fallback a SSE) |
| GET | `/api/stream` | — | SSE con estado en tiempo real |
| POST | `/api/control/toggle` | — | Play/Pause simulacion |
| POST | `/api/control/speed` | `{ "speed": 5 }` | Cambiar multiplicador de velocidad |
| POST | `/api/spawn` | `{ "type": "EMERGENCY", "lat": ..., "lng": ..., "title": "..." }` | Crear entidad |
| POST | `/api/delete` | `{ "type": "ambulance", "id": "..." }` | Eliminar entidad |
| POST | `/api/network` | `{ "channel": "mqtt", "enabled": true }` | Configurar canal de red |
| POST | `/api/spawn/poi` | `{ "type": "hospital", "lat": ..., "lng": ..., "name": "..." }` | Crear POI |
| POST | `/api/spawn/jam` | `{ "lat": ..., "lng": ..., "radius": 500 }` | Crear atasco |

## Endpoints de IA

| Metodo | Ruta | Body | Descripcion |
|--------|------|------|-------------|
| GET | `/api/ai/mode` | — | Consultar modo (hitl/autonomous) |
| POST | `/api/ai/mode` | `{ "mode": "autonomous" }` | Cambiar modo IA |
| POST | `/api/hitl/respond` | `{ "proposal_id": "...", "action": "approve" }` | Responder a propuesta |
| POST | `/api/chat` | `{ "message": "...", "sessionId": "..." }` | Chat RAG (streaming) |
| GET | `/api/chat/history` | `?sessionId=...` | Historial de chat |
| POST | `/api/knowledge/seed` | — | Re-sembrar base de conocimiento |

## Variables de entorno

| Variable | Destino | Ejemplo |
|----------|---------|---------|
| `VITE_SIMULATION_API_URL` | Frontend (proxy Vite) | `http://127.0.0.1:8000` |
| `SUPABASE_URL` | Backend Python | `http://127.0.0.1:54321` |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend Python | (ver `supabase status`) |
| `OSRM_BASE_URL` | Backend Python | `http://127.0.0.1:5001` |
| `OPENAI_API_KEY` | Backend Python | (opcional, si no Ollama) |
| `OLLAMA_BASE_URL` | Backend Python | `http://localhost:11434/v1` |

## Persistencia operativa en Supabase

La telemetria se persiste en `telemetry_logs` con insercion batch por tick. Las propuestas IA se registran en `ai_hitl_proposals`. El historial de chat se almacena en `chat_messages`.

## Base vectorial para IA (RAG)

Se habilita `pgvector` con dos tablas vectoriales:

- `protocols_knowledge` — protocolos operativos para decision HITL
- `ai_knowledge_chunks` — base de conocimiento del chatbot

Funciones RPC:

- `match_protocols(query_embedding, match_threshold, match_count)` — busqueda de protocolos por similitud
- `match_ai_knowledge_chunks(query_embedding, match_count, filter)` — busqueda de conocimiento por similitud

## Migraciones SQL

- `supabase/migrations/20260329120000_full_schema.sql` — esquema completo
- `supabase/migrations/20260329130000_chat_and_ai_mode.sql` — chat y modo IA
