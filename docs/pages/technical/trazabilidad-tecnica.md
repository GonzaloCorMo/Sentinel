# Trazabilidad tecnica (que usamos y donde)

## Stack y ubicaciones

- **Vue 3 + Vite**: `frontend/src/`
- **Vistas principales**: `frontend/src/views/`
- **Componentes UI**: `frontend/src/components/`
- **Stores Pinia**: `frontend/src/stores/`
- **Layouts**: `frontend/src/layouts/`
- **Motor de simulacion (FastAPI)**: `simulation/app/`
- **Supabase (infra + SQL)**: `supabase/`
- **Documentacion (MkDocs)**: `docs/`
- **Docker (OSRM + Mosquitto)**: `docker/`, `docker-compose.yml`
- **Scripts de arranque**: `up.sh`, `docker compose down`

## Mapa tecnico por modulo

### Autenticacion

- UI login: `frontend/src/views/LoginView.vue`
- Callback OAuth: `frontend/src/views/AuthCallbackView.vue`
- Cliente Supabase: `frontend/src/lib/supabase.ts`
- Proteccion de rutas: `frontend/src/router/index.ts` (guard `requiresAuth`)

### Operacion y simulacion

- Mapa de operaciones: `frontend/src/views/MapOperationsView.vue`
- Telemetria de flota: `frontend/src/views/FleetTelemetryView.vue`
- Comunicaciones: `frontend/src/views/CommsDashboardView.vue`
- Vista global isla: `frontend/src/views/IslandMonitorView.vue`
- Panel vehiculo: `frontend/src/views/VehicleHomeView.vue`
- PWA ciudadana: `frontend/src/views/MessageAlertView.vue`
- Selector region: `frontend/src/components/RegionSelector.vue`
- Selector idioma: `frontend/src/components/LanguageSelector.vue`
- Store de simulacion: `frontend/src/stores/simulation.ts`
- Store region activa: `frontend/src/stores/region.ts`
- i18n: `frontend/src/i18n/index.ts` + `frontend/src/i18n/locales/{es,en,gl}.json`
- Motor de simulacion: `simulation/app/engine.py` (2915 lineas, ~70 endpoints en main.py)
- Endpoints API: `simulation/app/main.py`
- Motores telemetria: `simulation/app/engines/` (positioning, mechanical, medical, environmental, network, composite)
- Regiones OSRM: `simulation/app/regions.py`
- Routing OSRM + fallback: `simulation/app/routing.py` + `route_nav.py`
- Scoring asignacion: `simulation/app/dispatch_scoring.py`

### IA operativa (HITL / Autonomo)

- Motor de decisiones IA: `simulation/app/ai_decision_engine.py`
- Proveedor de LLM: `simulation/app/llm_provider.py`
- Panel de propuestas UI: `frontend/src/components/dashboard/AIProposalPanel.vue`

### Chatbot RAG

- Servicio de chat: `simulation/app/chat_service.py`
- Seeder de conocimiento: `simulation/app/knowledge_seeder.py`
- Panel de chat UI: `frontend/src/components/dashboard/ChatPanel.vue`

### Comunicaciones multi-canal

- Gestor de canales: `simulation/app/channels.py` (MQTT + P2P mesh + HTTP fallback)
- Backup replay Kafka: `simulation/app/backup_replay.py`
- Telemetry writer: `simulation/app/telemetry_writer.py`
- Event writer: `simulation/app/event_writer.py`
- Panel de comms UI: `frontend/src/views/CommsDashboardView.vue`

### Aruba Pulse Kafka

- Consumer: `simulation/app/events_consumer.py`
- Schemas: `simulation/app/schemas/aruba_events.py`
- Inventory sync: `simulation/app/inventory_sync.py`

### Enrutamiento OSRM (multi-region)

- Routing client + fallback: `simulation/app/routing.py`
- Region registry: `simulation/app/regions.py`
- Stack docker: `docker-compose.yml` (4× trio fetcher/builder/routed)
- Datos persistidos: `docker/osrm-data/<region>/`

### IA y LLM

- Decision engine: `simulation/app/ai_decision_engine.py`
- LLM provider (HPE-vLLM + Ollama): `simulation/app/llm_provider.py`
- Chat RAG: `simulation/app/chat_service.py`
- Command service (NL → tool-calling): `simulation/app/command_service.py`
- Shift report (LLM flagship): `simulation/app/shift_report_service.py`
- Knowledge seeder (RAG bootstrap): `simulation/app/knowledge_seeder.py`
- Synthetic titles: `simulation/app/synthetic_titles.py`
- Fleet meta (descripcion + caps generadas por IA): `simulation/app/fleet_meta.py`

### ML

- Cliente HTTP al ml-service: `simulation/app/ml_client.py`
- Servicio ONNX: `ml-service/app.py`

## Convenciones aplicadas

- SSE como canal principal de tiempo real (no WebSocket).
- Proxy Vite para unificar `/api/*` → FastAPI.
- Supabase como fuente de persistencia y autenticacion.
- Tailwind CSS v4 con tema oscuro HPE.
- Pinia como gestor de estado global.
- asyncio para concurrencia en el motor de simulacion.

## Referencias

- [Vue 3](https://vuejs.org/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Supabase](https://supabase.com/docs)
- [Leaflet](https://leafletjs.com/)
- [ECharts](https://echarts.apache.org/)
