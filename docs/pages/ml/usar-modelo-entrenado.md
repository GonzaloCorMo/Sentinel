# Usar el modelo que entrenamos

Cómo pasar de `models/eta_xgb.json` (artefacto del proyecto paralelo) a que el gemelo digital esté usando ese modelo en decisiones reales de despacho.

> Repositorio de entrenamiento externo; ajusta la ruta y el nombre del paquete a tu copia. En los comandos, `../ml-training` es la carpeta del repositorio y `<paquete_ml>` su paquete Python.

## Flujo de deploy end-to-end

```
ml-training/                                 gemelo digital (este repo)
 ─────────────────                            ──────────────────────────
  entrenar modelo                              FastAPI simulation
       ↓                                             ↑ predict_eta()
  export a ONNX             COPIA               ml-service
       │     ────────────────────────►         (ONNX Runtime)
       │                                             ↑ carga .onnx
  models/eta_xgb.onnx                          /models/eta_xgb.onnx
```

## Paso 1 — Entrena

```bash
cd ../ml-training
source .venv/bin/activate
python -m <paquete_ml>.export --out data/
python -m <paquete_ml>.models.eta_regressor \
  --data data/ml_mission_training.parquet \
  --out models/eta_xgb.json
```

Salida esperada (con ≥2k missions):
```
test  MAE=32.1s  RMSE=47.4s  R²=0.68
baseline (mean) MAE=86.3s — mejora=+54.2s
```

Si `R²` queda por debajo de 0.3 o MAE no bate al baseline mean → revisa features y corre más simulación antes de seguir.

## Paso 2 — Exporta a ONNX

XGBoost no se exporta con `skl2onnx` directo. Usa `onnxmltools`:

```python
# scripts/export_eta_onnx.py (añadir al proyecto paralelo si lo quieres permanente)
from pathlib import Path
from xgboost import XGBRegressor
from onnxmltools import convert_xgboost
from skl2onnx.common.data_types import FloatTensorType

model = XGBRegressor()
model.load_model("models/eta_xgb.json")
n_features = model.n_features_in_

initial = [("input", FloatTensorType([None, n_features]))]
onnx_model = convert_xgboost(model, initial_types=initial, target_opset=16)

Path("models/eta_xgb.onnx").write_bytes(onnx_model.SerializeToString())
print(f"exported {n_features} features → models/eta_xgb.onnx")
```

Ejecuta:
```bash
python scripts/export_eta_onnx.py
ls -lh models/eta_xgb.onnx
```

Para el anomaly detector el export es automático (script `anomaly.py` ya lo hace con `skl2onnx`).

## Paso 3 — Sube al volumen del ml-service

El gemelo usa un volumen Docker `<proyecto>_ml-models` (el prefijo es el nombre del proyecto compose; compruébalo con `docker volume ls | grep ml-models`) montado en `/models` dentro del contenedor `ml-service`. En los comandos siguientes, `ML` es el id del contenedor:

```bash
ML=$(docker ps -qf name=ml-service)
```

### Método A: `docker cp` (rápido, ad-hoc)

```bash
# Desde ml-training/
docker cp models/eta_xgb.onnx "$ML":/models/eta_xgb.onnx

# Verifica
docker exec "$ML" ls -lh /models/
```

### Método B: copia al mountpoint del volumen (persiste entre recreate)

```bash
# Averigua dónde vive el volumen
VOL=$(docker volume inspect <proyecto>_ml-models -f '{{ .Mountpoint }}')
sudo cp models/eta_xgb.onnx "$VOL/eta_xgb.onnx"
sudo chown 1000:1000 "$VOL/eta_xgb.onnx"   # uid del usuario del container
```

## Paso 4 — Registra el modelo en el ml-service

Actualmente `ml-service/app.py` solo tiene loader para `fleet_anomaly`. Hay que añadir el ETA. Edita `ml-service/app.py`:

