"""Catálogo de emergencias realistas para la generación automática.

Cada entrada describe un tipo de llamada como las que recibe un 112/061:
frecuencia relativa, cómo cambia de noche, reparto de gravedad, tiempo
medio de asistencia en el lugar y probabilidad de que el paciente acabe
trasladado al hospital. Las cifras son aproximaciones razonables a la
demanda de un servicio de emergencias sanitarias urbano en España, no
datos oficiales.

El ``type`` coincide con las claves que la interfaz sabe traducir
(``emergency_type.*``): medical, trauma, fire, hazmat, flood, altercation,
mass_casualty.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

Severity = str  # "critical" | "high" | "medium" | "low"


@dataclass(frozen=True)
class EmergencyKind:
    key: str
    type: str
    title: str
    weight: float
    # Pesos de gravedad (critical, high, medium, low).
    severity: tuple[float, float, float, float]
    on_scene_min: float
    transport_p: float
    descriptions: tuple[str, ...]
    # Multiplicador de frecuencia entre las 22:00 y las 07:00.
    night_factor: float = 1.0
    # Grupo de edad típico del paciente: "elderly" | "adult" | "young" | "child" | "any".
    age: str = "any"
    # Preferir avenidas y carreteras (accidentes de tráfico).
    major_road: bool = False
    extra: dict = field(default_factory=dict)


CATALOG: tuple[EmergencyKind, ...] = (
    # ── Médicas ────────────────────────────────────────────────────────
    EmergencyKind("chest_pain", "medical", "Dolor torácico", 9.0, (0.2, 0.5, 0.3, 0.0), 18, 0.9,
                  ("{who} con dolor opresivo en el pecho desde hace {mins} minutos, sudoroso.",
                   "{who} refiere dolor en el pecho que se irradia al brazo izquierdo."), age="elderly"),
    EmergencyKind("cardiac_arrest", "medical", "Parada cardiorrespiratoria", 1.5, (1.0, 0.0, 0.0, 0.0), 35, 0.55,
                  ("{who} inconsciente, no respira. Un familiar inicia maniobras de reanimación.",
                   "{who} se desploma en la calle y no responde. Testigos comienzan RCP."), age="elderly"),
    EmergencyKind("breathing", "medical", "Dificultad respiratoria", 8.0, (0.1, 0.5, 0.4, 0.0), 16, 0.8,
                  ("{who} con mucha dificultad para respirar, labios morados.",
                   "{who} con antecedentes de EPOC, se ahoga y no mejora con su inhalador."), age="elderly"),
    EmergencyKind("stroke", "medical", "Síntomas de ictus", 4.0, (0.5, 0.5, 0.0, 0.0), 15, 0.98,
                  ("{who} con la boca torcida y sin fuerza en el brazo derecho desde hace {mins} minutos.",
                   "{who} habla con dificultad y no entiende lo que se le dice."), age="elderly"),
    EmergencyKind("syncope", "medical", "Pérdida de conocimiento", 8.0, (0.0, 0.3, 0.6, 0.1), 15, 0.65,
                  ("{who} se ha desmayado; ahora está consciente pero muy mareado.",
                   "{who} perdió el conocimiento unos segundos en un comercio."), age="any"),
    EmergencyKind("elderly_fall", "medical", "Caída en domicilio", 10.0, (0.0, 0.1, 0.5, 0.4), 20, 0.6,
                  ("{who} se ha caído en casa y no puede levantarse; dolor en la cadera.",
                   "{who} vive sola, la encuentra una vecina en el suelo."), age="elderly"),
    EmergencyKind("seizure", "medical", "Crisis convulsiva", 3.0, (0.0, 0.5, 0.5, 0.0), 15, 0.7,
                  ("{who} está convulsionando; es epiléptico conocido.",
                   "{who} sufre una convulsión que dura más de {mins} minutos."), age="any"),
    EmergencyKind("diabetic", "medical", "Descompensación diabética", 3.0, (0.0, 0.3, 0.6, 0.1), 15, 0.4,
                  ("{who} diabético, confuso y sudoroso; glucemia muy baja.",
                   "{who} con diabetes, desorientado, la familia no consigue que coma."), age="elderly"),
    EmergencyKind("abdominal", "medical", "Dolor abdominal intenso", 5.0, (0.0, 0.2, 0.6, 0.2), 14, 0.75,
                  ("{who} con dolor abdominal muy intenso y vómitos.",
                   "{who} con dolor en la parte baja del abdomen que no cede."), age="adult"),
    EmergencyKind("intoxication", "medical", "Intoxicación", 3.0, (0.0, 0.3, 0.5, 0.2), 15, 0.7,
                  ("{who} con intoxicación etílica, apenas responde.",
                   "{who} ha tomado una cantidad indeterminada de pastillas."), night_factor=2.2, age="young"),
    EmergencyKind("allergy", "medical", "Reacción alérgica grave", 1.5, (0.2, 0.6, 0.2, 0.0), 14, 0.8,
                  ("{who} con hinchazón en la cara y dificultad para tragar tras comer.",
                   "{who} con reacción alérgica tras una picadura de avispa."), age="any"),
    EmergencyKind("anxiety", "medical", "Crisis de ansiedad", 4.0, (0.0, 0.0, 0.4, 0.6), 18, 0.3,
                  ("{who} con crisis de ansiedad, hiperventila y dice que se va a morir.",
                   "{who} muy agitado, la familia no consigue calmarle."), night_factor=1.3, age="young"),
    EmergencyKind("child_fever", "medical", "Fiebre alta en menor", 2.0, (0.0, 0.0, 0.5, 0.5), 12, 0.5,
                  ("{who} con fiebre de 40 °C que no baja con antitérmicos.",
                   "{who} con fiebre alta y muy decaído."), age="child"),
    EmergencyKind("bleeding", "medical", "Hemorragia", 2.0, (0.0, 0.5, 0.5, 0.0), 15, 0.85,
                  ("{who} con hemorragia nasal que no cede desde hace {mins} minutos; toma anticoagulantes.",
                   "{who} con un corte profundo en la mano que sangra mucho."), age="any"),
    # ── Traumatismos ───────────────────────────────────────────────────
    EmergencyKind("traffic_accident", "trauma", "Accidente de tráfico con heridos", 5.0, (0.2, 0.4, 0.4, 0.0), 25, 0.8,
                  ("Colisión entre dos turismos; {who} atrapado en el vehículo.",
                   "Salida de vía de un turismo; {who} con dolor cervical."), night_factor=1.2, age="adult", major_road=True),
    EmergencyKind("pedestrian_hit", "trauma", "Atropello", 1.5, (0.3, 0.5, 0.2, 0.0), 20, 0.95,
                  ("{who} atropellado en un paso de peatones, consciente pero sangra de la cabeza.",
                   "Un turismo atropella a {who_lower} al cruzar la calle."), age="any", major_road=True),
    EmergencyKind("street_fall", "trauma", "Caída en vía pública", 5.0, (0.0, 0.1, 0.5, 0.4), 15, 0.6,
                  ("{who} se ha caído en la acera; posible fractura de muñeca.",
                   "{who} tropieza y se golpea la cabeza contra el bordillo."), age="elderly"),
    EmergencyKind("bike_accident", "trauma", "Accidente de bicicleta o patinete", 2.0, (0.0, 0.3, 0.6, 0.1), 15, 0.7,
                  ("{who} cae de un patinete eléctrico; herida en la cara y dolor en el hombro.",
                   "Ciclista golpeado por la puerta de un coche aparcado."), age="young", major_road=True),
    EmergencyKind("work_accident", "trauma", "Accidente laboral", 1.5, (0.0, 0.5, 0.5, 0.0), 20, 0.85,
                  ("Operario herido en una obra; {who} con un golpe en la pierna.",
                   "{who} se ha cortado con una máquina en un taller."), night_factor=0.15, age="adult"),
    EmergencyKind("fall_height", "trauma", "Caída desde altura", 0.8, (0.4, 0.6, 0.0, 0.0), 25, 0.95,
                  ("{who} cae desde un andamio de unos cuatro metros.",
                   "{who} cae por la escalera desde el primer piso, no se mueve."), age="adult"),
    # ── Incendios, materiales peligrosos e inundaciones ───────────────
    EmergencyKind("house_fire", "fire", "Incendio en vivienda", 1.0, (0.2, 0.5, 0.3, 0.0), 40, 0.3,
                  ("Humo saliendo de una ventana del tercer piso; podría haber gente dentro.",
                   "Incendio en una cocina; {who} con posible inhalación de humo."), night_factor=1.3),
    EmergencyKind("vehicle_fire", "fire", "Incendio de vehículo", 0.6, (0.0, 0.3, 0.7, 0.0), 25, 0.1,
                  ("Un turismo arde en la calzada; el conductor ha salido por su pie.",
                   "Fuego en el motor de una furgoneta aparcada."), major_road=True),
    EmergencyKind("gas_leak", "hazmat", "Fuga de gas en edificio", 0.4, (0.0, 0.5, 0.5, 0.0), 30, 0.2,
                  ("Fuerte olor a gas en un portal; vecinos con mareos.",
                   "{who} con dolor de cabeza y náuseas; sospecha de monóxido por la caldera."), night_factor=1.4),
    EmergencyKind("flooding", "flood", "Inundación con personas afectadas", 0.2, (0.0, 0.3, 0.7, 0.0), 30, 0.2,
                  ("Bajo inundado tras las lluvias; {who} no puede salir.",
                   "Vehículo atrapado por el agua en un paso inferior."), age="elderly"),
    # ── Orden público ─────────────────────────────────────────────────
    EmergencyKind("assault", "altercation", "Agresión con heridos", 2.5, (0.1, 0.4, 0.5, 0.0), 18, 0.7,
                  ("Pelea a la salida de un local; {who} con heridas en la cara.",
                   "{who} agredido en la calle, consciente y sangrando."), night_factor=3.0, age="young"),
    EmergencyKind("stabbing", "altercation", "Herido por arma blanca", 0.4, (0.4, 0.6, 0.0, 0.0), 20, 0.95,
                  ("{who} con una herida por arma blanca en el abdomen tras una riña.",),
                   night_factor=2.5, age="young"),
    # ── Múltiples víctimas ─────────────────────────────────────────────
    EmergencyKind("multi_vehicle", "mass_casualty", "Accidente con múltiples víctimas", 0.15, (0.5, 0.5, 0.0, 0.0), 45, 1.0,
                  ("Colisión entre un autobús y varios turismos; numerosos heridos.",
                   "Choque en cadena con al menos seis heridos."), major_road=True),
)

# Demanda relativa por hora local (0–23): mínimo de madrugada, picos a media
# mañana y a última hora de la tarde. Se normaliza a media 1.
_HOURLY = (0.75, 0.62, 0.52, 0.45, 0.42, 0.45, 0.58, 0.80, 1.02, 1.18, 1.25, 1.28,
           1.27, 1.22, 1.15, 1.12, 1.13, 1.15, 1.18, 1.20, 1.17, 1.08, 0.98, 0.87)
_HOURLY_MEAN = sum(_HOURLY) / len(_HOURLY)

SEVERITIES: tuple[Severity, ...] = ("critical", "high", "medium", "low")


def demand_factor(hour: int) -> float:
    """Multiplicador de la tasa de llamadas para una hora local (media 1)."""
    return _HOURLY[hour % 24] / _HOURLY_MEAN


def is_night(hour: int) -> bool:
    return hour >= 22 or hour < 7


def pick_kind(rng: random.Random, hour: int) -> EmergencyKind:
    night = is_night(hour)
    weights = [k.weight * (k.night_factor if night else 1.0) for k in CATALOG]
    return rng.choices(CATALOG, weights=weights, k=1)[0]


def pick_severity(rng: random.Random, kind: EmergencyKind) -> Severity:
    return rng.choices(SEVERITIES, weights=kind.severity, k=1)[0]


_AGES = {
    "elderly": (68, 94),
    "adult": (28, 66),
    "young": (16, 32),
    "child": (1, 12),
    "any": (18, 85),
}


def _who(rng: random.Random, kind: EmergencyKind) -> str:
    lo, hi = _AGES.get(kind.age, _AGES["any"])
    age = rng.randint(lo, hi)
    if age < 14:
        return f"{'Niña' if rng.random() < 0.5 else 'Niño'} de {age} años"
    return f"{'Mujer' if rng.random() < 0.5 else 'Hombre'} de {age} años"


def describe(rng: random.Random, kind: EmergencyKind) -> str:
    who = _who(rng, kind)
    text = rng.choice(kind.descriptions)
    return text.format(who=who, who_lower=who[0].lower() + who[1:], mins=rng.choice((5, 10, 15, 20, 30)))


def on_scene_seconds(rng: random.Random, kind: EmergencyKind, severity: Severity) -> float:
    """Tiempo de asistencia en el lugar (s): lognormal alrededor de la media del tipo."""
    base = kind.on_scene_min * {"critical": 1.25, "high": 1.0, "medium": 0.9, "low": 0.8}.get(severity, 1.0)
    minutes = rng.lognormvariate(math.log(base), 0.3)
    return max(4.0, min(base * 2.5, minutes)) * 60.0


def needs_transport(rng: random.Random, kind: EmergencyKind, severity: Severity) -> bool:
    adj = {"critical": 0.1, "high": 0.05, "medium": 0.0, "low": -0.15}.get(severity, 0.0)
    return rng.random() < max(0.0, min(1.0, kind.transport_p + adj))


def patient_severity(severity: Severity) -> str:
    """Gravedad de la emergencia → estado del paciente que muestra la interfaz."""
    return {"critical": "critical", "high": "moderate", "medium": "moderate", "low": "stable"}.get(severity, "moderate")


def handover_seconds(rng: random.Random, severity: Severity) -> float:
    """Transferencia en urgencias del hospital (s): 8–25 min, más rápida si es crítico."""
    base = {"critical": 9.0, "high": 12.0, "medium": 15.0, "low": 16.0}.get(severity, 14.0)
    return max(6.0, min(30.0, rng.gauss(base, 3.0))) * 60.0


_KIND_BY_KEY = {k.key: k for k in CATALOG}


def kind_by_key(key: str | None) -> EmergencyKind | None:
    return _KIND_BY_KEY.get(key or "")


def kind_for_type(rng: random.Random, etype: str | None, hour: int = 12) -> EmergencyKind:
    """Tipo del catálogo para un ``emergencyType`` dado (emergencias manuales o externas)."""
    pool = [k for k in CATALOG if k.type == (etype or "medical")]
    if not pool:
        return pick_kind(rng, hour)
    night = is_night(hour)
    return rng.choices(pool, weights=[k.weight * (k.night_factor if night else 1.0) for k in pool], k=1)[0]


MAJOR_ROAD_HINTS = ("avenida", "estrada", "autovía", "autopista", "rolda", "carretera", "ac-", "sc-", "n-", "ap-", "cp-")


def is_major_road(name: str) -> bool:
    n = name.strip().lower()
    return any(n.startswith(h) or f" {h}" in n for h in MAJOR_ROAD_HINTS)


# ── Carga equilibrada ───────────────────────────────────────────────────
# Ocupación objetivo de la flota: ~65 % de media deja margen para las horas
# punta (demand_factor llega a ~1,28 → ~83 %) sin que se acumulen avisos.
TARGET_UTILIZATION = 0.65
# Minutos que no están en el catálogo: llegada al lugar, traslado y vuelta a
# base o repostaje, en ciudad.
_RESPONSE_MIN = 7.0
_TRANSPORT_MIN = 7.0
_RETURN_MIN = 6.0
_ON_SCENE_SEV = {"critical": 1.25, "high": 1.0, "medium": 0.9, "low": 0.8}
_TRANSPORT_ADJ = {"critical": 0.1, "high": 0.05, "medium": 0.0, "low": -0.15}
_HANDOVER_MIN = {"critical": 9.0, "high": 12.0, "medium": 15.0, "low": 16.0}


def _mean_cycle_minutes() -> float:
    """Duración media de una misión (de la asignación a quedar libre), en minutos."""
    total_w = sum(k.weight for k in CATALOG)
    cycle = 0.0
    for k in CATALOG:
        sev_w = sum(k.severity)
        for w, sev in zip(k.severity, SEVERITIES):
            p = k.weight / total_w * w / sev_w
            on_scene = k.on_scene_min * _ON_SCENE_SEV[sev] * 1.05  # media de la lognormal
            transport = max(0.0, min(1.0, k.transport_p + _TRANSPORT_ADJ[sev]))
            cycle += p * (_RESPONSE_MIN + on_scene + transport * (_TRANSPORT_MIN + _HANDOVER_MIN[sev]) + _RETURN_MIN)
    return cycle


MEAN_CYCLE_MIN = _mean_cycle_minutes()


def balanced_rate_per_min(units: int, pending: int = 0) -> float:
    """Emergencias por minuto simulado que mantienen la flota al ~65 % de ocupación.

    Si ya hay avisos esperando unidad, se frena en proporción a la cola para
    que el escenario se recupere en lugar de saturarse.
    """
    if units <= 0:
        return 0.0
    rate = TARGET_UTILIZATION * units / MEAN_CYCLE_MIN
    if pending > 0:
        rate *= max(0.15, 1.0 - pending / max(1.0, units * 0.5))
    return rate
