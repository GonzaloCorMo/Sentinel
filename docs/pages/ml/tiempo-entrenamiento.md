# Cuánto tiempo simular para tener un modelo útil

Guía numérica de cuánto tiempo necesita el gemelo estar en modo autónomo para que cada modelo pase de "baseline entrenado" a "decente" a "production-feeling". Todas las estimaciones asumen el escenario default (Aruba Island, flota auto-bootstrap de 3 unidades, distribución de tipos `medical/altercation/mass_casualty = 60/20/20`).

## Cómo se calcula

La cadencia real viene de dos parámetros:

```
missions_per_real_hour = ratePerMin · 60 · speedMultiplier / 60
                       = ratePerMin · speedMultiplier
```

Ejemplos:

| ratePerMin | speedMultiplier | Missions/hora real | Missions/día (24h) |
|---|---|---|---|
| 3 (default) | 1 | 3 | 72 |
| 10 | 5 | 50 | 1 200 |
| 20 | 10 | 200 | 4 800 |
| 30 | 15 | 450 | 10 800 |
| 40 | 20 | 800 | 19 200 |

**Cuello de botella realista**: tu máquina empieza a notar carga de escrituras Supabase + CPU OSRM por encima de `~800 missions/hora`. Para sesiones muy largas recomiendo `rate=20, speed=10` (200/h estable).

## ETA regressor (XGBoost)

Label: `response_time_s` (real al llegar al lugar del incidente).

| Hito | Missions | Tiempo real (rate=20 speed=10) | MAE esperado | Uso real |
|---|---|---|---|---|
| **Entrenable** | 200 | **1h** | 80–110s | Converge pero overfit; solo para verificar pipeline. |
| **Baseline funcional** | 2 000 | **10h** | 45–60s | Mejora clara sobre media. Útil en dashboard como "ETA estimada IA". |
| **Decente** | 5 000 | **25h (~1 día)** | 30–40s | Empieza a superar al estimador OSRM simple en casos con atasco. |
| **Sólido** | 20 000 | **100h (~4 días continuos)** | 20–28s | Feature importance estable. Competitivo para decisiones operativas. |
| **Production-feeling** | 50 000+ | **250h (~10 días)** | 15–22s | R² > 0.8. Apto para overwrite del ETA del motor. |

### Por qué tantos datos

El modelo aprende interacciones no lineales (distancia × atascos × hora_día × fuel), y cada interacción necesita ≥ 30–50 ejemplos para que el árbol no sobreajuste. Con 14 features típicas, 2k muestras cubren las combinaciones frecuentes; 50k cubren las raras.

### Señal de que ya sirve

```sql
-- Mejora del modelo IA vs el ETA heurístico del motor
with ia as (
  select avg(abs(eta_error_s)) as mae
  from mission_outcomes
  where eta_predicted_s is not null
    and simulation_session_id = '<session_con_modelo_IA_activo>'
),
motor as (
  select avg(abs(eta_error_s)) as mae
  from mission_outcomes
  where simulation_session_id = '<session_sin_modelo_IA>'
)
select motor.mae - ia.mae as improvement_s from ia, motor;
```

Si `improvement_s > 10s` → el modelo ya bate al motor en producción.

## Dispatch ranker (LightGBM LambdaRank)

Label baseline: `is_chosen` (imita al motor). Label upgrade: `1 / response_time_s` (ordena por outcome real).

**Constraint clave**: solo aprende cuando hay **≥3 candidatas IDLE** al despachar. Con flota de 3 (default de training mode) la mayoría de despachos tienen ≤2 candidatas — el modelo no aprende.

Sube el mínimo a 6–10 ambulancias antes de coleccionar datos. En `simulation/app/engine.py::_training_mode_tick`:

```python
if len(self.ambulances) < 10:   # era < 3
    ...
```

Con flota=10:

