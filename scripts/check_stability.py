"""Comprueba que las unidades se mueven sin oscilar.

Muestrea el estado durante un rato y verifica:
  - una unidad parada (sin ruta: libre, en el lugar, en transferencia,
    repostando) no se desplaza ni marca velocidad;
  - una unidad en ruta no retrocede: su avance a lo largo de la ruta
    (routeProgressM) solo crece mientras la ruta sea la misma, y su
    posición no da saltos imposibles.

Uso:  python scripts/check_stability.py [--seconds 60] [--speed 5]
Requiere el stack levantado. Genera un escenario nuevo.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request

API = "http://localhost:8080"


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dn = (b[0] - a[0]) * 111_320
    de = (b[1] - a[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dn, de)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--speed", type=float, default=5)
    ap.add_argument("--no-scenario", action="store_true")
    args = ap.parse_args()

    if not args.no_scenario:
        call("POST", "/api/sim/generate-scenario", {
            "hospitals": 3, "gasStations": 3, "ambulances": 6, "incidents": 6, "clearExisting": True,
        })
    call("POST", "/api/sim/control", {"action": "play", "speedMultiplier": args.speed})

    prev: dict[str, dict] = {}
    still_moves: dict[str, float] = {}
    backwards: dict[str, int] = {}
    jumps: dict[str, float] = {}
    # Destinos de zona de espera por unidad: ir y volver entre dos es un vaivén.
    staging_dest: dict[str, list[str]] = {}
    samples = 0
    t0 = time.time()
    last_t = None
    while time.time() - t0 < args.seconds:
        now = time.time()
        st = call("GET", "/api/sim/state")
        samples += 1
        for a in st["ambulances"]:
            aid = a["id"]
            label = a.get("displayLabel") or aid[:6]
            pos = (float(a["latitude"]), float(a["longitude"]))
            route = a.get("routeCoords") or []
            key = (len(route), tuple(route[0]) if route else None, tuple(route[-1]) if route else None)
            p = prev.get(aid)
            if p is not None:
                moved = dist_m(p["pos"], pos)
                stationary_now = not route and not p["route"]
                if stationary_now and p["phase"] == a.get("missionPhase"):
                    if moved > 0.5 or float(a.get("speedKmh") or 0) > 0.5:
                        still_moves[label] = max(still_moves.get(label, 0.0), moved)
                if route and p["key"] == key:
                    if float(a.get("routeProgressM") or 0) + 0.5 < p["prog"]:
                        backwards[label] = backwards.get(label, 0) + 1
                    # 130 km/h al multiplicador de velocidad, con margen.
                    dt = now - (last_t or now)
                    if dt > 0 and moved > 36.0 * args.speed * dt * 1.5 + 5:
                        jumps[label] = max(jumps.get(label, 0.0), moved)
            if a.get("missionPhase") == "to_staging" and a.get("stagingHospitalId"):
                seq = staging_dest.setdefault(label, [])
                if not seq or seq[-1] != a["stagingHospitalId"]:
                    seq.append(a["stagingHospitalId"])
            prev[aid] = {"pos": pos, "route": bool(route), "key": key,
                         "prog": float(a.get("routeProgressM") or 0), "phase": a.get("missionPhase")}
        last_t = now
        time.sleep(1.0)
    call("POST", "/api/sim/control", {"action": "play", "speedMultiplier": 1})

    print(f"Muestras: {samples}")
    fails = 0
    pingpong = {k: len(v) for k, v in staging_dest.items() if len(v) >= 3 and len(set(v)) < len(v)}
    for title, d, fmt in (
        ("Unidades paradas que se mueven", still_moves, "{:.1f} m"),
        ("Unidades que retroceden en su ruta", backwards, "{} veces"),
        ("Saltos de posición imposibles", jumps, "{:.0f} m"),
        ("Unidades que van y vienen entre zonas de espera", pingpong, "{} cambios"),
    ):
        if d:
            fails += len(d)
            print(f"MAL {title}: " + ", ".join(f"{k} ({fmt.format(v)})" for k, v in d.items()))
        else:
            print(f"OK  {title}: ninguna")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
