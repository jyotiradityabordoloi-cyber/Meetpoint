"""Search-as-you-type and approximate visitor location."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request

from ..config import Settings
from ..deps import client_ip, get_search, get_settings_dep
from ..errors import ApiError
from ..services.geo import LatLng
from ..services.google import GoogleError
from ..services.locate import from_headers, is_public_ip
from ..services.search import SearchProvider

log = logging.getLogger("meetpoint.api")
router = APIRouter(prefix="/api", tags=["places"])

UNAVAILABLE = "Place search isn't available. Use your location or drop a pin on the map."


@router.get("/autocomplete")
async def autocomplete(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    session: str | None = Query(default=None, max_length=64),
    lang: str | None = Query(default=None, max_length=12),
    search: SearchProvider | None = Depends(get_search),
):
    """Up to 5 place suggestions, ranked near (lat, lng) when given."""
    request.app.state.autocomplete_limiter.check(f"ip:{client_ip(request)}")
    if search is None:
        raise ApiError(503, "SEARCH_UNAVAILABLE", UNAVAILABLE)
    bias = LatLng(lat, lng) if lat is not None and lng is not None else None
    try:
        items = await search.autocomplete(q.strip(), bias, session, lang)
    except GoogleError as e:
        log.warning("autocomplete failed (%s, %s)", search.name, e.kind)
        raise ApiError(502, "SEARCH_FAILED", "Place search failed. Try again, or drop a pin on the map.") from e
    return {"provider": search.name, "suggestions": [s.to_dict() for s in items]}


@router.get("/places/{place_id}")
async def place_details(
    request: Request,
    place_id: str,
    session: str | None = Query(default=None, max_length=64),
    search: SearchProvider | None = Depends(get_search),
):
    """Coordinates for a suggestion that didn't include them (Google)."""
    request.app.state.autocomplete_limiter.check(f"ip:{client_ip(request)}")
    if search is None:
        raise ApiError(503, "SEARCH_UNAVAILABLE", UNAVAILABLE)
    if len(place_id) > 300:
        raise ApiError(422, "VALIDATION_ERROR", "place id is too long")
    try:
        place = await search.resolve(place_id, session)
    except GoogleError as e:
        log.warning("place details failed (%s)", e.kind)
        raise ApiError(502, "SEARCH_FAILED", "Couldn't load that place. Try another, or drop a pin on the map.") from e
    if place is None:
        raise ApiError(404, "PLACE_NOT_FOUND", "That place couldn't be found. Try another, or drop a pin on the map.")
    return {"label": place.label, "lat": place.lat, "lng": place.lng}


@router.get("/locate")
async def locate(request: Request, settings: Settings = Depends(get_settings_dep)):
    """Approximate (city-level) location of the visitor, or {"located": false}.

    Never stored or logged; only used to bias search and centre the first map view.
    """
    loc = from_headers(request.headers) if settings.trust_geo_headers else None
    ip = client_ip(request)
    if loc is None and is_public_ip(ip):
        loc = request.app.state.geoip.lookup(ip)
    if loc is None and settings.dev_locate_public_ip and not is_public_ip(ip):
        loc = await request.app.state.dev_locator.locate()
    if loc is None:
        return {"located": False}
    return {"located": True, **loc.to_dict()}
