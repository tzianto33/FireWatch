"""Canadian Forest Fire Weather Index (FWI) System.

Implements the equations of Van Wagner (1987), "Development and Structure of
the Canadian Forest Fire Weather Index System", Forestry Technical Report 35.
The same system is used by the European Forest Fire Information System (EFFIS).

Inputs are noon (local standard time) observations:
    temp  - air temperature, °C
    rh    - relative humidity, %
    wind  - 10 m wind speed, km/h
    rain  - 24 h accumulated precipitation, mm
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Day-length adjustment factors by latitude band, as in the cffdrs reference
# implementation. The original tables are for ~46°N; the others extend the
# system to lower latitudes and the southern hemisphere.
_DMC_DAY_LENGTH = {
    "north": [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0],     # lat > 33
    "north_low": [7.9, 8.4, 8.9, 9.5, 9.9, 10.2, 10.1, 9.7, 9.1, 8.6, 8.1, 7.8],   # 0 < lat <= 33
    "south_low": [10.1, 9.6, 9.1, 8.5, 8.1, 7.8, 7.9, 8.3, 8.9, 9.4, 9.9, 10.2],   # -30 < lat <= 0
    "south": [11.5, 10.5, 9.2, 7.9, 6.8, 6.2, 6.5, 7.4, 8.7, 10.0, 11.2, 11.8],    # lat <= -30
}
_DC_DAY_LENGTH = {
    "north": [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6],   # lat > 20
    "south": [6.4, 5.0, 2.4, 0.4, -1.6, -1.6, -1.6, -1.6, -1.6, 0.9, 3.8, 5.8],   # lat <= -20
}
_DC_EQUATORIAL = 1.4                                                               # -20 < lat <= 20
DEFAULT_LAT = 46.0


def _dmc_factor(month: int, lat: float) -> float:
    band = ("north" if lat > 33 else "north_low" if lat > 0
            else "south_low" if lat > -30 else "south")
    return _DMC_DAY_LENGTH[band][month - 1]


def _dc_factor(month: int, lat: float) -> float:
    if lat > 20:
        return _DC_DAY_LENGTH["north"][month - 1]
    if lat <= -20:
        return _DC_DAY_LENGTH["south"][month - 1]
    return _DC_EQUATORIAL

# Standard start-up values at the beginning of the fire season.
FFMC_START = 85.0
DMC_START = 6.0
DC_START = 15.0

# EFFIS danger classes: (upper bound of FWI, label).
DANGER_CLASSES = [
    (5.2, "Very low"),
    (11.2, "Low"),
    (21.3, "Moderate"),
    (38.0, "High"),
    (50.0, "Very high"),
    (math.inf, "Extreme"),
]


def ffmc(temp: float, rh: float, wind: float, rain: float, ffmc_prev: float) -> float:
    """Fine Fuel Moisture Code: moisture of litter and fine fuels (ignition ease)."""
    rh = min(rh, 100.0)
    mo = 147.2 * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)

    if rain > 0.5:
        rf = rain - 0.5
        wetting = 42.5 * rf * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rf))
        if mo > 150.0:
            mo += wetting + 0.0015 * (mo - 150.0) ** 2 * math.sqrt(rf)
        else:
            mo += wetting
        mo = min(mo, 250.0)

    ed = (0.942 * rh ** 0.679 + 11.0 * math.exp((rh - 100.0) / 10.0)
          + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh)))
    if mo > ed:
        ko = (0.424 * (1.0 - (rh / 100.0) ** 1.7)
              + 0.0694 * math.sqrt(wind) * (1.0 - (rh / 100.0) ** 8))
        kd = ko * 0.581 * math.exp(0.0365 * temp)
        m = ed + (mo - ed) * 10.0 ** (-kd)
    else:
        ew = (0.618 * rh ** 0.753 + 10.0 * math.exp((rh - 100.0) / 10.0)
              + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh)))
        if mo < ew:
            k1 = (0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7)
                  + 0.0694 * math.sqrt(wind) * (1.0 - ((100.0 - rh) / 100.0) ** 8))
            kw = k1 * 0.581 * math.exp(0.0365 * temp)
            m = ew - (ew - mo) * 10.0 ** (-kw)
        else:
            m = mo

    return max(0.0, min(101.0, 59.5 * (250.0 - m) / (147.2 + m)))


def dmc(temp: float, rh: float, rain: float, dmc_prev: float, month: int,
        lat: float = DEFAULT_LAT) -> float:
    """Duff Moisture Code: moisture of loosely compacted organic layers."""
    rh = min(rh, 100.0)
    temp = max(temp, -1.1)
    rk = 1.894 * (temp + 1.1) * (100.0 - rh) * _dmc_factor(month, lat) * 1e-6

    po = dmc_prev
    if rain > 1.5:
        re = 0.92 * rain - 1.27
        mo = 20.0 + math.exp(5.6348 - po / 43.43)
        if po <= 33.0:
            b = 100.0 / (0.5 + 0.3 * po)
        elif po <= 65.0:
            b = 14.0 - 1.3 * math.log(po)
        else:
            b = 6.2 * math.log(po) - 17.2
        mr = mo + 1000.0 * re / (48.77 + b * re)
        po = max(244.72 - 43.43 * math.log(mr - 20.0), 0.0)

    return max(0.0, po + 100.0 * rk)


def dc(temp: float, rain: float, dc_prev: float, month: int, lat: float = DEFAULT_LAT) -> float:
    """Drought Code: moisture of deep, compact organic layers (long-term drought)."""
    temp = max(temp, -2.8)
    pe = max((0.36 * (temp + 2.8) + _dc_factor(month, lat)) / 2.0, 0.0)

    do = dc_prev
    if rain > 2.8:
        rd = 0.83 * rain - 1.27
        qo = 800.0 * math.exp(-do / 400.0)
        qr = qo + 3.937 * rd
        do = max(400.0 * math.log(800.0 / qr), 0.0)

    return max(0.0, do + pe)


def isi(wind: float, ffmc_today: float) -> float:
    """Initial Spread Index: expected rate of fire spread."""
    mo = 147.2 * (101.0 - ffmc_today) / (59.5 + ffmc_today)
    ff = 19.115 * math.exp(-0.1386 * mo) * (1.0 + mo ** 5.31 / 4.93e7)
    return ff * math.exp(0.05039 * wind)


def bui(dmc_today: float, dc_today: float) -> float:
    """Buildup Index: total fuel available for combustion."""
    if dmc_today == 0.0 and dc_today == 0.0:
        return 0.0
    if dmc_today <= 0.4 * dc_today:
        u = 0.8 * dmc_today * dc_today / (dmc_today + 0.4 * dc_today)
    else:
        u = dmc_today - (1.0 - 0.8 * dc_today / (dmc_today + 0.4 * dc_today)) * (
            0.92 + (0.0114 * dmc_today) ** 1.7)
    return max(u, 0.0)


def fwi(isi_today: float, bui_today: float) -> float:
    """Fire Weather Index: overall fire intensity / danger rating."""
    if bui_today <= 80.0:
        fd = 0.626 * bui_today ** 0.809 + 2.0
    else:
        fd = 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui_today))
    b = 0.1 * isi_today * fd
    if b > 1.0:
        return math.exp(2.72 * (0.434 * math.log(b)) ** 0.647)
    return b


def danger_class(fwi_value: float) -> str:
    for upper, label in DANGER_CLASSES:
        if fwi_value < upper:
            return label
    return DANGER_CLASSES[-1][1]


@dataclass
class FWIDay:
    date: str
    temp: float
    rh: float
    wind: float
    rain: float
    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    wind_dir: float | None = None  # degrees the wind blows FROM (0 = north)

    @property
    def danger(self) -> str:
        return danger_class(self.fwi)


def compute_series(days: list[dict], lat: float = DEFAULT_LAT) -> list[FWIDay]:
    """Run the FWI system day by day.

    `days` is a chronological list of dicts with keys
    date (YYYY-MM-DD), temp, rh, wind, rain. The moisture codes carry over
    from one day to the next, so the series should start well before the
    date of interest (spin-up) to be meaningful. `lat` selects the
    day-length factors.
    """
    f, p, d = FFMC_START, DMC_START, DC_START
    out: list[FWIDay] = []
    for day in days:
        month = int(day["date"][5:7])
        f = ffmc(day["temp"], day["rh"], day["wind"], day["rain"], f)
        p = dmc(day["temp"], day["rh"], day["rain"], p, month, lat)
        d = dc(day["temp"], day["rain"], d, month, lat)
        i = isi(day["wind"], f)
        u = bui(p, d)
        out.append(FWIDay(day["date"], day["temp"], day["rh"], day["wind"], day["rain"],
                          f, p, d, i, u, fwi(i, u), day.get("wind_dir")))
    return out
