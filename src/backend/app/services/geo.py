"""Pure spherical geometry. No I/O, standard library only.

Every function works at any latitude and across the antimeridian because
points are handled as unit vectors on the sphere, never as flat lat/lng.
"""

from __future__ import annotations

import math
from typing import Iterable, NamedTuple, Sequence

EARTH_RADIUS_KM = 6371.0088


class LatLng(NamedTuple):
    lat: float
    lng: float


Vec = tuple[float, float, float]


def _norm_lng(lng: float) -> float:
    lng = (lng + 180.0) % 360.0 - 180.0
    return 180.0 if lng == -180.0 else lng


def to_xyz(p: LatLng) -> Vec:
    lat, lng = math.radians(p.lat), math.radians(p.lng)
    c = math.cos(lat)
    return (c * math.cos(lng), c * math.sin(lng), math.sin(lat))


def from_xyz(v: Vec) -> LatLng:
    x, y, z = v
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    lng = math.degrees(math.atan2(y, x)) if (abs(x) > 1e-15 or abs(y) > 1e-15) else 0.0
    return LatLng(lat, _norm_lng(lng))


def _normalize(v: Vec) -> Vec:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-15:
        raise ValueError("degenerate vector (antipodal points)")
    return (v[0] / n, v[1] / n, v[2] / n)


def _angle(a: Vec, b: Vec) -> float:
    """Central angle in radians between two unit vectors (numerically stable)."""
    cx = a[1] * b[2] - a[2] * b[1]
    cy = a[2] * b[0] - a[0] * b[2]
    cz = a[0] * b[1] - a[1] * b[0]
    cross = math.sqrt(cx * cx + cy * cy + cz * cz)
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return math.atan2(cross, dot)


def haversine_km(a: LatLng, b: LatLng) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def spherical_centroid(points: Sequence[LatLng]) -> LatLng:
    if not points:
        raise ValueError("no points")
    sx = sy = sz = 0.0
    for p in points:
        x, y, z = to_xyz(p)
        sx, sy, sz = sx + x, sy + y, sz + z
    try:
        return from_xyz(_normalize((sx, sy, sz)))
    except ValueError:
        return points[0]


def geometric_median(points: Sequence[LatLng], eps: float = 1e-10, max_iter: int = 1000) -> LatLng:
    """Weiszfeld's algorithm on the unit sphere, seeded from the spherical centroid.

    Minimises the sum of great-circle distances, so one far-away person does not
    drag the centre toward them the way the centroid does. Input points that the
    estimate lands on are skipped for that step (standard Weiszfeld modification).
    """
    if not points:
        raise ValueError("no points")
    if len(points) == 1:
        return points[0]
    vs = [to_xyz(p) for p in points]
    x = to_xyz(spherical_centroid(points))
    for _ in range(max_iter):
        nx = ny = nz = den = 0.0
        for v in vs:
            d = _angle(x, v)
            if d < 1e-12:
                continue
            w = 1.0 / d
            nx, ny, nz, den = nx + v[0] * w, ny + v[1] * w, nz + v[2] * w, den + w
        if den == 0.0:
            break
        try:
            x_new = _normalize((nx / den, ny / den, nz / den))
        except ValueError:
            break
        if _angle(x, x_new) < eps:
            x = x_new
            break
        x = x_new
    return from_xyz(x)


def destination_point(origin: LatLng, bearing_deg: float, dist_km: float) -> LatLng:
    lat1 = math.radians(origin.lat)
    lng1 = math.radians(origin.lng)
    brg = math.radians(bearing_deg)
    d = dist_km / EARTH_RADIUS_KM
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lng2 = lng1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return LatLng(math.degrees(lat2), _norm_lng(math.degrees(lng2)))


def group_spread_km(points: Sequence[LatLng]) -> float:
    best = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            best = max(best, haversine_km(points[i], points[j]))
    return best


def candidate_ring(centre: LatLng, radius_km: float, count: int, offset_deg: float = 0.0) -> list[LatLng]:
    return [destination_point(centre, offset_deg + i * 360.0 / count, radius_km) for i in range(count)]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def ring_radius_km(spread_km: float, ratio: float = 0.25, lo: float = 0.5, hi: float = 10.0) -> float:
    return clamp(ratio * spread_km, lo, hi)


def dedupe_points(points: Iterable[LatLng], min_km: float = 0.01) -> list[LatLng]:
    out: list[LatLng] = []
    for p in points:
        if all(haversine_km(p, q) >= min_km for q in out):
            out.append(p)
    return out
