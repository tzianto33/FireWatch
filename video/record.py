"""Record the demo walkthrough of the running app, timed to the narration.

    uvicorn firewatch.server:app --port 8000      # in another terminal
    python video/narration.py
    python video/record.py                        # writes ~/.cache/firewatch/video/screen.webm and video/out/timeline.json

Every scene lasts as long as its narration clip plus a short pause. The
Nemotron agent is run for real before recording starts and its result is
shown in the agent scene, so viewers don't sit through the model's latency.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://localhost:8000/"
OUT = Path(__file__).parent / "out"
# Raw capture lives outside the project: Playwright's long file names can exceed the Windows path limit.
WORK = Path(os.environ.get("FIREWATCH_CACHE", Path.home() / ".cache" / "firewatch")) / "video"
SIZE = {"width": 1600, "height": 900}
PAUSE = 1.1  # seconds of breathing room after each narration clip

durations = json.loads((OUT / "audio" / "durations.json").read_text())


def js(page, code: str, arg=None):
    return page.evaluate(code, arg)


def wait_loaded(page, timeout=60):
    """Wait until the latest /api/fwi request has rendered."""
    page.wait_for_function("() => state.data && document.getElementById('load').textContent === 'Load'",
                           timeout=timeout * 1000)


def until(start: float, seconds: float):
    remaining = start + seconds - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def warm_up(page):
    """Load every view once so weather, tiles and the agent answer are cached."""
    page.goto(URL)
    page.wait_for_function("() => state.regions.length && state.data", timeout=120_000)
    page.wait_for_function("() => !document.getElementById('proof').hidden", timeout=180_000)
    for key in ("palisades", "varnavas"):
        js(page, "k => selectFire(k, false)", key)
        wait_loaded(page)
        time.sleep(2.5)
    js(page, "() => goToPoint(-33.87, 151.21, true)")
    wait_loaded(page)
    time.sleep(2)
    # Real agent run for Varnavas, shown later without the wait.
    js(page, "() => selectFire('varnavas', false)")
    wait_loaded(page)
    js(page, """async () => {
        window.__agent = await api('/api/briefing', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({region: 'varnavas', date: '2024-08-11', forecast: 0, lang: 'en'})});
    }""")
    # Pre-compute the spread scenarios (terrain, hourly weather) for the target day and the day before.
    js(page, "() => { toggleIgnite(true); }")
    page.wait_for_function("() => state.spread.result && state.spread.result.date === '2024-08-11'", timeout=120_000)
    js(page, "() => setIndex(state.data.days.findIndex(x => x.date === state.data.target) - 1)")
    page.wait_for_function("() => state.spread.result && state.spread.result.date === '2024-08-10'", timeout=120_000)
    js(page, "() => { toggleIgnite(false); state.spread.frameMs = 260; }")
    js(page, "() => selectFire('palisades', false)")
    wait_loaded(page)
    js(page, "() => map.setView([34.1, -118.5], 8, {animate: false})")
    time.sleep(4)
    js(page, "() => map.setView([20, 10], 2, {animate: false})")
    time.sleep(4)
    # Clean starting frame: world view, Palisades loaded, page at the top.
    js(page, """() => {
        window.scrollTo(0, 0);
        document.querySelector('.side').scrollTo(0, 0);
        document.getElementById('search').value = '';
        map.setView([28, -40], 3, {animate: false});
    }""")
    time.sleep(3)


def scene_hook(page, start, d):
    # Vector markers are scaled during a fly animation and look blurred; hide them until it lands.
    js(page, """() => {
        const pane = map.getPane('overlayPane');
        pane.style.transition = 'opacity .4s';
        pane.style.opacity = 0;
        map.once('moveend', () => { pane.style.opacity = 1; });
        map.flyTo([34.1, -118.5], 8, {duration: 5});
    }""")
    until(start, d)


def scene_problem(page, start, d):
    js(page, "() => map.flyTo([10, 20], 2, {duration: 6})")
    until(start, d)


def scene_product(page, start, d):
    time.sleep(1.5)
    page.click("#search")
    page.keyboard.type("Sydney", delay=120)
    page.keyboard.press("Enter")
    page.wait_for_selector("#search-results li[data-i]", timeout=15_000)
    time.sleep(0.8)
    page.click("#search-results li[data-i='0']")
    wait_loaded(page)
    until(start, d)


def scene_replay(page, start, d):
    js(page, "() => selectFire('varnavas', false)")
    wait_loaded(page)
    target = js(page, "() => state.data.days.findIndex(x => x.date === state.data.target)")
    js(page, "i => setIndex(i)", target - 21)
    until(start, 3.0)
    for i in range(target - 20, target):          # to 10 August
        js(page, "i => setIndex(i)", i)
        time.sleep(0.42)
    until(start, 11.5)
    js(page, "i => setIndex(i)", target)          # 11 August: the jump
    until(start, d)


def scene_spread(page, start, d):
    until(start, 2.6)                             # "...anywhere on Earth? FireWatch grows it hour by hour"
    js(page, "() => { toggleIgnite(true); showSpreadCard(); }")
    until(start, 16.0)                            # "The day before, the same fire would have crawled."
    target = js(page, "() => state.data.days.findIndex(x => x.date === state.data.target)")
    js(page, "i => setIndex(i)", target - 1)
    until(start, d - 0.6)
    js(page, "i => setIndex(i)", target)
    until(start, d)


def scene_agent(page, start, d):
    js(page, """() => {
        const side = document.querySelector('.side');
        side.scrollTo({top: document.querySelector('.agent').offsetTop - 14, behavior: 'smooth'});
        $('brief').textContent = 'Working…';
        $('brief').disabled = true;
        $('briefing-text').innerHTML = '<p class="muted">Nemotron is calling its tools and weighing the evidence…</p>';
    }""")
    time.sleep(1.6)
    js(page, """() => {
        const out = window.__agent;
        $('model').innerHTML = `${escapeHtml(out.model)} · Nebius Token Factory <span class="mode ${out.mode}">tool-calling agent</span>`;
        renderTrace(out.trace);
        $('briefing-text').innerHTML = DOMPurify.sanitize(marked.parse(out.text));
        $('brief').textContent = 'Run agent';
        $('brief').disabled = false;
    }""")
    until(start, 9.5)
    # Slowly scroll through the briefing.
    for _ in range(26):
        js(page, "() => document.querySelector('.side').scrollBy({top: 22})")
        time.sleep(0.4)
    until(start, d)


def scene_proof(page, start, d):
    js(page, "() => window.scrollTo({top: document.getElementById('evidence').offsetTop - 40, behavior: 'smooth'})")
    until(start, 6.5)
    js(page, "() => window.scrollBy({top: 360, behavior: 'smooth'})")
    until(start, d)


def scene_close(page, start, d):
    js(page, """() => {
        toggleIgnite(false);
        window.scrollTo({top: 0, behavior: 'smooth'});
        map.flyTo([20, 10], 2, {duration: 4});
        const card = document.createElement('div');
        card.id = 'endcard';
        card.innerHTML = `<div style="font-size:64px">🔥</div>
          <div style="font-size:56px;font-weight:800;letter-spacing:-.03em">FireWatch</div>
          <div style="font-size:22px;color:#c7c3ba;margin-top:6px">Global wildfire danger agent</div>
          <div style="font-size:18px;color:#8b909b;margin-top:28px">NVIDIA Nemotron · Nebius Token Factory · open source</div>`;
        Object.assign(card.style, {position: 'fixed', inset: '0', zIndex: 5000, display: 'grid',
          placeContent: 'center', textAlign: 'center', background: 'rgba(10,12,16,.82)',
          backdropFilter: 'blur(6px)', opacity: 0, transition: 'opacity 1.2s'});
        document.body.appendChild(card);
        setTimeout(() => card.style.opacity = 1, 1800);
    }""")
    until(start, d + 1.5)  # hold the end card


SCENES = [("hook", scene_hook), ("problem", scene_problem), ("product", scene_product),
          ("replay", scene_replay), ("spread", scene_spread), ("agent", scene_agent),
          ("proof", scene_proof), ("close", scene_close)]


def main():
    tmp = WORK / "raw"
    shutil.rmtree(tmp, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE)
        page = context.new_page()
        page_opened = time.monotonic()
        warm_up(page)

        t0 = time.monotonic()
        timeline = {"offset": round(t0 - page_opened, 3), "scenes": []}
        for name, fn in SCENES:
            start = time.monotonic()
            timeline["scenes"].append({"name": name, "start": round(start - t0, 3)})
            print(f"{start - t0:6.1f}s  {name}", flush=True)
            fn(page, start, durations[name] + PAUSE)
        timeline["end"] = round(time.monotonic() - t0, 3)

        video_path = Path(page.video.path())
        context.close()
        browser.close()

    shutil.move(str(video_path), WORK / "screen.webm")
    shutil.rmtree(tmp, ignore_errors=True)
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=2))
    print(f"done: {timeline['end']:.1f}s recorded after {timeline['offset']:.1f}s warm-up")


if __name__ == "__main__":
    main()
