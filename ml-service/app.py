"""HPE Sentinel — servicio ML.

Sirve modelos ONNX sobre features derivadas de la telemetría v2.0. Primer
caso de uso: detector de anomalías `fleet_anomaly` (IsolationForest→ONNX).

Endpoints:
  GET  /health
  GET  /models               lista modelos cargados
  POST /predict/{model}      features → score + etiqueta
  POST /train/{model}        [dev] re-entrena con dataset pequeño + recarga
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from skl2onnx import to_onnx
from skl2onnx.common.data_types import FloatTensorType
from sklearn.ensemble import IsolationForest

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("ml-service")

MODELS_DIR = Path(os.environ.get("ML_MODELS_DIR", "/models"))
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Features esperadas por el modelo fleet_anomaly (orden fijo).
FLEET_ANOMALY_FEATURES = [
    "mech_fuel_pct",
    "mech_battery_pct",
    "mech_engine_temp_c",
    "mech_engine_rpm",
    "mech_eco_score",
    "mech_vibration_g",
    "mech_long_g",
    "mech_lat_g",
    "der_vehicle_health",
    "der_aggression",
    "net_loss_pct",
    "net_lat_mqtt_ms",
    "env_cabin_co2",
    "env_vibration_g",
]

_sessions: dict[str, ort.InferenceSession] = {}
_thresholds: dict[str, float] = {"fleet_anomaly": 0.0}


def _synth_training_data(n: int = 400) -> np.ndarray:
    """Genera datos sintéticos "normales" de flota para entrenar el detector.
    Shape: (n, len(FLEET_ANOMALY_FEATURES))."""
    rng = np.random.default_rng(42)
    rows = []
    for _ in range(n):
        rows.append([
            rng.uniform(40, 100),    # fuel
            rng.uniform(70, 100),    # battery
            rng.normal(90, 6),       # engine temp
            rng.normal(1600, 400),   # rpm
            rng.normal(75, 10),      # eco
            abs(rng.normal(0.15, 0.08)),  # vibration
            rng.normal(0, 0.15),     # long g
            rng.normal(0, 0.1),      # lat g
            rng.normal(85, 8),       # health
            rng.uniform(10, 60),     # aggression
            abs(rng.normal(1.5, 0.8)),  # loss
            abs(rng.normal(30, 10)),  # mqtt lat
            rng.normal(650, 60),     # cabin co2
            abs(rng.normal(0.4, 0.15)),  # env vib
        ])
    return np.asarray(rows, dtype=np.float32)


def train_fleet_anomaly() -> dict[str, Any]:
    """Entrena IsolationForest → exporta ONNX → recarga sesión."""
    X = _synth_training_data()
    model = IsolationForest(n_estimators=80, contamination=0.05, random_state=42)
    model.fit(X)
    onnx_model = to_onnx(
        model,
        initial_types=[("input", FloatTensorType([None, X.shape[1]]))],
        target_opset={"": 18, "ai.onnx.ml": 3},
        options={id(model): {"score_samples": True}},
    )
    path = MODELS_DIR / "fleet_anomaly.onnx"
    with open(path, "wb") as f:
        f.write(onnx_model.SerializeToString())
    _load_session("fleet_anomaly", path)
    _log.info("Trained + saved fleet_anomaly (%d samples)", X.shape[0])
    return {"ok": True, "samples": int(X.shape[0]), "path": str(path)}


def _load_session(name: str, path: Path) -> None:
    _sessions[name] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _ensure_fleet_anomaly() -> None:
    if "fleet_anomaly" in _sessions:
        return
    path = MODELS_DIR / "fleet_anomaly.onnx"
    if path.exists():
        try:
            _load_session("fleet_anomaly", path)
            return
        except Exception:
            _log.exception("Reload fleet_anomaly failed; retraining")
    train_fleet_anomaly()


app = FastAPI(title="HPE Sentinel ML Service", version="1.0")


@app.on_event("startup")
async def _startup() -> None:
    try:
        _ensure_fleet_anomaly()
    except Exception:
        _log.exception("fleet_anomaly init failed")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "models": list(_sessions.keys())}


@app.get("/models")
async def list_models() -> list[dict[str, Any]]:
    out = []
    for name, sess in _sessions.items():
        out.append({
            "name": name,
            "version": "v1",
            "inputs": [inp.name for inp in sess.get_inputs()],
            "outputs": [o.name for o in sess.get_outputs()],
            "features": FLEET_ANOMALY_FEATURES if name == "fleet_anomaly" else [],
        })
    return out


class PredictBody(BaseModel):
    features: dict[str, float]


@app.post("/predict/fleet_anomaly")
async def predict_fleet_anomaly(body: PredictBody) -> dict[str, Any]:
    _ensure_fleet_anomaly()
    sess = _sessions.get("fleet_anomaly")
    if not sess:
        raise HTTPException(503, "model not ready")
    vec = np.asarray(
        [[float(body.features.get(k, 0.0)) for k in FLEET_ANOMALY_FEATURES]],
        dtype=np.float32,
    )
    outputs = sess.run(None, {"input": vec})
    # IsolationForest ONNX: outputs pueden ser [labels, scores] con shapes varios.
    def _first_scalar(arr):
        a = np.asarray(arr).ravel()
        return float(a[0]) if a.size > 0 else 0.0
    label = int(_first_scalar(outputs[0]))
    score = _first_scalar(outputs[1]) if len(outputs) > 1 else 0.0
    is_anomaly = label == -1
    severity = (
        "critical" if score < -0.15 else
        "warning" if score < -0.05 else
        "info"
    )
    missing = [k for k in FLEET_ANOMALY_FEATURES if k not in body.features]
    return {
        "model": "fleet_anomaly",
        "version": "v1",
        "isAnomaly": is_anomaly,
        "anomalyScore": round(score, 4),
        "severity": severity,
        "label": label,
        "missingFeatures": missing,
    }


@app.post("/train/fleet_anomaly")
async def train_endpoint() -> dict[str, Any]:
    return train_fleet_anomaly()
