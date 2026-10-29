"""Active fire detections from NASA FIRMS (VIIRS satellites).

Near-real-time (NRT) data covers roughly the last two months; older dates
use the standard-processing (SP) archive.
"""

from __future__ import annotations

import csv
import io
import math
import os
from datetime import date, timedelta

import httpx

AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}/{day}"
NRT_WINDOW_DAYS = 60
MAX_DAY_RANGE = 10


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def active_fires(lat: float, lon: float, until: date, days: int = 2,
                 radius_km: float = 50.0) -> list[dict]:
    """Fire detections within `radius_km` of the point, during the `days`
    days ending on `until`, nearest first."""
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY is not set.")

    days = max(1, min(days, MAX_DAY_RANGE))
    first_day = until - timedelta(days=days - 1)
    recent = (date.today() - first_day).days <= NRT_WINDOW_DAYS
    source = "VIIRS_SNPP_NRT" if recent else "VIIRS_SNPP_SP"

    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    bbox = f"{lon - dlon:.3f},{lat - dlat:.3f},{lon + dlon:.3f},{lat + dlat:.3f}"

    # Errors never include the request URL: it contains the key and is shown to visitors.
    try:
        resp = httpx.get(AREA_URL.format(key=key, source=source, bbox=bbox,
                                         days=days, day=first_day.isoformat()), timeout=60)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"FIRMS unreachable ({type(exc).__name__})") from None
    if resp.status_code == 400:
        raise RuntimeError("FIRMS rejected the request: check that FIRMS_MAP_KEY is a valid MAP_KEY.")
    if resp.status_code != 200:
        raise RuntimeError(f"FIRMS error: HTTP {resp.status_code}")
    if not resp.text.startswith("latitude"):
        raise RuntimeError(f"FIRMS error: {resp.text.strip()[:200]}")

    fires = []
    for row in csv.DictReader(io.StringIO(resp.text)):
        f_lat, f_lon = float(row["latitude"]), float(row["longitude"])
        dist = _distance_km(lat, lon, f_lat, f_lon)
        if dist > radius_km:
            continue
        fires.append({
            "lat": f_lat, "lon": f_lon, "distance_km": round(dist, 1),
            "date": row["acq_date"], "time_utc": row["acq_time"].zfill(4),
            "frp_mw": float(row.get("frp") or 0.0),  # fire radiative power
            "confidence": row.get("confidence", ""),
            "daynight": row.get("daynight", ""),
        })
    return sorted(fires, key=lambda f: f["distance_km"])


def summarize(fires: list[dict]) -> str:
    if not fires:
        return "No satellite fire detections in the area."
    nearest = fires[0]
    total_frp = sum(f["frp_mw"] for f in fires)
    return (f"{len(fires)} satellite fire detections; nearest {nearest['distance_km']} km away "
            f"({nearest['date']} {nearest['time_utc']} UTC); total radiative power {total_frp:.0f} MW.")
