"""Curated synthetic emergency titles by type.

Used by the training-mode spawner so the dashboard shows realistic
incident headlines instead of generic "Auto medical" placeholders.
"""
from __future__ import annotations

SYNTHETIC_TITLES: dict[str, list[str]] = {
    "medical": [
        "Cardiac arrest reported",
        "Severe allergic reaction",
        "Diabetic emergency",
        "Stroke symptoms reported",
        "Respiratory distress",
        "Unconscious adult",
        "Seizure in progress",
        "Pediatric fever crisis",
        "Heat stroke symptoms",
        "Severe abdominal pain",
    ],
    "trauma": [
        "Vehicle collision with injuries",
        "Fall from height",
        "Bicycle vs. car accident",
        "Workplace injury reported",
        "Pedestrian struck",
        "Boating accident",
        "Sports injury, head trauma",
    ],
    "altercation": [
        "Fight in progress",
        "Domestic disturbance",
        "Bar altercation",
        "Public disorder reported",
        "Robbery in progress",
        "Aggression with weapon reported",
    ],
    "mass_casualty": [
        "Multi-vehicle pileup",
        "Building collapse, multiple casualties",
        "Crowd crush at public event",
        "Bus accident with multiple victims",
        "Industrial accident, mass casualties",
    ],
    "fire": [
        "Structure fire reported",
        "Vehicle on fire",
        "Brush fire spreading",
        "Kitchen fire with smoke inhalation",
        "Electrical fire in commercial building",
    ],
    "hazmat": [
        "Chemical spill reported",
        "Suspected gas leak",
        "Industrial hazmat release",
        "Fuel tanker leak",
    ],
    "flood": [
        "Flash flooding, persons trapped",
        "Vehicle in floodwater",
        "Storm surge damage reported",
    ],
}
