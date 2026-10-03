---
name: add-region
description: Añadir, sustituir o quitar una región/mapa de simulación en Sentinel (backend, grafo OSRM en Docker, extracto OSM, hospitales reales, frontend y docs). Úsalo cuando el usuario pida trabajar sobre otra ciudad.
---

# Región nueva

1. **Extracto OSM**: busca uno que cubra la ciudad y comprueba con `curl -sIL` que responde 200.
   - Si solo hay un extracto grande (región o país), recórtalo con osmium al área metropolitana, como Santiago en `docker-compose.yml` (`osrm-fetcher-santiago`, imagen `debian:bookworm-slim`, `osmium extract -b <bbox> -F pbf …`).
   - Ojo con los nombres ambiguos: el «Santiago» de BBBike es Santiago de Chile.
2. **Docker**: añade el trío `osrm-fetcher-<id>` / `osrm-builder-<id>` / `osrm-<id>` con un puerto libre en el host, y `OSRM_URL_<ID>` en el servicio `simulation`. Añade el caso en `docker/osrm/build-graph.sh` y la variable en `.env.example`.
3. **Backend**: añade la entrada en `simulation/app/regions.py`:
   - `center` y `spawn`, junto al hospital de referencia;
   - `probe`: dos puntos en calles reales;
   - `hospitals` reales sacados del propio extracto, nada de inventarlos:
     `osmium tags-filter map.osm.pbf nwr/amenity=hospital` + `osmium export -f geojsonseq`.
4. **Frontend**: normalmente no hace falta nada (las regiones llegan por `/api/regions`). Si cambia la región por defecto, actualiza `lib/mapDefaults.ts` y `stores/region.ts`.
5. **Docs**: actualiza `docs/pages/technical/osrm-local-docker.md`, `docker/osrm/README.md`, el manual y el README.
6. **Probar**:
   - `docker compose up -d osrm-<id> simulation`;
   - `POST /api/regions/active {"regionId":"<id>"}`;
   - genera un escenario y comprueba que las rutas siguen calles (`routeCoords` con muchos puntos);
   - `bash scripts/smoke.sh`.
