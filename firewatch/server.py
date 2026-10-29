"""Web UI and JSON API.

Run with:
    uvicorn firewatch.server:app --port 8000

Every endpoint takes either a preset `region` key or any `lat`/`lon` on
land, anywhere in the world. Keys are re-read from .env on every model /
FIRMS call, so they can be added while the server is running.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from firewatch import agent, firms, fwi, geocode, spread, weather
from firewatch.regions import REGIONS

STATIC = Path(__file__).parent / "static"
REPLAY_DAYS = 60       # days before the target date available on the replay slider
BRIEFING_HISTORY = 7   # days of history sent to the model
MAX_FORECAST = 7
EARLIEST = date(2013, 1, 1)  # VIIRS fire archive starts in 2012
# Agent runs cost model tokens; on a public deployment cap them per visitor and per day.
BRIEFINGS_PER_HOUR = int(os.environ.get("BRIEFINGS_PER_HOUR", "6"))
BRIEFINGS_PER_DAY = int(os.environ.get("BRIEFINGS_PER_DAY", "300"))

app = FastAPI(title="FireWatch")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(weather.WeatherBusy)
def weather_busy(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Make browsers revalidate the page and its assets, so a redeploy shows up at once."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
_fires_cache: dict[tuple[float, float, str], dict] = {}
_spread_cache: dict[tuple, dict] = {}
_briefing_cache: dict[tuple, dict] = {}
_briefing_log: dict[str, deque] = {}
_briefing_day: list = [None, 0]
_briefing_lock = threading.Lock()


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def _allow_briefing(client: str) -> None:
    """Raise 429 when this visitor or the whole deployment has used its agent runs."""
    now = time.time()
    with _briefing_lock:
        today = date.today().isoformat()
        if _briefing_day[0] != today:
            _briefing_day[:] = [today, 0]
        if _briefing_day[1] >= BRIEFINGS_PER_DAY:
            raise HTTPException(429, "The daily agent budget of this demo is used up. Try again tomorrow.")
        log = _briefing_log.setdefault(client, deque())
        while log and now - log[0] > 3600:
            log.popleft()
        if len(log) >= BRIEFINGS_PER_HOUR:
            raise HTTPException(429, f"Limit of {BRIEFINGS_PER_HOUR} agent runs per hour reached. Try again later.")
        log.append(now)
        _briefing_day[1] += 1


def _location(region: str | None, lat: float | None, lon: float | None) -> dict:
    if region:
        if region not in REGIONS:
            raise HTTPException(404, f"Unknown region: {region}")
        return {"key": region, **REGIONS[region]}
    if lat is None or lon is None:
        raise HTTPException(400, "Give either region or lat and lon")
    if not (-60 <= lat <= 75 and -180 <= lon <= 180):
        raise HTTPException(400, "Latitude must be between 60°S and 75°N (no wildfire fuels beyond)")
    lat, lon = round(lat, 2), round(lon, 2)
    name, country, code = geocode.place_name(lat, lon)
    if code == "":
        raise HTTPException(400, "That point is at sea. Pick a point on land.")
    return {"key": None, "name": name, "country": country, "lat": lat, "lon": lon, "fire": None}


def _target(day: str, forecast: int) -> date:
    try:
        target = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    if not EARLIEST <= target <= date.today():
        raise HTTPException(400, f"date must be between {EARLIEST} and today")
    if not 0 <= forecast <= MAX_FORECAST:
        raise HTTPException(400, f"forecast must be 0-{MAX_FORECAST}")
    return target


def _label(loc: dict) -> str:
    return f"{loc['name']}, {loc['country']}" if loc["country"] else loc["name"]


@lru_cache(maxsize=512)
def _series(lat: float, lon: float, day: str, forecast: int) -> tuple[fwi.FWIDay, ...]:
    raw = weather.daily_weather(lat, lon, date.fromisoformat(day), forecast)
    return tuple(fwi.compute_series(raw, lat))


def _fires(lat: float, lon: float, day: str) -> dict:
    load_dotenv(override=True)
    key = (lat, lon, day)
    if key in _fires_cache:
        return _fires_cache[key]
    try:
        items = firms.active_fires(lat, lon, date.fromisoformat(day))
    except RuntimeError as exc:
        status = "no_key" if "FIRMS_MAP_KEY" in str(exc) else "error"
        return {"status": status, "summary": str(exc), "items": []}
    except Exception as exc:
        return {"status": "error", "summary": f"FIRMS request failed ({type(exc).__name__})", "items": []}
    result = {"status": "ok", "summary": firms.summarize(items), "items": items}
    _fires_cache[key] = result
    return result


def _day_json(d: fwi.FWIDay, target: str) -> dict:
    out = {k: round(v, 1) if isinstance(v, float) else v for k, v in asdict(d).items()}
    out.update(danger=d.danger, forecast=d.date > target)
    return out


@app.get("/")
def index() -> HTMLResponse:
    """The page, with asset URLs stamped by modification time so browsers never mix versions."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        version = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return HTMLResponse(html)


@app.get("/api/regions")
def regions() -> list[dict]:
    return [{"key": k, **v} for k, v in REGIONS.items()]


@app.get("/api/search")
def search(q: str = Query(min_length=2, max_length=120)) -> list[dict]:
    try:
        return geocode.search(q.strip())
    except Exception as exc:
        raise HTTPException(502, f"Place search failed: {exc}")


