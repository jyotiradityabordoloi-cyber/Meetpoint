# MeetPoint — Implementation Plan (Phase-wise)

**Version:** 1.0 · **Date:** November 2026
**Source documents:** [prd.md](prd.md), MeetPoint System Architecture v1.0

This plan breaks MeetPoint into ordered phases. Each phase has a goal, the exact files to create, the tasks, the tests and a **definition of done** (exit criteria). Phases 0–7 deliver v1 (MVP); Phase 8 is production hardening; Phase 9 is launch and measurement; Phase 10 is the post-launch evolution path.

---

## Phase overview

| # | Phase | Main output | Depends on | Est. effort* |
|---|-------|-------------|------------|--------------|
| 0 | Project foundations | Repo, tooling, CI, config, `/health` | — | 2–3 days |
| 1 | Geometry core | `services/geo.py` + tests | 0 | 2–3 days |
| 2 | Data layer & Groups API | Models, schemas, groups/members CRUD | 0 | 3–4 days |
| 3 | Google client, region & geocode proxy | `services/google.py`, `services/region.py`, `/api/geocode` | 0, 2 | 3–4 days |
| 4 | Meeting engine | `services/engine.py`, `/meeting-point` endpoint | 1, 3 | 6–8 days |
| 5 | Frontend: setup, people, invite | `App.jsx`, `GroupSettings`, `People`, `AddPerson`, `api.js` | 2, 3 | 4–5 days |
| 6 | Frontend: results & map | `Results.jsx`, `MapView.jsx`, `format.js` | 4, 5 | 4–5 days |
| 7 | P1 features & polish | Meeting time, history, error UX, mobile | 4, 6 | 3–4 days |
| 8 | Production hardening | Docker, Alembic, purge job, rate limit, secrets, observability | 0–7 | 5–7 days |
| 9 | Benchmark, beta & launch | 20-city benchmark, metric dashboards, launch | 8 | 5 days + beta |
| 10 | Post-launch evolution (v1.x / v2) | Caching, parallelism, realtime, accessibility | 9 | Per item |

\*Indicative, for 1–2 engineers. Phases 1, 2 and 3 can run in parallel after Phase 0; Phase 5 can start against a stubbed engine once Phase 2 is done.

```
Phase 0 ─┬─► Phase 1 ─────────────┐
         ├─► Phase 2 ─┬─► Phase 3 ┴─► Phase 4 ─┐
         │            └─► Phase 5 ─────────────┴─► Phase 6 ─► Phase 7 ─► Phase 8 ─► Phase 9 ─► Phase 10
```

---

## Target repository layout

```
src/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, routers, /health, table creation
│   │   ├── config.py            # pydantic-settings; all tunables from env
│   │   ├── db.py                # engine + session factory
│   │   ├── models.py            # SQLAlchemy tables
│   │   ├── schemas.py           # Pydantic request/response models
│   │   ├── deps.py              # DB session + per-request map client (None if no key)
│   │   ├── errors.py            # ApiError + error codes → {"detail": {code, message}}
│   │   ├── routers/
│   │   │   ├── groups.py        # groups & members
│   │   │   └── meeting.py       # meeting-point, geocode proxy
│   │   └── services/
│   │       ├── geo.py           # pure spherical maths
│   │       ├── region.py        # reverse-geocode → area/city/country/units
│   │       ├── google.py        # async Routes / Places (New) / Geocoding client
│   │       └── engine.py        # meeting-point pipeline
│   ├── tests/
│   │   ├── fakes.py             # FakeMapClient (asserts element limits)
│   │   ├── test_geo.py
│   │   ├── test_engine.py
│   │   └── test_api.py
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── api.js
│   │   ├── format.js
│   │   └── components/
│   │       ├── GroupSettings.jsx
│   │       ├── People.jsx
│   │       ├── AddPerson.jsx
│   │       ├── Results.jsx
│   │       └── MapView.jsx
│   ├── vite.config.js
│   └── package.json
└── docker-compose.yml
```

