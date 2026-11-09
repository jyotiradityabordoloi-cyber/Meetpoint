from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .db import init_db
from .deps import RateLimiter
from .errors import install_error_handlers
from .purge import purge_old_groups
from .routers import groups, meeting

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("meetpoint")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="MeetPoint API", version="1.0.0")
    app.state.settings = settings
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.geocode_limiter = RateLimiter(settings.rate_limit_per_minute * 6)

    init_db(settings.database_url)
    if settings.purge_on_startup:
        try:
            n = purge_old_groups(settings.group_retention_days)
            if n:
                log.info("purged %d expired groups", n)
        except Exception:  # never block start-up on housekeeping
            log.exception("purge on startup failed")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def no_index_api(request, call_next):
        # API responses are data, not pages: keep them out of search results.
        response = await call_next(request)
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    install_error_handlers(app)
    app.include_router(groups.router)
    app.include_router(meeting.router)

    @app.get("/health")
    @app.get("/api/health")
    def health():
        return {"ok": True, "maps_configured": settings.maps_configured}

    if not settings.maps_configured:
        log.warning("GOOGLE_MAPS_API_KEY is not set: travel times will be estimates and no venues are returned")
    return app


# Run with: uvicorn app.main:create_app --factory --reload
