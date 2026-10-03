# Sentinel — gemelo digital de una flota de emergencias

Simulación en tiempo real de unidades (ambulancias, bomberos, policía…) sobre calles reales, con dashboard de operador, panel de conductor y PWA ciudadana. La IA (chat, órdenes, propuestas de despacho e informes) corre **en local** con Ollama. Región por defecto: **Santiago de Compostela**.

Lee también el `CLAUDE.md` de la carpeta donde vayas a trabajar: `frontend/CLAUDE.md` (diseño, i18n, mapas) y `simulation/CLAUDE.md` (motor, API, IA).

## Mapa del repo

| Ruta | Qué es |
|---|---|
| `frontend/` | Vue 3 + Vite + TS + Tailwind v4 + Pinia + vue-i18n. Dashboard (`/map`, `/overview`, `/fleet`, `/comms`, `/reports`, `/config`), panel de vehículo (`/vehicle`), PWA ciudadana (`/message-alert`) |
| `simulation/app/` | FastAPI + asyncio: motor (`engine.py`), API (`main.py`), regiones (`regions.py`), fuente de eventos (`event_source.py`), IA (`ai_decision_engine.py`, `chat_service.py`, `command_service.py`, `llm_provider.py`) |
| `ml-service/` | FastAPI + ONNX Runtime: detector de anomalías `fleet_anomaly` (solo red interna, puerto 9100) |
| `supabase/migrations/` | SQL aplicado por `supabase-migrator` en orden de nombre; se registran en `public._migrations` |
| `docs/` | VitePress (páginas en `docs/pages/`), servido en :3001 |
| `docker/` | Mosquitto + scripts OSRM (`build-graph.sh`); grafos en `docker/osrm-data/` (ignorado) |
| `scripts/` | `verify.sh` (comprobaciones estáticas), `smoke.sh` (pruebas contra el stack), `generate-supabase-keys.py` |

## Comandos

```bash
# Stack completo (GPU NVIDIA; usa --profile cpu si no hay GPU)
COMPOSE_PROFILES=gpu-nvidia docker compose up -d
docker compose ps
docker compose logs -f simulation

# Comprobaciones — ejecútalas antes de dar algo por terminado
bash scripts/verify.sh            # typecheck, build, i18n, pyflakes, docs, compose
bash scripts/verify.sh frontend   # o: backend | docs | compose
bash scripts/smoke.sh             # contra el stack levantado (añade --ai para probar el chat)

# Desarrollo fuera de Docker
cd frontend && npm run dev        # :5173, proxy /api → :8080
cd docs && npm run dev            # :3001
```

URLs: frontend `localhost:5173` · API `localhost:8080` (OpenAPI en `/openapi.yaml`) · docs `:3001` · Supabase `:54321` (Studio `:54323`) · OSRM Santiago `:5000`, Bogotá `:5001`, CDMX `:5002`.

Usuarios de desarrollo: `admin@sentinel.local` (rol admin) y `vehiculo@sentinel.local` (rol vehicle). Las contraseñas están en `.env` (`DEV_ADMIN_PASSWORD`, `DEV_VEHICLE_PASSWORD`). Se crean con la API admin de GoTrue y la `SUPABASE_SERVICE_ROLE_KEY`. **No pegues secretos de `.env` en el chat ni en commits.**

## Cómo aplicar cambios en el stack

- **Python (`simulation/app`)**: el código está montado, pero uvicorn no recarga solo. Reinicia con `docker compose restart simulation`. Eso **borra el estado en memoria** (flota y emergencias); vuelve a poblar con `POST /api/sim/generate-scenario` y `POST /api/sim/control {"action":"play"}`.
- **Frontend `src/`**: hay recarga en caliente (Vite con polling, porque los bind mounts de Docker Desktop no emiten inotify). Si cambias `package.json`, `vite.config.ts` o `index.html`, ejecuta `docker compose up -d --build --no-deps frontend` y borra `/app/node_modules/.vite` dentro del contenedor.
- **Docs**: `docs/pages` y `.vitepress` están montados y recargan solos.
- **Migraciones**: añade un archivo nuevo con prefijo de fecha; nunca renombres ni edites el SQL de una migración ya aplicada (el migrador las identifica por nombre). Aplica con `docker compose up -d supabase-migrator`.
- **Dependencias Python**: `docker compose up -d --build simulation`.

## Verificación (obligatoria)

1. `bash scripts/verify.sh` en verde (o la parte que hayas tocado).
2. Si tocaste comportamiento en ejecución: `bash scripts/smoke.sh` con el stack levantado.
3. Si tocaste UI: ábrela en el navegador (Claude in Chrome) en **ambos temas** y revisa la consola. Las capturas a pantalla completa pueden fallar; usa `zoom` sobre una región o `scale` 0.6.
4. Informa de lo que **no** pudiste comprobar.

No hay tests unitarios: si añades lógica no trivial (cálculos, parsers), añade un test pequeño o una comprobación en `smoke.sh`.

## Convenciones

- **Idioma**: la interfaz está en español, con traducciones en `en` y `gl`; todo texto visible va por i18n en los tres locales (`verify.sh` comprueba que las claves coincidan). Comentarios y docs en español; identificadores en inglés.
- **Tono de la UI**: claro y operativo, como en una sala de coordinación del 112. Sin jerga técnica en pantalla (nada de OSRM, SSE, HITL, tick o ML), sin emojis decorativos y sin "AI slop". Cada página explica en una línea para qué sirve.
- **Commits**: Conventional Commits en inglés (`feat(frontend): …`, `fix(simulation): …`). Commit solo cuando el usuario lo pida o al cerrar una tarea acordada; nunca `push` sin permiso.
- **Coordenadas**: la app y la API usan `[lat, lon]`; MapLibre usa `[lon, lat]`. Convierte siempre con `toLngLat()` (`frontend/src/lib/mapEngine.ts`).
- **Seguridad**: los títulos y descripciones de emergencias y eventos vienen de fuera (PWA, ingesta REST). Escápalos (`esc()`) antes de meterlos en HTML de marcadores o popups, y pasa todo `v-html` y SVG subido por `sanitizeHtml`/`sanitizeSvg` (`lib/sanitize.ts`).
- **Sin restos del origen**: el proyecto nació en un reto técnico. No reintroduzcas nombres de marcas, regiones (Aruba), IPs o servicios externos de aquel entorno.

## Trampas conocidas del entorno (Windows + Git Bash + Docker Desktop)

- Los heredocs de bash con comillas simples y caracteres como `«` o `'` dentro de Python a veces fallan ("unexpected EOF"). Para scripts largos, escribe el `.py` en el scratchpad y ejecútalo.
- `pathlib.write_text` en Windows escribe CRLF: usa `newline="\n"`. `.gitattributes` fuerza LF en el repo; los `.sh` con CRLF rompen los contenedores.
- No dejes scripts en `%TEMP%` y los ejecutes desde allí: ahí hay un `dns.py` ajeno que hace sombra al paquete `dnspython`.
- Los `curl -d` con tildes desde Git Bash pueden mandar UTF-8 inválido (422). Para probar endpoints con texto en español, usa Python `urllib`.
- El modelo local comparte cola con la IA observadora; las respuestas tardan entre 10 y 40 s. Las llamadas de fondo están serializadas (`AI_OBSERVER_LLM_CONCURRENCY`); no lo subas sin medir.
- La sincronía OSRM tarda unos segundos tras arrancar; mientras tanto el motor traza rutas en línea recta.
