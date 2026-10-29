"""Daily noon weather from Open-Meteo, shaped for the FWI system.

Historical days come from the ERA5 archive (about 5 days behind real time);
recent and future days come from the forecast API. The FWI system is defined
on local *standard* noon, so times are requested in a fixed UTC offset derived
from the longitude (no daylight saving).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FORECAST_BACKOFF = 600      # seconds to rely on the fallback after the forecast API fails
_forecast_down_until = 0.0
HOURLY = "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation"
ARCHIVE_LAG_DAYS = 6


def standard_timezone(lon: float) -> str:
    """Fixed-offset zone for local standard time at this longitude.

    Etc/ zones use the POSIX sign convention: UTC+2 is "Etc/GMT-2".
    """
    offset = max(-12, min(12, round(lon / 15.0)))
    if offset == 0:
        return "Etc/GMT"
    return f"Etc/GMT{'-' if offset > 0 else '+'}{abs(offset)}"


# Outside the project tree: deep project paths can exceed the Windows path limit.
CACHE_DIR = Path(os.environ.get("FIREWATCH_CACHE", Path.home() / ".cache" / "firewatch")) / "weather"
RETRY_WAITS = (2, 5, 10, 20)  # seconds between attempts after HTTP 429


FORECAST_TTL = 3600  # seconds; forecasts are refreshed hourly upstream


class WeatherBusy(RuntimeError):
    """The weather service keeps rate-limiting us."""


def _get(url: str, params: dict, cache: bool = False, ttl: float | None = None) -> dict:
    """GET JSON with retries on rate limiting.

    With cache=True the response is stored on disk: ERA5 archive data for a
    past period never changes, so each request is only ever made once. With a
    ttl (seconds) the stored copy is reused only while it is that fresh,
    which suits forecasts.
    """
    path = None
    if cache or ttl:
        digest = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:32]
        path = CACHE_DIR / f"{digest}.json"
        if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
            return json.loads(path.read_text(encoding="utf-8"))

    for wait in (*RETRY_WAITS, None):
        resp = httpx.get(url, params=params, timeout=60)
        # Only the per-minute limit clears by waiting; hourly/daily limits fail at once.
        if resp.status_code != 429 or wait is None or "Minutely" not in resp.text:
            break
        time.sleep(wait)
    if resp.status_code == 429:
        raise WeatherBusy("The weather service is rate-limiting requests. Please try again in a minute.")
    resp.raise_for_status()
    data = resp.json()

    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _to_daily(hourly: dict) -> list[dict]:
    """Collapse hourly data to one FWI observation per day.

    temp / rh / wind (speed and the direction it blows from) are taken at
    12:00, rain is the 24 h total from 13:00 the previous day up to and
    including 12:00.
    """
    times = hourly["time"]
    directions = hourly.get("wind_direction_10m") or [None] * len(times)
    days: list[dict] = []
    rain_acc = 0.0
    for i, t in enumerate(times):
        precip = hourly["precipitation"][i] or 0.0
        rain_acc += precip
        if t.endswith("T12:00"):
            temp = hourly["temperature_2m"][i]
            rh = hourly["relative_humidity_2m"][i]
            wind = hourly["wind_speed_10m"][i]
            if None not in (temp, rh, wind):
                days.append({"date": t[:10], "temp": temp, "rh": rh, "wind": wind,
                             "wind_dir": directions[i], "rain": round(rain_acc, 2)})
            rain_acc = 0.0
    return days


def _archive(lat: float, lon: float, start: date, end: date) -> list[dict]:
    data = _get(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": HOURLY, "timezone": standard_timezone(lon), "wind_speed_unit": "kmh",
    }, cache=True)
    return _to_daily(data["hourly"])


def _recent_hourly(lat: float, lon: float, start: date, end: date) -> dict:
    """Hourly data for recent and coming days.

    The forecast API is tried first. When it is rate-limited or down, the
    Historical Forecast API (a separate service and quota, which also carries
    the latest run's coming days) supplies the same variables.
    """
    global _forecast_down_until
    params = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
              "hourly": HOURLY, "timezone": standard_timezone(lon), "wind_speed_unit": "kmh"}
    if time.time() >= _forecast_down_until:
        try:
            return _get(FORECAST_URL, params, ttl=FORECAST_TTL)["hourly"]
        except (WeatherBusy, httpx.HTTPError):
            _forecast_down_until = time.time() + FORECAST_BACKOFF  # skip it for a while
    return _get(HISTORICAL_FORECAST_URL, params, ttl=FORECAST_TTL)["hourly"]


def _forecast(lat: float, lon: float, past_days: int, forecast_days: int) -> list[dict]:
    today = date.today()
    return _to_daily(_recent_hourly(lat, lon, today - timedelta(days=past_days),
                                    today + timedelta(days=forecast_days - 1)))


def hourly_weather(lat: float, lon: float, day: date, start_hour: int = 12, hours: int = 6) -> list[dict]:
    """Hour-by-hour weather on one day (local standard time), for fire spread.

    Returns hours start_hour .. start_hour + hours inclusive, each with
    temp, rh, wind (km/h), wind_dir (degrees, from) and rain (mm).
    """
    if day <= date.today() - timedelta(days=ARCHIVE_LAG_DAYS):
        h = _get(ARCHIVE_URL, {
            "latitude": lat, "longitude": lon, "start_date": day.isoformat(), "end_date": day.isoformat(),
            "hourly": HOURLY, "timezone": standard_timezone(lon), "wind_speed_unit": "kmh",
        }, cache=True)["hourly"]
    else:
        h = _recent_hourly(lat, lon, day, day)
    out = []
    for i, t in enumerate(h["time"]):
        hour = int(t[11:13])
        if start_hour <= hour <= start_hour + hours and h["temperature_2m"][i] is not None:
            out.append({"hour": hour, "temp": h["temperature_2m"][i], "rh": h["relative_humidity_2m"][i],
                        "wind": h["wind_speed_10m"][i], "wind_dir": h["wind_direction_10m"][i],
                        "rain": h["precipitation"][i] or 0.0})
    return out


TROPICS_LAT = 15.0
TROPICS_SPINUP_DAYS = 180
MIN_SPINUP_DAYS = 90


def season_start(day: date, lat: float = 46.0) -> date:
    """Start of the FWI spin-up (and of the "season" used for percentiles).

    Northern hemisphere: 1 March. Southern hemisphere: 1 September.
    Tropics, where seasons follow rain rather than winter: 180 days back.
    Early in a season the previous year's start is used, so there are always
    at least MIN_SPINUP_DAYS to build up the drought codes.
    """
    if abs(lat) < TROPICS_LAT:
        return day - timedelta(days=TROPICS_SPINUP_DAYS)
    month = 3 if lat > 0 else 9
    start = date(day.year if day.month >= month else day.year - 1, month, 1)
    if (day - start).days < MIN_SPINUP_DAYS:
        start = start.replace(year=start.year - 1)
    return start


def daily_weather(lat: float, lon: float, until: date, forecast_days: int = 0) -> list[dict]:
    """Noon observations from the start of the fire season up to `until`.

    If `until` is recent, the last days come from the forecast API, and
    `forecast_days` extra days into the future can be appended.
    """
    today = date.today()
    start = season_start(until, lat)
    archive_end = min(until, today - timedelta(days=ARCHIVE_LAG_DAYS))

    days = _archive(lat, lon, start, archive_end) if archive_end >= start else []

    if until > archive_end or forecast_days:
        recent = _forecast(lat, lon, past_days=ARCHIVE_LAG_DAYS + 1,
                           forecast_days=max(1, forecast_days + 1))
        known = {d["date"] for d in days}
        horizon = (until + timedelta(days=forecast_days)).isoformat()
        days += [d for d in recent if d["date"] not in known and d["date"] <= horizon]

    return days
