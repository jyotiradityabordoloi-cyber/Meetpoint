import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_search
from app.main import create_app
from app.services.geo import LatLng
from app.services.locate import ApproxLocation, GeoIpDb, from_headers, is_public_ip
from app.services.search import GooglePlacesSearch, PhotonSearch, ResolvedPlace, Suggestion


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- providers -------------------------------------------------------------
async def test_photon_parses_and_biases():
    seen = {}

    def handler(req: httpx.Request):
        seen["params"] = dict(req.url.params)
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "osm_type": "R",
                            "osm_id": 19883364,
                            "name": "Whitefield",
                            "county": "Bangalore East",
                            "state": "Karnataka",
                            "country": "India",
                        },
                        "geometry": {"coordinates": [77.7497, 12.9696]},
                    },
                    {
                        "properties": {"name": "Whitefield", "state": "Karnataka", "country": "India", "county": "Bangalore East"},
                        "geometry": {"coordinates": [77.75, 12.97]},
                    },  # duplicate label -> dropped
                    {
                        "properties": {"street": "MG Road", "housenumber": "12", "city": "Bengaluru"},
                        "geometry": {"coordinates": [77.6, 12.97]},
                    },
                    {"properties": {}, "geometry": {"coordinates": [1, 2]}},  # nameless -> dropped
                ]
            },
        )

    s = PhotonSearch("https://photon.example/", mock(handler))
    out = await s.autocomplete("whitefield", LatLng(12.97, 77.59), None, "en-IN")
    assert seen["params"]["lat"] == "12.9700" and seen["params"]["lon"] == "77.5900"
    assert seen["params"]["lang"] == "en"
    assert [o.main for o in out] == ["Whitefield", "MG Road 12"]
    assert out[0].secondary == "Karnataka, India" and out[0].lat == 12.9696 and out[0].lng == 77.7497
    assert out[0].id == "osm:R19883364"
    assert await s.resolve("osm:R1", None) is None


async def test_google_autocomplete_and_details():
    seen = {}

    def handler(req: httpx.Request):
        if req.method == "POST":
            seen["body"] = json.loads(req.content)
            seen["mask"] = req.headers["x-goog-fieldmask"]
            return httpx.Response(
                200,
                json={
                    "suggestions": [
                        {
                            "placePrediction": {
                                "placeId": "abc",
                                "text": {"text": "Whitefield, Bengaluru, Karnataka, India"},
                                "structuredFormat": {
                                    "mainText": {"text": "Whitefield"},
                                    "secondaryText": {"text": "Bengaluru, Karnataka, India"},
                                },
                            }
                        },
                        {"queryPrediction": {"text": {"text": "whitefield cafes"}}},
                    ]
                },
            )
        seen["details_url"] = str(req.url)
        return httpx.Response(200, json={"formattedAddress": "Whitefield, Bengaluru", "location": {"latitude": 12.97, "longitude": 77.75}})

    g = GooglePlacesSearch("KEY", mock(handler))
    out = await g.autocomplete("whitef", LatLng(12.9, 77.6), "sess-1", "en")
    assert out == [Suggestion(id="abc", main="Whitefield", secondary="Bengaluru, Karnataka, India")]
    assert seen["body"]["sessionToken"] == "sess-1"
    assert seen["body"]["locationBias"]["circle"]["center"] == {"latitude": 12.9, "longitude": 77.6}
    assert "placePrediction" in seen["mask"]
    place = await g.resolve("abc", "sess-1")
    assert place == ResolvedPlace(label="Whitefield, Bengaluru", lat=12.97, lng=77.75)
    assert "sessionToken=sess-1" in seen["details_url"]


