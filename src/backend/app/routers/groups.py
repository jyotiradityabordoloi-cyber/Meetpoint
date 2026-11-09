from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..deps import get_maps, get_settings_dep
from ..errors import ApiError, group_not_found, member_not_found
from ..models import Group, Member
from ..schemas import GroupCreate, GroupOut, GroupPatch, MemberCreate, MemberOut, group_out, member_out
from ..services.google import GoogleError, MapClient

log = logging.getLogger("meetpoint.api")
router = APIRouter(prefix="/api/groups", tags=["groups"])


def load_group(db: Session, group_id: str) -> Group:
    g = db.get(Group, group_id) if len(group_id) <= 12 else None
    if g is None:
        raise group_not_found()
    return g


@router.post("", status_code=201, response_model=GroupOut)
def create_group(body: GroupCreate, db: Session = Depends(get_db)):
    g = Group(name=body.name, travel_mode=body.travel_mode.value, meet_at=body.meet_at, venue_types=body.venue_types)
    db.add(g)
    db.commit()
    db.refresh(g)
    return group_out(g)


@router.get("/{group_id}", response_model=GroupOut)
def get_group(group_id: str, db: Session = Depends(get_db)):
    return group_out(load_group(db, group_id))


@router.patch("/{group_id}", response_model=GroupOut)
def patch_group(group_id: str, body: GroupPatch, db: Session = Depends(get_db)):
    g = load_group(db, group_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        g.name = body.name
    if "travel_mode" in fields and body.travel_mode is not None:
        g.travel_mode = body.travel_mode.value
    if "meet_at" in fields:
        g.meet_at = body.meet_at  # None clears it
    if "venue_types" in fields and body.venue_types is not None:
        g.venue_types = body.venue_types
    g.touch()
    db.commit()
    db.refresh(g)
    return group_out(g)


@router.post("/{group_id}/members", status_code=201, response_model=MemberOut)
async def add_member(
    group_id: str,
    body: MemberCreate,
    db: Session = Depends(get_db),
    maps: MapClient | None = Depends(get_maps),
    settings: Settings = Depends(get_settings_dep),
):
    g = load_group(db, group_id)
    if len(g.members) >= settings.max_members:
        raise ApiError(422, "TOO_MANY_MEMBERS", f"A meetup can have at most {settings.max_members} people.")

    lat, lng, address = body.lat, body.lng, body.address
    if lat is None or lng is None:
        if maps is None:
            raise ApiError(503, "GEOCODER_UNAVAILABLE", "Address search isn't available. Use your location or drop a pin on the map.")
        try:
            matches = await maps.geocode(address or "")
        except GoogleError as e:
            log.warning("geocode failed (%s)", e.kind)
            raise ApiError(502, "GEOCODER_FAILED", "Address search failed. Try again, or drop a pin on the map.") from e
        if not matches:
            raise ApiError(
                422,
                "ADDRESS_NOT_FOUND",
                f'We couldn\'t find "{address}". Add the area and city, or drop a pin.',
            )
        lat, lng, address = matches[0].lat, matches[0].lng, matches[0].label or address

    m = Member(
        group_id=g.id,
        name=body.name,
        lat=lat,
        lng=lng,
        address=address,
        travel_mode=body.travel_mode.value if body.travel_mode else None,
    )
    db.add(m)
    g.touch()
    db.commit()
    db.refresh(m)
    return member_out(m)


@router.delete("/{group_id}/members/{member_id}", status_code=204)
def delete_member(group_id: str, member_id: str, db: Session = Depends(get_db)):
    g = load_group(db, group_id)
    m = db.get(Member, member_id)
    if m is None or m.group_id != g.id:
        raise member_not_found()
    db.delete(m)
    g.touch()
    db.commit()
    return Response(status_code=204)
