"""FireWatch agent: Nemotron decides which tools to call, then writes the briefing.

Tools
    get_fire_weather          FWI table (history + forecast), computed in code
    get_season_context        how unusual the target day is for this season
    get_satellite_detections  NASA FIRMS active fires near the point
    simulate_spread           FBP potential spread of a fire starting here (1/3/6 h)
    search_news               Tavily news search, condensed by the fast Nemotron model

The reasoning model (Nemotron Ultra/Super) runs the tool-calling loop; the
fast model (Nemotron Nano) condenses raw search results, so the expensive
model only reads short, sourced facts. If the endpoint rejects tool calling,
the same tools run in a fixed order and the reasoning model writes the
briefing from their combined output, so a briefing is always produced.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date

from openai import BadRequestError, OpenAI

from firewatch import firms, fwi, llm, news, spread

MAX_STEPS = 6

SYSTEM_PROMPT = llm.SYSTEM_PROMPT + """
You work as an agent with tools. Gather evidence before writing:
- Always start with get_fire_weather, then get_season_context.
- Call get_satellite_detections to check for fires already burning.
- Call simulate_spread with the fuel that best matches the landscape, to say how far and in which direction a fire starting here could travel in 1, 3 and 6 hours. Quote it as potential spread and mention its assumptions in a few words; use it to make evacuation timing concrete.
- Call search_news when danger is High or above, when satellites detect fires, or when local context would change the advice. Cite sources as markdown links.
Never state facts that no tool returned. When you have enough evidence, write the final briefing (no tool call).
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_fire_weather",
        "description": "Daily Canadian Fire Weather Index values (FFMC, DMC, DC, ISI, BUI, FWI, danger class) "
                       "with noon temperature, humidity, wind and 24 h rain, for the last days and any forecast days.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_season_context",
        "description": "How the target day's FWI ranks against every day of this fire season so far at this location.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_satellite_detections",
        "description": "Active fire detections from NASA FIRMS (VIIRS) within 50 km, around the target date.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "simulate_spread",
        "description": "Potential spread of a fire igniting at this location on the target day, from the Canadian "
                       "FBP System: head rate of spread, direction, distance and burned area after 1, 3 and 6 hours. "
                       "Assumes one uniform fuel type, flat terrain and the noon wind held constant.",
        "parameters": {"type": "object", "properties": {
            "fuel": {"type": "string", "enum": sorted(spread.FUELS),
                     "description": "pine = pine forest (FBP C-3), boreal = spruce forest (C-2), grass = cured grassland (O-1b)"},
        }, "required": ["fuel"]},
    }},
    {"type": "function", "function": {
        "name": "search_news",
        "description": "Search news and official announcements published around the target date "
                       "(wildfires, evacuations, fire bans, heatwaves). Returns condensed facts with sources.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query, e.g. 'wildfire Attica evacuation'"},
        }, "required": ["query"]},
    }},
]

USER_TEMPLATE = """Location: {place} ({lat:.2f}, {lon:.2f})
Target date: {date}{forecast}

Investigate the wildfire situation with your tools, then write a briefing in {language} with these sections:
1. Situation (danger level, main drivers, trend, how unusual for the season, any detections or reported fires)
2. Residents (what to do today)
3. Municipality / civil protection (patrols, resources, restrictions to consider)
4. Volunteers and fire watchers (where and when to be alert)
5. Outlook (only if forecast days exist; otherwise omit this section)
6. Sources (only news articles returned by search_news, as markdown links; otherwise omit this section)
Keep it under 350 words.
"""


@dataclass
class Context:
    place: str
    lat: float
    lon: float
    target: str
    season: list[fwi.FWIDay]          # 1 March .. target (+ forecast)
    window: list[fwi.FWIDay]          # days shown to the model
    fires: dict                       # result of the FIRMS lookup
    language: str = "en"
    sources: list[dict] = field(default_factory=list)


@dataclass
class Result:
    model: str
    fast_model: str | None
    mode: str                         # "agent" or "pipeline"
    text: str
    trace: list[dict]
    sources: list[dict]


# ---------- tools ----------

def _fire_weather(ctx: Context) -> dict:
    return {"days": [{
        "date": d.date, "forecast": d.date > ctx.target,
        "temp_C": round(d.temp, 1), "rh_pct": round(d.rh), "wind_kmh": round(d.wind),
        "rain_mm": round(d.rain, 1), "FFMC": round(d.ffmc, 1), "DMC": round(d.dmc, 1),
        "DC": round(d.dc), "ISI": round(d.isi, 1), "BUI": round(d.bui, 1),
        "FWI": round(d.fwi, 1), "danger": d.danger,
    } for d in ctx.window]}