---

## Phase 0 — Project foundations

**Goal:** a runnable skeleton for backend and frontend, with config, CI and the error contract fixed before any feature work.

### Tasks

1. **Backend scaffold** — Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2, httpx, pytest, pytest-asyncio, ruff.
2. **`config.py`** — one `Settings` class; every tunable from the architecture's configuration reference, with city-agnostic defaults:

   | Variable | Default |
   |----------|---------|
   | `GOOGLE_MAPS_API_KEY` | empty (→ estimates only) |
   | `DATABASE_URL` | `sqlite:///./meetpoint.db` |
   | `CORS_ORIGINS` | `http://localhost:5173` |
   | `MIN_MEMBERS` / `MAX_MEMBERS` | 2 / 25 |
   | `MAX_GROUP_SPREAD_KM` | 100 |
   | `CANDIDATE_RADIUS_RATIO` / `_MIN_KM` / `_MAX_KM` | 0.25 / 0.5 / 10 |
   | `TOP_ZONES` | 3 |
   | `W_MAX` / `W_STD` / `W_MEAN` | 0.5 / 0.3 / 0.2 |
   | `VENUE_RADIUS_STEPS` | 800, 1500, 3000 (m) |
   | `VENUE_POOL_SIZE` / `VENUES_TO_RETURN` | 10 / 5 |
   | `FALLBACK_SPEED_KMH` | per mode (e.g. DRIVE 30, TWO_WHEELER 28, TRANSIT 22, BICYCLE 15, WALK 4.8) |
   | `FALLBACK_DETOUR_FACTOR` | 1.3 |
   | `GROUP_RETENTION_DAYS` | 30 |

3. **Error contract (`errors.py`)** — a single `ApiError(status, code, message)` and exception handler producing:
   ```json
   { "detail": { "code": "ADDRESS_NOT_FOUND", "message": "We couldn't find \"Main St\". Add the area and city, or drop a pin." } }
   ```
   Codes to register: `GROUP_NOT_FOUND`, `MEMBER_NOT_FOUND`, `NOT_ENOUGH_MEMBERS`, `TOO_MANY_MEMBERS`, `NO_REACHABLE_AREA`, `ADDRESS_NOT_FOUND`, `GEOCODER_FAILED` (502), `GEOCODER_UNAVAILABLE` (503).
4. **`main.py`** — app factory, CORS from `CORS_ORIGINS`, `GET /health` → `{ ok: true, maps_configured: bool }`.
5. **Frontend scaffold** — Vite 5 + React 18, Leaflet 1.9, react-leaflet 4; Vite dev proxy `/api → VITE_API_URL` (default `http://127.0.0.1:8000`).
6. **CI** — lint (ruff, eslint) + backend tests + frontend build on every PR.
7. **`.env.example`** with every variable; `.env` git-ignored.

### Definition of done

- `uvicorn app.main:app --reload` serves `/health`; `npm run dev` serves a blank page with a world map.
- CI green on an empty test suite.
- **No city, coordinate or bounding box anywhere in config or code.**

---

## Phase 1 — Geometry core (`services/geo.py`)

**Goal:** pure, dependency-free spherical maths that everything else builds on. No I/O.

### Functions

| Function | Purpose |
|----------|---------|
| `haversine_km(a, b)` | Great-circle distance |
| `to_xyz(p)` / `from_xyz(v)` | Lat/lng ↔ unit vector (avoids antimeridian bugs) |
| `spherical_centroid(points)` | Normalised mean of unit vectors |
| `geometric_median(points, eps=1e-7, max_iter=200)` | Weiszfeld on the unit sphere, seeded from the centroid |
| `destination_point(origin, bearing_deg, dist_km)` | Point at a bearing and distance |
| `group_spread_km(points)` | Max pairwise distance (n ≤ 25, O(n²) is fine) |
| `candidate_ring(centre, radius_km, count)` | Evenly spaced points on a ring |
| `ring_radius_km(spread)` | `clamp(0.25 × spread, 0.5, 10)` from config |

