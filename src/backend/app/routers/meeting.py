from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..deps import client_ip, get_maps, get_settings_dep
from ..errors import ApiError
from ..models import Recommendation, new_id
from ..schemas import GeocodeMatch, iso
from ..services.engine import EngineError, EngineInput, EngineMember, find_meeting_point
from ..services.google import GoogleError, MapClient
from .groups import load_group

log = logging.getLogger("meetpoint.api")
router = APIRouter(prefix="/api", tags=["meeting"])


def _rec_out(r: Recommendation) -> dict:
    return {**r.payload, "id": r.id, "group_id": r.group_id, "created_at": iso(r.created_at)}


@router.post("/groups/{group_id}/meeting-point")
async def meeting_point(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    maps: MapClient | None = Depends(get_maps),
    settings: Settings = Depends(get_settings_dep),
):
    g = load_group(db, group_id)
    request.app.state.limiter.check(f"ip:{client_ip(request)}", f"group:{g.id}")
    inp = EngineInput(
        members=[EngineMember(id=m.id, name=m.name, lat=m.lat, lng=m.lng, mode=m.travel_mode or g.travel_mode) for m in g.members],
        venue_types=list(g.venue_types or []),
        meet_at=g.meet_at,
    )
    try:
        payload = await find_meeting_point(inp, maps, settings)
    except EngineError as e:
        raise ApiError(422, e.code, e.message) from e

    log.info(
        "search group=%s members=%d venues=%d estimated=%s warnings=%s usage=%s total_ms=%s",
        g.id,
        len(inp.members),
        len(payload["venues"]),
        payload["estimated"],
        [w["code"] for w in payload["warnings"]],
        payload["usage"],
        payload["timings_ms"].get("total_ms"),
    )
    rec = Recommendation(id=new_id(), group_id=g.id, payload=payload)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _rec_out(rec)


@router.get("/groups/{group_id}/recommendations")
def recommendations(group_id: str, limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
    g = load_group(db, group_id)
    rows = db.scalars(
        select(Recommendation).where(Recommendation.group_id == g.id).order_by(Recommendation.created_at.desc()).limit(limit)
    ).all()
    return [_rec_out(r) for r in rows]


@router.get("/geocode", response_model=list[GeocodeMatch])
async def geocode(
    request: Request,
    q: str = Query(min_length=2, max_length=300),
    maps: MapClient | None = Depends(get_maps),
):
    request.app.state.geocode_limiter.check(f"ip:{client_ip(request)}")
    if maps is None:
        raise ApiError(503, "GEOCODER_UNAVAILABLE", "Address search isn't available. Use your location or drop a pin on the map.")
    try:
        matches = await maps.geocode(q)
    except GoogleError as e:
        log.warning("geocode failed (%s)", e.kind)
        raise ApiError(502, "GEOCODER_FAILED", "Address search failed. Try again, or drop a pin on the map.") from e
    return [GeocodeMatch(label=m.label, lat=m.lat, lng=m.lng) for m in matches[:5]]
