# Proyecto paralelo de entrenamiento ML

Repositorio hermano al gemelo digital (`ml-training` en estos ejemplos) que consume sus datos y entrena modelos propios. Pensado para funcionar de forma independiente: si el gemelo está apagado, basta con que Supabase siga accesible.

> Repositorio de entrenamiento externo; ajusta la ruta y el nombre del paquete a tu copia. En los comandos, `<paquete_ml>` es el nombre del paquete Python de ese repositorio.

## Ubicación

Por convención al mismo nivel:
```
<carpeta-de-trabajo>/
├── <repo-del-gemelo>/   ← gemelo (este repo)
└── ml-training/         ← proyecto ML paralelo (externo)
```

## Estructura

```
ml-training/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── src/<paquete_ml>/
│   ├── __init__.py
│   ├── db.py                     ← conexión psycopg
│   ├── export.py                 ← SQL → Parquet
│   ├── features.py               ← feature engineering
│   └── models/
│       ├── __init__.py
│       ├── eta_regressor.py      ← XGBoost
│       ├── dispatch_ranker.py    ← LightGBM LambdaRank
│       └── anomaly.py            ← IsolationForest + export ONNX
├── notebooks/
│   └── 01_eda.ipynb              ← exploración inicial
├── data/                         ← Parquet exports (gitignored)
├── models/                       ← artefactos entrenados (gitignored)
└── docs/
    ├── dataset.md                ← esquema + features
    ├── training.md               ← how-to entrenamiento
    └── deploy.md                 ← ONNX export + carga en gemelo
```

## Quickstart desde cero

```bash
# 1. Activa training mode en el gemelo y déjalo generar datos
curl -X POST http://localhost:8080/api/sim/training-mode \
  -H "Content-Type: application/json" -d '{"enabled":true,"ratePerMin":20}'

# 2. Setup del proyecto paralelo
cd ../ml-training
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edita .env: POSTGRES_URL=postgresql://postgres:<PASSWORD>@localhost:54322/postgres

# 3. Cuando tengas ≥2k missions (ver SQL abajo) exporta y entrena
python -m <paquete_ml>.export --out data/
python -m <paquete_ml>.models.eta_regressor --data data/ml_mission_training.parquet
```

### Comprobar volumen actual
```sql
select count(*) from mission_outcomes;
select count(*), avg(candidate_count) from route_decisions;
```

## Dependencias principales

| Librería | Para qué |
|---|---|
| `pandas` + `pyarrow` | DataFrames + Parquet IO |
| `psycopg[binary]` | Cliente Postgres moderno |
| `xgboost` | ETA regressor |
| `lightgbm` | Dispatch ranker (LambdaRank) |
| `scikit-learn` | IsolationForest + split + métricas |
| `skl2onnx` + `onnxmltools` | Export a ONNX para reinyectar al gemelo |
| `jupyter` + `matplotlib` + `seaborn` | Notebooks EDA |
| `click` + `rich` | CLI pulido |

## Modelos incluidos

### ETA regressor (`src/<paquete_ml>/models/eta_regressor.py`)

Predice `response_time_s` a partir de:
- Distancia en km al lugar del incidente.
- Nº de atascos que cruza la ruta.
- Nº de reroutes previstos.
- Nº de candidatas disponibles al despachar.
- Tipo de emergencia + severidad (one-hot).

Modelo: `XGBoostRegressor` (400 árboles, lr=0.05, depth=6).

Métrica típica con 5k missions: **MAE ~32s** contra baseline mean (~86s).

### Dispatch ranker (`src/<paquete_ml>/models/dispatch_ranker.py`)

Input: una fila por ambulancia candidata (features: distancia, fuel, battery, on_route).
Output: score que ordena candidatas por "probabilidad de ser la mejor".

Algoritmo: **LightGBM LambdaRank** (`objective=lambdarank`, NDCG@1/3).

Label baseline: `is_chosen` (imita al motor). Upgrade: sustituir por `1 / response_time_s` del outcome real para learning-to-rank supervisado.

### Anomaly detector (`src/<paquete_ml>/models/anomaly.py`)

Refino del IsolationForest que viene con el gemelo (ml-service). Entrena con datos reales de `ml_telemetry_features` en lugar del bootstrap sintético, exporta a ONNX, se sube al volumen `ml-models` del gemelo.

## Ciclo de deploy

1. Entrena en `ml-training`.
2. Exporta a `.onnx`.
3. `docker compose cp` al servicio `ml-service` del gemelo.
4. `docker compose restart ml-service`.
5. El `simulation` backend vuelve a consumirlo automáticamente.

Detalle: `docs/deploy.md` del proyecto paralelo.

## Por qué vive fuera del gemelo

- **Dependencias pesadas**: XGBoost + LightGBM + pyarrow + jupyter añaden ~500 MB. No deben estar en la imagen runtime del backend.
- **Ciclo de vida distinto**: el gemelo actualiza cuando cambia un endpoint. El training puede iterar decenas de veces al día sobre el mismo gemelo congelado.
- **Stack diferente**: notebooks + MLflow (futuro) + data visualization ≠ FastAPI + asyncio.
- **Separación de concerns**: si mañana quieres usar SageMaker / Vertex / cualquier plataforma cloud, mueves solo `ml-training` sin tocar el gemelo.

## Roadmap del proyecto paralelo

- [x] Skeleton con export + 3 modelos baseline
- [x] Documentación dataset + training + deploy
- [ ] MLflow para tracking de experimentos
- [ ] DVC para versionar datos y modelos
- [ ] GitHub Actions CI para re-entrenar en push de nuevos datasets
- [ ] Notebook comparativo: XGBoost vs LightGBM vs CatBoost en ETA
- [ ] Implementar RL dispatch con CityFlow / SUMO-like (muy largo plazo)
