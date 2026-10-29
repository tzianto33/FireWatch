"""API checks that need no network access."""

from fastapi.testclient import TestClient

from firewatch.regions import REGIONS
from firewatch.server import app

client = TestClient(app)


def test_index_served():
    assert "FireWatch" in client.get("/").text


def test_regions():
    keys = {r["key"] for r in client.get("/api/regions").json()}
    assert keys == set(REGIONS)


def test_unknown_region():
    assert client.get("/api/fwi?region=atlantis&date=2024-08-11").status_code == 404


def test_bad_dates():
    assert client.get("/api/fwi?region=varnavas&date=11-08-2024").status_code == 400
    assert client.get("/api/fwi?region=varnavas&date=2099-01-01").status_code == 400
    assert client.get("/api/fwi?region=varnavas&date=2024-08-11&forecast=30").status_code == 400


def test_briefing_rate_limit(monkeypatch):
    from fastapi import HTTPException
    from firewatch import server
    monkeypatch.setattr(server, "BRIEFINGS_PER_HOUR", 2)
    monkeypatch.setattr(server, "_briefing_log", {})
    server._allow_briefing("1.2.3.4")
    server._allow_briefing("1.2.3.4")
    server._allow_briefing("5.6.7.8")       # other visitors keep their own budget
    try:
        server._allow_briefing("1.2.3.4")
    except HTTPException as exc:
        assert exc.status_code == 429
    else:
        raise AssertionError("third run in an hour should be refused")
