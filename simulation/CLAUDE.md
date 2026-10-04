# Motor de simulación (FastAPI + asyncio)

## Piezas

- `app/main.py` — API HTTP + SSE y `lifespan`. Tareas de fondo: `engine.run_loop`, sonda OSRM, `run_event_source` (eventos mock) y `ai_engine.observe_loop`.
- `app/engine.py` — estado en memoria (unidades, emergencias, POIs, atascos, clima, eventos externos), bucle de ticks, despacho, rutas y ETA. `get_state_payload()` es el contrato del snapshot que consume el frontend; si cambias claves, actualiza `frontend/src/types/simulation.ts`.
- `app/regions.py` — región de la simulación (solo `santiago`; la estructura admite añadir más): centro, punto de salida, URL OSRM y hospitales reales (`hospitals`) que coloca el generador de escenarios. Para añadir una región, añade aquí su entrada, los servicios `osrm-*` en `docker-compose.yml` y su caso en `docker/osrm/build-graph.sh`.
- `app/event_source.py` — fuente de eventos externos: mock local (`EVENT_SOURCE=mock|off`) e ingesta REST (`POST /api/events/ingest`, `POST /api/weather/ingest`). El contrato está en `app/schemas/external_events.py` (Pydantic, `extra="forbid"`).
- `app/ai_decision_engine.py` — IA observadora: crea propuestas (modo con aprobación o autónomo), con RAG sobre `protocols` en pgvector y explicación generada por el LLM. Sus llamadas al LLM pasan por un semáforo (`AI_OBSERVER_LLM_CONCURRENCY`).
- `app/llm_provider.py` — cliente OpenAI-compatible (por defecto Ollama `http://ollama:11434/v1`, modelo `qwen2.5:3b`) y embeddings (`nomic-embed-text`, rellenados hasta 1536 dimensiones). Tiempos de espera: `LLM_TIMEOUT_SEC` (60 s) y 5 s de conexión.
- `app/chat_service.py` (chat RAG en streaming) · `app/command_service.py` (órdenes: vía rápida con expresiones regulares y, si no, tool-calling) · `app/shift_report_service.py` (informes).
- `app/placement.py` — colocación sobre calles reales (OSRM `/nearest`) y cortes de tráfico como tramos de calle. **Todo punto generado pasa por aquí**: nunca uses posiciones aleatorias en grados.
- `app/emergency_catalog.py` — tipos de llamada con frecuencia, gravedad, tiempo en el lugar, probabilidad de traslado y demanda por hora.
- `app/region_data/<región>.json` — lugares reales de OpenStreetMap (gasolineras, bases, bomberos, policía), leídos con `regions.region_places()`.
- `app/weather_source.py` — tiempo real de MeteoGalicia (estaciones de la región como POIs `mg-<id>`, lectura cada 10 min); `WEATHER_SOURCE=mock` para sintético.
- `app/supabase_client.py` — si no hay Supabase, el motor sigue funcionando sin persistencia.

## Reglas

- Todo lo que llega de fuera (REST, PWA, LLM) se valida con Pydantic o se trata como no confiable. Los textos que el frontend pinta como HTML los escapa el frontend, pero no confíes en ello para nada que vaya a SQL o al shell.
- No bloquees el event loop: E/S siempre `async` (httpx, SDK async de OpenAI); el trabajo de CPU, fuera del bucle de ticks.
- Las llamadas al LLM siempre tienen salida por error con un mensaje claro para el usuario (ver `chat_service`); el modelo local puede tardar o no estar.
- Mensajes y textos generados para el usuario, en español claro (sin "Pulse", "mock" ni jerga).
- `python -m pyflakes app` limpio (`bash scripts/verify.sh backend`).

## Modelo de la simulación

Ver `docs/pages/technical/modelo-de-simulacion.md`. En resumen:
- Las rutas son `RouteCoords` (una lista con `seg_speeds_ms`, la velocidad de cada tramo según OSRM). El tick mueve la unidad con aceleración y frenada hacia la velocidad objetivo (`_target_speed_ms`).
- Las fases con duración (`on_scene`, `at_hospital`, `refueling`) usan `phaseUntil` en segundos simulados (`sim_time_s`, expuesto como `simTimeS`).
- Emergencias: `pending` → `assigned` → `on_scene` → `resolved` (al salir del lugar).
- **Carga**: las emergencias automáticas van por defecto a ritmo equilibrado (`emergency_catalog.balanced_rate_per_min`: ~65 % de ocupación, frena si hay cola). Las incidencias externas simuladas van en **tiempo simulado** (`MOCK_EVENT_INTERVAL_MIN`, 8 min de media) y se descuentan del presupuesto. No vuelvas a programar nada del mundo simulado en tiempo de reloj.
- Una unidad **sin ruta está detenida**: el motor de posicionamiento devuelve su posición tal cual y velocidad 0. Nunca añadas ruido a la posición: el motor la reescribe en cada tick y se acumula (las unidades «se mecían»).

## Probar

```bash
docker compose restart simulation          # tras editar (borra el estado en memoria)
curl -s localhost:8080/api/sim/state | python -m json.tool | head
bash scripts/smoke.sh --ai
python scripts/check_realism.py      # todo sobre calles, velocidades y fases plausibles
python scripts/check_stability.py    # nada oscila: paradas quietas, sin retrocesos ni saltos ni vaivenes
python scripts/check_balance.py      # carga equilibrada: ocupación ~35–85 % y sin cola de avisos creciente
```

Escenario de prueba: `POST /api/sim/generate-scenario {"hospitals":3,"gasStations":2,"ambulances":5,"incidents":3,"clearExisting":true}` y después `POST /api/sim/control {"action":"play"}`.
