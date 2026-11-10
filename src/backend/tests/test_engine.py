from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.services import geo
from app.services.engine import (
    EngineError,
    EngineInput,
    EngineMember,
    bayesian_rating,
    fairness_index,
    fairness_score,
    find_meeting_point,
    venue_quality,
)
from app.services.geo import LatLng

from .fakes import FakeMapClient

S = Settings(google_maps_api_key="test", database_url="sqlite://")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

CITIES = {
    "lisbon": LatLng(38.7223, -9.1393),
    "bengaluru": LatLng(12.9716, 77.5946),
    "tokyo": LatLng(35.6762, 139.6503),
    "sao_paulo": LatLng(-23.5505, -46.6333),
    "new_york": LatLng(40.7128, -74.0060),
    "nairobi": LatLng(-1.2921, 36.8219),
    "auckland": LatLng(-36.8485, 174.7633),
}


def group_around(c: LatLng, n=4, km=6.0, mode="DRIVE", modes=None):
    out = []
    for i in range(n):
        p = geo.destination_point(c, i * 360 / n + 10, km * (0.6 + 0.4 * (i % 2)))
        out.append(EngineMember(id=f"m{i}", name=f"P{i}", lat=p.lat, lng=p.lng, mode=(modes[i] if modes else mode)))
    return out


def run(members, maps, types=("cafe", "restaurant"), meet_at=None, settings=S):
    import asyncio

    return asyncio.run(find_meeting_point(EngineInput(members, list(types), meet_at), maps, settings, now=NOW))


# ---- scoring ---------------------------------------------------------------
def test_fairness_score_examples_from_prd():
    assert fairness_score([20, 20, 20]) == pytest.approx(14.0)
    assert fairness_score([5, 10, 45]) == pytest.approx(31.8, abs=0.05)
    assert fairness_index([20, 20, 20]) == 1.0
    assert fairness_index([5, 10, 45]) < 0.2


def test_bayesian_rating_prefers_many_reviews():
    assert bayesian_rating(5.0, 3) < bayesian_rating(4.6, 2000)
    assert bayesian_rating(None, 0) == 4.0
    assert venue_quality(4.6, 2000, True, 1.0) > venue_quality(4.6, 2000, False, 1.0)


# ---- full pipeline in 7 cities on 5 continents ------------------------------
@pytest.mark.parametrize("city", list(CITIES))
def test_pipeline_in_many_cities(city):
    maps = FakeMapClient()
    res = run(group_around(CITIES[city], n=5), maps)
    assert 1 <= len(res["venues"]) <= 5
    assert res["zones"] and len(res["zones"]) <= 3
    assert not res["estimated"]
    assert res["region"]["locality"] == "Test City"
    v = res["venues"][0]
    assert len(v["travel"]) == 5 and all(t["minutes"] > 0 for t in v["travel"])
    scores = [x["score"] for x in res["venues"]]
    assert scores == sorted(scores)
    # bounded cost: 1 zone matrix + 1 venue matrix (single mode), <= 9 Places, 1 reverse geocode
    u = res["usage"]
    assert u["route_matrix_requests"] == 2
    assert u["places_calls"] <= 9
    assert u["geocode_calls"] == 1
    assert len({x["id"] for x in res["venues"]}) == len(res["venues"])  # de-duplicated


def test_fairer_than_naive_centroid_with_outlier():
    c = CITIES["bengaluru"]
    members = group_around(c, n=4, km=2)
    far = geo.destination_point(c, 45, 25)
    members.append(EngineMember(id="far", name="Far", lat=far.lat, lng=far.lng, mode="DRIVE"))
    res = run(members, FakeMapClient())
    top = res["zones"][0]
    # The centroid is one of the candidates, so the chosen zone can't be less fair than it.
    centroid = geo.spherical_centroid([LatLng(m.lat, m.lng) for m in members])
    cent_minutes = [geo.haversine_km(LatLng(m.lat, m.lng), centroid) * 1.25 / 32 * 60 + 1 for m in members]
    assert top["score"] <= fairness_score(cent_minutes) + 0.1


def test_imperial_units_for_us():
    res = run(group_around(CITIES["new_york"]), FakeMapClient(country="US"))
    assert res["region"]["units"] == "imperial"


