# OSRM local (Santiago de Compostela)

El backend calcula rutas contra el OSRM de Santiago de Compostela. El grafo se genera con tres contenedores (descarga → compilación → servidor) y se guarda en `docker/osrm-data/santiago/`:

| Región | Servicio | Puerto | Extracto |
|---|---|---|---|
| Santiago de Compostela | `osrm-santiago` | 5000 | Geofabrik Galicia (~110 MB) recortado con osmium al área metropolitana |

## Primera vez

`./up.sh` (o `docker compose up -d`) lo arranca. `osrm-builder-santiago` compila si falta `docker/osrm-data/santiago/region.osrm`.

Para generar el grafo a mano:

```bash
bash docker/osrm/build-graph.sh santiago
```

Variables opcionales:
- `PBF_URL`: otro extracto;
- `BBOX`: recorte, en formato `minLon,minLat,maxLon,maxLat`;
- `FORCE=1`: regenerar el grafo.

En compose, los equivalentes son `OSRM_PBF_URL_SANTIAGO` y `OSRM_BBOX_SANTIAGO` en `.env`. Si cambias el recuadro, borra `docker/osrm-data/santiago/` para forzar la descarga y la compilación.

## Simulación sin Docker

```bash
export OSRM_URL_SANTIAGO=http://127.0.0.1:5000
```

Para forzar otra URL de OSRM: `OSRM_BASE_URL=http://...`. Si OSRM no está disponible, el motor usa una **polilínea recta** entre los puntos de paso.
