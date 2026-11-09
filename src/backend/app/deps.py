from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

import httpx
from fastapi import Request

from .config import Settings
from .errors import ApiError
from .services.google import GoogleMapClient, MapClient
from .services.search import GooglePlacesSearch, PhotonSearch, SearchProvider


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_maps(request: Request) -> AsyncIterator[MapClient | None]:
    """One Google client per request, or None when no key is configured."""
    settings: Settings = request.app.state.settings
    if not settings.maps_configured:
        yield None
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.google_timeout_s, connect=3.0)) as http:
        yield GoogleMapClient(
            settings.google_maps_api_key,
            http,
            {"TRANSIT": settings.matrix_max_elements_transit, "*": settings.matrix_max_elements},
        )


async def get_search(request: Request) -> AsyncIterator[SearchProvider | None]:
    """Google Places Autocomplete with a key; Photon (OSM) if configured; else None."""
    settings: Settings = request.app.state.settings
    if settings.maps_configured:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
            yield GooglePlacesSearch(settings.google_maps_api_key, http)
    elif settings.photon_url:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
            yield PhotonSearch(settings.photon_url, http)
    else:
        yield None


class RateLimiter:
    """Small in-process sliding-window limiter (per API instance).

    Good enough for v1 abuse protection; swap for Redis or the load balancer's
    limiter when running many instances.
    """

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, *keys: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        for key in keys:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise ApiError(429, "RATE_LIMITED", "Too many searches in a short time. Please wait a minute and try again.")
        for key in keys:
            self._hits[key].append(now)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
