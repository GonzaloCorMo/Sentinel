# Fuente de eventos externos

El motor de simulación recibe dos tipos de datos externos: **eventos operativos** georreferenciados (incidentes, cortes, emergencias) y **lecturas meteorológicas** por estación. Ambos comparten un único contrato y pueden llegar por dos vías:

- **Mock local** (por defecto): un generador sintético integrado en el backend que mantiene el dashboard con datos en tiempo real sin infraestructura externa.
- **Ingesta REST**: endpoints `POST` con validación estricta para conectar una fuente real. Funcionan aunque el mock esté apagado.

```
                ┌───────────────────────────┐
 EVENT_SOURCE=  │ event_source.py (mock)    │──┐
   mock         └───────────────────────────┘  │   ingest_external_event()
                                               ├─> ingest_weather_reading()  ──> Motor ──> SSE /api/sim/stream ──> Dashboard
 Fuente real ── POST /api/events/ingest ───────┤                                 │
               POST /api/weather/ingest ───────┘                                 └─> Supabase weather_readings
```

## Módulos

| Pieza | Ubicación |
|---|---|
| Generador mock + tarea de fondo | `simulation/app/event_source.py` (`run_event_source`, arrancado en el `lifespan` de `main.py`) |
| Esquemas Pydantic | `simulation/app/schemas/external_events.py` (`ExternalEvent`, `WeatherReading`) |
| Volcado al motor | `engine.ingest_external_event()` y `engine.ingest_weather_reading()` en `simulation/app/engine.py` |
| Persistencia del clima | `simulation/app/weather_db.py` → tabla `weather_readings` |

## Generador mock

Con `EVENT_SOURCE=mock` el backend emite periódicamente:

- **Eventos externos sintéticos** situados dentro del área de la región activa (alrededor de su centro). Tipos: `storm`, `fire`, `flood`, `accident`, `lane_closure`, `power_outage`, `medical_emergency`, `hazmat_spill`, `construction`, `public_event`. Severidades: `low`, `medium`, `high`, `critical`. Cada evento se autorresuelve pasados unos minutos para no saturar el mapa.
- **Lecturas meteorológicas** de N estaciones con deriva suave (paseo aleatorio acotado por variable) y chubascos ocasionales que suben la precipitación y bajan la visibilidad. Si el mapa tiene POIs `weather_station`, se usan sus ids; si no, se crean estaciones sintéticas (`mock-ws-1` … `mock-ws-N`).

Todo lo que genera el mock se valida contra los mismos esquemas que la ingesta REST.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `EVENT_SOURCE` | `mock` | `mock` activa el generador; `off` lo desactiva y deja solo la ingesta REST. |
| `MOCK_EVENT_INTERVAL_SEC` | `25` | Segundos medios entre eventos sintéticos (con jitter). |
| `MOCK_WEATHER_INTERVAL_SEC` | `10` | Segundos entre lecturas meteorológicas por estación. |
| `MOCK_WEATHER_STATIONS` | `4` | Estaciones sintéticas cuando no hay POIs `weather_station` en el mapa. |

## Impacto en la simulación

El motor trata igual un evento del mock que uno recibido por REST:

- **Atascos**: los tipos viales (`accident`, `lane_closure`, `construction`, `hazmat_spill`) con `radius_m` se materializan como un polígono de bloqueo en `external_jams`; el routing OSRM lo evita en el siguiente cálculo de ruta. Al resolverse el evento, el bloqueo desaparece.
- **Emergencias despachables**: los tipos de emergencia (`medical_emergency`, `fire`, `accident`, `hazmat_spill`, `flood`) generan una emergencia en el motor que pasa por el flujo **HITL** (o se despacha directamente en modo autónomo). Los eventos se deduplican por `id` y se ignoran si son demasiado antiguos.
- **Meteorología**: la última lectura por estación alimenta el motor `environmental` de cada ambulancia, el factor meteorológico de la ETA (`weatherFactor`) y el scoring de asignación. Lluvia intensa, viento fuerte o baja visibilidad degradan la ETA prevista.
- **IA observer**: eventos de severidad alta cerca de unidades activas pueden levantar propuestas.
- **Situación de la región**: `GET /api/region/summary` agrega clima, eventos e impacto meteorológico en las misiones.

## Ingesta REST

Validación Pydantic estricta (`extra="forbid"`): un payload con campos desconocidos, tipos incorrectos o valores fuera de rango se rechaza con `422` y no llega al motor. Ambos endpoints responden `202 Accepted` con `{"ok": true, "id": "..."}`.

