"""Intérprete del modo comando del chat: NL → tool call estructurada.

Convierte prompts del operador como *"muéstrame ambulancias con combustible
bajo cerca del hospital central"* en una llamada de herramienta (nombre +
args) que el frontend ejecuta aplicando filtros, focus, spawn, cambio de
modo IA, etc.

Pipeline:
    1. `_fast_interpret`: matcher regex determinista para patrones
       frecuentes. Barato y predecible.
    2. Si no matcha, el LLM devuelve el comando como JSON con esquema (`chat_completion_json`).
    3. Si el LLM falla, `_heuristic_fallback` con regex más amplios.

Contrato de salida (siempre dict):
    ``{"ok": bool, "command": str, "args": dict, "summary": str,
       "error": str | None}``
"""
from __future__ import annotations

import logging
from typing import Any

from . import llm_provider

_logger = logging.getLogger(__name__)


# ── Definición de herramientas (OpenAI tool format) ─────────────────────
SYSTEM_PROMPT = (
    "Eres el intérprete de comandos del dashboard Sentinel (gemelo digital "
    "de ambulancias). Recibes instrucciones del operador en español y decides "
    "qué herramienta ejecutar. Reglas:\n"
    "- Si el usuario pide filtrar/listar/mostrar unidades con ciertas propiedades → filter_units.\n"
    "- Si pide seleccionar/ir a/enfocar una unidad concreta → focus_unit.\n"
    "- Si pide pasar a modo autónomo / HITL → set_ai_mode.\n"
    "- Si pide añadir/desplegar/spawnear unidades → spawn_units.\n"
    "- Si pide crear/registrar incidencia → create_emergency.\n"
    "- Si pide limpiar/quitar filtros → reset_filters.\n"
    "- Si es pregunta general o no accionable → explain.\n"
    "Usa SOLO UNA herramienta por respuesta. Extrae parámetros del texto con precisión."
)