def season_context(season: list[fwi.FWIDay], target: str) -> dict:
    past = [d for d in season if d.date <= target]
    if not past:
        return {"error": "no data"}
    today = past[-1]
    values = sorted(d.fwi for d in past)
    rank = sum(v <= today.fwi for v in values) / len(values)
    peak = max(past, key=lambda d: d.fwi)
    return {
        "target_fwi": round(today.fwi, 1),
        "season_start": past[0].date,
        "days_in_season": len(past),
        "percentile": round(100 * rank),
        "season_peak": {"date": peak.date, "fwi": round(peak.fwi, 1)},
        "days_very_high_or_worse": sum(d.fwi >= 38 for d in past),
        "change_vs_previous_day": round(today.fwi - past[-2].fwi, 1) if len(past) > 1 else None,
    }


def _detections(ctx: Context) -> dict:
    if ctx.fires.get("status") != "ok":
        return {"available": False, "reason": ctx.fires.get("summary", "not checked")}
    return {"available": True, "summary": ctx.fires["summary"], "nearest": ctx.fires["items"][:5]}


def _spread(ctx: Context, fuel: str) -> dict:
    day = next((d for d in ctx.window if d.date == ctx.target), None)
    if day is None:
        return {"error": "no fire weather for the target day"}
    out = dict(spread.scenario(day, ctx.lat, ctx.lon, fuel if fuel in spread.FUELS else "pine"))
    out.pop("frames")  # coordinates are for the map, not the model
    out["rings"] = [{k: v for k, v in r.items() if k != "polygon"} for r in out["rings"]]
    return out


def _search_news(ctx: Context, c: OpenAI, fast_model: str | None, query: str) -> dict:
    try:
        results = news.search(query, date.fromisoformat(ctx.target))
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    if not results:
        return {"available": True, "facts": "No matching news found.", "sources": []}

    for r in results:
        if r["url"] not in {s["url"] for s in ctx.sources}:
            ctx.sources.append({"title": r["title"], "url": r["url"], "published_date": r["published_date"]})

    raw = "\n\n".join(f"[{i + 1}] {r['title']} ({r['published_date'] or 'date unknown'})\n{r['url']}\n{r['content']}"
                      for i, r in enumerate(results))
    if not fast_model:
        return {"available": True, "facts": raw, "sources": [r["url"] for r in results]}
    resp = c.chat.completions.create(model=fast_model, temperature=0.1, max_tokens=500, messages=[
        {"role": "system", "content": "Extract only facts relevant to wildfire risk near the given place "
                                      "and date: fires, evacuations, bans, heat, wind. One short bullet per "
                                      "fact, each ending with its source number like [2]. No speculation."},
        {"role": "user", "content": f"Place: {ctx.place}\nDate: {ctx.target}\n\n{raw}"},
    ])
    facts = llm.strip_reasoning(resp.choices[0].message.content or "")
    return {"available": True, "facts": facts,
            "sources": [f"[{i + 1}] {r['url']}" for i, r in enumerate(results)]}


def _summary(name: str, out: dict) -> str:
    """One line for the UI trace."""
    if name == "get_fire_weather":
        past = [d for d in out["days"] if not d["forecast"]]
        if not past:
            return "no data"
        return f"{len(out['days'])} days · FWI {past[-1]['FWI']} ({past[-1]['danger']}) on {past[-1]['date']}"
    if name == "get_season_context":
        return (f"{out['percentile']}th percentile of {out['days_in_season']} days · "
                f"peak {out['season_peak']['fwi']} on {out['season_peak']['date']}") if "percentile" in out else "no data"
    if name == "get_satellite_detections":
        return out.get("summary") or _unavailable(out.get("reason", ""), "satellite detections")
    if name == "simulate_spread":
        if "rings" not in out:
            return out.get("error", "no result")
        r3 = next(r for r in out["rings"] if r["hours"] == 3)
        toward = f" toward {out['spread_toward']}" if out["spread_toward"] else ""
        return (f"{out['fuel']['label']}: {out['head_ros_m_min']} m/min · {r3['head_km']} km{toward} "
                f"and {r3['area_ha']} ha after 3 h")
    if name == "search_news":
        if not out.get("available"):
            return _unavailable(out.get("reason", ""), "news search")
        return f"{len(out.get('sources', []))} articles condensed"
    return ""


