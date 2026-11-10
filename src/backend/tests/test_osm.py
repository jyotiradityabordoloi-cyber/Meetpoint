import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.services.engine import EngineInput, EngineMember, find_meeting_point
from app.services.geo import LatLng
from app.services.osm import OsmMapClient


def feature(name, lng, lat, value="restaurant", key="amenity", osm_id=1, **props):
    return {
        "properties": {"name": name, "osm_key": key, "osm_value": value, "osm_type": "N", "osm_id": osm_id, **props},
        "geometry": {"coordinates": [lng, lat]},
    }


HAUZ_KHAS = LatLng(28.5494, 77.2001)


def photon(handler):
    return OsmMapClient("https://photon.example/", httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_delay_s=0)


async def test_search_nearby_maps_types_to_osm_tags_and_parses():
    seen = {}

    seen = {"tags": []}
    by_tag = {
        "amenity:restaurant": [
            feature("Karim's", 77.2047, 28.5528, osm_id=11, street="Aurobindo Marg", district="South", city="Delhi"),
            feature("", 77.20, 28.55, osm_id=14),  # nameless
        ],
        "amenity:pub": [feature("Imperfecto", 77.1951, 28.5547, value="pub", osm_id=12)],
        "amenity:bar": [
            feature("Far Away Bar", 77.40, 28.70, value="bar", osm_id=13),  # outside radius
            feature("Karim's", 77.2047, 28.5528, osm_id=11),  # same place listed twice
        ],
        "amenity:cafe": [],
    }

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["radius"] = req.url.params["radius"]
        tags = req.url.params.get_list("osm_tag")
        seen["tags"].extend(tags)
        return httpx.Response(200, json={"features": [f for t in tags for f in by_tag[t]]})

    places = await photon(handler).search_nearby(HAUZ_KHAS, 1500, ["restaurant", "bar", "pub", "cafe", "coffee_shop"])
    assert seen["path"] == "/reverse" and seen["radius"] == "3.00"  # fetched once at 3 km, filtered to 1.5 km
    assert sorted(seen["tags"]) == ["amenity:bar", "amenity:cafe", "amenity:pub", "amenity:restaurant"]  # one per kind
    assert sorted(p.name for p in places) == ["Imperfecto", "Karim's"]  # merged, de-duplicated, in range
    places.sort(key=lambda p: p.name != "Karim's")
    k = places[0]
    assert k.id == "osm:N11" and k.type == "restaurant" and k.rating is None and k.open_now is None and k.maps_url is None
    assert k.address == "Aurobindo Marg, South, Delhi"
    assert places[1].type == "pub"


async def test_mall_type_and_unknown_types():
    def handler(req):
        return httpx.Response(200, json={"features": [feature("Select Citywalk", 77.219, 28.528, value="mall", key="shop")]})

    c = photon(handler)
    assert (await c.search_nearby(LatLng(28.528, 77.219), 800, ["shopping_mall"]))[0].type == "shopping_mall"
    assert await c.search_nearby(HAUZ_KHAS, 800, ["casino"]) == []  # no request needed


async def test_reverse_geocode_gives_area_names_and_geocode():
    def handler(req):
        if req.url.path == "/reverse":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "name": "x",
                                "district": "Hauz Khas",
                                "city": "New Delhi",
                                "countrycode": "IN",
                                "country": "India",
                            },
                            "geometry": {"coordinates": [77.2, 28.55]},
                        }
                    ]
                },
            )
        return httpx.Response(
            200, json={"features": [feature("Connaught Place", 77.2167, 28.6315, value="square", city="New Delhi", country="India")]}
        )

    c = photon(handler)
    comps = (await c.reverse_geocode(HAUZ_KHAS))[0].components
    assert {c_["long_name"] for c_ in comps} == {"Hauz Khas", "New Delhi", "India"}
    g = await c.geocode("connaught place")
    assert g[0].label == "Connaught Place, New Delhi, India" and g[0].lat == 28.6315


