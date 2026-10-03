# Runbook de resiliencia operativa

Tres niveles de fallback para telemetría y comunicaciones entre vehículos y centralita: **MQTT → P2P mesh → HTTP**. La región OSRM activa es independiente y tiene su propio fallback (polilínea recta). La fuente de eventos externos no tiene dependencias de red: si falla, la simulación sigue con normalidad.

## 1. Caída de MQTT primario

### Síntomas

- La cabecera del dashboard marca `MQTT` en rojo o el `linkState` global pasa a `degraded`.
- `networkStatus.mqtt = false` en `/api/sim/state`.
- Aumenta la latencia de comunicaciones o desaparecen mensajes en `/api/sim/comms/stream`.

### Acciones

1. Comprobar el contenedor: `docker compose ps mosquitto` y `docker compose logs -f mosquitto`.
2. Forzar la reactivación del canal: `POST /api/sim/network` con `{mqtt: true, p2p: true, http: true}`.
3. Si el broker no responde, el motor cae automáticamente a P2P mesh (`simulation/app/channels.py`).
4. Si P2P también falla, los vehículos envían `POST /api/telemetry/ingest` (fallback HTTP de nivel 3) y el último payload queda visible como `lastHttpIngest` en el snapshot.
5. Restaurar el primario y validarlo 5 minutos antes de revertir manualmente con el toggle del dashboard.

## 2. Degradación de OSRM (región activa)

### Síntomas

- Rutas vacías o solo una polilínea recta entre origen y destino.
- El probe de fondo `_osrm_probe_loop` falla (badge `OSRM` en rojo en la cabecera; `osrmRouting` en el snapshot).
- ETA inestable o velocidad media incorrecta.

### Acciones

1. Identificar la región activa: `GET /api/regions`.
2. Revisar los logs del contenedor `osrm-<region>` (p. ej. `docker compose logs -f osrm-aruba`).
3. Si el grafo está corrupto, regenerarlo: `rm -rf docker/osrm-data/<region>/region.osrm*` y `docker compose up -d osrm-fetcher-<region> osrm-builder-<region> osrm-<region>`.
4. Como bypass temporal se puede cambiar de región: `POST /api/regions/active {regionId: "santiago"}`. Esto **reinicia** la simulación.
5. Validar el grafo directamente: `curl http://localhost:<puerto-region>/route/v1/driving/lon1,lat1;lon2,lat2`.

## 3. Fuente de eventos sin datos

### Síntomas

- `GET /api/events/status` muestra `status` distinto de `running` o `lastError` informado.
- `weatherStations` deja de actualizarse en el snapshot pero la simulación local sigue avanzando.
- No aparecen eventos nuevos en `externalEvents`.

### Acciones

1. Comprobar `EVENT_SOURCE` en `.env` (`mock` por defecto; con `off` solo entra lo que llegue por REST).
2. Logs: `docker compose logs -f simulation | grep -i "event source"`.
3. Si una fuente real envía datos por REST, verificar que recibe `202` y no `422` (payload inválido según `ExternalEvent` / `WeatherReading`).
4. Reinicio: `docker compose restart simulation`.
5. Para validar la degradación de ETA sin depender de la fuente, usar `POST /api/weather/override`.

Detalle en [Fuente de eventos](fuente-de-eventos.md).

## 4. Recuperación post-incidente

1. Confirmar broker MQTT activo y estable durante 5–10 minutos.
2. Confirmar probe OSRM en verde y ETA estable.
3. Confirmar que `GET /api/events/status` está en `running` y que llegan lecturas meteorológicas.
4. Validar la actualización del estado de flota y emergencias en el dashboard.
5. Ejecutar un despacho de prueba (`POST /api/sim/emergency`) y validar la línea temporal en `/comms`.
6. Registrar el incidente en la documentación de operación.

## Variables clave

| Variable | Default | Uso |
|---|---|---|
| `MQTT_BROKER_HOST` | `mosquitto` | Host MQTT. |
| `MQTT_BROKER_PORT` | `1883` | Puerto MQTT. |
| `MQTT_TELEMETRY_TOPIC` | `sentinel/telemetry` | Topic MQTT primario de telemetría. |
| `TELEMETRY_INGEST_URL` | `http://simulation:8080/api/telemetry/ingest` | Fallback HTTP de nivel 3. |
| `OSRM_URL_<REGION>` | `http://osrm-<region>:5000` | Override de la URL OSRM. |
| `DEFAULT_REGION` | `aruba` | Región inicial. |
| `EVENT_SOURCE` | `mock` | Fuente de eventos (`mock` / `off`). |

## Comprobaciones rápidas

```bash
# Health del backend
curl http://localhost:8080/health

# Estado de los canales
curl http://localhost:8080/api/sim/state | jq .networkStatus

# Estado de la fuente de eventos
curl http://localhost:8080/api/events/status

# Health de ml-service (vía proxy)
curl http://localhost:8080/api/ml/health

# Forzar tormenta para validar la degradación de ETA
curl -X POST http://localhost:8080/api/weather/override \
  -H "Content-Type: application/json" \
  -d '{"precipitationMm":15,"windKmh":40,"visibilityKm":2,"holdSeconds":300}'
```

## Referencias

- [MQTT Essentials](https://www.hivemq.com/mqtt-essentials/)
- [OSRM HTTP API](http://project-osrm.org/docs/v5.24.0/api/)
