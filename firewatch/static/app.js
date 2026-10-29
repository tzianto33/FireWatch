const DANGER = {
  "Very low": "#3f9b4a",
  "Low": "#a3c23b",
  "Moderate": "#f2c12e",
  "High": "#f0892a",
  "Very high": "#e0402f",
  "Extreme": "#a3161d",
};
// Brighter variants for text on the dark background (the fills above are too dark to read).
const DANGER_TEXT = { ...DANGER, "Very high": "#ff5a4a", "Extreme": "#ff4d5a" };
// EFFIS danger classes as [lower, upper) FWI bands; the gauge gives each class an equal slice.
const BANDS = [[0, 5.2, "Very low"], [5.2, 11.2, "Low"], [11.2, 21.3, "Moderate"],
               [21.3, 38, "High"], [38, 50, "Very high"], [50, 100, "Extreme"]];
// Rough upper ends for the component bars (typical summer maxima).
const SCALES = { ffmc: 101, dmc: 300, dc: 1000, isi: 40, bui: 300 };
const TOOL_ICONS = { get_fire_weather: "🌡️", get_season_context: "📈", get_satellite_detections: "🛰️",
                     simulate_spread: "🔥", search_news: "📰" };
const CONTINENT_ORDER = ["Europe", "North America", "South America", "Africa", "Asia", "Oceania"];

const $ = (id) => document.getElementById(id);
// loc is either { region: "key" } or { lat, lon } for a clicked or searched point.
const state = { data: null, index: 0, timer: null, regions: [], markers: {}, loc: null, pin: null,
                request: 0, overviewDate: null,
                spread: { active: false, ig: null, timer: null, request: 0 } };
// Ignitions this close to the analysed point reuse its fire-weather series; farther away the
// clicked spot is analysed first, so every fire runs on its own local weather.
const REUSE_WEATHER_KM = 15;

// ---------- helpers ----------
function textOn(color) {
  return ["#a3c23b", "#f2c12e", "#f0892a"].includes(color) ? "#1a1205" : "#fff";
}
let toastTimer = null;
function showError(msg) {
  clearTimeout(toastTimer);
  $("error").textContent = msg;
  $("error").hidden = !msg;
  if (msg) toastTimer = setTimeout(() => { $("error").hidden = true; }, 6000);
}
async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}
function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}
function isoToday() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
function fmtDate(iso, opts = { weekday: "short", day: "numeric", month: "short", year: "numeric" }) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-GB", opts);
}
const shortDate = (iso) => fmtDate(iso, { day: "numeric", month: "short", year: "numeric" });
function locQuery(loc) {
  return loc.region ? `region=${loc.region}` : `lat=${loc.lat}&lon=${loc.lon}`;
}

// ---------- gauge ----------
const G = { cx: 110, cy: 112, r: 86 };
function polar(deg) {
  const a = (deg * Math.PI) / 180;
  return [G.cx + G.r * Math.cos(a), G.cy - G.r * Math.sin(a)];
}
function drawGauge() {
  const slice = 180 / BANDS.length, gap = 1.6;
  $("gauge-arcs").innerHTML = BANDS.map(([, , name], i) => {
    const [x1, y1] = polar(180 - i * slice - gap / 2);
    const [x2, y2] = polar(180 - (i + 1) * slice + gap / 2);
    return `<path d="M${x1.toFixed(1)} ${y1.toFixed(1)} A${G.r} ${G.r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}"
      fill="none" stroke="${DANGER[name]}" stroke-width="16" stroke-linecap="butt"/>`;
  }).join("");
}
function setGauge(fwi) {
  let pos = BANDS.length;
  for (let i = 0; i < BANDS.length; i++) {
    const [lo, hi] = BANDS[i];
    if (fwi < hi) { pos = i + (fwi - lo) / (hi - lo); break; }
  }
  const deg = 180 - (pos / BANDS.length) * 180;   // math angle, 180 = left
  $("gauge-needle").style.transform = `rotate(${90 - deg}deg)`;
}

