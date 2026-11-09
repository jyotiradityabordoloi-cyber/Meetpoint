# MeetPoint — Product Requirements Document

**Version:** 1.0 · **Date:** November 2026 · **Status:** Draft for review
**Companion docs:** [implementation.md](implementation.md) (phase-wise build plan), MeetPoint System Architecture v1.0

> Find a meeting place that is fair for everyone in a group to reach, in any city.

---

## 1. Problem definition

When a group of people in a city decides to meet, choosing the place is slow and usually unfair. Someone suggests a spot near them, the group negotiates in a chat thread, and the person who lives farthest away often ends up travelling two or three times longer than everyone else. Map apps answer *"how do I get from A to B"* for one person; none answer *"where should all of us go so that nobody is stuck with a long trip."*

A straight-line midpoint does not solve it:

- The midpoint can land in a lake, a rail yard or an area with nowhere to sit.
- Distance is a poor stand-in for **time** in cities with traffic, rivers, hills or patchy public transport.
- Travel habits differ by city: cars dominate in some, metro and buses in others, scooters and motorbikes in many Asian cities.

**Problem statement.** Groups have no quick, trustworthy way to pick a venue that balances everyone's travel time, in whichever city they happen to be in.

---

## 2. Goals and success metrics

| ID | Goal | Description |
|----|------|-------------|
| G1 | **Fast** | A group gets a fair recommendation within a minute of starting. |
| G2 | **Fair and explainable** | The recommendation minimises the longest and most uneven trips, and shows each person's travel time so the group can see why it is fair. |
| G3 | **Any city** | Works wherever Google Maps Platform has coverage, with no per-city setup, data or code. |
| G4 | **Predictable cost** | Every search uses a bounded number of map API calls. |

### Success metrics (90 days after launch)

| Metric | Target | Measured by |
|--------|--------|-------------|
| Time from opening the app to first recommendation | Median < 60 s | Client events |
| Search latency | p95 < 5 s | API logs |
| Fairness gain over naive midpoint | Longest trip at the top venue is ≥ 20% shorter than at the venue nearest the plain centroid | Offline benchmark in 20 cities |
| Recommendation acceptance | 40% of searches end in "Open in Google Maps" or a shared link on a suggested venue | Client events |
| Successful searches | 97% return at least one venue | API logs |
| Geographic reach | Searches in 50+ cities and 10+ countries; no single city above 30% of traffic | Region field on results |
| API usage per search | ≤ 2 route-matrix calls (chunked), ≤ 9 Places calls, ≤ 1 reverse geocode | API logs, billing export |
| Repeat use | 25% of organisers create a second meetup within 30 days | Group records |

---

## 3. Non-goals (v1)

- Booking tables, tickets, payments or deals.
- Live location sharing or tracking people while they travel.
- Group chat or venue voting — the invite link is shared in the chat app people already use.
- Long-distance trip planning. Groups spread over more than 100 km are **warned, not optimised**.
- Native iOS and Android apps — v1 is a responsive web app.
- Step-free or accessibility-aware routing (planned for v2).
- Building our own map, routing or places data.
- Personalised venue suggestions based on past behaviour.

---

## 4. Target audience

**Primary:** friend groups of 3–10 people, mostly 20–40 years old, living across a large metro area, who plan after-work and weekend meetups in messaging groups and travel by a mix of modes.

**Secondary:** hybrid-work teams choosing an offsite, families spread across a city, community organisers choosing a meetup venue, and two people meeting halfway.

### Personas

| Persona | Situation | What they need | What it means for the product |
|---------|-----------|----------------|-------------------------------|
| **Sam, 28 — the organiser** | Always ends up planning; tired of long chat debates | A decision in minutes that the group accepts | One person can add everyone; invite link for self-entry; clear ranked list |
| **Lerato, 31 — the far-away friend** | Lives on the city edge and takes public transport | Not to be the one who always travels longest | Score penalises the longest trip; her travel time is visible on every option |
| **Diego, 38 — the team lead** | Picking an offsite for 8 people who drive, ride or take transit | Fairness across mixed travel modes | Travel mode per person; up to 25 people; meeting time affects traffic |

### Never hard-coded (varies city to city)

- Dominant travel mode, and whether two-wheeler or transit routing is offered at all
- Distance units (km or miles)
- Time zone
- Typical venue types (cafés, pubs, malls, parks)
- City size

The product reads all of these from the group's own locations and choices.

---

## 5. Solution overview

MeetPoint is a responsive web app.

1. An organiser names the meetup, picks how people will travel, an optional meeting time and the kinds of places to suggest.
2. They add people by **address search**, **current location** or a **pin on the map**. Friends can open the invite link and add themselves.
3. With two or more people, **Find meeting point** returns **five ranked venues**.
4. Each venue shows **a bar per person on a shared time scale**, so a lopsided option is obvious at a glance; the map shows people, the fairest zones and the venues.

**Core idea:** optimise *travel-time fairness*, not distance. The engine finds a robust geographic centre, scores a ring of candidate zones by real travel times for every person, then searches venues only in the fairest zones and re-ranks them by actual per-person travel time and venue quality.

