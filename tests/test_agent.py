"""Agent loop against a scripted fake of the OpenAI-compatible client (no network)."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import BadRequestError

from firewatch import agent, fwi, spread


@pytest.fixture(autouse=True)
def offline_spread(monkeypatch):
    """The spread tool normally fetches hourly weather and terrain; use flat, noon values."""
    monkeypatch.setattr(spread, "scenario", lambda day, lat, lon, fuel: spread.simulate(day, lat, lon, fuel))


def _season():
    days = [{"date": f"2024-08-{d:02d}", "temp": 30.0, "rh": 30.0, "wind": 10.0 + d, "rain": 0.0}
            for d in range(1, 12)]
    return fwi.compute_series(days)


def _ctx():
    season = _season()
    return agent.Context(place="Varnavas, Greece", lat=38.22, lon=23.93, target="2024-08-11",
                         season=season, window=season[-7:],
                         fires={"status": "no_key", "summary": "FIRMS_MAP_KEY is not set.", "items": []})


def _tool_call(i, name, args):
    call = NS(id=f"call_{i}", type="function", function=NS(name=name, arguments=json.dumps(args)))
    call.model_dump = lambda: {"id": call.id, "type": "function",
                               "function": {"name": name, "arguments": json.dumps(args)}}
    return call


class FakeClient:
    """Replays a script of assistant messages; records every request."""

    def __init__(self, script, reject_tools=False):
        self.api_key = "fake"
        self.script = list(script)
        self.reject_tools = reject_tools
        self.requests = []
        self.models = NS(list=lambda: [NS(id="nvidia/nemotron-ultra"), NS(id="nvidia/nemotron-nano")])
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.reject_tools and "tools" in kwargs:
            raise BadRequestError("tools not supported", body=None,
                                  response=httpx.Response(400, request=httpx.Request("POST", "http://x")))
        msg = self.script.pop(0)
        return NS(choices=[NS(message=msg)])


def test_agent_calls_tools_then_writes_briefing(monkeypatch):
    monkeypatch.delenv("NEMOTRON_MODEL", raising=False)
    monkeypatch.delenv("NEMOTRON_FAST_MODEL", raising=False)
    c = FakeClient([
        NS(content="", tool_calls=[_tool_call(1, "get_fire_weather", {}),
                                   _tool_call(2, "get_season_context", {})]),
        NS(content="", tool_calls=[_tool_call(3, "get_satellite_detections", {})]),
        NS(content="<think>plan</think>## Situation\nExtreme.", tool_calls=None),
    ])
    result = agent.run(_ctx(), c)

    assert result.mode == "agent"
    assert result.model == "nvidia/nemotron-ultra"
    assert result.fast_model == "nvidia/nemotron-nano"
    assert result.text == "## Situation\nExtreme."
    assert [t["tool"] for t in result.trace] == ["get_fire_weather", "get_season_context",
                                                "get_satellite_detections"]
    # Tool results were sent back to the model with matching ids.
    tool_msgs = [m for m in c.requests[-1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_1", "call_2", "call_3"]
    assert "percentile" in json.loads(tool_msgs[1]["content"])


def test_falls_back_to_pipeline_without_tool_calling(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    c = FakeClient([NS(content="Briefing from pipeline.", tool_calls=None)], reject_tools=True)
    result = agent.run(_ctx(), c)

    assert result.mode == "pipeline"
    assert result.text == "Briefing from pipeline."
    assert [t["tool"] for t in result.trace] == ["get_fire_weather", "get_season_context",
                                                "get_satellite_detections", "simulate_spread", "search_news"]
    assert "not configured" in result.trace[-1]["summary"]


def test_season_context_ranks_target_day():
    ctx = agent.season_context(_season(), "2024-08-11")
    assert ctx["percentile"] == 100          # wind rises every day, so the last day is the worst
    assert ctx["season_peak"]["date"] == "2024-08-11"
    assert ctx["days_in_season"] == 11
