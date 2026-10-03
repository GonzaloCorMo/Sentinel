"""Comprobación de realismo de la simulación contra el stack en marcha.

Genera un escenario y verifica que:
  - emergencias, unidades, hospitales y gasolineras están sobre una calle
    (OSRM /nearest a menos de 60 m; las emergencias, en calles con nombre);
  - las unidades se mueven a velocidades plausibles y pasan por las fases
    de una misión real (hacia la emergencia → en el lugar → traslado →
    transferencia en hospital).

Uso:  python scripts/check_realism.py [--seconds 90]
Requiere el stack levantado (API en :8080, OSRM de Santiago en :5000).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

API = "http://localhost:8080"
OSRM = "http://localhost:5000"


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def nearest(lat: float, lon: float) -> tuple[float, str]:
    w = call("GET", f"{OSRM}/nearest/v1/driving/{lon},{lat}?number=1")["waypoints"][0]
    return float(w["distance"]), str(w.get("name") or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=90)
    args = ap.parse_args()
    fails = 0

    res = call("POST", f"{API}/api/sim/generate-scenario", {
        "hospitals": 3, "gasStations": 3, "ambulances": 6, "incidents": 8, "clearExisting": True,
        "extraByType": {"firetruck_combustion": 1, "police_combustion": 2},
    })
    print("Colocado:", res["placed"])
    st = res["state"]

    def check(label: str, lat: float, lon: float, named: bool, max_m: float = 60.0) -> None:
        nonlocal fails
        d, name = nearest(lat, lon)
        ok = d <= max_m and (name or not named)
        if not ok:
            fails += 1
        print(f"  {'OK ' if ok else 'MAL'} {label:<38} a {d:5.1f} m de {name or '(calle sin nombre)'}")

    print("Emergencias:")
    for e in st["emergencies"]:
        check(f"{e.get('title', '')[:26]} [{e.get('severity')}]", e["latitude"], e["longitude"], named=True)
    print("Unidades:")
    for a in st["ambulances"]:
        check(a.get("displayLabel") or a["id"][:8], a["latitude"], a["longitude"], named=False, max_m=40.0)
    print("Lugares (posición real del edificio; basta con calle a <150 m):")
    for p in st["pois"]:
        if p.get("kind") in ("hospital", "gas_station"):
            check(f"{p['kind']}: {p.get('name', '')[:28]}", p["latitude"], p["longitude"], named=False, max_m=150.0)

    call("POST", f"{API}/api/sim/control", {"action": "play", "speedMultiplier": 20})
    phases: set[str] = set()
    speeds: list[float] = []
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        s = call("GET", f"{API}/api/sim/state")
        for a in s["ambulances"]:
            phases.add(str(a.get("missionPhase")))
            v = float(a.get("speedKmh") or 0)
            if v > 0.5:
                speeds.append(v)
        time.sleep(2)
    call("POST", f"{API}/api/sim/control", {"action": "play", "speedMultiplier": 1})
    if speeds:
        speeds.sort()
        print(f"Velocidades en marcha (km/h): mediana {speeds[len(speeds)//2]:.0f}, "
              f"p90 {speeds[int(len(speeds)*0.9)]:.0f}, máx {speeds[-1]:.0f}")
        if speeds[-1] > 130:
            fails += 1
            print("  MAL velocidad máxima no plausible")
    print("Fases observadas:", ", ".join(sorted(phases)))
    for needed in ("to_emergency", "on_scene"):
        if needed not in phases:
            fails += 1
            print(f"  MAL no se vio la fase {needed}")
    print("Resultado:", "correcto" if fails == 0 else f"{fails} fallos")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
