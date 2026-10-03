# API de simulación (HTTP + SSE)

Motor FastAPI en `simulation/app/main.py`. El frontend Vue consume el estado y los eventos casi en tiempo real.

- **Base URL**: `http://localhost:8080`.
- **OpenAPI**: `GET /openapi.yaml` (especificación completa generada automáticamente).
- **Healthcheck**: `GET /health`.

Esta página cubre el comportamiento (cadencias, esquemas, fallbacks). El listado exhaustivo de endpoints está en el OpenAPI.

## Estado global

`GET /api/sim/state` devuelve el mismo snapshot que emite el stream SSE (útil como polling de respaldo):

| Grupo | Claves |
|---|---|
| Motor | `connected`, `updatedAt`, `isSimulating`, `paused`, `motorState`, `stats` (incluye `simulationSpeed`), `osrmRouting` |
| Entidades | `ambulances`, `emergencies`, `pois`, `jams` (manuales + derivados de eventos externos), `companions`, `entityTypes` |
| Comunicaciones | `networkStatus`, `linkState`, `commsRecent`, `lastHttpIngest` |
| IA | `aiMode`, `aiProposals`, `aiLog`, `dispatchRequiresApproval` |
| Entrenamiento | `trainingMode`, `trainingRatePerMin`, `sessionId` |
| Fuente de eventos | `externalEvents`, `weatherStations`, `eventSourceStatus`, `externalEmergencyRatePerMin` |

Cada ambulancia incluye `telemetry` anidado (cinco motores: `positioning`, `mechanical`, `medical`, `environmental`, `network`) y campos operativos (`routeCoords`, `routeProgressM`, `roadSpeedLimitKmh`, `weatherFactor`, `eta_predicted_s`, `missionStatus`, `assignedEmergencyId`, `fuelLevel` / `batteryLevel` según `powertrain`, …).

## Telemetría: esquema JSON

`GET /api/sim/telemetry/schema` devuelve el JSON Schema generado desde Pydantic (`simulation/app/schemas/telemetry.py`), útil para clientes y como contrato.

Campos destacados:

- **Mecánico**: `tirePressurePsi`, `oilTempC`, `brakeFluidTempC`, `secondaryBatteryVoltageV`, `longitudinalG`, `lateralG`, `fuelLevelPct` (oscilación cosmética), `batteryLevelPct`.
- **Médico**: `etco2MmHg`, `bloodGlucoseMgDl`, `bodyTempC`, `infusionRateMlH` (FC con ruido gaussiano acotado).
- **Posicionamiento**: `roadSpeedLimitKmh` (puede ser `null`), `gpsHdop`, `gpsAccuracyM`.
- **Ambiental**: `tempC`, `humidityPct`, `windSpeedMs`, `windDirectionDeg`, `visibilityKm`, `precipMmH`, `uvIndex` (coherente con `weatherStations` cuando hay lecturas de la [fuente de eventos](fuente-de-eventos.md)).
- **Red**: `signalStrength`, `linkLatencyMs`, `packetLossPct`, `activeChannel` (`mqtt` / `p2p` / `http`).

::: warning Fuente de verdad de la energía
La fuente de verdad de combustible y batería es `amb["fuelLevel"]` / `amb["batteryLevel"]`. La telemetría mecánica solo replica esos valores con jitter cosmético; la sincronización va siempre `amb → tele`, nunca al revés.
:::

## Streams Server-Sent Events

| Endpoint | Cadencia | Payload |
|---|---|---|
| `GET /api/sim/stream` | ~2,5 Hz (0,4 s fijo) | `{ "state": { ... } }` con la forma de `/api/sim/state` |
| `GET /api/sim/comms/stream?since=<seq>` | ~3,5 Hz (0,28 s) | Mensajes del canal de comunicaciones desde el `seq` monotónico indicado |
| `POST /api/chat` | streaming de tokens | `{chunk: "..."}` por token + `{done: true}` final |

La cadencia SSE es **fija**: `speedMultiplier` solo afecta al motor interno, no al ritmo de publicación.

## Control de la simulación

- `POST /api/sim/control` con `{ "action": "play" | "pause" | "reset", "speedMultiplier": 0.1–20 }`.
- Al arrancar el proceso, `paused=true` y `motorState="PAUSED"` hasta el primer `play`. Mientras está en pausa no avanzan los ticks ni se actualiza la telemetría.

### Tiempo simulado

`dt_sim = dt_real * speed_multiplier`. Los motores de telemetría reciben `dt_sim`; el avance por la ruta es `delta_m = route_speed_ms * dt_sim`.

## Entidades