// ---------- map ----------
// One copy of the world: no horizontal wrapping, panning stops at the edges.
const WORLD = L.latLngBounds([[-85, -180], [85, 180]]);
const map = L.map("map", {
  zoomControl: true, scrollWheelZoom: true, worldCopyJump: false,
  maxBounds: WORLD, maxBoundsViscosity: 1.0,
}).setView([25, 10], 2);
map.zoomControl.setPosition("topleft");
// The lowest zoom at which the world still fills the map, so no empty margins appear.
function fitMinZoom() {
  const size = map.getSize();
  map.setMinZoom(Math.max(1, Math.ceil(Math.log2(Math.max(size.x, size.y) / 256))));
}
fitMinZoom();
map.on("resize", fitMinZoom);
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
const ONE_WORLD = { noWrap: true, bounds: WORLD };
L.tileLayer(`${ESRI}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
  attribution: "Tiles &copy; Esri", maxZoom: 16, ...ONE_WORLD,
}).addTo(map);
// Place names (zIndex 3) sit above the danger overlay (zIndex 2) so they stay readable.
L.tileLayer(`${ESRI}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, zIndex: 3, ...ONE_WORLD }).addTo(map);
const firesLayer = L.layerGroup().addTo(map);
const spreadLayer = L.layerGroup().addTo(map);
// Official global fire danger (Copernicus GWIS, ECMWF FWI, from 2018); follows the replay day.
const effis = L.tileLayer.wms("https://maps.effis.emergency.copernicus.eu/gwis", {
  layers: "ecmwf.fwi", format: "image/png", transparent: true, version: "1.1.1",
  opacity: 0.5, time: "2024-08-11", attribution: "Fire danger &copy; Copernicus GWIS/ECMWF", ...ONE_WORLD,
}).addTo(map).setZIndex(2);
L.control.layers(null, {
  "Official fire danger (Copernicus GWIS)": effis,
  "Satellite detections (FIRMS)": firesLayer,
  "Fire spread scenario": spreadLayer,
}, { collapsed: true, position: "topright" }).addTo(map);
let effisTimer = null;
function setEffisDay(day) {
  clearTimeout(effisTimer);  // during replay only the day the slider settles on is fetched
  effisTimer = setTimeout(() => { if (effis.wmsParams.time !== day) effis.setParams({ time: day }); }, 250);
}

$("legend").innerHTML = Object.entries(DANGER)
  .map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("");

function markerStyle(danger, selected) {
  const color = DANGER[danger] || "#5b606b";
  return { radius: selected ? 13 : 7, color: selected ? "#fff" : "rgba(0,0,0,.6)", weight: selected ? 3 : 1.5,
           fillColor: color, fillOpacity: 0.95 };
}

function drawRegions() {
  for (const r of state.regions) {
    const m = L.circleMarker([r.lat, r.lon], markerStyle(null, false)).addTo(map);
    m.bindTooltip(`<b>${escapeHtml(r.name)}</b><br>${escapeHtml(r.country)}`);
    m.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      state.loc = { region: r.key };
      load();
    });
    state.markers[r.key] = m;
  }
}

map.on("click", (e) => {
  const lat = +e.latlng.lat.toFixed(3);
  const lon = +L.Util.wrapNum(e.latlng.lng, [-180, 180]).toFixed(3);
  if (state.spread.active) setIgnition(lat, lon);
  else goToPoint(+lat.toFixed(2), +lon.toFixed(2), false);
});

function goToPoint(lat, lon, zoom) {
  state.loc = { lat, lon };
  $("fire").value = "";
  if (zoom) map.setView([lat, lon], 8);
  load();
}

function setPin(loc, danger) {
  if (state.pin) { map.removeLayer(state.pin); state.pin = null; }
  if (loc.key) return;
  state.pin = L.circleMarker([loc.lat, loc.lon], markerStyle(danger, true)).addTo(map);
}

async function loadOverview(date) {
  if (state.overviewDate === date) return;
  state.overviewDate = date;
  try {
    const rows = await api(`/api/overview?date=${date}`);
    for (const r of rows) {
      const m = state.markers[r.key];
      if (!m) continue;
      m.options.danger = r.danger;
      m.setStyle(markerStyle(r.danger, r.key === state.data?.location.key));
      m.setTooltipContent(`<b>${escapeHtml(r.name)}</b><br>${escapeHtml(r.country)} · FWI ${r.fwi ?? "–"} · ${r.danger ?? "no data"}`);
    }
  } catch (e) { state.overviewDate = null; /* decorative; retried next load */ }
}

function drawFires(fires) {
  firesLayer.clearLayers();
  for (const f of fires.items) {
    L.circleMarker([f.lat, f.lon], {
      radius: Math.min(4 + Math.sqrt(f.frp_mw), 14), color: "#ffd166", weight: 1,
      fillColor: "#ff3b1f", fillOpacity: 0.85,
    }).bindTooltip(`${f.date} ${f.time_utc} UTC<br>FRP ${f.frp_mw} MW · ${f.distance_km} km`)
      .addTo(firesLayer);
  }
}