### Weiszfeld on the sphere (reference)

```python
def geometric_median(points, eps=1e-7, max_iter=200):
    vs = [to_xyz(p) for p in points]
    x = normalize(sum_vectors(vs))                # spherical centroid as seed
    for _ in range(max_iter):
        num, den = [0.0, 0.0, 0.0], 0.0
        for v in vs:
            d = angle_between(x, v)               # geodesic distance on unit sphere
            if d < 1e-12:                         # coincides with an input point
                return from_xyz(v)
            w = 1.0 / d
            num = add(num, scale(v, w)); den += w
        x_new = normalize(scale(num, 1.0 / den))
        if angle_between(x, x_new) < eps:
            return from_xyz(x_new)
        x = x_new
    return from_xyz(x)
```

### Tests (`tests/test_geo.py`)

- Known distances (e.g. two well-known city pairs within 0.5%).
- Median vs centroid with one outlier: median stays near the cluster; centroid moves.
- Antimeridian: points at lng 179.9 and −179.9 → centre near ±180, not 0.
- Near-pole points don't produce NaN.
- Ring shape: every ring point is `radius ± 1%` from the centre; bearings evenly spaced.
- `ring_radius_km` clamps at both ends.

### Definition of done

100% branch coverage on `geo.py`; all tests pass; no imports outside the standard library.

---

## Phase 2 — Data layer & Groups API

**Goal:** groups and members can be created, read, updated and removed via REST; data persists in SQLite locally and PostgreSQL in Docker.

### Data model (`models.py`)

| Table.column | Type | Notes |
|--------------|------|-------|
| `groups.id` | varchar(12) PK | Random 48-bit hex (`secrets.token_hex(6)`); also the invite token |
| `groups.name` | varchar(120) | |
| `groups.travel_mode` | varchar(16) | `DRIVE`, `TWO_WHEELER`, `TRANSIT`, `WALK`, `BICYCLE` |
| `groups.meet_at` | varchar(40) null | ISO-8601 **with UTC offset** |
| `groups.venue_types` | JSON | Places (New) type ids, e.g. `cafe`, `park` |
| `groups.created_at` | timestamptz | Used by the 30-day purge |
| `members.id` / `group_id` | varchar(12) PK / FK → groups, indexed | `ON DELETE CASCADE` |
| `members.name` | varchar(80) | |
| `members.lat`, `lng` | float | Validated to ±90 / ±180 |
| `members.address` | varchar(300) null | Geocoder's formatted address or user label |
| `members.travel_mode` | varchar(16) null | Overrides group mode |
| `recommendations.id` / `group_id` | varchar(12) PK / FK, indexed | `ON DELETE CASCADE` |
| `recommendations.payload` | JSON | Full engine output |
| `recommendations.created_at` | timestamptz | |

Tables are created at start-up in v1 (Alembic arrives in Phase 8).

### Schemas (`schemas.py`)

- `TravelMode` enum (5 values).
- `GroupCreate { name, travel_mode, meet_at?, venue_types[] }`, `GroupPatch` (all optional).
- `MemberCreate { name, lat?, lng?, address?, travel_mode? }` with a model validator: **either** `lat+lng` **or** `address` is required.
- Length limits matching column sizes; `venue_types` from an allow-list (`cafe`, `restaurant`, `park`, `bar`, `shopping_mall`, …).
- `meet_at` parsed as timezone-aware datetime; naive datetimes rejected with 422.

### Endpoints (`routers/groups.py`)

