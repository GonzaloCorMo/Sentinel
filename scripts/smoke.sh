#!/usr/bin/env bash
# Pruebas de humo contra el stack en marcha (docker compose up).
#   bash scripts/smoke.sh          # API, rutas, eventos, ML, docs, frontend
#   bash scripts/smoke.sh --ai     # además prueba el chat con el modelo local (~10-40 s)
set -uo pipefail

API="${API:-http://localhost:8080}"
FAIL=0
ok() { printf "\033[32m✓ %s\033[0m\n" "$1"; }
ko() { printf "\033[31m✗ %s\033[0m\n" "$1"; FAIL=1; }

check() { # nombre url [patrón esperado]
  local body
  body=$(curl -s --max-time 15 "$2") || { ko "$1 (sin respuesta)"; return; }
  if [ -n "${3:-}" ] && ! grep -q "$3" <<<"$body"; then ko "$1 (respuesta inesperada)"; else ok "$1"; fi
}

check "API /health" "$API/health" '"ok"'
check "Región activa" "$API/api/regions" '"active"'
check "OSRM listo" "$API/api/sim/state" '"ready": *true\|"ready":true'
check "Fuente de eventos" "$API/api/events/status" '"status"'
check "Resumen regional" "$API/api/region/summary" '"region"'
check "Servicio ML" "$API/api/ml/health" '"ok"'
check "Frontend :5173" "http://localhost:5173/" '<div id="app">'
check "Docs :3001" "http://localhost:3001/" '<html'

# Ingesta REST: un evento válido (202) y uno inválido (422).
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/events/ingest" -H "Content-Type: application/json" \
  -d "{\"id\":\"smoke-$(date +%s)\",\"type\":\"accident\",\"severity\":\"low\",\"title\":\"Prueba\",\"description\":\"smoke\",\"latitude\":42.87,\"longitude\":-8.55,\"started_at\":\"2026-01-01T00:00:00Z\"}")
[ "$code" = "202" ] && ok "Ingesta de evento (202)" || ko "Ingesta de evento (HTTP $code)"
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/events/ingest" -H "Content-Type: application/json" -d '{"id":"x"}')
[ "$code" = "422" ] && ok "Validación de evento (422)" || ko "Validación de evento (HTTP $code)"

if [ "${1:-}" = "--ai" ]; then
  out=$(curl -s -N --max-time 120 -X POST "$API/api/chat" -H "Content-Type: application/json" \
    -d '{"message":"hola","sessionId":"00000000-0000-4000-8000-00000000beef"}')
  if grep -q '"chunk"' <<<"$out" && ! grep -q "error al generar" <<<"$out"; then ok "Chat con el modelo local"; else ko "Chat con el modelo local"; fi
fi

echo
[ "$FAIL" -eq 0 ] && ok "Stack correcto" || ko "Hay fallos"
exit "$FAIL"
