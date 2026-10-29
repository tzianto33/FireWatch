"""Worldwide behaviour that needs no network access."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from firewatch import fwi
from firewatch.regions import REGIONS
from firewatch.server import app
from firewatch.weather import season_start, standard_timezone

client = TestClient(app)


@pytest.mark.parametrize("lon,zone", [
    (-118.54, "Etc/GMT+8"),  # Los Angeles
    (-8.15, "Etc/GMT+1"),    # Portugal: solar noon about UTC-0:30, rounds to UTC-1
    (-0.42, "Etc/GMT"),      # Gironde
    (23.93, "Etc/GMT-2"),    # Attica
    (137.2, "Etc/GMT-9"),    # Kangaroo Island
    (179.9, "Etc/GMT-12"),
    (-179.9, "Etc/GMT+12"),
])
def test_standard_timezone(lon, zone):
    assert standard_timezone(lon) == zone


@pytest.mark.parametrize("day,lat,start", [
    ("2024-08-11", 38.2, "2024-03-01"),    # northern summer
    ("2017-06-17", 39.9, "2017-03-01"),    # 108 days in: still this season
    ("2025-03-22", 36.4, "2024-03-01"),    # early spring: too little spin-up, use last year
    ("2019-12-20", -35.8, "2019-09-01"),   # southern summer
    ("2024-02-02", -33.1, "2023-09-01"),
    ("2023-08-08", 10.0, "2023-02-09"),    # tropics: 180 days back
])
def test_season_start(day, lat, start):
    assert season_start(date.fromisoformat(day), lat).isoformat() == start


def test_southern_hemisphere_dries_in_january():
    """Same hot dry week: the drought code builds faster in the season's own summer."""
    week = [{"date": f"2025-01-{d:02d}", "temp": 32.0, "rh": 25.0, "wind": 20.0, "rain": 0.0}
            for d in range(1, 8)]
    south = fwi.compute_series(week, lat=-35.0)[-1]
    north = fwi.compute_series(week, lat=46.0)[-1]
    assert south.dc > north.dc
    assert south.dmc > north.dmc


def test_presets_have_fire_dates_on_six_continents():
    assert {r["continent"] for r in REGIONS.values()} == {
        "Europe", "North America", "South America", "Africa", "Asia", "Oceania"}
    for key, r in REGIONS.items():
        assert r["fire"] >= "2013-01-01", key
        assert -60 <= r["lat"] <= 75, key


def test_polar_point_rejected():
    assert client.get("/api/fwi?lat=80.0&lon=10.0&date=2024-08-11").status_code == 400


def test_point_needs_both_coordinates():
    assert client.get("/api/fwi?lat=40.0&date=2024-08-11").status_code == 400


def test_point_at_sea_rejected(monkeypatch):
    monkeypatch.setattr("firewatch.geocode.place_name", lambda lat, lon: ("0.00°N, 0.00°E", "", ""))
    resp = client.get("/api/fwi?lat=0.0&lon=0.0&date=2024-08-11")
    assert resp.status_code == 400
    assert "at sea" in resp.json()["detail"]


def test_search_requires_query():
    assert client.get("/api/search?q=a").status_code == 422
