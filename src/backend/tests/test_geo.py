import math

import pytest

from app.services import geo
from app.services.geo import LatLng


def test_haversine_known_distances():
    london, paris = LatLng(51.5074, -0.1278), LatLng(48.8566, 2.3522)
    assert geo.haversine_km(london, paris) == pytest.approx(343.5, rel=0.01)
    ny, la = LatLng(40.7128, -74.0060), LatLng(34.0522, -118.2437)
    assert geo.haversine_km(ny, la) == pytest.approx(3936, rel=0.01)
    assert geo.haversine_km(london, london) == 0


def test_median_resists_outlier_but_centroid_does_not():
    cluster = [LatLng(12.97 + i * 0.002, 77.59 + i * 0.002) for i in range(5)]
    outlier = LatLng(13.30, 77.90)  # ~50 km away
    pts = cluster + [outlier]
    median = geo.geometric_median(pts)
    centroid = geo.spherical_centroid(pts)
    cluster_c = geo.spherical_centroid(cluster)
    assert geo.haversine_km(median, cluster_c) < 1.0
    assert geo.haversine_km(centroid, cluster_c) > 5.0


def test_median_of_two_points_lies_between_them():
    a, b = LatLng(0, 0), LatLng(0, 1)
    m = geo.geometric_median([a, b])
    total = geo.haversine_km(a, b)
    assert geo.haversine_km(a, m) + geo.haversine_km(m, b) == pytest.approx(total, rel=1e-6)


def test_median_symmetric_triangle_is_centre():
    pts = [geo.destination_point(LatLng(-33.87, 151.21), b, 5) for b in (0, 120, 240)]
    m = geo.geometric_median(pts)
    assert geo.haversine_km(m, LatLng(-33.87, 151.21)) < 0.01


def test_antimeridian():
    pts = [LatLng(-17.0, 179.95), LatLng(-17.0, -179.95), LatLng(-17.05, 179.99)]
    c = geo.spherical_centroid(pts)
    m = geo.geometric_median(pts)
    for p in (c, m):
        assert abs(abs(p.lng) - 180) < 0.2, p
        assert -90 <= p.lat <= 90


def test_near_pole_no_nan():
    pts = [LatLng(89.99, 0), LatLng(89.99, 120), LatLng(89.99, -120)]
    m = geo.geometric_median(pts)
    assert not math.isnan(m.lat) and not math.isnan(m.lng)
    assert m.lat > 89.99


def test_destination_and_ring_shape():
    c = LatLng(64.14, -21.94)  # high latitude
    ring = geo.candidate_ring(c, 3.0, 12)
    assert len(ring) == 12
    for p in ring:
        assert geo.haversine_km(c, p) == pytest.approx(3.0, rel=0.01)
    # neighbours evenly spaced
    gaps = [geo.haversine_km(ring[i], ring[(i + 1) % 12]) for i in range(12)]
    assert max(gaps) - min(gaps) < 0.02


def test_ring_radius_clamps():
    assert geo.ring_radius_km(0.1) == 0.5
    assert geo.ring_radius_km(8) == 2.0
    assert geo.ring_radius_km(500) == 10.0


def test_group_spread_and_dedupe():
    pts = [LatLng(0, 0), LatLng(0, 0.5), LatLng(0, 1)]
    assert geo.group_spread_km(pts) == pytest.approx(111.2, rel=0.01)
    assert len(geo.dedupe_points([LatLng(1, 1), LatLng(1, 1.00001), LatLng(2, 2)])) == 2
