import json

import httpx
import pytest

from app.services.geo import LatLng
from app.services.google import (
    MATRIX_FIELD_MASK,
    GoogleError,
    GoogleMapClient,
    chunk_matrix,
)
from app.services.region import region_from_results


def client_with(handler):
    return GoogleMapClient("KEY", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_chunk_matrix_respects_limit():
    for n_o, n_d, lim in [(25, 10, 100), (25, 20, 625), (7, 150, 100), (1, 1, 100)]:
        blocks = chunk_matrix(n_o, n_d, lim)
        assert all(len(o) * len(d) <= lim for o, d in blocks)
        assert sum(len(o) * len(d) for o, d in blocks) == n_o * n_d


async def test_route_matrix_request_and_parse():
    seen = {}

    def handler(req: httpx.Request):
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json=[
                {"originIndex": 0, "destinationIndex": 0, "duration": "600s", "distanceMeters": 5000, "condition": "ROUTE_EXISTS"},
                {"originIndex": 0, "destinationIndex": 1, "condition": "ROUTE_NOT_FOUND"},
                {"originIndex": 1, "destinationIndex": 0, "status": {"code": 5}, "condition": "ROUTE_EXISTS", "duration": "1s"},
                {"originIndex": 1, "destinationIndex": 1, "duration": "90s", "distanceMeters": 800, "condition": "ROUTE_EXISTS"},
            ],
        )

    c = client_with(handler)
    m = await c.route_matrix([LatLng(1, 1), LatLng(2, 2)], [LatLng(3, 3), LatLng(4, 4)], "WALK", traffic_aware=True)
    assert m[0][0].seconds == 600 and m[0][0].meters == 5000
    assert m[0][1] is None and m[1][0] is None
    assert m[1][1].seconds == 90
    assert seen["headers"]["x-goog-fieldmask"] == MATRIX_FIELD_MASK
    assert seen["headers"]["x-goog-api-key"] == "KEY"
    assert "routingPreference" not in seen["body"]  # not allowed for WALK
    assert seen["body"]["origins"][0]["waypoint"]["location"]["latLng"] == {"latitude": 1, "longitude": 1}


async def test_route_matrix_errors_map_to_google_error():
    c = client_with(lambda req: httpx.Response(429, text="RESOURCE_EXHAUSTED"))
    with pytest.raises(GoogleError) as e:
        await c.route_matrix([LatLng(0, 0)], [LatLng(0, 1)], "DRIVE", traffic_aware=False)
    assert e.value.kind == "quota"

    def boom(req):
        raise httpx.ConnectTimeout("t")

    with pytest.raises(GoogleError) as e:
        await client_with(boom).route_matrix([LatLng(0, 0)], [LatLng(0, 1)], "DRIVE", traffic_aware=False)
    assert e.value.kind == "network"


async def test_search_nearby_parses_places():
    def handler(req):
        body = json.loads(req.content)
        assert body["locationRestriction"]["circle"]["radius"] == 800
        assert body["rankPreference"] == "POPULARITY"
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "id": "abc",
                        "displayName": {"text": "Café"},
                        "location": {"latitude": 1.0, "longitude": 2.0},
                        "rating": 4.5,
                        "userRatingCount": 100,
                        "currentOpeningHours": {"openNow": True},
                        "googleMapsUri": "https://maps.google.com/?cid=1",
                        "primaryType": "cafe",
                    },
                    {"id": "nolocation"},
                ]
            },
        )

    places = await client_with(handler).search_nearby(LatLng(1, 2), 800, ["cafe"])
    assert len(places) == 1 and places[0].name == "Café" and places[0].open_now is True

    empty = await client_with(lambda r: httpx.Response(200, json={})).search_nearby(LatLng(1, 2), 800, ["cafe"])
    assert empty == []


async def test_geocode_zero_results_is_not_error_and_region_parsing():
    c = client_with(lambda r: httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []}))
    assert await c.geocode("nowhere") == []

    payload = {
        "status": "OK",
        "results": [
            {
                "formatted_address": "Brooklyn, NY, USA",
                "geometry": {"location": {"lat": 40.6, "lng": -73.9}},
                "address_components": [
                    {"long_name": "Williamsburg", "short_name": "Williamsburg", "types": ["neighborhood"]},
                    {"long_name": "Brooklyn", "short_name": "Brooklyn", "types": ["sublocality_level_1", "sublocality"]},
                    {"long_name": "New York", "short_name": "New York", "types": ["locality"]},
                    {"long_name": "United States", "short_name": "US", "types": ["country"]},
                ],
            }
        ],
    }
    res = await client_with(lambda r: httpx.Response(200, json=payload)).reverse_geocode(LatLng(40.6, -73.9))
    region = region_from_results(res)
    assert region == {"area": "Williamsburg", "locality": "New York", "country_code": "US", "units": "imperial"}

    denied = client_with(lambda r: httpx.Response(200, json={"status": "REQUEST_DENIED"}))
    with pytest.raises(GoogleError):
        await denied.geocode("x")
