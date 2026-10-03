# Introducción

## Objetivo

Simular la operación real de una flota de ambulancias en distintas regiones (Aruba por defecto, también Madrid, Bogotá y Ciudad de México): despacho de emergencias, telemetría vehicular + clínica + ambiental + red, resiliencia de comunicaciones, IA que detecta anomalías y propone/ejecuta acciones, integración con la fuente externa de datos del reto (Aruba Pulse vía Kafka) y exposición de información para análisis masivo y ML.

## Componentes

- **Dashboard operador** (`/map`, `/fleet`, `/comms`, `/config`, `/reports`, `/island`) — operación central, vista global de la isla, chatbot ⌘ comando.
- **PWA ciudadana** (`/message-alert`) — reportar emergencia desde móvil con voz + geolocalización.
- **Panel vehículo** (`/vehicle`) — el piloto registra unidad, recibe asignación, ve ruta OSRM con steps. Login del rol `vehicle` redirige aquí por defecto.
- **Motor simulación** (`simulation/`) — FastAPI, bucle asyncio, 5 motores telemetría por unidad, ETA dinámica, scoring asignación.
- **IA observer** (`simulation/app/ai_decision_engine.py`) — anomalías + RAG + propuestas HITL o ejecución autónoma.
- **Servicio LLM** (`simulation/app/llm_provider.py`) — cliente HPE-vLLM (Gemma flash + Qwen flagship) + Ollama embeddings local.
- **Servicio ML** (`ml-service/`) — ONNX Runtime + IsolationForest, modelo `fleet_anomaly`.
- **Kafka producer/consumer** (`simulation/app/events_consumer.py`) — consume `aruba.events` + `aruba.weather`, publica telemetría AsyncAPI.
- **Multi-region OSRM** (`docker/osrm-data/`) — 4 grafos paralelos; conmutar región resetea simulación.
- **Supabase** — Postgres + pgvector + GoTrue + Storage.

## Flujo alto nivel

```
Aruba Pulse Kafka ──> events_consumer ──┐
Ciudadano (PWA) ─────────────────────────┤
Operador ─────────────────────────────────┼─> Emergencia → Motor sim → IA observer ──┬─> HITL panel (aprueba operador)
Motor IA ─────────────────────────────────┘                                            └─> Autónomo (IA ejecuta)
                                                                                                  │
                                                                                                  ▼
                                                                                   dispatch → ambulancia OSRM (región activa)
                                                                                                  │
                                                                                                  ▼
                                                                            telemetría v2.0 → SSE → dashboard
                                                                                                  ├─> Kafka producer (aruba.team.tres-dias-de-gracia)
                                                                                                  └─> telemetry_logs → ml_telemetry_features → ONNX
```

## Modos IA

- `hitl` — IA propone, operador aprueba. Motor sigue auto-asignando ambulancias a emergencias.
- `autonomous` — IA ejecuta sin aprobación. Motor ya NO auto-asigna; solo la IA manda. Al cambiar a este modo, las propuestas pendientes se auto-resuelven inmediatamente.

## i18n

Tres idiomas integrados (vue-i18n): español (default), inglés, gallego. Selector visible en cabecera, login, panel vehículo y PWA. Las claves cubren toda la UI: navegación, tipos de entidad, filtros, panel IA, chatbot, mensajes de error.

## Documentación por rol

- **Desarrollador** — arranque: [Docker](getting-started/docker.md) · arquitectura · technical/*
- **Operador** — [Manual de uso](guia-usuario/manual-de-uso.md) · [Chatbot IA](guia-usuario/chatbot-ia.md)
- **Integrador** — [API SSE](technical/simulation-api-http-sse.md) · [Aruba Pulse Kafka](technical/aruba-pulse-kafka.md) · [Base de datos](technical/base-de-datos.md)
