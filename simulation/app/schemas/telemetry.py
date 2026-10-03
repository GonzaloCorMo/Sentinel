"""Modelos de telemetría densa (IA-ready). Alineados con los dict emitidos por `engines/`."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TirePressuresPsi(BaseModel):
    fl: float = Field(..., description="Front left tire pressure (PSI)")
    fr: float = Field(..., description="Front right")
    rl: float = Field(..., description="Rear left")
    rr: float = Field(..., description="Rear right")


class MechanicalTelemetryPayload(BaseModel):
    fuelLevelPct: float = Field(..., ge=0, le=100)
    batteryPct: float = Field(..., ge=0, le=100)
    engineTempC: float
    tirePressureKpa: dict[str, float] = Field(default_factory=dict)
    tirePressurePsi: TirePressuresPsi | dict[str, float] | None = Field(
        default=None, description="Presión en PSI (preferido para display US)"
    )
    oilTempC: float = Field(default=90.0, description="Temperatura aceite motor (°C)")
    brakeFluidTempC: float = Field(default=35.0, description="Temperatura líquido de frenos (°C)")
    secondaryBatteryVoltageV: float = Field(
        default=13.2, description="Batería secundaria (módulo médico) (V)"
    )
    longitudinalG: float = Field(default=0.0, description="Aceleración longitudinal (g)")
    lateralG: float = Field(default=0.0, description="Aceleración lateral (g)")
    sirensOn: bool = False
    sirensActive: bool = Field(default=False, description="Sirenas operativas (misión urgencia/traslado)")
    odometerKm: float = Field(default=0.0, description="Distancia acumulada (km)")
    engineRpm: float = Field(default=800.0, description="RPM motor")
    cabinTemperatureC: float = Field(default=22.0, description="Temperatura cabina paciente (°C)")


class MedicalTelemetryPayload(BaseModel):
    heartRateBpm: float = Field(..., description="FC con ruido simulado")
    bloodPressureMmhg: dict[str, float]
    spo2Pct: float
    defibrillatorStatus: str
    etco2MmHg: float = Field(default=35.0, description="EtCO2 al final de espiración (mmHg)")
    bloodGlucoseMgDl: float = Field(default=110.0, description="Glucosa capilar (mg/dL)")
    bodyTempC: float = Field(default=36.8, description="Temperatura corporal (°C)")
    infusionRateMlH: float = Field(default=0.0, description="Infusión IV (ml/h)")
    ecgRhythm: str = Field(default="Sinusal", description="Ritmo ECG categorizado")
    gcsScore: int = Field(default=15, ge=3, le=15, description="Glasgow")
    respiratoryRatePerMin: int = Field(default=16, ge=8, le=40, description="Frecuencia respiratoria")


class PositioningTelemetryPayload(BaseModel):
    latitude: float
    longitude: float
    speedKmh: float
    speedMs: float
    accelerationMs2: float
    headingDeg: float
    roadSpeedLimitKmh: float | None = Field(
        default=None, description="Límite de vía estimado (km/h), null si desconocido"
    )
    gpsHdop: float = Field(default=1.0, description="Dilución horizontal (HDOP)")
    gpsAccuracyM: float = Field(default=3.0, description="Precisión horizontal estimada (m)")


class AmbulanceTelemetryPayload(BaseModel):
    positioning: PositioningTelemetryPayload
    mechanical: MechanicalTelemetryPayload
    medical: MedicalTelemetryPayload | None = Field(
        default=None, description="Null si la unidad no lleva paciente"
    )

    model_config = {"extra": "allow"}


def telemetry_schema_json() -> dict[str, Any]:
    """JSON Schema para documentación / clientes."""
    return AmbulanceTelemetryPayload.model_json_schema()