@app.get("/api/fwi")
def fwi_window(date_: str = Query(alias="date"), forecast: int = 0, region: str | None = None,
               lat: float | None = None, lon: float | None = None) -> dict:
    target = _target(date_, forecast)
    loc = _location(region, lat, lon)
    day = target.isoformat()
    series = _series(loc["lat"], loc["lon"], day, forecast)
    start = (target - timedelta(days=REPLAY_DAYS)).isoformat()
    days = [_day_json(d, day) for d in series if d.date >= start]
    if not days:
        raise HTTPException(502, "No weather data returned")
    return {"location": {**loc, "label": _label(loc)}, "target": day,
            "days": days, "fires": _fires(loc["lat"], loc["lon"], day)}


@app.get("/api/spread")
def spread_scenario(date_: str = Query(alias="date"), forecast: int = 0, region: str | None = None,
                    lat: float | None = None, lon: float | None = None,
                    ig_lat: float | None = None, ig_lon: float | None = None,
                    day: str | None = None, fuel: str = "pine") -> dict:
    """Potential spread from an ignition point, using one day of the location's FWI series."""
    if fuel not in spread.FUELS:
        raise HTTPException(400, f"fuel must be one of {sorted(spread.FUELS)}")
    target = _target(date_, forecast)
    loc = _location(region, lat, lon)
    series = _series(loc["lat"], loc["lon"], target.isoformat(), forecast)
    wanted = day or target.isoformat()
    match = next((d for d in series if d.date == wanted), None)
    if match is None:
        raise HTTPException(404, f"No fire weather for {wanted}")
    lat_i = round(ig_lat if ig_lat is not None else loc["lat"], 3)
    lon_i = round(ig_lon if ig_lon is not None else loc["lon"], 3)
    key = (loc["lat"], loc["lon"], target.isoformat(), forecast, wanted, lat_i, lon_i, fuel)
    if key not in _spread_cache:
        _spread_cache[key] = spread.scenario(match, lat_i, lon_i, fuel)
    return _spread_cache[key]


@app.get("/api/overview")
def overview(date_: str = Query(alias="date")) -> list[dict]:
    """Danger on one date at every preset hotspot, for the map markers."""
    day = _target(date_, 0).isoformat()

    def one(key: str) -> dict:
        r = REGIONS[key]
        try:
            d = next((x for x in _series(r["lat"], r["lon"], day, 0) if x.date == day), None)
        except Exception:
            d = None
        return {"key": key, **r, "fwi": round(d.fwi, 1) if d else None,
                "danger": d.danger if d else None}

    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(one, REGIONS))


class BriefingRequest(BaseModel):
    date: str
    forecast: int = 0
    lang: str = "en"
    region: str | None = None
    lat: float | None = None
    lon: float | None = None


@app.post("/api/briefing")
def make_briefing(req: BriefingRequest, request: Request) -> dict:
    target = _target(req.date, req.forecast)
    loc = _location(req.region, req.lat, req.lon)
    load_dotenv(override=True)
    day = target.isoformat()
    key = (loc["lat"], loc["lon"], day, req.forecast, req.lang)
    if key in _briefing_cache and day < date.today().isoformat():
        return _briefing_cache[key]  # past days don't change; reuse the answer
    _allow_briefing(_client(request))
    series = list(_series(loc["lat"], loc["lon"], day, req.forecast))
    idx = next((i for i, d in enumerate(series) if d.date == day), len(series) - 1)
    ctx = agent.Context(
        place=_label(loc), lat=loc["lat"], lon=loc["lon"], target=day, season=series,
        window=series[max(0, idx - BRIEFING_HISTORY + 1): idx + 1 + req.forecast],
        fires=_fires(loc["lat"], loc["lon"], day), language=req.lang)
    try:
        result = agent.run(ctx)
    except Exception as exc:
        raise HTTPException(503, f"Nemotron briefing unavailable: {exc}")
    _briefing_cache[key] = asdict(result)
    return _briefing_cache[key]


@lru_cache(maxsize=8)
def _validation() -> dict:
    """FWI on the start date of every preset historic fire, ranked within its own season."""

    def one(key: str) -> dict:
        r = REGIONS[key]
        try:
            ctx = agent.season_context(list(_series(r["lat"], r["lon"], r["fire"], 0)), r["fire"])
        except Exception as exc:
            return {"key": key, "name": r["name"], "country": r["country"], "date": r["fire"], "error": str(exc)}
        return {"key": key, "name": r["name"], "country": r["country"], "date": r["fire"],
                "fwi": ctx["target_fwi"], "danger": fwi.danger_class(ctx["target_fwi"]),
                "percentile": ctx["percentile"]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [row for row in pool.map(one, [k for k, r in REGIONS.items() if r["fire"]])]
    ok = [row for row in rows if "error" not in row]
    return {
        "fires": sorted(rows, key=lambda x: x["date"]),
        "total": len(ok),
        "top_decile": sum(row["percentile"] >= 90 for row in ok),
        "high_or_worse": sum(row["fwi"] >= 21.3 for row in ok),
        "very_high_or_worse": sum(row["fwi"] >= 38 for row in ok),
        "median_percentile": sorted(row["percentile"] for row in ok)[len(ok) // 2] if ok else None,
    }


@app.get("/api/validation")
def validation() -> dict:
    result = _validation()
    if result["total"] < len(result["fires"]):
        _validation.cache_clear()  # retry failed lookups next time
    return result
