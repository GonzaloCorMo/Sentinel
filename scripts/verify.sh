#!/usr/bin/env bash
# Comprobaciones estáticas del repo (no necesitan el stack levantado).
#   bash scripts/verify.sh            # todo
#   bash scripts/verify.sh frontend   # solo una parte: frontend | backend | docs | compose
# Sale con código ≠ 0 si algo falla. Pensado para ejecutarse antes de cada commit.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ONLY="${1:-all}"
FAIL=0

step() { printf "\n\033[1m== %s\033[0m\n" "$1"; }
ok()   { printf "\033[32m✓ %s\033[0m\n" "$1"; }
ko()   { printf "\033[31m✗ %s\033[0m\n" "$1"; FAIL=1; }

run_frontend() {
  step "Frontend: typecheck + build"
  if [ ! -d frontend/node_modules ]; then (cd frontend && npm ci --no-audit --no-fund >/dev/null 2>&1); fi
  if (cd frontend && npx vue-tsc --noEmit -p tsconfig.json); then ok "vue-tsc"; else ko "vue-tsc"; fi
  if (cd frontend && npx vite build >/tmp/sentinel-vite.log 2>&1); then ok "vite build"; else ko "vite build"; tail -20 /tmp/sentinel-vite.log; fi
  rm -rf frontend/dist
  step "Frontend: i18n (mismas claves en es/en/gl)"
  if python - <<'PY'
import json, sys
def keys(d, p=""):
    out = set()
    for k, v in d.items():
        q = f"{p}.{k}" if p else k
        out |= keys(v, q) if isinstance(v, dict) else {q}
    return out
base = "frontend/src/i18n/locales/"
sets = {l: keys(json.load(open(base + f"{l}.json", encoding="utf-8"))) for l in ("es", "en", "gl")}
bad = False
for l in ("en", "gl"):
    missing = sets["es"] - sets[l]
    extra = sets[l] - sets["es"]
    if missing: print(f"{l}: faltan {len(missing)} claves, p. ej. {sorted(missing)[:5]}"); bad = True
    if extra: print(f"{l}: sobran {len(extra)} claves, p. ej. {sorted(extra)[:5]}"); bad = True
sys.exit(1 if bad else 0)
PY
  then ok "locales alineados"; else ko "locales desalineados"; fi
}

run_backend() {
  step "Backend: sintaxis Python"
  if python -m compileall -q simulation/app ml-service >/dev/null; then ok "compileall"; else ko "compileall"; fi
  if python -m pyflakes --version >/dev/null 2>&1; then
    if python -m pyflakes simulation/app ml-service; then ok "pyflakes"; else ko "pyflakes (avisos arriba)"; fi
  else
    echo "  (pyflakes no instalado: pip install pyflakes para análisis estático)"
  fi
  find simulation ml-service -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
}

run_docs() {
  step "Docs: VitePress build (falla con enlaces rotos)"
  if [ ! -d docs/node_modules ]; then (cd docs && npm ci --no-audit --no-fund >/dev/null 2>&1); fi
  if (cd docs && npx vitepress build >/tmp/sentinel-docs.log 2>&1); then ok "vitepress build"; else ko "vitepress build"; tail -20 /tmp/sentinel-docs.log; fi
  rm -rf docs/.vitepress/dist docs/.vitepress/cache
}

run_compose() {
  step "Docker Compose: configuración"
  if docker compose config -q 2>/dev/null; then ok "docker compose config"; else ko "docker compose config (¿falta .env?)"; fi
}

case "$ONLY" in
  frontend) run_frontend ;;
  backend) run_backend ;;
  docs) run_docs ;;
  compose) run_compose ;;
  all) run_frontend; run_backend; run_docs; run_compose ;;
  *) echo "Uso: $0 [all|frontend|backend|docs|compose]"; exit 2 ;;
esac

echo
if [ "$FAIL" -eq 0 ]; then ok "Todo correcto"; else ko "Hay fallos"; fi
exit "$FAIL"
