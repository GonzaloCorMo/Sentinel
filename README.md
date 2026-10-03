# HPE Ambulancia Digital Twin

Gemelo digital de flota sanitaria con dashboard de operaciones, telemetría en tiempo real, PWA ciudadana, panel de vehículo y motor de IA con modos HITL / autónomo. Soporta multi-región (Aruba · Madrid · Bogotá · CDMX), consume eventos meteorológicos y de incidentes externos vía Kafka (Aruba Pulse), publica telemetría AsyncAPI al broker corporativo, integra LLM HPE-vLLM y dispone de pipeline ML de detección de anomalías.

## Despliegue de producción

- **Servidor**: `10.10.48.25` — todos los servicios HTTP del proyecto se exponen ahí.
- **Broker LLM HPE-vLLM**: `10.10.48.10:8000` (Gemma flash) · `10.10.48.10:8001` (Qwen flagship).
- **Broker Kafka del reto**: `10.10.48.30:9092` — topic publish `aruba.team.tres-dias-de-gracia`, topics consume `aruba.events`, `aruba.weather`.

| Servicio | URL producción | Puerto |
|---|---|---|
| Dashboard + PWA + panel vehículo | `http://10.10.48.25:5173` | 5173 |
| API simulación (FastAPI) | `http://10.10.48.25:8080` | 8080 |
| OpenAPI YAML | `http://10.10.48.25:8080/openapi.yaml` | 8080 |
| MkDocs | `http://10.10.48.25:3001` | 3001 |
| Supabase API (Kong) | `http://10.10.48.25:54321` | 54321 |
| Supabase Studio | `http://10.10.48.25:54323` | 54323 |
| Postgres | `10.10.48.25:54322` | 54322 |
| OSRM Aruba (default) | `http://10.10.48.25:5003` | 5003 |
| OSRM Madrid | `http://10.10.48.25:5000` | 5000 |
| OSRM Bogotá | `http://10.10.48.25:5001` | 5001 |
| OSRM CDMX | `http://10.10.48.25:5002` | 5002 |
| Mosquitto (MQTT) | `mqtt://10.10.48.25:1883` | 1883 |

## Stack

| Área | Tecnología |
|---|---|
| Frontend dashboard + PWA + panel vehículo | Vue 3 + Vite + TS + Tailwind + Leaflet + ECharts + Pinia + vue-i18n (es/en/gl) |
| Backend simulación | FastAPI + asyncio + aiomqtt + aiokafka + httpx |
| Base de datos + Auth | Supabase (Postgres + pgvector + GoTrue + Storage) |
| LLM chat | HPE-vLLM externo (Gemma flash · Qwen flagship), API OpenAI-compat |
| Embeddings | Ollama local (`nomic-embed-text`) — perfil compose `gpu-nvidia` / `gpu-amd` / `cpu` |
| Routing | OSRM multi-región (Aruba · Madrid · Bogotá · CDMX), 4 grafos paralelos |
| Mensajería | Mosquitto (MQTT) con fallback P2P + HTTP + replay backup |
| Eventos externos | Kafka consumer (Aruba Pulse: `aruba.events`, `aruba.weather`) + producer AsyncAPI |
| Servicio ML | FastAPI + ONNX Runtime + IsolationForest (`fleet_anomaly`) |
| Docs | MkDocs Material |

## Arranque rápido

```bash
./up.sh
```

Levanta todo el stack vía Docker Compose. Primera vez ~10 min (descarga 4 grafos OSRM + modelo embeddings). Siguientes arranques <2s.

> **GPU del host**: el embedder local Ollama necesita un perfil de Compose según el hardware:
> ```bash
> docker compose --profile gpu-nvidia up -d   # CUDA (RTX, A100, H100)
> docker compose --profile gpu-amd    up -d   # ROCm (MI300X, MI250) - HPE Cray
> docker compose --profile cpu        up -d   # sin GPU
> ```
> El chat LLM va al endpoint HPE-vLLM externo (no requiere GPU local).

Requisitos: Docker + plugin docker-compose. Detalle en [docs/pages/getting-started/docker.md](docs/pages/getting-started/docker.md).

## Estructura