// ---------- fire spread scenario ----------
const RING_STYLE = {
  6: { color: "#ffd166", fillColor: "#ffd166", fillOpacity: 0.12, weight: 1.5, dashArray: "4 4" },
  3: { color: "#ff8a3d", fillColor: "#ff8a3d", fillOpacity: 0.22, weight: 2 },
  1: { color: "#ff3d2e", fillColor: "#ff3d2e", fillOpacity: 0.4, weight: 2 },
};

function distanceKm(a, b) {
  const r = Math.PI / 180;
  const h = Math.sin((b[0] - a[0]) * r / 2) ** 2 +
    Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin((b[1] - a[1]) * r / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

function toggleIgnite(force) {
  const on = force ?? !state.spread.active;
  state.spread.active = on;
  $("ignite-btn").setAttribute("aria-pressed", on);
  $("ignite-side").setAttribute("aria-pressed", on);
  $("ignite-side").textContent = on ? "Stop fire simulation" : "🔥 Simulate a fire here";
  document.body.classList.toggle("igniting", on);
  $("map-hint").textContent = on ? "Click anywhere on land to start a fire" : "Click anywhere on land to analyse it";
  $("spread-card").hidden = !on;
  if (on && state.data) {
    const [lat, lon] = state.spread.ig ?? [state.data.location.lat, state.data.location.lon];
    setIgnition(lat, lon);
  } else {
    clearInterval(state.spread.anim);
    spreadLayer.clearLayers();
  }
}

function setIgnition(lat, lon) {
  if (!state.data) return;
  const loc = state.data.location;
  if (distanceKm([lat, lon], [loc.lat, loc.lon]) > REUSE_WEATHER_KM) {
    // Analyse the clicked spot first; render() then starts the fire exactly where clicked.
    state.spread.pending = [lat, lon];
    spreadLayer.clearLayers();
    $("spread-body").innerHTML = '<p class="muted small">Loading the fire weather for this spot…</p>';
    goToPoint(+lat.toFixed(2), +lon.toFixed(2), false);
    return;
  }
  state.spread.ig = [lat, lon];
  refreshSpread(true);
}

function scheduleSpread() {
  clearTimeout(state.spread.timer);
  state.spread.timer = setTimeout(() => refreshSpread(false), 200);
}

async function refreshSpread(fit) {
  if (!state.spread.active || !state.spread.ig || !state.data) return;
  const day = state.data.days[state.index].date;
  const [ig_lat, ig_lon] = state.spread.ig;
  // A newer refresh can supersede this one; it must still zoom to and animate a new ignition.
  if (fit) state.spread.fitNext = true;
  const request = ++state.spread.request;
  const slow = setTimeout(() => {
    if (request === state.spread.request) {
      $("spread-body").innerHTML = '<p class="muted small">Fetching terrain and hourly weather for this spot… the first run here takes a few seconds.</p>';
    }
  }, 600);
  try {
    const res = await api(`/api/spread?${locQuery(state.loc)}&date=${state.data.target}` +
      `&forecast=${$("forecast").value}&day=${day}&fuel=${$("fuel").value}&ig_lat=${ig_lat}&ig_lon=${ig_lon}`);
    if (request !== state.spread.request) return;
    state.spread.result = res;
    const fresh = state.spread.fitNext;
    state.spread.fitNext = false;
    drawSpread(res, fresh, fresh);
    renderSpreadCard(res);
  } catch (e) {
    if (request === state.spread.request) showError(e.message);
  } finally {
    clearTimeout(slow);
  }
}

function clockAt(res, minutes) {
  const m = res.start_hour * 60 + minutes;
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

function drawRings(res) {
  for (const r of [...res.rings].sort((a, b) => b.hours - a.hours)) {
    L.polygon(r.polygon, { ...RING_STYLE[r.hours] || RING_STYLE[6], interactive: true })
      .bindTooltip(`${clockAt(res, 60 * r.hours)} · after ${r.hours} h · ${r.head_km} km · ${r.area_ha.toLocaleString()} ha`, { sticky: true })
      .addTo(spreadLayer);
  }
}

function drawSpread(res, fit, animate) {
  clearInterval(state.spread.anim);
  spreadLayer.clearLayers();
  const visible = res.head_ros_m_min >= MIN_VISIBLE_ROS;
  const marker = L.marker(res.ignition, {
    icon: L.divIcon({ className: "ignition-icon", html: "🔥", iconSize: [28, 28], iconAnchor: [14, 22] }),
    interactive: false,
  });
  if (fit && visible) {
    map.fitBounds(L.polygon(res.rings[res.rings.length - 1].polygon).getBounds().pad(0.3), { maxZoom: 12 });
  }
  if (!visible) {
    const fwi = state.data.days[state.index].fwi;
    marker.addTo(spreadLayer).bindTooltip(
      `<b>No spread on this day</b><br>Fuels are too moist (FWI ${fwi}).<br>Try a dry day, e.g. a historic fire.`,
      { permanent: true, direction: "right", offset: [12, -10], className: "spread-clock" });
    return;
  }
  if (!animate || !res.frames?.length) {
    drawRings(res);
    marker.addTo(spreadLayer);
    return;
  }
  // Time-lapse: the burned area grows frame by frame, then the hour rings stay.
  const burn = L.polygon([], { color: "#ff3d2e", weight: 2, fillColor: "#ff5a2b", fillOpacity: 0.45 }).addTo(spreadLayer);
  marker.addTo(spreadLayer);
  marker.bindTooltip("", { permanent: true, direction: "right", offset: [12, -10], className: "spread-clock" });
  let k = 0;
  const step = () => {
    const f = res.frames[k];
    burn.setLatLngs(f.polygon);
    marker.setTooltipContent(`<b>${clockAt(res, f.minutes)}</b> · ${f.area_ha.toLocaleString()} ha`);
    if (++k >= res.frames.length) {
      clearInterval(state.spread.anim);
      setTimeout(() => {
        if (state.spread.result !== res) return;
        spreadLayer.removeLayer(burn);
        marker.unbindTooltip();
        drawRings(res);
        marker.remove(); marker.addTo(spreadLayer);
      }, 900);
    }
  };
  step();
  state.spread.anim = setInterval(step, state.spread.frameMs ?? 170);
}

const MIN_VISIBLE_ROS = 0.5;  // m/min; below this the ellipses are too small to see

function renderSpreadCard(res) {
  if (res.head_ros_m_min < MIN_VISIBLE_ROS) {
    $("spread-body").innerHTML = `
      <div class="spread-head"><b>${res.head_ros_m_min}</b><span class="muted">m/min · ${fmtDate(res.date, { day: "numeric", month: "short" })}</span></div>
      <p class="small" style="margin:0">Fuels are too moist for a fire to spread on this day, so there is nothing to draw.
        Try a dry day: pick a historic fire such as <a href="#" id="spread-example">Varnavas · 11 Aug 2024</a>.</p>`;
    $("spread-example").onclick = (e) => { e.preventDefault(); selectFire("varnavas", false); };
    return;
  }
  const end = res.wind_end;
  const shift = end && (end.from !== res.wind_from || Math.abs(end.kmh - res.wind_kmh) >= 5)
    ? ` → <b>${end.kmh} km/h from ${end.from}</b> by ${clockAt(res, 360)}` : "";
  $("spread-body").innerHTML = `
    <div class="spread-head"><b>${res.head_ros_m_min}</b><span class="muted">m/min at ignition · ${fmtDate(res.date, { day: "numeric", month: "short" })}, ${clockAt(res, 0)}</span></div>
    <p class="small" style="margin:0 0 10px">Wind ${res.wind_kmh} km/h from ${res.wind_from}${shift} · main run toward <b>${res.spread_toward}</b></p>
    <div class="spread-rows">${res.rings.map((r) => `
      <div><span>${clockAt(res, 60 * r.hours)} · ${r.hours} h</span><b>${r.head_km} km</b><small>${r.area_ha.toLocaleString()} ha</small></div>`).join("")}
    </div>
    <div class="spread-actions">
      <button class="btn small" id="spread-replay">▶ Replay spread</button>
      <span class="muted small">${res.hourly_weather ? "Hourly weather" : "Noon weather"} · ${escapeHtml(res.terrain)}</span>
    </div>
    <p class="spread-note">${escapeHtml(res.assumptions)}</p>`;
  $("spread-replay").onclick = () => drawSpread(res, false, true);
}

function showSpreadCard() {
  const side = document.querySelector(".side");
  side.scrollTo({ top: $("spread-card").offsetTop - 14, behavior: "smooth" });
}
$("ignite-btn").onclick = () => { toggleIgnite(); if (state.spread.active) showSpreadCard(); };
$("ignite-side").onclick = () => { toggleIgnite(); if (state.spread.active) showSpreadCard(); };
$("fuel").onchange = () => refreshSpread(false);

// ---------- search ----------
async function search(q) {
  const list = $("search-results");
  list.hidden = false;
  list.innerHTML = '<li class="empty">Searching…</li>';
  try {
    const rows = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (!rows.length) { list.innerHTML = '<li class="empty">No places found</li>'; return; }
    list.innerHTML = rows.map((r, i) =>
      `<li data-i="${i}" tabindex="0">${escapeHtml(r.label)}</li>`).join("");
    list.querySelectorAll("li").forEach((li) => {
      const pick = () => {
        const r = rows[+li.dataset.i];
        list.hidden = true;
        $("search").value = r.label.split(",")[0];
        goToPoint(r.lat, r.lon, true);
      };
      li.onclick = pick;
      li.onkeydown = (e) => { if (e.key === "Enter") pick(); };
    });
  } catch (e) {
    list.innerHTML = `<li class="empty">${escapeHtml(e.message)}</li>`;
  }
}
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  const q = $("search").value.trim();
  if (q.length >= 2) search(q);
};
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search")) $("search-results").hidden = true;
});

