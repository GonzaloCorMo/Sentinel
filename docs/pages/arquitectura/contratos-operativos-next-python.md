# Contratos operativos frontend ↔ backend

El frontend de producto es **Vue 3** (`frontend/`). El motor vive en **FastAPI** (`simulation/app/main.py`) y se consume directamente a través del proxy de Vite (`/api` → servicio `simulation`). No hay capa intermedia.

## Canal principal: SSE

El frontend se suscribe a `GET /api/sim/stream` con `EventSource`. Cada mensaje (`data:`) lleva el snapshot completo:

```json
{
  "state": {
    "connected": true,
    "updatedAt": "2026-10-03T09:15:00+00:00",
    "isSimulating": true,
    "paused": false,
    "motorState": "RUNNING",
    "networkStatus": { "mqtt": true, "p2p": true, "http": true },
    "linkState": "mqtt_active",
    "ambulances": [],
    "emergencies": [],
    "pois": [],
    "jams": [],
    "companions": [],
    "commsRecent": [],
    "stats": { "totalAmbulances": 0, "activeEmergencies": 0, "resolvedEmergencies": 0, "simulationSpeed": 1 },
    "aiMode": "hitl",
    "aiProposals": [],
    "aiLog": [],
    "dispatchRequiresApproval": true,
    "externalEvents": [],
    "weatherStations": {},
    "eventSourceStatus": { "enabled": true, "source": "mock", "status": "running" },
    "externalEmergencyRatePerMin": 0
  }
}
```

(Ejemplo abreviado; la lista completa de claves está en [API de simulación](../technical/simulation-api-http-sse.md#estado-global).)

`GET /api/sim/state` devuelve el mismo objeto sin envolver y sirve como polling de respaldo.

## Acciones (HTTP)

Las mutaciones van por `POST`/`DELETE` bajo `/api/sim/*`, `/api/ai/*`, `/api/regions/*`, `/api/events/*` y `/api/weather/*`. El efecto se observa en el siguiente mensaje SSE; el frontend no mantiene estado optimista propio salvo en formularios.

| Acción | Endpoint |
|---|---|
| Play / pausa / reset / velocidad | `POST /api/sim/control` |
| Crear emergencia, unidad, POI o atasco | `POST /api/sim/emergency` · `/api/sim/spawn` · `/api/sim/poi` · `/api/sim/jam` |
| Asignación manual | `POST /api/sim/assign` |
| Canales de red | `POST /api/sim/network` |
| Modo IA y propuestas | `POST /api/ai/mode` · `POST /api/ai/proposals/{id}/resolve` |
| Región activa | `POST /api/regions/active` |
| Chat RAG (streaming) | `POST /api/chat` |

## Variables de entorno

Un único `.env` en la raíz; Docker Compose inyecta cada variable en su servicio.

| Variable | Destino | Ejemplo |
|----------|---------|---------|
| `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` | Frontend | `http://localhost:54321` |
| `DEV_PROXY_API_TARGET` | Frontend (proxy Vite `/api`) | `http://simulation:8080` |
| `DEV_PROXY_SB_TARGET` | Frontend (proxy Vite `/sb`) | `http://supabase-kong:8000` |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Backend | `http://supabase-kong:8000` |
| `OSRM_URL_<REGION>` / `DEFAULT_REGION` | Backend | `http://osrm-aruba:5000` / `aruba` |
| `EVENT_SOURCE` y `MOCK_*` | Backend | `mock` (ver [Fuente de eventos](../technical/fuente-de-eventos.md)) |
| `LLM_FLASH_BASE_URL` / `LLM_FLAGSHIP_BASE_URL` | Backend | endpoint vLLM externo |
| `OLLAMA_BASE_URL` | Backend | `http://ollama:11434/v1` |

## Persistencia operativa en Supabase

- Telemetría en `telemetry_logs` con inserción batch por tick.
- Propuestas IA en `ai_hitl_proposals`.
- Historial de chat en `chat_messages`.
- Lecturas meteorológicas en `weather_readings`.

## Base vectorial para IA (RAG)

`pgvector` con dos tablas vectoriales:

- `protocols_knowledge`: protocolos operativos para la decisión HITL.
- `ai_knowledge_chunks`: base de conocimiento del chatbot.

Funciones RPC:

- `match_protocols(query_embedding, match_threshold, match_count)`
- `match_ai_knowledge_chunks(query_embedding, match_count, filter)`

## Migraciones SQL

Viven en `supabase/migrations/` y se aplican al crear la base de datos (servicio `supabase-migrator`). Esquema detallado en [Base de datos](../technical/base-de-datos.md).