| Method & path | Success | Errors |
|---------------|---------|--------|
| `POST /api/groups` | 201 group | 422 validation |
| `GET /api/groups/{id}` | 200 group with members | 404 `GROUP_NOT_FOUND` |
| `PATCH /api/groups/{id}` | 200 group | 404, 422 |
| `POST /api/groups/{id}/members` | 201 member | 404, 422 `TOO_MANY_MEMBERS`, 422 `ADDRESS_NOT_FOUND`, 502 `GEOCODER_FAILED`, 503 `GEOCODER_UNAVAILABLE` |
| `DELETE /api/groups/{id}/members/{mid}` | 204 | 404 `MEMBER_NOT_FOUND` |

> Address-based member add depends on the geocoder from Phase 3; until then, accept `lat+lng` only and return 503 `GEOCODER_UNAVAILABLE` for address-only requests.

### Tests (`tests/test_api.py`, part 1)

CRUD round trips on SQLite; 26th member rejected; cascade delete removes members and recommendations; invalid coordinates/modes → 422; unknown group → 404 with the standard error body.

### Definition of done

All group/member endpoints work against SQLite and a PostgreSQL 16 container; error bodies always match the contract.

---

## Phase 3 — Google client, region & geocode proxy

**Goal:** a single async adapter for all Google calls, with errors normalised so the engine can choose fallbacks. The browser never sees the key.

### `services/google.py`

Define a `MapClient` **protocol** (so tests inject a fake) and a `GoogleMapClient` implementation using one `httpx.AsyncClient` per request (timeouts: connect 3 s, read 5 s).

| Method | Google endpoint | Key details |
|--------|-----------------|-------------|
| `route_matrix(origins, destinations, mode, traffic_aware, departure_time?, arrival_time?)` | Routes `computeRouteMatrix` | Field mask: `originIndex,destinationIndex,duration,distanceMeters,condition,status`. `routingPreference`: `TRAFFIC_UNAWARE` (zones) / `TRAFFIC_AWARE` (venues, road modes only). Enforce **625 elements** (100 for `TRANSIT`) and chunk origins/destinations if exceeded. |
| `search_nearby(center, radius_m, types)` | Places (New) `places:searchNearby` | `includedTypes`, `maxResultCount: 20`, `rankPreference: POPULARITY`, circular `locationRestriction` (≤ 50 km). Field mask limited to the ~10 fields the UI needs (id, displayName, location, rating, userRatingCount, currentOpeningHours.openNow, googleMapsUri, primaryType, …) to stay in the cheapest billing tier. |
| `geocode(address)` | Geocoding `geocode/json?address=` | Return up to 5 matches; `ZERO_RESULTS` → empty list (**not** an error). |
| `reverse_geocode(lat, lng)` | Geocoding `geocode/json?latlng=` | Raw components for `region.py`. |

All HTTP/quota/timeout failures raise `GoogleError(kind, status, message)`; a specific kind for "travel mode not supported" so the engine can retry `TWO_WHEELER` as `DRIVE`.

### `deps.py`

`get_maps()` → `GoogleMapClient` if `GOOGLE_MAPS_API_KEY` is set, otherwise `None` (engine then uses estimates only).

### `services/region.py`

Parse reverse-geocode components into `{ area, locality, country_code, units }`. `units = "imperial"` for `US, GB, LR, MM`, else `"metric"`. On failure: empty region, metric units.

### Geocode proxy (`routers/meeting.py`)

`GET /api/geocode?q=` → up to 5 `{ label, lat, lng }` matches. No key → 503 `GEOCODER_UNAVAILABLE`; Google failure → 502 `GEOCODER_FAILED`. Also wire geocoding into `POST /members` when only `address` is given (0 matches → 422 `ADDRESS_NOT_FOUND` naming what to add).

### Tests

- Unit tests with `httpx.MockTransport`: request bodies, field masks, chunking, error mapping, `ZERO_RESULTS` handling.
- `region.py`: one fixture per continent; units correct for US/GB/LR/MM vs others.

### Definition of done

All three Google APIs callable through the adapter; no Google URL or key in the frontend bundle; tests run with no network.

---

## Phase 4 — Meeting engine (`services/engine.py`)

