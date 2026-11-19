"use strict";
/* Vindmølle-dashboardet for Horns Rev 1. Seks skærmbilleder skifter hvert 20. sekund.
   Data kommer fra serveren på Pi'en (/api/...). Vejr, strømpris, alarmer og events er rigtige.
   Fejlene på parkkortet og serviceprognosen er simulerede, og skærmene siger det.
   Taster: pil venstre/højre skifter, 1-6 vælger, mellemrum pauser. ?screen=3&rotate=0 i adressen virker også. */

const PARK = { turbines: 80, ratedMW: 2.0, cutIn: 4, rated: 15, cutOut: 25, co2PerMWh: 0.37 };
const SCREEN_MS = 20000;
const UNITS = { gearbox_temperature: "°C", generator_temperature: "°C", vibration: "mm/s" };
const SENSOR_NAMES = { gearbox_temperature: "gearkasse", generator_temperature: "generator", vibration: "vibration" };

const S = {
  weather: null, prices: null, alarms: [], events: [], rules: null, errors: {},
  screen: 0, paused: false, shownAt: Date.now(), seen: new Set(), firstFeed: true,
  lostKr: 0, startedAt: Date.now(),
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (v, d = 1) => (v == null || Number.isNaN(Number(v))) ? "…"
  : Number(v).toLocaleString("da-DK", { minimumFractionDigits: d, maximumFractionDigits: d });
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const pct = (f) => `${nf(f * 100, f > 0 && f < 0.1 ? 1 : 0)} %`;   // 0,5 % i stedet for 0 % ved svag vind
const msg = (key) => `<p class="${S.errors[key] ? "err" : "mute"}">${esc(S.errors[key] || "Henter data …")}</p>`;

/* ------------------------------------------------------------ tid og data */
const HOUR_FMT = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Copenhagen", year: "numeric", month: "2-digit",
  day: "2-digit", hour: "2-digit", hourCycle: "h23" });
const dkHour = (d = new Date()) => HOUR_FMT.format(d).replace(" ", "T");          // "2026-11-20T14"
const dkTime = (iso) => new Date(iso).toLocaleTimeString("da-DK", { timeZone: "Europe/Copenhagen" });

async function getJSON(url) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 15000);
  try {
    const r = await fetch(url, { signal: ctl.signal, cache: "no-store" });
    return await r.json();
  } finally {
    clearTimeout(timer);
  }
}

async function load(key, url, apply) {
  try {
    const d = await getJSON(url);
    S.errors[key] = d && d.error ? d.error : null;
    if (!(d && d.error)) apply(d);
  } catch (e) {
    S.errors[key] = "Ingen forbindelse til serveren på Pi'en";
  }
  renderHeader();
  render();
}

const loadWeather = () => load("weather", "api/weather", (d) => { S.weather = d; });
const loadPrices = () => load("prices", "api/prices", (d) => { S.prices = d; });
const loadRules = () => load("rules", "api/rules", (d) => { S.rules = d; });
const loadLive = () => {
  load("alarms", "api/alarms", (d) => { S.alarms = d.items || []; });
  load("events", "api/events", (d) => { S.events = d.items || []; });
};

/* ------------------------------------------------------------ vind og effekt */
function nowIndex() {
  if (!S.weather || !S.weather.time) return -1;
  const k = dkHour();
  return S.weather.time.findIndex((t) => t.slice(0, 13) === k);
}

function avgWind(i) {
  const ms = (S.weather && S.weather.models) || [];
  const vals = ms.map((m) => ({ s: m.speed[i], d: m.direction[i] })).filter((v) => v.s != null && v.d != null);
  if (!vals.length) return null;
  const speed = vals.reduce((a, v) => a + v.s, 0) / vals.length;
  const x = vals.reduce((a, v) => a + Math.sin(v.d * Math.PI / 180), 0);
  const y = vals.reduce((a, v) => a + Math.cos(v.d * Math.PI / 180), 0);
  return { speed, dir: (Math.atan2(x, y) * 180 / Math.PI + 360) % 360, n: vals.length };
}
const windNow = () => { const i = nowIndex(); return i < 0 ? null : avgWind(i); };

/* Vestas V80 forenklet: starter ved 4 m/s, fuld effekt fra 15 m/s, stopper ved 25 m/s. */
function powerFrac(v) {
  if (v == null || v < PARK.cutIn || v >= PARK.cutOut) return 0;
  if (v >= PARK.rated) return 1;
  return (v ** 3 - PARK.cutIn ** 3) / (PARK.rated ** 3 - PARK.cutIn ** 3);
}

const COMPASS = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
const compass = (deg) => COMPASS[Math.round(deg / 22.5) % 16];
const angleDiff = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };

function priceAt(hourKey) {
  const h = S.prices && S.prices.hours ? S.prices.hours.find((x) => x.hour === hourKey) : null;
  return h ? h.price : null;    // kr/MWh
}

