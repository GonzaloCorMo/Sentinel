# Runbook de resiliencia operativa

Tres niveles de fallback para telemetría y comms entre vehículos y centralita: **MQTT → P2P mesh → HTTP**. La región OSRM activa es independiente y tiene su propio fallback (polilínea recta).

## 1. Caída de MQTT primario

### Síntomas

- El header del dashboard pinta `MQTT` en rojo o el `linkState` global cae a `degraded`.
- `engine.network.mqtt = false` (visible en `/api/sim/state` → `networkStatus`).
- Aumenta latencia de comms o desaparecen mensajes en `/api/sim/comms/stream`.

### Acciones

1. Comprobar contenedor: `docker compose ps mosquitto` y `docker compose logs -f mosquitto`.
2. Forzar reactivación del canal: `POST /api/sim/network` con `{mqtt: true, p2p: true, http: true}`.
3. Si el broker no responde, el motor cae automáticamente a P2P mesh; los vehículos firman handshake entre ellos en `channels.py`.
4. Si P2P también falla, los vehículos hacen `POST /api/telemetry/ingest` (HTTP fallback Nivel 3) y el último payload queda en `_last_http_ingest` del motor.
5. Restaurar primario y validar 5 min antes de revertir manualmente con el toggle del dashboard.

## 2. Degradación OSRM (región activa)

### Síntomas

- Rutas vacías o solo polilínea recta entre origen y destino (motor reportará `osrm_fallback=true` en las trazas).
- Probe del background task `_osrm_probe_loop` falla (badge `OSRM` rojo en header).
- ETA inestable o velocidad media incorrecta.

### Acciones

1. Identificar región activa: `GET /api/regions` → campo `active`.
2. Ver logs del contenedor `osrm-<region>` (ej. `docker compose logs -f osrm-aruba`).
3. Si el grafo está corrupto, recrearlo: `rm -rf docker/osrm-data/<region>/region.osrm*` y `docker compose up -d osrm-fetcher-<region> osrm-builder-<region> osrm-<region>`.
4. Como bypass temporal puede cambiarse a otra región: `POST /api/regions/active {regionId: "madrid"}`. Esto **resetea** la simulación.
5. Validar `route/v1/driving/...` directamente con `curl http://10.10.48.25:<puerto-region>/route/v1/driving/lon1,lat1;lon2,lat2`.

## 3. Caída del consumer Aruba Pulse Kafka

### Síntomas

- `GET /api/aruba/events/status` reporta `connected: false` o `last_message_at` muy viejo.
- `engine.aruba_weather` deja de actualizarse pero la simulación local sigue avanzando.
- `engine.aruba_events` no recibe nuevos incidentes.

### Acciones

1. Verificar conectividad TCP al broker: `nc -zv 10.10.48.30 9092` desde el host del simulador.
2. Revisar credenciales SASL en `.env` (`KAFKA_USERNAME` / `KAFKA_PASSWORD`) si `KAFKA_SECURITY_PROTOCOL=SASL_PLAINTEXT`.
3. Logs: `docker compose logs -f simulation | grep -i kafka`.
4. Reinicio: `docker compose restart simulation`.
5. Como contingencia operativa, ejecutar replay del último día disponible:
   ```bash
   curl -X POST http://10.10.48.25:8080/api/sim/backup/replay \
     -H "Content-Type: application/json" \
     -d '{"date":"2026-04-25","showAll":true}'
   ```

## 4. Pérdida del producer Kafka AsyncAPI

### Síntomas

- El observador externo no ve mensajes nuevos en `aruba.team.tres-dias-de-gracia`.
- Logs del simulador muestran `kafka_producer_error` repetido.

### Acciones

1. Confirmar `KAFKA_TELEMETRY_ENABLED=true` en `.env`.
2. Revisar conectividad y credenciales (mismas que el consumer).
3. Reiniciar: `docker compose restart simulation`.
4. Validar end-to-end con `GET /api/kafka/telemetry/stream?from_beginning=true` desde otra terminal.

## 5. Recuperación post-incidente

1. Confirmar broker MQTT activo y estable durante 5–10 minutos.
2. Confirmar OSRM probe verde y ETA estable.
3. Confirmar consumer Kafka recibiendo (≥1 mensaje en `aruba.weather` cada minuto si la org está publicando).
4. Validar actualización de estado de flota/emergencias en dashboard.
5. Ejecutar un despacho de prueba (`POST /api/sim/emergency`) y validar timeline en `/comms`.
6. Registrar evento en documentación de operación.

## Variables clave

| Variable | Default | Uso |
|---|---|---|
| `MQTT_BROKER_HOST` | `mosquitto` | Host MQTT. |
| `MQTT_BROKER_PORT` | `1883` | Puerto MQTT. |
| `MQTT_TELEMETRY_TOPIC` | `hpe/sentinel/telemetry` | Topic primario. |
| `OSRM_URL_<REGION>` | `http://osrm-<region>:5000` | Override URL OSRM. |
| `DEFAULT_REGION` | `aruba` | Región inicial. |
| `KAFKA_BOOTSTRAP_SERVERS` | `10.10.48.30:9092` | Broker Aruba Pulse / AsyncAPI. |
| `TELEMETRY_INGEST_URL` | `http://simulation:8080/api/telemetry/ingest` | HTTP fallback Nivel 3. |

## Endpoints de comprobación rápida

```bash
# Health backend
curl http://10.10.48.25:8080/health

# Estado de canales
curl http://10.10.48.25:8080/api/sim/state | jq .networkStatus

# Estado consumer Aruba Pulse
curl http://10.10.48.25:8080/api/aruba/events/status

# Estado backup replay
curl http://10.10.48.25:8080/api/sim/backup/status

# Health ml-service (vía proxy)
curl http://10.10.48.25:8080/api/ml/health

# Forzar tormenta para validar degradación ETA
curl -X POST http://10.10.48.25:8080/api/aruba/weather/override \
  -H "Content-Type: application/json" \
  -d '{"precipitationMm":15,"windKmh":40,"visibilityKm":2,"holdSeconds":300}'
```

## Referencias profesionales

- [MQTT Essentials](https://www.hivemq.com/mqtt-essentials/)
- [OSRM HTTP API](http://project-osrm.org/docs/v5.24.0/api/)
- [aiokafka](https://aiokafka.readthedocs.io/)
