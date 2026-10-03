# OSRM multirregión (Santiago · Bogotá · CDMX)

El motor FastAPI calcula rutas con `fetch_route` (`simulation/app/routing.py`): consulta el OSRM de la **región activa**, gestionada por `simulation/app/regions.py`. Si la petición HTTP falla, el motor reintenta y, si sigue fallando, usa una polilínea recta entre waypoints como fallback.

## Regiones soportadas

| Región | id | Centro | OSRM container | Puerto host |
|---|---|---|---|---|
| Santiago de Compostela | `santiago` (por defecto) | 42.8710, -8.5640 | `osrm-santiago` | 5000 |
| Bogotá | `bogota` | 4.6286, -74.0653 | `osrm-bogota` | 5001 |
| Ciudad de México | `mexico` | 19.4326, -99.1332 | `osrm-mexico` | 5002 |

Cada región es un trío de contenedores en `docker-compose.yml`:

1. **`osrm-fetcher-<region>`** — descarga el `.osm.pbf` (URL configurable vía `OSRM_PBF_URL_<REGION>`). Para Santiago no existe extracto de ciudad: se descarga Galicia (Geofabrik) y se recorta con `osmium` al bbox `OSRM_BBOX_SANTIAGO` (por defecto `-8.70,42.80,-8.40,42.97`: Santiago, Ames, Teo y el aeropuerto).
2. **`osrm-builder-<region>`** — corre `osrm-extract` + `osrm-partition` + `osrm-customize` (algoritmo MLD).
3. **`osrm-<region>`** — sirve `osrm-routed --algorithm mld` en el puerto 5000 interno.

Los datos persisten en `docker/osrm-data/<region>/`. Solo el contenedor de la región activa recibe queries; los otros dos quedan idle (~250 MB RAM cada uno).

## Cambio de región en runtime

```bash
# Listar regiones
curl http://localhost:8080/api/regions

# Cambiar a Santiago de Compostela
curl -X POST http://localhost:8080/api/regions/active \
  -H "Content-Type: application/json" -d '{"regionId":"santiago"}'
```

Conmutar región resetea la simulación (`engine.reset_simulation`) — las coordenadas in-memory de POIs, flota y atascos no son válidas en el grafo de la nueva región.

En Santiago de Compostela, el generador de escenarios coloca los hospitales reales de la ciudad: CHUS, HM La Esperanza y HM Rosaleda.

El **frontend** llama al endpoint `POST /api/regions/active` desde `RegionSelector.vue` y centra el mapa en `region.center` automáticamente.

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `DEFAULT_REGION` | `santiago` | Región inicial al arrancar el motor. |
| `OSRM_URL_SANTIAGO` | `http://osrm-santiago:5000` | Override URL Santiago de Compostela. |
| `OSRM_BBOX_SANTIAGO` | `-8.70,42.80,-8.40,42.97` | Recorte del extracto de Galicia (`minLon,minLat,maxLon,maxLat`). |
| `OSRM_URL_BOGOTA` | `http://osrm-bogota:5000` | Override URL Bogotá. |
| `OSRM_URL_MEXICO` | `http://osrm-mexico:5000` | Override URL CDMX. |
| `OSRM_PBF_URL_<REGION>` | URL OSM extract | Cambia el PBF descargado por el fetcher. |

## Proxy OSRM al frontend

El panel del vehículo (`VehicleHomeView`) pide steps turn-by-turn vía `GET /api/osrm/{path}`, un proxy que reenvía a la URL del OSRM activo. Al cambiar de región, el destino del proxy cambia automáticamente sin reiniciar nada.

## Probe / health

El motor publica un task de fondo `_osrm_probe_loop` que ataca el endpoint `route/v1/driving/<probe_lon_a>,<probe_lat_a>;<probe_lon_b>,<probe_lat_b>` cada 30s para validar el grafo activo. El estado se refleja en el header del dashboard como badge `OSRM`.

## Fallback offline

Si el contenedor OSRM cae o tarda demasiado, `routing.fetch_route` devuelve la polilínea recta (Haversine) y `roadSpeedLimitKmh` queda `null`. La simulación sigue avanzando con velocidad nominal del tipo de entidad — útil para demos sin red.