| Hito | Decisions con ≥3 cands | Tiempo real (rate=30 speed=15) | NDCG@1 | Uso |
|---|---|---|---|---|
| **Entrenable** | 500 | **1.5h** | 0.85 (memoriza) | Pipeline OK. |
| **Baseline** | 5 000 | **15h** | 0.95 (imita motor) | Útil solo para A/B con regla existente. |
| **Decente (label outcome real)** | 15 000 | **45h (~2 días)** | 0.70–0.80 | Aprende a elegir unidades con menos ETA real. |
| **Sólido** | 50 000 | **150h (~6 días)** | 0.85+ | Apto para sustituir `_dispatch_emergency`. |

## Anomaly detector (IsolationForest)

Es **no supervisado** → converge con mucho menos dato, pero para ser útil necesita ver rango normal completo de la flota.

| Hito | Ticks (≈ rows ml_telemetry_features) | Tiempo real (3 ambs, speed=10) | Utilidad |
|---|---|---|---|
| **Entrenable** | 5 000 | **30 min** | Baseline tipo "bootstrap" del ml-service. |
| **Decente** | 50 000 | **5h** | Detecta mal todos los outliers raros pero atrapa los obvios. |
| **Sólido** | 500 000 | **50h (~2 días)** | Contamination 5% ajustada a tu escenario real. Supera al modelo sintético del repo. |

El modelo mejora más con variedad de escenarios (jams frecuentes, múltiples tipos de emergencia) que con volumen puro.

## Plan de entrenamiento recomendado

**Día 1 (1–2h)** — Sanity check:
```bash
# Arrancar con rate bajo para validar que todo escribe
curl -X POST http://localhost:8000/api/sim/training-mode -d '{"enabled":true,"ratePerMin":5}' -H "Content-Type: application/json"
# Dejar 30 min
# Verificar en Supabase que hay rows en las 4 tablas
```

**Semana 1 (24h efectivas simulando)** — Primer ETA regressor baseline:
```bash
# rate=20 speed=10 → 200/h → 2400 missions en 12h continuas
# o 1 noche de 10h a rate=30 speed=15 → 450·10 = 4500 missions
```
Entrena con `python -m hpe_ml.models.eta_regressor`. Espera **MAE ~40s**.

**Semana 2** — Aumenta flota a 10 + dispatch ranker:
```bash
# Edita _training_mode_tick para flota mínima 10
# Déjalo correr 48h a rate=30 speed=15
# → ~20k decisions con ≥3 candidates
```

**Mes 1** — Despliegue iterativo:
1. Entrena ETA regressor.
2. Exporta a ONNX (ver [Usar modelo entrenado](usar-modelo-entrenado.md)).
3. Déjalo correr 1 semana con el modelo activo.
4. Compara `eta_error_s` pre/post en SQL.
5. Re-entrena con los nuevos datos.

## Orden de magnitud sin training mode

Si solo usas el gemelo en demos/reuniones (rate=0, sesiones de 1h a speed=1x):
- ~10–20 missions por sesión.
- Necesitarías **100+ demos** para 2k missions → **meses**.

→ **Conclusión**: el training mode no es opcional. Para tener un modelo propio en semanas, hay que dedicar una máquina (o VPS barato) a correrlo 24/7.

## Monitorización live

Query útil para ver si vas por buen camino:

```sql
select
  date_trunc('hour', created_at) as h,
  count(*)                       as missions,
  avg(response_time_s)::int      as avg_resp,
  avg(eta_error_s)::int          as avg_err,
  avg(outcome_quality)::numeric(4,2) as avg_q,
  count(distinct simulation_session_id) as sessions
from mission_outcomes
where created_at > now() - interval '48 hours'
group by 1
order by 1 desc;
```

Y el conteo acumulado:
```sql
select
  sum(case when response_time_s is not null then 1 else 0 end) as ready_missions,
  count(*) filter (where candidate_count >= 3)                  as ranker_candidates,
  (select count(*) from ml_telemetry_features)                  as telemetry_rows
from mission_outcomes
cross join route_decisions rd
where rd.outcome_id = mission_outcomes.id;
```
