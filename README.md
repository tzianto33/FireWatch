# FireWatch 🔥

**A wildfire danger agent for anywhere on Earth. It turns validated fire-weather science, satellite detections and live news into clear actions for residents, civil protection and volunteers. Built on NVIDIA Nemotron and Nebius Token Factory.**

Nebius x NVIDIA Global AI Hackathon 2026 · *Best Apps and Agents* track

> **23 of 25** major wildfires on six continents since 2016 started on a day that FireWatch ranks in the **top 10 %** of its season's fire danger (median: 99th percentile).

## The problem

Wildfires are getting worse on every continent: Los Angeles, Maui, Chile, Canada, Australia, the Mediterranean. Fire danger indices exist, but they are numbers (FFMC 92, BUI 290, FWI 66…) published in different formats by different agencies. Residents, small municipalities and volunteer fire watchers cannot act on them, and they rarely see the context: is today unusual? Is something already burning nearby? What are the authorities saying?

## What FireWatch does

- **Any point on Earth.** Search a place or click the map to get its fire danger, today with a 7-day forecast or on any day since 2013.
- **Validated science, not LLM guesses.** The danger comes from the Canadian Fire Weather Index System, as used by EFFIS and GWIS. It is computed in code, with latitude-specific day-length factors and a hemisphere-aware season, and it matches the reference values of Van Wagner (1987).
- **Nemotron agent.** A tool-calling agent decides what evidence it needs: fire weather, how unusual the day is for the season, NASA satellite detections, news. It then writes a briefing for three audiences in 10 languages, using the local emergency number. Every tool call is shown in the UI.
- **"What if a fire started here?"** Turn on fire mode and click the map. A time-lapse shows the fire growing from noon to 6 pm. Rates of spread come from the Canadian Fire Behavior Prediction (FBP) System, and the perimeter grows by Huygens' principle, the method of FARSITE and Prometheus. It accounts for real terrain (AWS Terrain Tiles at ~500 m: the FBP slope factor turned into an equivalent wind), hourly wind and hourly fine-fuel moisture (Van Wagner 1977), and it does not cross the sea. On level ground with constant weather it reproduces the analytical FBP ellipse to within 1 %, which is unit-tested. For Varnavas (11 Aug 2024), the simulated run heads south along the line of the real NASA FIRMS detections: 129 of 149 lie south of the ignition, and 68 fall inside the 6-hour perimeter. The agent uses it to make evacuation timing concrete. Not modelled: fuel maps, spotting, crowning, suppression.
- **Replay history.** Step day by day through the weeks before 25 major fires. Our gauge and the official global danger map (Copernicus GWIS, ECMWF FWI) move together.
- **Evidence panel.** For every historic fire, it shows where the start day ranked in its own season.

## Architecture

```
   Open-Meteo (ERA5 archive + forecast, any point, local standard noon)
            │
            ▼
   Canadian FWI System (fwi.py) ── FFMC · DMC · DC · ISI · BUI · FWI · danger class
            │      latitude bands (cffdrs) · season from 1 Mar (N) / 1 Sep (S) / 180 d (tropics)
            ▼                         tools
   ┌───────────────────────────────────────────────────────────────┐
   │ Nemotron agent (agent.py) on Nebius Token Factory              │
   │  reasoning model: Nemotron Ultra/Super  ── tool-calling loop   │
   │   ├─ get_fire_weather          FWI table, history + forecast   │
   │   ├─ get_season_context        percentile vs season, trend     │
   │   ├─ get_satellite_detections  NASA FIRMS VIIRS, 50 km         │
   │   ├─ simulate_spread           FBP + Huygens, terrain, hourly  │
   │   └─ search_news               Tavily (date-bounded) ──► fast   │
   │                                model Nemotron Nano condenses   │
   │                                results into sourced facts      │
   └───────────────────────────────────────────────────────────────┘
            │
            ▼
   Briefing: situation · residents · civil protection · volunteers · outlook · sources
```

**Design principles**

1. **The model never invents the danger level.** Numbers come from code. Nemotron interprets them, gathers context and turns it into actions.
2. **Right model for the job.** Nemotron Nano condenses raw search results. The reasoning model only reads short, sourced facts, so credits last longer and answers stay fast.
3. **Always answers.** If an endpoint rejects tool calling, the same tools run in a fixed order. Archive weather is cached on disk (`~/.cache/firewatch`), and Open-Meteo rate limits are retried.

## Validation

`GET /api/validation` computes the FWI on the start day of 25 major wildfires and ranks it against every day of that location's season so far:

| Fire | Date | FWI | Season percentile |
|---|---|---|---|
| Fort McMurray, CA | 2016-05-01 | 25.7 | 94 |
| Knysna, ZA | 2017-06-07 | 90.8 | 100 |
| Pedrógão Grande, PT | 2017-06-17 | 37.0 | 98 |
| Mati, GR | 2018-07-23 | 36.6 | **86** |
| Monchique, PT | 2018-08-03 | 65.1 | 100 |
| Camp Fire (Paradise), US | 2018-11-08 | 46.4 | **67** |
| Kangaroo Island, AU | 2019-12-20 | 70.4 | 100 |
| Arakapas, CY | 2021-07-03 | 72.4 | 100 |
| Montiferru, IT | 2021-07-24 | 73.6 | 99 |
| Manavgat, TR | 2021-07-28 | 73.7 | 99 |
| North Evia, GR | 2021-08-03 | 60.7 | 100 |
| Sierra de la Culebra, ES | 2022-06-15 | 41.4 | 99 |
| Landiras, FR | 2022-07-12 | 26.6 | 99 |
| Rhodes, GR | 2023-07-18 | 52.5 | 94 |
| Palermo, IT | 2023-07-24 | 80.4 | 100 |
| Lahaina, US | 2023-08-08 | 33.2 | 100 |
| Tenerife, ES | 2023-08-15 | 46.2 | 96 |
| Evros, GR | 2023-08-19 | 72.4 | 99 |
| Viña del Mar, CL | 2024-02-02 | 82.4 | 100 |
| Jasper, CA | 2024-07-22 | 27.6 | 98 |
| Varnavas, GR | 2024-08-11 | 66.4 | 90 |
| Madeira, PT | 2024-08-14 | 17.5 | 100 |
| Pacific Palisades, US | 2025-01-07 | 78.1 | 100 |
| Ōfunato, JP | 2025-02-26 | 14.1 | 99 |
| Uiseong, KR | 2025-03-22 | 24.6 | 98 |

**Honest limits.** Fire weather is a necessary condition, not a prediction of ignition. The two misses are instructive: both were driven by winds that a single noon reading underestimates. The Camp Fire was driven by early-morning downslope winds, and Mati by gale-force afternoon gusts. Even among the hits, Pedrógão Grande's spread came from a thunderstorm downburst that no daily index captures. Low absolute values in mild climates (Madeira, Ōfunato) were still the worst days of their season, which is why FireWatch reports the percentile alongside the FWI.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env        # NEBIUS_API_KEY (required for the agent), TAVILY_API_KEY, FIRMS_MAP_KEY
uvicorn firewatch.server:app --port 8000
```

Open http://localhost:8000. Without keys, the FWI, replay, search, global danger map and validation all work. The agent, satellite detections and news switch on as soon as keys are added to `.env`, with no restart. Shortcuts: <kbd>Space</kbd> plays the replay, <kbd>←</kbd>/<kbd>→</kbd> step through it.

### Deploy

- **Render:** New → Blueprint → this repository. `render.yaml` sets everything up; enter `NEBIUS_API_KEY`, `FIRMS_MAP_KEY` and optionally `TAVILY_API_KEY` when asked.
- **Any Docker host** (Hugging Face Spaces, Fly.io, Cloud Run): use the `Dockerfile` and pass the same keys as secrets.

Agent runs cost tokens, so a public deployment limits them to `BRIEFINGS_PER_HOUR` per visitor (default 6) and `BRIEFINGS_PER_DAY` in total (default 300). Briefings for past dates are cached.

### Command line

```bash
python -m firewatch.cli --region palisades --date 2025-01-07           # replay + agent
python -m firewatch.cli --lat -33.87 --lon 151.21 --forecast 5         # any point, live
python -m firewatch.cli --region varnavas --date 2024-08-11 --lang el  # Greek briefing
python -m firewatch.cli --list-models                                  # Nemotron models on your account
```

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/fwi?region=…\|lat=…&lon=…&date=…&forecast=…` | FWI series (60 days + forecast), satellite detections |
| `POST /api/briefing` | Run the Nemotron agent; returns briefing, tool trace, sources, models |
| `GET /api/spread?…&ig_lat=…&ig_lon=…&day=…&fuel=pine\|grass\|boreal` | Potential fire spread polygons and stats (FBP) |
| `GET /api/search?q=…` | Place search |
| `GET /api/overview?date=…` | Danger at the 25 hotspots |
| `GET /api/validation` | Historic-fire evidence |
| `GET /api/regions` | Presets |

### Tests

```bash
python -m pytest -q
```

They cover the FWI reference values, latitude bands and hemisphere-aware seasons, timezones, API validation, and the agent loop against a scripted fake Nemotron: tool calls, trace, and the pipeline fallback.

## NVIDIA & Nebius usage

- **NVIDIA Nemotron**: the reasoning model (Ultra › Super) runs the tool-calling agent, and Nano condenses news. Both are picked automatically from the account's Nemotron models, or set with `NEMOTRON_MODEL` / `NEMOTRON_FAST_MODEL`.
- **Nebius Token Factory** serves every model call through its OpenAI-compatible API.

## Data sources

Open-Meteo (ERA5, forecasts) · AWS Terrain Tiles (elevation) · Copernicus GWIS/EFFIS (official ECMWF fire danger map) · NASA FIRMS (VIIRS active fires) · Tavily (news search) · OpenStreetMap Nominatim (places) · Esri (basemap).

## Disclaimer

FireWatch is a decision-support tool. It does not replace official warnings. In an emergency call your local emergency number (112 in Europe, 911 in North America, 000 in Australia) and follow the instructions of the authorities.

## License

MIT
