# Trazabilidad técnica

Qué usamos y dónde vive cada pieza.

## Stack y ubicaciones

- **Vue 3 + Vite**: `frontend/src/` (vistas en `views/`, componentes en `components/`, stores Pinia en `stores/`, layouts en `layouts/`).
- **Motor de simulación (FastAPI)**: `simulation/app/`.
- **Servicio ML (ONNX)**: `ml-service/`.
- **Supabase (infra + SQL)**: `supabase/`.
- **Documentación (VitePress)**: `docs/` (páginas en `docs/pages/`, configuración en `docs/.vitepress/`).
- **Docker (OSRM + Mosquitto)**: `docker/`, `docker-compose.yml`.
- **Scripts**: `up.sh`, `build.sh`, `scripts/`.

## Mapa técnico por módulo

### Autenticación

- UI de login: `frontend/src/views/LoginView.vue`
- Callback OAuth: `frontend/src/views/AuthCallbackView.vue`
- Nueva contraseña: `frontend/src/views/AuthUpdatePasswordView.vue`
- Cliente Supabase: `frontend/src/lib/supabase.ts`
- Protección de rutas: `frontend/src/router/index.ts` (guard `requiresAuth`)

### Operación y simulación

- Mapa de operaciones: `frontend/src/views/MapOperationsView.vue`
- Situación de la región: `frontend/src/views/RegionOverviewView.vue`
- Telemetría de flota: `frontend/src/views/FleetTelemetryView.vue`
- Comunicaciones: `frontend/src/views/CommsDashboardView.vue`
- Configuración de escenario: `frontend/src/views/ScenarioConfigView.vue`
- Informes de turno: `frontend/src/views/ShiftReportsView.vue`
- Panel del vehículo: `frontend/src/views/VehicleHomeView.vue`
- PWA ciudadana: `frontend/src/views/CitizenReportView.vue`
- Store de simulación: `frontend/src/stores/simulation.ts`
- i18n: `frontend/src/i18n/` (locales `es`, `en`, `gl`)
- Motor: `simulation/app/engine.py`
- Endpoints API: `simulation/app/main.py`
- Motores de telemetría: `simulation/app/engines/` (positioning, mechanical, medical, environmental, network, composite)
- FSM y reglas de recursos: `simulation/app/ambulance_fsm.py`
- Scoring de asignación: `simulation/app/dispatch_scoring.py`

### Fuente de eventos

- Generador mock y tarea de fondo: `simulation/app/event_source.py`
- Esquemas (`ExternalEvent`, `WeatherReading`): `simulation/app/schemas/external_events.py`
- Persistencia del clima: `simulation/app/weather_db.py`

### Comunicaciones multicanal

- Gestor de canales: `simulation/app/channels.py` (MQTT + P2P mesh + fallback HTTP)
- Escritura de telemetría: `simulation/app/telemetry_writer.py`
- Escritura de eventos: `simulation/app/event_writer.py`

### Enrutamiento OSRM (multirregión)

- Cliente de routing + fallback: `simulation/app/routing.py` + `simulation/app/route_nav.py`
- Registro de regiones: `simulation/app/regions.py`
- Stack Docker: `docker-compose.yml` (4 tríos fetcher/builder/routed)
- Datos persistidos: `docker/osrm-data/<region>/`

### IA y LLM

- Motor de decisiones (HITL / autónomo): `simulation/app/ai_decision_engine.py`
- Proveedor LLM (Ollama local, API compatible con OpenAI): `simulation/app/llm_provider.py`
- Chat RAG: `simulation/app/chat_service.py`
- Comandos (lenguaje natural → tool-calling): `simulation/app/command_service.py`
- Informes de turno (LLM flagship): `simulation/app/shift_report_service.py`
- Seeder de conocimiento (RAG): `simulation/app/knowledge_seeder.py`
- Títulos sintéticos: `simulation/app/synthetic_titles.py`
- Metadatos de flota (descripción + capacidades generadas por IA): `simulation/app/fleet_meta.py`
- Paneles UI: `frontend/src/components/dashboard/AIProposalPanel.vue`, `frontend/src/components/dashboard/ChatPanel.vue`

### ML

- Cliente HTTP de ml-service: `simulation/app/ml_client.py`
- Servicio ONNX: `ml-service/app.py`

## Convenciones

- SSE como canal principal de tiempo real (no WebSocket).
- Proxy de Vite para unificar `/api/*` → FastAPI y `/sb/*` → Supabase.
- Supabase como fuente de persistencia y autenticación.
- Tailwind CSS v4 con tema oscuro.
- Pinia como gestor de estado global.
- asyncio para la concurrencia del motor.

## Referencias

- [Vue 3](https://vuejs.org/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Supabase](https://supabase.com/docs)
- [MapLibre GL JS](https://maplibre.org/) + [Stadia Maps](https://stadiamaps.com/) (estilos Alidade Smooth)
- [ECharts](https://echarts.apache.org/)