```
├── frontend/             Vue 3 + Vite (dashboard, PWA, panel vehículo, i18n es/en/gl)
├── simulation/           FastAPI - motor sim + IA observer + Kafka producer/consumer
│   └── app/
│       ├── engine.py             Bucle ticks, flota, POIs, emergencias, ETA dinámica
│       ├── engines/              5 motores telemetría (positioning/mechanical/medical/environmental/network)
│       ├── ai_decision_engine.py IA observer + propuestas HITL/autónomo
│       ├── command_service.py    Comandos NL (regex fast-path + LLM tool-calling)
│       ├── chat_service.py       Chat RAG sobre protocolos
│       ├── shift_report_service.py Informe post-turno LLM
│       ├── llm_provider.py       Cliente HPE-vLLM (Gemma/Qwen)
│       ├── routing.py            OSRM multi-región + fallback recta
│       ├── regions.py            Registro 4 regiones (Aruba/Madrid/Bogotá/CDMX)
│       ├── events_consumer.py    Kafka consumer Aruba Pulse
│       ├── inventory_sync.py     Sync inventario Aruba API
│       ├── backup_replay.py      Replay de eventos Kafka históricos
│       ├── channels.py           MQTT + P2P + HTTP fallback
│       ├── dispatch_scoring.py   Scoring asignación unidad↔emergencia
│       ├── ml_client.py          Cliente HTTP al ml-service
│       └── main.py               FastAPI - 70 endpoints
├── ml-service/           ONNX Runtime - IsolationForest fleet_anomaly
├── supabase/             Migraciones SQL + config
├── docker/               Mosquitto + 4× OSRM (fetcher/builder/routed por región)
├── docs/                 MkDocs - documentación técnica + usuario
├── scripts/              Herramientas dev (generar-supabase-keys.py)
├── docker-compose.yml    Orquestación completa
├── up.sh                 Arranque + banner URLs
├── build.sh              Rebuild imágenes con validación
└── .env / .env.example   Variables raíz (único punto de configuración)
```

## Features clave

- **Multi-región OSRM** — Aruba (default), Madrid, Bogotá, Ciudad de México. 4 grafos paralelos en docker; conmutar región resetea simulación.
- **i18n completo** — Español, Inglés, Gallego (vue-i18n). Selector en cabecera, login, panel vehículo, PWA y dashboard.
- **Telemetría v2.0** — 5 motores por unidad (positioning, mechanical, medical, environmental, network) + scores derivados (vehicle health, clinical risk, link quality, cabin comfort, driving aggression).
- **Tipos de entidad dinámicos** — fleet_entity_types en Supabase con campos `powertrain` (combustion/electric/unique), `crewMin/Max`, `costPerMin`, `activationCost`. Drain energético consciente del tipo: combustion → fuelLevel + gas_station, electric → batteryLevel + charging_station.
- **Aruba Pulse integration** — consumer Kafka de `aruba.events` (incidentes externos) + `aruba.weather` (lecturas estaciones), endpoint `/api/island/summary` con vista global por cuadrantes, alertas y agregados.
- **Publicación Kafka AsyncAPI** — telemetría enviada al topic del reto cada 5-10s + endpoint SSE `/api/kafka/telemetry/stream` para observabilidad.
- **Modo backup replay** — replay de un día Kafka histórico (`/api/sim/backup/replay`) en ritmo natural o instantáneo.
- **Resiliencia comms** — MQTT primario, P2P mesh, HTTP `/api/telemetry/ingest`. Toggle por canal en dashboard.
- **ETA dinámica** — recálculo continuo con factor meteorológico, eventos próximos y velocidad real OSRM. Tracking en `engine._mission_tracking`.
- **IA observer** — anomalías + propuestas HITL con explicabilidad (summary, keyFactors, recommendedAction, confidence, urgency) + RAG sobre protocolos vía pgvector.
- **Modos IA**: `hitl` (operador aprueba) · `autonomous` (IA ejecuta sin aprobación; motor NO auto-asigna).
- **Chatbot ⌘ comando** — regex fast-path + tool-calling sobre HPE-vLLM Gemma. Filtros en vivo sobre map + fleet (`/api/ai/command`).
- **Informes post-turno** — resumen LLM con KPIs + highlights + recomendaciones (`/api/ai/shift-report`).
- **Pipeline ML** — telemetry_logs → vista `ml_telemetry_features` (51 cols) → ONNX `fleet_anomaly` → tabla `ml_predictions`.
- **Modo training autónomo** — genera emergencias periódicas y bootstrap auto de POIs/flota para alimentar dataset ML.
- **Roles**: admin (operador), vehicle (piloto), citizen (PWA).

## Endpoints API (simulación)

Base URL: `http://10.10.48.25:8080` · OpenAPI YAML: `GET /openapi.yaml`

