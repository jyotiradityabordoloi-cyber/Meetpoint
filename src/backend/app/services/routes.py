"""Journey paths (the line each person travels) for drawing on the map.

Google Routes computeRoutes when a Maps key is set (real roads and transit
lines); otherwise OSRM (OpenStreetMap routing, e.g. routing.openstreetmap.de)
when OSRM_URL is set. If neither can route a trip, the client draws a curve.
These calls happen only when someone opens a venue, never during the search.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx

from .geo import LatLng
from .google import GoogleError, _kind_for_status

log = logging.getLogger("meetpoint.routes")

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
ROAD_MODES = {"DRIVE", "TWO_WHEELER"}


@dataclass(frozen=True)
class Route:
    points: list[tuple[float, float]]  # [(lat, lng), ...]
    seconds: float
    meters: float


class JourneyRouter(Protocol):
    name: str

    async def route(
        self, origin: LatLng, dest: LatLng, mode: str, departure: datetime | None, arrival: datetime | None
    ) -> Route | None: ...


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode Google's encoded polyline format into (lat, lng) pairs."""
    coords: list[tuple[float, float]] = []
    index = lat = lng = 0
    factor = 10**precision
    n = len(encoded)
    while index < n:
        for which in (0, 1):
            shift = result = 0
            while True:
                if index >= n:
                    return coords
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lng += delta
        coords.append((lat / factor, lng / factor))
    return coords


def simplify(points: list[tuple[float, float]], max_points: int = 400) -> list[tuple[float, float]]:
    """Keep the payload small: evenly thin long paths, always keeping both ends."""
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleRoutes:
    name = "google"

    def __init__(self, api_key: str, http: httpx.AsyncClient):
        self._key = api_key
        self._http = http

    async def route(self, origin, dest, mode, departure=None, arrival=None) -> Route | None:
        body: dict = {
            "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}}},
            "destination": {"location": {"latLng": {"latitude": dest.lat, "longitude": dest.lng}}},
            "travelMode": mode,
            "polylineQuality": "OVERVIEW",
        }
        if mode in ROAD_MODES:
            body["routingPreference"] = "TRAFFIC_AWARE" if departure else "TRAFFIC_UNAWARE"
            if departure:
                body["departureTime"] = _rfc3339(departure)
        elif mode == "TRANSIT" and arrival:
            body["arrivalTime"] = _rfc3339(arrival)
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": ROUTES_FIELD_MASK}
        try:
            r = await self._http.post(ROUTES_URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code >= 400:
            raise GoogleError(_kind_for_status(r.status_code, r.text[:300]), r.text[:300], r.status_code)
        routes = (r.json() or {}).get("routes") or []
        if not routes:
            return None
        rt = routes[0]
        enc = (rt.get("polyline") or {}).get("encodedPolyline")
        if not enc:
            return None
        dur = str(rt.get("duration", "0s")).rstrip("s") or "0"
        return Route(points=simplify(decode_polyline(enc)), seconds=float(dur), meters=float(rt.get("distanceMeters", 0)))


# OSRM profiles. Public transit has no OSRM profile: the client draws a curve.
OSRM_PROFILES = {
    "DRIVE": ("routed-car", "driving"),
    "TWO_WHEELER": ("routed-car", "driving"),
    "BICYCLE": ("routed-bike", "bike"),
    "WALK": ("routed-foot", "foot"),
}


class OsrmRoutes:
    """OSRM, laid out like routing.openstreetmap.de: {base}/{routed-car}/route/v1/{driving}/..."""

    name = "osrm"

    def __init__(self, base_url: str, http: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._http = http

    async def route(self, origin, dest, mode, departure=None, arrival=None) -> Route | None:
        prof = OSRM_PROFILES.get(mode)
        if prof is None:
            return None
        url = f"{self._base}/{prof[0]}/route/v1/{prof[1]}/{origin.lng:.6f},{origin.lat:.6f};{dest.lng:.6f},{dest.lat:.6f}"
        try:
            r = await self._http.get(
                url, params={"overview": "full", "geometries": "geojson"}, headers={"User-Agent": "MeetPoint (open-source)"}
            )
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code >= 400:
            raise GoogleError("unavailable", r.text[:200], r.status_code)
        d = r.json() or {}
        if d.get("code") != "Ok" or not d.get("routes"):
            return None
        rt = d["routes"][0]
        pts = [(c[1], c[0]) for c in (rt.get("geometry") or {}).get("coordinates") or []]
        if len(pts) < 2:
            return None
        return Route(points=simplify(pts), seconds=float(rt.get("duration", 0)), meters=float(rt.get("distance", 0)))


class RouteCache:
    """Tiny in-process LRU so re-opening a venue doesn't re-bill the same routes."""

    def __init__(self, size: int = 500):
        self.size = size
        self._d: OrderedDict = OrderedDict()

    @staticmethod
    def key(provider, origin, dest, mode, when) -> tuple:
        return (provider, round(origin.lat, 5), round(origin.lng, 5), round(dest.lat, 5), round(dest.lng, 5), mode, when)

    def get(self, k):
        if k in self._d:
            self._d.move_to_end(k)
            return self._d[k]
        return None

    def put(self, k, v) -> None:
        self._d[k] = v
        self._d.move_to_end(k)
        while len(self._d) > self.size:
            self._d.popitem(last=False)


async def route_all(
    router: JourneyRouter,
    cache: RouteCache,
    trips: list[tuple[str, LatLng, str]],
    dest: LatLng,
    departure: datetime | None,
    arrival: datetime | None,
    concurrency: int = 6,
) -> dict[str, Route | None]:
    """Route every (member_id, origin, mode) to dest. Failures become None (client draws a curve)."""
    sem = asyncio.Semaphore(concurrency)
    when = (departure or arrival).isoformat() if (departure or arrival) else None

    async def one(mid, origin, mode):
        k = RouteCache.key(router.name, origin, dest, mode, when)
        hit = cache.get(k)
        if hit is not None:
            return mid, hit or None
        async with sem:
            try:
                rt = await router.route(origin, dest, mode, departure, arrival)
            except GoogleError as e:
                log.info("journey route failed (%s, %s)", router.name, e.kind)
                return mid, None  # transient: don't cache
        cache.put(k, rt or False)  # cache "no route" too
        return mid, rt

    results = await asyncio.gather(*[one(*t) for t in trips])
    return dict(results)
