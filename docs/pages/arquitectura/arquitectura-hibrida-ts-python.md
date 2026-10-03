# Arquitectura híbrida Vue + Python

## Decisión

Se mantiene una arquitectura híbrida:

- `frontend/` (Vue 3 + Vite + TypeScript + Supabase): UI, auth, rutas protegidas y capa web.
- `simulation/` (FastAPI + asyncio): motores de simulación, IA, RAG, telemetría y fuente de eventos.

## Motivo

- Reducir el riesgo de migrar motores maduros de Python a TypeScript.
- Acelerar la entrega del producto web con Vue 3 y Supabase como backend de auth y datos.
- Evolucionar por contrato SSE/HTTP entre frontend y backend.

## Integración

```
┌──────────────┐    SSE / HTTP    ┌───────────────────┐
│  Vue 3       │ ◄──────────────► │  FastAPI          │
│  (proxy Vite)│   /api/*         │  (simulation/)    │
└──────┬───────┘                  └────────┬──────────┘
       │                                   │
       │ /sb/* → Kong                      │ SUPABASE_SERVICE_ROLE_KEY
       ▼                                   ▼
┌──────────────────────────────────────────────────┐
│          Supabase (PostgreSQL + pgvector)        │
└──────────────────────────────────────────────────┘
```

- Vite hace de punto de entrada único: proxea `/api/*` al motor FastAPI y `/sb/*` a Supabase (Kong). Ver [Probar la PWA en móvil](../getting-started/pwa-mobile.md#detalles-tecnicos).
- El frontend usa la clave anónima de Supabase para auth.
- El backend Python usa la clave service role para escribir telemetría, eventos e IA.

## Comunicación en tiempo real

El motor expone `GET /api/sim/stream` (SSE) y emite el snapshot completo de la simulación a ~2,5 Hz:

- Ambulancias (posición, estado FSM, telemetría, severidad del paciente).
- Emergencias (activas, asignadas, resueltas).
- POIs (hospitales, gasolineras, estaciones de carga y meteorológicas) y atascos.
- Propuestas IA pendientes y log de actividad.
- Estado de comunicaciones (canal activo, métricas).
- Eventos externos y meteorología de la [fuente de eventos](../technical/fuente-de-eventos.md).

El frontend se suscribe con `EventSource` y actualiza el store de Pinia (`frontend/src/stores/simulation.ts`). Si el stream cae, recurre a polling de `GET /api/sim/state`.

Contrato detallado de endpoints: [API de simulación (HTTP + SSE)](../technical/simulation-api-http-sse.md).

## Persistencia en Supabase

Tablas clave:

- `telemetry_logs`: telemetría por tick (relacional, ID bigint).
- `ai_hitl_proposals`: propuestas de la IA con estado (`pending` / `approved` / `rejected` / `auto_approved`).
- `protocols_knowledge`: protocolos con embeddings para RAG.
- `ai_knowledge_chunks`: base de conocimiento del chatbot con embeddings.
- `chat_messages`: historial de conversaciones del chatbot.
- `weather_readings`: lecturas meteorológicas ingeridas.

Esquema completo en [Base de datos](../technical/base-de-datos.md).
