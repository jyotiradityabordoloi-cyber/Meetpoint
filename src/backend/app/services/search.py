"""Place search as you type: Google Places Autocomplete (New) or Photon (OpenStreetMap).

Google is used when a Maps key is configured. Without one, Photon
(https://photon.komoot.io or a self-hosted instance) gives real worldwide
results for free, and Nominatim answers while Photon is down (`osm_fallback`).
Both rank results near an optional bias point, such as the
group's other members or the viewer's approximate IP location.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx

from .geo import LatLng
from .google import GoogleError, _kind_for_status
from .osm_fallback import Breaker, NominatimClient, breaker, is_outage

log = logging.getLogger("meetpoint.search")

AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
AUTOCOMPLETE_FIELD_MASK = (
    "suggestions.placePrediction.placeId,suggestions.placePrediction.text,suggestions.placePrediction.structuredFormat"
)
DETAILS_FIELD_MASK = "id,formattedAddress,location,displayName"
BIAS_RADIUS_M = 50_000.0  # Places API maximum for a circular bias


@dataclass(frozen=True)
class Suggestion:
    id: str
    main: str
    secondary: str
    # Filled when the provider returns coordinates directly (Photon).
    # Google suggestions need a /places/{id} lookup when picked.
    lat: float | None = None
    lng: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedPlace:
    label: str
    lat: float
    lng: float


class SearchProvider(Protocol):
    name: str

    async def autocomplete(self, query: str, bias: LatLng | None, session: str | None, lang: str | None) -> list[Suggestion]: ...

    async def resolve(self, place_id: str, session: str | None) -> ResolvedPlace | None: ...


# --------------------------------------------------------------------------
# Google Places Autocomplete (New)
# --------------------------------------------------------------------------
class GooglePlacesSearch:
    name = "google"

    def __init__(self, api_key: str, http: httpx.AsyncClient):
        self._key = api_key
        self._http = http

    def _headers(self, mask: str) -> dict:
        return {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": mask, "Content-Type": "application/json"}

    async def autocomplete(self, query: str, bias: LatLng | None, session: str | None, lang: str | None) -> list[Suggestion]:
        body: dict = {"input": query, "includeQueryPredictions": False}
        if session:
            body["sessionToken"] = session  # groups keystrokes + details into one billed session
        if lang:
            body["languageCode"] = lang
        if bias:
            body["locationBias"] = {"circle": {"center": {"latitude": bias.lat, "longitude": bias.lng}, "radius": BIAS_RADIUS_M}}
        try:
            r = await self._http.post(AUTOCOMPLETE_URL, json=body, headers=self._headers(AUTOCOMPLETE_FIELD_MASK))
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code >= 400:
            raise GoogleError(_kind_for_status(r.status_code, r.text[:300]), r.text[:300], r.status_code)
        out = []
        for s in (r.json() or {}).get("suggestions", []) or []:
            p = s.get("placePrediction")
            if not p or not p.get("placeId"):
                continue
            fmt = p.get("structuredFormat") or {}
            main = (fmt.get("mainText") or {}).get("text") or (p.get("text") or {}).get("text") or ""
            secondary = (fmt.get("secondaryText") or {}).get("text") or ""
            out.append(Suggestion(id=p["placeId"], main=main, secondary=secondary))
        return out[:5]

    async def resolve(self, place_id: str, session: str | None) -> ResolvedPlace | None:
        params = {"sessionToken": session} if session else None
        try:
            r = await self._http.get(DETAILS_URL.format(place_id=place_id), params=params, headers=self._headers(DETAILS_FIELD_MASK))
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GoogleError(_kind_for_status(r.status_code, r.text[:300]), r.text[:300], r.status_code)
        d = r.json() or {}
        loc = d.get("location") or {}
        if "latitude" not in loc:
            return None
        label = d.get("formattedAddress") or (d.get("displayName") or {}).get("text") or ""
        return ResolvedPlace(label=label, lat=float(loc["latitude"]), lng=float(loc["longitude"]))


# --------------------------------------------------------------------------
# Photon (OpenStreetMap), free and keyless
# --------------------------------------------------------------------------
PHOTON_LANGS = {"en", "de", "fr", "it"}  # languages the public instance supports


def _photon_secondary(p: dict) -> str:
    parts = []
    street = p.get("street")
    if street and p.get("housenumber"):
        street = f"{street} {p['housenumber']}"
    for v in (street, p.get("district") or p.get("locality"), p.get("city"), p.get("state"), p.get("country")):
        if v and v != p.get("name") and v not in parts:
            parts.append(v)
    return ", ".join(parts)


class PhotonSearch:
    name = "photon"

    def __init__(self, base_url: str, http: httpx.AsyncClient, nominatim: NominatimClient | None = None, brk: Breaker = breaker):
        self._url = base_url.rstrip("/") + "/api/"
        self._http = http
        self._nominatim = nominatim
        self._breaker = brk

    async def autocomplete(self, query: str, bias: LatLng | None, session: str | None, lang: str | None) -> list[Suggestion]:
        if self._nominatim is not None and self._breaker.is_open("photon"):
            return await self._backup(query, bias, lang)
        try:
            return await self._photon(query, bias, lang)
        except GoogleError as e:
            if self._nominatim is None or not is_outage(e):
                raise
            self._breaker.trip("photon")
            return await self._backup(query, bias, lang)

    async def _backup(self, query: str, bias: LatLng | None, lang: str | None) -> list[Suggestion]:
        # Nominatim is not made for search-as-you-type: only fuller queries go to it
        if len(query.strip()) < 3:
            return []
        found = await self._nominatim.search(query, bias=bias, limit=5, lang=lang[:2] if lang else None)
        return [Suggestion(id=f["id"], main=f["name"], secondary=f["secondary"], lat=f["lat"], lng=f["lng"]) for f in found]

    async def _photon(self, query: str, bias: LatLng | None, lang: str | None) -> list[Suggestion]:
        params: dict = {"q": query, "limit": 6}
        if bias:
            params.update(lat=f"{bias.lat:.4f}", lon=f"{bias.lng:.4f}", location_bias_scale="0.3")
        if lang and lang[:2] in PHOTON_LANGS:
            params["lang"] = lang[:2]
        try:
            r = await self._http.get(self._url, params=params, headers={"User-Agent": "MeetPoint (open-source)"})
        except httpx.HTTPError as e:
            raise GoogleError("network", type(e).__name__) from e
        if r.status_code >= 400:
            # a 4xx other than 429 is a query Photon cannot answer, not an outage
            kind = "quota" if r.status_code == 429 else ("invalid_argument" if r.status_code < 500 else "unavailable")
            raise GoogleError(kind, r.text[:200], r.status_code)
        out: list[Suggestion] = []
        seen: set[str] = set()
        for f in (r.json() or {}).get("features", []) or []:
            p = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or []
            if len(coords) != 2:
                continue
            main = p.get("name") or " ".join(x for x in (p.get("street"), p.get("housenumber")) if x) or p.get("city")
            if not main:
                continue
            secondary = _photon_secondary(p)
            key = f"{main}|{secondary}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Suggestion(
                    id=f"osm:{p.get('osm_type', '')}{p.get('osm_id', '')}",
                    main=main,
                    secondary=secondary,
                    lat=float(coords[1]),
                    lng=float(coords[0]),
                )
            )
        return out[:5]

    async def resolve(self, place_id: str, session: str | None) -> ResolvedPlace | None:
        return None  # Photon suggestions already carry coordinates