### `POST /api/events/ingest`

Body = `ExternalEvent`:

| Campo | Tipo | Notas |
|---|---|---|
| `id` | string | Identificador único; reenviar el mismo `id` actualiza el evento (p. ej. para resolverlo). |
| `type` | enum | `storm` · `fire` · `flood` · `accident` · `lane_closure` · `power_outage` · `medical_emergency` · `hazmat_spill` · `construction` · `public_event` |
| `severity` | enum | `low` · `medium` · `high` · `critical` |
| `title` | string | Máx. 200 caracteres. |
| `description` | string | |
| `latitude` / `longitude` | float | Grados WGS84. |
| `radius_m` | float? | Radio de afectación; necesario para que un evento vial cree atasco. |
| `road_id` | string? | Vía afectada, si se conoce. |
| `started_at` | string | ISO 8601. |
| `resolved_at` | string? | ISO 8601; si viene informado el evento se considera resuelto. |
| `geometry` | `float[][]`? | Geometría opcional como lista de pares de coordenadas. |

```bash
curl -X POST http://localhost:8080/api/events/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "id": "evt-2026-0001",
    "type": "accident",
    "severity": "high",
    "title": "Colisión en la autopista",
    "description": "Dos vehículos implicados, carril derecho bloqueado.",
    "latitude": 42.8805,
    "longitude": -8.5457,
    "radius_m": 150,
    "started_at": "2026-10-03T09:15:00Z"
  }'
```

Para resolverlo basta con reenviar el mismo `id` con `resolved_at` informado.

### `POST /api/weather/ingest`

Body = `WeatherReading`:

| Campo | Tipo | Rango |
|---|---|---|
| `id` | string | |
| `station_id` | string | |
| `timestamp` | string | ISO 8601 |
| `temperature_c` | float | −50 … 60 |
| `humidity_pct` | float | 0 … 100 |
| `wind_speed_kmh` | float | 0 … 500 |
| `wind_direction_deg` | float | 0 … 360 |
| `pressure_hpa` | float | 800 … 1100 |
| `precipitation_mm` | float | ≥ 0 |
| `visibility_km` | float | 0 … 100 |
| `uv_index` | float | 0 … 15 |

```bash
curl -X POST http://localhost:8080/api/weather/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "id": "ws-01-20261003T0915",
    "station_id": "ws-01",
    "timestamp": "2026-10-03T09:15:00Z",
    "temperature_c": 29.4,
    "humidity_pct": 72,
    "wind_speed_kmh": 18.5,
    "wind_direction_deg": 85,
    "pressure_hpa": 1012.3,
    "precipitation_mm": 0,
    "visibility_km": 11,
    "uv_index": 8
  }'
```

Las lecturas se persisten en la tabla `weather_readings` de Supabase.

## Endpoints de lectura

| Endpoint | Devuelve |
|---|---|
| `GET /api/events?type&severity&only_active&limit` | Últimos eventos recibidos (mock o REST), filtrables. |
| `GET /api/events/status` | Estado de la fuente (`enabled`, `source`, `status`, `lastError`) y contadores de ingesta. |
| `GET /api/weather` | Última lectura por estación. |
| `GET /api/weather/{station_id}/history` | Historial corto (últimas 20 lecturas) de una estación. |
| `POST /api/weather/override` | Inyecta una lectura sintética en todas las estaciones o en `stationIds` (demos / what-if). |

Ejemplo de override para forzar una tormenta durante 5 minutos:

```bash
curl -X POST http://localhost:8080/api/weather/override \
  -H "Content-Type: application/json" \
  -d '{"precipitationMm":15,"windKmh":40,"visibilityKm":2,"holdSeconds":300}'
```

Mientras dura `holdSeconds` (600 por defecto), las lecturas entrantes se guardan en el historial pero no sustituyen los valores forzados.

## Tiempo real en el dashboard

El dashboard no consulta estos endpoints en bucle: lo recibe todo por SSE en `GET /api/sim/stream`. Claves del snapshot relacionadas:

| Clave | Contenido |
|---|---|
| `externalEvents` | Eventos activos (no resueltos). |
| `weatherStations` | Última lectura por estación. |
| `eventSourceStatus` | Mismo contenido que `GET /api/events/status`. |
| `externalEmergencyRatePerMin` | Ritmo reciente de emergencias generadas a partir de eventos externos. |
