"""Hotspots: the fair spot moves to a livelier area nearby when that costs little."""

import asyncio
from datetime import datetime, timezone

import httpx

from app.config import Settings
from app.services import geo, osm
from app.services.engine import EngineInput, EngineMember, fairness_score, find_meeting_point
from app.services.geo import LatLng
from app.services.osm import OsmMapClient
from app.services.osm_fallback import Breaker, NominatimClient, _Spacer

from .fakes import FakeMapClient

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
OSM_MODE = Settings(database_url="sqlite://")  # no Google key: estimated times
BLR = LatLng(12.9716, 77.5946)


class CountingFake(FakeMapClient):
    """OpenStreetMap-like client (estimated times) that can count places nearby."""

    estimates_only = True

    def __init__(self, base=3, ring=40, **kw):
        super().__init__(**kw)
        self.base, self.ring = base, ring
        self.count_requests: list[list[LatLng]] = []

    async def count_places(self, points, radius_m):
        self.count_requests.append(list(points))
        if len(self.count_requests) == 1:  # the fair spot itself
            return [self.base]
        ring = self.ring if isinstance(self.ring, list) else [self.ring] * len(points)
        return ring[: len(points)]


def group(c=BLR, n=4, km=6.0):
    out = []
    for i in range(n):
        p = geo.destination_point(c, i * 360 / n + 10, km * (0.6 + 0.4 * (i % 2)))
        out.append(EngineMember(id=f"m{i}", name=f"P{i}", lat=p.lat, lng=p.lng, mode="DRIVE"))
    return out


def run(maps, settings=OSM_MODE, members=None):
    return asyncio.run(find_meeting_point(EngineInput(members or group(), ["cafe", "restaurant"]), maps, settings, now=NOW))


def test_quiet_fair_spot_moves_to_a_lively_area_nearby():
    maps = CountingFake(base=3, ring=40)
    res = run(maps)
    zone = res["zones"][0]
    assert zone["lively"]["places"] == 40 and zone["lively"]["places_before"] == 3
    assert 1.1 <= zone["lively"]["moved_km"] <= 1.3
    assert zone["lively"]["extra_minutes"] <= OSM_MODE.lively_max_extra_min
    assert "LIVELY_AREA" in [w["code"] for w in res["warnings"]]
    assert res["usage"]["lively_calls"] == 7  # the spot + 6 around it
    assert res["usage"]["geocode_calls"] == 2  # the heading names the new area
    assert res["venues"]


def test_moved_spot_keeps_the_fairness_rule():
    """The move never makes the longest trip more than lively_max_extra_min longer."""
    unmoved = run(CountingFake(base=35))["zones"][0]  # lively already: stays
    moved = run(CountingFake(base=3, ring=40))["zones"][0]
    assert max(moved["minutes"].values()) <= max(unmoved["minutes"].values()) + OSM_MODE.lively_max_extra_min
    assert moved["score"] == round(fairness_score(list(moved["minutes"].values())), 1)


def test_lively_spot_already_is_not_moved_and_costs_one_count():
    maps = CountingFake(base=35)
    res = run(maps)
    assert "lively" not in res["zones"][0] and "LIVELY_AREA" not in [w["code"] for w in res["warnings"]]
    assert res["usage"]["lively_calls"] == 1


def test_only_clearly_livelier_spots_count():
    # 10 nearby is below lively_min_places (15); 12 is not 1.5x of 9
    assert "lively" not in run(CountingFake(base=3, ring=10))["zones"][0]
    assert "lively" not in run(CountingFake(base=9, ring=12))["zones"][0]


def test_busiest_spot_wins():
    res = run(CountingFake(base=2, ring=[20, 45, 30, 16, 18, 25]))
    assert res["zones"][0]["lively"]["places"] == 45


def test_no_move_when_it_costs_too_much_travel():
    far = Settings(database_url="sqlite://", lively_ring_km=9.0)  # every livelier spot is far away
    assert "lively" not in run(CountingFake(base=3, ring=40), settings=far)["zones"][0]


def test_unknown_counts_change_nothing():
    assert "lively" not in run(CountingFake(base=None))["zones"][0]
    assert "lively" not in run(CountingFake(base=3, ring=[None] * 6))["zones"][0]


def test_can_be_turned_off_and_never_runs_with_google():
    off = Settings(database_url="sqlite://", lively_enabled=False)
    maps = CountingFake(base=3, ring=40)
    assert "lively" not in run(maps, settings=off)["zones"][0] and maps.count_requests == []

    google = CountingFake(base=3, ring=40)
    google.estimates_only = False  # real routing (paid) travel times: no extra spots tried
    res = run(google, settings=Settings(google_maps_api_key="k", database_url="sqlite://"))
    assert google.count_requests == [] and "lively" not in res["zones"][0]


# ---- counting places with the OpenStreetMap client --------------------------
def feature(name, lat, lng):
    return {"properties": {"name": name, "osm_type": "N", "osm_id": 1}, "geometry": {"coordinates": [lng, lat]}}


def test_photon_counts_named_places_in_range_and_caches(monkeypatch):
    monkeypatch.setattr(osm, "_lively_cache", {})
    seen = []

    def handler(req):
        seen.append(req)
        p = req.url.params
        assert sorted(p.get_list("osm_tag")) == sorted(osm.LIVELY_TAGS) and p["limit"] == "50" and p["radius"] == "0.50"
        lat, lng = float(p["lat"]), float(p["lon"])
        return httpx.Response(
            200,
            json={
                "features": [feature(f"Cafe {i}", lat + 0.0005 * i, lng) for i in range(8)]  # 0 to 390 m
                + [feature("Far", lat + 0.02, lng), feature("", lat, lng)]  # 2.2 km away; no name
            },
        )

    c = OsmMapClient("https://photon.example", httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_delay_s=0)
    assert asyncio.run(c.count_places([BLR, LatLng(12.93, 77.62)], 500)) == [8, 8]
    assert asyncio.run(c.count_places([BLR], 500)) == [8]  # cached: no new request
    assert len(seen) == 2


def test_counts_fall_back_to_nominatim_when_photon_is_down(monkeypatch):
    monkeypatch.setattr(osm, "_lively_cache", {})

    def handler(req):
        if req.url.host == "photon.example":
            return httpx.Response(503)
        assert req.url.params["q"] == "[amenity=restaurant]" and req.url.params["bounded"] == "1"
        lat, lng = 12.9716, 77.5946
        return httpx.Response(
            200,
            json=[
                {
                    "osm_type": "node",
                    "osm_id": i,
                    "lat": str(lat + 0.0004 * i),
                    "lon": str(lng),
                    "name": f"R{i}",
                    "category": "amenity",
                    "type": "restaurant",
                }
                for i in range(6)
            ],
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = OsmMapClient(
        "https://photon.example",
        http,
        retry_delay_s=0,
        nominatim=NominatimClient("https://nominatim.example", http, spacer=_Spacer(0)),
        brk=Breaker(),
    )
    assert asyncio.run(c.count_places([BLR], 500)) == [6]


def test_counts_are_unknown_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(osm, "_lively_cache", {})
    c = OsmMapClient(
        "https://photon.example", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))), retry_delay_s=0
    )
    assert asyncio.run(c.count_places([BLR], 500)) == [None]
