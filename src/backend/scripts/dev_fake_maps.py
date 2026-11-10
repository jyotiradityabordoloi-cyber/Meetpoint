"""Run the API for local development and demos without a Google Maps key.

- Place search as you type and area names use Photon (real OpenStreetMap data,
  https://photon.komoot.io, fair-use public instance). Set PHOTON_URL to use
  your own instance, or PHOTON_URL="" to work fully offline.
- Venues and area names are real OpenStreetMap data via Photon (no ratings or
  photos); travel times are estimated from distance. With PHOTON_URL="" the
  fake map client the tests use takes over (made-up venues, fully offline).
- Journey paths on the map follow real roads via OSRM (OpenStreetMap routing,
  https://routing.openstreetmap.de, fair use). Set OSRM_URL="" to draw curves instead.
- Your approximate location (for search ranking and the first map view) is
  looked up from this machine's public IP. Set DEV_LOCATE_PUBLIC_IP=false to stop.

Never use this in production.

    cd src/backend
    python scripts/dev_fake_maps.py            # serves on :8000
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.config import Settings  # noqa: E402
from app.deps import get_maps, get_router, get_search  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.osm import OsmMapClient  # noqa: E402
from app.services.routes import OsrmRoutes  # noqa: E402
from app.services.search import PhotonSearch  # noqa: E402
from tests.fakes import FakeMapClient  # noqa: E402

PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io")
OSRM_URL = os.environ.get("OSRM_URL", "https://routing.openstreetmap.de")


def build():
    settings = Settings(
        google_maps_api_key="fake",
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./meetpoint-demo.db"),
        rate_limit_per_minute=1000,
        photon_url=PHOTON_URL,
        dev_locate_public_ip=os.environ.get("DEV_LOCATE_PUBLIC_IP", "true").lower() != "false",
    )
    app = create_app(settings)

    async def demo_maps():
        # Same as production without a Google key: real OSM venues and area names
        # via Photon, distance-estimated times. Fully offline fake if PHOTON_URL="".
        if not PHOTON_URL:
            yield FakeMapClient(country=os.environ.get("FAKE_COUNTRY", "PT"))
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as http:
            yield OsmMapClient(PHOTON_URL, http)

    async def demo_search():
        if not PHOTON_URL:
            yield None
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
            yield PhotonSearch(PHOTON_URL, http)

    app.dependency_overrides[get_maps] = demo_maps
    app.dependency_overrides[get_search] = demo_search

    async def demo_router():
        if not OSRM_URL:
            yield None
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as http:
            yield OsrmRoutes(OSRM_URL, http)

    app.dependency_overrides[get_router] = demo_router
    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