// ---------- chart ----------
const cursorPlugin = {
  id: "cursor",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { y } } = chart;
    for (const [lower, upper, name] of BANDS) {
      if (lower >= y.max) break;
      const top = y.getPixelForValue(Math.min(name === "Extreme" ? y.max : upper, y.max));
      const bottom = y.getPixelForValue(lower);
      ctx.fillStyle = DANGER[name] + "1c";
      ctx.fillRect(a.left, top, a.right - a.left, bottom - top);
    }
  },
  afterDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { x, y } } = chart;
    const px = x.getPixelForValue(state.index);
    const d = state.data?.days[state.index];
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
    if (d) {
      ctx.setLineDash([]);
      ctx.fillStyle = DANGER[d.danger]; ctx.strokeStyle = "#0a0c10"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, y.getPixelForValue(d.fwi), 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    ctx.restore();
  },
};

const chart = new Chart($("chart"), {
  type: "line",
  data: { labels: [], datasets: [{
    data: [], borderColor: "#ff7a3a", borderWidth: 2, tension: 0.3,
    pointRadius: 0, pointHoverRadius: 4, fill: { target: "origin", above: "rgba(255,106,43,.08)" },
    segment: { borderDash: (c) => state.data?.days[c.p1DataIndex]?.forecast ? [5, 4] : undefined },
  }] },
  options: {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    onClick: (_, els) => { if (els.length) { stop(); setIndex(els[0].index); } },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#12151c", borderColor: "rgba(255,255,255,.14)", borderWidth: 1, padding: 10,
        callbacks: {
          title: (t) => state.data.days[t[0].dataIndex].date + (state.data.days[t[0].dataIndex].forecast ? " · forecast" : ""),
          label: (c) => `FWI ${c.parsed.y} · ${state.data.days[c.dataIndex].danger}`,
        },
      },
    },
    scales: {
      x: { ticks: { color: "#7d828c", maxTicksLimit: 6, font: { size: 11 } }, grid: { display: false } },
      y: { min: 0, suggestedMax: 60, ticks: { color: "#7d828c", font: { size: 11 }, maxTicksLimit: 5 }, grid: { color: "rgba(255,255,255,.05)" } },
    },
  },
  plugins: [cursorPlugin],
});

