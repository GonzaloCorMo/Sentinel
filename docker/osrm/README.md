# OSRM local multi-región para rutas por calles

El backend rutea contra el OSRM de la **región activa** (selector en el header del dashboard). Cada región es un trío fetcher → builder → routed con su propio grafo en `docker/osrm-data/<region>/`:

| Región | Servicio | Puerto host | PBF default |
|---|---|---|---|
| Aruba (default) | `osrm-aruba` | 5000 | Geofabrik aruba (~3 MB) |
| Madrid | `osrm-madrid` | 5001 | BBBike Madrid (~75 MB) |
| Bogotá | `osrm-bogota` | 5002 | BBBike Bogota (~50 MB) |
| Ciudad de México | `osrm-mexico` | 5003 | BBBike MexicoCity (~80 MB) |

El selector cambia la región activa vía `POST /api/regions/active` — los 4 contenedores siguen corriendo idle (~1 GB RAM total), solo el activo recibe queries. Cambiar región resetea el escenario.

## 0. Primera vez: `./start.sh` o script solo

`./start.sh` arranca los 4 stacks; cada `osrm-builder-<region>` compila si falta `docker/osrm-data/<region>/region.osrm`.

Para generar manualmente el grafo de una región:

```bash
bash docker/osrm/build-graph.sh aruba    # default
bash docker/osrm/build-graph.sh madrid
bash docker/osrm/build-graph.sh bogota
bash docker/osrm/build-graph.sh mexico
```

Variables opcionales: `PBF_URL` (sobrescribe extracto), `FORCE=1` (regenerar borrando el grafo actual).

## 1. Descargar un extracto `.osm.pbf` (manual)

URLs default por región:
- Aruba: `https://download.geofabrik.de/central-america/aruba-latest.osm.pbf`
- Madrid: `https://download.bbbike.org/osm/bbbike/Madrid/Madrid.osm.pbf`
- Bogotá: `https://download.bbbike.org/osm/bbbike/Bogota/Bogota.osm.pbf`
- CDMX: `https://download.bbbike.org/osm/bbbike/MexicoCity/MexicoCity.osm.pbf`

Override con `OSRM_PBF_URL_<REGION>` en `.env`.

## 2. Procesar con la imagen OSRM (MLD)

Desde la raíz del repo, con `docker` y el `.pbf` en `./docker/osrm-data/<region>/map.osm.pbf`:

```bash
REGION=madrid
cd docker/osrm-data/${REGION}

docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-extract -p /opt/car.lua /data/map.osm.pbf
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-partition /data/map.osrm
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-customize /data/map.osrm

for f in map.osrm*; do mv "$f" "region.${f#map.}"; done
```

## 3. Arrancar el servicio

Desde la raíz del repositorio:

```bash
docker compose up -d osrm-aruba osrm-madrid osrm-bogota osrm-mexico
```

(`./start.sh` arranca los 4. El frontend conmuta entre ellos vía el selector.)

## 4. Simulación Python

Sin docker compose, exporta las URLs por región:

```bash
export OSRM_URL_ARUBA=http://127.0.0.1:5000
export OSRM_URL_MADRID=http://127.0.0.1:5001
export OSRM_URL_BOGOTA=http://127.0.0.1:5002
export OSRM_URL_MEXICO=http://127.0.0.1:5003
export DEFAULT_REGION=aruba
```

Para forzar un OSRM único (modo legacy): `OSRM_BASE_URL=http://...`. Si OSRM no está disponible, el motor usa **polilínea recta** entre waypoints.
