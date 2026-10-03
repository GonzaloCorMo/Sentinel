# Arquitectura hibrida Vue + Python

## Decision

Se mantiene arquitectura hibrida:

- `frontend/` (Vue 3 + Vite + TypeScript + Supabase): UI, auth, rutas protegidas y capa web.
- `simulation/` (FastAPI + asyncio): motores de simulacion, IA, RAG y telemetria.

## Motivo

- Reducir riesgo de migrar motores maduros de Python a TypeScript.
- Acelerar entrega de producto web con Vue 3 y Supabase como backend de auth/datos.
- Evolucionar por contrato SSE/HTTP entre frontend y backend.

## Integracion

```
┌──────────────┐    SSE / HTTP    ┌──────────────────┐
│  Vue 3       │ ◄──────────────► │  FastAPI          │
│  (Vite proxy)│   /api/*         │  (simulation/)    │
└──────┬───────┘                  └────────┬─────────┘
       │                                   │
       │ VITE_SUPABASE_*                   │ SUPABASE_SERVICE_ROLE_KEY
       ▼                                   ▼
┌──────────────────────────────────────────────────┐
│              Supabase (PostgreSQL + pgvector)     │
└──────────────────────────────────────────────────┘
```

- Vite proxea `/api/*` a `http://127.0.0.1:8000` (FastAPI).
- El frontend usa la clave anonima de Supabase para auth.
- El backend Python usa la clave service role para escritura de telemetria e IA.

## Comunicacion en tiempo real

El motor FastAPI expone un endpoint SSE (`GET /api/stream`) que emite el estado completo de la simulacion cada tick:

- Ambulancias (posicion, estado FSM, telemetria, severidad del paciente)
- Emergencias (activas, asignadas, resueltas)
- POIs (hospitales, gasolineras), atascos
- Propuestas IA pendientes y log de actividad
- Estado de comunicaciones (canal activo, metricas)

El frontend se suscribe con `EventSource` y actualiza el store de Pinia.

## Endpoints principales (FastAPI directo)

| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/api/stream` | SSE con estado en tiempo real |
| GET | `/api/state` | Estado completo (polling fallback) |
| POST | `/api/control/toggle` | Play/Pause |
| POST | `/api/control/speed` | Cambiar velocidad |
| POST | `/api/spawn` | Crear ambulancia o emergencia |
| POST | `/api/delete` | Eliminar entidad |
| POST | `/api/network` | Activar/desactivar canal de red |
| POST | `/api/chat` | Chatbot RAG (streaming) |
| GET | `/api/ai/mode` | Consultar modo IA |
| POST | `/api/ai/mode` | Cambiar modo IA (HITL/autonomo) |
| POST | `/api/hitl/respond` | Aprobar/rechazar propuesta IA |

## Persistencia en Supabase

Las tablas clave son:

- `telemetry_logs` — logs de telemetria por tick (relacional, bigint ID)
- `ai_hitl_proposals` — propuestas de la IA con estado (pending/approved/rejected/auto_approved)
- `protocols_knowledge` — protocolos con embeddings para RAG
- `ai_knowledge_chunks` — base de conocimiento del chatbot con embeddings
- `chat_messages` — historial de conversaciones del chatbot

Consulta [Base de datos](../technical/base-de-datos.md) para el esquema completo.