**Goal:** the core product logic. Input = plain data (members with coordinates and modes, venue types, meeting time) + an injected `MapClient | None`. Output = a result object. **No FastAPI or DB imports.**

### Pipeline

| Stage | Input → output | Logic | External calls |
|-------|----------------|-------|----------------|
| 1. Validate | members → members, warnings | 2–25 people else `NOT_ENOUGH_MEMBERS`/`TOO_MANY_MEMBERS`; all at same place → notice; spread > `MAX_GROUP_SPREAD_KM` → warning and switch WALK/BICYCLE/TRANSIT to DRIVE | — |
| 2. Centre | points → centre | `geometric_median` seeded by spherical centroid | — |
| 3. Zones | centre, spread → ≤ 20 points | Centre + 6 at r/2 + 12 at r + centroid; `r = ring_radius_km(spread)`; cap so `n_members_in_mode × zones ≤ limit` (625, or 100 for TRANSIT) — drop outer ring points first | — |
| 4. Region | centre → area, city, country, units | `reverse_geocode` + `region.py` | 1 Geocoding |
| 5. Zone matrix | members × zones → minutes | Group members by effective mode; one request per mode, **traffic-unaware**, run with `asyncio.gather`; drop any zone where **any** member has no route | 1 per mode (chunked) |
| 6. Score zones | minutes → S per zone | `S = W_MAX·max + W_STD·σ + W_MEAN·mean`; pick top `TOP_ZONES` greedily with min separation `0.4·r` | — |
| 7. Venues | 3 zones → venue pool | `search_nearby` at 800 → 1500 → 3000 m until ≥ 3 venues per zone; de-dupe by place id; compute Q | ≤ 9 Places |
| 8. Venue matrix | members × top 10 → minutes | **Traffic-aware** for road modes; `meet_at` as `departureTime` (road) or `arrivalTime` (TRANSIT); past time ignored with notice | 1 per mode |
| 9. Rank | → top 5 | `Final = 0.7·norm(S) + 0.3·(1 − Q)`; return lowest 5 | — |

### Scoring reference

```python
def fairness_score(minutes: list[float], w=(0.5, 0.3, 0.2)) -> float:
    m = mean(minutes)
    sd = pstdev(minutes)              # population σ: 20/20/20 → 14.0, 5/10/45 → 31.8
    return w[0] * max(minutes) + w[1] * sd + w[2] * m

def fairness_index(minutes) -> float:
    m = mean(minutes)
    return 1.0 if m == 0 else max(0.0, 1 - pstdev(minutes) / m)

def bayesian_rating(rating: float | None, n: int | None, prior=4.0, k=50) -> float:
    n = n or 0
    return prior if not rating else (n * rating + k * prior) / (n + k)

def venue_quality(rating, n, open_now, dist_to_zone_m, max_radius_m) -> float:
    closeness = 1 - min(dist_to_zone_m / max_radius_m, 1)
    return 0.6 * bayesian_rating(rating, n) / 5 + 0.25 * (1 if open_now else 0) + 0.15 * closeness

def final_score(S, S_min, S_max, Q) -> float:
    norm = 0.0 if S_max == S_min else (S - S_min) / (S_max - S_min)   # min-max over the venue pool
    return 0.7 * norm + 0.3 * (1 - Q)
```

### Fallbacks (must all be implemented and tested)

| Failure | Detected by | Behaviour | User sees |
|---------|-------------|-----------|-----------|
| No Google key | `get_maps()` returns `None` | Distance-based estimates; no venues; no region | Estimate notice; fair zones on map |
| Routes error (quota, 5xx, timeout) | `GoogleError` in `route_matrix` | `haversine × 1.3 / FALLBACK_SPEED_KMH[mode]`; `estimated = true` | "Live travel times weren't available" |
| Two-wheeler not offered | `GoogleError` (mode unsupported) for `TWO_WHEELER` | Retry same request as `DRIVE` | Notice naming the switch |
| Zone unreachable for someone | `condition != ROUTE_EXISTS` | Zone dropped | Nothing, unless none left |
| No zone reachable | Empty zone list | Stop with `NO_REACHABLE_AREA` | Suggests driving or checking addresses |
| Places error / no venues after 3 km | `GoogleError` / empty | Return zones only | "No venues found" |
| Reverse geocode fails | `GoogleError` | Region empty; metric units | Generic heading |

