"""Fire spread from a point ignition: terrain-aware, hour-by-hour.

Rates of spread come from the Canadian Forest Fire Behavior Prediction (FBP)
System, the companion of the FWI System (Forestry Canada 1992, ST-X-3;
Wotton et al. 2009). The perimeter grows by Huygens' principle, the method
used by FARSITE and Prometheus: every vertex expands as a small elliptical
wavelet shaped by its own conditions.

    fine fuel moisture   hourly FFMC (Van Wagner 1977), from the day's FFMC at noon
    wind                 hourly speed and direction
    slope                FBP slope factor, turned into an equivalent wind and
                         added to the real wind as a vector
    rate of spread       RSI = a (1 - e^(-b ISI))^c, times the buildup effect
    shape                length-to-breadth ratio from the net wind
    growth               point-ignition acceleration, alpha = 0.115 / min
    water                vertices do not advance into the sea

Not modelled: fuel variation (one fuel type everywhere), spotting, crowning
transitions, suppression. Results show potential spread for situational
awareness, not an operational prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.validation import make_valid

from firewatch.fwi import FWIDay

M_PER_DEG_LAT = 111_320.0
ACCELERATION = 0.115          # 1/min, point ignition
GRASS_CURING = 90.0           # % cured, typical of a dry summer
DT_MIN = 5.0                  # time step of the perimeter growth
FRAME_EVERY_MIN = 15          # snapshots for the time-lapse
HOURS = (1, 3, 6)
START_HOUR = 12               # ignition at local noon, the start of the burning period
MAX_VERTICES = 360
MAX_SLOPE_PCT = 70.0          # FBP caps the slope factor here


@dataclass(frozen=True)
class Fuel:
    label: str
    fbp: str
    a: float
    b: float
    c: float
    q: float | None = None    # buildup effect parameters (None: no BUI effect)
    bui0: float | None = None
    grass: bool = False


FUELS = {
    "pine": Fuel("Pine forest", "C-3", 110, 0.0444, 3.0, 0.75, 62),
    "boreal": Fuel("Boreal spruce", "C-2", 110, 0.0282, 1.5, 0.70, 64),
    "grass": Fuel("Grassland", "O-1b", 250, 0.035, 1.7, grass=True),
}


# ---------- FBP building blocks ----------

def _curing_factor() -> float:
    c = GRASS_CURING
    return 0.005 * (math.exp(0.061 * c) - 1) if c < 58.8 else 0.176 + 0.02 * (c - 58.8)


def _rsi(fuel: Fuel, isi: float) -> float:
    """Rate of spread (m/min) before the buildup effect."""
    ros = fuel.a * (1 - math.exp(-fuel.b * isi)) ** fuel.c
    return ros * _curing_factor() if fuel.grass else ros


def _isi_for_ros(fuel: Fuel, ros: float) -> float:
    """Inverse of _rsi: the ISI that gives this rate of spread."""
    if fuel.grass:
        ros /= _curing_factor()
    ratio = min(ros / fuel.a, 0.999)
    if ratio <= 0:
        return 0.0
    return -math.log(1 - ratio ** (1 / fuel.c)) / fuel.b


def _buildup_effect(fuel: Fuel, bui: float) -> float:
    if fuel.q is None or bui <= 0:
        return 1.0
    return math.exp(50 * math.log(fuel.q) * (1 / bui - 1 / fuel.bui0))


def length_to_breadth(fuel: Fuel, wind_kmh: float) -> float:
    if fuel.grass:
        return 1.1 * wind_kmh ** 0.464 if wind_kmh >= 1 else 1.0
    return 1.0 + 8.729 * (1 - math.exp(-0.030 * wind_kmh)) ** 2.155


def _fine_fuel_function(ffmc: float) -> float:
    m = 147.27723 * (101 - ffmc) / (59.5 + ffmc)
    return 91.9 * math.exp(-0.1386 * m) * (1 + m ** 5.31 / 4.93e7)


def hourly_ffmc(ffmc_prev: float, temp: float, rh: float, wind: float, rain: float) -> float:
    """One-hour step of the Fine Fuel Moisture Code (Van Wagner 1977)."""
    rh = min(rh, 100.0)
    mo = 147.27723 * (101 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0:
        rf = rain
        wet = 42.5 * rf * math.exp(-100 / (251 - mo)) * (1 - math.exp(-6.93 / rf))
        mo = min(mo + wet + (0.0015 * (mo - 150) ** 2 * math.sqrt(rf) if mo > 150 else 0), 250)
    ed = 0.942 * rh ** 0.679 + 11 * math.exp((rh - 100) / 10) + 0.18 * (21.1 - temp) * (1 - math.exp(-0.115 * rh))
    ew = 0.618 * rh ** 0.753 + 10 * math.exp((rh - 100) / 10) + 0.18 * (21.1 - temp) * (1 - math.exp(-0.115 * rh))
    if mo > ed:
        k = (0.424 * (1 - (rh / 100) ** 1.7) + 0.0694 * math.sqrt(wind) * (1 - (rh / 100) ** 8)) * 0.0579 * math.exp(0.0365 * temp)
        m = ed + (mo - ed) * math.exp(-2.303 * k)
    elif mo < ew:
        k = (0.424 * (1 - ((100 - rh) / 100) ** 1.7) + 0.0694 * math.sqrt(wind) * (1 - ((100 - rh) / 100) ** 8)) * 0.0579 * math.exp(0.0365 * temp)
        m = ew - (ew - mo) * math.exp(-2.303 * k)
    else:
        m = mo
    return max(0.0, min(101.0, 59.5 * (250 - m) / (147.27723 + m)))


@dataclass
class Hour:
    ffmc: float
    wind: float       # km/h
    wind_from: float  # degrees


def _hours(day: FWIDay, hourly: list[dict] | None, n_hours: int) -> list[Hour]:
    """Fire weather for each hour after ignition (index 0 = ignition hour)."""
    if not hourly:
        return [Hour(day.ffmc, day.wind, day.wind_dir or 0.0)] * (n_hours + 1)
    out, ffmc = [], day.ffmc
    for k, h in enumerate(hourly[:n_hours + 1]):
        if k > 0:
            ffmc = hourly_ffmc(ffmc, h["temp"], h["rh"], h["wind"], h["rain"])
        out.append(Hour(ffmc, h["wind"], h["wind_dir"] if h["wind_dir"] is not None else 0.0))
    while len(out) < n_hours + 1:
        out.append(out[-1])
    return out


@dataclass
class Behaviour:
    head: float       # m/min, equilibrium
    back: float       # m/min
    lb: float
    azimuth: float    # direction of maximum spread, degrees clockwise from north


def behaviour(fuel: Fuel, be: float, hour: Hour, gx: float, gy: float) -> Behaviour:
    """Head/back rate of spread and shape from wind and slope (FBP vector method)."""
    isz = 0.208 * _fine_fuel_function(hour.ffmc)   # ISI with no wind
    # Slope: the extra spread uphill is expressed as an equivalent wind speed.
    slope_pct = min(100 * math.hypot(gx, gy), MAX_SLOPE_PCT)
    wse = 0.0
    if slope_pct > 0 and isz > 0:
        sf = math.exp(3.533 * (slope_pct / 100) ** 1.2)
        isf = _isi_for_ros(fuel, _rsi(fuel, isz) * sf)
        if isf > isz:
            wse = min(math.log(isf / isz) / 0.05039, 80.0)
    downwind = math.radians(hour.wind_from + 180)
    vx = hour.wind * math.sin(downwind)
    vy = hour.wind * math.cos(downwind)
    if wse > 0:
        g = math.hypot(gx, gy)
        vx += wse * gx / g      # uphill = direction of increasing elevation
        vy += wse * gy / g
    wsv = math.hypot(vx, vy)
    azimuth = math.degrees(math.atan2(vx, vy)) % 360 if wsv > 0 else 0.0
    head = _rsi(fuel, isz * math.exp(0.05039 * wsv)) * be
    back = _rsi(fuel, isz * math.exp(-0.05039 * wsv)) * be
    return Behaviour(head, back, length_to_breadth(fuel, wsv), azimuth)


# ---------- perimeter growth ----------

def _resample(poly: Polygon, spacing: float) -> list[tuple[float, float]]:
    ring = poly.exterior
    n = int(min(MAX_VERTICES, max(24, ring.length / spacing)))
    return [ring.interpolate(k / n, normalized=True).coords[0] for k in range(n)]


def _clean(points: list[tuple[float, float]]) -> Polygon:
    """Valid, counter-clockwise polygon; loops from crossing wavelets are dropped."""
    poly = Polygon(points)
    if not poly.is_valid:
        fixed = make_valid(poly)
        parts = [g for g in getattr(fixed, "geoms", [fixed]) if isinstance(g, Polygon)]
        poly = max(parts, key=lambda g: g.area) if parts else poly.buffer(0)
    return orient(Polygon(poly.exterior), 1.0)


def grow(fuel: Fuel, be: float, hours: list[Hour], terrain, minutes: float) -> list[tuple[int, Polygon]]:
    """Grow the perimeter for `minutes`; returns (minute, polygon) snapshots."""
    pts = [(5 * math.cos(2 * math.pi * k / 24), 5 * math.sin(2 * math.pi * k / 24)) for k in range(24)]
    snapshots = []
    t = 0.0
    while t < minutes - 1e-9:
        dt = min(DT_MIN, minutes - t)
        hour = hours[min(int(t // 60), len(hours) - 1)]
        accel = 1 - math.exp(-ACCELERATION * (t + dt / 2))
        n = len(pts)
        new = []
        for i in range(n):
            x, y = pts[i]
            if terrain.is_water(x, y):
                new.append((x, y))
                continue
            (xp, yp), (xn, yn) = pts[i - 1], pts[(i + 1) % n]
            tx, ty = xn - xp, yn - yp
            tlen = math.hypot(tx, ty) or 1.0
            nx, ny = ty / tlen, -tx / tlen          # outward normal of a counter-clockwise ring
            gx, gy = terrain.gradient(x, y)
            b_ = behaviour(fuel, be, hour, gx, gy)
            head, back = b_.head * accel * dt, b_.back * accel * dt
            semi_major = (head + back) / 2
            semi_minor = semi_major / b_.lb
            offset = (head - back) / 2
            az = math.radians(b_.azimuth)
            ux, uy = math.sin(az), math.cos(az)     # along the spread direction
            vx, vy = math.cos(az), -math.sin(az)    # across it
            nu, nv = nx * ux + ny * uy, nx * vx + ny * vy
            s = math.hypot(semi_major * nu, semi_minor * nv) or 1.0
            # Point of the wavelet ellipse farthest along the normal (Huygens envelope).
            dx = offset * ux + (semi_major ** 2 * nu * ux + semi_minor ** 2 * nv * vx) / s
            dy = offset * uy + (semi_major ** 2 * nu * uy + semi_minor ** 2 * nv * vy) / s
            nxp, nyp = x + dx, y + dy
            new.append((x, y) if terrain.is_water(nxp, nyp) else (nxp, nyp))
        t += dt
        poly = _clean(new)
        pts = _resample(poly, spacing=max(poly.exterior.length / MAX_VERTICES, 5.0))
        if round(t) % FRAME_EVERY_MIN == 0:
            snapshots.append((round(t), poly))
    return snapshots


# ---------- public API ----------

def compass(deg: float) -> str:
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[round(deg / 45) % 8]


def _to_latlon(poly: Polygon, lat: float, lon: float) -> list[list[float]]:
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
    return [[round(lat + y / M_PER_DEG_LAT, 5), round(lon + x / m_per_deg_lon, 5)]
            for x, y in poly.simplify(2.0).exterior.coords]


def equilibrium_head_ros(day: FWIDay, fuel_key: str = "pine") -> float:
    """Flat-ground head rate of spread at noon (m/min), for sizing the terrain grid."""
    fuel = FUELS[fuel_key]
    hour = Hour(day.ffmc, day.wind, day.wind_dir or 0.0)
    return behaviour(fuel, _buildup_effect(fuel, day.bui), hour, 0.0, 0.0).head


def simulate(day: FWIDay, lat: float, lon: float, fuel_key: str = "pine", hours: tuple[int, ...] = HOURS,
             hourly: list[dict] | None = None, terrain=None) -> dict:
    from firewatch.terrain import FlatTerrain

    terrain = terrain or FlatTerrain()
    fuel = FUELS[fuel_key]
    be = _buildup_effect(fuel, day.bui)
    weather = _hours(day, hourly, max(hours))
    start = behaviour(fuel, be, weather[0], *terrain.gradient(0.0, 0.0))
    snapshots = grow(fuel, be, weather, terrain, 60 * max(hours))

    def stats(poly: Polygon) -> tuple[float, float]:
        far = max(poly.exterior.coords, key=lambda p: math.hypot(*p))
        return math.hypot(*far), math.degrees(math.atan2(far[0], far[1])) % 360

    rings = []
    for h in hours:
        poly = next(p for m, p in snapshots if m == 60 * h)
        dist, _ = stats(poly)
        rings.append({"hours": h, "head_km": round(dist / 1000, 2), "area_ha": round(poly.area / 10_000),
                      "polygon": _to_latlon(poly, lat, lon)})
    _, toward = stats(snapshots[-1][1])
    first, last = weather[0], weather[max(hours)]

    return {
        "date": day.date,
        "ignition": [lat, lon],
        "start_hour": START_HOUR,
        "fuel": {"key": fuel_key, "label": fuel.label, "fbp": fuel.fbp},
        "wind_kmh": round(first.wind),
        "wind_from_deg": round(first.wind_from),
        "wind_from": compass(first.wind_from),
        "wind_end": {"kmh": round(last.wind), "from": compass(last.wind_from)},
        "spread_toward": compass(toward),
        "head_ros_m_min": round(start.head, 1),
        "length_to_breadth": round(start.lb, 2),
        "hourly_weather": bool(hourly),
        "terrain": terrain.source,
        "rings": rings,
        "frames": [{"minutes": m, "area_ha": round(p.area / 10_000), "polygon": _to_latlon(p, lat, lon)}
                   for m, p in snapshots],
        "assumptions": f"{fuel.label} (FBP {fuel.fbp}) everywhere; {terrain.source}; "
                       f"{'hourly' if hourly else 'noon'} wind and fuel moisture; no spotting or suppression. "
                       "Potential spread for awareness only, not an operational prediction.",
    }


def scenario(day: FWIDay, lat: float, lon: float, fuel_key: str = "pine") -> dict:
    """simulate() with real hourly weather and terrain, falling back to flat / noon values."""
    from datetime import date

    from firewatch import weather
    from firewatch.terrain import GridTerrain

    try:
        # Weather barely changes within a kilometre: rounding lets nearby ignitions share the download.
        hourly = weather.hourly_weather(round(lat, 2), round(lon, 2), date.fromisoformat(day.date),
                                        START_HOUR, max(HOURS))
    except Exception:
        hourly = None
    reach = equilibrium_head_ros(day, fuel_key) * 60 * max(HOURS) * 1.2 + 1000
    try:
        terrain = GridTerrain.around(lat, lon, reach)
    except Exception:
        terrain = None
    return simulate(day, lat, lon, fuel_key, hourly=hourly, terrain=terrain)
