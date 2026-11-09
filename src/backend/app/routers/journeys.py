"""Journey paths to a venue, and venue photos (proxied so the key stays on the server)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import client_ip, get_maps, get_router
from ..errors import ApiError
from ..services.geo import LatLng
from ..services.google import GoogleError, MapClient
from ..services.routes import JourneyRouter, route_all
from .groups import load_group

log = logging.getLogger("meetpoint.api")
router = APIRouter(prefix="/api", tags=["journeys"])

PHOTO_NAME = re.compile(r"^places/[A-Za-z0-9_-]{1,300}/photos/[A-Za-z0-9_-]{1,800}$")


class JourneyTarget(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def _meeting_times(meet_at: str | None) -> tuple[datetime | None, datetime | None]:
    if not meet_at:
        return None, None
    try:
        dt = datetime.fromisoformat(meet_at.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if dt.tzinfo is None or dt <= datetime.now(timezone.utc) + timedelta(minutes=1):
        return None, None
    return dt, dt


@router.post("/groups/{group_id}/journeys")
async def journeys(
    group_id: str,
    body: JourneyTarget,
    request: Request,
    db: Session = Depends(get_db),
    route_provider: JourneyRouter | None = Depends(get_router),
):
    """The path each member travels to (lat, lng). `path` is null when no route
    is available (the client then draws a smooth curve)."""
    g = load_group(db, group_id)
    request.app.state.journeys_limiter.check(f"ip:{client_ip(request)}", f"group:{g.id}")
    dest = LatLng(body.lat, body.lng)
    trips = [(m.id, LatLng(m.lat, m.lng), m.travel_mode or g.travel_mode) for m in g.members]
    routes = {}
    if route_provider is not None and trips:
        departure, arrival = _meeting_times(g.meet_at)
        routes = await route_all(route_provider, request.app.state.route_cache, trips, dest, departure, arrival)
    out = []
    for mid, origin, mode in trips:
        rt = routes.get(mid)
        out.append(
            {
                "member_id": mid,
                "mode": mode,
                "from": {"lat": origin.lat, "lng": origin.lng},
                "path": [[round(a, 5), round(b, 5)] for a, b in rt.points] if rt else None,
                "minutes": round(rt.seconds / 60, 1) if rt else None,
                "meters": round(rt.meters) if rt else None,
            }
        )
    return {"provider": route_provider.name if route_provider else None, "to": {"lat": dest.lat, "lng": dest.lng}, "journeys": out}


@router.get("/photos")
async def photo(
    request: Request,
    name: str = Query(max_length=1200),
    w: int = Query(480, ge=64, le=1600),
    maps: MapClient | None = Depends(get_maps),
):
    """Redirect to a Google Places photo. The browser never sees the API key."""
    if not PHOTO_NAME.match(name):
        raise ApiError(422, "VALIDATION_ERROR", "Invalid photo name")
    request.app.state.photo_limiter.check(f"ip:{client_ip(request)}")
    if maps is None or not hasattr(maps, "photo_uri"):
        raise ApiError(404, "PHOTO_NOT_FOUND", "Photo not available")
    try:
        uri = await maps.photo_uri(name, w)
    except GoogleError as e:
        log.info("photo lookup failed (%s)", e.kind)
        raise ApiError(502, "PHOTO_FAILED", "Photo not available right now") from e
    if not uri:
        raise ApiError(404, "PHOTO_NOT_FOUND", "Photo not available")
    # photoUri is short-lived; let the browser reuse it for a while.
    return RedirectResponse(uri, status_code=302, headers={"Cache-Control": "public, max-age=3600"})
