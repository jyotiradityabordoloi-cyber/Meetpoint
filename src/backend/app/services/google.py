"""Async adapters for Google Maps Platform: Routes, Places (New), Geocoding.

Every failure is raised as GoogleError so the engine can pick a fallback.
The engine only depends on the MapClient protocol, so tests inject a fake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

import httpx

from .geo import LatLng

log = logging.getLogger("meetpoint.google")

ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

MATRIX_FIELD_MASK = "originIndex,destinationIndex,duration,distanceMeters,condition,status"
PLACES_FIELD_MASK = ",".join(
    f"places.{f}"
    for f in (
        "id",
        "displayName",
        "location",
        "rating",
        "userRatingCount",
        "currentOpeningHours.openNow",
        "googleMapsUri",
        "primaryType",
        "shortFormattedAddress",
        "types",
        "photos",
        # Same billing tier as rating/openNow (already requested): no extra cost tier.
        "priceLevel",
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "websiteUri",
        "regularOpeningHours.weekdayDescriptions",
    )
)
MAX_PHOTOS = 3
PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
PHOTO_URL = "https://places.googleapis.com/v1/{name}/media"
ROAD_MODES = {"DRIVE", "TWO_WHEELER"}


class GoogleError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind  # "quota", "denied", "invalid_argument", "unavailable", "network", "bad_response"
        self.status = status


@dataclass(frozen=True)
class MatrixElement:
    seconds: float
    meters: float


# matrix[i][j] is the trip from origin i to destination j, or None if no route.
Matrix = list[list[MatrixElement | None]]


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    lat: float
    lng: float
    rating: float | None
    ratings: int
    open_now: bool | None
    maps_url: str | None
    type: str | None
    address: str | None
    # [{"name": "places/../photos/..", "attribution": "Jane D."}], at most MAX_PHOTOS
    photos: tuple = ()
    price_level: int | None = None  # 0 free .. 4 very expensive
    phone: str | None = None
    phone_intl: str | None = None
    website: str | None = None
    hours: tuple = ()  # human-readable lines, Monday first, in the place's language


@dataclass(frozen=True)
class GeocodeResult:
    label: str
    lat: float
    lng: float
    components: list[dict]


class MapClient(Protocol):
    async def route_matrix(
        self,
        origins: Sequence[LatLng],
        destinations: Sequence[LatLng],
        mode: str,
        *,
        traffic_aware: bool,
        departure_time: datetime | None = None,
        arrival_time: datetime | None = None,
    ) -> Matrix: ...

    async def search_nearby(self, center: LatLng, radius_m: float, types: Sequence[str]) -> list[Place]: ...

    async def geocode(self, address: str) -> list[GeocodeResult]: ...

    async def reverse_geocode(self, point: LatLng) -> list[GeocodeResult]: ...


def chunk_matrix(n_orig: int, n_dest: int, limit: int) -> list[tuple[range, range]]:
    """Split an n_orig x n_dest matrix into blocks of at most `limit` elements."""
    if n_orig == 0 or n_dest == 0:
        return []
    dest_block = min(n_dest, limit)
    orig_block = max(1, limit // dest_block)
    blocks = []
    for o in range(0, n_orig, orig_block):
        for d in range(0, n_dest, dest_block):
            blocks.append((range(o, min(o + orig_block, n_orig)), range(d, min(d + dest_block, n_dest))))
    return blocks


def _photos(raw) -> tuple:
    out = []
    for ph in raw or []:
        name = ph.get("name")
        if not name:
            continue
        authors = [a.get("displayName") for a in ph.get("authorAttributions") or [] if a.get("displayName")]
        out.append({"name": name, "attribution": ", ".join(authors) or None})
        if len(out) >= MAX_PHOTOS:
            break
    return tuple(out)


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _waypoint(p: LatLng) -> dict:
    return {"waypoint": {"location": {"latLng": {"latitude": p.lat, "longitude": p.lng}}}}


def _parse_seconds(s: Any) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str) and s.endswith("s"):
        return float(s[:-1] or 0)
    raise ValueError(f"bad duration {s!r}")


def _kind_for_status(status: int, body: str) -> str:
    if status == 429 or "RESOURCE_EXHAUSTED" in body or "OVER_QUERY_LIMIT" in body:
        return "quota"
    if status in (401, 403) or "PERMISSION_DENIED" in body or "REQUEST_DENIED" in body:
        return "denied"
    if status == 400:
        return "invalid_argument"
    return "unavailable"


class GoogleMapClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient, matrix_limits: dict[str, int] | None = None):
        self._key = api_key
        self._http = http
        self._limits = matrix_limits or {"TRANSIT": 100, "*": 625}

    # ---- helpers -------------------------------------------------------
    async def _post(self, url: str, body: dict, field_mask: str) -> Any:
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": field_mask, "Content-Type": "application/json"}
        try:
            r = await self._http.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise GoogleError("network", f"{type(e).__name__}") from e
        if r.status_code >= 400:
            text = r.text[:500]
            raise GoogleError(_kind_for_status(r.status_code, text), text, r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise GoogleError("bad_response", "invalid JSON") from e

    # ---- Routes --------------------------------------------------------
    async def route_matrix(
        self,
        origins: Sequence[LatLng],
        destinations: Sequence[LatLng],
        mode: str,
        *,
        traffic_aware: bool,
        departure_time: datetime | None = None,
        arrival_time: datetime | None = None,
    ) -> Matrix:
        limit = self._limits.get(mode, self._limits["*"])
        out: Matrix = [[None] * len(destinations) for _ in origins]
        for o_rng, d_rng in chunk_matrix(len(origins), len(destinations), limit):
            body: dict[str, Any] = {
                "origins": [_waypoint(origins[i]) for i in o_rng],
                "destinations": [_waypoint(destinations[j]) for j in d_rng],
                "travelMode": mode,
            }
            if mode in ROAD_MODES:
                body["routingPreference"] = "TRAFFIC_AWARE" if traffic_aware else "TRAFFIC_UNAWARE"
                if traffic_aware and departure_time is not None:
                    body["departureTime"] = _rfc3339(departure_time)
            elif mode == "TRANSIT":
                if arrival_time is not None:
                    body["arrivalTime"] = _rfc3339(arrival_time)
                elif departure_time is not None:
                    body["departureTime"] = _rfc3339(departure_time)
            data = await self._post(ROUTES_URL, body, MATRIX_FIELD_MASK)
            if isinstance(data, dict) and "error" in data:
                raise GoogleError("invalid_argument", str(data["error"])[:300])
            if not isinstance(data, list):
                raise GoogleError("bad_response", "route matrix response is not a list")
            for el in data:
                try:
                    oi = int(el.get("originIndex", 0))
                    di = int(el.get("destinationIndex", 0))
                except (TypeError, ValueError):
                    continue
                status = el.get("status") or {}
                if status.get("code"):
                    continue
                if el.get("condition") != "ROUTE_EXISTS" or "duration" not in el:
                    continue
                try:
                    secs = _parse_seconds(el["duration"])
                except ValueError:
                    continue
                out[o_rng.start + oi][d_rng.start + di] = MatrixElement(secs, float(el.get("distanceMeters", 0)))
        return out

    # ---- Places (New) --------------------------------------------------
    async def search_nearby(self, center: LatLng, radius_m: float, types: Sequence[str]) -> list[Place]:
        body = {
            "includedTypes": list(types),
            "maxResultCount": 20,
            "rankPreference": "POPULARITY",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": center.lat, "longitude": center.lng},
                    "radius": float(min(radius_m, 50_000)),
                }
            },
        }
        data = await self._post(PLACES_URL, body, PLACES_FIELD_MASK)
        places = []
        for p in (data or {}).get("places", []) or []:
            loc = p.get("location") or {}
            if "latitude" not in loc or "longitude" not in loc or not p.get("id"):
                continue
            hours = p.get("currentOpeningHours") or {}
            places.append(
                Place(
                    id=p["id"],
                    name=(p.get("displayName") or {}).get("text") or "Unnamed place",
                    lat=float(loc["latitude"]),
                    lng=float(loc["longitude"]),
                    rating=p.get("rating"),
                    ratings=int(p.get("userRatingCount") or 0),
                    open_now=hours.get("openNow"),
                    maps_url=p.get("googleMapsUri"),
                    type=p.get("primaryType") or next(iter(p.get("types") or []), None),
                    address=p.get("shortFormattedAddress"),
                    photos=_photos(p.get("photos")),
                    price_level=PRICE_LEVELS.get(p.get("priceLevel")),
                    phone=p.get("nationalPhoneNumber"),
                    phone_intl=p.get("internationalPhoneNumber"),
                    website=p.get("websiteUri"),
                    hours=tuple((p.get("regularOpeningHours") or {}).get("weekdayDescriptions") or ()),
                )
            )
        return places

    async def photo_uri(self, name: str, max_width: int) -> str | None:
        """Short-lived image URL for a Places photo (no API key in it)."""
        try:
            r = await self._http.get(
                PHOTO_URL.format(name=name),
                params={"maxWidthPx": max_width, "skipHttpRedirect": "true"},
                headers={"X-Goog-Api-Key": self._key},
            )
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GoogleError(_kind_for_status(r.status_code, r.text[:300]), r.text[:300], r.status_code)
        return (r.json() or {}).get("photoUri")

    # ---- Geocoding -----------------------------------------------------
    async def _geocode(self, params: dict) -> list[GeocodeResult]:
        try:
            r = await self._http.get(GEOCODE_URL, params={**params, "key": self._key})
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code >= 400:
            raise GoogleError(_kind_for_status(r.status_code, r.text[:300]), r.text[:300], r.status_code)
        data = r.json()
        status = data.get("status")
        if status == "ZERO_RESULTS":
            return []
        if status != "OK":
            raise GoogleError(_kind_for_status(400 if status == "INVALID_REQUEST" else 503, status or ""), str(status))
        out = []
        for res in data.get("results", []):
            loc = (res.get("geometry") or {}).get("location") or {}
            if "lat" in loc and "lng" in loc:
                out.append(
                    GeocodeResult(
                        label=res.get("formatted_address", ""),
                        lat=float(loc["lat"]),
                        lng=float(loc["lng"]),
                        components=res.get("address_components") or [],
                    )
                )
        return out

    async def geocode(self, address: str) -> list[GeocodeResult]:
        return (await self._geocode({"address": address}))[:5]

    async def reverse_geocode(self, point: LatLng) -> list[GeocodeResult]:
        return await self._geocode({"latlng": f"{point.lat:.6f},{point.lng:.6f}"})
