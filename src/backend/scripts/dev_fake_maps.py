"""Run the API for local development and demos without a Google Maps key.

- Place search as you type and area names use Photon (real OpenStreetMap data,
  https://photon.komoot.io, fair-use public instance). Set PHOTON_URL to use
  your own instance, or PHOTON_URL="" to work fully offline.
- Travel times come from straight-line distance and venues are made up
  (the same fake map client the tests use), anywhere in the world.
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
from app.deps import get_maps, get_search  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.google import GeocodeResult, GoogleError  # noqa: E402
from app.services.search import PhotonSearch  # noqa: E402
from tests.fakes import FakeMapClient  # noqa: E402

PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io")


class DemoMapClient(FakeMapClient):
    """Fake travel times and venues, but real area names via Photon reverse geocoding."""

    async def reverse_geocode(self, point):
        if not PHOTON_URL:
            return await super().reverse_geocode(point)
        try:
            async with httpx.AsyncClient(timeout=4.0) as http:
                r = await http.get(
                    PHOTON_URL.rstrip("/") + "/reverse",
                    params={"lat": point.lat, "lon": point.lng, "lang": "en"},
                    headers={"User-Agent": "MeetPoint-dev"},
                )
            feats = r.json().get("features") or []
        except (httpx.HTTPError, ValueError) as e:
            raise GoogleError("network", type(e).__name__) from e
        if not feats:
            return []
        p = feats[0].get("properties") or {}
        comps = []
        if p.get("district") or p.get("locality"):
            comps.append({"long_name": p.get("district") or p.get("locality"), "short_name": "", "types": ["neighborhood"]})
        if p.get("city") or p.get("county"):
            comps.append({"long_name": p.get("city") or p.get("county"), "short_name": "", "types": ["locality"]})
        if p.get("countrycode"):
            comps.append({"long_name": p.get("country", ""), "short_name": p["countrycode"], "types": ["country"]})
        return [GeocodeResult(label=p.get("name", ""), lat=point.lat, lng=point.lng, components=comps)]


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
        yield DemoMapClient(country=os.environ.get("FAKE_COUNTRY", "PT"))

    async def demo_search():
        if not PHOTON_URL:
            yield None
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
            yield PhotonSearch(PHOTON_URL, http)

    app.dependency_overrides[get_maps] = demo_maps
    app.dependency_overrides[get_search] = demo_search
    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
