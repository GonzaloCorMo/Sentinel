# HPE Ambulancia Digital Twin — Documentación

Gemelo digital de flota sanitaria con dashboard de operaciones, PWA ciudadana, panel de vehículo, motor de IA (HITL + autónomo) y pipeline ML. Multi-región (Aruba · Madrid · Bogotá · CDMX), integración Aruba Pulse vía Kafka, LLM HPE-vLLM y i18n (es/en/gl).

## Producción

- **Servidor**: `10.10.48.25`
- Dashboard / PWA: `http://10.10.48.25:5173`
- API simulación: `http://10.10.48.25:8080` (OpenAPI: `/openapi.yaml`)
- MkDocs: `http://10.10.48.25:3001`
- Supabase: `http://10.10.48.25:54321` (Studio `:54323`)

## Stack

| Área | Tecnología |
|---|---|
| Frontend dashboard + PWA + panel vehículo | Vue 3 + Vite + TS + Tailwind + Leaflet + ECharts + Pinia + vue-i18n (es/en/gl) |
| Backend simulación | FastAPI + asyncio + aiomqtt + aiokafka + httpx |
| Base de datos + Auth | Supabase (Postgres + pgvector + GoTrue + Storage) |
| LLM chat | HPE-vLLM externo (Gemma flash · Qwen flagship) — `10.10.48.10:8000/8001` |
| Embeddings | Ollama local (`nomic-embed-text`) — perfiles compose `gpu-nvidia/gpu-amd/cpu` |
| Routing | OSRM multi-región — 4 grafos en paralelo (Aruba/Madrid/Bogotá/CDMX) |
| Mensajería | Mosquitto (MQTT) + fallback P2P + HTTP + replay backup Kafka |
| Eventos externos | Kafka Aruba Pulse (`aruba.events`, `aruba.weather`) + producer AsyncAPI |
| Servicio ML | FastAPI + ONNX Runtime + IsolationForest (`fleet_anomaly`) |

## Secciones

- **Getting Started** — levantar el stack, probar la PWA móvil
- **Arquitectura** — contratos operativos frontend ↔ backend
- **Auth** — Supabase Auth, roles (admin / vehicle / citizen)
- **Technical** — BD, OSRM multi-región, API SSE, IA HITL/autónomo, chatbot RAG, Aruba Pulse Kafka, telemetría v2.0
- **ML Training** — pipeline entrenamiento, modo simulación autónoma
- **Guía de usuario** — manual operador, chatbot comando, operación en tiempo real

## Arranque rápido

```bash
./up.sh
```

Más detalles: [Arranque con Docker](getting-started/docker.md).
