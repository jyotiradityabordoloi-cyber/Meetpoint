"""All tunable values, read from environment variables (or .env).

There is deliberately no default city, coordinate, bounding box or country here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

TRAVEL_MODES = ("DRIVE", "TWO_WHEELER", "TRANSIT", "WALK", "BICYCLE")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_maps_api_key: str = ""
    database_url: str = "sqlite:///./meetpoint.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Group limits
    min_members: int = 2
    max_members: int = 25
    max_group_spread_km: float = 100.0
    same_place_km: float = 0.05

    # Candidate zones
    candidate_radius_ratio: float = 0.25
    candidate_radius_min_km: float = 0.5
    candidate_radius_max_km: float = 10.0
    top_zones: int = 3
    zone_min_separation_ratio: float = 0.4

    # Fairness score weights: S = w_max*max + w_std*std + w_mean*mean
    w_max: float = 0.5
    w_std: float = 0.3
    w_mean: float = 0.2

    # Venues
    venue_radius_steps: str = "800,1500,3000"
    min_venues_per_zone: int = 3
    venue_pool_size: int = 10
    venues_to_return: int = 5
    final_weight_fairness: float = 0.7
    bayes_prior_rating: float = 4.0
    bayes_prior_weight: int = 50

    # Fallback estimates when routing is unavailable
    fallback_speed_kmh: str = "DRIVE=30,TWO_WHEELER=28,TRANSIT=22,BICYCLE=15,WALK=4.8"
    fallback_detour_factor: float = 1.3

    # Google route-matrix element limits
    matrix_max_elements: int = 625
    matrix_max_elements_transit: int = 100
    google_timeout_s: float = 8.0

    # Place search as you type. Google Places Autocomplete is used when a Maps
    # key is set; otherwise Photon (OpenStreetMap) if PHOTON_URL is set,
    # e.g. https://photon.komoot.io (fair use) or a self-hosted instance.
    photon_url: str = ""

    # Journey paths drawn on the map. Google Routes with a key; otherwise OSRM
    # if OSRM_URL is set (e.g. https://routing.openstreetmap.de, fair use).
    osrm_url: str = ""

    # Approximate visitor location (search bias, first map view). Edge headers
    # (Cloudflare / Vercel / GCP) are read when present; GEOIP_DB_PATH points to
    # a MaxMind GeoLite2-City .mmdb file. DEV_LOCATE_PUBLIC_IP is for local dev.
    geoip_db_path: str = ""
    trust_geo_headers: bool = True
    dev_locate_public_ip: bool = False

    # Privacy / abuse
    group_retention_days: int = 30
    purge_on_startup: bool = True
    rate_limit_per_minute: int = 10

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def venue_radius_steps_m(self) -> list[int]:
        return [int(x) for x in self.venue_radius_steps.split(",") if x.strip()]

    @property
    def fallback_speeds(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for part in self.fallback_speed_kmh.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip().upper()] = float(v)
        return out

    @property
    def maps_configured(self) -> bool:
        return bool(self.google_maps_api_key.strip())

    def matrix_limit(self, mode: str) -> int:
        return self.matrix_max_elements_transit if mode == "TRANSIT" else self.matrix_max_elements


@lru_cache
def get_settings() -> Settings:
    return Settings()