```python
# ── cerca del bloque de IsolationForest ─────────────────────────────────
def load_model(name: str):
    path = MODELS_DIR / f"{name}.onnx"
    if not path.exists():
        return None
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

_sessions["fleet_anomaly"] = load_model("fleet_anomaly")
_sessions["eta_xgb"] = load_model("eta_xgb")


class EtaInput(BaseModel):
    features: list[float]   # [distance_km, jams_crossed, rerouted_times,
                            #  candidate_count, et_medical, et_altercation,
                            #  et_mass_casualty, sev_stable, sev_moderate, sev_critical]


@app.post("/predict/eta")
def predict_eta(body: EtaInput):
    sess = _sessions.get("eta_xgb")
    if sess is None:
        raise HTTPException(503, "eta_xgb model not loaded")
    inp = np.asarray([body.features], dtype=np.float32)
    out = sess.run(None, {"input": inp})[0]
    return {"eta_s": float(out[0])}
```

Reinicia el servicio:
```bash
# Si tienes bind mount sobre ml-service/ (recomendado en docker-compose.yml):
docker restart "$ML"

# Si no, rebuild:
./build.sh ml-service && docker compose up -d ml-service
```

Verifica:

::: tip Puerto 9100
En el `docker-compose.yml` por defecto, `ml-service` solo escucha en la red interna (`http://ml-service:9100`). Para lanzar estas peticiones desde el host, publica el puerto `9100:9100` en el servicio o ejecútalas desde otro contenedor de la red.
:::

```bash
curl http://localhost:9100/models
curl -X POST http://localhost:9100/predict/eta \
  -H "Content-Type: application/json" \
  -d '{"features":[2.5, 1, 0, 3, 1,0,0, 1,0,0]}'
# → {"eta_s": 187.3}
```

## Paso 5 — Conecta el motor con el nuevo endpoint

En `simulation/app/ml_client.py` añade:

```python
async def predict_eta(features: list[float]) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.post(f"{ML_BASE_URL}/predict/eta",
                             json={"features": features})
            r.raise_for_status()
            return float(r.json()["eta_s"])
    except Exception:
        return None   # fallback silencioso — motor sigue con OSRM simple
```

En `simulation/app/engine.py::_track_dispatch` sobrescribe `eta_predicted_s` con la predicción del modelo si disponible:

```python
# justo antes de self._events.emit_decision(...)
try:
    from .ml_client import predict_eta
    features = self._build_eta_features(em, amb, trip_m)
    eta_ia = await predict_eta(features)
    if eta_ia is not None:
        tr["eta_predicted_s_motor"] = tr["eta_predicted_s"]   # conserva el original
        tr["eta_predicted_s"] = round(eta_ia, 1)
        tr["eta_source"] = "ia"
except Exception:
    pass
```

Y añade el feature builder coherente con el del proyecto paralelo:

```python
def _build_eta_features(self, em, amb, trip_m):
    # Orden IDÉNTICO al del entrenamiento (<paquete_ml>/features.py::build_eta_features)
    et = em.get("emergencyType", "medical")
    sev = amb.get("patientSeverity", "stable")
    return [
        trip_m / 1000.0,                                    # distance_km
        len(self._jam_tuples()),                            # jams_crossed (proxy)
        0,                                                  # rerouted_times (0 al despachar)
        sum(1 for a in self.ambulances
            if infer_fsm_state(a) == AmbulanceState.IDLE),  # candidate_count
        1.0 if et == "medical" else 0.0,
        1.0 if et == "altercation" else 0.0,
        1.0 if et == "mass_casualty" else 0.0,
        1.0 if sev == "stable" else 0.0,
        1.0 if sev == "moderate" else 0.0,
        1.0 if sev == "critical" else 0.0,
    ]
```

**CRÍTICO**: el orden de los features tiene que coincidir exactamente entre entrenamiento (`<paquete_ml>/features.py`) e inferencia (`engine.py`). Cualquier desalineación → predicciones sin sentido.