// ---------- view ----------
function setIndex(i) {
  const days = state.data.days;
  state.index = Math.max(0, Math.min(i, days.length - 1));
  $("slider").value = state.index;
  const d = days[state.index];
  const color = DANGER[d.danger];

  $("day").textContent = fmtDate(d.date) + (d.forecast ? " · forecast" : "");
  $("fwi").textContent = d.fwi.toFixed(1);
  $("fwi").style.color = DANGER_TEXT[d.danger];
  $("danger").textContent = d.danger;
  $("danger").style.background = color;
  $("danger").style.color = textOn(color);
  document.querySelector(".hero").style.setProperty("--glow", color + "40");
  setGauge(d.fwi);
  $("temp").textContent = `${Math.round(d.temp)} °C`;
  $("rh").textContent = `${Math.round(d.rh)} %`;
  $("wind").textContent = `${Math.round(d.wind)} km/h`;
  $("rain").textContent = `${d.rain} mm`;
  $("components").innerHTML = [
    ["FFMC", "ffmc", "Fine fuel dryness: how easily fires ignite"],
    ["DMC", "dmc", "Duff moisture: medium-term drying"],
    ["DC", "dc", "Drought code: deep, long-term drought"],
    ["ISI", "isi", "Initial spread index: how fast fire spreads"],
    ["BUI", "bui", "Buildup index: fuel available to burn"],
  ].map(([label, k, hint]) => `
    <div class="row" title="${hint}">
      <span>${label}</span>
      <span class="bar"><span style="width:${Math.min(100, d[k] / SCALES[k] * 100)}%"></span></span>
      <span class="val">${d[k]}</span>
    </div>`).join("");

  const target = days.findIndex((x) => x.date === state.data.target);
  $("slider-day").textContent = shortDate(d.date);
  $("slider-label").textContent = d.date === state.data.target ? "target day" :
    state.index < target ? `${target - state.index} days before` : `forecast +${state.index - target} d`;

  const loc = state.data.location;
  const m = loc.key ? state.markers[loc.key] : state.pin;
  if (m) m.setStyle(markerStyle(d.danger, true));
  setEffisDay(d.date);
  if (state.spread.active) scheduleSpread();
  chart.update("none");
}

