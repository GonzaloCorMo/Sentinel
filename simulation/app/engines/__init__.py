"""Motores de telemetría composables (patrón Composition)."""

from .composite import TelemetryComposite
from .mechanical import MechanicalEngine
from .medical import MedicalEngine
from .positioning import PositioningEngine

__all__ = [
    "TelemetryComposite",
    "PositioningEngine",
    "MechanicalEngine",
    "MedicalEngine",
]