/* ------------------------------------------------------------ små SVG-tegninger */
function sparkline(values, { w = 200, h = 50, color = "var(--accent)", min, max, mark = -1, extra = "" } = {}) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return "";
  const lo = min ?? Math.min(...v), hi = max ?? Math.max(...v), span = (hi - lo) || 1;
  const pts = values.map((x, i) => (x == null ? null : [i * w / (values.length - 1), h - 3 - (x - lo) / span * (h - 6)]));
  const d = pts.filter(Boolean).map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  let s = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${extra}`
    + `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  if (mark >= 0 && pts[mark]) s += `<circle cx="${pts[mark][0]}" cy="${pts[mark][1]}" r="3.5" fill="#fff"/>`;
  return s + "</svg>";
}

function arrowSVG(fromDeg) {
  const rot = (fromDeg + 180) % 360;   // pilen peger derhen, hvor vinden blæser
  return `<svg viewBox="-50 -50 100 100"><circle r="44" fill="none" stroke="var(--line)" stroke-width="2"/>`
    + `<text y="-31" text-anchor="middle" font-size="11">N</text>`
    + `<g transform="rotate(${rot.toFixed(0)})"><path d="M0 -30 L11 -8 L3.5 -8 L3.5 30 L-3.5 30 L-3.5 -8 L-11 -8 Z" fill="var(--accent)"/></g></svg>`;
}

