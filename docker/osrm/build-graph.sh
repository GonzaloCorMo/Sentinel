#!/usr/bin/env bash
# Compila grafo OSRM (extract → partition → customize) para una región concreta.
# Uso desde raíz del repo:
#   bash docker/osrm/build-graph.sh [aruba|madrid|bogota|mexico]   (default: aruba)
# Variables: PBF_URL=... (override), FORCE=1 (regenerar borrando grafo previo).
# Requisitos: docker, curl. Tiempo: 1-10 min según extracto.

set -euo pipefail

REGION="${1:-aruba}"

case "$REGION" in
  aruba)  DEFAULT_PBF="https://download.geofabrik.de/central-america/aruba-latest.osm.pbf" ;;
  madrid) DEFAULT_PBF="https://download.bbbike.org/osm/bbbike/Madrid/Madrid.osm.pbf" ;;
  bogota) DEFAULT_PBF="https://download.bbbike.org/osm/bbbike/Bogota/Bogota.osm.pbf" ;;
  mexico) DEFAULT_PBF="https://download.bbbike.org/osm/bbbike/MexicoCity/MexicoCity.osm.pbf" ;;
  *)
    echo "ERROR: región desconocida: $REGION (use aruba|madrid|bogota|mexico)"
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DATA="${REPO_ROOT}/docker/osrm-data/${REGION}"
IMAGE="${OSRM_IMAGE:-osrm/osrm-backend:latest}"
PBF_URL="${PBF_URL:-$DEFAULT_PBF}"
PBF_FILE="${PBF_FILE:-map.osm.pbf}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker no encontrado en PATH."
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl requerido para descargar el extracto."
  exit 1
fi

mkdir -p "${DATA}"

if [ -f "${DATA}/region.osrm" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "==> [${REGION}] Ya existe ${DATA}/region.osrm (FORCE=1 para regenerar)."
  exit 0
fi

if [ -f "${DATA}/region.osrm" ] && [ "${FORCE:-0}" = "1" ]; then
  echo "==> [${REGION}] FORCE=1: borrando grafo anterior"
  rm -f "${DATA}"/map.osrm* "${DATA}"/region.osrm* "${DATA}"/map.osm.pbf 2>/dev/null || true
fi

if [ ! -f "${DATA}/${PBF_FILE}" ]; then
  echo "==> [${REGION}] Descargando ${PBF_URL}"
  echo "    → ${DATA}/${PBF_FILE}"
  curl -L --fail --progress-bar -o "${DATA}/${PBF_FILE}.part" "${PBF_URL}"
  mv "${DATA}/${PBF_FILE}.part" "${DATA}/${PBF_FILE}"
else
  echo "==> [${REGION}] Reutilizando ${DATA}/${PBF_FILE}"
fi

echo "==> [${REGION}] osrm-extract…"
docker run --rm -v "${DATA}:/data" "${IMAGE}" osrm-extract -p /opt/car.lua "/data/${PBF_FILE}"

echo "==> [${REGION}] osrm-partition…"
docker run --rm -v "${DATA}:/data" "${IMAGE}" osrm-partition /data/map.osrm

echo "==> [${REGION}] osrm-customize…"
docker run --rm -v "${DATA}:/data" "${IMAGE}" osrm-customize /data/map.osrm

echo "==> [${REGION}] Renombrando map.osrm* → region.osrm*"
shopt -s nullglob
mapfiles=( "${DATA}"/map.osrm* )
if [ "${#mapfiles[@]}" -eq 0 ]; then
  echo "ERROR: no se generó map.osrm* en ${DATA}"
  exit 1
fi
for f in "${mapfiles[@]}"; do
  base="$(basename "$f")"
  new="${base/map.osrm/region.osrm}"
  mv -v "$f" "${DATA}/${new}"
done
shopt -u nullglob

if [ ! -f "${DATA}/region.osrm" ]; then
  echo "ERROR: falta ${DATA}/region.osrm tras el renombrado."
  exit 1
fi

echo "==> [${REGION}] Listo: ${DATA}/region.osrm"
echo "==> Arranca el servicio osrm-${REGION}:  docker compose up -d osrm-${REGION}"
