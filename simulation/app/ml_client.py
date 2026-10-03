"""Cliente del servicio ML (ONNX) + export de features a CSV/Parquet.

El servicio `ml-service` corre un modelo ``fleet_anomaly`` sobre ONNX
Runtime. Este módulo:

- Convierte telemetría in-memory de una unidad a vector de features
  (`FLEET_ANOMALY_FEATURES`).
- Llama al endpoint HTTP ``/predict/fleet_anomaly``.
- Persiste la predicción en ``ml_predictions`` de Supabase.
- Ofrece `export_features` para volcar la tabla
  ``ml_telemetry_features`` como CSV / JSONL / Parquet (si pyarrow
  disponible).
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

ML_BASE_URL = os.environ.get("ML_SERVICE_URL", "http://ml-service:9100")
DEFAULT_TIMEOUT = 8.0

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


def _amb_to_features(amb: dict[str, Any]) -> dict[str, float]:
    """Extrae el vector de features desde la telemetría en memoria."""
    tele = amb.get("telemetry") or {}
    mech = tele.get("mechanical") or {}
    der = tele.get("derived") or {}
    net = tele.get("network") or {}
    env = tele.get("environmental") or {}
    lat_ms = (net.get("latencyMs") or {}).get("mqtt")
    return {
        "mech_fuel_pct": float(mech.get("fuelLevelPct") or amb.get("fuelLevel") or 0),
        "mech_battery_pct": float(mech.get("batteryPct") or amb.get("batteryLevel") or 0),
        "mech_engine_temp_c": float(mech.get("engineTempC") or 0),
        "mech_engine_rpm": float(mech.get("engineRpm") or 0),
        "mech_eco_score": float(mech.get("ecoScore") or 0),
        "mech_vibration_g": float(mech.get("engineVibrationG") or 0),
        "mech_long_g": float(mech.get("longitudinalG") or 0),
        "mech_lat_g": float(mech.get("lateralG") or 0),
        "der_vehicle_health": float(der.get("vehicleHealthPct") or 0),
        "der_aggression": float(der.get("drivingAggressionScore") or 0),
        "net_loss_pct": float(net.get("packetLossPct") or 0),
        "net_lat_mqtt_ms": float(lat_ms or 0),
        "env_cabin_co2": float((env.get("cabin") or {}).get("co2Ppm") or 0),
        "env_vibration_g": float(env.get("chassisVibrationG") or 0),
    }


async def predict_fleet_anomaly(amb: dict[str, Any]) -> dict[str, Any]:
    """Llama al servicio ML y persiste la predicción.

    Args:
        amb: Dict de la ambulancia (con ``telemetry`` poblada).

    Returns:
        Dict con ``ok``, ``anomalyScore``, ``version``, ``features``. En
        fallo de red, devuelve ``{"ok": False, "error": str}``.
    """
    features = _amb_to_features(amb)
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(f"{ML_BASE_URL}/predict/fleet_anomaly", json={"features": features})
            r.raise_for_status()
            pred = r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Persiste
    sb = get_supabase()
    if sb is not None:
        try:
            sb.table("ml_predictions").insert({
                "ambulance_id": str(amb["id"]),
                "model_name": "fleet_anomaly",
                "model_version": pred.get("version", "v1"),
                "prediction": pred,
                "features_in": features,
                "score": pred.get("anomalyScore"),
            }).execute()
        except Exception:
            _logger.exception("persist prediction failed")

    pred["ok"] = True
    pred["features"] = features
    return pred


async def ml_health() -> dict[str, Any]:
    """Healthcheck del servicio ML (``/health``)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{ML_BASE_URL}/health")
            return r.json() if r.status_code == 200 else {"ok": False}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Export dataset ──────────────────────────────────────────────────────
async def export_features(hours: int = 6, format: str = "csv") -> tuple[bytes, str]:
    """Exporta ``ml_telemetry_features`` de las últimas ``hours`` horas.

    Args:
        hours: Ventana temporal (máx 168).
        format: ``"csv"``, ``"jsonl"`` o ``"parquet"`` (requiere pyarrow;
            si falta, cae a JSONL).

    Returns:
        Tupla ``(bytes, content_type)``.
    """
    sb = get_supabase()
    if sb is None:
        return b"", "text/plain"
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        r = sb.table("ml_telemetry_features").select("*").gte("timestamp", since).limit(20000).execute()
        rows = r.data or []
    except Exception:
        _logger.exception("export_features query failed")
        rows = []

    if format == "parquet":
        try:
            import pandas as pd
            import pyarrow as pa
            import pyarrow.parquet as pq
            df = pd.DataFrame(rows)
            buf = io.BytesIO()
            pq.write_table(pa.Table.from_pandas(df), buf, compression="snappy")
            return buf.getvalue(), "application/octet-stream"
        except Exception:
            _logger.warning("pyarrow no disponible; devolviendo JSON lines")
            format = "jsonl"

    if format == "jsonl":
        buf = io.StringIO()
        for row in rows:
            buf.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return buf.getvalue().encode("utf-8"), "application/x-ndjson"

    # CSV
    buf = io.StringIO()
    if rows:
        import csv
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return buf.getvalue().encode("utf-8"), "text/csv"
