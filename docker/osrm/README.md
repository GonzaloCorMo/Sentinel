# OSRM local multirregión

El backend rutea contra el OSRM de la **región activa** (selector de la cabecera del dashboard). Cada región es un trío fetcher → builder → routed con su propio grafo en `docker/osrm-data/<region>/`:

| Región | Servicio | Puerto host | Extracto por defecto |
|---|---|---|---|
| Aruba (por defecto) | `osrm-aruba` | 5003 | openstreetmap.fr Aruba (~3 MB) |
| Santiago de Compostela | `osrm-santiago` | 5000 | Geofabrik Galicia (~110 MB) recortado con osmium al área metropolitana |
| Bogotá | `osrm-bogota` | 5001 | BBBike Bogota |
| Ciudad de México | `osrm-mexico` | 5002 | BBBike MexicoCity |

Cambiar de región (`POST /api/regions/active`) resetea el escenario; los cuatro contenedores siguen corriendo y solo el activo recibe consultas.

## Primera vez

`./up.sh` (o `docker compose up -d`) arranca los cuatro stacks; cada `osrm-builder-<region>` compila si falta `docker/osrm-data/<region>/region.osrm`.

Para generar a mano el grafo de una región:

```bash
bash docker/osrm/build-graph.sh aruba
bash docker/osrm/build-graph.sh santiago
bash docker/osrm/build-graph.sh bogota
bash docker/osrm/build-graph.sh mexico
```

Variables opcionales: `PBF_URL` (otro extracto), `BBOX` (recorte de Santiago, `minLon,minLat,maxLon,maxLat`) y `FORCE=1` (regenerar).

En compose, los equivalentes son `OSRM_PBF_URL_<REGION>` y `OSRM_BBOX_SANTIAGO` en `.env`. Si cambias el bbox, borra `docker/osrm-data/santiago/` para forzar la descarga y la compilación.

## Simulación sin Docker

```bash
export OSRM_URL_ARUBA=http://127.0.0.1:5003
export OSRM_URL_SANTIAGO=http://127.0.0.1:5000
export OSRM_URL_BOGOTA=http://127.0.0.1:5001
export OSRM_URL_MEXICO=http://127.0.0.1:5002
export DEFAULT_REGION=aruba
```

Para forzar un OSRM único: `OSRM_BASE_URL=http://...`. Si OSRM no está disponible, el motor usa una **polilínea recta** entre waypoints.
