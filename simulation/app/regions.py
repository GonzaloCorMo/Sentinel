"""Registro central de regiones / mapas disponibles para el escenario.

Cada región describe el extracto OSRM al que apuntar, el centro/zoom del
mapa por defecto y las coordenadas de spawn que usa el motor cuando bootstrappea
flota / POIs / emergencias automaticas. La región activa es estado global del
proceso: cambiarla resetea la simulación (`engine.reset_simulation`) porque las
coordenadas de POIs/ambulancias previas no son válidas en el nuevo grafo.

Cada OSRM corre en su propio contenedor docker (osrm-aruba, osrm-madrid, ...);
solo el de la región activa recibe queries — los demás quedan idle.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass


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


def _osrm_url(region: str, default_host: str, default_port: int) -> str:
    """URL OSRM resuelta vía env (override) o fallback `http://<host>:<port>`."""
    env = (os.environ.get(f"OSRM_URL_{region.upper()}") or "").strip()
    return env or f"http://{default_host}:{default_port}"


# ── Registro de regiones ──────────────────────────────────────────────────
# Centros y spawns elegidos junto al hospital de referencia de cada ciudad.
# Probe = dos puntos cercanos en calle real para validar el grafo OSRM.

REGIONS: dict[str, RegionConfig] = {
    "aruba": RegionConfig(
        id="aruba",
        name="Aruba (Oranjestad)",
        country="Aruba",
        timezone="America/Aruba",
        center_lat=12.5398,
        center_lon=-70.0344,
        zoom=14,
        spawn_lat=12.5407,
        spawn_lon=-70.0347,
        pbf_url="https://download.openstreetmap.fr/extracts/central-america/aruba.osm.pbf",
        osrm_url=_osrm_url("aruba", "osrm-aruba", 5000),
        probe_lon_a=-70.0344, probe_lat_a=12.5398,
        probe_lon_b=-70.0316, probe_lat_b=12.5226,
    ),
    "madrid": RegionConfig(
        id="madrid",
        name="Madrid (Las Rozas)",
        country="España",
        timezone="Europe/Madrid",
        center_lat=40.4933,
        center_lon=-3.8742,
        zoom=14,
        spawn_lat=40.4942,
        spawn_lon=-3.8745,
        pbf_url="https://download.bbbike.org/osm/bbbike/Madrid/Madrid.osm.pbf",
        osrm_url=_osrm_url("madrid", "osrm-madrid", 5000),
        probe_lon_a=-3.8742, probe_lat_a=40.4933,
        probe_lon_b=-3.8755, probe_lat_b=40.4945,
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

DEFAULT_REGION_ID = (os.environ.get("DEFAULT_REGION") or "aruba").strip().lower()
if DEFAULT_REGION_ID not in REGIONS:
    DEFAULT_REGION_ID = "aruba"

_lock = threading.Lock()
_active_region_id: str = DEFAULT_REGION_ID


def list_regions() -> list[RegionConfig]:
    """Lista de regiones disponibles (orden estable: aruba, madrid, bogota, mexico)."""
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