# ---- IP location -----------------------------------------------------------
def test_location_from_edge_headers():
    cf = from_headers({"cf-iplatitude": "12.9716", "cf-iplongitude": "77.5946", "cf-ipcity": "Bengaluru", "cf-ipcountry": "IN"})
    assert cf == ApproxLocation(12.9716, 77.5946, "Bengaluru", "IN", "cloudflare")
    v = from_headers({"x-vercel-ip-latitude": "51.5", "x-vercel-ip-longitude": "-0.12", "x-vercel-ip-city": "London%20City"})
    assert v.source == "vercel" and v.city == "London City"
    g = from_headers({"x-appengine-citylatlong": "-23.55,-46.63", "x-appengine-country": "BR"})
    assert g.source == "gcp" and g.lat == -23.55
    assert from_headers({"cf-iplatitude": "abc", "cf-iplongitude": "1"}) is None
    assert from_headers({"cf-iplatitude": "0", "cf-iplongitude": "0"}) is None
    assert from_headers({}) is None
    assert ApproxLocation(12.97161, 77.59461, None, None, "x").to_dict()["lat"] == 12.97


def test_public_ip_and_missing_geoip_db():
    assert is_public_ip("8.8.8.8")
    assert not is_public_ip("127.0.0.1") and not is_public_ip("192.168.1.4") and not is_public_ip("nonsense")
    assert GeoIpDb("does-not-exist.mmdb").lookup("8.8.8.8") is None
    assert GeoIpDb("").lookup("8.8.8.8") is None


# ---- HTTP routes -----------------------------------------------------------
class FakeSearch:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def autocomplete(self, query, bias, session, lang):
        self.calls.append((query, bias, session))
        return [
            Suggestion(id="g1", main=f"{query.title()} Park", secondary="Somewhere"),
            Suggestion(id="osm:N1", main=f"{query.title()} Road", secondary="Elsewhere", lat=1.0, lng=2.0),
        ]

    async def resolve(self, place_id, session):
        return ResolvedPlace("Resolved Park, Somewhere", 3.0, 4.0) if place_id == "g1" else None


@pytest.fixture
def client(tmp_path):
    def make(search="fake", **kw):
        app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'p.db'}", **kw))
        fake = FakeSearch() if search == "fake" else None

        async def _search():
            yield fake

        app.dependency_overrides[get_search] = _search
        return TestClient(app), fake

    return make


def test_autocomplete_route(client):
    c, fake = client()
    r = c.get("/api/autocomplete", params={"q": "white", "lat": 12.9, "lng": 77.6, "session": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fake" and [s["main"] for s in body["suggestions"]] == ["White Park", "White Road"]
    assert fake.calls[0] == ("white", LatLng(12.9, 77.6), "s1")
    assert c.get("/api/autocomplete", params={"q": "w"}).status_code == 422
    assert c.get("/api/autocomplete", params={"q": "white", "lat": 99}).status_code == 422


def test_place_details_route(client):
    c, _ = client()
    assert c.get("/api/places/g1").json() == {"label": "Resolved Park, Somewhere", "lat": 3.0, "lng": 4.0}
    r = c.get("/api/places/nope")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "PLACE_NOT_FOUND"


def test_search_unavailable(client):
    c, _ = client(search=None)
    r = c.get("/api/autocomplete", params={"q": "white"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "SEARCH_UNAVAILABLE"


def test_locate_route(client):
    c, _ = client()
    assert c.get("/api/locate").json() == {"located": False}  # test client is not a public IP
    r = c.get("/api/locate", headers={"cf-iplatitude": "12.9716", "cf-iplongitude": "77.5946", "cf-ipcountry": "IN"})
    assert r.json() == {"located": True, "lat": 12.97, "lng": 77.59, "city": None, "country_code": "IN", "source": "cloudflare"}

    c2, _ = client(trust_geo_headers=False)
    assert c2.get("/api/locate", headers={"cf-iplatitude": "1", "cf-iplongitude": "2"}).json() == {"located": False}


def test_health_reports_search_provider(client):
    c, _ = client(photon_url="https://photon.example")
    assert c.get("/health").json()["search"] == "photon"
    c2, _ = client(google_maps_api_key="k")
    assert c2.get("/health").json()["search"] == "google"