async def test_errors_become_google_errors():
    from app.services.google import GoogleError

    c = photon(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(GoogleError) as e:
        await c.search_nearby(HAUZ_KHAS, 800, ["restaurant"])
    assert e.value.kind == "quota"


def test_engine_returns_named_osm_venues_with_estimated_times():
    """No Google key + Photon: real venues, area heading, estimated (flagged) times, no matrix calls."""

    def handler(req):
        if req.url.path == "/reverse" and "osm_tag" not in req.url.params:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {"district": "Hauz Khas", "city": "New Delhi", "countrycode": "IN"},
                            "geometry": {"coordinates": [0, 0]},
                        }
                    ]
                },
            )
        lat, lng = float(req.url.params["lat"]), float(req.url.params["lon"])
        return httpx.Response(
            200,
            json={
                "features": [
                    feature(f"Place {i} near {lat:.3f}", lng + 0.001 * i, lat + 0.001 * i, osm_id=int(abs(lat * 1e4)) * 10 + i)
                    for i in range(1, 6)
                ]
            },
        )

    members = [
        EngineMember("a", "A", 28.6315, 77.2167, "DRIVE"),
        EngineMember("b", "B", 28.5494, 77.2001, "TWO_WHEELER"),
        EngineMember("c", "C", 28.6129, 77.2295, "TRANSIT"),
    ]
    s = Settings(database_url="sqlite://")
    res = asyncio.run(
        find_meeting_point(EngineInput(members, ["restaurant", "bar", "pub"]), photon(handler), s, now=datetime(2026, 11, 10, tzinfo=UTC))
    )
    assert 3 <= len(res["venues"]) <= 5
    assert all(v["name"].startswith("Place ") for v in res["venues"])
    assert res["region"]["area"] == "Hauz Khas" and res["region"]["locality"] == "New Delhi"
    assert res["estimated"] is True
    codes = [w["code"] for w in res["warnings"]]
    assert "ESTIMATED_TIMES" in codes and "TWO_WHEELER_FALLBACK" not in codes
    assert res["usage"]["route_matrix_requests"] == 0  # no routing matrix in OSM mode
    assert res["usage"]["places_calls"] <= 9
    assert {t["mode"] for t in res["venues"][0]["travel"]} == {"DRIVE", "TWO_WHEELER", "TRANSIT"}


async def test_widening_reuses_one_fetch_per_kind():
    calls = []

    def handler(req):
        calls.append(req.url.params["osm_tag"])
        return httpx.Response(
            200, json={"features": [feature("Near", 77.2005, 28.5496, osm_id=1), feature("Mid", 77.212, 28.5494, osm_id=2)]}
        )

    c = photon(handler)
    small = await c.search_nearby(HAUZ_KHAS, 800, ["restaurant"])
    wide = await c.search_nearby(HAUZ_KHAS, 1500, ["restaurant"])
    assert [p.name for p in small] == ["Near"] and [p.name for p in wide] == ["Near", "Mid"]
    assert calls == ["amenity:restaurant"]  # second radius served from the first fetch


async def test_request_budget_fairest_area_per_kind_others_combined():
    calls = []

    def handler(req):
        calls.append(tuple(req.url.params.get_list("osm_tag")))
        return httpx.Response(200, json={"features": []})

    c = photon(handler)
    kinds = ["restaurant", "bar", "pub"]
    for centre in (HAUZ_KHAS, LatLng(28.56, 77.21), LatLng(28.54, 77.19)):  # fairest first
        for radius in (800, 1500, 3000):  # engine widening
            await c.search_nearby(centre, radius, kinds)
    assert len(calls) == 5
    assert calls[:3] == [("amenity:restaurant",), ("amenity:bar",), ("amenity:pub",)]
    assert calls[3:] == [("amenity:restaurant", "amenity:bar", "amenity:pub")] * 2


async def test_busy_server_is_retried_once():
    attempts = []

    def handler(req):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"features": [feature("Kaffeine", 77.1942, 28.5536, value="pub", osm_id=5)]})

    places = await photon(handler).search_nearby(HAUZ_KHAS, 1500, ["pub"])
    assert [p.name for p in places] == ["Kaffeine"] and len(attempts) == 2


async def test_requests_never_overlap():
    import asyncio as _asyncio

    live = {"now": 0, "max": 0}

    async def handler(req):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        await _asyncio.sleep(0.01)
        live["now"] -= 1
        return httpx.Response(200, json={"features": []})

    c = OsmMapClient("https://photon.example/", httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_delay_s=0)
    await c.search_nearby(HAUZ_KHAS, 800, ["restaurant", "bar", "pub"])
    assert live["max"] == 1


async def test_cinema_option_maps_to_osm():
    seen = {}

    def handler(req):
        seen["tags"] = req.url.params.get_list("osm_tag")
        return httpx.Response(200, json={"features": [feature("PVR Select Citywalk", 77.2195, 28.5286, value="cinema", osm_id=9)]})

    places = await photon(handler).search_nearby(LatLng(28.5286, 77.2195), 800, ["movie_theater"])
    assert seen["tags"] == ["amenity:cinema"] and places[0].type == "movie_theater"