async def interpret(text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Traduce un comando en lenguaje natural a una tool-call.

    Orden: matcher determinista → LLM con tool_choice → heurística. Si el
    LLM no está disponible y no hay fast match, devuelve ``ok=False`` con
    error ``"llm_unavailable"``.

    Args:
        text: Comando del operador en español.
        context: Dict opcional con ``pois`` y ``entityTypes`` actuales;
            se inyecta como hint en el system prompt del LLM para que el
            modelo resuelva nombres concretos (ej. "Hospital Marañón").

    Returns:
        Dict con ``ok``, ``command``, ``args``, ``summary``, ``error``.
        El frontend usa ``summary`` para feedback inmediato al usuario.
    """
    # Intento 1: matcher determinista para patrones comunes. Más rápido y
    # fiable que el LLM para comandos frecuentes. Solo cae a LLM si no hay match.
    fast = _fast_interpret(text, context)
    if fast is not None:
        return fast

    if not llm_provider.is_llm_available():
        return {"ok": False, "command": "explain", "args": {"text": "LLM no disponible"}, "summary": "LLM no disponible", "error": "llm_unavailable"}

    ctx_hint = ""
    if context:
        pois = context.get("pois") or []
        types = context.get("entityTypes") or []
        poi_names = [f"{p.get('name')} ({p.get('kind')})" for p in pois[:10]]
        type_ids = [f"{t['id']}={t.get('name', '')}" for t in types if t.get("kind") == "vehicle"]
        ctx_hint = (
            "\n\nCONTEXTO ACTUAL DEL ESCENARIO:\n"
            f"- POIs disponibles: {', '.join(poi_names) or 'ninguno'}\n"
            f"- Tipos de vehículo: {', '.join(type_ids) or 'ninguno'}"
        )

    json_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": [
                    "filter_units", "focus_unit", "set_ai_mode", "spawn_units",
                    "create_emergency", "reset_filters", "explain",
                ],
            },
            "args": {"type": "object"},
        },
        "required": ["command", "args"],
    }
    schema_hint = (
        "\n\nResponde con un objeto JSON {\"command\": str, \"args\": object}.\n"
        "Comandos válidos y sus args:\n"
        "- filter_units: {fuelBelow?, fuelAbove?, batteryBelow?, hasPatient?, severity?, "
        "entityTypeId?, missionPhase?, poweredOff?, nearPoiKind?, nearPoiName?, maxDistanceKm?, sortBy?, limit?}\n"
        "  severity: critical|moderate|stable. missionPhase: idle (disponible) | to_emergency (hacia la emergencia) | "
        "on_scene (atendiendo en el lugar) | to_hospital (trasladando paciente) | at_hospital (transferencia en urgencias) | "
        "to_refuel (repostando) | to_staging (volviendo a base). Usa nearPoi* solo si se nombra un lugar concreto.\n"
        "- focus_unit: {query: str}\n"
        "- set_ai_mode: {mode: 'hitl'|'autonomous'}\n"
        "- spawn_units: {entityTypeId: str, count: int, nearPoiName?, latitude?, longitude?}\n"
        "- create_emergency: {title: str, latitude: number, longitude: number, description?, emergencyType?}\n"
        "- reset_filters: {}\n"
        "- explain: {text: str}\n"
        "Devuelve SOLO el JSON, sin texto adicional."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + ctx_hint + schema_hint},
        {"role": "user", "content": text},
    ]

    try:
        parsed = await llm_provider.chat_completion_json(
            messages, schema=json_schema, temperature=0.15, max_tokens=512,
        )
    except Exception as e:
        _logger.exception("LLM JSON command failed, fallback heurístico")
        return _heuristic_fallback(text, str(e))

    name = str(parsed.get("command") or "explain")
    raw_args = parsed.get("args") or {}
    if not isinstance(raw_args, dict):
        raw_args = {}
    args = _normalize_args(raw_args)
    summary = _summarize_command(name, args)
    return {"ok": name != "explain", "command": name, "args": args, "summary": summary, "error": None}


def _normalize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Normaliza el dict de args que devuelve el LLM.

    Corrige dos patologías frecuentes de modelos pequeños:
        1. Patrón ``{type:"number", value:"..."}`` al reflejar el schema
           en lugar de emitir el valor plano.
        2. Strings con ``"null"`` / ``"none"`` / ``""`` que deben tratarse
           como ausencia.

    Además coacciona tipos (float/int/bool) por nombre de campo.
    """
    out: dict[str, Any] = {}
    for k, v in args.items():
        # Desenvolver {type, value}
        if isinstance(v, dict) and "value" in v and "type" in v:
            v = v["value"]
        # Valor vacío / "null" / None
        if v is None or (isinstance(v, str) and v.strip().lower() in ("null", "none", "")):
            continue
        # Coerce por nombre de campo
        if k in ("fuelBelow", "fuelAbove", "batteryBelow", "latitude", "longitude", "maxDistanceKm"):
            try: v = float(v)
            except Exception: continue
        elif k in ("count", "limit"):
            try: v = int(float(v))
            except Exception: continue
        elif k in ("hasPatient", "poweredOff"):
            if isinstance(v, str):
                v = v.strip().lower() in ("true", "yes", "sí", "si", "1")
        out[k] = v
    return out


def _fast_interpret(text: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Matcher determinista: cubre comandos frecuentes sin invocar al LLM.

    Devuelve None si ningún patrón encaja, dejando el fallback al modelo.
    Soporta combinaciones (p.ej. "ambulancias con fuel<30 y paciente crítico").
    """
    import re

    low = text.lower().strip()
    if not low:
        return None

    # Reset / limpiar filtros
    if re.search(r"\b(reset|limpia(?:r)?|borra(?:r)?|quita(?:r)?)\s+(los\s+)?filtros?\b", low) or \
       re.search(r"\b(mostra(?:r)?\s+)?tod(?:a|o)s?\b(?!.+(con|de|menor|mayor))", low):
        return {"ok": True, "command": "reset_filters", "args": {}, "summary": "Filtros limpiados", "error": None}

    # Modo IA
    if re.search(r"\bmodo\s+aut[óo]nomo\b|\bia\s+aut[óo]noma\b", low):
        return {"ok": True, "command": "set_ai_mode", "args": {"mode": "autonomous"}, "summary": "IA autónoma", "error": None}
    if re.search(r"\bmodo\s+hitl\b|\bmodo\s+(humano|manual)\b|\baprobaci[óo]n\s+manual\b", low):
        return {"ok": True, "command": "set_ai_mode", "args": {"mode": "hitl"}, "summary": "IA con aprobación", "error": None}

    # Filter units: acumulamos args según keywords presentes
    args: dict[str, Any] = {}

    # fuel < N / batería > N. Tolera "mayor o igual a", ">=", "menos de", etc.
    # `[^\d]{0,15}?` come hasta 15 chars no-dígito (palabras de comparación)
    # antes del número, evitando matches a párrafos.
    for field_rx, fld_lt, fld_gt in (
        (r"(?:fuel|combust\w*|energ[íi]a|gasolina)", "fuelBelow", "fuelAbove"),
        (r"(?:bater[íi]a|carga)", "batteryBelow", None),
    ):
        m = re.search(field_rx + r"\s*(?:<=?|menor(?:\s+(?:o\s+)?igual)?|menos|bajo|por\s+debajo\s+de|≤)[^\d]{0,15}?(\d+)", low)
        if m and fld_lt:
            args[fld_lt] = float(m.group(1))
        m = re.search(field_rx + r"\s*(?:>=?|mayor(?:\s+(?:o\s+)?igual)?|m[áa]s|por\s+encima\s+de|superior(?:\s+(?:o\s+)?igual)?|≥)[^\d]{0,15}?(\d+)", low)
        if m and fld_gt:
            args[fld_gt] = float(m.group(1))
        # "combustible bajo" sin número = <25
        if fld_lt and fld_lt not in args and re.search(field_rx + r"\s+(bajo|cr[íi]tico)\b", low):
            args[fld_lt] = 25.0

    # Paciente / severidad
    if re.search(r"\b(con|lleva(?:ndo)?)\s+paciente\b", low):
        args["hasPatient"] = True
    if re.search(r"\bsin\s+paciente\b|\bvac[íi]as?\b", low):
        args["hasPatient"] = False
    if re.search(r"\bcr[íi]tic[oa]s?\b", low):
        args["severity"] = "critical"
    elif re.search(r"\bmoderad[oa]s?\b", low):
        args["severity"] = "moderate"
    elif re.search(r"\bestable[s]?\b", low):
        args["severity"] = "stable"

    # Fase misión
    phase_map = {
        "on_scene": r"\ben\s+el\s+lugar\b|\ben\s+escena\b|\batendiendo\b|\bon\s+scene\b",
        "at_hospital": r"\btransferencia\b|\ben\s+urgencias\b|\bentregando\b",
        "to_hospital": r"\b(?:de\s+camino|hacia|rumbo)\s+al?\s+hospital\b|\btransportando\b|\btrasladando\b",
        "to_emergency": r"\b(?:de\s+camino|hacia|rumbo|yendo)\s+(?:a\s+)?(?:la\s+|una\s+)?(?:emergencia|incident\w*|aviso)|\ben\s+camino\b",
        "to_refuel": r"\b(?:de\s+camino|hacia)\s+(?:gasolinera|repostaj)|\brepostando\b",
        "to_staging": r"\bvolviendo\s+a\s+(?:la\s+)?base\b|\bregresando\b",
        "idle": r"\b(inactiv[oa]s?|en\s+espera|ociosas?|idle|disponibles?|libres?)\b",
    }
    for ph, rx in phase_map.items():
        if re.search(rx, low):
            args["missionPhase"] = ph
            break

    # Apagadas
    if re.search(r"\b(apagad[oa]s?|off|sin\s+encender)\b", low):
        args["poweredOff"] = True

    # Tipo de vehículo por nombre en el catálogo
    types = (context or {}).get("entityTypes") or []
    for t in types:
        if t.get("kind") != "vehicle":
            continue
        name = (t.get("name") or "").lower().strip()
        if not name:
            continue
        # match palabra completa por nombre del tipo
        if re.search(r"\b" + re.escape(name) + r"s?\b", low):
            args["entityTypeId"] = t["id"]
            break

    # Si se capturaron filtros → devolver filter_units
    if args:
        return {
            "ok": True,
            "command": "filter_units",
            "args": args,
            "summary": _summarize_command("filter_units", args),
            "error": None,
        }

    # Focus explícito: "foco en X", "selecciona X", "enfoca X"
    m = re.search(r"\b(?:foco\s+en|enfoca(?:r)?|selecciona(?:r)?|ir\s+a)\s+(.+)$", low)
    if m:
        q = m.group(1).strip().strip(".!?¡¿\"'")
        return {"ok": True, "command": "focus_unit", "args": {"query": q}, "summary": f"Enfocando '{q}'", "error": None}

    # Spawn units: "despliega/añade/crea N <tipo>"
    m = re.search(r"\b(?:despliega|despliegue|a[ñn]ade|crea(?:r)?|spawn(?:ea)?|genera(?:r)?)\s+(\d+)\s+([\wáéíóúñ_-]+)", low)
    if m:
        n = int(m.group(1))
        kw = m.group(2).strip()
        type_id = None
        types = (context or {}).get("entityTypes") or []
        for t in types:
            if t.get("kind") != "vehicle":
                continue
            tname = (t.get("name") or "").lower()
            tid = (t.get("id") or "").lower()
            if kw in tname or kw in tid:
                type_id = t["id"]
                break
        if type_id is None:
            keyword_aliases = {
                "ambulancia": "ambulance_combustion",
                "ambulancias": "ambulance_combustion",
                "polic[ií]a": "police_combustion",
                "polic[ií]as": "police_combustion",
                "bombero": "firetruck_combustion",
                "bomberos": "firetruck_combustion",
                "dron": "drone_unique",
                "drones": "drone_unique",
            }
            for alias, fallback_id in keyword_aliases.items():
                if re.search(r"\b" + alias + r"\b", kw):
                    type_id = fallback_id
                    break
        if type_id:
            args = {"entityTypeId": type_id, "count": min(20, max(1, n))}
            return {"ok": True, "command": "spawn_units", "args": args,
                    "summary": _summarize_command("spawn_units", args), "error": None}

    # Create emergency: "crea(r) (una) emergencia/incidencia (de tipo X) en lat,lon" o
    # "crea emergencia en <nombre POI>"
    m = re.search(r"\b(?:crea(?:r)?|registra(?:r)?|nueva)\s+(?:una\s+)?(?:emergencia|incidencia|incidente)\b(.*)", low)
    if m:
        rest = m.group(1).strip()
        latlon = re.search(r"(-?\d+\.\d+)\s*[,\s]\s*(-?\d+\.\d+)", rest)
        em_args: dict[str, Any] = {"title": rest[:80] or "Incidencia"}
        if latlon:
            em_args["latitude"] = float(latlon.group(1))
            em_args["longitude"] = float(latlon.group(2))
        else:
            poi_match = re.search(r"\ben\s+(?:el|la|los|las)?\s*(.+)$", rest)
            if poi_match:
                target = poi_match.group(1).strip().strip(".!?¡¿\"'")
                pois = (context or {}).get("pois") or []
                for p in pois:
                    pname = (p.get("name") or "").lower()
                    if target and target in pname:
                        em_args["latitude"] = float(p.get("latitude"))
                        em_args["longitude"] = float(p.get("longitude"))
                        em_args["title"] = f"Incidencia en {p.get('name')}"
                        break
        sev_match = re.search(r"\b(medica|m[eé]dica|trauma|altercado|altercaci[oó]n|mass[_ ]?casualty|masivo)\b", low)
        if sev_match:
            tok = sev_match.group(1)
            if "altercad" in tok or "altercaci" in tok:
                em_args["emergencyType"] = "altercation"
            elif "mass" in tok or "masivo" in tok:
                em_args["emergencyType"] = "mass_casualty"
            else:
                em_args["emergencyType"] = "medical"
        if "latitude" in em_args and "longitude" in em_args:
            return {"ok": True, "command": "create_emergency", "args": em_args,
                    "summary": _summarize_command("create_emergency", em_args), "error": None}

    return None


def _heuristic_fallback(text: str, err: str) -> dict[str, Any]:
    """Si el tool-calling del modelo falla, intenta interpretar con regex simples."""
    low = text.lower()
    args: dict[str, Any] = {}
    cmd = "explain"
    summary = "No pude interpretar el comando con IA."
    import re
    m = re.search(r"(?:fuel|combust\w*)\s*(?:<|menor|menos|bajo|por debajo de?)\s*(\d+)", low)
    if m:
        args["fuelBelow"] = float(m.group(1))
        cmd = "filter_units"
        summary = f"Mostrando unidades con combustible < {int(args['fuelBelow'])}%"
    elif "con paciente" in low or "llevando paciente" in low:
        args["hasPatient"] = True
        cmd = "filter_units"
        summary = "Mostrando unidades con paciente a bordo"
    elif "cr[ií]tic" in low or "cr%C3%ADtic" in low:
        args["severity"] = "critical"
        cmd = "filter_units"
        summary = "Mostrando unidades con paciente crítico"
    elif "modo aut" in low:
        args["mode"] = "autonomous"
        cmd = "set_ai_mode"
        summary = "Cambiando IA a modo autónomo"
    elif "modo hitl" in low or "modo humano" in low:
        args["mode"] = "hitl"
        cmd = "set_ai_mode"
        summary = "Cambiando IA a modo HITL"
    elif "reset" in low or "limpia" in low or "quita filtro" in low:
        cmd = "reset_filters"
        summary = "Limpiando filtros"
    return {"ok": cmd != "explain", "command": cmd, "args": args, "summary": summary, "error": err}


# Valores internos → texto para el operador.
_SEVERITY_ES = {"critical": "crítica", "moderate": "moderada", "stable": "estable"}
_PHASE_ES = {
    "idle": "disponibles", "to_emergency": "hacia la emergencia", "on_scene": "en el lugar",
    "to_hospital": "trasladando paciente", "at_hospital": "en transferencia", "to_refuel": "repostando",
    "refueling": "repostando", "to_staging": "volviendo a base",
}


def _summarize_command(name: str, args: dict[str, Any]) -> str:
    """Formatea feedback humano en una frase a partir de ``command + args``."""
    if name == "filter_units":
        bits = []
        if "fuelBelow" in args: bits.append(f"combustible < {args['fuelBelow']}%")
        if "fuelAbove" in args: bits.append(f"combustible > {args['fuelAbove']}%")
        if "batteryBelow" in args: bits.append(f"batería < {args['batteryBelow']}%")
        if args.get("hasPatient"): bits.append("con paciente")
        if "severity" in args: bits.append(f"gravedad {_SEVERITY_ES.get(str(args['severity']), args['severity'])}")
        if "entityTypeId" in args: bits.append(f"tipo {args['entityTypeId']}")
        if "missionPhase" in args: bits.append(_PHASE_ES.get(str(args["missionPhase"]), f"fase {args['missionPhase']}"))
        if args.get("poweredOff") is True: bits.append("en reserva")
        if "nearPoiKind" in args or "nearPoiName" in args:
            target = args.get("nearPoiName") or args.get("nearPoiKind")
            km = args.get("maxDistanceKm", 2)
            bits.append(f"a ≤ {km} km de «{target}»")
        if "sortBy" in args: bits.append(f"orden: {args['sortBy']}")
        if not bits: return "Mostrando todas las unidades"
        return "Filtrando: " + ", ".join(bits)
    if name == "focus_unit":
        return f"Enfocando unidad '{args.get('query', '')}'"
    if name == "set_ai_mode":
        return f"IA → modo {args.get('mode')}"
    if name == "spawn_units":
        return f"Desplegando {args.get('count', 1)} × {args.get('entityTypeId')}"
    if name == "create_emergency":
        return f"Creando incidencia: {args.get('title', '—')}"
    if name == "reset_filters":
        return "Filtros limpiados"
    if name == "explain":
        return args.get("text") or "—"
    return name
