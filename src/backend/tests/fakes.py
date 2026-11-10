"""A fake map client: deterministic, offline, and strict about Google's limits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.services import geo
from app.services.geo import LatLng
from app.services.google import GeocodeResult, GoogleError, MatrixElement, Place

SPEEDS = {"DRIVE": 32.0, "TWO_WHEELER": 30.0, "TRANSIT": 24.0, "BICYCLE": 15.0, "WALK": 5.0}
LIMITS = {"TRANSIT": 100}

# Generic, made-up venue names for the offline demo / tests.
DEMO_NAMES = [
    "Corner Café",
    "Harbour Kitchen",
    "The Green Room",
    "Little Lantern",
    "Market Hall",
    "Riverside Bistro",
    "Copper Kettle",
    "Olive & Thyme",
    "Sunday Social",
    "The Reading Room",
    "Blue Door Coffee",
    "Garden Terrace",
    "Night Owl Bar",
    "Plaza Diner",
    "Maple Bakery",
    "Old Mill Pub",
    "Courtyard Grill",
    "Paper Moon Café",
    "Station Eatery",
    "Hilltop Park",
]
DEFAULT_LIMIT = 625


@dataclass
class FakeMapClient:
    # Failure switches
    fail_routes: bool = False
    unsupported_modes: set[str] = field(default_factory=set)
    fail_places: bool = False
    fail_geocode: bool = False
    no_venues: bool = False
    venues_per_search: int = 6
    # Points within this radius (km) of `blocked` are unreachable (e.g. a lake)
    blocked: LatLng | None = None
    blocked_km: float = 0.0
    blocked_everywhere: bool = False
    country: str = "PT"
    area: str = "Centro"
    locality: str = "Test City"
    # Recorded calls
    matrix_calls: list[dict] = field(default_factory=list)
    places_calls: list[dict] = field(default_factory=list)
    geocode_calls: int = 0

    async def route_matrix(self, origins, destinations, mode, *, traffic_aware, departure_time=None, arrival_time=None):
        n = len(origins) * len(destinations)
        limit = LIMITS.get(mode, DEFAULT_LIMIT)
        assert n <= limit, f"route matrix too large: {n} > {limit} for {mode}"
        self.matrix_calls.append(
            {
                "mode": mode,
                "elements": n,
                "traffic_aware": traffic_aware,
                "departure_time": departure_time,
                "arrival_time": arrival_time,
            }
        )
        if self.fail_routes:
            raise GoogleError("unavailable", "boom", 503)
        if mode in self.unsupported_modes:
            raise GoogleError("invalid_argument", f"{mode} not supported", 400)
        speed = SPEEDS[mode] * (0.8 if traffic_aware else 1.0)
        out = []
        for o in origins:
            row = []
            for d in destinations:
                if self.blocked_everywhere or (self.blocked is not None and geo.haversine_km(d, self.blocked) <= self.blocked_km):
                    row.append(None)
                    continue
                km = geo.haversine_km(o, d) * 1.25
                row.append(MatrixElement(seconds=km / speed * 3600 + 60, meters=km * 1000))
            out.append(row)
        return out

    async def search_nearby(self, center, radius_m, types):
        assert radius_m <= 50_000
        self.places_calls.append({"center": center, "radius": radius_m, "types": list(types)})
        if self.fail_places:
            raise GoogleError("unavailable", "places down", 503)
        if self.no_venues:
            return []
        places = []
        for i in range(self.venues_per_search):
            bearing = (i * 61) % 360
            dist_km = (radius_m / 1000) * (0.2 + 0.12 * i)
            p = geo.destination_point(center, bearing, min(dist_km, radius_m / 1000 * 0.95))
            # id based on the rounded location so overlapping zones produce duplicates
            pid = "P" + hashlib.md5(f"{p.lat:.3f},{p.lng:.3f}".encode()).hexdigest()[:10]
            places.append(
                Place(
                    id=pid,
                    name=DEMO_NAMES[int(pid[1:], 16) % len(DEMO_NAMES)],
                    lat=p.lat,
                    lng=p.lng,
                    rating=3.8 + (i % 5) * 0.25,
                    ratings=10 + i * 150,
                    open_now=(i % 3 != 0),
                    maps_url=f"https://maps.google.com/?cid={pid}",
                    type=types[i % len(types)] if types else None,
                    address=None,
                    price_level=None if i % 4 == 3 else (i % 3) + 1,
                    hours=tuple(
                        f"{d}: 9:00 AM – 11:00 PM" for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
                    ),
                )
            )
        return places

    async def geocode(self, address):
        self.geocode_calls += 1
        if self.fail_geocode:
            raise GoogleError("unavailable", "geo down", 503)
        if "nowhere" in address.lower():
            return []
        h = int(hashlib.md5(address.encode()).hexdigest()[:8], 16)
        lat = 38.70 + (h % 1000) / 10000
        lng = -9.20 + (h // 1000 % 1000) / 10000
        return [GeocodeResult(label=f"{address}, Lisboa, Portugal", lat=lat, lng=lng, components=[])]

    async def reverse_geocode(self, point):
        self.geocode_calls += 1
        if self.fail_geocode:
            raise GoogleError("unavailable", "geo down", 503)
        return [
            GeocodeResult(
                label="x",
                lat=point.lat,
                lng=point.lng,
                components=[
                    {"long_name": self.area, "short_name": self.area, "types": ["neighborhood", "political"]},
                    {"long_name": self.locality, "short_name": self.locality, "types": ["locality", "political"]},
                    {"long_name": "Country", "short_name": self.country, "types": ["country", "political"]},
                ],
            )
        ]