function render(data) {
  state.data = data;
  const days = data.days, loc = data.location;
  rememberLoc(loc.key ? { region: loc.key } : { lat: loc.lat, lon: loc.lon });
  $("place").textContent = loc.label;
  chart.data.labels = days.map((d) => fmtDate(d.date, { day: "numeric", month: "short" }));
  chart.data.datasets[0].data = days.map((d) => d.fwi);
  chart.options.scales.y.suggestedMax = Math.max(60, ...days.map((d) => d.fwi)) + 5;
  $("slider").max = days.length - 1;

  const fires = data.fires;
  const msg = { no_key: "Add FIRMS_MAP_KEY to .env to see satellite detections.", error: fires.summary };
  $("fires-summary").textContent = msg[fires.status] || fires.summary;
  $("fires-list").innerHTML = fires.items.slice(0, 30).map((f) =>
    `<li>${f.date} ${f.time_utc} UTC · ${f.distance_km} km · FRP ${f.frp_mw} MW</li>`).join("");
  drawFires(fires);

  for (const [key, m] of Object.entries(state.markers)) {
    m.setStyle(markerStyle(m.options.danger, key === loc.key));
  }
  setPin(loc, null);
  // Presets zoom to regional scale (EFFIS cells are ~8 km); clicked points keep the user's zoom.
  if (loc.key && !state.spread.active) map.flyTo([loc.lat, loc.lon], fires.items.length ? 8 : 7, { duration: 1.2 });

  // A new place gets a fresh ignition at its centre.
  state.spread.ig = null;
  setIndex(days.findIndex((d) => d.date === data.target));
  if (state.spread.active) {
    const [lat, lon] = state.spread.pending ?? [loc.lat, loc.lon];
    state.spread.pending = null;
    setIgnition(lat, lon);
  }
}

async function load() {
  if (!state.loc) return;
  stop();
  showError("");
  const date = $("date").value, forecast = $("forecast").value;
  $("load").disabled = true;
  $("load").textContent = "Loading…";
  $("briefing-text").innerHTML = '<p class="muted">Press “Run agent” for a briefing on the target day.</p>';
  $("model").textContent = "";
  $("trace").innerHTML = "";
  $("sources").innerHTML = "";
  const request = ++state.request;  // only the latest click/selection may render
  try {
    const data = await api(`/api/fwi?${locQuery(state.loc)}&date=${date}&forecast=${forecast}`);
    if (request !== state.request) return;
    render(data);
    loadOverview(date);
  } catch (e) {
    if (request === state.request) {
      state.spread.pending = null;
      showError(e.message);
    }
  } finally {
    if (request === state.request) {
      $("load").disabled = false;
      $("load").textContent = "Load";
    }
  }
}

