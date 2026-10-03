"""Esquemas Pydantic para documentación y validación opcional."""

from .telemetry import (
    AmbulanceTelemetryPayload,
    MechanicalTelemetryPayload,
    MedicalTelemetryPayload,
    PositioningTelemetryPayload,
)

__all__ = [
    "AmbulanceTelemetryPayload",
    "MechanicalTelemetryPayload",
    "MedicalTelemetryPayload",
    "PositioningTelemetryPayload",
]