### Base / Health

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Healthcheck (`{status: "ok"}`). |
| GET | `/openapi.yaml` | Spec OpenAPI completa en YAML. |
| GET | `/ask?q=<texto>` | Pregunta NL → command_service interpreta (regex/LLM/heurística) y responde con conteo + sample. |

### Vehículos (catálogo público)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/vehicles?type=<tipo>` | Lista pública de vehículos (filtrable por tipo). |
| GET | `/vehicles/{vehicle_id}` | Detalle de un vehículo. |
| GET | `/vehicles/status` | Resumen `{total, by_type, by_status}`. |

### Estaciones meteorológicas (catálogo público)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/weather-stations` | Lista de estaciones meteorológicas. |
| GET | `/weather-stations/{station_id}/reading` | Lectura sintética determinista (reusa motor environmental). |

### Estado simulación + streaming

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/sim/state` | Snapshot completo del motor + IA (polling fallback). |
| GET | `/api/sim/stream` | SSE estado completo a ~2.5 Hz (cadencia fija 0.4 s). |
| GET | `/api/sim/comms/stream?since=<seq>` | SSE canal comms desde seq monotónico. |
| GET | `/api/sim/telemetry/schema` | JSON Schema de telemetría v2.0. |
| GET | `/api/kafka/telemetry/stream?group_id&include_meta&from_beginning` | SSE consumiendo Kafka topic AsyncAPI. |

### Control simulación

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/sim/control` | `{action: play\|pause\|reset, speedMultiplier: 0.1-20}` |
| POST | `/api/sim/spawn` | Instancia unidad nueva en el mapa. |
| POST | `/api/sim/emergency` | Crea emergencia (dashboard o PWA). |
| POST | `/api/sim/jam` | Crea zona de atasco (polígono ≥3 vértices). |
| POST | `/api/sim/jam/point` | Crea atasco cuadrado `radiusM × radiusM`. |
| POST | `/api/sim/assign` | Asignación manual unidad ↔ emergencia. |
| POST | `/api/sim/poi` | Añade POI (hospital, gas_station, charging_station, lugar custom). |
| DELETE | `/api/sim/ambulance/{ambulance_id}` | Elimina unidad. |
| DELETE | `/api/sim/companion/{companion_id}` | Elimina companion (heli, policía). |
| DELETE | `/api/sim/emergency/{emergency_id}` | Cancela emergencia. |
| DELETE | `/api/sim/poi/{poi_id}` | Elimina POI. |
| DELETE | `/api/sim/jam/{jam_id}` | Elimina atasco. |
| POST | `/api/sim/training-mode` | Activa modo simulación autónoma para entrenar ML. |
| POST | `/api/sim/network` | Toggle MQTT / P2P / HTTP. |
| POST | `/api/sim/backup/replay` | Replay Kafka histórico de un día (`{date, showAll, naturalSpeed}`). |
| GET | `/api/sim/backup/status` | Estado replay backup. |
| POST | `/api/sim/backup/stop` | Cancela replay backup. |
| POST | `/api/sim/crisis` | Demo: `{kind: altercation\|mass_casualty\|eta_exceeded}`. |
| POST | `/api/sim/generate-scenario` | Genera escenario completo (POIs + flota + incidencias). |
| POST | `/api/sim/generate-incidents` | Genera N incidencias realistas (LLM o pool fallback). |

### Vehículo (panel piloto)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/sim/vehicle/{vehicle_id}/position` | Push posición desde el panel del vehículo (GPS o manual). |
| POST | `/api/sim/vehicle/{vehicle_id}/advance` | Avanza fase de misión manual (botón "He llegado"). |

### Fleet catalog (rol vehicle)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/fleet/types` | Catálogo in-memory de tipos (builtin + custom). |
| GET | `/api/fleet/vehicles?ownerUserId=<uid>` | Lista unidades persistidas. |
| POST | `/api/fleet/vehicles` | Registra unidad del piloto. |
| DELETE | `/api/fleet/vehicles/{vehicle_id}` | Borra unidad persistida. |

### Tipos de entidad CRUD

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/sim/entity-types` | Catálogo completo (builtin + custom + IA). |
| POST | `/api/sim/entity-types` | Registra tipo custom (IA genera desc/caps si faltan). |
| PATCH | `/api/sim/entity-types/{type_id}` | Parchea tipo custom. |
| DELETE | `/api/sim/entity-types/{type_id}` | Elimina tipo. |

### Dispatch config

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/sim/dispatch-config` | `{dispatchRequiresApproval: bool}`. |
| POST | `/api/sim/dispatch-config` | Activa/desactiva HITL para nuevas emergencias. |