### Call & element budget (assert in tests)

With n people in one mode: zone matrix ≤ n × 20 elements (≤ 100 for TRANSIT), venue matrix ≤ n × 10, ≤ 9 Places calls, 1 reverse geocode. Example: 6 people driving ≈ 120 + 60 = 180 elements. Mixed modes add requests, not elements.

### Endpoint

`POST /api/groups/{id}/meeting-point` (`routers/meeting.py`): load group + members → build engine input (resolve effective mode per member) → `await engine.run(...)` → insert into `recommendations` → return payload:

```json
{
  "id": "4f1c2a9b7e03",
  "center": { "lat": 38.7291, "lng": -9.1503 },
  "region": { "area": "Avenidas Novas", "locality": "Lisboa", "country_code": "PT", "units": "metric" },
  "zones": [{ "lat": 38.7302, "lng": -9.1488, "score": 13.9, "fairness": 0.86, "minutes": { "m1": 14.2 } }],
  "venues": [{
    "id": "ChIJ...", "name": "Café Example", "type": "cafe", "rating": 4.6, "ratings": 812,
    "open_now": true, "maps_url": "https://maps.google.com/?cid=...", "lat": 38.7305, "lng": -9.1479,
    "score": 0.142, "fairness": 0.84, "max_minutes": 16.4,
    "travel": [{ "member_id": "m1", "name": "Inês", "mode": "TRANSIT", "minutes": 14.5, "meters": 3900 }]
  }],
  "warnings": [],
  "estimated": false
}
```

### Tests (`tests/test_engine.py`)

- `FakeMapClient` returning travel times from haversine × mode speed and synthetic venues; **asserts the 625/100 element limits on every call** and counts calls.
- Full pipeline in **7 cities on 5 continents** (include one southern-hemisphere and one near-antimeridian case, one imperial-unit country).
- One test per fallback row and per PRD edge case (same place, > 100 km, unreachable centre, no venues, two-wheeler unsupported, past meeting time, 25-person TRANSIT group).
- Scoring unit tests: 20/20/20 → 14.0; 5/10/45 → 31.8; Bayesian 5.0×3 < 4.6×2000.

### Definition of done

Endpoint returns ranked venues in all test cities; every fallback covered; call budget asserted; engine module has no HTTP/DB imports.

---

## Phase 5 — Frontend: setup, people & invite link

**Goal:** an organiser can set up a meetup and add people; invitees can join from a link.

### Components

