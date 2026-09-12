"""Small dependency-free geometry helpers (WGS84 lon/lat)."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Ring = Sequence[Sequence[float]]


def ring_bbox(ring: Ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def rings_bbox(rings: Iterable[Ring]) -> tuple[float, float, float, float]:
    boxes = [ring_bbox(r) for r in rings if r]
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def point_in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """Ray casting. Points exactly on an edge may go either way - acceptable."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
                lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_in_rings(lon: float, lat: float, rings: Iterable[Ring]) -> bool:
    """Esri polygons: outer rings are clockwise, holes counter-clockwise.

    We treat any containment as inside, then subtract holes by parity - which
    for our use (municipal boundaries) is equivalent and far simpler.
    """
    hits = 0
    for ring in rings:
        if point_in_ring(lon, lat, ring):
            hits += 1
    return hits % 2 == 1


def bbox_contains(bbox: Sequence[float], lon: float, lat: float) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def centroid(rings: Iterable[Ring]) -> tuple[float, float] | None:
    """Area-weighted centroid of the largest ring; falls back to mean point."""
    best, best_area = None, -1.0
    for ring in rings:
        if len(ring) < 3:
            continue
        a = cx = cy = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i][0], ring[i][1]
            x1, y1 = ring[i + 1][0], ring[i + 1][1]
            cross = x0 * y1 - x1 * y0
            a += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if abs(a) < 1e-14:
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            cand, area = (sum(xs) / len(xs), sum(ys) / len(ys)), 0.0
        else:
            a *= 0.5
            cand, area = (cx / (6 * a), cy / (6 * a)), abs(a)
        if area >= best_area:
            best, best_area = cand, area
    return best


EARTH_M = 6371008.8


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(a))


def ring_perimeter_m(ring: Ring) -> float:
    total = 0.0
    for i in range(len(ring) - 1):
        total += haversine_m(ring[i][0], ring[i][1], ring[i + 1][0], ring[i + 1][1])
    return total


def ring_area_m2(ring: Ring) -> float:
    """Equirectangular approximation - fine for parcel-sized shapes."""
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    a = 0.0
    for i in range(len(ring) - 1):
        x0 = ring[i][0] * k * 111320.0
        y0 = ring[i][1] * 110540.0
        x1 = ring[i + 1][0] * k * 111320.0
        y1 = ring[i + 1][1] * 110540.0
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def acres(m2: float) -> float:
    return m2 / 4046.8564224
