"""Place names for arbitrary map points (OpenStreetMap Nominatim).

Nominatim's usage policy asks for an identifying User-Agent and at most one
request per second; results are cached so each point is looked up once.
"""

from __future__ import annotations

from functools import lru_cache

import httpx

REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "FireWatch/0.1 (Nebius x NVIDIA hackathon demo)"


def coords_label(lat: float, lon: float) -> str:
    return f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"


@lru_cache(maxsize=512)
def place_name(lat: float, lon: float) -> tuple[str, str, str | None]:
    """Return (place, country, country_code) near the point.

    country_code is "" when the point has no country (open sea) and None when
    the lookup itself failed, so callers can tell the two apart.
    """
    try:
        resp = httpx.get(REVERSE_URL, timeout=8, headers={"User-Agent": USER_AGENT}, params={
            "lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10, "accept-language": "en"})
        resp.raise_for_status()
        addr = resp.json().get("address", {})
    except Exception:
        return coords_label(lat, lon), "", None
    place = next((addr[k] for k in ("village", "town", "city", "municipality", "county",
                                    "state_district", "state") if addr.get(k)), None)
    return place or coords_label(lat, lon), addr.get("country", ""), addr.get("country_code", "")


@lru_cache(maxsize=256)
def search(query: str, limit: int = 5) -> list[dict]:
    """Forward search: place name -> candidate points."""
    resp = httpx.get(SEARCH_URL, timeout=8, headers={"User-Agent": USER_AGENT}, params={
        "q": query, "format": "jsonv2", "limit": limit, "accept-language": "en"})
    resp.raise_for_status()
    return [{"label": r.get("display_name", ""), "lat": round(float(r["lat"]), 2),
             "lon": round(float(r["lon"]), 2)} for r in resp.json()]
