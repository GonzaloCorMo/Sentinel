# Simulador y despacho

Comportamiento implementado en `simulation/app/engine.py`, con el scoring en `simulation/app/dispatch_scoring.py` y las reglas de recursos en `simulation/app/ambulance_fsm.py`. El contrato HTTP está en [API de simulación](simulation-api-http-sse.md).

## Reloj global

- `speedMultiplier` es **único** para todo el motor (rango 0,1–20) y se cambia con `POST /api/sim/control`.
- `dt_sim = dt_real * speed_multiplier`: los motores de telemetría y el avance por ruta usan `dt_sim`; el stream SSE mantiene su cadencia fija.
- `action: "pause"` / `"play"` congela o reanuda la simulación para **toda** la flota. El motor arranca en pausa.

## Despacho de emergencias

Para cada emergencia `pending`, el motor:

1. Toma como candidatas las unidades en estado `IDLE` sin ruta activa.
2. Las puntúa con un **registro de factores ponderados** (`ScoringRegistry`). Mayor puntuación = mejor candidata:

   | Factor | Peso | Efecto |
   |---|---|---|
   | `distance` | −1,0 | Penaliza la distancia Haversine (km) a la emergencia. |
   | `severity` | +5,0 | Prioriza según la gravedad de la emergencia. |
   | `weather` | +3,0 | Tiene en cuenta la meteorología de las estaciones cercanas. |
   | `jam_crossing` | −2,0 | Penaliza rutas que cruzan atascos (manuales o derivados de eventos externos). |

3. Recorre las candidatas en orden y asigna la primera que **puede aceptar la misión** (`can_accept_mission`): el combustible debe cubrir ida y vuelta más una reserva del 8 %, y la batería secundaria debe estar por encima del 18 %.
4. Si ninguna candidata tiene recursos suficientes, la mejor va primero a **repostar** (`to_refuel`) y queda con la emergencia pendiente para retomarla al terminar.

El desglose del scoring se guarda por misión (`dispatch_score_breakdown`) para que las decisiones sean explicables y alimenten el dataset de ranking ML.

Con `dispatchRequiresApproval=true`, las nuevas emergencias pasan antes por el flujo HITL (ver [IA: HITL y autónomo](ai-hitl-autonomo.md)). En modo autónomo el motor deja de autoasignar y decide la IA.

## Repostaje

- Las unidades eléctricas van a estaciones de carga y las de combustión a gasolineras (según el `powertrain` de su tipo).
- **Repostaje en reposo**: una unidad libre con energía por debajo del 20 % va al punto de repostaje más cercano (con un enfriamiento entre intentos).
- **Repostaje de supervivencia**: cuando una unidad es la mejor opción pero no llega, reposta y retoma la emergencia.

## Eventos externos

Los eventos de la [fuente de eventos](fuente-de-eventos.md) intervienen en el despacho de dos formas: los viales crean atascos que penalizan el scoring y que el routing evita, y los de emergencia generan nuevas emergencias que entran en este mismo flujo.

## Referencias

| Concepto | Archivo |
|----------|---------|
| Motor y bucle de despacho | `simulation/app/engine.py` |
| Scoring de candidatas | `simulation/app/dispatch_scoring.py` |
| Reglas de recursos y FSM | `simulation/app/ambulance_fsm.py` |
| API FastAPI | `simulation/app/main.py` |
| Mapa de operaciones | `frontend/src/views/MapOperationsView.vue` |