**City-agnostic by design:**

- No default city, coordinates or bounding boxes; the map starts at a world view.
- The region (area, city, country, units) comes from reverse-geocoding the group's centre.
- Search radii scale with how spread out the group is.
- Modes that a country does not support fall back to driving, with a notice.

---

## 6. System architecture (summary)

```
Web client (React + Leaflet/OSM)
        │  REST /api
        ▼
FastAPI backend
  ├── Groups API (groups, members, geocode proxy)
  ├── Meeting engine (centre → zones → scoring → venues)
  └── Google client (async, errors → fallbacks)
        │                              │
        ▼                              ▼
   PostgreSQL                   Google Maps Platform
   groups · members ·           Routes API (route matrix)
   recommendations              Places API (New) (nearby venues)
                                Geocoding API (address ↔ coordinates)
```

| Component | Responsibility | Technology |
|-----------|----------------|------------|
| Web client | Meetup setup, people list, address search, map, ranked results, invite link | React 18 (Vite), Leaflet with OpenStreetMap tiles |
| API | Groups and members CRUD, input validation, error codes, geocode proxy (API key never reaches the browser) | FastAPI, Pydantic |
| Meeting engine | Centre, candidate zones, travel matrices, scoring, venue search and re-ranking, fallbacks | Python service module (`engine.py`) |
| Google client | Route matrix, nearby places, geocoding; maps errors to fallbacks | httpx (async); Routes API, Places API (New), Geocoding API |
| Database | Groups, members (coordinates, mode), saved recommendations | PostgreSQL (SQLAlchemy); SQLite for local dev |

### Request lifecycle — *Find meeting point*

1. User clicks **Find meeting point** → frontend submits group.
2. Backend fetches group members and coordinates from PostgreSQL.
3. Backend asks Routes API for travel times to candidate zones → route matrix.
4. Backend ranks candidate zones, then searches nearby venues with Places API.
5. Backend asks Routes API for traffic-aware travel times to the top 10 venues.
6. Backend returns ranked recommendations; frontend displays meeting points.

### Data model

- **groups** (`id`, `name`, `travel_mode`, `meet_at`, `venue_types`, `created_at`)
- **members** (`id`, `group_id`, `name`, `lat`, `lng`, `address`, `travel_mode` override)
- **recommendations** (`id`, `group_id`, `payload` JSON, `created_at`)

### Main endpoints

| Method | Path |
|--------|------|
| POST | `/api/groups` |
| GET / PATCH | `/api/groups/{id}` |
| POST | `/api/groups/{id}/members` |
| DELETE | `/api/groups/{id}/members/{mid}` |
| POST | `/api/groups/{id}/meeting-point` |
| GET | `/api/geocode?q=` |

### Non-functional requirements

- Stateless API servers scale horizontally.
- Access is by unguessable group link (no accounts in v1).
- Exact coordinates are kept only as long as the meetup is useful: groups purged after **30 days** by default (configurable).

---

## 7. User flow

```
1 Open app ─► 2 Set up meetup ─► 3 Add yourself ─► 4 Invite others ─► 5 Find meeting point ─► 6 Compare options ─► 7 Go
  world map,    mode · time ·      search, GPS       copy link to       2+ people               travel strips + map    open in
  no city       places             or pin            chat                                                              Google Maps
```

Invited friends join at step 3 from the link; the organiser can also add everyone themselves.

### Flow-level rules

- **Can't find an address:** up to five matches are offered; if none fits, the user drops a pin on the map.
- **Location permission denied:** the user is pointed to address search or the map pin.
- **Fewer than two people:** the main button stays disabled and reads *"Add at least 2 people"*.
- **After changes** (person added/removed, mode or venue types changed): old results are cleared so stale advice is never shown.

---

## 8. Key features

| Feature | Description | Priority |
|---------|-------------|----------|
| Meetup and invite link | Create a meetup in one step; share a link so each person adds their own starting point | **P0** |
| Flexible location input | Address search, current location or map pin; works with any address format worldwide | **P0** |
| Travel mode per person | Car, two-wheeler, public transport, walk or cycle, with a group default | **P0** |
| Fair meeting-point engine | Ranks areas and venues by everyone's real travel time, weighted toward the longest trip | **P0** |
| Venue suggestions | Cafés, restaurants, parks, bars or malls near the fairest zones, with rating and open-now | **P0** |
| Travel strips | One bar per person on a shared scale, with minutes and distance in local units | **P0** |
| Map view | People in their line colours, fairest zones, numbered venues; click to drop a pin | **P0** |
| Graceful fallbacks | Estimates when routing is down; driving when a mode is unsupported; zones when no venues | **P0** |
| Meeting time | Uses traffic and transit timetables for the chosen time | **P1** |
| Recommendation history | Each search result is saved with the meetup | **P1** |
| Venue voting, calendar invite, accessibility routing | Later releases | **P2** |

---

## 9. Key logic

### 9.1 Fair centre

