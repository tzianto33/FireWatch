"""Command line entry point.

Examples:
    python -m firewatch.cli --region varnavas --date 2024-08-11
    python -m firewatch.cli --region landiras --forecast 5 --lang fr
    python -m firewatch.cli --lat 39.92 --lon -8.15 --date 2017-06-17
    python -m firewatch.cli --list-models
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from dotenv import load_dotenv

from firewatch import agent, firms, fwi, geocode, llm, weather
from firewatch.regions import REGIONS


def print_table(days: list[fwi.FWIDay], target: str) -> None:
    print(f"{'date':<11}{'T°C':>6}{'RH%':>5}{'wind':>6}{'rain':>6}"
          f"{'FFMC':>7}{'DMC':>6}{'DC':>6}{'ISI':>6}{'BUI':>6}{'FWI':>6}  danger")
    for d in days:
        mark = " <" if d.date == target else ("  (forecast)" if d.date > target else "")
        print(f"{d.date:<11}{d.temp:>6.1f}{d.rh:>5.0f}{d.wind:>6.0f}{d.rain:>6.1f}"
              f"{d.ffmc:>7.1f}{d.dmc:>6.1f}{d.dc:>6.0f}{d.isi:>6.1f}{d.bui:>6.1f}"
              f"{d.fwi:>6.1f}  {d.danger}{mark}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Greek briefings on Windows consoles
    p = argparse.ArgumentParser(description="FireWatch wildfire danger agent")
    p.add_argument("--region", choices=sorted(REGIONS), default="varnavas")
    p.add_argument("--lat", type=float, help="any latitude (overrides --region)")
    p.add_argument("--lon", type=float, help="any longitude (overrides --region)")
    p.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    p.add_argument("--forecast", type=int, default=0, help="extra forecast days (live mode)")
    p.add_argument("--history", type=int, default=7, help="days of history shown and sent to the model")
    p.add_argument("--lang", default="en", choices=sorted(llm.LANGUAGES), help="briefing language")
    p.add_argument("--no-llm", action="store_true", help="only compute FWI, skip Nemotron")
    p.add_argument("--no-fires", action="store_true", help="skip NASA FIRMS satellite detections")
    p.add_argument("--list-models", action="store_true", help="list Nemotron models on Token Factory")
    args = p.parse_args(argv)

    if args.list_models:
        print("\n".join(llm.nemotron_models(llm.client())))
        return 0

    if (args.lat is None) != (args.lon is None):
        p.error("--lat and --lon go together")
    if args.lat is not None:
        place, country, _ = geocode.place_name(round(args.lat, 2), round(args.lon, 2))
        region = {"name": f"{place}, {country}" if country else place,
                  "lat": args.lat, "lon": args.lon}
    else:
        r = REGIONS[args.region]
        region = {**r, "name": f"{r['name']}, {r['country']}"}
    until = date.fromisoformat(args.date)
    raw = weather.daily_weather(region["lat"], region["lon"], until, args.forecast)
    series = fwi.compute_series(raw, region["lat"])
    if not series:
        print("No weather data returned.", file=sys.stderr)
        return 1

    idx = next((i for i, d in enumerate(series) if d.date == args.date), len(series) - 1)
    window = series[max(0, idx - args.history + 1): idx + 1 + args.forecast]

    print(f"\n{region['name']}  ({region['lat']}, {region['lon']})\n")
    print_table(window, args.date)

    fires_result = {"status": "skipped", "summary": "Not checked.", "items": []}
    if not args.no_fires:
        try:
            fires = firms.active_fires(region["lat"], region["lon"], until)
            fires_result = {"status": "ok", "summary": firms.summarize(fires), "items": fires}
            print(f"\nNASA FIRMS: {fires_result['summary']}")
            for f in fires[:5]:
                print(f"  {f['date']} {f['time_utc']} UTC  {f['distance_km']:>5} km  "
                      f"FRP {f['frp_mw']:>6.1f} MW  ({f['lat']:.3f}, {f['lon']:.3f})")
        except Exception as exc:
            print(f"\n[NASA FIRMS skipped: {exc}]", file=sys.stderr)

    if args.no_llm:
        return 0
    ctx = agent.Context(place=region["name"], lat=region["lat"], lon=region["lon"],
                        target=series[idx].date, season=series, window=window,
                        fires=fires_result, language=args.lang)
    try:
        result = agent.run(ctx)
    except Exception as exc:  # keep the FWI output useful even without the model
        print(f"\n[Nemotron agent skipped: {exc}]", file=sys.stderr)
        return 2
    print(f"\n--- Agent ({result.mode}) · {result.model} ---")
    for step in result.trace:
        print(f"  {step['tool']:<26} {step['summary']}  ({step['ms']} ms)")
    print(f"\n{result.text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
