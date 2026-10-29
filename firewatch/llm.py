"""NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible API).

The FWI numbers are computed deterministically in fwi.py; the model never
guesses the danger level. Its job is to interpret the numbers, explain the
drivers in plain language and turn them into concrete actions per audience.
"""

from __future__ import annotations

import json
import os
import re

from openai import OpenAI

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
# Preference orders when NEMOTRON_MODEL / NEMOTRON_FAST_MODEL are not set.
REASONING = ("ultra", "super", "nano")  # strongest reasoning first
FAST = ("nano", "super", "ultra")       # cheapest, quickest first

SYSTEM_PROMPT = """You are FireWatch, a wildfire danger analyst supporting civil protection and communities worldwide.
You receive the output of the Canadian Fire Weather Index (FWI) System, computed from real weather data.
Rules:
- Treat the numbers as ground truth. Never invent values, fires or evacuation orders.
- Never name specific roads, villages, apps, websites, social-media accounts, agencies' handles, aircraft or past fires unless a tool returned them. People act on this briefing in emergencies: when you lack a local detail, give the generic action instead (e.g. "know two ways out of your neighbourhood", "follow your civil protection authority").
- If satellite detections or news were unavailable, say so in one short line; do not speculate about what they would show.
- Explain WHY the danger is what it is, naming the driving factors (wind, humidity, heat, drought build-up).
- FFMC = fine fuel dryness (ignition), DMC/DC = medium/deep drought, ISI = spread rate, BUI = fuel available, FWI = overall danger.
- Be concrete and brief. Use short bullet points.
- Adapt advice to the local landscape and country (e.g. eucalyptus in Portugal, pine forests in the Mediterranean, chaparral in California, boreal forest in Canada, bushland in Australia) without inventing local facts.
- Always end with this sentence, translated into the briefing language, using the local emergency number (112 in Europe, 911 in North America, 000 in Australia, 111 in New Zealand, 10177 in South Africa, 119 in Japan and Korea; 112 otherwise): "Decision-support tool only. In an emergency call <number> and follow official instructions."
"""

USER_TEMPLATE = """Region: {region}
Target date: {date}
Danger class on target date: {danger} (FWI {fwi:.1f})

Daily FWI data (most recent last):
{table}

Satellite fire detections near the region (NASA FIRMS, VIIRS):
{fires}

Write a briefing in {language} with these sections:
1. Situation (2-3 sentences: danger level and its main drivers, trend over the last days, any satellite detections)
2. Residents (what to do today)
3. Municipality / civil protection (patrols, resources, restrictions to consider)
4. Volunteers and fire watchers (where and when to be alert, e.g. hours of peak wind)
5. Outlook (only if forecast days are included)
"""

LANGUAGES = {"en": "English", "el": "Greek", "es": "Spanish", "pt": "Portuguese",
             "fr": "French", "it": "Italian", "de": "German", "tr": "Turkish",
             "ja": "Japanese", "ko": "Korean"}


def client() -> OpenAI:
    key = os.environ.get("NEBIUS_API_KEY")
    if not key:
        raise RuntimeError("NEBIUS_API_KEY is not set. Copy .env.example to .env and add your key.")
    return OpenAI(api_key=key, base_url=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL))


_models_cache: dict[str, list[str]] = {}


def nemotron_models(c: OpenAI) -> list[str]:
    key = str(c.api_key)
    if key not in _models_cache:
        _models_cache[key] = sorted(m.id for m in c.models.list() if "nemotron" in m.id.lower())
    return _models_cache[key]


def pick_model(c: OpenAI, preference: tuple[str, ...] = REASONING) -> str:
    env = "NEMOTRON_FAST_MODEL" if preference == FAST else "NEMOTRON_MODEL"
    if os.environ.get(env):
        return os.environ[env]
    models = nemotron_models(c)
    if not models:
        raise RuntimeError("No Nemotron model found on this Token Factory account; set NEMOTRON_MODEL.")
    for tier in preference:
        for m in models:
            if tier in m.lower():
                return m
    return models[0]


def strip_reasoning(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def briefing(region: str, days: list, target_date: str, language: str = "en",
             model: str | None = None,
             fires: str = "Not checked.") -> tuple[str, str]:
    """Return (model_id, briefing text) for the FWI series ending around target_date."""
    rows = [{
        "date": d.date, "temp_C": round(d.temp, 1), "rh_pct": round(d.rh),
        "wind_kmh": round(d.wind), "rain_mm": round(d.rain, 1),
        "FFMC": round(d.ffmc, 1), "DMC": round(d.dmc, 1), "DC": round(d.dc),
        "ISI": round(d.isi, 1), "BUI": round(d.bui, 1), "FWI": round(d.fwi, 1),
        "danger": d.danger, "forecast": d.date > target_date,
    } for d in days]
    target = next((d for d in days if d.date == target_date), days[-1])

    c = client()
    model = model or pick_model(c)
    resp = c.chat.completions.create(
        model=model,
        temperature=0.3,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(
                region=region, date=target_date, danger=target.danger, fwi=target.fwi,
                table="\n".join(json.dumps(r) for r in rows), fires=fires,
                language=LANGUAGES.get(language, language))},
        ],
    )
    return model, strip_reasoning(resp.choices[0].message.content or "")
