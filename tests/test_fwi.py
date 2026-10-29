"""Checks against the reference example of the Canadian FWI system
(first day of the standard test data set used by Van Wagner / cffdrs)."""

import pytest

from firewatch import fwi


def test_reference_day():
    days = [{"date": "2026-04-13", "temp": 17.0, "rh": 42.0, "wind": 25.0, "rain": 0.0}]
    day = fwi.compute_series(days)[0]
    assert day.ffmc == pytest.approx(87.69, abs=0.05)
    assert day.dmc == pytest.approx(8.55, abs=0.05)
    assert day.dc == pytest.approx(19.01, abs=0.05)
    assert day.isi == pytest.approx(10.85, abs=0.05)
    assert day.bui == pytest.approx(8.49, abs=0.05)
    assert day.fwi == pytest.approx(10.10, abs=0.05)


def test_rain_lowers_danger():
    dry = {"date": "2026-07-01", "temp": 35.0, "rh": 20.0, "wind": 30.0, "rain": 0.0}
    wet = dict(dry, rain=20.0, rh=80.0)
    series = fwi.compute_series([dry] * 30 + [wet])
    assert series[-1].fwi < series[-2].fwi


@pytest.mark.parametrize("value,label", [
    (0, "Very low"), (6, "Low"), (15, "Moderate"),
    (30, "High"), (45, "Very high"), (70, "Extreme"),
])
def test_danger_classes(value, label):
    assert fwi.danger_class(value) == label