def _unavailable(reason: str, what: str) -> str:
    """Trace line for a tool that could not run: plain words, no key names."""
    if "not set" in reason or "Not checked" in reason:
        return f"{what} not configured on this deployment"
    return f"{what} unavailable right now"


def _run_tool(ctx: Context, c: OpenAI, fast_model: str | None, name: str, args: dict) -> dict:
    if name == "get_fire_weather":
        return _fire_weather(ctx)
    if name == "get_season_context":
        return season_context(ctx.season, ctx.target)
    if name == "get_satellite_detections":
        return _detections(ctx)
    if name == "simulate_spread":
        return _spread(ctx, args.get("fuel", "pine"))
    if name == "search_news":
        return _search_news(ctx, c, fast_model, args.get("query") or news.default_query(ctx.place, date.fromisoformat(ctx.target)))
    return {"error": f"unknown tool {name}"}


# ---------- loops ----------

def _user_prompt(ctx: Context) -> str:
    n_forecast = sum(d.date > ctx.target for d in ctx.window)
    return USER_TEMPLATE.format(
        place=ctx.place, lat=ctx.lat, lon=ctx.lon, date=ctx.target,
        forecast=f" (plus {n_forecast} forecast days)" if n_forecast else "",
        language=llm.LANGUAGES.get(ctx.language, ctx.language))


def _agent_loop(ctx: Context, c: OpenAI, model: str, fast_model: str | None, trace: list[dict]) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _user_prompt(ctx)}]
    for _ in range(MAX_STEPS):
        resp = c.chat.completions.create(model=model, messages=messages, tools=TOOLS,
                                         tool_choice="auto", temperature=0.3, max_tokens=1500)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return llm.strip_reasoning(msg.content or "")
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            started = time.perf_counter()
            out = _run_tool(ctx, c, fast_model, tc.function.name, args)
            trace.append({"tool": tc.function.name, "args": args, "summary": _summary(tc.function.name, out),
                          "ms": round(1000 * (time.perf_counter() - started))})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out)})
    # Out of steps: ask for the briefing with what was gathered.
    messages.append({"role": "user", "content": "Write the final briefing now, without calling tools."})
    resp = c.chat.completions.create(model=model, messages=messages, temperature=0.3, max_tokens=1500)
    return llm.strip_reasoning(resp.choices[0].message.content or "")


def _pipeline(ctx: Context, c: OpenAI, model: str, fast_model: str | None, trace: list[dict]) -> str:
    """Fixed tool order for endpoints without tool calling."""
    plan = [("get_fire_weather", {}), ("get_season_context", {}), ("get_satellite_detections", {}),
            ("simulate_spread", {"fuel": "pine"}),
            ("search_news", {"query": news.default_query(ctx.place, date.fromisoformat(ctx.target))})]
    evidence = []
    for name, args in plan:
        started = time.perf_counter()
        out = _run_tool(ctx, c, fast_model, name, args)
        trace.append({"tool": name, "args": args, "summary": _summary(name, out),
                      "ms": round(1000 * (time.perf_counter() - started))})
        evidence.append(f"## {name}\n{json.dumps(out)}")
    resp = c.chat.completions.create(model=model, temperature=0.3, max_tokens=1500, messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(ctx) + "\n\nTool results:\n\n" + "\n\n".join(evidence)},
    ])
    return llm.strip_reasoning(resp.choices[0].message.content or "")


def run(ctx: Context, c: OpenAI | None = None) -> Result:
    c = c or llm.client()
    model = llm.pick_model(c, llm.REASONING)
    try:
        fast_model = llm.pick_model(c, llm.FAST)
    except RuntimeError:
        fast_model = None
    trace: list[dict] = []
    try:
        text = _agent_loop(ctx, c, model, fast_model, trace)
        mode = "agent"
    except BadRequestError:
        # Tool calling not supported for this model/endpoint: same tools, fixed order.
        trace.clear()
        ctx.sources.clear()
        text = _pipeline(ctx, c, model, fast_model, trace)
        mode = "pipeline"
    return Result(model=model, fast_model=fast_model if fast_model != model else None,
                  mode=mode, text=text, trace=trace, sources=ctx.sources)