// ---------- replay ----------
function setPlayIcon(playing) {
  $("play-icon").setAttribute("d", playing ? "M7 5h4v14H7zM13 5h4v14h-4z" : "M8 5v14l11-7z");
  $("play").setAttribute("aria-label", playing ? "Pause replay" : "Play replay");
}
function stop() {
  clearInterval(state.timer);
  state.timer = null;
  setPlayIcon(false);
}
function play() {
  if (state.timer) return stop();
  if (!state.data) return;
  const target = state.data.days.findIndex((d) => d.date === state.data.target);
  const end = state.data.days.length - 1;
  if (state.index >= end) setIndex(Math.max(0, target - 21));
  setPlayIcon(true);
  state.timer = setInterval(() => {
    if (state.index >= end) return stop();
    setIndex(state.index + 1);
  }, 320);
}

// ---------- agent ----------
function renderTrace(trace) {
  $("trace").innerHTML = trace.map((t) => `
    <li><span class="ico">${TOOL_ICONS[t.tool] || "🔧"}</span>
      <span><code>${escapeHtml(t.tool)}</code>${t.args.query ? ` <span class="q">“${escapeHtml(t.args.query)}”</span>` : ""}
        <br>${escapeHtml(t.summary)}</span>
      <span class="muted">${t.ms} ms</span></li>`).join("");
}

function renderSources(sources) {
  $("sources").innerHTML = sources.map((s) => {
    let host = "";
    try { host = new URL(s.url).hostname.replace(/^www\./, ""); } catch (e) { return ""; }
    if (!/^https?:/.test(s.url)) return "";
    return `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.title)}
      <small>${escapeHtml(host)}${s.published_date ? " · " + escapeHtml(s.published_date.slice(0, 16)) : ""}</small></a>`;
  }).join("");
}

async function brief() {
  if (!state.data) return;
  $("brief").disabled = true;
  $("brief").textContent = "Working…";
  $("model").textContent = "";
  $("trace").innerHTML = "";
  $("sources").innerHTML = "";
  $("briefing-text").innerHTML = '<p class="muted">Nemotron is calling its tools and weighing the evidence…</p>';
  const loc = state.data.location;
  try {
    const out = await api("/api/briefing", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(loc.key ? { region: loc.key } : { lat: loc.lat, lon: loc.lon }),
        date: state.data.target, forecast: Number($("forecast").value), lang: $("lang").value,
      }),
    });
    $("model").innerHTML = `${escapeHtml(out.model)}${out.fast_model ? ` + ${escapeHtml(out.fast_model)} for news` : ""}
      · Nebius Token Factory <span class="mode ${out.mode}">${out.mode === "agent" ? "tool-calling agent" : "fixed pipeline"}</span>`;
    renderTrace(out.trace);
    $("briefing-text").innerHTML = DOMPurify.sanitize(marked.parse(out.text));
    renderSources(out.sources);
  } catch (e) {
    $("briefing-text").innerHTML = "";
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = e.message;
    $("briefing-text").appendChild(p);
  } finally {
    $("brief").disabled = false;
    $("brief").textContent = "Run agent";
  }
}

// ---------- historic fires & validation ----------
function selectFire(key, scroll) {
  const r = state.regions.find((x) => x.key === key);
  if (!r) return;
  $("fire").value = key;
  state.loc = { region: key };
  $("date").value = r.fire;
  $("forecast").value = "0";
  load();
  if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
}

function fillFirePicker() {
  const groups = CONTINENT_ORDER.map((c) => {
    const fires = state.regions.filter((r) => r.fire && r.continent === c)
      .sort((a, b) => b.fire.localeCompare(a.fire));
    if (!fires.length) return "";
    return `<optgroup label="${c}">${fires.map((r) =>
      `<option value="${r.key}">${escapeHtml(r.name)} · ${shortDate(r.fire)}</option>`).join("")}</optgroup>`;
  }).join("");
  $("fire").innerHTML = '<option value="">Choose a fire…</option>' + groups;
}

