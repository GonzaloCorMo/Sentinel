# Simulador global y despacho (motor Python)

Comportamiento implementado en `simulation/app/engine.py` y contrato HTTP en `simulation/app/main.py`.

## Reloj global de simulación

- `speed_multiplier` es **único** para todo el motor: se propaga a cada ambulancia al llamar a `update_speed_multiplier`.
- `POST /api/control/speed` acepta `multiplier` en **float**, rango **0.5–8** (alineado con el dashboard Next.js).
- `POST /api/control/toggle` pausa o reanuda la simulación para **toda** la flota (`is_simulating`).

## Despacho de emergencias

- Las emergencias en estado `INITIATED` se ordenan por **gravedad** (CRITICAL antes que HIGH, etc.).
- En cada ciclo del bucle de despacho se llama a `evaluate_fleet_assignments()` para no dejar pendientes sin reasignar cuando cambia disponibilidad o combustible.
- Solo se **asigna** una unidad si el **combustible actual** cubre la misión estimada: distancia hasta la emergencia + distancia de la emergencia al hospital más cercano, con consumo ~`0.15` % por km (coherente con `telemetry/mechanical.py`) más un **margen** (`MISSION_FUEL_MARGIN_PCT`).
- Si hay candidatos con buena puntuación pero **sin combustible suficiente**, el mejor pasa a **repostaje prioritario** (`route_to_nearest("GAS_STATION")`) antes de poder cubrir la urgencia.

## Repostaje y ocio

- **Repostaje proactivo**: unidades libres con combustible por debajo de `PROACTIVE_REFUEL_THRESHOLD_PCT` (60%) van a la gasolinera más cercana antes de quedar disponibles para ocio largo.
- **Ocio en hospital**: solo si combustible **≥ 60%** (`IDLE_HOSPITAL_MIN_FUEL_PCT`); se elige hospital con **menos ambulancias repostando cerca** (heurística por radio km) y luego carga en ruta y distancia.

## Referencias

| Concepto | Archivo |
|----------|---------|
| Motor | `simulation/app/engine.py` |
| API FastAPI | `simulation/app/main.py` |
| Dashboard | `proyecto_hpe/src/components/dashboard/dashboard-client.tsx` |