| Endpoint | Uso |
|---|---|
| `POST /api/sim/emergency` | Crear emergencia. |
| `POST /api/sim/spawn` | Crear unidad de flota (ambulancia u otro tipo). |
| `POST /api/sim/poi` | Crear POI (hospital, gasolinera, estación de carga…). |
| `POST /api/sim/jam` · `POST /api/sim/jam/point` | Crear atasco (polígono o punto). |
| `POST /api/sim/assign` | Asignar manualmente una ambulancia a una emergencia. |
| `DELETE /api/sim/{ambulance\|companion\|emergency\|poi\|jam}/{id}` | Eliminar entidad. |
| `GET/POST /api/sim/entity-types` · `DELETE /api/sim/entity-types/{type_id}` | Catálogo de tipos de unidad. |
| `GET/POST /api/sim/dispatch-config` | `{dispatchRequiresApproval}`: HITL para nuevas emergencias. |

## Generación de escenarios

- `POST /api/sim/generate-scenario`: crea hospitales, gasolineras, ambulancias y emergencias en un radio alrededor del centro indicado. Admite `extraByType` para mezclar tipos custom (p. ej. ambulancia eléctrica + helicóptero). Si están presentes `ambulance_combustion` y `ambulance_electric`, el reparto es 60/40.
- `POST /api/sim/generate-incidents`: N emergencias del catálogo realista (`emergency_catalog.py`), cada una en una calle real de la región.
- `POST /api/sim/crisis`: demos rápidas `altercation`, `mass_casualty`, `eta_exceeded`.

## Modo de entrenamiento autónomo

`POST /api/sim/training-mode` con `{enabled: true, ratePerMin: 2}`:

- Fuerza `paused=false` y `dispatchRequiresApproval=false`.
- Genera emergencias periódicas al ritmo configurado.
- Crea POIs y una flota mínima si el mapa está vacío.
- Abre una nueva `simulation_session` para particionar el dataset ML.

Detalle en [Modo simulación autónoma](../ml/modo-simulacion-autonoma.md).

## Eventos externos y meteorología

Eventos operativos y lecturas meteorológicas llegan desde el generador mock o por ingesta REST (`POST /api/events/ingest`, `POST /api/weather/ingest`). Lectura en `GET /api/events`, `GET /api/events/status`, `GET /api/weather`, `GET /api/weather/{station_id}/history`; override para demos en `POST /api/weather/override`.

`GET /api/region/summary` ofrece el resumen agregado de la región activa: la región (`region: {id, name}`), clima, eventos por tipo y severidad, KPIs de flota, ETA media, impacto meteorológico y reparto por cuadrantes (NW/NE/SW/SE) alrededor del centro de la región.

Contrato completo en [Fuente de eventos](fuente-de-eventos.md).

## Regiones y rutas OSRM

- `GET /api/regions` y `POST /api/regions/active` (`{regionId}`): listar y cambiar la región activa. Cambiar de región reinicia la simulación.
- La URL del OSRM activo se obtiene de `regions.get_active_region().osrm_url`. Si la petición falla, se reintenta; si sigue fallando, se usa una polilínea recta entre waypoints (`roadSpeedLimitKmh` queda `null`).
- `GET /api/osrm/{path}`: proxy para que el panel del vehículo pida indicaciones sin conocer la URL directa de OSRM.

Detalle en [OSRM multirregión](osrm-local-docker.md).

## Ingesta de telemetría (fallback HTTP)

`POST /api/telemetry/ingest`: fallback de nivel 3. Si MQTT y P2P fallan, los vehículos pueden enviar la telemetría por HTTP. El último payload queda expuesto como `lastHttpIngest` en el snapshot. Ver [Runbook de resiliencia](runbook-resiliencia-operativa.md).

## IA

| Endpoint | Uso |
|---|---|
| `GET /api/ai/mode` | Modo actual. |
| `POST /api/ai/mode` | `{mode: "hitl" \| "autonomous"}`. Al pasar a autónomo, las propuestas pendientes se resuelven automáticamente. |
| `GET /api/ai/proposals` | Propuestas pendientes (HITL). |
| `POST /api/ai/proposals/{proposal_id}/resolve` | `{action: "approved" \| "rejected"}`. |
| `POST /api/ai/command` | Lenguaje natural → tool-calling estructurado (`filter_units`, `focus_unit`, `set_ai_mode`, `reset_filters`, `explain`). |
| `POST /api/ai/shift-report` | Informe operativo de los últimos N minutos. |
| `GET /api/ai/shift-reports?limit` | Informes guardados. |
| `POST /api/chat` · `GET /api/chat/history` | Chatbot RAG. |
| `POST /api/knowledge/seed` | Forzar el resembrado de la base de conocimiento. |

## Pipeline ML

| Endpoint | Uso |
|---|---|
| `GET /api/ml/health` | Healthcheck de `ml-service` (ONNX). |
| `POST /api/ml/predict/fleet-anomaly` | `{ambulanceId}` → score IsolationForest. |
| `POST /api/ml/predict/fleet-anomaly/all` | Score de toda la flota. |
| `GET /api/ml/export?hours&format=csv\|parquet\|jsonl` | Exporta features para entrenamiento externo. |
| `GET /api/ml/predictions?limit` | Predicciones recientes persistidas. |
