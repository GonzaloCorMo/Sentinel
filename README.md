# Sentinel — Digital Twin

Gemelo digital de una flota de emergencias: dashboard de operaciones con telemetría en tiempo real, PWA ciudadana para reportar incidentes, panel de vehículo para la tripulación y un motor de IA con modos HITL / autónomo. Multi-región (Santiago de Compostela · Bogotá · CDMX), routing OSRM, IA 100 % local con Ollama, chat RAG y pipeline ML de detección de anomalías.

## Stack

| Área | Tecnología |
|---|---|
| Frontend (dashboard, PWA, panel vehículo) | Vue 3 · Vite · TypeScript · Tailwind CSS v4 · Leaflet + MapLibre GL (teselas OpenFreeMap, sin API key) · Pinia · vue-i18n (es/en/gl) |
| Sistema visual | Tokens CSS con tema Dark (por defecto) / Light · Inter + JetBrains Mono autoalojadas |
| Backend simulación | FastAPI · asyncio · aiomqtt · httpx |
| Eventos externos | Fuente mock local + ingesta REST (`/api/events/ingest`, `/api/weather/ingest`) |
| Base de datos + Auth | Supabase (Postgres + pgvector + GoTrue + Storage) |
| LLM | Ollama local en Docker: chat `qwen2.5:3b` · embeddings `nomic-embed-text` (API compatible con OpenAI) |
| Routing | OSRM multi-región, un grafo por región |
| Comunicaciones | MQTT (Mosquitto) con fallback P2P + HTTP |
| Servicio ML | FastAPI · ONNX Runtime · IsolationForest (`fleet_anomaly`) |
| Documentación | VitePress |

## Arranque rápido

```bash
cp .env.example .env
python3 scripts/generate-supabase-keys.py   # pega el resultado en .env
./up.sh
```

Levanta el stack completo con Docker Compose. La primera vez tarda ~10 min (descarga los grafos OSRM y los modelos de Ollama: `qwen2.5:3b` ~1,9 GB y `nomic-embed-text` ~270 MB).

> Ollama (chat, comandos, informes y embeddings) necesita un perfil de Compose según el hardware. Con GPU NVIDIA el chat responde en unos 10 s (probado en una RTX 3050 de 4 GB):
> ```bash
> docker compose --profile gpu-nvidia up -d   # CUDA
> docker compose --profile gpu-amd    up -d   # ROCm
> docker compose --profile cpu        up -d   # sin GPU
> ```

| Servicio | URL local |
|---|---|
| Dashboard + PWA + panel vehículo | http://localhost:5173 |
| API simulación (FastAPI) · OpenAPI | http://localhost:8080 · `/openapi.yaml` |
| Documentación (VitePress) | http://localhost:3001 |
| Supabase API (Kong) · Studio | http://localhost:54321 · http://localhost:54323 |
| Mosquitto (MQTT) | mqtt://localhost:1883 |
| OSRM Santiago · Bogotá · CDMX | http://localhost:5000 · :5001 · :5002 |

## Estructura

```
├── frontend/             Vue 3 + Vite (dashboard, PWA, panel vehículo)
│   └── src/assets/       main.css (tokens + capas) · theme-scales.css (escalas dark/light)
├── simulation/           FastAPI: motor de simulación + IA observer
│   └── app/
│       ├── engine.py             Bucle de ticks, flota, POIs, emergencias, ETA dinámica
│       ├── event_source.py       Fuente de eventos externos (mock local)
│       ├── schemas/              Contratos Pydantic (telemetría, eventos externos)
│       ├── engines/              Motores de telemetría (posición/mecánica/médica/entorno/red)
│       ├── ai_decision_engine.py Propuestas HITL / autónomo
│       ├── chat_service.py       Chat RAG sobre protocolos
│       ├── routing.py            OSRM multi-región + fallback en línea recta
│       ├── channels.py           MQTT + P2P + HTTP fallback
│       └── main.py               API HTTP + SSE
├── ml-service/           ONNX Runtime: IsolationForest fleet_anomaly
├── supabase/             Migraciones SQL + config
├── docker/               Mosquitto + OSRM por región
├── docs/                 VitePress (páginas en docs/pages)
├── docker-compose.yml    Orquestación completa
├── up.sh · build.sh      Arranque y rebuild de imágenes
└── .env.example          Plantilla de configuración (único punto de configuración)
```

## Fuente de eventos externos

El motor recibe incidentes (accidentes, cortes, incendios…) y lecturas meteorológicas por dos vías con el mismo contrato (`simulation/app/schemas/external_events.py`):

- **Mock local** (`EVENT_SOURCE=mock`, por defecto): genera eventos y clima sintéticos en la región activa, así que el dashboard tiene datos en vivo sin infraestructura externa.
- **REST**: para conectar una fuente real.

```bash
curl -X POST http://localhost:8080/api/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"id":"ev-1","type":"accident","severity":"high","title":"Colisión","description":"Dos vehículos",
       "latitude":42.88,"longitude":-8.54,"radius_m":150,"started_at":"2026-10-03T10:00:00Z"}'
```