## Paso 6 — A/B en producción

Deja correr unas horas con el modelo activo y compara:

```sql
-- Sessions con modelo IA
with ia as (
  select
    date_trunc('hour', created_at) as h,
    avg(abs(eta_error_s)) as mae_ia
  from mission_outcomes
  where simulation_session_id = (
    select id from simulation_sessions
    where started_at > now() - interval '6 hours'
      and mode = 'training_autonomous'
    order by started_at desc limit 1
  )
  group by 1
),
base as (
  select avg(abs(eta_error_s)) as mae_baseline
  from mission_outcomes
  where simulation_session_id != ia_session_id
    and created_at < now() - interval '6 hours'
)
select
  ia.h,
  ia.mae_ia,
  base.mae_baseline,
  base.mae_baseline - ia.mae_ia as improvement_s
from ia, base
order by h desc;
```

Si `improvement_s` sistemáticamente positivo → el modelo mejora al motor. Prómocelo a default. Si no → re-entrena con más datos o ajusta features.

## Paso 7 — Iteración continua

Una vez el flujo funciona:

1. Semanalmente re-entrena con los datos nuevos.
2. Versiona los ONNX: `models/eta_xgb.v2.onnx`, `v3.onnx`...
3. Guarda el MAE de cada versión en una tabla tuya (`ml_model_registry` — considérala para el próximo roadmap).
4. Cuando un modelo nuevo bate al actual por ≥10% en MAE, promociona.

## Rollback

Si el modelo nuevo degrada outcomes:

```bash
# Copia el modelo anterior de vuelta
docker cp models/eta_xgb.v1.onnx "$ML":/models/eta_xgb.onnx
docker restart "$ML"
```

O directo: borra el fichero y el fallback en `predict_eta` devuelve `None` → el motor vuelve al ETA heurístico OSRM.

## Mismo flujo para dispatch ranker

El ranker no es un regressor simple, devuelve un ordering. Implementación:

- Endpoint `/predict/dispatch_ranker` en `ml-service` que recibe `{candidates: [[features], ...]}` y devuelve `scores: [float]`.
- En `engine._dispatch_emergency`, en vez de `sorted by haversine`, pedimos scores al ml-service y ordenamos por score descendente.
- Feature order idéntico al del entrenamiento LightGBM.

Volumen para que valga la pena: ≥15k decisions con ≥3 candidatas (ver [Tiempo de entrenamiento](tiempo-entrenamiento.md)).

## Cheat sheet de comandos

```bash
# Entrenar
cd ../ml-training && source .venv/bin/activate
python -m <paquete_ml>.export --out data/
python -m <paquete_ml>.models.eta_regressor --data data/ml_mission_training.parquet

# Exportar ONNX
python scripts/export_eta_onnx.py

# Deploy
ML=$(docker ps -qf name=ml-service)
docker cp models/eta_xgb.onnx "$ML":/models/
docker restart "$ML"

# Verificar
curl http://localhost:9100/models
curl -X POST http://localhost:9100/predict/eta -d '{"features":[2.5,1,0,3,1,0,0,1,0,0]}' -H "Content-Type: application/json"

# Monitorizar A/B
docker exec "$(docker ps -qf name=supabase-db)" psql -U postgres -c \
  "select avg(abs(eta_error_s)) from mission_outcomes where created_at > now() - interval '1 hour';"
```

## Referencias

- [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/)
- [onnxmltools XGBoost converter](https://onnxruntime.ai/docs/tutorials/traditional-ml.html#xgboost)
- [LightGBM ONNX export](https://onnxruntime.ai/docs/tutorials/traditional-ml.html#lightgbm)
- [ML model versioning (MLflow Model Registry)](https://mlflow.org/docs/latest/model-registry.html) — siguiente paso cuando quieras formalizar el versionado.