The **geometric median** of all starting points, computed with **Weiszfeld's algorithm on the sphere**. Unlike the centroid it is not pulled toward one person who lives far away, and spherical maths keeps it correct at any latitude and across the antimeridian.

### 9.2 Candidate zones

20 candidates: the centre, **6 points on a ring at r/2**, **12 on a ring at r**, plus the **centroid**, where

```
r = clamp(0.25 × group_spread, 0.5 km, 10 km)
group_spread = largest distance between any two people
```

The radius therefore suits a small town or a large metro without city-specific settings. Candidates are capped so `people × candidates` stays within the route-matrix limit (**625 elements**, or **100 for public transport**).

### 9.3 Travel times

- One route-matrix request **per travel mode in use** (people grouped by mode), chunked if needed.
- The wide zone scan uses **traffic-unaware** routing to keep cost down; the short list of final venues uses **traffic-aware** routing.
- A future meeting time is sent as **departure time** (road modes) or **arrival time** (public transport); a past time is ignored with a notice.
- Any zone that even one person cannot reach (water, no service) is dropped.

### 9.4 Fairness score (lower is better)

```
S = 0.5 × max + 0.3 × σ + 0.2 × mean      (minutes, across the group)
```

- `max` protects the person travelling farthest.
- `σ` (standard deviation) penalises imbalance.
- `mean` keeps total travel sensible.

**Example:** trips of 20/20/20 min score **14.0**, while 5/10/45 (same total) scores **31.8**, so the balanced option wins.

The app also shows a **fairness index** = `1 − σ / mean`, where 1.0 means everyone travels equally. Weights are configuration, not code.

### 9.5 Venues and final ranking

- The **top 3 zones** (at least `0.4 × r` apart, for variety) are searched with Places Nearby, starting at **800 m** and widening to **1.5 km** then **3 km** until at least 3 venues appear; results are de-duplicated by place ID.
- **Venue quality:**
  ```
  Q = 0.6 × (bayesian_rating / 5) + 0.25 × open_now + 0.15 × closeness_to_zone
  bayesian_rating = (n × rating + 50 × 4.0) / (n + 50)
  ```
  so a 5.0 from three reviews does not beat a 4.6 from 2,000.
- The **10 best venues** get real per-person travel times, then
  ```
  Final = 0.7 × normalised_S + 0.3 × (1 − Q)
  ```
  The **5 lowest** are returned.

### 9.6 Region and fallbacks

- Reverse-geocoding the centre gives area, city and country; the country sets distance units (**miles for US, GB, LR, MM**).
- If the Routes API fails, times are estimated from `straight-line distance × 1.3 detour factor` at a typical speed per mode, and results are marked as **estimates**.
- If two-wheeler routing is not offered in the country, driving is used instead.

---

## 10. Edge cases

| Case | Handling |
|------|----------|
| Fewer than 2 people / more than 25 | Button disabled; API returns `NOT_ENOUGH_MEMBERS` or `TOO_MANY_MEMBERS` with a clear message |
| Everyone at the same place | Notice shown; venues are suggested right around that spot |
| People in different cities (spread > 100 km) | Warning; walking, cycling and transit switch to driving; result may be a town in between |
| Fair centre falls in water, an airport or a park with no roads | Unreachable zones are dropped; if none is reachable, `NO_REACHABLE_AREA` suggests driving or checking addresses |
| Public transport not available for some trips | Missing routes drop those zones; user can change that person's mode |
| Two-wheeler routing not offered in the country | Automatic retry with driving, with a notice |
| Address not found or ambiguous | Up to 5 matches to choose from; map pin as fallback; `ADDRESS_NOT_FOUND` message names what to add (area, city) |
| Location permission denied | Message points to search or map pin |
| Sparse area with few venues | Search radius widens to 3 km; if still empty, the fairest zones are shown on the map instead |
| Venue closed | Open-now lowers the score and is labelled. *v1 limitation:* open-now reflects the current time; v2 checks opening hours at the meeting time |
| Meeting time in the past or unreadable | Ignored for routing, with a notice |
| Viewers in different time zones | Time is stored with its UTC offset and shown in each viewer's local time |
| Google quota, key or network failure | Routing falls back to estimates (flagged); Places failure returns zones only; address search asks for a pin |
| Large groups with public transport | Candidate count is reduced and requests are chunked to stay within the 100-element limit |
| Points near the antimeridian or the poles | All geometry uses spherical formulas |
| Two people with the same name | Members are identified by ID and shown in different line colours |
| Invalid or deleted invite link | *"This group doesn't exist or was deleted"* and the app starts a fresh meetup |
| Location privacy | API key stays on the server; link-only access; groups purged after 30 days by default |

---

## 11. Release scope summary

| Release | Contents |
|---------|----------|
| **v1 (MVP)** | All P0 features, P1 meeting time and recommendation history, responsive web, link-based access, 30-day purge |
| **v1.x** | Caching, parallel Places calls, rate limiting, organiser edit token, real-time people list |
| **v2** | Accessibility routing, opening hours at meeting time, venue voting, calendar invite, optional accounts |

See [implementation.md](implementation.md) for the phase-wise build plan.
