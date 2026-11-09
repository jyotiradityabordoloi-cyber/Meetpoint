import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_maps, get_router
from app.main import create_app
from app.services.geo import LatLng
from app.services.google import GoogleMapClient, _photos
from app.services.routes import GoogleRoutes, OsrmRoutes, Route, RouteCache, decode_polyline, route_all, simplify


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- polyline + helpers ----------------------------------------------------
def test_decode_polyline_reference_example():
    # Google's documented example
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert decode_polyline("") == []


def test_simplify_keeps_ends():
    pts = [(i, i) for i in range(1000)]
    s = simplify(pts, 50)
    assert len(s) == 50 and s[0] == (0, 0) and s[-1] == (999, 999)
    assert simplify(pts[:10], 50) == pts[:10]


def test_photos_parsing_limits_and_credits():
    raw = [
        {"name": "places/a/photos/1", "authorAttributions": [{"displayName": "Ana"}, {"displayName": "Raj"}]},
        {"name": "places/a/photos/2"},
        {},
        {"name": "places/a/photos/3"},
        {"name": "places/a/photos/4"},
    ]
    assert _photos(raw) == (
        {"name": "places/a/photos/1", "attribution": "Ana, Raj"},
        {"name": "places/a/photos/2", "attribution": None},
        {"name": "places/a/photos/3", "attribution": None},
    )
    assert _photos(None) == ()


# ---- providers -------------------------------------------------------------
async def test_google_routes_request_and_parse():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["mask"] = req.headers["x-goog-fieldmask"]
        return httpx.Response(
            200,
            json={"routes": [{"duration": "754s", "distanceMeters": 5120, "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC"}}]},
        )

    dep = datetime(2030, 1, 1, 18, tzinfo=UTC)
    rt = await GoogleRoutes("K", mock(handler)).route(LatLng(1, 2), LatLng(3, 4), "DRIVE", dep, None)
    assert rt == Route(points=[(38.5, -120.2), (40.7, -120.95)], seconds=754.0, meters=5120.0)
    assert seen["body"]["routingPreference"] == "TRAFFIC_AWARE" and seen["body"]["departureTime"] == "2030-01-01T18:00:00Z"
    assert "polyline.encodedPolyline" in seen["mask"]

    await GoogleRoutes("K", mock(handler)).route(LatLng(1, 2), LatLng(3, 4), "WALK", None, None)
    assert "routingPreference" not in seen["body"]
    empty = GoogleRoutes("K", mock(lambda r: httpx.Response(200, json={})))
    assert await empty.route(LatLng(1, 2), LatLng(3, 4), "TRANSIT", None, None) is None


async def test_osrm_routes_profiles_and_transit():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(
            200,
            json={"code": "Ok", "routes": [{"duration": 600, "distance": 4000, "geometry": {"coordinates": [[2, 1], [2.5, 1.5], [4, 3]]}}]},
        )

    o = OsrmRoutes("https://osrm.example/", mock(handler))
    rt = await o.route(LatLng(1, 2), LatLng(3, 4), "BICYCLE")
    assert "/routed-bike/route/v1/bike/2.000000,1.000000;4.000000,3.000000" in seen["url"]
    assert rt.points == [(1, 2), (1.5, 2.5), (3, 4)] and rt.seconds == 600
    assert await o.route(LatLng(1, 2), LatLng(3, 4), "TRANSIT") is None  # no transit profile


async def test_route_all_caches_and_survives_failures():
    calls = []

    class Router:
        name = "t"

        async def route(self, origin, dest, mode, departure=None, arrival=None):
            calls.append(mode)
            if mode == "WALK":
                from app.services.google import GoogleError

                raise GoogleError("network", "x")
            if mode == "TRANSIT":
                return None
            return Route([(origin.lat, origin.lng), (dest.lat, dest.lng)], 60, 1000)

    cache = RouteCache()
    trips = [("a", LatLng(1, 1), "DRIVE"), ("b", LatLng(2, 2), "WALK"), ("c", LatLng(3, 3), "TRANSIT")]
    out = await route_all(Router(), cache, trips, LatLng(0, 0), None, None)
    assert out["a"].seconds == 60 and out["b"] is None and out["c"] is None
    out2 = await route_all(Router(), cache, trips, LatLng(0, 0), None, None)
    assert out2["a"] == out["a"]
    # DRIVE and TRANSIT ("no route") were cached; WALK (an error) is retried.
    assert calls == ["DRIVE", "WALK", "TRANSIT", "WALK"]