function gaugeSVG(frac, { label = "", color = "var(--accent)", sub = "" } = {}) {
  const f = clamp(frac, 0, 1), R = 80, cx = 100, cy = 100;
  const pt = (t) => [cx - R * Math.cos(Math.PI * t), cy - R * Math.sin(Math.PI * t)];
  const arc = (t0, t1) => { const [x0, y0] = pt(t0), [x1, y1] = pt(t1); return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${R} ${R} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`; };
  let s = `<svg viewBox="0 0 200 125"><path d="${arc(0, 1)}" stroke="var(--line)" stroke-width="16" fill="none" stroke-linecap="round"/>`;
  if (f > 0.004) s += `<path d="${arc(0, f)}" stroke="${color}" stroke-width="16" fill="none" stroke-linecap="round"/>`;
  s += `<text x="100" y="92" text-anchor="middle" font-size="30" font-weight="800" style="fill:var(--ink)">${esc(label)}</text>`;
  s += `<text x="100" y="116" text-anchor="middle" font-size="10">${esc(sub)}</text>`;
  s += `<text x="20" y="118" text-anchor="middle" font-size="9">0</text><text x="180" y="118" text-anchor="middle" font-size="9">100 %</text></svg>`;
  return s;
}

/* ------------------------------------------------------------ toplinjen */
function renderHeader() {
  const w = windNow();
  $("nowWind").textContent = w ? `${nf(w.speed)} m/s ${compass(w.dir)}` : "…";
  const p = priceAt(dkHour());
  $("nowPrice").textContent = p == null ? "…" : `${nf(p / 1000, 2)} kr/kWh`;
}

function tickClock() {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString("da-DK", { timeZone: "Europe/Copenhagen" });
  $("nowDate").textContent = now.toLocaleDateString("da-DK", { timeZone: "Europe/Copenhagen", weekday: "long", day: "numeric", month: "short" });
}

/* ------------------------------------------------------------ 1 vejret */
function weatherCard(name, origin, speed, dir, body, sub) {
  return `<div class="panel wcard"><div class="who"><b>${esc(name)}</b><span>${esc(origin)}</span></div>`
    + `<div style="display:flex;gap:.6em;align-items:center"><div class="big">${nf(speed)}</div>`
    + `<div class="mute">m/s<br>fra ${dir == null ? "…" : `${compass(dir)} ${Math.round(dir)}°`}</div></div>`
    + `<div class="arrow">${dir == null ? "" : arrowSVG(dir)}</div>`
    + `<div><div class="spark">${body}</div><div class="note">${esc(sub)}</div></div></div>`;
}

function renderWeather() {
  const w = S.weather;
  if (!w || !w.models || !w.models.length) { $("wcards").innerHTML = msg("weather"); $("spread").innerHTML = ""; return; }
  const i = Math.max(0, nowIndex()), to = Math.min(w.time.length, i + 24);
  const all = w.models.flatMap((m) => m.speed.slice(i, to)).filter((v) => v != null);
  const lo = Math.min(...all), hi = Math.max(...all);
  let html = w.models.map((m) => weatherCard(m.name, m.origin, m.speed[i], m.direction[i],
    sparkline(m.speed.slice(i, to), { min: lo, max: hi, mark: 0 }), "80 m · de næste 24 timer")).join("");
  if (w.met) {
    html += weatherCard(w.met.name, w.met.origin, w.met.speed, w.met.direction,
      `<p class="note">Måler i 10 meters højde, hvor vinden er svagere end ved rotoren.</p>`, "10 m · lige nu");
  }
  $("wcards").innerHTML = html;

  const now = w.models.map((m) => m.speed[i]).filter((v) => v != null);
  if (now.length > 1) {
    const mn = Math.min(...now), mx = Math.max(...now);
    const more = mn > 0 ? ((mx / mn) ** 3 - 1) * 100 : null;
    $("spread").innerHTML = `<div><div class="mute">Modellerne er uenige med</div><div class="big">${nf(mx - mn)} m/s</div></div>`
      + `<div>Det laveste bud er ${nf(mn)} m/s og det højeste ${nf(mx)} m/s. Energien i vinden vokser med vindhastigheden i tredje potens, `
      + `så det højeste bud betyder cirka ${more == null ? "meget" : nf(more, 0) + " %"} mere energi end det laveste. `
      + `Derfor er en god vejrprognose penge værd for en park som Horns Rev 1.`
      + (w.stale ? ` <span class="stale">Viser gemte vejrdata.</span>` : "") + `</div>`;
  }
}

/* ------------------------------------------------------------ 2 parken i realtid */
const T = [];
const STAGES = [
  ["fejl", "Fejl opdaget i målingerne", 0],
  ["alarm", "Alarm rejst, vagten får en mail", 4],
  ["stoppet", "Møllen stoppes", 9],
  ["tekniker", "Tekniker sejler ud til møllen", 14],
  ["drift", "Repareret og i drift igen", 24],
];
const SIM_END = 30;
const KINDS = ["vibration over grænsen", "varm gearkasse", "varm generator"];
let incidents = [];
let lastIncident = 0;

function buildPark() {
  const cols = 8, rows = 10, dx = 66, dy = 50, skew = 15;
  let s = `<svg viewBox="0 0 700 560" preserveAspectRatio="xMidYMid meet">`
    + `<text x="12" y="20" font-size="13">Horns Rev 1 set fra oven · 560 m mellem møllerne</text>`;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const n = r * cols + c + 1, x = 70 + c * dx + r * skew, y = 55 + r * dy;
      T.push({ n });
      s += `<g class="t" id="t${n}" transform="translate(${x} ${y})"><circle class="ring" r="17"/>`
        + `<g class="blades"><circle r="15.5" fill="none" stroke="none"/>`
        + `<path d="M-1.8 0 L0 -15 L1.8 0 Z" fill="#dfe8f3"/>`
        + `<path d="M-1.8 0 L0 -15 L1.8 0 Z" fill="#dfe8f3" transform="rotate(120)"/>`
        + `<path d="M-1.8 0 L0 -15 L1.8 0 Z" fill="#dfe8f3" transform="rotate(240)"/></g>`
        + `<circle class="hub" r="2.6"/><text y="27" text-anchor="middle" font-size="9">${n}</text></g>`;
    }
  }
  s += `<g id="windArrow" transform="translate(55 470)"></g></svg>`;
  $("park").innerHTML = s;
}

function turbineFor(id) {
  if (id === "WT-PI") return 1;                       // Pi'en er mølle 1 på kortet
  const m = /^WT-0*(\d+)$/.exec(id || "");
  const n = m ? Number(m[1]) : 0;
  return n >= 1 && n <= PARK.turbines ? n : null;
}

function realAlarms() {
  const map = new Map();
  for (const a of S.alarms) {
    if (a.status !== "raised") continue;
    const n = turbineFor(a.turbine_id);
    if (n && (!map.has(n) || a.priority === "kritisk")) map.set(n, a);
  }
  return map;
}

const stageOf = (inc, now) => { const t = (now - inc.start) / 1000; let st = STAGES[0]; for (const s of STAGES) if (t >= s[2]) st = s; return st; };

function tickSim(now) {
  if (now - lastIncident > 22000 && incidents.length < 2) {
    const busy = new Set([...incidents.map((i) => i.n), ...realAlarms().keys()]);
    const free = T.map((t) => t.n).filter((n) => !busy.has(n));
    const n = free[Math.floor(Math.random() * free.length)];
    incidents.push({ n, start: now, kind: KINDS[Math.floor(Math.random() * KINDS.length)] });
    lastIncident = now;
  }
  incidents = incidents.filter((i) => (now - i.start) / 1000 < SIM_END);
  // Tabt indtjening: stoppede møller × 2 MW × effekt × strømpris, sekund for sekund
  const stopped = incidents.filter((i) => ["stoppet", "tekniker"].includes(stageOf(i, now)[0])).length;
  const w = windNow(), p = priceAt(dkHour());
  if (stopped && w && p != null) S.lostKr += stopped * PARK.ratedMW * powerFrac(w.speed) * p / 3600;
}

function updatePark(now) {
  if (!T.length) return;
  const w = windNow(), v = w ? w.speed : null;
  const running = v != null && v >= PARK.cutIn && v < PARK.cutOut;
  const rpm = running ? 9 + 10 * powerFrac(v) : 0;      // V80 drejer cirka 9-19 omgange i minuttet
  $("park").style.setProperty("--spin", `${(rpm ? 60 / rpm : 4).toFixed(2)}s`);
  const real = realAlarms();
  for (const t of T) {
    const el = $(`t${t.n}`);
    const inc = incidents.find((i) => i.n === t.n);
    const st = inc ? stageOf(inc, now)[0] : "";
    const a = real.get(t.n);
    const stop = !running || st === "stoppet" || st === "tekniker";
    el.setAttribute("class", `t ${st}${a ? " real" + (a.priority === "høj" ? " hoj" : "") : ""}${stop ? " stop" : ""}`);
  }
  if (w) $("windArrow").innerHTML = `<g transform="scale(0.55)">${arrowSVG(w.dir).replace(/^<svg[^>]*>|<\/svg>$/g, "")}</g>`
    + `<text y="40" text-anchor="middle" font-size="11">vind ${nf(v)} m/s</text>`;
}

function renderParkSide() {
  const now = Date.now(), w = windNow(), real = realAlarms();
  const v = w ? w.speed : null, running = v != null && v >= PARK.cutIn && v < PARK.cutOut;
  const stopped = incidents.filter((i) => ["stoppet", "tekniker"].includes(stageOf(i, now)[0])).length;
  const inDrift = running ? PARK.turbines - stopped : 0;
  let html = `<div class="counts"><div><b>${inDrift}</b>i drift</div><div><b>${incidents.length}</b>simulerede fejl</div>`
    + `<div><b class="${real.size ? "crit" : ""}">${real.size}</b>rigtige alarmer</div>`
    + `<div><b>${nf(inDrift * PARK.ratedMW * powerFrac(v), 0)}</b>MW lige nu</div></div>`;
  const inc = incidents[incidents.length - 1];
  if (inc) {
    const t = (now - inc.start) / 1000;
    html += `<h3>Mølle ${inc.n}: ${esc(inc.kind)}</h3><ul class="timeline">`
      + STAGES.map((s, k) => {
        const done = t >= s[2], cur = done && (k === STAGES.length - 1 || t < STAGES[k + 1][2]);
        return `<li class="${cur ? "cur" : done ? "done" : ""}"><i></i><span>${esc(s[1])}</span><span>${done ? `+${s[2]} s` : ""}</span></li>`;
      }).join("") + `</ul>`;
  } else {
    html += `<h3>Alle møller kører</h3><p class="mute">Næste simulerede fejl kommer om lidt.</p>`;
  }
  html += `<h3 style="margin-top:1em">Rigtige alarmer fra alarm-servicen</h3>`;
  const open = S.alarms.filter((a) => a.status === "raised").slice(0, 4);
  html += open.length ? open.map((a) => {
    const n = turbineFor(a.turbine_id);
    return `<div class="alarm ${a.priority === "kritisk" ? "kritisk" : ""}"><div class="top"><b>${esc(a.turbine_id)}${n ? ` · mølle ${n}` : ""}</b>`
      + `<span class="badge ${a.priority === "kritisk" ? "kritisk" : "hoj"}">${esc(a.priority)}</span></div>`
      + `<div class="mute">${esc(a.reason)}</div></div>`;
  }).join("") : `<p class="mute">Ingen åbne alarmer lige nu.</p>`;
  html += `<p class="note">Fejlforløbet er simuleret og skruet ned fra timer til sekunder. Tykke røde og orange ringe er rigtige alarmer. Pi'en er mølle 1.</p>`;
  $("parkSide").innerHTML = html;
}

/* ------------------------------------------------------------ 3 beregneren */
function powerCurveSVG(v) {
  const W = 300, H = 120, X = (s) => 20 + s / 27 * (W - 30), Y = (f) => H - 18 - f * (H - 30);
  let d = "";
  for (let s = 0; s <= 27; s += 0.25) d += `${s ? "L" : "M"}${X(s).toFixed(1)} ${Y(powerFrac(s)).toFixed(1)} `;
  let svg = `<svg viewBox="0 0 ${W} ${H}"><path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>`
    + `<line x1="20" y1="${H - 18}" x2="${W - 10}" y2="${H - 18}" stroke="var(--line)"/>`;
  for (const [s, t] of [[PARK.cutIn, "start 4"], [PARK.rated, "fuld 15"], [PARK.cutOut, "stop 25"]]) {
    svg += `<line x1="${X(s)}" y1="${H - 18}" x2="${X(s)}" y2="${H - 14}" stroke="var(--mute)"/><text x="${X(s)}" y="${H - 4}" text-anchor="middle" font-size="9">${t}</text>`;
  }
  if (v != null) svg += `<circle cx="${X(Math.min(v, 27))}" cy="${Y(powerFrac(v))}" r="5" fill="#fff"/>`;
  return svg + `<text x="20" y="10" font-size="9">effekt</text><text x="${W - 10}" y="${H - 22}" text-anchor="end" font-size="9">m/s</text></svg>`;
}

function renderCalc() {
  const w = windNow(), v = w ? w.speed : null, f = powerFrac(v);
  let verdict = "Venter på vejrdata.";
  if (v != null) {
    if (v < PARK.cutIn) verdict = `Vinden er ${nf(v)} m/s. Møllerne står stille, for de starter først ved ${PARK.cutIn} m/s.`;
    else if (v >= PARK.cutOut) verdict = `Vinden er ${nf(v)} m/s. Det er storm, og møllerne stopper ved ${PARK.cutOut} m/s.`;
    else if (v >= PARK.rated) verdict = `Vinden er ${nf(v)} m/s. Møllerne kører med fuld effekt.`;
    else verdict = `Vinden er ${nf(v)} m/s. Møllerne kører med ${pct(f)} af fuld effekt.`;
  }
  const rows = [4, 6, 8, 10, 12, 15];
  const near = v == null ? -1 : rows.reduce((b, s, k) => (Math.abs(s - v) < Math.abs(rows[b] - v) ? k : b), 0);
  $("calcStart").innerHTML = `<h3>A. Skal møllerne køre?</h3><div class="gauge">${gaugeSVG(f, { label: pct(f), sub: "af fuld effekt" })}</div>`
    + `<p class="verdict ${f > 0 ? "ok" : "warn"}">${esc(verdict)}</p>`
    + `<p class="mute">Hele parken giver ${nf(f * PARK.turbines * PARK.ratedMW, 0)} MW af 160 MW. Energien i vinden vokser med vindhastigheden i tredje potens: dobbelt så meget vind har otte gange så meget energi.</p>`
    + `<div style="display:grid;grid-template-columns:1.3fr 1fr;gap:1em;align-items:center"><div style="height:8em">${powerCurveSVG(v)}</div>`
    + `<table class="minitab"><tr><th>Vind</th><th>Effekt</th></tr>${rows.map((s, k) => `<tr class="${k === near ? "hit" : ""}"><td>${s} m/s</td><td>${nf(powerFrac(s) * 100, 0)} %</td></tr>`).join("")}</table></div>`;

  // B: møllen peger, hvor vinden kom fra for en time siden. Vinklen er, hvor meget vinden har drejet siden.
  const i = nowIndex(), prev = i > 0 ? avgWind(i - 1) : null;
  const theta = w && prev ? angleDiff(w.dir, prev.dir) : null;
  const loss = theta == null ? null : 1 - Math.cos(theta * Math.PI / 180) ** 3;
  const limit = 0.05, turn = loss != null && loss > limit;
  const angles = [0, 5, 10, 15, 20, 30];
  const nearA = theta == null ? -1 : angles.reduce((b, s, k) => (Math.abs(s - theta) < Math.abs(angles[b] - theta) ? k : b), 0);
  const yaw = theta == null ? "" : `<svg viewBox="-60 -60 120 120"><g stroke="var(--accent)" stroke-width="2.5">`
    + [-30, 0, 30].map((x) => `<line x1="${x}" y1="-55" x2="${x}" y2="-30"/>`).join("") + `</g>`
    + `<g transform="rotate(${theta.toFixed(0)})"><rect x="-6" y="-4" width="12" height="30" rx="3" fill="#dfe8f3"/><line x1="-38" y1="-6" x2="38" y2="-6" stroke="#dfe8f3" stroke-width="5" stroke-linecap="round"/></g>`
    + `<text y="55" text-anchor="middle" font-size="10">vinden kommer oppefra · møllen står ${nf(theta, 0)}° skævt</text></svg>`;
  $("calcYaw").innerHTML = `<h3>B. Skal møllen drejes mod vinden?</h3><div class="gauge">${gaugeSVG(loss ?? 0, { label: loss == null ? "…" : nf(loss * 100, 1) + " %", sub: "tabt effekt", color: turn ? "var(--warn)" : "var(--ok)" })}</div>`
    + `<p class="verdict ${turn ? "warn" : "ok"}">${loss == null ? "Venter på vejrdata." : turn ? `Drej møllen. Den taber ${nf(loss * 100, 1)} % af effekten.` : `Ingen grund til at dreje. Tabet er kun ${nf(loss * 100, 1)} %.`}</p>`
    + `<p class="mute">Står møllen skævt, falder effekten med cosinus til vinklen i tredje potens. Her antages det, at møllen peger, hvor vinden kom fra for en time siden. Grænsen for at dreje er 5 % tab, cirka 11°.</p>`
    + `<div style="display:grid;grid-template-columns:1fr 1.2fr;gap:1em;align-items:center"><div style="height:8em">${yaw}</div>`
    + `<table class="minitab"><tr><th>Skævt</th><th>Tab</th></tr>${angles.map((a, k) => `<tr class="${k === nearA ? "hit" : ""}"><td>${a}°</td><td>${nf((1 - Math.cos(a * Math.PI / 180) ** 3) * 100, 1)} %</td></tr>`).join("")}</table></div>`;
}

/* ------------------------------------------------------------ 4 strømpris og regnskab */
function renderPrices() {
  const P = S.prices;
  if (!P || !P.hours || !P.hours.length) { $("priceChart").innerHTML = msg("prices"); $("ledger").innerHTML = ""; return; }
  const hrs = P.hours, nowKey = dkHour();
  const vals = hrs.map((h) => h.price / 1000);
  const lo = Math.min(0, ...vals), hi = Math.max(...vals, 0.1);
  const W = 1000, H = 260, L = 50, B = 26, bw = (W - L - 10) / hrs.length;
  const Y = (x) => 10 + (hi - x) / (hi - lo) * (H - B - 10);
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
  const step = hi - lo > 2 ? 1 : hi - lo > 1 ? 0.5 : 0.25;
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
    svg += `<line x1="${L}" x2="${W - 10}" y1="${Y(t)}" y2="${Y(t)}" stroke="var(--line)"/><text x="${L - 6}" y="${Y(t) + 4}" text-anchor="end" font-size="12">${nf(t, 2)}</text>`;
  }
  hrs.forEach((h, k) => {
    const x = L + k * bw, v = h.price / 1000, past = h.hour < nowKey, cur = h.hour === nowKey;
    const color = v < 0 ? "var(--crit)" : cur ? "var(--accent)" : past ? "#3a4f6b" : "#5d7ea3";
    svg += `<rect x="${(x + 1).toFixed(1)}" y="${Math.min(Y(v), Y(0)).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${Math.abs(Y(v) - Y(0)).toFixed(1)}" fill="${color}"/>`;
    const hh = h.hour.slice(11, 13);
    if (hh % 3 === 0) svg += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="12">${hh}</text>`;
    if (hh === "00" && k > 0) svg += `<line x1="${x}" x2="${x}" y1="6" y2="${H - B}" stroke="var(--mute)" stroke-dasharray="4 4"/><text x="${x + 4}" y="20" font-size="12">i morgen</text>`;
  });
  svg += `</svg>`;
  const neg = hrs.filter((h) => h.price < 0 && h.hour >= nowKey).length;
  $("priceChart").innerHTML = `<h3>Strømprisen i DK1 time for time (kr. pr. kWh)</h3><div style="height:calc(100% - 5em)">${svg}</div>`
    + `<p class="note">${neg ? `${neg} kommende timer har negativ pris. Så betaler det sig at stoppe møllerne.` : "Ingen negative priser i de kommende timer."} Kilde: Energi Data Service, day-ahead-priser.</p>`;

  // Regnskab for i dag ud fra vindprognosen og prisen time for time
  let mwh = 0, kr = 0;
  const today = nowKey.slice(0, 10), minuteFrac = new Date().getMinutes() / 60;
  if (S.weather && S.weather.time) {
    S.weather.time.forEach((t, i) => {
      const key = t.slice(0, 13);
      if (key.slice(0, 10) !== today || key > nowKey) return;
      const w = avgWind(i), p = priceAt(key);
      const share = key === nowKey ? minuteFrac : 1;
      const e = PARK.turbines * PARK.ratedMW * powerFrac(w ? w.speed : null) * share;
      mwh += e;
      if (p != null) kr += e * p;
    });
  }
  const w = windNow(), pNow = priceAt(nowKey);
  const perStop = w && pNow != null ? PARK.ratedMW * powerFrac(w.speed) * pNow : null;
  const stat = (t, v, s) => `<div class="panel"><h3>${t}</h3><div class="big">${v}</div><p class="mute">${s}</p></div>`;
  $("ledger").innerHTML = stat("Produceret i dag", `${nf(mwh, 0)} MWh`, "Anslået fra vindprognosen og møllens effektkurve.")
    + stat("Indtjening i dag", `${nf(kr / 1e6, 2)} mio. kr.`, "Produktion gange strømprisen time for time.")
    + stat("Et stop koster", perStop == null ? "…" : `${nf(perStop, 0)} kr/t`, `Én stoppet mølle lige nu. Simulerede stop har kostet ${nf(S.lostKr, 0)} kr., siden skærmen startede.`)
    + stat("CO₂ sparet i dag", `${nf(mwh * PARK.co2PerMWh, 0)} t`, "Antaget 0,37 t CO₂ pr. MWh, som fra et gaskraftværk.");
}

/* ------------------------------------------------------------ 5 alarmcentralen */
function dueText(iso) {
  const ms = new Date(iso) - Date.now(), abs = Math.abs(ms), h = Math.floor(abs / 3.6e6), m = Math.floor(abs % 3.6e6 / 6e4);
  const t = h ? `${h} t ${m} min` : `${m} min`;
  return ms >= 0 ? `<span class="ok">${t} til svarfristen</span>` : `<span class="crit">Svarfristen er overskredet med ${t}</span>`;
}

function renderAlarms() {
  const FLOW = ["SensorValueReceived", "AnomalyDetected", "AlarmRaised", "NotificationSent", "AlarmAcknowledged"];
  const count = (t) => S.events.filter((e) => e.event_type === t).length;
  $("flow").innerHTML = FLOW.map((t, k) => `${k ? `<span class="arr">→</span>` : ""}<span class="sticky">${t}<small>× ${count(t)}</small></span>`).join("")
    + `<span class="arr" style="margin-left:1em">eller</span><span class="sticky" style="background:#6fa8ff">SensorFaultSuspected<small>× ${count("SensorFaultSuspected")}</small></span>`
    + `<span class="mute" style="margin-left:auto">De seneste ${S.events.length} events · opdateres hvert 5. sekund</span>`;

  const open = S.alarms.filter((a) => a.status === "raised")
    .sort((a, b) => (a.priority === b.priority ? (b.raised_at > a.raised_at ? 1 : -1) : a.priority === "kritisk" ? -1 : 1));
  let html = `<h3>Åbne alarmer (${open.length})</h3>`;
  if (S.errors.alarms) html += msg("alarms");
  html += open.length ? open.slice(0, 6).map((a) => `<div class="alarm ${a.priority === "kritisk" ? "kritisk" : ""}">`
    + `<div class="top"><b>${esc(a.turbine_id)} · ${esc(SENSOR_NAMES[a.sensor_type] || a.sensor_type)}</b><span class="badge ${a.priority === "kritisk" ? "kritisk" : "hoj"}">${esc(a.priority)}</span></div>`
    + `<div>${nf(a.value)} ${esc(UNITS[a.sensor_type] || "")} · grænse ${nf(a.threshold)} · rejst ${dkTime(a.raised_at)}</div>`
    + `<div>${dueText(a.response_due_at)}</div></div>`).join("")
    : `<p class="mute">Ingen åbne alarmer. Ryst Pi'en, eller tryk joysticket ind, så kommer der en.</p>`;
  $("openAlarms").innerHTML = html;

  // Pi'ens seneste målinger
  const last = {};
  for (const e of S.events) {
    if (e.turbine_id === "WT-PI" && e.event_type === "SensorValueReceived" && e.payload && !last[e.payload.sensor_type]) last[e.payload.sensor_type] = e;
  }
  const newest = Object.values(last).sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1))[0];
  const age = newest ? Math.round((Date.now() - new Date(newest.occurred_at)) / 1000) : null;
  let feed = `<h3>Live fra alarm-servicen i Azure</h3><div class="pi"><b>Pi'en (WT-PI)</b>`;
  feed += newest ? Object.entries(last).map(([k, e]) => `<span>${esc(SENSOR_NAMES[k] || k)} <b>${nf(e.payload.value)} ${esc(UNITS[k] || "")}</b></span>`).join("")
    + `<span class="${age > 30 ? "warn" : "mute"}">for ${age < 120 ? age + " s" : Math.round(age / 60) + " min"} siden</span>` : `<span class="mute">ingen målinger i de seneste events</span>`;
  feed += `</div>`;
  if (S.errors.events) feed += msg("events");
  feed += `<ul class="feed">` + S.events.slice(0, 16).map((e) => {
    const isNew = !S.firstFeed && !S.seen.has(e.id);
    const p = e.payload || {};
    const what = p.sensor_type ? `${SENSOR_NAMES[p.sensor_type] || p.sensor_type} ${nf(p.value)} ${UNITS[p.sensor_type] || ""}`
      : p.alarm_id ? `alarm ${p.alarm_id}${p.priority ? " · " + p.priority : ""}${p.acknowledged_by ? " · " + p.acknowledged_by : ""}` : "";
    return `<li class="${isNew ? "new" : ""}"><span class="mute">${dkTime(e.occurred_at)}</span><span class="etype ${esc(e.event_type)}">${esc(e.event_type)}</span>`
      + `<span>${esc(e.turbine_id)}</span><span class="mute">${esc(what)}</span></li>`;
  }).join("") + `</ul>`;
  $("feedPanel").innerHTML = feed;
  for (const e of S.events) S.seen.add(e.id);
  if (S.events.length) S.firstFeed = false;
}

