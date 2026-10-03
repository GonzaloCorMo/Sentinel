"""Pluggable weighted-scoring registry for ambulance dispatch decisions.

Each `Factor` returns a numeric contribution. Total score = sum of
weighted contributions across registered factors. Higher score = better
candidate. The first viable candidate (passes hard filters) wins.

Add a new factor by subclassing `Factor`, registering it on engine init,
and tuning its weight. Breakdown returned per scoring call so dispatch
decisions stay explainable in telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Protocol

from .route_nav import haversine_m


class Factor(Protocol):
    name: str
    weight: float

    def score(self, amb: dict[str, Any], emergency: dict[str, Any], ctx: "ScoringContext") -> float:
        ...


@dataclass
class ScoringContext:
    """Engine-level data factors may inspect when scoring."""

    weather: dict[str, dict[str, Any]] = field(default_factory=dict)
    weather_station_coords: dict[str, tuple[float, float]] = field(default_factory=dict)
    external_jams: list[dict[str, Any]] = field(default_factory=list)
    jams: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DistanceFactor:
    """Negative weight on Haversine km — closer is better."""

    name: str = "distance"
    weight: float = -1.0

    def score(self, amb: dict[str, Any], em: dict[str, Any], ctx: ScoringContext) -> float:
        try:
            d_km = haversine_m(
                float(amb["latitude"]), float(amb["longitude"]),
                float(em["latitude"]), float(em["longitude"]),
            ) / 1000.0
        except (KeyError, TypeError, ValueError):
            return 0.0
        return d_km


@dataclass
class SeverityFactor:
    """Adds weight × severity (1=low … 4=critical). Promotes high-severity matches."""

    name: str = "severity"
    weight: float = 5.0
    _MAP = {"low": 1, "medium": 2, "high": 3, "critical": 4}

    def score(self, amb: dict[str, Any], em: dict[str, Any], ctx: ScoringContext) -> float:
        sev = em.get("severity")
        if isinstance(sev, str):
            return float(self._MAP.get(sev.lower(), 1))
        if isinstance(sev, (int, float)):
            return float(sev)
        return 0.0


@dataclass
class WeatherFactor:
    """Penalize candidates near bad-weather stations.

    Uses precip / wind / visibility around amb position. Score in [0,1]
    where 1.0 = clean weather (factor returns +1), 0.45 worst case.
    Centered around 0.7 so clean weather is positive contribution.
    """

    name: str = "weather"
    weight: float = 3.0

    def score(self, amb: dict[str, Any], em: dict[str, Any], ctx: ScoringContext) -> float:
        try:
            lat, lon = float(amb["latitude"]), float(amb["longitude"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        factor = compute_weather_factor(lat, lon, ctx)
        return factor - 0.7


@dataclass
class JamCrossingFactor:
    """Penalize candidates whose straight path crosses any jam polygon.

    Uses straight-line check (not OSRM) for speed during scoring; the
    real OSRM detour happens later in `_route_with_jam_avoidance`.
    """

    name: str = "jam_crossing"
    weight: float = -2.0

    def score(self, amb: dict[str, Any], em: dict[str, Any], ctx: ScoringContext) -> float:
        from .geometry_poly import polyline_intersects_polygon

        try:
            a = (float(amb["latitude"]), float(amb["longitude"]))
            b = (float(em["latitude"]), float(em["longitude"]))
        except (KeyError, TypeError, ValueError):
            return 0.0
        line = [a, b]
        crossings = 0
        for jam in (*ctx.jams, *ctx.external_jams):
            poly = jam.get("polygon") or []
            if len(poly) < 3:
                continue
            tup = [(float(p[0]), float(p[1])) for p in poly]
            if polyline_intersects_polygon(line, tup):
                crossings += 1
        return float(crossings)


def compute_weather_factor(
    lat: float, lon: float, ctx: ScoringContext, *, station_id: str | None = None
) -> float:
    """Return speed-degradation factor in [0.45, 1.0] based on nearest station.

    1.0 = clean. 0.45 = severe (storm + low visibility). Used both by
    `WeatherFactor` (scoring) and ETA computation (engine.py).
    """
    if not ctx.weather:
        return 1.0
    nearest_id = station_id
    if nearest_id is None:
        best_d = float("inf")
        for sid, coords in ctx.weather_station_coords.items():
            try:
                d = haversine_m(lat, lon, coords[0], coords[1])
            except (TypeError, ValueError):
                continue
            if d < best_d:
                best_d = d
                nearest_id = sid
        if nearest_id is None:
            nearest_id = next(iter(ctx.weather))
    reading = ctx.weather.get(nearest_id) or {}
    try:
        precip = float(reading.get("precipitation_mm", 0.0) or 0.0)
        wind = float(reading.get("wind_speed_kmh", 0.0) or 0.0)
        vis = float(reading.get("visibility_km", 10.0) or 10.0)
    except (TypeError, ValueError):
        return 1.0
    factor = 1.0 - 0.06 * max(0.0, precip - 0.5)
    factor -= 0.012 * max(0.0, wind - 20.0)
    factor -= 0.05 * max(0.0, 8.0 - vis)
    if not isfinite(factor):
        return 1.0
    return max(0.35, min(1.0, factor))


@dataclass
class ScoringRegistry:
    """Holds registered factors; scores candidates with breakdown."""

    factors: list[Factor] = field(default_factory=list)

    def register(self, factor: Factor) -> None:
        self.factors.append(factor)

    def score_candidate(
        self, amb: dict[str, Any], emergency: dict[str, Any], ctx: ScoringContext
    ) -> tuple[float, dict[str, float]]:
        breakdown: dict[str, float] = {}
        total = 0.0
        for f in self.factors:
            try:
                raw = f.score(amb, emergency, ctx)
            except Exception:
                raw = 0.0
            contrib = f.weight * raw
            breakdown[f.name] = round(contrib, 3)
            total += contrib
        return total, breakdown


def default_registry() -> ScoringRegistry:
    """Day-1 factor mix: distance + severity + weather + jam avoidance."""
    reg = ScoringRegistry()
    reg.register(DistanceFactor())
    reg.register(SeverityFactor())
    reg.register(WeatherFactor())
    reg.register(JamCrossingFactor())
    return reg
