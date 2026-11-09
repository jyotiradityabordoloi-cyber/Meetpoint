"""Approximate location from the visitor's IP address.

Used only to make search suggestions and the first map view relevant. It is
city-level at best, never stored and never logged. Sources, in order:

1. Location headers added by the hosting edge, when present:
   Cloudflare (CF-IPLatitude / CF-IPLongitude, "visitor location headers"),
   Vercel (X-Vercel-IP-Latitude / -Longitude), Google App Engine
   (X-AppEngine-CityLatLong).
2. A local MaxMind GeoLite2-City database (GEOIP_DB_PATH). No third party sees the IP.
3. Development only (DEV_LOCATE_PUBLIC_IP): when the visitor is on localhost,
   look up this machine's own public IP once via ipapi.co.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import asdict, dataclass
from urllib.parse import unquote

import httpx

log = logging.getLogger("meetpoint.locate")


@dataclass(frozen=True)
class ApproxLocation:
    lat: float
    lng: float
    city: str | None
    country_code: str | None
    source: str

    def to_dict(self) -> dict:
        d = asdict(self)
        # Coarsen: ~1 km is plenty for biasing search and centring a map.
        d["lat"] = round(self.lat, 2)
        d["lng"] = round(self.lng, 2)
        return d


def _float(v: str | None) -> float | None:
    try:
        f = float(v) if v not in (None, "") else None
    except ValueError:
        return None
    return f


def _valid(lat: float | None, lng: float | None) -> bool:
    return lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def from_headers(headers) -> ApproxLocation | None:
    lat, lng = _float(headers.get("cf-iplatitude")), _float(headers.get("cf-iplongitude"))
    if _valid(lat, lng):
        return ApproxLocation(lat, lng, headers.get("cf-ipcity"), headers.get("cf-ipcountry"), "cloudflare")

    lat, lng = _float(headers.get("x-vercel-ip-latitude")), _float(headers.get("x-vercel-ip-longitude"))
    if _valid(lat, lng):
        city = headers.get("x-vercel-ip-city")
        return ApproxLocation(lat, lng, unquote(city) if city else None, headers.get("x-vercel-ip-country"), "vercel")

    ll = headers.get("x-appengine-citylatlong")
    if ll and "," in ll:
        lat, lng = (_float(x) for x in ll.split(",", 1))
        if _valid(lat, lng):
            return ApproxLocation(lat, lng, headers.get("x-appengine-city"), headers.get("x-appengine-country"), "gcp")
    return None


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_global


class GeoIpDb:
    """Lazy reader for a MaxMind GeoLite2-City / GeoIP2-City .mmdb file."""

    def __init__(self, path: str):
        self.path = path
        self._reader = None
        self._failed = False

    def lookup(self, ip: str) -> ApproxLocation | None:
        if self._failed or not self.path:
            return None
        if self._reader is None:
            try:
                import maxminddb

                self._reader = maxminddb.open_database(self.path)
            except Exception as e:  # missing file or package: disable quietly
                log.warning("GeoIP database unavailable (%s)", type(e).__name__)
                self._failed = True
                return None
        try:
            rec = self._reader.get(ip) or {}
        except ValueError:
            return None
        loc = rec.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if not _valid(lat, lng):
            return None
        city = ((rec.get("city") or {}).get("names") or {}).get("en")
        country = (rec.get("country") or {}).get("iso_code")
        return ApproxLocation(float(lat), float(lng), city, country, "geoip")


class DevPublicIpLocator:
    """Development helper: where is *this machine*? Cached for an hour."""

    URL = "https://ipapi.co/json/"

    def __init__(self):
        self._cache: tuple[float, ApproxLocation | None] | None = None

    async def locate(self) -> ApproxLocation | None:
        now = time.monotonic()
        if self._cache and now - self._cache[0] < 3600:
            return self._cache[1]
        result = None
        try:
            async with httpx.AsyncClient(timeout=4.0) as http:
                r = await http.get(self.URL, headers={"User-Agent": "MeetPoint-dev"})
            if r.status_code == 200:
                d = r.json()
                lat, lng = d.get("latitude"), d.get("longitude")
                if _valid(lat, lng):
                    result = ApproxLocation(float(lat), float(lng), d.get("city"), d.get("country_code"), "dev-public-ip")
        except (httpx.HTTPError, ValueError) as e:
            log.info("dev public-IP lookup failed (%s)", type(e).__name__)
        self._cache = (now, result)
        return result
