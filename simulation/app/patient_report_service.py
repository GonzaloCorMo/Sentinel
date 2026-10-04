"""Informe clínico de un paciente simulado, generado con la IA local.

Pensado para la transferencia al hospital: formato ISBAR (Identificación,
Situación, Antecedentes, Evaluación, Recomendación), hallazgos, alertas y
acciones orientativas. Todos los datos son simulados y ficticios.

Para que un modelo pequeño no invente cifras, el código calcula antes los
hechos (constantes fuera de rango, tendencias, tiempo de atención) y el LLM
solo redacta y prioriza a partir de ellos. Si el LLM no responde, se devuelve
un informe determinista con los mismos hechos.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import llm_provider
from .supabase_client import get_supabase

_logger = logging.getLogger(__name__)

DISCLAIMER = "Informe orientativo generado por IA a partir de datos simulados. No sustituye el criterio médico."

# (clave, etiqueta, unidad, crítico[min,max], aviso[min,max])
_RANGES: tuple[tuple[str, str, str, tuple[float, float], tuple[float, float]], ...] = (
    ("hr", "Frecuencia cardiaca", "lpm", (50, 120), (60, 100)),
    ("spo2", "SpO₂", "%", (90, 101), (94, 101)),
    ("sys", "TA sistólica", "mmHg", (90, 180), (100, 160)),
    ("rr", "Frecuencia respiratoria", "rpm", (8, 25), (12, 20)),
    ("temp", "Temperatura", "°C", (35.5, 39.0), (36.0, 38.0)),
    ("gcs", "Glasgow", "/15", (9, 15), (15, 15)),
    ("glucose", "Glucemia", "mg/dL", (60, 250), (70, 180)),
)

_SEVERITY_ES = {"critical": "crítico", "moderate": "moderado", "stable": "estable"}
_PHASE_ES = {"on_scene": "en el lugar", "to_hospital": "en traslado al hospital", "at_hospital": "en transferencia en urgencias"}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def compute_facts(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hechos objetivos a partir de las constantes simuladas."""
    hist = ctx.get("vitals") or []
    cur = ctx.get("current") or {}
    bp = cur.get("bloodPressureMmhg") or {}
    last = {
        "hr": _num(cur.get("heartRateBpm")), "sys": _num(bp.get("systolic")), "dia": _num(bp.get("diastolic")),
        "spo2": _num(cur.get("spo2Pct")), "rr": _num(cur.get("respiratoryRatePerMin")), "temp": _num(cur.get("bodyTempC")),
        "gcs": _num(cur.get("gcsScore")), "glucose": _num(cur.get("bloodGlucoseMgDl")),
    }
    if not any(v is not None for v in last.values()) and hist:
        last = {k: _num(hist[-1].get(k)) for k in last}
    # Precisión clínica: enteros salvo la temperatura.
    last = {k: (round(v, 1) if k == "temp" else float(round(v))) if v is not None else None for k, v in last.items()}
    first = hist[0] if hist else {}
    abnormal: list[dict[str, Any]] = []
    for key, label, unit, crit, warn in _RANGES:
        v = last.get(key)
        if v is None:
            continue
        level = "crítico" if (v < crit[0] or v > crit[1]) else "alterado" if (v < warn[0] or v > warn[1]) else None
        trend = None
        f = _num(first.get(key))
        if f is not None and len(hist) >= 3:
            delta = v - f
            if abs(delta) >= max(2.0, abs(f) * 0.08):
                trend = "empeora" if (level and ((v < warn[0] and delta < 0) or (v > warn[1] and delta > 0))) else ("sube" if delta > 0 else "baja")
        if level or trend:
            abnormal.append({"label": label, "value": v, "unit": unit, "level": level or "normal", "trend": trend})
    ecg = cur.get("ecgRhythm") or (hist[-1].get("ecg") if hist else None)
    return {
        "last": last,
        "ecg": ecg,
        "abnormal": abnormal,
        "news2": cur.get("news2Score"),
        "pain": cur.get("painScore"),
        "spco": cur.get("spcoPct"),
        "samples": len(hist),
    }