| Variable | Default | Uso |
|---|---|---|
| `EVENT_SOURCE` | `mock` | `mock` genera datos sintéticos · `off` solo ingesta REST |
| `MOCK_EVENT_INTERVAL_SEC` | `25` | Segundos medios entre eventos |
| `MOCK_WEATHER_INTERVAL_SEC` | `10` | Segundos entre lecturas meteorológicas |
| `MOCK_WEATHER_STATIONS` | `4` | Estaciones sintéticas si el mapa no tiene POIs `weather_station` |

Detalle: [docs/pages/technical/fuente-de-eventos.md](docs/pages/technical/fuente-de-eventos.md).

## IA local

Toda la IA corre en Ollama dentro del stack, sin servicios externos. Para cambiar de modelo, define en `.env` `OLLAMA_CHAT_MODEL`, `LLM_FLASH_MODEL` y `LLM_FLAGSHIP_MODEL` (p. ej. `llama3.2:3b`) y ejecuta `docker compose up -d ollama-init` para descargarlo. Detalle: [docs/pages/technical/ai-chatbot-rag.md](docs/pages/technical/ai-chatbot-rag.md).

## Sistema visual

- Tema **Dark por defecto** y **Light**, conmutables desde la cabecera (persistido en `localStorage`, sin parpadeo al cargar).
- Las familias de color de Tailwind se redirigen en `@theme` a escalas que cambian con `data-theme`: los neutros se invierten y el antiguo acento de marca pasa a monocromo.
- Color vivo **solo** para semántica: `ok` (verde), `warn` (ámbar) y `crit` (rojo) en estados de maquinaria, alertas y KPIs críticos.
- Radios de 0–4 px, separadores de 1 px y sin degradados. Inter para la interfaz; JetBrains Mono con cifras tabulares para telemetría y datos numéricos.

## Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/sim/state` | Snapshot completo del motor + IA |
| GET | `/api/sim/stream` | SSE con el estado completo (~2.5 Hz) |
| POST | `/api/sim/control` | `{action: play\|pause\|reset, speedMultiplier}` |
| POST | `/api/sim/spawn` · `/api/sim/emergency` · `/api/sim/poi` | Crear unidades, emergencias y POIs |
| POST | `/api/events/ingest` · `/api/weather/ingest` | Ingesta REST de eventos y clima |
| GET | `/api/events` · `/api/events/status` | Eventos recibidos y estado de la fuente |
| GET | `/api/weather` · `/api/weather/{id}/history` | Últimas lecturas e historial por estación |
| POST | `/api/weather/override` | Fuerza condiciones meteorológicas (what-if) |
| GET | `/api/region/summary` | Situación de la región activa (clima + eventos + flota + zonas) |
| GET/POST | `/api/ai/mode` · `/api/ai/proposals/{id}/resolve` | IA HITL / autónomo |
| POST | `/api/chat` | Chat RAG (SSE) |
| POST | `/api/ml/predict/fleet-anomaly/all` | Puntuación de anomalía de toda la flota |

Referencia completa en la documentación y en `GET /openapi.yaml`.

## Variables de entorno

Un único `.env` en la raíz (plantilla: [`.env.example`](.env.example)); Docker Compose lo inyecta en cada servicio.

| Variable | Uso |
|---|---|
| `DEFAULT_REGION` | Región inicial (`santiago` por defecto, `bogota`, `mexico`) |
| `OLLAMA_CHAT_MODEL` | Modelo de chat que descarga `ollama-init` (`qwen2.5:3b`) |
| `LLM_FLASH_BASE_URL` · `LLM_FLAGSHIP_BASE_URL` | Endpoint compatible con OpenAI (por defecto `http://ollama:11434/v1`; vale vLLM, LM Studio…) |
| `LLM_FLASH_MODEL` · `LLM_FLAGSHIP_MODEL` | Modelos de chat e informes (`qwen2.5:3b`) |
| `LLM_TIMEOUT_SEC` | Tiempo máximo por llamada al LLM (60 s) |
| `AI_OBSERVER_LLM_CONCURRENCY` | Llamadas simultáneas de la IA observadora (1) |
| `OLLAMA_EMBED_MODEL` | Modelo de embeddings local |
| `MQTT_TELEMETRY_TOPIC` | Topic MQTT de telemetría (`sentinel/telemetry`) |
| `EVENT_SOURCE` y `MOCK_*` | Fuente de eventos externos (ver arriba) |

## Desarrollo

```bash
# Frontend
cd frontend && npm ci && npm run dev        # http://localhost:5173 (proxy /api → :8080)
npm run typecheck && npm run build

# Backend
cd simulation && pip install -r requirements.txt && python run.py

# Documentación
cd docs && npm ci && npm run dev            # http://localhost:3001
```

Rebuild de imágenes tras tocar `Dockerfile`, `requirements.txt` o `package.json`: `./build.sh [servicio…]`.

## Licencia

Propietaria; consulta [LICENSE](LICENSE).