def test_mixed_modes_one_request_per_mode_no_extra_elements():
    maps = FakeMapClient()
    members = group_around(CITIES["tokyo"], n=6, modes=["DRIVE", "TRANSIT", "WALK", "DRIVE", "TRANSIT", "WALK"])
    res = run(members, maps)
    modes = [c["mode"] for c in maps.matrix_calls]
    assert sorted(set(modes)) == ["DRIVE", "TRANSIT", "WALK"]
    assert len(maps.matrix_calls) == 6  # 3 modes x (zones + venues)
    assert {t["mode"] for t in res["venues"][0]["travel"]} == {"DRIVE", "TRANSIT", "WALK"}


def test_traffic_aware_only_for_venue_matrix_and_times_passed():
    maps = FakeMapClient()
    meet = (NOW + timedelta(days=2)).isoformat()
    members = group_around(CITIES["lisbon"], n=4, modes=["DRIVE", "TRANSIT", "DRIVE", "TRANSIT"])
    run(members, maps, meet_at=meet)
    zone_calls = [c for c in maps.matrix_calls if not c["traffic_aware"]]
    venue_calls = [c for c in maps.matrix_calls if c["traffic_aware"]]
    assert zone_calls and venue_calls
    drive_venue = next(c for c in venue_calls if c["mode"] == "DRIVE")
    assert drive_venue["departure_time"] is not None and drive_venue["arrival_time"] is None
    transit = [c for c in maps.matrix_calls if c["mode"] == "TRANSIT"]
    assert all(c["arrival_time"] is not None for c in transit)
    drive_zone = next(c for c in zone_calls if c["mode"] == "DRIVE")
    assert drive_zone["departure_time"] is None


# ---- validation & edge cases ----------------------------------------------
def test_not_enough_and_too_many_members():
    with pytest.raises(EngineError) as e:
        run(group_around(CITIES["lisbon"], n=1), FakeMapClient())
    assert e.value.code == "NOT_ENOUGH_MEMBERS"
    with pytest.raises(EngineError) as e:
        run(group_around(CITIES["lisbon"], n=26), FakeMapClient())
    assert e.value.code == "TOO_MANY_MEMBERS"


def test_everyone_at_same_place():
    c = CITIES["nairobi"]
    members = [EngineMember(id=f"m{i}", name="X", lat=c.lat, lng=c.lng, mode="WALK") for i in range(3)]
    res = run(members, FakeMapClient())
    assert "SAME_PLACE" in [w["code"] for w in res["warnings"]]
    assert res["venues"]
    assert geo.haversine_km(LatLng(**res["center"]), c) < 0.01


def test_different_cities_switch_to_driving():
    a, b = CITIES["lisbon"], LatLng(41.1579, -8.6291)  # Lisbon - Porto ~275 km
    members = [
        EngineMember(id="a", name="A", lat=a.lat, lng=a.lng, mode="TRANSIT"),
        EngineMember(id="b", name="B", lat=b.lat, lng=b.lng, mode="WALK"),
    ]
    maps = FakeMapClient()
    res = run(members, maps)
    assert "DIFFERENT_CITIES" in [w["code"] for w in res["warnings"]]
    assert {c["mode"] for c in maps.matrix_calls} == {"DRIVE"}
    assert res["radius_km"] == 10.0


def test_unreachable_centre_zones_dropped():
    c = CITIES["sao_paulo"]
    members = group_around(c, n=4, km=8)
    centre = geo.geometric_median([LatLng(m.lat, m.lng) for m in members])
    res = run(members, FakeMapClient(blocked=centre, blocked_km=1.2))
    for z in res["zones"]:
        assert geo.haversine_km(LatLng(z["lat"], z["lng"]), centre) > 1.2


def test_no_reachable_area():
    with pytest.raises(EngineError) as e:
        run(group_around(CITIES["tokyo"]), FakeMapClient(blocked_everywhere=True))
    assert e.value.code == "NO_REACHABLE_AREA"


def test_two_wheeler_falls_back_to_drive():
    maps = FakeMapClient(unsupported_modes={"TWO_WHEELER"})
    res = run(group_around(CITIES["new_york"], n=3, mode="TWO_WHEELER"), maps)
    assert "TWO_WHEELER_FALLBACK" in [w["code"] for w in res["warnings"]]
    assert not res["estimated"]
    assert all(t["mode"] == "DRIVE" for t in res["venues"][0]["travel"])


