# Introducción

**Sentinel** es un gemelo digital de una flota de ambulancias. Simula la operación real en distintas regiones (Santiago de Compostela por defecto; también Bogotá y Ciudad de México): despacho de emergencias, telemetría vehicular, clínica, ambiental y de red, resiliencia de comunicaciones, IA que detecta anomalías y propone o ejecuta acciones, ingesta de eventos externos (incidentes y meteorología) y exposición de datos para análisis masivo y ML.

## Componentes

- **Dashboard del operador** (Mapa `/map` · Situación `/overview` · Flota `/fleet` · Conectividad `/comms` · Informes `/reports` · Ajustes `/config`): operación central, resumen de la región y asistente con modos "Preguntar" y "Dar una orden".
- **PWA ciudadana** (`/message-alert`, alias `/m`): reportar una emergencia desde el móvil con voz y geolocalización.
- **Panel del vehículo** (`/vehicle`): el piloto registra su unidad, recibe la asignación y ve la ruta OSRM con indicaciones paso a paso. El login con rol `vehicle` redirige aquí por defecto.
- **Motor de simulación** (`simulation/`): FastAPI con bucle asyncio, cinco motores de telemetría por unidad, ETA dinámica y scoring de asignación.
- **Fuente de eventos** (`simulation/app/event_source.py`): generador mock de eventos externos y lecturas meteorológicas, más ingesta REST para fuentes reales. Ver [Fuente de eventos](technical/fuente-de-eventos.md).
- **IA observer** (`simulation/app/ai_decision_engine.py`): anomalías + RAG + propuestas HITL o ejecución autónoma.
- **Proveedor LLM** (`simulation/app/llm_provider.py`): cliente compatible con OpenAI que habla con Ollama dentro del stack (chat `qwen2.5:3b` y embeddings `nomic-embed-text`). Ver [IA local con Ollama](technical/ai-chatbot-rag.md#ia-local-con-ollama).
- **Servicio ML** (`ml-service/`): ONNX Runtime + IsolationForest, modelo `fleet_anomaly`.
- **OSRM multirregión** (`docker/osrm-data/`): tres grafos en paralelo; cambiar de región reinicia la simulación.
- **Supabase**: Postgres + pgvector + GoTrue + Storage.

## Stack

| Área | Tecnología |
|---|---|
| Frontend (dashboard, PWA, panel vehículo) | Vue 3 + Vite + TypeScript + Tailwind + MapLibre GL (teselas vectoriales OpenFreeMap) + Pinia + vue-i18n (es/en/gl) |
| Backend de simulación | FastAPI + asyncio + aiomqtt + httpx |
| Base de datos y auth | Supabase (Postgres + pgvector + GoTrue + Storage) |
| LLM (chat, comandos, informes) | Ollama local, API compatible con OpenAI (`qwen2.5:3b` por defecto) |
| Embeddings | Ollama local (`nomic-embed-text`); perfiles compose `gpu-nvidia` / `gpu-amd` / `cpu` |
| Routing | OSRM multirregión: 3 grafos en paralelo (Santiago / Bogotá / CDMX) |
| Mensajería | Mosquitto (MQTT) + fallback P2P + fallback HTTP |
| Eventos externos | Generador mock local + ingesta REST (`/api/events/ingest`, `/api/weather/ingest`) |
| Servicio ML | FastAPI + ONNX Runtime + IsolationForest (`fleet_anomaly`) |
| Documentación | VitePress (este sitio) |

## URLs locales

| Servicio | URL |
|---|---|
| Dashboard / PWA | `http://localhost:5173` |
| API de simulación | `http://localhost:8080` (OpenAPI en `/openapi.yaml`) |
| Documentación | `http://localhost:3001` |
| Supabase | `http://localhost:54321` (Studio en `:54323`) |

Puertos completos en [Arranque con Docker](getting-started/docker.md#puertos-expuestos).

## Flujo de alto nivel

```
Fuente de eventos (mock / ingesta REST) ─┐
Ciudadano (PWA) ─────────────────────────┤
Operador ────────────────────────────────┼─> Emergencia → Motor sim → IA observer ──┬─> Panel HITL (aprueba el operador)
Motor IA ────────────────────────────────┘                                          └─> Autónomo (ejecuta la IA)
                                                                                                │
                                                                                                ▼
                                                                          despacho → ambulancia con ruta OSRM (región activa)
                                                                                                │
                                                                                                ▼
                                                                          telemetría v2.0 → SSE → dashboard
                                                                                                └─> telemetry_logs → ml_telemetry_features → ONNX
```

## Modos de IA

- `hitl` ("Con aprobación"): la IA propone y el operador aprueba. El motor sigue autoasignando ambulancias a emergencias.
- `autonomous` ("Autónoma"): la IA ejecuta sin aprobación. El motor deja de autoasignar; solo manda la IA. Al cambiar a este modo, las propuestas pendientes se resuelven automáticamente.

Detalle en [IA: HITL y autónomo](technical/ai-hitl-autonomo.md).

## i18n

Tres idiomas integrados (vue-i18n): español (por defecto), inglés y gallego. El selector está visible en cabecera, login, panel del vehículo y PWA. Las claves cubren toda la UI: navegación, tipos de entidad, filtros, panel IA, chatbot y mensajes de error.

## Documentación por rol

- **Desarrollador**: [Levantar el proyecto](getting-started/levantar-proyecto.md) · [Arranque con Docker](getting-started/docker.md) · [Arquitectura](arquitectura/arquitectura-hibrida-ts-python.md) · [Trazabilidad técnica](technical/trazabilidad-tecnica.md)
- **Operador**: [Manual de uso](guia-usuario/manual-de-uso.md) · [Chatbot IA](guia-usuario/chatbot-ia.md) · [Operación de despacho](guia-usuario/operacion-real-despacho.md)
- **Integrador**: [API HTTP + SSE](technical/simulation-api-http-sse.md) · [Fuente de eventos](technical/fuente-de-eventos.md) · [Base de datos](technical/base-de-datos.md)