### Regiones / mapas

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/regions` | Lista regiones disponibles + id activa. |
| POST | `/api/regions/active` | `{regionId}` — cambia región (resetea simulación). |
| GET | `/api/osrm/{path}` | Proxy OSRM de la región activa. |

### Aruba Pulse / Island monitor

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/aruba/events?type&severity&only_active&limit` | Eventos consumidos del topic `aruba.events`. |
| GET | `/api/aruba/events/status` | Estado consumer Kafka (debug/health). |
| GET | `/api/aruba/weather` | Última lectura por estación de `aruba.weather`. |
| GET | `/api/aruba/weather/{station_id}/history` | Historial de una estación. |
| POST | `/api/aruba/weather/override` | Inyecta lectura sintética (demos / what-if). |
| GET | `/api/island/summary` | Vista global isla (clima + eventos + flota + ETA + zonas NW/NE/SW/SE). |

### IA — HITL + Autónomo + Comandos

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/ai/proposals` | Propuestas IA pendientes (modo HITL). |
| POST | `/api/ai/proposals/{proposal_id}/resolve` | `{action: approved\|rejected}`. |
| GET | `/api/ai/mode` | Modo actual (`hitl` o `autonomous`). |
| POST | `/api/ai/mode` | Cambia modo IA. Al pasar a autónomo auto-resuelve pendientes. |
| POST | `/api/ai/command` | Interpreta comando NL con tool-calling. |
| POST | `/api/ai/shift-report` | Genera informe operativo de ventana N min. |
| GET | `/api/ai/shift-reports?limit` | Lista informes guardados. |

### Chat RAG

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/chat` | SSE stream tokens LLM con RAG sobre protocolos. |
| GET | `/api/chat/history?sessionId` | Historial completo de la sesión. |

### ML

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/ml/health` | Healthcheck ml-service. |
| POST | `/api/ml/predict/fleet-anomaly` | `{ambulanceId}` → score anomalía. |
| POST | `/api/ml/predict/fleet-anomaly/all` | Score de toda la flota (bulk). |
| GET | `/api/ml/export?hours&format=csv\|parquet\|jsonl` | Exporta features para training. |
| GET | `/api/ml/predictions?limit` | Últimas predicciones persistidas. |

### Knowledge / Telemetría ingest

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/knowledge/seed` | Re-siembra protocolos en pgvector. |
| POST | `/api/telemetry/ingest` | Fallback HTTPS si MQTT/P2P no disponibles. |

### ml-service (interno, red docker)

URL interna: `http://ml-service:9100` — no expuesto al host por defecto.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Healthcheck. |
| GET | `/models` | Modelos cargados. |
| POST | `/predict/fleet_anomaly` | Predicción individual ONNX. |
| POST | `/train/fleet_anomaly` | Entrena IsolationForest desde features. |

## Variables de entorno

Único `.env` en la raíz. Plantilla: [`.env.example`](.env.example).

```bash
cp .env.example .env
python3 scripts/generate-supabase-keys.py  # regenera JWT secrets
# pega el output en .env
```

Variables clave:

| Variable | Default | Uso |
|---|---|---|
| `DEFAULT_REGION` | `aruba` | Región inicial al arrancar (aruba/madrid/bogota/mexico). |
| `OSRM_URL_<REGION>` | `http://osrm-<region>:5000` | Override URL OSRM por región. |
| `LLM_FLASH_BASE_URL` | `http://10.10.48.10:8000/v1` | Endpoint HPE-vLLM Gemma. |
| `LLM_FLAGSHIP_BASE_URL` | `http://10.10.48.10:8001/v1` | Endpoint HPE-vLLM Qwen. |
| `LLM_FLASH_MODEL` | `google/gemma-4-31b-it` | Modelo flash. |
| `LLM_FLAGSHIP_MODEL` | `Qwen/Qwen3-235B-A22B` | Modelo flagship. |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Modelo embeddings local. |
| `KAFKA_BOOTSTRAP_SERVERS` | `10.10.48.30:9092` | Broker Kafka del reto. |
| `KAFKA_TOPIC` | `aruba.team.tres-dias-de-gracia` | Topic de publicación. |
| `KAFKA_EVENTS_TOPIC_EVENTS` | `aruba.events` | Topic consumer eventos Pulse. |
| `KAFKA_EVENTS_TOPIC_WEATHER` | `aruba.weather` | Topic consumer clima Pulse. |
| `KAFKA_TELEMETRY_ENABLED` | `true` | Activa publisher AsyncAPI. |
| `KAFKA_EVENTS_ENABLED` | `true` | Activa consumer Aruba Pulse. |
| `MQTT_BROKER_HOST` | `mosquitto` | Host MQTT. |