def test_routes_failure_uses_flagged_estimates():
    res = run(group_around(CITIES["lisbon"]), FakeMapClient(fail_routes=True))
    assert res["estimated"] is True
    assert "ESTIMATED_TIMES" in [w["code"] for w in res["warnings"]]
    assert res["venues"]


def test_places_failure_returns_zones_only():
    res = run(group_around(CITIES["auckland"]), FakeMapClient(fail_places=True))
    assert res["venues"] == [] and res["zones"]
    codes = [w["code"] for w in res["warnings"]]
    assert "PLACES_UNAVAILABLE" in codes and "NO_VENUES" in codes


def test_sparse_area_widens_radius_then_zones_only():
    maps = FakeMapClient(no_venues=True)
    res = run(group_around(CITIES["nairobi"]), maps)
    assert res["venues"] == []
    assert len(maps.places_calls) == 9  # 3 zones x 3 radius steps
    assert sorted({c["radius"] for c in maps.places_calls}) == [800, 1500, 3000]


def test_widening_stops_when_enough_venues():
    maps = FakeMapClient(venues_per_search=3)
    run(group_around(CITIES["lisbon"]), maps)
    assert {c["radius"] for c in maps.places_calls} == {800}


def test_reverse_geocode_failure_gives_generic_region():
    res = run(group_around(CITIES["tokyo"]), FakeMapClient(fail_geocode=True))
    assert res["region"]["locality"] is None and res["region"]["units"] == "metric"
    assert res["venues"]


def test_no_maps_key_estimates_and_zones_only():
    res = run(group_around(CITIES["bengaluru"]), None)
    assert res["estimated"] is True and res["venues"] == [] and res["zones"]
    assert res["usage"] is None


def test_past_meeting_time_ignored():
    maps = FakeMapClient()
    res = run(group_around(CITIES["lisbon"]), maps, meet_at=(NOW - timedelta(hours=3)).isoformat())
    assert "PAST_MEETING_TIME" in [w["code"] for w in res["warnings"]]
    assert all(c["departure_time"] is None for c in maps.matrix_calls)
    assert res["meet_at"] is None


def test_large_transit_group_respects_100_element_limit():
    # The fake client asserts the limit on every call.
    maps = FakeMapClient()
    res = run(group_around(CITIES["sao_paulo"], n=25, km=10, mode="TRANSIT"), maps)
    assert res["venues"]
    assert all(c["elements"] <= 100 for c in maps.matrix_calls)
    zone_call = next(c for c in maps.matrix_calls if not c["traffic_aware"])
    assert zone_call["elements"] == 100  # 25 people x 4 zones


def test_drive_group_of_25_stays_within_625():
    maps = FakeMapClient()
    run(group_around(CITIES["new_york"], n=25, km=15), maps)
    assert all(c["elements"] <= 625 for c in maps.matrix_calls)


def test_weights_are_configuration():
    custom = Settings(google_maps_api_key="t", database_url="sqlite://", w_max=1.0, w_std=0.0, w_mean=0.0)
    res = run(group_around(CITIES["lisbon"]), FakeMapClient(), settings=custom)
    z = res["zones"][0]
    assert z["score"] == pytest.approx(max(z["minutes"].values()), abs=0.1)


def test_pick_varied_mixes_kinds_then_fills_by_score():
    from types import SimpleNamespace

    from app.services.engine import pick_varied

    def v(name, kind, score):
        return {"place": SimpleNamespace(type=kind, name=name), "final": score, "S": score}

    ranked = [
        v("r1", "restaurant", 0.1),
        v("r2", "restaurant", 0.2),
        v("r3", "restaurant", 0.3),
        v("b1", "bar", 0.4),
        v("r4", "restaurant", 0.5),
        v("p1", "pub", 0.6),
        v("r5", "restaurant", 0.7),
    ]
    names = [x["place"].name for x in pick_varied(ranked, 5)]
    assert set(names) >= {"b1", "p1"} and names[0] == "r1"
    assert names == ["r1", "r2", "r3", "b1", "p1"]
    one_kind = [v(f"r{i}", "restaurant", i) for i in range(7)]
    assert [x["place"].name for x in pick_varied(one_kind, 5)] == ["r0", "r1", "r2", "r3", "r4"]
