"""Narration for the demo video: one text-to-speech clip per scene.

    python video/narration.py            # writes video/out/audio/NN.wav and durations.json
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import wave
from pathlib import Path

import edge_tts
import imageio_ffmpeg

OUT = Path(__file__).parent / "out" / "audio"
VOICE = "en-US-AndrewNeural"
RATE = "+4%"

SCENES = [
    ("hook", "January 7th, 2025. Pacific Palisades, Los Angeles. That morning, the fire weather was among "
             "the worst of the entire season. The warning existed, but as a number almost nobody can read: "
             "Fire Weather Index, seventy-eight."),
    ("problem", "Fire danger data is published every day, everywhere, in formats built for specialists. "
                "Residents, small towns and volunteer fire watchers can't act on it. They need to know: "
                "how bad is today, why, and what do I do?"),
    ("product", "FireWatch is a wildfire danger agent for anywhere on Earth. Search a place, or click the map. "
                "It computes the Canadian Fire Weather Index from real weather data, in code, validated "
                "against reference values. The language model never guesses the danger level."),
    ("replay", "Replay any day since 2013. This is Varnavas, near Athens, August 2024. Watch weeks of drought "
               "build up. Then, on the eleventh, the index nearly doubles overnight as the wind picks up. "
               "That's the day the fire broke out. The official Copernicus danger map moves in sync."),
    ("spread", "What if a fire started here, anywhere on Earth? FireWatch grows it hour by hour across the "
               "real terrain, with that afternoon's wind, using the Canadian Fire Behavior Prediction System. "
               "By six p.m. it has run twelve kilometres south, right along NASA's satellite detections of "
               "the real fire. The day before, the same fire would have crawled."),
    ("agent", "Then the agent takes over. NVIDIA Nemotron 3 Ultra, served on Nebius Token Factory, decides "
              "which tools it needs: fire weather, how unusual today is for the season, NASA satellite "
              "detections, and the spread simulation. It turns the evidence into a briefing for residents, "
              "civil protection and volunteers, in ten languages, and it never states facts its tools "
              "didn't return."),
    ("proof", "Does it work? We checked twenty-five major wildfires on six continents. Twenty-three started "
              "on a day FireWatch ranks in the top ten percent of its season. And where it missed, we show why."),
    ("close", "FireWatch. Open source, built with NVIDIA Nemotron on Nebius Token Factory. "
              "Because a warning only helps if people can act on it."),
]


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


async def synthesize() -> dict[str, float]:
    OUT.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    durations = {}
    for i, (name, text) in enumerate(SCENES):
        mp3 = OUT / f"{i:02d}_{name}.mp3"
        wav = mp3.with_suffix(".wav")
        await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(mp3))
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "48000", "-ac", "2", str(wav)],
                       check=True)
        durations[name] = round(wav_seconds(wav), 2)
    (OUT / "durations.json").write_text(json.dumps(durations, indent=2))
    return durations


if __name__ == "__main__":
    d = asyncio.run(synthesize())
    for k, v in d.items():
        print(f"{k:<8} {v:6.2f} s")
    print(f"total    {sum(d.values()):6.2f} s")
