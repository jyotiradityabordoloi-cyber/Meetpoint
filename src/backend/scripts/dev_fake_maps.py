"""Run the API with the offline FAKE map client used by the tests.

For UI development and demos without a Google Maps key: travel times come
from straight-line distance and venues are synthetic ("Venue ab12").
Never use this in production.

    cd src/backend
    python scripts/dev_fake_maps.py            # serves on :8000
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import uvicorn  # noqa: E402

from app.config import Settings  # noqa: E402
from app.deps import get_maps  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.fakes import FakeMapClient  # noqa: E402


def build():
    settings = Settings(
        google_maps_api_key="fake",
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./meetpoint-demo.db"),
        rate_limit_per_minute=1000,
    )
    app = create_app(settings)

    async def fake_maps():
        yield FakeMapClient(country=os.environ.get("FAKE_COUNTRY", "PT"))

    app.dependency_overrides[get_maps] = fake_maps
    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