async def test_photo_uri():
    def handler(req):
        assert req.url.params["skipHttpRedirect"] == "true" and req.url.params["maxWidthPx"] == "480"
        assert req.headers["x-goog-api-key"] == "K"
        return httpx.Response(200, json={"name": "x", "photoUri": "https://lh3.googleusercontent.com/abc"})

    c = GoogleMapClient("K", mock(handler))
    assert await c.photo_uri("places/a/photos/b", 480) == "https://lh3.googleusercontent.com/abc"


# ---- HTTP routes -----------------------------------------------------------
class StubRouter:
    name = "stub"

    async def route(self, origin, dest, mode, departure=None, arrival=None):
        if mode == "TRANSIT":
            return None
        return Route([(origin.lat, origin.lng), ((origin.lat + dest.lat) / 2, origin.lng), (dest.lat, dest.lng)], 900, 6000)


class PhotoMaps:
    async def photo_uri(self, name, w):
        return None if name.endswith("missing") else f"https://img.example/{name.split('/')[-1]}?w={w}"


@pytest.fixture
def app_client(tmp_path):
    def make(router="stub"):
        app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'j.db'}"))

        async def _router():
            yield StubRouter() if router == "stub" else None

        async def _maps():
            yield PhotoMaps()

        app.dependency_overrides[get_router] = _router
        app.dependency_overrides[get_maps] = _maps
        return TestClient(app)

    return make


def _group_with_people(c, meet_at=None):
    g = c.post("/api/groups", json={"name": "T", "travel_mode": "DRIVE", "meet_at": meet_at}).json()
    c.post(f"/api/groups/{g['id']}/members", json={"name": "A", "lat": 12.99, "lng": 77.75})
    c.post(f"/api/groups/{g['id']}/members", json={"name": "B", "lat": 12.93, "lng": 77.62, "travel_mode": "TRANSIT"})
    return g


def test_journeys_route(app_client):
    c = app_client()
    g = _group_with_people(c, (datetime.now(UTC) + timedelta(days=1)).isoformat())
    r = c.post(f"/api/groups/{g['id']}/journeys", json={"lat": 12.95, "lng": 77.7})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "stub" and body["to"] == {"lat": 12.95, "lng": 77.7}
    a, b = body["journeys"]
    assert a["mode"] == "DRIVE" and a["path"][0] == [12.99, 77.75] and a["path"][-1] == [12.95, 77.7]
    assert a["minutes"] == 15.0 and a["meters"] == 6000
    assert b["mode"] == "TRANSIT" and b["path"] is None and b["from"] == {"lat": 12.93, "lng": 77.62}
    assert c.post(f"/api/groups/{g['id']}/journeys", json={"lat": 99, "lng": 0}).status_code == 422
    assert c.post("/api/groups/nope/journeys", json={"lat": 1, "lng": 1}).status_code == 404


def test_journeys_without_router_returns_curve_hints(app_client):
    c = app_client(router=None)
    g = _group_with_people(c)
    body = c.post(f"/api/groups/{g['id']}/journeys", json={"lat": 12.95, "lng": 77.7}).json()
    assert body["provider"] is None and all(j["path"] is None for j in body["journeys"])


def test_photo_proxy(app_client):
    c = app_client()
    r = c.get("/api/photos", params={"name": "places/ChIJabc/photos/AUc_x-1", "w": 400}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://img.example/AUc_x-1?w=400"
    assert "max-age" in r.headers["cache-control"]
    assert c.get("/api/photos", params={"name": "../../etc/passwd"}).status_code == 422
    assert c.get("/api/photos", params={"name": "places/a/photos/missing"}).status_code == 404
