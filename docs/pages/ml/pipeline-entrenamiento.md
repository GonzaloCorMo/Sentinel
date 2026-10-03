# Pipeline ML de entrenamiento propio

El gemelo digital captura datos crudos de cada tick, cada decisión y cada outcome para alimentar un proyecto paralelo (`ml-training`) donde se entrenan modelos propios y se vuelven a desplegar sobre el gemelo vía ONNX.

## Flujo end-to-end

```
┌──────────────────────────┐
│  Motor FastAPI           │ (this repo: simulation/)
│  ├─ engine.py            │
│  ├─ event_writer.py      │───┐
│  └─ telemetry_writer.py  │   │
└──────────────────────────┘   │  emite a Supabase
                               ▼
                ┌──────────────────────────────┐
                │  Supabase (Postgres+pgvector)│
                │  ├─ entity_events            │
                │  ├─ mission_outcomes         │
                │  ├─ route_decisions          │
                │  ├─ simulation_sessions      │
                │  ├─ telemetry_logs           │
                │  └─ view ml_mission_training │
                └──────────────────────────────┘
                               │  SQL / pyarrow
                               ▼
                ┌──────────────────────────────┐
                │  ml-training (repo externo)  │
                │  ├─ export.py → Parquet      │
                │  ├─ features.py              │
                │  ├─ models/eta_regressor.py  │ XGBoost
                │  ├─ models/dispatch_ranker.py│ LightGBM LambdaRank
                │  └─ models/anomaly.py        │ IsolationForest
                └──────────────────────────────┘
                               │  .onnx
                               ▼
                ┌──────────────────────────────┐
                │  ml-service (de vuelta gemelo)│
                │  └─ /models/*.onnx            │
                └──────────────────────────────┘
```

## Tablas añadidas

Migración: `supabase/migrations/20260422200000_ml_training_pipeline.sql`.

### `entity_events`
Append-only log del ciclo de vida de cada objeto del mapa. Una fila por create/update/delete/dispatch/resolve/reroute/phase_change. Discrimina por `kind` (`ambulance`, `companion`, `poi`, `emergency`, `jam`). Indexado por tiempo, kind, entity_id, session.

### `mission_outcomes`
Una fila por emergencia resuelta. Contiene los **labels ML**:
- `response_time_s` → label del ETA regressor.
- `eta_error_s` → señal para regressor iterativo.
- `outcome_quality` (0..1) → label del dispatch ranker (upgrade).

### `route_decisions`
Snapshot del mundo al despachar: lista de `candidates` jsonb + ambulancia elegida + fuente (`engine_auto` / `ai_autonomous` / `ai_hitl` / `operator`). Origen del learning-to-rank.

### `simulation_sessions`
Marca cada sesión del motor (arranque/reset). Permite particionar datasets y comparar rendimiento entre ejecuciones.

### Vista `ml_mission_training`
Join de outcomes + decisions ya listo para XGBoost. Se exporta directamente a Parquet.

## Modo simulación autónoma

Nuevo endpoint `/api/sim/training-mode` (ver [API HTTP SSE](../technical/simulation-api-http-sse.md)) que:

- Arranca una sesión nueva (`simulation_sessions`).
- Genera emergencias periódicas según `ratePerMin` (default 3).
- Bootstrap auto de POIs y flota mínima si faltan.
- Fuerza `paused=False` + `dispatchRequiresApproval=False` para que el generador no se bloquee.

**Rate + speed multiplier**: combinar `speedMultiplier=10` con `ratePerMin=20` da ~200 missions/hora real.

```bash
curl -X POST http://localhost:8080/api/sim/training-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"ratePerMin":20}'

curl -X POST http://localhost:8080/api/sim/control \
  -H "Content-Type: application/json" \
  -d '{"speedMultiplier":15}'
```

## Proyecto paralelo `ml-training`

Vive como carpeta hermana del gemelo (`../ml-training/`). Detalle completo en su README y sus docs internas (`dataset.md`, `training.md`, `deploy.md`).

> Repositorio de entrenamiento externo; ajusta la ruta y el nombre del paquete a tu copia.

Quickstart tras tener datos:

```bash
cd ../ml-training
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # pega el POSTGRES_URL (local: postgresql://postgres:XXX@localhost:54322/postgres)

python -m <paquete_ml>.export --out data/
python -m <paquete_ml>.models.eta_regressor --data data/ml_mission_training.parquet
```

## Modelos

| Modelo | Algoritmo | Librería | Label | Estado |
|---|---|---|---|---|
| ETA regressor | Gradient boosting | XGBoost | `response_time_s` | skeleton listo |
| Dispatch ranker | LambdaRank | LightGBM | `is_chosen` (baseline) / `outcome_quality` (upgrade) | skeleton listo |
| Anomaly (refino) | IsolationForest | scikit-learn | unsupervised | skeleton listo |
| ETA attention net | Transformer | PyTorch (futuro) | `response_time_s` | no implementado |

## Volumen de datos necesario

| Modelo | Mínimo útil | Excelente | Horas sim (rate=20, speed=15) |
|---|---|---|---|
| ETA regressor | 2k missions | 20k | 0.5h → baseline, 5h → sólido |
| Dispatch ranker | 10k decisions c/ ≥3 cands | 50k | 3h → baseline, 15h → sólido |
| Anomaly detector | 50k ticks | 500k | 1h → baseline |

## Referencias

### Diseño de datos
- [PostgreSQL JSONB](https://www.postgresql.org/docs/current/datatype-json.html) — por qué JSONB > tabla-por-tipo.
- [Event sourcing — Martin Fowler](https://martinfowler.com/eaaDev/EventSourcing.html) — patrón del log append-only.
- [Feature Store concepts](https://www.featurestore.org/what-is-a-feature-store) — qué es y cuándo vale la pena.

### Modelos
- [XGBoost paper (Chen & Guestrin, 2016)](https://arxiv.org/abs/1603.02754)
- [XGBoost docs](https://xgboost.readthedocs.io/)
- [LightGBM paper (Ke et al., 2017)](https://papers.nips.cc/paper/6907-lightgbm-a-highly-efficient-gradient-boosting-decision-tree)
- [LightGBM LambdaRank](https://lightgbm.readthedocs.io/en/latest/Features.html#lambdarank)
- [Isolation Forest (Liu et al., 2008)](https://doi.org/10.1109/ICDM.2008.17)

### Serving / ONNX
- [ONNX](https://onnx.ai/)
- [sklearn-onnx](https://onnx.ai/sklearn-onnx/)
- [onnxmltools](https://github.com/onnx/onnxmltools) — convertidor XGBoost / LightGBM.
- [ONNX Runtime](https://onnxruntime.ai/)

### Dominio (operaciones médicas)
- [EMS Agenda 2050 — NHTSA](https://www.ems.gov/projects/ems-agenda-2050.html)
- [OSRM Project](http://project-osrm.org/)
- [London Ambulance Service studies — PubMed](https://pubmed.ncbi.nlm.nih.gov/?term=london+ambulance+response+time)