async function loadValidation() {
  try {
    const v = await api("/api/validation");
    const continents = new Set(v.fires.filter((f) => !f.error).map((f) => state.regions.find((r) => r.key === f.key)?.continent));
    $("proof-num").textContent = `${v.top_decile}/${v.total}`;
    $("proof-text").textContent = `major wildfires on ${continents.size} continents started on a top-10% fire-danger day of their season`;
    $("proof").hidden = false;
    $("evidence-num").textContent = `${v.top_decile}/${v.total}`;
    $("evidence-sub").textContent = `in the top 10% · median ${v.median_percentile}th percentile`;

    const byKey = Object.fromEntries(state.regions.map((r) => [r.key, r]));
    $("validation-rows").innerHTML = CONTINENT_ORDER.map((c) => {
      const rows = v.fires.filter((f) => !f.error && byKey[f.key]?.continent === c);
      if (!rows.length) return "";
      return `<div class="vgroup">${c}</div>` + rows.map((f) => `
        <div class="vrow" data-key="${f.key}" role="button" tabindex="0" title="Replay ${escapeHtml(f.name)}">
          <span><b>${escapeHtml(f.name)}</b> <span class="muted">· ${escapeHtml(f.country)}</span></span>
          <span class="fwi" style="color:${DANGER_TEXT[f.danger]}">${f.fwi}</span>
          <span class="track"><span style="width:${f.percentile}%;background:${DANGER[f.danger]}"></span><i></i></span>
          <span class="meta"><span>${shortDate(f.date)}</span><span>${f.percentile}th percentile · ${f.danger}</span></span>
        </div>`).join("");
    }).join("");
    document.querySelectorAll(".vrow").forEach((row) => {
      row.onclick = () => selectFire(row.dataset.key, true);
      row.onkeydown = (e) => { if (e.key === "Enter") selectFire(row.dataset.key, true); };
    });
  } catch (e) {
    $("validation-rows").innerHTML = `<p class="muted">Validation unavailable: ${escapeHtml(e.message)}</p>`;
  }
}

// ---------- init ----------
$("load").onclick = load;
$("play").onclick = play;
$("brief").onclick = brief;
$("slider").oninput = (e) => { stop(); setIndex(Number(e.target.value)); };
$("fire").onchange = () => selectFire($("fire").value, false);
$("today").onclick = () => {
  $("date").value = isoToday();
  $("forecast").value = "5";
  load();
};
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select, textarea") || !state.data) return;
  if (e.key === " ") { e.preventDefault(); play(); }
  if (e.key === "ArrowLeft") { stop(); setIndex(state.index - 1); }
  if (e.key === "ArrowRight") { stop(); setIndex(state.index + 1); }
});

// Analyse the user's own position (browser geolocation; the browser asks for permission once).
function locateUser({ quiet = false, onFound } = {}) {
  if (!navigator.geolocation) {
    if (!quiet) showError("Your browser cannot share its location.");
    return;
  }
  navigator.geolocation.getCurrentPosition((pos) => {
    onFound?.();
    const lat = +pos.coords.latitude.toFixed(2), lon = +pos.coords.longitude.toFixed(2);
    map.setView([lat, lon], 8, { animate: false });
    goToPoint(lat, lon, false);
  }, (err) => {
    if (!quiet) {
      showError(err.code === err.PERMISSION_DENIED
        ? "Location access is blocked. Allow it for this site in your browser settings."
        : "Could not get your location right now.");
    }
  }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 10 * 60 * 1000 });
}
$("locate").onclick = () => {
  $("date").value = isoToday();
  $("forecast").value = "5";
  locateUser();
};

// Opens on today's danger with a 5-day forecast, at the user's location; without it, at the
// last place viewed (Athens on a first visit).
const DEFAULT_LOC = { lat: 37.98, lon: 23.73 };
function savedLoc() {
  try {
    const loc = JSON.parse(localStorage.getItem("firewatch.loc"));
    if (loc && (loc.region || (Number.isFinite(loc.lat) && Number.isFinite(loc.lon)))) return loc;
  } catch (e) { /* storage unavailable or corrupt: use the default */ }
  return DEFAULT_LOC;
}
function rememberLoc(loc) {
  try { localStorage.setItem("firewatch.loc", JSON.stringify(loc)); } catch (e) { /* not essential */ }
}

(async () => {
  drawGauge();
  $("date").max = isoToday();
  $("date").value = isoToday();
  $("forecast").value = "5";
  state.regions = await api("/api/regions");
  fillFirePicker();
  drawRegions();
  // Open where the user is. If the browser has not answered within a moment (permission prompt
  // still open, or slow fix), show the last place first and move once the position arrives.
  let located = false;
  const fallback = setTimeout(() => {
    if (located) return;
    state.loc = savedLoc();
    if (!state.loc.region) map.setView([state.loc.lat, state.loc.lon], 8, { animate: false });
    load();
  }, 1500);
  locateUser({ quiet: true, onFound: () => { located = true; clearTimeout(fallback); } });
  loadValidation();
})();