/* ------------------------------------------------------------ 6 serviceprognose */
function mulberry32(a) {
  return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

let PROG = null;
function buildPrognosis(limit) {
  const DAYS = 60, out = [];
  for (let n = 1; n <= PARK.turbines; n++) {
    const r = mulberry32(n * 7919 + 17);
    const base = 48 + r() * 12, slope = (r() ** 3) * 0.36 - 0.02, jump = r() < 0.06 ? 0.15 : 0;
    const hist = [];
    for (let d = 0; d < DAYS; d++) {
      const noise = (r() + r() + r() - 1.5) * 1.6;
      hist.push(base + slope * d + (d > 40 ? jump * (d - 40) : 0) + noise);
    }
    // Lineær trend på de seneste 30 dage (mindste kvadraters metode)
    const xs = [...Array(30).keys()].map((k) => DAYS - 30 + k), ys = xs.map((x) => hist[x]);
    const mx = xs.reduce((a, b) => a + b) / 30, my = ys.reduce((a, b) => a + b) / 30;
    const b = xs.reduce((a, x, k) => a + (x - mx) * (ys[k] - my), 0) / xs.reduce((a, x) => a + (x - mx) ** 2, 0);
    const a = my - b * mx, fit = a + b * (DAYS - 1);
    const days = fit >= limit ? 0 : b > 0.01 ? (limit - fit) / b : Infinity;
    out.push({ n, hist, a, b, fit, days });
  }
  return out.sort((x, y) => x.days - y.days);
}

function renderPrognosis() {
  const limit = (S.rules && S.rules.gearbox_temperature && S.rules.gearbox_temperature.warning) || 80;
  if (!PROG || PROG.limit !== limit) PROG = { limit, list: buildPrognosis(limit) };
  const list = PROG.list, soon = list.filter((t) => t.days <= 30).length;
  $("progLead").textContent = `Hvornår skal møllerne til service? ${soon} møller rammer advarselsgrænsen på ${limit} °C inden for 30 dage, hvis tendensen fra de seneste 30 dage fortsætter.`;
  const rows = list.slice(0, 10).map((t) => {
    const cls = t.days <= 14 ? "crit" : t.days <= 30 ? "warn" : "ok";
    const when = t.days === Infinity ? "ikke inden for et år" : t.days === 0 ? "nu"
      : new Date(Date.now() + t.days * 864e5).toLocaleDateString("da-DK", { day: "numeric", month: "short" });
    const horizon = Math.min(Math.ceil(t.days === Infinity ? 30 : t.days) + 5, 60);
    const proj = [...t.hist.map(() => null)];
    const series = [...t.hist, ...Array(horizon).fill(null)];
    const projSeries = [...proj, ...Array(horizon).fill(null)];
    projSeries[t.hist.length - 1] = t.fit;
    for (let k = 1; k <= horizon; k++) projSeries[t.hist.length - 1 + k] = t.a + t.b * (t.hist.length - 1 + k);
    const all = [...t.hist, limit, ...projSeries.filter((v) => v != null)];
    const lo = Math.min(...all) - 1, hi = Math.max(...all) + 1, h = 40, wv = 300;
    const yL = (h - 3 - (limit - lo) / (hi - lo) * (h - 6)).toFixed(1);
    const extra = `<line x1="0" x2="${wv}" y1="${yL}" y2="${yL}" stroke="var(--crit)" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`;
    const histSvg = sparkline(series, { w: wv, h, min: lo, max: hi, extra });
    const projPath = sparkline(projSeries, { w: wv, h, min: lo, max: hi, color: "var(--warn)" });
    return `<tr><td><b>Mølle ${t.n}</b></td><td>${nf(t.fit)} °C</td><td>${t.b >= 0 ? "+" : ""}${nf(t.b, 2)} °C/dag</td>`
      + `<td class="days ${cls}">${t.days === Infinity ? "…" : nf(t.days, 0) + " dage"}</td><td>${when}</td>`
      + `<td class="spark" style="position:relative"><div style="position:absolute;inset:0">${histSvg}</div><div style="position:absolute;inset:0">${projPath}</div></td></tr>`;
  }).join("");
  $("prognosis").innerHTML = `<table class="plist"><tr><th>Mølle</th><th>Nu</th><th>Tendens</th><th>Når grænsen om</th><th>Dato</th><th>60 dage og prognose (rød streg = ${limit} °C)</th></tr>${rows}</table>`;
}

/* ------------------------------------------------------------ skift mellem skærme */
const SCREENS = ["s1", "s2", "s3", "s4", "s5", "s6"];
const RENDER = [renderWeather, renderParkSide, renderCalc, renderPrices, renderAlarms, renderPrognosis];

function render() {
  try { RENDER[S.screen](); } catch (e) { console.error(e); }
}

function show(k) {
  S.screen = (k + SCREENS.length) % SCREENS.length;
  S.shownAt = Date.now();
  SCREENS.forEach((id, j) => $(id).classList.toggle("on", j === S.screen));
  $("title").textContent = $(SCREENS[S.screen]).dataset.title;
  [...$("dots").children].forEach((b, j) => b.classList.toggle("on", j === S.screen));
  render();
}

function init() {
  const params = new URLSearchParams(location.search);
  if (params.get("rotate") === "0") S.paused = true;
  $("dots").innerHTML = SCREENS.map((id, j) => `<button data-k="${j}">${j + 1} ${esc($(id).dataset.title)}</button>`).join("");
  $("dots").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) show(Number(b.dataset.k)); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") show(S.screen + 1);
    else if (e.key === "ArrowLeft") show(S.screen - 1);
    else if (e.key === " ") { S.paused = !S.paused; S.shownAt = Date.now(); e.preventDefault(); }
    else if (/^[1-6]$/.test(e.key)) show(Number(e.key) - 1);
  });
  buildPark();
  show(Math.max(0, (Number(params.get("screen")) || 1) - 1));
  tickClock();

  loadRules(); loadWeather(); loadPrices(); loadLive();
  setInterval(loadWeather, 10 * 60 * 1000);
  setInterval(loadPrices, 15 * 60 * 1000);
  setInterval(loadLive, 5000);
  setInterval(() => { if (!S.rules) loadRules(); }, 10 * 60 * 1000);

  setInterval(() => {               // hvert sekund: ur, simulation, kort og nedtællinger
    const now = Date.now();
    tickClock();
    tickSim(now);
    updatePark(now);
    if (S.screen === 1) renderParkSide();
    if (S.screen === 4) renderAlarms();
    if (S.screen === 3) renderPrices();
  }, 1000);

  setInterval(() => {               // fremdriftslinjen og automatisk skift
    const el = Date.now() - S.shownAt;
    $("progress").firstElementChild.style.width = S.paused ? "0" : `${Math.min(100, el / SCREEN_MS * 100)}%`;
    if (!S.paused && el >= SCREEN_MS) show(S.screen + 1);
  }, 200);
}

init();
