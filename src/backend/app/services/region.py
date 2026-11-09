"""Turn reverse-geocode results into area, city, country and distance units."""

from __future__ import annotations

from typing import Any, Iterable

IMPERIAL_COUNTRIES = {"US", "GB", "LR", "MM"}

AREA_TYPES = ("neighborhood", "sublocality_level_1", "sublocality", "sublocality_level_2", "colloquial_area")
LOCALITY_TYPES = ("locality", "postal_town", "administrative_area_level_3", "administrative_area_level_2")


def units_for(country_code: str | None) -> str:
    return "imperial" if (country_code or "").upper() in IMPERIAL_COUNTRIES else "metric"


def empty_region() -> dict:
    return {"area": None, "locality": None, "country_code": None, "units": "metric"}


def _components(results: Iterable[Any]) -> list[dict]:
    comps: list[dict] = []
    for r in results:
        comps.extend(getattr(r, "components", None) or (r.get("address_components", []) if isinstance(r, dict) else []))
    return comps


def _first(comps: list[dict], types: tuple[str, ...], key: str = "long_name") -> str | None:
    for t in types:  # priority order of types first, then result order
        for c in comps:
            if t in (c.get("types") or []):
                return c.get(key)
    return None


def region_from_results(results: Iterable[Any]) -> dict:
    comps = _components(results)
    if not comps:
        return empty_region()
    country = _first(comps, ("country",), "short_name")
    locality = _first(comps, LOCALITY_TYPES)
    area = _first(comps, AREA_TYPES)
    if area == locality:
        area = None
    return {
        "area": area,
        "locality": locality,
        "country_code": country.upper() if country else None,
        "units": units_for(country),
    }
