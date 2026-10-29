"""FBP spread model: reference values, Huygens growth, slope, water and hourly weather."""

import math

import pytest

from firewatch import spread
from firewatch.fwi import FWIDay
from firewatch.terrain import GridTerrain


def _day(ffmc=92.2, bui=290.0, wind=27.0, wind_dir=0.0):
    return FWIDay("2024-08-11", 30.0, 32.0, wind, 0.0, ffmc, 246.5, 882.8, 22.7, bui, 66.4, wind_dir)


def _terrain(elev_fn, half=6000.0, n=41):
    step = 2 * half / (n - 1)
    elev = [[elev_fn(-half + i * step, -half + j * step) for i in range(n)] for j in range(n)]
    return GridTerrain(38.0, 24.0, half, n, elev)


def _bounds(res, hours):
    ring = next(r for r in res["rings"] if r["hours"] == hours)
    lats = [p[0] for p in ring["polygon"]]
    return min(lats), max(lats)


def test_reference_rate_of_spread_curve():
    # C-2 at ISI 10: 110 (1 - e^-0.282)^1.5 ≈ 13.4 m/min
    assert spread._rsi(spread.FUELS["boreal"], 10.0) == pytest.approx(13.4, abs=0.1)


def test_isi_inverse_roundtrip():
    fuel = spread.FUELS["pine"]
    assert spread._isi_for_ros(fuel, spread._rsi(fuel, 15.0)) == pytest.approx(15.0, rel=1e-3)


def test_flat_constant_wind_matches_analytical_ellipse():
    """On level ground with constant weather the Huygens perimeter must reproduce the FBP ellipse."""
    res = spread.simulate(_day(), 38.0, 24.0, "pine", hours=(1, 3))
    ros = res["head_ros_m_min"]
    a = spread.ACCELERATION
    for ring in res["rings"]:
        t = 60 * ring["hours"]
        analytic_km = ros * (t + (math.exp(-a * t) - 1) / a) / 1000
        assert ring["head_km"] == pytest.approx(analytic_km, rel=0.04)


def test_fire_spreads_downwind():
    res = spread.simulate(_day(wind_dir=0.0), 38.0, 24.0, "pine", hours=(1, 3))
    south, north = _bounds(res, 3)
    assert abs(south - 38.0) > 5 * abs(north - 38.0)      # wind from N: runs south
    assert res["wind_from"] == "N" and res["spread_toward"] == "S"


def test_fire_runs_uphill_without_wind():
    hill = _terrain(lambda x, y: 1000 + 0.3 * y)              # 30 % slope rising northwards
    res = spread.simulate(_day(wind=0.0), 38.0, 24.0, "pine", hours=(1,), terrain=hill)
    south, north = _bounds(res, 1)
    assert north - 38.0 > 2 * (38.0 - south)


def test_fire_stops_at_the_sea():
    coast = _terrain(lambda x, y: 100.0 if y > -800 else 0.0)  # sea starts 800 m south
    res = spread.simulate(_day(wind_dir=0.0), 38.0, 24.0, "pine", hours=(1, 3), terrain=coast)
    south, _ = _bounds(res, 3)
    grid_step_deg = (2 * 6000 / 40) / spread.M_PER_DEG_LAT
    assert (38.0 - south) * spread.M_PER_DEG_LAT < 800 + grid_step_deg * spread.M_PER_DEG_LAT


def test_wind_shift_bends_the_fire():
    """Wind turning from north to west mid-run pushes the fire east as well as south."""
    hourly = [{"hour": 12 + h, "temp": 30, "rh": 30, "wind": 27, "rain": 0,
               "wind_dir": 0 if h < 2 else 270} for h in range(5)]
    steady = spread.simulate(_day(), 38.0, 24.0, "pine", hours=(4,))
    turning = spread.simulate(_day(), 38.0, 24.0, "pine", hours=(4,), hourly=hourly)
    east = lambda res: max(p[1] for p in res["rings"][0]["polygon"])
    assert east(turning) > east(steady) + 0.01


def test_hourly_ffmc_dries_in_afternoon_and_wets_in_rain():
    assert spread.hourly_ffmc(88.0, 32, 20, 20, 0.0) > 88.0
    assert spread.hourly_ffmc(92.0, 18, 95, 5, 5.0) < 80.0


def test_area_grows_and_frames_cover_the_run():
    res = spread.simulate(_day(), 38.0, 24.0, "grass")
    areas = [r["area_ha"] for r in res["rings"]]
    assert areas == sorted(areas) and areas[0] > 0
    assert res["frames"][-1]["minutes"] == 360 and len(res["frames"]) == 24
