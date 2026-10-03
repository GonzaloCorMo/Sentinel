# API de simulación (HTTP + SSE)

Motor FastAPI en `simulation/app/main.py`. **70 endpoints** organizados en grupos funcionales. El frontend Vue consume estado y eventos en tiempo casi real.

- **Base URL producción**: `http://10.10.48.25:8080`
- **OpenAPI**: `GET /openapi.yaml` (spec completa generada automáticamente)
- **Healthcheck**: `GET /health`

Para el listado completo con descripciones ver [README §Endpoints API](../../../README.md#endpoints-api-simulación). Esta página cubre los detalles de comportamiento (cadencias, schemas, fallbacks).

## Estado global

- `GET /api/sim/state` — JSON con `ambulances`, `emergencies`, `jams`, `pois`, `companions`, `stats`, `networkStatus`, `linkState`, `aiMode`, `aruba_events`, `aruba_weather`, `region`, `motorState`, `paused`, `tick`, `speedMultiplier`.
- Cada ambulancia incluye `telemetry` anidado (5 motores: `positioning`, `mechanical`, `medical`, `environmental`, `network`) y campos operativos (`routeCoords`, `routeProgressM`, `roadSpeedLimitKmh`, `weatherFactor`, `eta_predicted_s`, `missionStatus`, `assignedEmergencyId`, `fuelLevel`/`batteryLevel` según `powertrain`, …).

## Telemetría — esquema JSON

- `GET /api/sim/telemetry/schema` — JSON Schema generado desde Pydantic (`simulation/app/schemas/telemetry.py`), útil para clientes y documentación de contrato.

Campos destacados:

- **Mecánico:** `tirePressurePsi`, `oilTempC`, `brakeFluidTempC`, `secondaryBatteryVoltageV`, `longitudinalG`, `lateralG`, `fuelLevelPct` (cosmetic wobble), `batteryLevelPct`.
- **Médico:** `etco2MmHg`, `bloodGlucoseMgDl`, `bodyTempC`, `infusionRateMlH` (FC con ruido gaussiano acotado).
- **Posicionamiento:** `roadSpeedLimitKmh` (puede ser `null`), `gpsHdop`, `gpsAccuracyM`.
- **Environmental:** `tempC`, `humidityPct`, `windSpeedMs`, `windDirectionDeg`, `visibilityKm`, `precipMmH`, `uvIndex` (consistente con `aruba_weather` cuando hay lectura Pulse activa).
- **Network:** `signalStrength`, `linkLatencyMs`, `packetLossPct`, `activeChannel` (mqtt/p2p/http).

> **Importante**: la fuente de verdad de fuel/batería es `amb["fuelLevel"]` / `amb["batteryLevel"]`. La telemetría mecánica solo replica esos valores con jitter cosmético; el sync va `amb → tele`, nunca al revés (ver `_sync_energy_from_tele`).

## Streams Server-Sent Events (SSE)

| Endpoint | Cadencia | Payload |
|---|---|---|
| `GET /api/sim/stream` | ~2.5 Hz (0.4 s fijo) | `{ "state": { ... } }` con shape de `/api/sim/state` |
| `GET /api/sim/comms/stream?since=<seq>` | ~3.5 Hz (0.28 s) | Mensajes del canal comms desde el seq monotónico indicado |
| `GET /api/kafka/telemetry/stream?group_id&include_meta&from_beginning` | en demanda | Consumer Kafka del topic AsyncAPI publicado por nuestro propio motor |
| `POST /api/chat` | streaming tokens | `{chunk: "..."}` por token + `{done: true}` final |

La cadencia SSE es **fija**: el `speedMultiplier` solo afecta al motor interno (`dt_sim = dt_real * speed_multiplier`), no al ritmo de publicación.

## Control simulación

- `POST /api/sim/control` — `{ "action": "play" | "pause" | "reset", "speedMultiplier": 0.1–20 }`.
- Al arrancar el proceso, `paused=true` y `motorState="PAUSED"` hasta el primer `play`. No hay avance de ticks ni actualización de telemetría mientras `paused=true`.

## Tiempo simulado

`dt_sim = dt_real * speed_multiplier`. Los motores de telemetría reciben `dt_sim`; el avance a lo largo de la ruta es `delta_m = route_speed_ms * dt_sim`. La cadencia de SSE no se altera.

## Generación de escenarios

- `POST /api/sim/generate-scenario` — crea hospitales, gasolineras, ambulancias y emergencias en un radio del centro indicado. Soporta `extraByType` para mezclar tipos custom (ej. ambulancia eléctrica + helicóptero). Si los tipos `ambulance_combustion` + `ambulance_electric` están presentes, el ratio es 60/40.
- `POST /api/sim/generate-incidents` — N emergencias realistas (LLM HPE-vLLM si disponible, pool fallback si no).
- `POST /api/sim/crisis` — demos rápidas: `altercation`, `mass_casualty`, `eta_exceeded`.

## Modo training autónomo

`POST /api/sim/training-mode` con `{enabled: true, ratePerMin: 2}`:

- Fuerza `paused=False` y `dispatchRequiresApproval=False`.
- Genera emergencias periódicas (rate configurable).
- Bootstrap auto de POIs + flota mínima si el mapa está vacío (incluye `gas_station` para combustion y, si la implementación lo expone, `charging_station` para electric).
- Abre nueva `simulation_session` para particionar el dataset ML.

## Backup replay

Replay de un día Kafka histórico:

```bash
curl -X POST http://10.10.48.25:8080/api/sim/backup/replay \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-04-25","showAll":false,"naturalSpeed":60}'
```

- `showAll=true` → ingest instantáneo (todo el día en pocos segundos).
- `showAll=false` → ritmo natural, acelerado por `naturalSpeed`.
- `GET /api/sim/backup/status` — progreso. `POST /api/sim/backup/stop` cancela.

## Aruba Pulse / Island summary

- `GET /api/aruba/events?type&severity&only_active&limit` — eventos consumidos del topic `aruba.events`.
- `GET /api/aruba/weather` — última lectura por estación de `aruba.weather`.
- `POST /api/aruba/weather/override` — fuerza tormenta/niebla para demos (ver `WeatherOverrideRequest` en `main.py:1291`).
- `GET /api/island/summary` — vista global: agregados meteorológicos, eventos por type/severity, KPIs de flota, ETA promedio, weather impact, y bucketing por cuadrantes (NW/NE/SW/SE de Aruba).

Detalles de Kafka en [Aruba Pulse Kafka](aruba-pulse-kafka.md).

## Rutas OSRM

- Por defecto la URL del OSRM activo se calcula desde `regions.get_active_region().osrm_url`.
- Si falla la petición HTTP, se reintenta y, si sigue fallando, se usa una polilínea recta entre waypoints (`roadSpeedLimitKmh` queda `null`).
- Proxy `GET /api/osrm/{path:path}` para que el panel del vehículo pida steps sin conocer la URL OSRM directa.

## Telemetría ingest fallback

`POST /api/telemetry/ingest` — fallback Nivel 3: si MQTT y P2P fallan, los vehículos pueden empujar telemetría por HTTPS. Guarda `_last_http_ingest` con timestamp y payload.

## Modos IA — endpoints clave

| Endpoint | Uso |
|---|---|
| `GET /api/ai/mode` | Modo actual. |
| `POST /api/ai/mode` | `{mode: "hitl"\|"autonomous"}`. Al pasar a autónomo, las propuestas pendientes se auto-resuelven. |
| `GET /api/ai/proposals` | Pendientes (HITL). |
| `POST /api/ai/proposals/{proposal_id}/resolve` | `{action: "approved"\|"rejected"}`. |
| `POST /api/ai/command` | NL → tool-calling estructurado (filter_units, focus_unit, set_ai_mode, reset_filters, explain). |
| `POST /api/ai/shift-report` | Informe operativo de los últimos N min. |
| `GET /api/ai/shift-reports?limit` | Lista de informes guardados. |

## Pipeline ML

| Endpoint | Uso |
|---|---|
| `GET /api/ml/health` | Healthcheck del `ml-service` (ONNX). |
| `POST /api/ml/predict/fleet-anomaly` | `{ambulanceId}` → score IsolationForest. |
| `POST /api/ml/predict/fleet-anomaly/all` | Score de toda la flota. |
| `GET /api/ml/export?hours&format=csv\|parquet\|jsonl` | Exporta features para training externo. |
| `GET /api/ml/predictions?limit` | Predicciones recientes persistidas. |
