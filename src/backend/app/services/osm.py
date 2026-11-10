"""OpenStreetMap map client for running without a Google Maps key.

Uses Photon (https://photon.komoot.io or a self-hosted instance) for:
- venues near a point: reverse search filtered by OSM tag (cafes, restaurants,
  bars, pubs, parks, malls...), nearest first, real names and addresses;
- area names for the results heading (reverse geocoding);
- address lookups.

OpenStreetMap has no ratings, photos or live opening status, and there is no
free routing matrix here, so travel times are estimated from distance
(`estimates_only`). Adding a Google key switches everything to Google.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import httpx

from . import geo
from .geo import LatLng
from .google import GeocodeResult, GoogleError, Matrix, Place

log = logging.getLogger("meetpoint.osm")

# Places (New) type ids used by the app -> OpenStreetMap key:value tags
TYPE_TO_OSM: dict[str, list[str]] = {
    "cafe": ["amenity:cafe"],
    "coffee_shop": ["amenity:cafe"],
    "restaurant": ["amenity:restaurant"],
    "bar": ["amenity:bar"],
    "pub": ["amenity:pub"],
    "park": ["leisure:park"],
    "shopping_mall": ["shop:mall"],
    "bakery": ["shop:bakery"],
    "library": ["amenity:library"],
    "museum": ["tourism:museum"],
}
OSM_VALUE_TO_TYPE = {
    "cafe": "cafe",
    "restaurant": "restaurant",
    "bar": "bar",
    "pub": "pub",
    "park": "park",
    "mall": "shopping_mall",
    "bakery": "bakery",
    "library": "library",
    "museum": "museum",
}
# Fetch once at this radius (nearest first) and reuse it when the engine widens
# its search step by step (800 m -> 1.5 km -> 3 km).
FETCH_RADIUS_M = 3000.0
UA = {"User-Agent": "MeetPoint (open-source; https://github.com/jyotiradityabordoloi-cyber/Meetpoint)"}


def _address(p: dict) -> str | None:
    street = p.get("street")
    if street and p.get("housenumber"):
        street = f"{p['housenumber']} {street}"
    parts = [x for x in (street, p.get("district") or p.get("locality"), p.get("city")) if x]
    return ", ".join(dict.fromkeys(parts)) or None


class OsmMapClient:
    """MapClient built on Photon. Travel times are estimates (no routing matrix)."""

    estimates_only = True

    def __init__(self, photon_url: str, http: httpx.AsyncClient, concurrency: int = 1, retry_delay_s: float = 1.2):
        self._base = photon_url.rstrip("/")
        self._http = http
        # One request at a time: the shared public server rejects bursts.
        self._sem = asyncio.Semaphore(concurrency)
        self._retry_delay_s = retry_delay_s
        self._cache: dict[tuple, asyncio.Task] = {}  # per request: (lat, lng, tags, radius) -> fetch
        self._centres: list[tuple[float, float]] = []  # areas in the order the engine asks (fairest first)

    async def _get(self, path: str, params: list[tuple[str, str]]) -> dict:
        async with self._sem:
            for attempt in (1, 2):
                try:
                    r = await self._http.get(f"{self._base}{path}", params=params, headers=UA)
                except httpx.HTTPError as e:
                    if attempt == 2:
                        raise GoogleError("network", type(e).__name__) from e
                    await asyncio.sleep(self._retry_delay_s)
                    continue
                if r.status_code in (429, 502, 503, 504) and attempt == 1:
                    log.info("photon busy (%s), retrying once", r.status_code)
                    await asyncio.sleep(self._retry_delay_s)
                    continue
                break
        if r.status_code >= 400:
            raise GoogleError("quota" if r.status_code == 429 else "unavailable", r.text[:200], r.status_code)
        try:
            return r.json() or {}
        except ValueError as e:
            raise GoogleError("bad_response", "invalid JSON") from e

    async def route_matrix(self, origins, destinations, mode, **kw) -> Matrix:
        # The engine checks `estimates_only` and never calls this.
        raise GoogleError("unavailable", "no routing matrix without a Google key")

    async def search_nearby(self, center: LatLng, radius_m: float, types: Sequence[str]) -> list[Place]:
        tags = list(dict.fromkeys(t for ty in types for t in TYPE_TO_OSM.get(ty, [])))
        if not tags:
            return []
        # Photon is a shared fair-use service, so keep it to ~5 requests a search:
        # the fairest area (asked about first) gets one request per kind of place,
        # so nearby bars and pubs are not crowded out by restaurants; the other
        # areas get one combined request each.
        ckey = (round(center.lat, 5), round(center.lng, 5))
        if ckey not in self._centres:
            self._centres.append(ckey)
        fetch_r = max(radius_m, FETCH_RADIUS_M)
        if len(tags) > 1 and self._centres.index(ckey) == 0:
            groups = [(t,) for t in tags]
            limit = 12
        else:
            groups = [tuple(tags)]
            limit = 30
        batches = await asyncio.gather(*[self._cached_nearby(center, fetch_r, g, limit) for g in groups], return_exceptions=True)
        errors = [b for b in batches if isinstance(b, BaseException)]
        if errors and len(errors) == len(batches):
            raise errors[0]
        merged: dict[str, tuple[float, Place]] = {}
        for batch in batches:
            if isinstance(batch, BaseException):
                continue
            for dist, place in batch:
                if dist > radius_m * 1.05:
                    continue  # fetched wider than asked; keep only what's in range
                key = place.id if place.id.startswith("osm:") and place.id != "osm:" else f"{place.name}|{place.lat:.4f},{place.lng:.4f}"
                if key not in merged or dist < merged[key][0]:
                    merged[key] = (dist, place)
        return [pl for _, pl in sorted(merged.values(), key=lambda x: x[0])]

    def _cached_nearby(self, center: LatLng, radius_m: float, tags: tuple[str, ...], limit: int) -> asyncio.Task:
        key = (round(center.lat, 5), round(center.lng, 5), tags, radius_m, limit)
        task = self._cache.get(key)
        if task is None or (task.done() and task.exception() is not None):
            task = asyncio.ensure_future(self._nearby(center, radius_m, tags, limit))
            self._cache[key] = task
        return task

    async def _nearby(self, center: LatLng, radius_m: float, tags: tuple[str, ...], limit: int) -> list[tuple[float, Place]]:
        params = [
            ("lat", f"{center.lat:.6f}"),
            ("lon", f"{center.lng:.6f}"),
            ("radius", f"{radius_m / 1000:.2f}"),
            ("limit", str(limit)),
            ("lang", "en"),
        ] + [("osm_tag", t) for t in tags]
        data = await self._get("/reverse", params)
        out: list[tuple[float, Place]] = []
        for f in data.get("features") or []:
            p = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or []
            name = (p.get("name") or "").strip()
            if len(coords) != 2 or not name:
                continue
            lat, lng = float(coords[1]), float(coords[0])
            dist = geo.haversine_km(center, LatLng(lat, lng)) * 1000
            if dist > radius_m * 1.05:
                continue
            pid = f"osm:{p.get('osm_type', '')}{p.get('osm_id', '')}" if p.get("osm_id") else f"{name}|{lat:.4f},{lng:.4f}"
            out.append(
                (
                    dist,
                    Place(
                        id=pid,
                        name=name,
                        lat=lat,
                        lng=lng,
                        rating=None,
                        ratings=0,
                        open_now=None,
                        maps_url=None,  # client links to the coordinates in Google Maps
                        type=OSM_VALUE_TO_TYPE.get(p.get("osm_value", ""), p.get("osm_value")),
                        address=_address(p),
                    ),
                )
            )
        return out

    async def reverse_geocode(self, point: LatLng) -> list[GeocodeResult]:
        data = await self._get("/reverse", [("lat", f"{point.lat:.6f}"), ("lon", f"{point.lng:.6f}"), ("lang", "en")])
        feats = data.get("features") or []
        if not feats:
            return []
        p = feats[0].get("properties") or {}
        comps = []
        area = p.get("locality") or p.get("district")
        if area:
            comps.append({"long_name": area, "short_name": area, "types": ["neighborhood"]})
        city = p.get("city") or p.get("county")
        if city and city == city.lower():
            city = city.title()
        if city:
            comps.append({"long_name": city, "short_name": city, "types": ["locality"]})
        if p.get("countrycode"):
            comps.append({"long_name": p.get("country", ""), "short_name": p["countrycode"], "types": ["country"]})
        return [GeocodeResult(label=p.get("name", ""), lat=point.lat, lng=point.lng, components=comps)]

    async def geocode(self, address: str) -> list[GeocodeResult]:
        data = await self._get("/api", [("q", address), ("limit", "5"), ("lang", "en")])
        out = []
        for f in data.get("features") or []:
            p = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or []
            if len(coords) != 2:
                continue
            label = ", ".join(
                dict.fromkeys(x for x in (p.get("name"), p.get("street"), p.get("city"), p.get("state"), p.get("country")) if x)
            )
            out.append(GeocodeResult(label=label, lat=float(coords[1]), lng=float(coords[0]), components=[]))
        return out
