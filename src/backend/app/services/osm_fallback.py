"""Backups for when Photon (the free OpenStreetMap search) is down or slow.

- Nominatim (OpenStreetMap's own search) for place search, address lookups,
  area names and cafes, restaurants, bars and other places near a point. Its
  public server allows about one request a second and asks for a real
  User-Agent, so calls are spaced out and cached here.
- Overpass (OpenStreetMap's data API) for places near a point, when Nominatim
  fails too. Its public servers are often busy, so it comes last.

Both are free and need no key. They are only used while Photon is failing: a
`Breaker` remembers a Photon failure for five minutes, so later requests
skip straight to the backup instead of waiting for Photon to time out again.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Sequence

import httpx

from . import geo
from .geo import LatLng
from .google import GeocodeResult, GoogleError, Place

log = logging.getLogger("meetpoint.osm_fallback")

UA = {"User-Agent": "MeetPoint (open-source; https://github.com/jyotiradityabordoloi-cyber/Meetpoint)"}


# --------------------------------------------------------------------------
# Breaker: skip a failing service for a while (shared by every request)
# --------------------------------------------------------------------------
class Breaker:
    def __init__(self, cooldown_s: float = 300.0):
        self.cooldown_s = cooldown_s
        self._down_until: dict[str, float] = {}

    def is_open(self, service: str) -> bool:
        """True while `service` failed recently and should be skipped."""
        return time.monotonic() < self._down_until.get(service, 0.0)

    def trip(self, service: str) -> None:
        if not self.is_open(service):
            log.warning("%s failing; using the backup for %.0f s", service, self.cooldown_s)
        self._down_until[service] = time.monotonic() + self.cooldown_s

    def reset(self) -> None:
        self._down_until.clear()


breaker = Breaker()


def is_outage(e: GoogleError) -> bool:
    """A failure that a backup can work around (not a bad request)."""
    return e.kind in ("network", "unavailable", "quota", "bad_response")


# --------------------------------------------------------------------------
# Nominatim
# --------------------------------------------------------------------------
class _Spacer:
    """Keeps calls to one host at least `gap_s` apart (Nominatim: 1 per second)."""

    def __init__(self, gap_s: float):
        self.gap_s = gap_s
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        start = max(now, self._next)
        self._next = start + self.gap_s
        if start > now:
            await asyncio.sleep(start - now)


_nominatim_spacer = _Spacer(1.1)
_nominatim_cache: OrderedDict[tuple, list] = OrderedDict()
_CACHE_MAX = 500


def _cache_get(key: tuple):
    if key in _nominatim_cache:
        _nominatim_cache.move_to_end(key)
        return _nominatim_cache[key]
    return None


def _cache_put(key: tuple, value: list) -> None:
    _nominatim_cache[key] = value
    _nominatim_cache.move_to_end(key)
    while len(_nominatim_cache) > _CACHE_MAX:
        _nominatim_cache.popitem(last=False)


def _area(a: dict) -> str | None:
    return a.get("suburb") or a.get("neighbourhood") or a.get("quarter") or a.get("city_district") or a.get("village")


def _city(a: dict) -> str | None:
    return a.get("city") or a.get("town") or a.get("municipality") or a.get("county") or a.get("state_district")


class NominatimClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient, spacer: _Spacer | None = None):
        self._base = base_url.rstrip("/")
        self._http = http
        self._spacer = spacer or _nominatim_spacer

    async def _get(self, path: str, params: dict) -> object:
        await self._spacer.wait()
        try:
            r = await self._http.get(f"{self._base}{path}", params={**params, "format": "jsonv2"}, headers=UA)
        except httpx.HTTPError as e:
            raise GoogleError("network", f"nominatim {type(e).__name__}") from e
        if r.status_code >= 400:
            raise GoogleError("quota" if r.status_code == 429 else "unavailable", r.text[:200], r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise GoogleError("bad_response", "invalid JSON") from e

    async def search(self, query: str, bias: LatLng | None = None, limit: int = 5, lang: str | None = None) -> list[dict]:
        """Places matching `query`, near `bias` first when given. Each: name, label parts, lat, lng."""
        key = ("search", query.strip().lower(), round(bias.lat, 1) if bias else None, round(bias.lng, 1) if bias else None, limit)
        hit = _cache_get(key)
        if hit is not None:
            return hit
        params: dict = {"q": query, "limit": str(limit), "addressdetails": "1"}
        if lang:
            params["accept-language"] = lang
        if bias:
            # a soft box of about 50 km around the bias point (bounded=0: results outside still come)
            d = 0.45
            params["viewbox"] = f"{bias.lng - d:.4f},{bias.lat + d:.4f},{bias.lng + d:.4f},{bias.lat - d:.4f}"
        data = await self._get("/search", params)
        out = []
        for it in data if isinstance(data, list) else []:
            try:
                lat, lng = float(it["lat"]), float(it["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            a = it.get("address") or {}
            name = it.get("name") or (it.get("display_name") or "").split(",")[0].strip()
            if not name:
                continue
            road = a.get("road")
            secondary = ", ".join(
                dict.fromkeys(
                    x for x in (road if road != name else None, _area(a), _city(a), a.get("state"), a.get("country")) if x and x != name
                )
            )
            out.append(
                {
                    "id": f"osm:{(it.get('osm_type') or '')[:1].upper()}{it.get('osm_id', '')}",
                    "name": name,
                    "secondary": secondary,
                    "lat": lat,
                    "lng": lng,
                }
            )
        _cache_put(key, out)
        return out

    async def nearby(self, center: LatLng, radius_m: float, tags: Sequence[str], tag_to_type: dict[str, str]) -> list[tuple[float, Place]]:
        """Named places with the given tags in a box around `center`, nearest first.

        One request a kind ("[amenity=cafe]"): Nominatim returns at most 40 a
        request, so a kind is never crowded out by another.
        """
        dlat = radius_m / 111_320
        dlng = radius_m / (111_320 * max(0.2, math.cos(math.radians(center.lat))))
        box = f"{center.lng - dlng:.5f},{center.lat + dlat:.5f},{center.lng + dlng:.5f},{center.lat - dlat:.5f}"
        found: dict[str, tuple[float, Place]] = {}
        errors: list[GoogleError] = []
        for tag in dict.fromkeys(tags):
            k, _, v = tag.partition(":")
            key = ("nearby", tag, box)
            items = _cache_get(key)
            if items is None:
                try:
                    data = await self._get(
                        "/search", {"q": f"[{k}={v}]", "viewbox": box, "bounded": "1", "limit": "40", "addressdetails": "1"}
                    )
                except GoogleError as e:
                    errors.append(e)
                    continue
                items = data if isinstance(data, list) else []
                _cache_put(key, items)
            for it in items:
                place = _nominatim_place(it, tag_to_type)
                if place is None:
                    continue
                dist = geo.haversine_km(center, LatLng(place.lat, place.lng)) * 1000
                if dist <= radius_m * 1.05 and (place.id not in found or dist < found[place.id][0]):
                    found[place.id] = (dist, place)
        if errors and len(errors) == len(dict.fromkeys(tags)):
            raise errors[0]
        return sorted(found.values(), key=lambda x: x[0])

    async def reverse(self, point: LatLng) -> list[GeocodeResult]:
        key = ("reverse", round(point.lat, 3), round(point.lng, 3))
        hit = _cache_get(key)
        if hit is not None:
            return hit
        data = await self._get(
            "/reverse", {"lat": f"{point.lat:.6f}", "lon": f"{point.lng:.6f}", "zoom": "16", "addressdetails": "1", "accept-language": "en"}
        )
        if not isinstance(data, dict) or "address" not in data:
            return []
        a = data.get("address") or {}
        comps = []
        if _area(a):
            comps.append({"long_name": _area(a), "short_name": _area(a), "types": ["neighborhood"]})
        if _city(a):
            comps.append({"long_name": _city(a), "short_name": _city(a), "types": ["locality"]})
        if a.get("country_code"):
            comps.append({"long_name": a.get("country", ""), "short_name": a["country_code"].upper(), "types": ["country"]})
        out = [GeocodeResult(label=data.get("name") or _area(a) or "", lat=point.lat, lng=point.lng, components=comps)]
        _cache_put(key, out)
        return out


def _nominatim_place(it: dict, tag_to_type: dict[str, str]) -> Place | None:
    name = (it.get("name") or "").strip()
    try:
        lat, lng = float(it["lat"]), float(it["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not name:
        return None
    ty = tag_to_type.get(f"{it.get('category', '')}:{it.get('type', '')}")
    a = it.get("address") or {}
    street = a.get("road")
    if street and a.get("house_number"):
        street = f"{a['house_number']} {street}"
    address = ", ".join(dict.fromkeys(x for x in (street, _area(a), _city(a)) if x)) or None
    return Place(
        id=f"osm:{(it.get('osm_type') or '')[:1].upper()}{it.get('osm_id', '')}",
        name=name,
        lat=lat,
        lng=lng,
        rating=None,
        ratings=0,
        open_now=None,
        maps_url=None,
        type=ty,
        address=address,
        types=(ty or "",),
    )


# --------------------------------------------------------------------------
# Overpass
# --------------------------------------------------------------------------
class OverpassClient:
    """Named places with given OpenStreetMap tags around a point."""

    def __init__(self, url: str, http: httpx.AsyncClient, max_results: int = 400):
        self._url = url
        self._http = http
        self._max = max_results

    @staticmethod
    def query(center: LatLng, radius_m: float, tags: Sequence[str], max_results: int) -> str:
        by_key: dict[str, list[str]] = {}
        for t in tags:
            k, _, v = t.partition(":")
            by_key.setdefault(k, []).append(v)
        around = f"around:{int(radius_m)},{center.lat:.6f},{center.lng:.6f}"
        parts = "".join(f'nwr["{k}"~"^({"|".join(sorted(set(vs)))})$"]["name"]({around});' for k, vs in by_key.items())
        return f"[out:json][timeout:20];({parts});out center tags qt {max_results};"

    async def nearby(self, center: LatLng, radius_m: float, tags: Sequence[str]) -> list[tuple[float, dict]]:
        """(distance in m, element) pairs, nearest first. Elements carry tags and a position."""
        if not tags:
            return []
        q = self.query(center, radius_m, tags, self._max)
        try:
            r = await self._http.post(self._url, data={"data": q}, headers=UA)
        except httpx.HTTPError as e:
            raise GoogleError("network", f"overpass {type(e).__name__}") from e
        if r.status_code >= 400:
            raise GoogleError("quota" if r.status_code == 429 else "unavailable", r.text[:200], r.status_code)
        try:
            data = r.json() or {}
        except ValueError as e:
            raise GoogleError("bad_response", "invalid JSON") from e
        out = []
        for el in data.get("elements") or []:
            pos = el if "lat" in el else el.get("center") or {}
            if "lat" not in pos or not (el.get("tags") or {}).get("name"):
                continue
            dist = geo.haversine_km(center, LatLng(float(pos["lat"]), float(pos["lon"]))) * 1000
            if dist <= radius_m * 1.05:
                out.append((dist, {**el, "lat": float(pos["lat"]), "lon": float(pos["lon"])}))
        out.sort(key=lambda x: x[0])
        return out


def overpass_place(el: dict, tag_to_type: dict[str, str]) -> Place:
    """An Overpass element as a Place (no rating, photos or live status in OpenStreetMap)."""
    tags = el.get("tags") or {}
    ty = None
    for k in ("amenity", "leisure", "shop", "tourism"):
        if tags.get(k) and f"{k}:{tags[k]}" in tag_to_type:
            ty = tag_to_type[f"{k}:{tags[k]}"]
            break
    street = tags.get("addr:street")
    if street and tags.get("addr:housenumber"):
        street = f"{tags['addr:housenumber']} {street}"
    area = tags.get("addr:suburb") or tags.get("addr:district") or tags.get("addr:neighbourhood")
    address = ", ".join(dict.fromkeys(x for x in (street, area, tags.get("addr:city")) if x)) or None
    return Place(
        id=f"osm:{(el.get('type') or '')[:1].upper()}{el.get('id', '')}",
        name=tags["name"],
        lat=el["lat"],
        lng=el["lon"],
        rating=None,
        ratings=0,
        open_now=None,
        maps_url=None,
        type=ty,
        address=address,
        types=(ty or "",),
    )