| Component | Responsibilities |
|-----------|------------------|
| `api.js` | `fetch` wrapper; parses `{detail:{code,message}}`; throws typed `ApiError` so UI can branch on `code` |
| `App.jsx` | Owns state: group id, settings, members, result, selected venue, dropped pin, errors. Reads `?g=<id>` on load → `GET /api/groups/{id}`; 404 → clear param, show "This group doesn't exist or was deleted", start fresh. Creates the group **lazily** on first person/invite and writes `?g=<id>` with `history.replaceState`. **Clears results after any change.** |
| `GroupSettings.jsx` | Meetup name, group travel mode, optional meeting time (stored as ISO-8601 with viewer's UTC offset), venue-type multi-select; PATCH on change (debounced) |
| `People.jsx` | List with line colour, address, mode override; remove button |
| `AddPerson.jsx` | Name; address search via `/api/geocode` (up to 5 matches); "Use my location" (Geolocation API; on denial point to search/pin); "Pick on map" mode; per-person mode |
| Invite | "Copy invite link" button (Clipboard API, fallback select-text); Web Share API on mobile |

### UX rules

- Main button disabled with label *"Add at least 2 people"* when < 2 members.
- Colours assigned by member index from a fixed palette; members keyed by id (handles duplicate names).
- Map starts at world view (lat 20, lng 0, zoom 2) — no default city.

### Definition of done

Two browsers on the same `?g=` link can each add themselves; refresh restores state; invalid link handled; works on a 375 px wide screen.

---

## Phase 6 — Frontend: results & map

**Goal:** show the recommendation so fairness is obvious at a glance.

### Components

| Component | Responsibilities |
|-----------|------------------|
| `Results.jsx` | Region heading ("Near Avenidas Novas, Lisboa"); warnings and estimate notice; 5 ranked venue cards: name, type, rating (count), open-now label, fairness index; **travel strips** — one bar per person on a **shared time scale across all venues**, labelled with minutes and distance; "Open in Google Maps" (`maps_url`) and "Share" actions; zones-only state when no venues |
| `MapView.jsx` | Leaflet + OSM tiles with attribution; members as coloured markers; fair zones as dashed circles; numbered venue markers; selecting a card highlights its marker and vice versa; fit bounds to members + venues; click to drop a pin (feeds `AddPerson`) |
| `format.js` | Mode/venue labels; minutes formatting; km vs mi from `region.units`; numbers via `Intl.NumberFormat(navigator.language)` |

### Instrumentation hooks (for PRD metrics)

Emit client events: `app_open`, `group_created`, `member_added` (method: search/gps/pin), `search_started`, `search_succeeded` (venues count, estimated), `venue_opened_in_maps`, `venue_shared`. Event transport can be a no-op until Phase 9.

### Definition of done

End-to-end: create group → add 3 people → find → see 5 venues with strips and map; selecting a venue syncs list and map; imperial units shown for a US group.

---

## Phase 7 — P1 features & polish

**Goal:** complete the P1 scope and harden the UX before production work.

### Tasks

1. **Meeting time (P1)** — engine already supports it; verify UI round-trip, viewer-local display, past-time notice, departure vs arrival time per mode.
2. **Recommendation history (P1)** — `GET /api/groups/{id}/recommendations?limit=5` (latest first) and on load show the latest saved result *only if* no member/setting changed since `created_at`; otherwise hide it (never show stale advice).
3. **Error UX** — map every error code to copy and a next action (e.g. `NO_REACHABLE_AREA` → "Try driving or check addresses"; `ADDRESS_NOT_FOUND` → "Add the area and city, or drop a pin").
4. **Loading state** — progress skeleton during search (target < 5 s); disable duplicate submissions.
5. **Accessibility basics** — keyboard navigation, labelled controls, colour + text in strips (not colour alone), contrast AA.
6. **Responsive layout** — map/list stacked on mobile, side-by-side on desktop.

### Definition of done

All P0 + P1 features in the PRD feature table work; full manual QA pass of the PRD edge-case table.

---

## Phase 8 — Production hardening

**Goal:** close every item marked *planned* in the architecture before public launch.

| Area | Task |
|------|------|
| Containers | `src/backend/Dockerfile` (slim Python 3.12, non-root, Uvicorn workers); `docker-compose.yml` with `postgres:16` + volume + API |
| Migrations | Replace start-up table creation with **Alembic**; baseline migration from current models |
| Data retention | Nightly job deleting groups older than `GROUP_RETENTION_DAYS` (cascade to members/recommendations); runs as a scheduled container/cron |
| Secrets | `GOOGLE_MAPS_API_KEY` from a secrets manager; key restricted to Routes, Places (New), Geocoding and server egress IPs |
| Rate limiting | Per IP and per group (e.g. 10 searches/min) on `/meeting-point` and `/geocode`; 429 with standard error body |
| Transport | HTTPS only; HSTS at the load balancer; `/api` → API, everything else → CDN |
| Privacy | No coordinates or addresses in logs; log member ids only |
| Observability | Request ids; per-stage engine timings; counters per fallback and error code; Google calls and elements per search; `/health` used by LB |
| Performance | Run DB read in parallel with nothing blocking; run the 3 Places zone searches **in parallel** (`asyncio.gather`) to meet the 1.5 s budget |
| Tiles | Switch OSM tiles to a commercial tile provider for production traffic (tile usage policy) |
| Load test | k6/Locust against a staging environment with a fake map client: p95 engine overhead < 200 ms; with real Google in staging p95 < 5 s |

### Performance budget (p95 < 5 s)

| Step | Target |
|------|--------|
| DB read (group + members) | < 50 ms |
| Reverse geocode | < 400 ms |
| Zone matrix (parallel across modes) | < 1.5 s |
| Places searches (parallel zones) | < 1.5 s |
| Venue matrix | < 1.2 s |
| Scoring, serialisation, save | < 100 ms |

### Definition of done

Staging environment mirrors production; security checklist signed off; purge job verified; dashboards show live traffic from staging.

---

## Phase 9 — Benchmark, beta & launch

**Goal:** prove the PRD metrics and launch.

### Tasks

1. **Offline fairness benchmark** — script runs synthetic groups (3–10 people) in **20 cities** across continents against real Google APIs; compares longest trip at the top venue vs the venue nearest the plain centroid. **Target: ≥ 20% shorter.** Store results for regression tracking.
2. **Metrics dashboard** for each PRD metric:
   - Time to first recommendation (median < 60 s)
   - Search latency (p95 < 5 s)
   - Acceptance (40% Open in Maps / Share)
   - Success rate (97% ≥ 1 venue)
   - Reach (50+ cities, 10+ countries, no city > 30%)
   - API usage per search (≤ 2 matrix, ≤ 9 Places, ≤ 1 reverse geocode)
   - Repeat use (25% organisers create a second meetup in 30 days)
3. **Billing alerts** on Google Maps Platform per API.
4. **Closed beta** with ~50 groups in several countries; triage feedback; fix P0 bugs.
5. **Launch checklist** — privacy notice, OSM/Google attributions, error budget, on-call runbook for fallback spikes.

### Definition of done

Benchmark meets ≥ 20% target; dashboards live; beta exit criteria met; public launch.

---

## Phase 10 — Post-launch evolution (v1.x → v2)

Each item is triggered by a real signal, not built speculatively.

| Trigger | Change | Release |
|---------|--------|---------|
| Repeated searches in busy areas | Redis cache for Places results (key: rounded zone centre + type list) and recent matrices | v1.x |
| p95 latency over 5 s | Parallel Places calls (if not done); then background job + polling | v1.x |
| Groups editing at the same time | Server-sent events or WebSocket updates of the people list | v1.x |
| Need for edit control | Organiser token for edits; longer ids; optional accounts | v1.x |
| "Venue closed at meeting time" complaints | Use `regularOpeningHours` at the meeting time instead of `openNow` | v2 |
| Accessibility routing | Per-person wheelchair preference passed to Routes for transit | v2 |
| Product roadmap (P2) | Venue voting, calendar invite | v2 |
| Mobile engagement | Native iOS/Android wrappers | v2+ |

---

## Cross-cutting standards (all phases)

- **City-agnostic rule:** code review rejects any hard-coded city, coordinate, bounding box, unit or mode assumption.
- **Layering rule:** routers → engine → adapters; the engine never imports FastAPI or the DB.
- **Bounded cost rule:** any change to the engine must keep the fake-client call/element assertions passing.
- **Degrade, don't fail:** every new external call ships with a fallback and a test for it.
- **Testing pyramid:** `test_geo.py` (pure maths) → `test_engine.py` (pipeline with fake client, 7 cities) → `test_api.py` (HTTP end to end on SQLite) → manual/E2E (Playwright) for the 7-step user flow.
- **Definition of done for any PR:** tests added, lint clean, no secrets, error codes documented, PRD edge cases unchanged or updated.
