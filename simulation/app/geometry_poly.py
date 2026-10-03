"""Intersección segmento–polígono (atascos vs ruta)."""
from __future__ import annotations

from typing import Sequence


def _orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    """Producto cruzado del vector (a→b) con (a→c)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return (
        min(a[0], c[0]) <= b[0] <= max(a[0], c[0])
        and min(a[1], c[1]) <= b[1] <= max(a[1], c[1])
    )


def segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    o1 = _orient(p1, p2, p3)
    o2 = _orient(p1, p2, p4)
    o3 = _orient(p3, p4, p1)
    o4 = _orient(p3, p4, p2)
    eps = 1e-12
    if abs(o1) < eps and _on_segment(p1, p3, p2):
        return True
    if abs(o2) < eps and _on_segment(p1, p4, p2):
        return True
    if abs(o3) < eps and _on_segment(p3, p1, p4):
        return True
    if abs(o4) < eps and _on_segment(p3, p2, p4):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def polyline_intersects_polygon(
    route_coords: Sequence[tuple[float, float]],
    polygon: Sequence[tuple[float, float]],
) -> bool:
    """True si algún segmento de la ruta cruza algún lado del polígono."""
    if len(route_coords) < 2 or len(polygon) < 3:
        return False
    n = len(polygon)
    for i in range(len(route_coords) - 1):
        a, b = route_coords[i], route_coords[i + 1]
        for j in range(n):
            c, d = polygon[j], polygon[(j + 1) % n]
            if segments_intersect(a, b, c, d):
                return True
    return False
