# OSRM (Santiago de Compostela)

El motor FastAPI calcula rutas con `fetch_route` (`simulation/app/routing.py`) contra el OSRM de Santiago de Compostela, la única región de la simulación (`simulation/app/regions.py`). Si la petición falla, reintenta y, si sigue fallando, usa una polilínea recta entre los puntos de paso.

## Región

| Región | id | Centro | Contenedor OSRM | Puerto |
|---|---|---|---|---|
| Santiago de Compostela | `santiago` | 42.8710, -8.5640 | `osrm-santiago` | 5000 |

El grafo se construye con tres contenedores de `docker-compose.yml`:

1. **`osrm-fetcher-santiago`**: no existe un extracto solo de la ciudad, así que descarga Galicia de Geofabrik y lo recorta con `osmium` al recuadro `OSRM_BBOX_SANTIAGO`. Por defecto es `-8.70,42.80,-8.40,42.97`: Santiago, Ames, Teo y el aeropuerto.
2. **`osrm-builder-santiago`**: ejecuta `osrm-extract`, `osrm-partition` y `osrm-customize` (algoritmo MLD).
3. **`osrm-santiago`**: sirve `osrm-routed --algorithm mld` en el puerto 5000.

Los datos se guardan en `docker/osrm-data/santiago/`. Del mismo extracto salen los lugares reales de `simulation/app/region_data/santiago.json` (gasolineras, bases, bomberos, policía); ver [Modelo de simulación](./modelo-de-simulacion).

## Qué consultas usa el motor

- `route`: rutas con `annotations=speed`, que da la velocidad de cada tramo para mover las unidades.
- `nearest`: ajustar a la calle más cercana todo lo que se genera.
- `table`: elegir la unidad y el hospital por tiempo real de llegada.

## Variables de entorno

| Variable | Valor por defecto | Uso |
|---|---|---|
| `DEFAULT_REGION` | `santiago` | Región al arrancar (hoy solo hay una). |
| `OSRM_URL_SANTIAGO` | `http://osrm-santiago:5000` | URL del OSRM. |
| `OSRM_BBOX_SANTIAGO` | `-8.70,42.80,-8.40,42.97` | Recorte del extracto de Galicia (`minLon,minLat,maxLon,maxLat`). |
| `OSRM_PBF_URL_SANTIAGO` | Geofabrik Galicia | Extracto que descarga el fetcher. |

## Proxy OSRM al frontend

El panel del vehículo (`VehicleHomeView`) pide las indicaciones giro a giro con `GET /api/osrm/{path}`, un proxy que reenvía al OSRM.

## Comprobación de estado

La tarea de fondo `_osrm_probe_loop` pide una ruta entre los dos puntos de prueba de la región. Lo hace cada 2 s mientras OSRM no responde y cada 15 s cuando ya está listo. El resultado aparece en `GET /api/sim/state` como `osrmRouting.ready`.

## Sin OSRM

Si el contenedor cae o tarda demasiado, `routing.fetch_route` devuelve una polilínea recta, sin velocidades por tramo, y la unidad circula a una velocidad urbana fija (unos 32 km/h).

En ese caso tampoco hay ajuste a calles: los puntos generados se quedan donde caen.

## Añadir otra región

La estructura sigue preparada para varias regiones: el skill `.claude/skills/add-region` describe los pasos. Con más de una región, la cabecera vuelve a mostrar el selector.
