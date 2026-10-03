---
name: stack
description: Levantar, reiniciar, reconstruir o diagnosticar el stack Docker de Sentinel (Supabase, simulación, frontend, docs, OSRM, Ollama) y poblarlo con un escenario de prueba. Úsalo cuando haya que arrancar el entorno local, aplicar cambios a un contenedor o investigar por qué un servicio no responde.
---

# Stack local

## Arrancar
```bash
test -f .env || { cp .env.example .env && python scripts/generate-supabase-keys.py; }   # claves: pégalas en .env
COMPOSE_PROFILES=gpu-nvidia docker compose up -d    # --profile cpu si no hay GPU NVIDIA
docker compose ps
```
La primera vez tarda en descargar imágenes, el extracto de Galicia (recortado con osmium), los grafos OSRM y los modelos de Ollama (`qwen2.5:3b`, `nomic-embed-text`). Ejecuta las esperas largas en segundo plano y vigila los contenedores `*-fetcher-*`, `*-builder-*`, `supabase-migrator` y `ollama-init`: deben acabar en `exited (0)`.

## Aplicar cambios
| Cambio | Acción |
|---|---|
| Python en `simulation/app` | `docker compose restart simulation` (borra el estado en memoria) |
| `simulation/requirements.txt` o Dockerfile | `docker compose up -d --build simulation` |
| `frontend/src` | nada (recarga en caliente) |
| `frontend/package.json`, `vite.config.ts`, `index.html` | `docker compose up -d --build --no-deps frontend` y `docker compose exec -T frontend rm -rf /app/node_modules/.vite` y `docker compose restart frontend` |
| Migración nueva en `supabase/migrations` | `docker compose up -d supabase-migrator` y revisa sus logs |
| Variable en `.env` / `docker-compose.yml` | `docker compose up -d <servicio>` (recrea el contenedor) |

## Poblar para probar
```bash
curl -s -X POST localhost:8080/api/sim/generate-scenario -H "Content-Type: application/json" \
  -d '{"hospitals":3,"gasStations":2,"ambulances":5,"incidents":3,"clearExisting":true}' -o /dev/null
curl -s -X POST localhost:8080/api/sim/control -H "Content-Type: application/json" -d '{"action":"play"}' -o /dev/null
```

## Diagnóstico rápido
- `docker compose logs --tail 50 <servicio>`; en Python busca `Traceback`.
- Errores típicos:
  - **Rutas en línea recta**: `GET /api/sim/state` → `osrmRouting.ready`. Revisa el contenedor `osrm-<región>` y su builder.
  - **Chat lento o con error**: `docker compose exec -T ollama-nvidia ollama ps` (¿modelo cargado? ¿en GPU?) y `LLM_*` en `.env`.
  - **Studio unhealthy**: debe tener `HOSTNAME: 0.0.0.0`.
  - **Migrador con código 2**: ¿Postgres arrancando? Relanza `supabase-migrator`.
- **Nunca** `docker compose down -v` sin permiso explícito: borra la base de datos y los modelos descargados.
