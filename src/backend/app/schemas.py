from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Places (New) type ids the UI may ask for. Kept as an allow-list so a typo
# can't turn into a failed Places request.
ALLOWED_VENUE_TYPES = {
    "cafe",
    "coffee_shop",
    "restaurant",
    "bar",
    "pub",
    "park",
    "shopping_mall",
    "bakery",
    "library",
    "museum",
    "movie_theater",
}
DEFAULT_VENUE_TYPES = ["cafe", "restaurant"]


class TravelMode(str, Enum):
    DRIVE = "DRIVE"
    TWO_WHEELER = "TWO_WHEELER"
    TRANSIT = "TRANSIT"
    WALK = "WALK"
    BICYCLE = "BICYCLE"


def _validate_meet_at(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError("meet_at must be ISO-8601, e.g. 2026-11-20T19:00:00+05:30") from e
    if dt.tzinfo is None:
        raise ValueError("meet_at must include a UTC offset, e.g. +05:30")
    return dt.isoformat()


def _validate_types(v: list[str]) -> list[str]:
    out: list[str] = []
    for t in v:
        t = t.strip().lower()
        if t not in ALLOWED_VENUE_TYPES:
            raise ValueError(f"unknown venue type '{t}'")
        if t not in out:
            out.append(t)
    return out


class GroupCreate(BaseModel):
    name: str = Field(default="Our meetup", min_length=1, max_length=120)
    travel_mode: TravelMode = TravelMode.DRIVE
    meet_at: str | None = None
    venue_types: list[str] = Field(default_factory=lambda: list(DEFAULT_VENUE_TYPES), max_length=10)

    _meet = field_validator("meet_at")(_validate_meet_at)
    _types = field_validator("venue_types")(_validate_types)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    travel_mode: TravelMode | None = None
    meet_at: str | None = None
    venue_types: list[str] | None = Field(default=None, max_length=10)

    _meet = field_validator("meet_at")(_validate_meet_at)

    @field_validator("venue_types")
    @classmethod
    def _types(cls, v):
        return None if v is None else _validate_types(v)


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    address: str | None = Field(default=None, max_length=300)
    travel_mode: TravelMode | None = None

    @field_validator("name", "address")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _coords_or_address(self):
        has_coords = self.lat is not None and self.lng is not None
        if (self.lat is None) != (self.lng is None):
            raise ValueError("give both lat and lng, or neither")
        if not has_coords and not self.address:
            raise ValueError("give an address or lat and lng")
        return self


class MemberOut(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    address: str | None
    travel_mode: TravelMode | None


class GroupOut(BaseModel):
    id: str
    name: str
    travel_mode: TravelMode
    meet_at: str | None
    venue_types: list[str]
    created_at: str
    updated_at: str
    members: list[MemberOut]


class GeocodeMatch(BaseModel):
    label: str
    lat: float
    lng: float


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:  # SQLite returns naive datetimes; we always store UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def member_out(m: Any) -> MemberOut:
    return MemberOut(id=m.id, name=m.name, lat=m.lat, lng=m.lng, address=m.address, travel_mode=m.travel_mode)


def group_out(g: Any) -> GroupOut:
    return GroupOut(
        id=g.id,
        name=g.name,
        travel_mode=g.travel_mode,
        meet_at=g.meet_at,
        venue_types=list(g.venue_types or []),
        created_at=iso(g.created_at),
        updated_at=iso(g.updated_at),
        members=[member_out(m) for m in g.members],
    )
