# Aruba Pulse — Integración Kafka

Aruba Pulse es la fuente de eventos del reto. El motor de simulación actúa como **consumer** (eventos + meteorología externos) y como **producer** (telemetría AsyncAPI publicada al topic del equipo).

## Broker

- **Bootstrap servers**: `10.10.48.30:9092` (env `KAFKA_BOOTSTRAP_SERVERS`).
- **Protocolo seguridad**: `PLAINTEXT` por defecto. `SASL_PLAINTEXT` si se configuran credenciales.
- **Cliente**: `aiokafka` (async/await sobre asyncio).

## Topics

| Topic | Dirección | Default env | Uso |
|---|---|---|---|
| `aruba.events` | consume | `KAFKA_EVENTS_TOPIC_EVENTS` | Incidentes, eventos meteorológicos extremos, road closures, infrastructure failures publicados por la organización. |
| `aruba.weather` | consume | `KAFKA_EVENTS_TOPIC_WEATHER` | Lecturas periódicas de las estaciones meteorológicas reales de la isla. |
| `aruba.team.tres-dias-de-gracia` | publish | `KAFKA_TOPIC` | Telemetría AsyncAPI propia (TeamEventMessageIn) cada 5–10s. |

## Consumer (`events_consumer.py`)

El consumer arranca en `lifespan()` de `main.py` como background task. Lee de los dos topics con un único `AIOKafkaConsumer`, parsea cada mensaje vía `schemas/aruba_events.py` (`parse_event`, `parse_weather`) y vuelca el resultado en:

- `engine.aruba_events` (deque limitado).
- `engine.aruba_weather[station_id]` (último snapshot por estación).
- `engine.aruba_weather_history(station_id)` (histórico corto rotativo).

El motor **respeta** los datos externos: si hay lectura Pulse fresca, el motor `environmental` la usa como base y solo añade jitter local. Esto sincroniza `engine.aruba_weather` con `telemetry.environmental.exterior` de cada ambulancia.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `KAFKA_EVENTS_ENABLED` | `true` | Activa/desactiva consumer. |
| `KAFKA_EVENTS_TOPIC_EVENTS` | `aruba.events` | Topic de eventos. |
| `KAFKA_EVENTS_TOPIC_WEATHER` | `aruba.weather` | Topic meteorológico. |
| `KAFKA_EVENTS_GROUP_ID` | `tres-dias-de-gracia-events` | Consumer group. |
| `KAFKA_EVENTS_AUTO_OFFSET_RESET` | `earliest` | `earliest` o `latest`. |
| `KAFKA_USERNAME` / `KAFKA_PASSWORD` | — | Solo si SASL. |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `PLAINTEXT` o `SASL_PLAINTEXT`. |
| `KAFKA_SASL_MECHANISM` | `PLAIN` | Mecanismo SASL si aplica. |

## Producer telemetría AsyncAPI

Background task `_kafka_telemetry_loop` publica cada `KAFKA_PUBLISH_INTERVAL_SEC` (default 5s, mín 5, máx 10) un payload `TeamEventMessageIn` por unidad activa al topic `aruba.team.tres-dias-de-gracia`.

| Variable | Default | Uso |
|---|---|---|
| `KAFKA_TELEMETRY_ENABLED` | `true` | Activa producer. |
| `KAFKA_TOPIC` | `aruba.team.tres-dias-de-gracia` | Topic destino. |
| `KAFKA_TEAM_ID` | `tres-dias-de-gracia` | Identifica al equipo en cada mensaje. |
| `KAFKA_PUBLISH_INTERVAL_SEC` | `5` | Cadencia de publicación. |

### Observabilidad

`GET /api/kafka/telemetry/stream?group_id&include_meta&from_beginning` consume el mismo topic AsyncAPI y emite SSE al frontend. Útil para validar end-to-end que lo publicado se recibe correctamente.

## Endpoints de la API que exponen los datos consumidos

| Endpoint | Devuelve |
|---|---|
| `GET /api/aruba/events?type&severity&only_active&limit` | Eventos consumidos filtrables. |
| `GET /api/aruba/events/status` | Estado del consumer (debug/health). |
| `GET /api/aruba/weather` | Última lectura por estación. |
| `GET /api/aruba/weather/{station_id}/history` | Historial corto de una estación. |
| `POST /api/aruba/weather/override` | Inyecta lectura sintética (demos / what-if con `holdSeconds`). |
| `GET /api/island/summary` | Vista global agregada (clima + eventos + flota + ETA + cuadrantes NW/NE/SW/SE). |

## Backup replay de un día histórico

Si la org publica un dataset histórico (export del topic) o quieres replayear un día previo:

```bash
curl -X POST http://10.10.48.25:8080/api/sim/backup/replay \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-04-25","showAll":false,"naturalSpeed":60}'
```

- `showAll=true` → ingest de golpe (todo el día en pocos segundos).
- `showAll=false` + `naturalSpeed` → respeta los timestamps reales pero acelerados.

Status: `GET /api/sim/backup/status`. Cancelar: `POST /api/sim/backup/stop`.

## Impacto en simulación

- **Routing**: si un evento `aruba.events` con `type=road_closed` cae cerca de la ruta de una ambulancia, la siguiente reroute la evita (el motor reevalúa polígono de bloqueo en el siguiente tick).
- **ETA dinámica**: lluvia (`precipitation_mm > 5`), viento (`wind_speed_kmh > 25`) o niebla (`visibility_km < 5`) reducen `weatherFactor` (1.0 → 0.6) y alargan `eta_predicted_s`.
- **IA observer**: eventos con severidad alta cerca de unidades activas levantan propuestas tipo `weather_hazard` o `infrastructure_alert`.
- **Vista global**: `/api/island/summary` agrega impacto en `weatherImpact.affectedMissions`.
