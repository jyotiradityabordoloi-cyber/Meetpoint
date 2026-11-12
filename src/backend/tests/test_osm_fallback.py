"""Backups for Photon: Overpass for venues, Nominatim for search and area names."""

import httpx
import pytest

from app.services import osm_fallback
from app.services.geo import LatLng
from app.services.google import GoogleError
from app.services.osm import OsmMapClient
from app.services.osm_fallback import Breaker, NominatimClient, OverpassClient, _Spacer
from app.services.search import PhotonSearch

CENTRE = LatLng(12.9716, 77.5946)  # central Bengaluru


@pytest.fixture(autouse=True)
def _fresh_cache():
    osm_fallback._nominatim_cache.clear()
    yield
    osm_fallback._nominatim_cache.clear()


def node(osm_id, name, lat, lng, **tags):
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lng, "tags": {"name": name, **tags}}


def way(osm_id, name, lat, lng, **tags):
    return {"type": "way", "id": osm_id, "center": {"lat": lat, "lon": lng}, "tags": {"name": name, **tags}}


OVERPASS_ELEMENTS = [
    way(20, "Cubbon Park", 12.9763, 77.5929, leisure="park"),
    node(10, "Koshy's", 12.9752, 77.6010, amenity="restaurant", **{"addr:street": "St Marks Road", "addr:city": "Bengaluru"}),
    node(11, "Matteo Coffea", 12.9718, 77.5990, amenity="cafe"),
    node(12, "Far Away Cafe", 13.20, 77.70, amenity="cafe"),  # outside the radius
    {"type": "node", "id": 13, "lat": 12.972, "lon": 77.595, "tags": {"amenity": "cafe"}},  # no name
]


def as_nominatim(el):
    """The same place as Nominatim lists it."""
    tags = el["tags"]
    pos = el if "lat" in el else el["center"]
    key = next(k for k in ("amenity", "leisure") if k in tags)
    address = {"road": tags.get("addr:street"), "city": tags.get("addr:city")}
    return {
        "osm_type": el["type"],
        "osm_id": el["id"],
        "lat": str(pos["lat"]),
        "lon": str(pos["lon"]),
        "name": tags.get("name", ""),
        "category": key,
        "type": tags[key],
        "address": {k: v for k, v in address.items() if v},
    }


class Recorder:
    """A mock transport: Photon fails (or answers), Nominatim and Overpass answer (or fail)."""

    def __init__(self, photon_status=None, photon_exc=None, nominatim_status=None):
        self.photon_status = photon_status
        self.photon_exc = photon_exc
        self.nominatim_status = nominatim_status
        self.calls: list[str] = []
        self.overpass_query = None
        self.nominatim_queries: list[str] = []

    def __call__(self, req: httpx.Request):
        host = req.url.host
        self.calls.append(host)
        if host == "photon.example":
            if self.photon_exc:
                raise self.photon_exc
            return httpx.Response(self.photon_status or 200, json={"features": []})
        if host == "overpass.example":
            self.overpass_query = dict(httpx.QueryParams(req.content.decode()))["data"]
            return httpx.Response(200, json={"elements": OVERPASS_ELEMENTS})
        if host == "nominatim.example":
            if self.nominatim_status:
                return httpx.Response(self.nominatim_status, text="busy")
            q = req.url.params.get("q", "")
            if q.startswith("["):  # places of one kind in a box: "[amenity=cafe]"
                self.nominatim_queries.append(q)
                assert req.url.params["bounded"] == "1" and req.url.params["viewbox"]
                k, v = q.strip("[]").split("=")
                return httpx.Response(200, json=[as_nominatim(e) for e in OVERPASS_ELEMENTS if e["tags"].get(k) == v])
            if req.url.path == "/reverse":
                return httpx.Response(
                    200,
                    json={"name": "", "address": {"suburb": "Shivajinagar", "city": "Bengaluru", "country": "India", "country_code": "in"}},
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "osm_type": "relation",
                        "osm_id": 7,
                        "lat": "12.9784",
                        "lon": "77.6408",
                        "name": "Indiranagar",
                        "address": {"suburb": "Indiranagar", "city": "Bengaluru", "state": "Karnataka", "country": "India"},
                    },
                    {"osm_type": "node", "osm_id": 8, "lat": "bad", "lon": "77.6", "name": "Broken"},
                ],
            )
        raise AssertionError(f"unexpected host {host}")


def osm_client(rec: Recorder, brk: Breaker, backups=True) -> OsmMapClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    return OsmMapClient(
        "https://photon.example",
        http,
        retry_delay_s=0,
        nominatim=NominatimClient("https://nominatim.example", http, spacer=_Spacer(0)) if backups else None,
        overpass=OverpassClient("https://overpass.example/api/interpreter", http) if backups else None,
        brk=brk,
    )


def test_overpass_query_groups_tags_by_key():
    q = OverpassClient.query(CENTRE, 3000, ["amenity:cafe", "amenity:restaurant", "leisure:park"], 400)
    assert q.startswith("[out:json][timeout:20];(")
    assert 'nwr["amenity"~"^(cafe|restaurant)$"]["name"](around:3000,12.971600,77.594600);' in q
    assert 'nwr["leisure"~"^(park)$"]["name"](around:3000,12.971600,77.594600);' in q
    assert q.endswith(");out center tags qt 400;")


