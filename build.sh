#!/usr/bin/env bash
# Construye (o reconstruye) las imágenes Docker del proyecto.
#
# Uso:
#   ./build.sh                     # build incremental (usa cache)
#   ./build.sh --no-cache          # rebuild completo de todas
#   ./build.sh --pull              # refresca imágenes base antes de build
#   ./build.sh simulation frontend # solo servicios indicados
#   ./build.sh --no-cache docs     # rebuild sin cache solo de docs
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()  { printf "${BLUE}[INFO]${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}[ OK ]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${RESET} %s\n" "$*"; }
fail()  { printf "${RED}[FAIL]${RESET} %s\n" "$*"; }

# Servicios con Dockerfile propio (no imágenes externas)
BUILDABLE=(simulation frontend ml-service docs)

usage() {
  cat <<EOF
Uso: ./build.sh [flags] [servicios...]

Flags:
  --no-cache     Ignora cache de capas (rebuild desde cero)
  --pull         Refresca imágenes base (python, node, etc) antes de build
  --parallel     Construye servicios en paralelo (más rápido, logs entrelazados)
  -h, --help     Muestra esta ayuda

Servicios construibles: ${BUILDABLE[*]}
Sin argumentos → construye todos los servicios.
EOF
}

if ! docker compose version >/dev/null 2>&1; then
  fail "Falta el plugin docker compose."
  echo "  sudo apt install docker-compose-plugin"
  exit 1
fi
if [ ! -f .env ]; then
  fail "Falta .env. cp .env.example .env"
  exit 1
fi

FLAGS=()
SERVICES=()
PARALLEL=false

while [ $# -gt 0 ]; do
  case "$1" in
    --no-cache) FLAGS+=("--no-cache"); shift ;;
    --pull)     FLAGS+=("--pull"); shift ;;
    --parallel) PARALLEL=true; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         fail "Flag desconocido: $1"; usage; exit 1 ;;
    *)          SERVICES+=("$1"); shift ;;
  esac
done

# Validar servicios pedidos
if [ ${#SERVICES[@]} -gt 0 ]; then
  for svc in "${SERVICES[@]}"; do
    if [[ ! " ${BUILDABLE[*]} " =~ " ${svc} " ]]; then
      fail "Servicio '${svc}' no construible. Construibles: ${BUILDABLE[*]}"
      exit 1
    fi
  done
else
  SERVICES=("${BUILDABLE[@]}")
fi

# ── Info previa ──────────────────────────────────────────────────────────
info "Servicios: ${SERVICES[*]}"
if [ ${#FLAGS[@]} -gt 0 ]; then
  info "Flags: ${FLAGS[*]}"
fi
[ "$PARALLEL" = true ] && info "Modo paralelo"

# ── Build ───────────────────────────────────────────────────────────────
START=$(date +%s)
BUILD_CMD=(docker compose build)
[ "$PARALLEL" = true ] && BUILD_CMD+=(--parallel)
[ ${#FLAGS[@]} -gt 0 ] && BUILD_CMD+=("${FLAGS[@]}")
BUILD_CMD+=("${SERVICES[@]}")

info "${BUILD_CMD[*]}"
echo

# Filtrar ruido pero conservar errores de TLS / red
if ! "${BUILD_CMD[@]}" 2>&1 | grep -vE "^#[0-9]+ (CACHED|DONE)$" || true; then
  :
fi

# docker compose build devuelve 0 incluso con errores en algunas versiones;
# verificar explícitamente que las imágenes existen.
MISSING=()
for svc in "${SERVICES[@]}"; do
  IMG="hpe-ambulancia-digital-twin-${svc}:latest"
  if ! docker image inspect "$IMG" >/dev/null 2>&1; then
    MISSING+=("$svc")
  fi
done

END=$(date +%s)
ELAPSED=$((END - START))

echo
if [ ${#MISSING[@]} -gt 0 ]; then
  fail "Build falló para: ${MISSING[*]}"
  printf "\n${DIM}Revisa log arriba. Causas comunes:${RESET}\n"
  printf "  ${DIM}• TLS/certificado → red con MITM (proxy corporativo, filtro ISP)${RESET}\n"
  printf "  ${DIM}• Timeout → docker pull base image sin alcanzar Docker Hub${RESET}\n"
  printf "  ${DIM}• Sin espacio → df -h && docker system prune${RESET}\n"
  exit 1
fi

# ── Banner ──────────────────────────────────────────────────────────────
printf "\n${BOLD}${GREEN}"
echo "  ╔════════════════════════════════════════════════════════════╗"
echo "  ║   Build completado                                         ║"
echo "  ╚════════════════════════════════════════════════════════════╝"
printf "${RESET}\n"

printf "  ${BOLD}%-22s${RESET} %ss\n" "Tiempo total:" "$ELAPSED"
printf "  ${BOLD}%-22s${RESET} %s\n" "Servicios construidos:" "${SERVICES[*]}"
echo
printf "  ${BOLD}Imágenes:${RESET}\n"
for svc in "${SERVICES[@]}"; do
  IMG="hpe-ambulancia-digital-twin-${svc}:latest"
  SIZE=$(docker image inspect "$IMG" --format '{{.Size}}' 2>/dev/null | awk '{printf "%.0f MB", $1/1024/1024}')
  CREATED=$(docker image inspect "$IMG" --format '{{.Created}}' 2>/dev/null | cut -dT -f1)
  printf "    ${GREEN}•${RESET} %-16s ${DIM}%s  (%s)${RESET}\n" "$svc" "$SIZE" "$CREATED"
done

echo
printf "${DIM}Siguiente paso:${RESET}  ./up.sh   ${DIM}(levanta stack)${RESET}\n"
printf "${DIM}Ver imágenes:${RESET}    docker images | grep hpe-ambulancia\n"
printf "${DIM}Limpiar cache:${RESET}   docker builder prune\n\n"