def _fmt(v: float | None, unit: str = "") -> str:
    if v is None:
        return "—"
    s = f"{v:.1f}" if abs(v - round(v)) > 0.05 else f"{int(round(v))}"
    return f"{s} {unit}".strip()


def _vitals_line(facts: dict[str, Any]) -> str:
    l = facts["last"]
    return (f"FC {_fmt(l['hr'], 'lpm')}, TA {_fmt(l['sys'])}/{_fmt(l['dia'])} mmHg, SpO₂ {_fmt(l['spo2'], '%')}, "
            f"FR {_fmt(l['rr'], 'rpm')}, T {_fmt(l['temp'], '°C')}, Glasgow {_fmt(l['gcs'])}/15, "
            f"glucemia {_fmt(l['glucose'], 'mg/dL')}, ritmo {facts.get('ecg') or '—'}")


def _alerts(facts: dict[str, Any]) -> list[str]:
    out = [f"{a['label']} {_fmt(a['value'], a['unit'])}: valor crítico" for a in facts["abnormal"] if a["level"] == "crítico"]
    if facts.get("ecg") == "Fibrilacion":
        out.insert(0, "Ritmo de fibrilación en el monitor")
    return out


def fallback_report(ctx: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Informe sin LLM: los mismos hechos, redactados con plantillas."""
    sev = _SEVERITY_ES.get(str(ctx.get("severity")), "sin clasificar")
    findings = [
        f"{a['label']}: {_fmt(a['value'], a['unit'])} ({a['level']}{', ' + a['trend'] if a['trend'] else ''})"
        for a in facts["abnormal"]
    ] or ["Constantes dentro de rango en la última lectura."]
    alerts = _alerts(facts)
    return {
        "summary": f"Paciente {sev} atendido por {ctx['conditionTitle'].lower()}. {_vitals_line(facts)}.",
        "isbar": {
            "identification": f"Unidad {ctx['unit']}, paciente {sev}, {_PHASE_ES.get(str(ctx.get('phase')), str(ctx.get('phase')))}.",
            "situation": f"{ctx['conditionTitle']}. {ctx.get('callDescription') or ''}".strip(),
            "background": f"Aviso en {ctx.get('street') or 'vía urbana'}. {ctx.get('minutesWithPatient', 0):.0f} min de atención.",
            "assessment": _vitals_line(facts) + ".",
            "recommendation": (f"Traslado a {ctx['hospital']}." if ctx.get("hospital") else "Valorar traslado.")
            + (" Prealertar a urgencias." if alerts else ""),
        },
        "findings": findings,
        "alerts": alerts,
        "actions": [],
    }


_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 frases en español: estado actual y prioridad."},
        "isbar": {
            "type": "object",
            "properties": {
                "identification": {"type": "string"},
                "situation": {"type": "string"},
                "background": {"type": "string"},
                "assessment": {"type": "string"},
                "recommendation": {"type": "string"},
            },
            "required": ["identification", "situation", "background", "assessment", "recommendation"],
        },
        "findings": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "actions": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                    "description": "Medidas orientativas para el equipo y el hospital receptor."},
    },
    "required": ["summary", "isbar", "findings", "actions"],
}


async def _protocol_snippets(query: str) -> list[str]:
    sb = get_supabase()
    if sb is None:
        return []
    try:
        emb = await llm_provider.embed_text(query)
        res = sb.rpc("match_protocols", {"query_embedding": emb, "match_threshold": 0.0, "match_count": 2}).execute()
        return [str(r.get("content") or "")[:700] for r in (res.data or []) if r.get("content")]
    except Exception:
        _logger.exception("Búsqueda de protocolos para el informe de paciente fallida")
        return []


async def generate_patient_report(ctx: dict[str, Any]) -> dict[str, Any]:
    facts = compute_facts(ctx)
    base = {
        "unit": ctx["unit"],
        "condition": ctx["conditionTitle"],
        "severity": ctx.get("severity"),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "facts": facts,
    }
    if not llm_provider.is_llm_available():
        return {**base, **fallback_report(ctx, facts), "source": "rules"}

    protocols = await _protocol_snippets(f"{ctx['conditionTitle']} actuación prehospitalaria")
    series = ctx.get("vitals") or []
    t0 = series[0]["t"] if series else 0.0
    trend_rows = [
        f"min {(v['t'] - t0) / 60:.0f}: FC {v.get('hr')} TA {v.get('sys')}/{v.get('dia')} SpO2 {v.get('spo2')} FR {v.get('rr')} GCS {v.get('gcs')}"
        for v in series[-8:]
    ]
    abnormal = "; ".join(
        f"{a['label']} {_fmt(a['value'], a['unit'])} ({a['level']}{', ' + a['trend'] if a['trend'] else ''})"
        for a in facts["abnormal"]
    ) or "ninguna constante fuera de rango"
    prompt = (
        "Redacta un informe de transferencia de un paciente para el hospital receptor, en español, formato ISBAR.\n"
        "Reglas estrictas:\n"
        "- Usa SOLO estos datos. No inventes valores, edades, antecedentes, fármacos administrados ni pruebas.\n"
        "- No añadas diagnósticos, tipos de shock ni causas que no aparezcan aquí; describe lo observado.\n"
        "- Las cifras deben coincidir exactamente con las de 'Constantes actuales'.\n"
        "- Frases cortas y claras para un médico que recibe al paciente.\n\n"
        f"Unidad: {ctx['unit']} · fase: {_PHASE_ES.get(str(ctx.get('phase')), ctx.get('phase'))}\n"
        f"Afección: {ctx['conditionTitle']} · gravedad: {_SEVERITY_ES.get(str(ctx.get('severity')), 'sin clasificar')}\n"
        f"Aviso: {ctx.get('callDescription') or '—'} · lugar: {ctx.get('street') or '—'}\n"
        f"Hospital de destino: {ctx.get('hospital') or 'sin asignar'} · minutos de atención: {ctx.get('minutesWithPatient', 0):.0f}\n"
        f"Constantes actuales: {_vitals_line(facts)}\n"
        f"NEWS2: {facts.get('news2')} · dolor (0-10): {facts.get('pain')}\n"
        f"Fuera de rango / tendencias: {abnormal}\n"
        f"Evolución reciente: {' | '.join(trend_rows) or 'sin serie'}\n"
        + ("\nProtocolos de referencia:\n" +"\n---\n".join(protocols) if protocols else "")
        + "\n\nEn 'actions' da medidas orientativas y prudentes (vigilancia, preparación del hospital, prealerta)."
    )
    try:
        data = await llm_provider.chat_completion_json(
            [
                {"role": "system", "content": "Eres un médico de emergencias extrahospitalarias que redacta transferencias claras y prudentes."},
                {"role": "user", "content": prompt},
            ],
            schema=_SCHEMA,
            temperature=0.1,
            max_tokens=900,
        )
        isbar = data.get("isbar") or {}
        report = {
            "summary": str(data.get("summary") or "").strip(),
            "isbar": {k: str(isbar.get(k) or "").strip() for k in ("identification", "situation", "background", "assessment", "recommendation")},
            "findings": [str(x) for x in (data.get("findings") or [])][:6],
            # Las alertas no las decide el modelo: solo valores críticos y ritmos graves.
            "alerts": _alerts(facts),
            "actions": [str(x) for x in (data.get("actions") or [])][:6],
        }
        if not report["summary"] or not any(report["isbar"].values()):
            raise ValueError("respuesta incompleta del modelo")
        return {**base, **report, "source": "llm"}
    except Exception:
        _logger.exception("Informe de paciente: el LLM falló, se usa la versión determinista")
        return {**base, **fallback_report(ctx, facts), "source": "rules"}