def check_places(places):
    assert [p.name for p in places] == ["Matteo Coffea", "Cubbon Park", "Koshy's"]  # nearest first, in range, named
    koshys = places[2]
    assert koshys.id == "osm:N10" and koshys.type == "restaurant" and koshys.rating is None
    assert koshys.address == "St Marks Road, Bengaluru"
    assert places[1].id == "osm:W20" and places[1].type == "park"
    assert places[0].type == "cafe"


async def test_photon_down_venues_come_from_nominatim_and_breaker_skips_photon():
    rec, brk = Recorder(photon_status=503), Breaker()
    c = osm_client(rec, brk)
    places = await c.search_nearby(CENTRE, 1500, ["cafe", "restaurant", "park"])

    assert brk.is_open("photon")
    check_places(places)
    assert sorted(rec.nominatim_queries) == ["[amenity=cafe]", "[amenity=restaurant]", "[leisure=park]"]  # one per kind
    assert "overpass.example" not in rec.calls

    # the next area skips Photon at once instead of waiting for it to fail again
    rec.calls.clear()
    await c.search_nearby(LatLng(12.93, 77.62), 1500, ["cafe"])
    assert "photon.example" not in rec.calls and "nominatim.example" in rec.calls


async def test_photon_and_nominatim_down_venues_come_from_overpass():
    rec, brk = Recorder(photon_status=503, nominatim_status=503), Breaker()
    places = await osm_client(rec, brk).search_nearby(CENTRE, 1500, ["cafe", "restaurant", "park"])
    check_places(places)
    assert "amenity" in rec.overpass_query and "leisure" in rec.overpass_query


async def test_photon_timeout_falls_back_without_a_retry():
    rec, brk = Recorder(photon_exc=httpx.ReadTimeout("slow")), Breaker()
    places = await osm_client(rec, brk).search_nearby(CENTRE, 1500, ["cafe"])
    assert rec.calls.count("photon.example") == 1  # no second wait on a dead server
    assert [p.name for p in places] == ["Matteo Coffea"]  # only cafes were asked for


async def test_without_backups_photon_errors_still_raise():
    rec = Recorder(photon_status=503)
    with pytest.raises(GoogleError):
        await osm_client(rec, Breaker(), backups=False).search_nearby(CENTRE, 1500, ["cafe"])
    assert "overpass.example" not in rec.calls and "nominatim.example" not in rec.calls


async def test_photon_working_uses_no_backup():
    rec, brk = Recorder(photon_status=200), Breaker()
    assert await osm_client(rec, brk).search_nearby(CENTRE, 1500, ["cafe"]) == []
    assert not brk.is_open("photon") and set(rec.calls) == {"photon.example"}


async def test_area_names_and_lookups_fall_back_to_nominatim():
    rec, brk = Recorder(photon_status=502), Breaker()
    c = osm_client(rec, brk)
    [area] = await c.reverse_geocode(CENTRE)
    names = {tuple(x["types"]): x["long_name"] for x in area.components}
    assert names == {("neighborhood",): "Shivajinagar", ("locality",): "Bengaluru", ("country",): "India"}
    assert next(x for x in area.components if x["types"] == ["country"])["short_name"] == "IN"

    [hit] = await c.geocode("Indiranagar")
    assert hit.label == "Indiranagar, Bengaluru, Karnataka, India" and (hit.lat, hit.lng) == (12.9784, 77.6408)


async def test_search_box_falls_back_to_nominatim():
    rec, brk = Recorder(photon_exc=httpx.ConnectTimeout("down")), Breaker()
    http = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    s = PhotonSearch(
        "https://photon.example", http, nominatim=NominatimClient("https://nominatim.example", http, spacer=_Spacer(0)), brk=brk
    )

    [sug] = await s.autocomplete("indira", LatLng(12.97, 77.59), None, "en-IN")
    assert (sug.id, sug.main, sug.secondary) == ("osm:R7", "Indiranagar", "Bengaluru, Karnataka, India")
    assert (sug.lat, sug.lng) == (12.9784, 77.6408)
    assert brk.is_open("photon")

    rec.calls.clear()
    assert await s.autocomplete("in", None, None, None) == []  # too short for Nominatim
    await s.autocomplete("indira", LatLng(12.97, 77.59), None, "en-IN")
    assert rec.calls == []  # Photon skipped while down; the same query is cached


async def test_search_box_bad_request_does_not_fall_back():
    def handler(req):
        if req.url.host == "photon.example":
            return httpx.Response(400, text="bad query")
        raise AssertionError("no backup for a bad request")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    brk = Breaker()
    s = PhotonSearch(
        "https://photon.example", http, nominatim=NominatimClient("https://nominatim.example", http, spacer=_Spacer(0)), brk=brk
    )
    with pytest.raises(GoogleError):
        await s.autocomplete("x" * 5, None, None, None)


def test_breaker_cools_down(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(osm_fallback.time, "monotonic", lambda: now[0])
    b = Breaker(cooldown_s=120)
    assert not b.is_open("photon")
    b.trip("photon")
    now[0] += 119
    assert b.is_open("photon")
    now[0] += 2
    assert not b.is_open("photon")


async def test_spacer_keeps_nominatim_calls_a_second_apart(monkeypatch):
    now = [0.0]
    slept = []

    async def fake_sleep(s):
        slept.append(round(s, 2))
        now[0] += s

    monkeypatch.setattr(osm_fallback.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(osm_fallback.asyncio, "sleep", fake_sleep)
    sp = _Spacer(1.1)
    for _ in range(3):
        await sp.wait()
    assert slept == [1.1, 1.1]
