"""Combine the screen capture and the narration into the final MP4.

    python video/mix.py        # writes video/out/firewatch_demo.mp4 (1080p, H.264 + AAC)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import imageio_ffmpeg

from record import OUT, WORK
from narration import SCENES

AUDIO = OUT / "audio"
FINAL = OUT / "firewatch_demo.mp4"
MAX_SECONDS = 179.0  # YouTube submission must be under 3 minutes


def main():
    timeline = json.loads((OUT / "timeline.json").read_text())
    starts = {s["name"]: s["start"] for s in timeline["scenes"]}
    length = min(timeline["end"], MAX_SECONDS)

    inputs = ["-ss", str(timeline["offset"]), "-t", str(length), "-i", str(WORK / "screen.webm")]
    filters, labels = [], []
    for i, (name, _) in enumerate(SCENES):
        inputs += ["-i", str(AUDIO / f"{i:02d}_{name}.wav")]
        delay = int(starts[name] * 1000) + 250   # a beat after the cut
        filters.append(f"[{i + 1}:a]adelay={delay}|{delay}[a{i}]")
        labels.append(f"[a{i}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,apad[aout]")
    filters.append("[0:v]scale=1920:1080:flags=lanczos,fps=30,format=yuv420p[vout]")

    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
           "-t", str(length), "-c:v", "libx264", "-preset", "slow", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(FINAL)]
    subprocess.run(cmd, check=True)
    print(f"{FINAL} · {length:.1f} s · {FINAL.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