Docker Compose inyecta las variables a cada servicio. Frontend (Vite) las recibe vía env del contenedor — no hay `.env` en `frontend/`.

## Dev loop

```bash
./up.sh                           # arranca todo
docker compose logs -f simulation # backend
docker compose logs -f frontend   # Vite HMR
docker compose down               # para (conserva BD)
docker compose down -v            # reset total (borra BD + modelo embeddings Ollama)
```

## Rebuild imágenes

Tras tocar `Dockerfile`, `requirements.txt` o `package.json`:

```bash
./build.sh                       # todas, incremental (cache)
./build.sh simulation frontend   # solo indicadas
./build.sh --no-cache            # rebuild completo desde cero
./build.sh --pull                # refresca imágenes base antes
./build.sh --parallel            # paralelo
./build.sh && ./up.sh            # rebuild + levantar
```

Detalle + troubleshooting (incluido error TLS por interceptación MITM en red corporativa): [docs/pages/getting-started/docker.md](docs/pages/getting-started/docker.md#buildsh--script-de-construcción).

## Comandos útiles (curl)

```bash
# Healthcheck
curl http://10.10.48.25:8080/health

# Snapshot estado simulación
curl http://10.10.48.25:8080/api/sim/state | jq

# Stream SSE telemetría (Ctrl+C para parar)
curl -N http://10.10.48.25:8080/api/sim/stream

# Cambiar región a Madrid
curl -X POST http://10.10.48.25:8080/api/regions/active \
  -H "Content-Type: application/json" -d '{"regionId":"madrid"}'

# Generar escenario completo
curl -X POST http://10.10.48.25:8080/api/sim/generate-scenario \
  -H "Content-Type: application/json" \
  -d '{"hospitals":3,"gasStations":2,"ambulances":5,"incidents":4,"clearExisting":true}'

# Forzar tormenta sobre todas las estaciones (10 min)
curl -X POST http://10.10.48.25:8080/api/aruba/weather/override \
  -H "Content-Type: application/json" \
  -d '{"precipitationMm":15,"windKmh":40,"visibilityKm":2,"holdSeconds":600}'

# Vista global de la isla
curl http://10.10.48.25:8080/api/island/summary | jq

# Eventos Aruba Pulse activos
curl 'http://10.10.48.25:8080/api/aruba/events?only_active=true' | jq

# Cambiar IA a modo autónomo
curl -X POST http://10.10.48.25:8080/api/ai/mode \
  -H "Content-Type: application/json" -d '{"mode":"autonomous"}'

# Informe post-turno (60 min)
curl -X POST http://10.10.48.25:8080/api/ai/shift-report \
  -H "Content-Type: application/json" -d '{"windowMinutes":60}'

# Predicción anomalía toda la flota
curl -X POST http://10.10.48.25:8080/api/ml/predict/fleet-anomaly/all

# Replay día histórico Kafka
curl -X POST http://10.10.48.25:8080/api/sim/backup/replay \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-04-25","showAll":false,"naturalSpeed":60}'

# Modo training autónomo (genera emergencias para entrenar ML)
curl -X POST http://10.10.48.25:8080/api/sim/training-mode \
  -H "Content-Type: application/json" -d '{"enabled":true,"ratePerMin":2}'
```

## Documentación

- [Arranque con Docker](docs/pages/getting-started/docker.md)
- [Probar PWA en móvil](docs/pages/getting-started/pwa-mobile.md)
- [Levantar proyecto (dev nativo)](docs/pages/getting-started/levantar-proyecto.md)
- [API simulación HTTP + SSE](docs/pages/technical/simulation-api-http-sse.md)
- [Aruba Pulse + Kafka](docs/pages/technical/aruba-pulse-kafka.md)
- [Multi-región OSRM](docs/pages/technical/osrm-local-docker.md)
- [Resiliencia comms](docs/pages/technical/runbook-resiliencia-operativa.md)
- [IA HITL / Autónomo](docs/pages/technical/ai-hitl-autonomo.md)
- [Chatbot RAG](docs/pages/technical/ai-chatbot-rag.md)
- [Pipeline ML](docs/pages/ml/pipeline-entrenamiento.md)
- Más en `docs/pages/` — o sirve MkDocs con `./up.sh` y abre http://10.10.48.25:3001

## Licencia

Proyecto HPE CDS Tech Challenge — uso interno.
