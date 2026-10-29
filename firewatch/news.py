"""Wildfire news and official announcements via the Tavily Search API.

For historical replays the search is bounded to the days around the target
date, so the agent sees what was actually reported at the time.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

SEARCH_URL = "https://api.tavily.com/search"
DAYS_BEFORE = 3
DAYS_AFTER = 7


def search(query: str, around: date, max_results: int = 5) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set.")

    end = min(around + timedelta(days=DAYS_AFTER), date.today())
    resp = httpx.post(SEARCH_URL, timeout=30, headers={"Authorization": f"Bearer {key}"}, json={
        "query": query,
        "topic": "news",
        "start_date": (around - timedelta(days=DAYS_BEFORE)).isoformat(),
        "end_date": end.isoformat(),
        "max_results": max_results,
        "search_depth": "basic",
    })
    resp.raise_for_status()
    return [{
        "title": r.get("title", ""),
        "url": r.get("url", ""),
        "content": (r.get("content") or "")[:600],
        "published_date": r.get("published_date"),
    } for r in resp.json().get("results", [])]


def default_query(place: str, day: date) -> str:
    return f"wildfire {place} {day.strftime('%B %Y')}"
