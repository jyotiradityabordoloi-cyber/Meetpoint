"""The meeting-point engine.

Turns a list of people into ranked venues. Receives plain data and a map
client (or None), returns a plain dict. Knows nothing about HTTP or the DB.

Pipeline: validate -> centre -> zones -> region + zone matrix -> score zones
          -> venues -> venue matrix -> final rank.
Every external call has a fallback; see ARCHITECTURE section 9.2.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from ..config import Settings
from . import geo
from .geo import LatLng
from .google import GoogleError, MapClient, Matrix, MatrixElement, Place, chunk_matrix
from .region import empty_region, region_from_results

log = logging.getLogger("meetpoint.engine")

ROAD_DOWNGRADE = {"WALK", "BICYCLE", "TRANSIT"}  # switched to DRIVE when people are in different cities


# --------------------------------------------------------------------------
# Inputs / outputs
# --------------------------------------------------------------------------
@dataclass
class EngineMember:
    id: str
    name: str
    lat: float
    lng: float
    mode: str

    @property
    def point(self) -> LatLng:
        return LatLng(self.lat, self.lng)


@dataclass
class EngineInput:
    members: list[EngineMember]
    venue_types: list[str]
    meet_at: str | None = None


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Scoring (pure functions, unit-tested directly)
# --------------------------------------------------------------------------
def fairness_score(minutes: Sequence[float], w_max: float = 0.5, w_std: float = 0.3, w_mean: float = 0.2) -> float:
    """S = w_max*max + w_std*std + w_mean*mean (population std). Lower is better.

    20/20/20 -> 14.0, 5/10/45 -> 31.8.
    """
    if not minutes:
        return float("inf")
    return w_max * max(minutes) + w_std * statistics.pstdev(minutes) + w_mean * statistics.fmean(minutes)


def fairness_index(minutes: Sequence[float]) -> float:
    """1 - std/mean, clipped to [0, 1]. 1.0 means everyone travels equally."""
    if not minutes:
        return 0.0
    m = statistics.fmean(minutes)
    if m <= 0:
        return 1.0
    return max(0.0, min(1.0, 1 - statistics.pstdev(minutes) / m))


def bayesian_rating(rating: float | None, n: int | None, prior: float = 4.0, k: int = 50) -> float:
    n = int(n or 0)
    if rating is None or n <= 0:
        return prior
    return (n * rating + k * prior) / (n + k)


def venue_quality(
    rating: float | None,
    n: int | None,
    open_now: bool | None,
    closeness: float,
    prior: float = 4.0,
    k: int = 50,
) -> float:
    """Q = 0.6*(bayes/5) + 0.25*open_now + 0.15*closeness. Unknown open-now counts as 0.5."""
    open_term = 0.5 if open_now is None else (1.0 if open_now else 0.0)
    closeness = max(0.0, min(1.0, closeness))
    return 0.6 * (bayesian_rating(rating, n, prior, k) / 5.0) + 0.25 * open_term + 0.15 * closeness


def normalise(values: Sequence[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


# --------------------------------------------------------------------------
# Call accounting (bounded-cost guarantee, also reported in the payload)
# --------------------------------------------------------------------------
class _Counting:
    """Wraps the map client: chunks route matrices to Google's element limits
    and counts every call so the payload can report the cost of a search."""

    def __init__(self, inner: MapClient, settings: Settings):
        self.inner = inner
        self.settings = settings
        self.matrix_calls = 0
        self.matrix_elements = 0
        self.places_calls = 0
        self.geocode_calls = 0

    async def route_matrix(self, origins, destinations, mode, **kw) -> Matrix:
        out: Matrix = [[None] * len(destinations) for _ in origins]
        blocks = chunk_matrix(len(origins), len(destinations), self.settings.matrix_limit(mode))
        for o_rng, d_rng in blocks:
            self.matrix_calls += 1
            self.matrix_elements += len(o_rng) * len(d_rng)
            part = await self.inner.route_matrix([origins[i] for i in o_rng], [destinations[j] for j in d_rng], mode, **kw)
            for oi, row in zip(o_rng, part):
                for dj, el in zip(d_rng, row):
                    out[oi][dj] = el
        return out

    async def search_nearby(self, center, radius_m, types) -> list[Place]:
        self.places_calls += 1
        return await self.inner.search_nearby(center, radius_m, types)

    async def reverse_geocode(self, point):
        self.geocode_calls += 1
        return await self.inner.reverse_geocode(point)

    def usage(self) -> dict:
        return {
            "route_matrix_requests": self.matrix_calls,
            "route_matrix_elements": self.matrix_elements,
            "places_calls": self.places_calls,
            "geocode_calls": self.geocode_calls,
        }


@dataclass
class _Ctx:
    settings: Settings
    maps: _Counting | None
    members: list[EngineMember]
    mode_of: dict[str, str]
    meet_at: datetime | None = None
    estimated: bool = False
    warnings: list[dict] = field(default_factory=list)
    _warned: set[str] = field(default_factory=set)

    def warn(self, code: str, message: str) -> None:
        if code not in self._warned:
            self._warned.add(code)
            self.warnings.append({"code": code, "message": message})


# --------------------------------------------------------------------------
# Travel-time matrices with fallbacks
# --------------------------------------------------------------------------
def _estimate(ctx: _Ctx, mode: str, origins: Sequence[LatLng], dests: Sequence[LatLng]) -> Matrix:
    speeds = ctx.settings.fallback_speeds
    speed = speeds.get(mode) or speeds.get("DRIVE") or 30.0
    detour = ctx.settings.fallback_detour_factor
    out: Matrix = []
    for o in origins:
        row = []
        for d in dests:
            km = geo.haversine_km(o, d) * detour
            row.append(MatrixElement(seconds=km / speed * 3600.0, meters=km * 1000.0))
        out.append(row)
    return out


async def _matrix_for_mode(
    ctx: _Ctx, mode: str, members: list[EngineMember], dests: Sequence[LatLng], traffic_aware: bool
) -> tuple[str, Matrix]:
    origins = [m.point for m in members]
    if ctx.maps is None:
        ctx.estimated = True
        return mode, _estimate(ctx, mode, origins, dests)

    kw: dict = {"traffic_aware": traffic_aware}
    if ctx.meet_at is not None:
        if mode == "TRANSIT":
            kw["arrival_time"] = ctx.meet_at
        elif mode in ("DRIVE", "TWO_WHEELER") and traffic_aware:
            kw["departure_time"] = ctx.meet_at
    try:
        return mode, await ctx.maps.route_matrix(origins, dests, mode, **kw)
    except GoogleError as e:
        if mode == "TWO_WHEELER":
            log.info("two-wheeler routing unavailable (%s); retrying as DRIVE", e.kind)
            for m in members:
                ctx.mode_of[m.id] = "DRIVE"
            ctx.warn(
                "TWO_WHEELER_FALLBACK",
                "Two-wheeler routing isn't offered here, so driving times are used for those people.",
            )
            return await _matrix_for_mode(ctx, "DRIVE", members, dests, traffic_aware)
        log.warning("route matrix failed for %s (%s); using estimates", mode, e.kind)
        ctx.estimated = True
        ctx.warn("ESTIMATED_TIMES", "Live travel times weren't available, so times are estimated from distance.")
        return mode, _estimate(ctx, mode, origins, dests)


async def _travel_matrix(ctx: _Ctx, dests: Sequence[LatLng], traffic_aware: bool) -> dict[str, list[MatrixElement | None]]:
    """One request per travel mode in use. Returns member_id -> row over dests."""
    by_mode: dict[str, list[EngineMember]] = {}
    for m in ctx.members:
        by_mode.setdefault(ctx.mode_of[m.id], []).append(m)
    modes = list(by_mode)
    results = await asyncio.gather(*[_matrix_for_mode(ctx, mode, by_mode[mode], dests, traffic_aware) for mode in modes])
    rows: dict[str, list[MatrixElement | None]] = {}
    for mode, (_, matrix) in zip(modes, results):
        for member, row in zip(by_mode[mode], matrix):
            rows[member.id] = row
    return rows


def _column_minutes(rows: dict[str, list[MatrixElement | None]], ids: list[str], j: int) -> list[float] | None:
    mins = []
    for mid in ids:
        el = rows[mid][j]
        if el is None:
            return None
        mins.append(el.seconds / 60.0)
    return mins


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
async def find_meeting_point(
    inp: EngineInput,
    maps: MapClient | None,
    settings: Settings,
    now: datetime | None = None,
) -> dict:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    now = now or datetime.now(timezone.utc)
    s = settings
    counting = _Counting(maps, s) if maps is not None else None
    members = list(inp.members)
    ctx = _Ctx(settings=s, maps=counting, members=members, mode_of={m.id: m.mode for m in members})

    # 1. Validate --------------------------------------------------------
    n = len(members)
    if n < s.min_members:
        raise EngineError("NOT_ENOUGH_MEMBERS", f"Add at least {s.min_members} people to find a meeting point.")
    if n > s.max_members:
        raise EngineError("TOO_MANY_MEMBERS", f"A meetup can have at most {s.max_members} people.")
    points = [m.point for m in members]
    spread = geo.group_spread_km(points)
    if spread < s.same_place_km:
        ctx.warn("SAME_PLACE", "Everyone is starting from the same place, so venues are suggested right around it.")
    if spread > s.max_group_spread_km:
        switched = [m for m in members if ctx.mode_of[m.id] in ROAD_DOWNGRADE]
        for m in switched:
            ctx.mode_of[m.id] = "DRIVE"
        ctx.warn(
            "DIFFERENT_CITIES",
            f"People are more than {s.max_group_spread_km:g} km apart, so this may suggest a town in between"
            + ("; walking, cycling and public transport were switched to driving." if switched else "."),
        )

    if inp.meet_at:
        try:
            dt = datetime.fromisoformat(inp.meet_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError
            if dt <= now + timedelta(minutes=1):
                ctx.warn("PAST_MEETING_TIME", "The meeting time is in the past, so current travel times are used.")
            else:
                ctx.meet_at = dt
        except ValueError:
            ctx.warn("BAD_MEETING_TIME", "The meeting time couldn't be read, so current travel times are used.")

    # 2. Centre ----------------------------------------------------------
    centre = geo.geometric_median(points)
    centroid = geo.spherical_centroid(points)

    # 3. Candidate zones -------------------------------------------------
    r = geo.ring_radius_km(spread, s.candidate_radius_ratio, s.candidate_radius_min_km, s.candidate_radius_max_km)
    candidates = geo.dedupe_points(
        [centre, centroid] + geo.candidate_ring(centre, r / 2, 6) + geo.candidate_ring(centre, r, 12, offset_deg=15.0)
    )
    counts: dict[str, int] = {}
    for m in members:
        counts[ctx.mode_of[m.id]] = counts.get(ctx.mode_of[m.id], 0) + 1
    cap = min(s.matrix_limit(mode) // c for mode, c in counts.items())
    zones = candidates[: max(1, cap)]
    timings["geometry_ms"] = (time.perf_counter() - t0) * 1000

    # 4 + 5. Region and zone matrix in parallel --------------------------
    t1 = time.perf_counter()

    async def _region() -> dict:
        if counting is None:
            return empty_region()
        try:
            return region_from_results(await counting.reverse_geocode(centre))
        except GoogleError as e:
            log.warning("reverse geocode failed (%s)", e.kind)
            return empty_region()

    region, zone_rows = await asyncio.gather(_region(), _travel_matrix(ctx, zones, traffic_aware=False))
    timings["zone_matrix_ms"] = (time.perf_counter() - t1) * 1000

    # 6. Score zones -----------------------------------------------------
    ids = [m.id for m in members]
    scored = []
    for j, z in enumerate(zones):
        mins = _column_minutes(zone_rows, ids, j)
        if mins is None:
            continue  # someone can't reach this zone
        scored.append(
            {
                "point": z,
                "score": fairness_score(mins, s.w_max, s.w_std, s.w_mean),
                "fairness": fairness_index(mins),
                "minutes": dict(zip(ids, mins)),
            }
        )
    if not scored:
        raise EngineError(
            "NO_REACHABLE_AREA",
            "We couldn't find an area everyone can reach. Try driving, or check the addresses.",
        )
    scored.sort(key=lambda z: z["score"])
    min_sep = s.zone_min_separation_ratio * r
    top: list[dict] = []
    for z in scored:
        if all(geo.haversine_km(z["point"], t["point"]) >= min_sep for t in top):
            top.append(z)
        if len(top) >= s.top_zones:
            break

    zones_out = [
        {
            "lat": round(z["point"].lat, 6),
            "lng": round(z["point"].lng, 6),
            "score": round(z["score"], 1),
            "fairness": round(z["fairness"], 2),
            "minutes": {k: round(v, 1) for k, v in z["minutes"].items()},
        }
        for z in top
    ]

    # 7. Venues near the fairest zones -----------------------------------
    t2 = time.perf_counter()
    venues_out: list[dict] = []
    pool: list[dict] = []
    if counting is None:
        ctx.warn("NO_VENUES", "Venue search isn't set up on this server, so the fairest areas are shown instead.")
    elif not inp.venue_types:
        ctx.warn("NO_VENUES", "Pick at least one kind of place to get venue suggestions.")
    else:
        pool = await _search_venues(ctx, counting, top, inp.venue_types)
        if not pool:
            ctx.warn("NO_VENUES", "No venues were found near the fairest areas, so those areas are shown instead.")
    timings["places_ms"] = (time.perf_counter() - t2) * 1000

    # 8. Venue matrix (traffic-aware) and 9. final rank ------------------
    t3 = time.perf_counter()
    if pool:
        zone_norm = normalise([v["zone_score"] for v in pool])
        for v, zn in zip(pool, zone_norm):
            v["prelim"] = s.final_weight_fairness * zn + (1 - s.final_weight_fairness) * (1 - v["quality"])
        pool.sort(key=lambda v: v["prelim"])
        pool = pool[: s.venue_pool_size]

        vrows = await _travel_matrix(ctx, [LatLng(v["place"].lat, v["place"].lng) for v in pool], traffic_aware=True)
        timed = []
        for j, v in enumerate(pool):
            mins = _column_minutes(vrows, ids, j)
            if mins is None:
                continue
            v["minutes"] = mins
            v["meters"] = [vrows[mid][j].meters for mid in ids]  # type: ignore[union-attr]
            v["S"] = fairness_score(mins, s.w_max, s.w_std, s.w_mean)
            timed.append(v)
        if not timed:
            ctx.warn("NO_VENUES", "None of the venues found could be reached by everyone, so the fairest areas are shown.")
        else:
            s_norm = normalise([v["S"] for v in timed])
            for v, sn in zip(timed, s_norm):
                v["final"] = s.final_weight_fairness * sn + (1 - s.final_weight_fairness) * (1 - v["quality"])
            timed.sort(key=lambda v: (v["final"], v["S"]))
            for v in timed[: s.venues_to_return]:
                p: Place = v["place"]
                venues_out.append(
                    {
                        "id": p.id,
                        "name": p.name,
                        "type": p.type,
                        "address": p.address,
                        "rating": p.rating,
                        "ratings": p.ratings,
                        "open_now": p.open_now,
                        "maps_url": p.maps_url,
                        "photos": list(p.photos),
                        "lat": round(p.lat, 6),
                        "lng": round(p.lng, 6),
                        "zone": v["zone_index"],
                        "score": round(v["final"], 3),
                        "fairness_score": round(v["S"], 1),
                        "fairness": round(fairness_index(v["minutes"]), 2),
                        "max_minutes": round(max(v["minutes"]), 1),
                        "mean_minutes": round(statistics.fmean(v["minutes"]), 1),
                        "travel": [
                            {
                                "member_id": m.id,
                                "name": m.name,
                                "mode": ctx.mode_of[m.id],
                                "minutes": round(mins, 1),
                                "meters": round(meters),
                            }
                            for m, mins, meters in zip(members, v["minutes"], v["meters"])
                        ],
                    }
                )
    timings["venue_matrix_ms"] = (time.perf_counter() - t3) * 1000
    timings["total_ms"] = (time.perf_counter() - t0) * 1000

    if ctx.estimated:
        ctx.warn("ESTIMATED_TIMES", "Live travel times weren't available, so times are estimated from distance.")

    return {
        "center": {"lat": round(centre.lat, 6), "lng": round(centre.lng, 6)},
        "radius_km": round(r, 2),
        "spread_km": round(spread, 2),
        "region": region,
        "zones": zones_out,
        "venues": venues_out,
        "members": [{"id": m.id, "name": m.name, "lat": m.lat, "lng": m.lng, "mode": ctx.mode_of[m.id]} for m in members],
        "meet_at": ctx.meet_at.isoformat() if ctx.meet_at else None,
        "warnings": ctx.warnings,
        "estimated": ctx.estimated,
        "usage": counting.usage() if counting else None,
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
    }


async def _search_venues(ctx: _Ctx, maps: _Counting, top: list[dict], types: list[str]) -> list[dict]:
    """Search each top zone (in parallel), widening the radius until enough venues appear."""
    steps = ctx.settings.venue_radius_steps_m
    need = ctx.settings.min_venues_per_zone
    failed = False

    async def one_zone(zi: int, zone: dict) -> list[tuple[Place, int, float]]:
        nonlocal failed
        found: dict[str, Place] = {}
        radius = steps[-1]
        for radius in steps:
            try:
                places = await maps.search_nearby(zone["point"], radius, types)
            except GoogleError as e:
                log.warning("places search failed (%s)", e.kind)
                failed = True
                break
            for p in places:
                found.setdefault(p.id, p)
            if len(found) >= need:
                break
        return [(p, zi, radius) for p in found.values()]

    results = await asyncio.gather(*[one_zone(i, z) for i, z in enumerate(top)])
    if failed:
        ctx.warn("PLACES_UNAVAILABLE", "Venue search had a problem, so some or all venues may be missing.")

    best: dict[str, dict] = {}
    for zone_results in results:
        for place, zi, radius in zone_results:
            zone = top[zi]
            dist_m = geo.haversine_km(zone["point"], LatLng(place.lat, place.lng)) * 1000
            closeness = 1 - min(dist_m / max(radius, 1), 1.0)
            q = venue_quality(
                place.rating,
                place.ratings,
                place.open_now,
                closeness,
                ctx.settings.bayes_prior_rating,
                ctx.settings.bayes_prior_weight,
            )
            entry = {"place": place, "zone_index": zi, "zone_score": zone["score"], "quality": q}
            prev = best.get(place.id)
            if prev is None or (entry["zone_score"], -q) < (prev["zone_score"], -prev["quality"]):
                best[place.id] = entry
    return list(best.values())
