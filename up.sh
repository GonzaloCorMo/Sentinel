#!/usr/bin/env bash
# Arranca el stack, espera lo mínimo, imprime banner de URLs.
# Si los contenedores ya están sanos el script acaba en <2s.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()  { printf "${BLUE}[INFO]${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}[ OK ]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${RESET} %s\n" "$*"; }

if ! docker compose version >/dev/null 2>&1; then
  printf "${RED}[FAIL]${RESET} Falta el plugin docker compose.\n"
  echo "  sudo apt install docker-compose-plugin"
  exit 1
fi
if [ ! -f .env ]; then
  printf "${RED}[FAIL]${RESET} Falta .env. cp .env.example .env\n"
  exit 1
fi

# ── Perfil GPU para Ollama embeddings ─────────────────────────────────────
# Auto-detecta NVIDIA / AMD ROCm en el host. Override con $GPU_PROFILE o
# $COMPOSE_PROFILES (gpu-nvidia | gpu-amd | cpu).
detect_gpu_profile() {
  if [ -n "${GPU_PROFILE:-}" ]; then echo "$GPU_PROFILE"; return; fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    echo "gpu-nvidia"; return
  fi
  if [ -e /dev/kfd ] && [ -e /dev/dri ]; then
    echo "gpu-amd"; return
  fi
  echo "cpu"
}

PROFILE="${COMPOSE_PROFILES:-$(detect_gpu_profile)}"
export COMPOSE_PROFILES="$PROFILE"
info "Perfil GPU activo: ${BOLD}${PROFILE}${RESET} (Ollama embeddings)"

# ── Arranque (compose detecta cambios; --pull never evita re-pull innecesario) ────
info "docker compose --profile ${PROFILE} up -d --pull never"
docker compose --profile "$PROFILE" up -d --pull never 2>&1 | grep -vE "^(Container|Network|Volume) .+ (Running|Created|Started)$" || true

# ── Espera: HTTP alive = cualquier respuesta (incluidos 401/404).
# Si el contenedor ya reporta healthy/running, reducimos el max agresivamente.
alive() {
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$1" 2>/dev/null || echo 000)
  # 000 = connection refused/timeout. Cualquier otro código = servicio responde.
  [ "$code" != "000" ] && [ -n "$code" ]
}

svc_ready() {
  # "Up" sin "starting" basta. Si tiene healthcheck esperamos "healthy".
  local status
  status=$(docker compose ps --format '{{.Service}}|{{.Status}}' 2>/dev/null \
    | awk -F'|' -v s="$1" '$1==s {print $2; exit}')
  [[ "$status" == Up* ]] && [[ "$status" != *starting* ]] && [[ "$status" != *unhealthy* ]]
}

wait_http() {
  local name="$1" url="$2" svc="$3" max_if_cold="${4:-900}"
  # Si el contenedor ya está up/healthy, máximo 15s. Si arranca frío, hasta max_if_cold.
  local max; svc_ready "$svc" && max=15 || max=$max_if_cold
  local i=0
  while [ $i -lt $max ]; do
    if alive "$url"; then
      ok "${name} listo"
      return 0
    fi
    sleep 2; i=$((i + 2))
  done
  warn "${name} no respondió en ${max}s — revisa 'docker compose logs ${svc}'"
  return 1
}

# Simulation requiere más tiempo la 1ª vez (OSRM + Ollama embedding init)
wait_http "API Simulación" "http://localhost:8080/api/sim/state"   simulation     900 || true
wait_http "Frontend Vue"   "http://localhost:5173/"                frontend       120 || true
wait_http "Supabase API"   "http://localhost:54321/auth/v1/health" supabase-kong   60 || true
wait_http "MkDocs"         "http://localhost:3001/"                docs            60 || true
wait_http "Inbucket"       "http://localhost:54324/"               supabase-inbucket 60 || true

# ── Banner ──────────────────────────────────────────────────────────────────
printf "\n${BOLD}${GREEN}"
echo "  ╔════════════════════════════════════════════════════════════╗"
echo "  ║   Sentinel Digital Twin — stack levantado                  ║"
echo "  ╚════════════════════════════════════════════════════════════╝"
printf "${RESET}\n"

printf "  ${BOLD}%-22s${RESET} %s\n" "Frontend:"         "http://localhost:5173/login"
printf "  ${BOLD}%-22s${RESET} %s\n" "API Simulación:"   "http://localhost:8080"
printf "  ${BOLD}%-22s${RESET} %s\n" "Documentación:"    "http://localhost:3001"
printf "  ${BOLD}%-22s${RESET} %s\n" "Supabase API:"     "http://localhost:54321"
printf "  ${BOLD}%-22s${RESET} %s\n" "Supabase Studio:"  "http://localhost:54323"
printf "  ${BOLD}%-22s${RESET} %s\n" "Inbucket (emails):" "http://localhost:54324"
printf "  ${BOLD}%-22s${RESET} %s\n" "Postgres:"         "postgresql://postgres:<POSTGRES_PASSWORD>@localhost:54322/postgres"
printf "  ${BOLD}%-22s${RESET} %s\n" "OSRM:"             "http://localhost:5000"
printf "  ${BOLD}%-22s${RESET} %s\n" "Mosquitto (MQTT):" "mqtt://localhost:1883"

printf "\n${DIM}Studio login:${RESET}          user/pass en .env (DASHBOARD_USERNAME / DASHBOARD_PASSWORD)\n"
printf "${DIM}Logs en vivo:${RESET}          docker compose logs -f [servicio]\n"
printf "${DIM}Parar stack:${RESET}           docker compose down    ${DIM}(conserva datos)${RESET}\n"
printf "${DIM}Reset completo:${RESET}        docker compose down -v ${DIM}(borra BD + modelo embeddings)${RESET}\n"
printf "${DIM}Forzar perfil GPU:${RESET}     GPU_PROFILE=gpu-amd ./up.sh ${DIM}(o gpu-nvidia | cpu)${RESET}\n\n"
