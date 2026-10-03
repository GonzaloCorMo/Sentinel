"""Registro central de regiones / mapas disponibles para el escenario.

Cada región describe el extracto OSRM al que apuntar, el centro/zoom del
mapa por defecto y las coordenadas de spawn que usa el motor cuando bootstrappea
flota / POIs / emergencias automaticas. La región activa es estado global del
proceso: cambiarla resetea la simulación (`engine.reset_simulation`) porque las
coordenadas de POIs/ambulancias previas no son válidas en el nuevo grafo.

Cada OSRM corre en su propio contenedor docker (osrm-santiago, osrm-bogota, ...);
solo el de la región activa recibe queries — los demás quedan idle.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class RegionConfig:
    id: str
    name: str
    country: str
    timezone: str
    center_lat: float
    center_lon: float
    zoom: int
    spawn_lat: float
    spawn_lon: float
    pbf_url: str
    osrm_url: str
    probe_lon_a: float
    probe_lat_a: float
    probe_lon_b: float
    probe_lat_b: float
    # Hospitales reales (nombre, lat, lon) que el generador de escenarios coloca
    # antes de recurrir a posiciones aleatorias. Fuente: OpenStreetMap.
    hospitals: tuple[tuple[str, float, float], ...] = field(default_factory=tuple)
    # Dispersión (m) de la actividad alrededor del centro urbano: el
    # generador muestrea emergencias con una normal de esta desviación, así
    # la densidad cae hacia la periferia como en una ciudad real.
    urban_sigma_m: float = 2200.0

    @property
    def spawn(self) -> tuple[float, float]:
        return (self.spawn_lat, self.spawn_lon)

    @property
    def center(self) -> tuple[float, float]:
        return (self.center_lat, self.center_lon)

    @property
    def probe_url_path(self) -> str:
        """Tramo `route/v1/driving/...` para el probe de OSRM (lon,lat;lon,lat)."""
        return (
            f"route/v1/driving/"
            f"{self.probe_lon_a},{self.probe_lat_a};"
            f"{self.probe_lon_b},{self.probe_lat_b}"
        )

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "country": self.country,
            "timezone": self.timezone,
            "center": [self.center_lat, self.center_lon],
            "zoom": self.zoom,
            "spawn": [self.spawn_lat, self.spawn_lon],
        }


_DATA_DIR = Path(__file__).parent / "region_data"


@lru_cache(maxsize=16)
def _region_data(region_id: str) -> dict:
    path = _DATA_DIR / f"{region_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def region_places(region_id: str, key: str) -> list[tuple[str, float, float]]:
    """Lugares reales de la región (OpenStreetMap) como ``(nombre, lat, lon)``.

    ``key``: ``fuel_stations`` | ``ambulance_bases`` | ``fire_stations`` |
    ``police_stations``. Lista vacía si la región no tiene datos.
    """
    out: list[tuple[str, float, float]] = []
    for row in _region_data(region_id).get(key) or []:
        try:
            out.append((str(row.get("name") or ""), float(row["lat"]), float(row["lon"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _osrm_url(region: str, default_host: str, default_port: int) -> str:
    """URL OSRM resuelta vía env (override) o fallback `http://<host>:<port>`."""
    env = (os.environ.get(f"OSRM_URL_{region.upper()}") or "").strip()
    return env or f"http://{default_host}:{default_port}"


# ── Registro de regiones ──────────────────────────────────────────────────
# Centros y spawns elegidos junto al hospital de referencia de cada ciudad.
# Probe = dos puntos cercanos en calle real para validar el grafo OSRM.

REGIONS: dict[str, RegionConfig] = {
    "santiago": RegionConfig(
        id="santiago",
        name="Santiago de Compostela",
        country="España",
        timezone="Europe/Madrid",
        # Centro junto al Hospital Clínico Universitario (CHUS).
        center_lat=42.8710,
        center_lon=-8.5640,
        zoom=14,
        spawn_lat=42.8702,
        spawn_lon=-8.5648,
        pbf_url="https://download.geofabrik.de/europe/spain/galicia-latest.osm.pbf",
        osrm_url=_osrm_url("santiago", "osrm-santiago", 5000),
        probe_lon_a=-8.5640, probe_lat_a=42.8710,
        probe_lon_b=-8.5445, probe_lat_b=42.8800,
        hospitals=(
            ("Complexo Hospitalario Universitario de Santiago (CHUS)", 42.87002, -8.56561),
            ("Hospital HM La Esperanza", 42.87859, -8.55154),
            ("Hospital HM Rosaleda", 42.87186, -8.54633),
        ),
    ),
    "bogota": RegionConfig(
        id="bogota",
        name="Bogotá",
        country="Colombia",
        timezone="America/Bogota",
        center_lat=4.6286,
        center_lon=-74.0653,
        zoom=14,
        spawn_lat=4.6295,
        spawn_lon=-74.0656,
        pbf_url="https://download.bbbike.org/osm/bbbike/Bogota/Bogota.osm.pbf",
        osrm_url=_osrm_url("bogota", "osrm-bogota", 5000),
        probe_lon_a=-74.0653, probe_lat_a=4.6286,
        probe_lon_b=-74.0670, probe_lat_b=4.6310,
    ),
    "mexico": RegionConfig(
        id="mexico",
        name="Ciudad de México",
        country="México",
        timezone="America/Mexico_City",
        center_lat=19.4326,
        center_lon=-99.1332,
        zoom=14,
        spawn_lat=19.4335,
        spawn_lon=-99.1335,
        pbf_url="https://download.bbbike.org/osm/bbbike/MexicoCity/MexicoCity.osm.pbf",
        osrm_url=_osrm_url("mexico", "osrm-mexico", 5000),
        probe_lon_a=-99.1332, probe_lat_a=19.4326,
        probe_lon_b=-99.1300, probe_lat_b=19.4350,
    ),
}

DEFAULT_REGION_ID = (os.environ.get("DEFAULT_REGION") or "santiago").strip().lower()
if DEFAULT_REGION_ID not in REGIONS:
    DEFAULT_REGION_ID = "santiago"

_lock = threading.Lock()
_active_region_id: str = DEFAULT_REGION_ID


def list_regions() -> list[RegionConfig]:
    """Lista de regiones disponibles (orden estable: santiago, bogota, mexico)."""
    return list(REGIONS.values())


def get_active_region() -> RegionConfig:
    with _lock:
        return REGIONS[_active_region_id]


def get_active_region_id() -> str:
    with _lock:
        return _active_region_id


def set_active_region(region_id: str) -> RegionConfig:
    """Cambia la región activa. Lanza KeyError si el id no existe."""
    rid = (region_id or "").strip().lower()
    if rid not in REGIONS:
        raise KeyError(rid)
    global _active_region_id
    with _lock:
        _active_region_id = rid
    return REGIONS[rid]
