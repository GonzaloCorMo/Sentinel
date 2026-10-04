"""Comprueba que el escenario queda equilibrado con las emergencias automáticas.

Genera un escenario, activa las emergencias automáticas en modo equilibrado
y deja correr la simulación acelerada. Mide:
  - ocupación media de la flota (unidades no libres / total);
  - avisos esperando unidad (media y máximo).

Equilibrado = ocupación entre ~35 % y ~85 % y la cola no crece sin control.

Uso:  python scripts/check_balance.py [--minutes 5] [--units 6]
(minutos reales; a 20× son ~20 veces más minutos simulados).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

API = "http://localhost:8080"


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--units", type=int, default=6)
    args = ap.parse_args()

    call("POST", "/api/sim/generate-scenario", {
        "hospitals": 3, "gasStations": 3, "ambulances": args.units, "incidents": 0, "clearExisting": True,
    })
    call("POST", "/api/sim/training-mode", {"enabled": True})
    call("POST", "/api/sim/control", {"action": "play", "speedMultiplier": 20})
    t0 = time.time()
    busy_samples, pending_samples = [], []
    created = 0
    st = {}
    while time.time() - t0 < args.minutes * 60:
        time.sleep(5)
        st = call("GET", "/api/sim/state")
        ambs = st["ambulances"]
        busy = sum(1 for a in ambs if (a.get("missionPhase") or "idle") not in ("idle", "to_staging"))
        busy_samples.append(busy / max(1, len(ambs)))
        pending_samples.append(sum(1 for e in st["emergencies"] if e.get("status") == "pending"))
        created = len(st["emergencies"])
    call("POST", "/api/sim/training-mode", {"enabled": False})
    call("POST", "/api/sim/control", {"action": "play", "speedMultiplier": 1})

    sim_h = float(st.get("simTimeS") or 0) / 3600
    occ = sum(busy_samples) / max(1, len(busy_samples))
    # La primera media hora simulada es arranque (flota vacía): se mide la cola después.
    tail = pending_samples[len(pending_samples) // 4:]
    pend_avg = sum(tail) / max(1, len(tail))
    pend_max = max(tail or [0])
    print(f"Tiempo simulado: {sim_h:.1f} h · emergencias: {created} ({created / max(sim_h, 0.01):.1f}/h)")
    print(f"Ritmo equilibrado anunciado: {st.get('balancedRatePerHour')}/h para {len(st['ambulances'])} unidades")
    print(f"Ocupación media de la flota: {occ:.0%}")
    print(f"Avisos sin unidad: media {pend_avg:.1f}, máximo {pend_max}")
    ok = 0.35 <= occ <= 0.85 and pend_avg <= max(1.0, args.units * 0.25) and pend_max <= args.units
    print("Resultado:", "equilibrado" if ok else "DESEQUILIBRADO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
