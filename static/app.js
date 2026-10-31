// Kitchen monitor frontend. Stateless: everything comes from the server.
// Home view polls /api/state; tapping a panel opens a detail view (#/energy,
// #/seerr, #/todo...) which fetches what it needs. Idle -> back to home.
'use strict';

const POLL_MS = 10_000;
const IDLE_MS = 90_000;
const NIGHT_WAKE_MS = 120_000;

const $ = (id) => document.getElementById(id);
// Phones get a scrolling one-column layout (style.css), the system keyboard, and
// none of the kiosk behaviour (idle return to home, night clock).
const PHONE = window.matchMedia('(max-width: 760px)');
let state = null;
let build = null;
let tz = 'Europe/London';
let lastOk = null;
let offline = false;
let lastTouch = Date.now();
let nightWakeUntil = 0;
let clockOffset = 0; // server clock minus ours, so timers agree across screens
const serverNow = () => Date.now() + clockOffset;

// --- formatting ----------------------------------------------------------------

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const dtf = (opts) => new Intl.DateTimeFormat('en-GB', { timeZone: tz, ...opts });
const fmtTime = (iso) => dtf({ hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
const fmtDay = (d) => dtf({ weekday: 'short', day: 'numeric', month: 'short' }).format(d);
const isoDate = (d) => dtf({ year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(d)
  .reduce((o, p) => ({ ...o, [p.type]: p.value }), {});
const ymd = (d) => { const p = isoDate(d); return `${p.year}-${p.month}-${p.day}`; };
const p1 = (v) => (v == null ? '–' : v.toFixed(1));
const gbp = (pence) => (pence == null ? '–' : `£${(pence / 100).toFixed(2)}`);
const BAND_LABEL = { plunge: 'Plunge', cheap: 'Cheap', normal: 'Normal', expensive: 'Pricey' };
const bandColor = (b) => `var(--${b || 'line'})`;
// Date-only strings ("2026-10-08") are calendar days: parse at noon UTC so any tz shows the same day.
const dayDate = (s) => new Date(`${s}T12:00:00Z`);

function ago(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function poster(letterFrom, src) {
  const letter = esc((letterFrom || '?').trim().charAt(0).toUpperCase());
  const img = src ? `<img src="${esc(src)}" alt="" loading="lazy" onerror="this.remove()">` : '';
  return `<div class="poster">${letter}${img}</div>`;
}

// --- bar chart (plain SVG) -------------------------------------------------------

function niceStep(range, n) {
  const raw = range / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
}

function barPath(x, base, tip, w, r) {
  const h = Math.abs(tip - base);
  r = Math.min(r, w / 2, h);
  const d = tip < base ? r : -r; // round the end away from the baseline
  return `M${x},${base}V${tip + d}Q${x},${tip} ${x + r},${tip}H${x + w - r}Q${x + w},${tip} ${x + w},${tip + d}V${base}Z`;
}

/**
 * items: array; o.value(d) -> number; o.color(d) -> css colour
 * o.xLabel(d,i) -> string|null; o.divider(d,i) -> string|null; o.past(d) -> bool
 * o.lines: [{v, label}] dashed reference lines; o.axis: show y ticks
 * o.selected: index; o.onSelect(i)
 */
function barChart(el, items, o) {
  const W = el.clientWidth, H = el.clientHeight;
  if (!W || !H || !items.length) { el.innerHTML = items.length ? '' : '<div class="empty">No data</div>'; return; }
  const axis = o.axis !== false;
  const padL = axis ? 34 : 2, padR = 2, padT = o.divider ? 20 : axis ? 10 : 4, padB = 20;
  const vals = items.map(o.value).map((v) => v ?? 0);
  const refs = (o.lines || []).map((l) => l.v);
  let hi = Math.max(...vals, ...refs, 0), lo = Math.min(...vals, 0);
  const step = niceStep(hi - lo || 1, axis ? 4 : 2);
  hi = Math.ceil(hi / step) * step || step;
  lo = Math.floor(lo / step) * step;
  const y = (v) => padT + ((hi - v) / (hi - lo)) * (H - padT - padB);
  const bw = (W - padL - padR) / items.length;
  const gap = bw > 8 ? 2 : bw > 4 ? 1 : 0;
  const parts = [];

  if (axis) {
    for (let v = lo; v <= hi + 1e-9; v += step) {
      parts.push(`<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"/>`);
      parts.push(`<text x="${padL - 4}" y="${y(v) + 4}" text-anchor="end">${+v.toFixed(1)}</text>`);
    }
  } else {
    parts.push(`<line class="grid" x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}"/>`);
  }
  for (const l of o.lines || []) {
    parts.push(`<line class="thresh" x1="${padL}" x2="${W - padR}" y1="${y(l.v)}" y2="${y(l.v)}"/>`);
  }
  items.forEach((d, i) => {
    const x = padL + i * bw;
    const v = vals[i];
    if (v !== 0) {
      const cls = o.past && o.past(d) ? ' class="past"' : '';
      parts.push(`<path${cls} d="${barPath(x + gap / 2, y(0), y(v), bw - gap, 4)}" style="fill:${o.color(d)}"/>`);
    }
    const lab = o.xLabel && o.xLabel(d, i);
    if (lab) parts.push(`<text x="${x + bw / 2}" y="${H - 4}" text-anchor="middle">${esc(lab)}</text>`);
    const div = o.divider && o.divider(d, i);
    if (div) {
      parts.push(`<line class="divider" x1="${x}" x2="${x}" y1="${padT - 4}" y2="${H - padB}"/>`);
      parts.push(`<text x="${x + 4}" y="${padT - 5}">${esc(div)}</text>`);
    }
  });
  if (o.selected != null && items[o.selected]) {
    const x = padL + o.selected * bw;
    parts.push(`<rect class="sel" x="${x}" y="${padT}" width="${bw}" height="${H - padT - padB}" rx="3"/>`);
  }
  if (o.onSelect) {
    items.forEach((_, i) => parts.push(`<rect class="hit" data-i="${i}" x="${padL + i * bw}" y="0" width="${bw}" height="${H}"/>`));
  }
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${parts.join('')}</svg>`;
  if (o.onPreview) return scrub(el.firstChild, items.length, { padL, padT, bw, W, H, padB }, o);
  if (o.onSelect) {
    el.firstChild.addEventListener('pointerdown', (e) => {
      const hit = e.target.closest('.hit');
      if (hit) o.onSelect(+hit.dataset.i);
    });
  }
}

// Press and slide along the bars to preview (outline + o.onPreview(i)); let go to
// pick (o.onSelect). Sliding up/down still scrolls the page and cancels.
function scrub(svg, n, g, o) {
  const NS = 'http://www.w3.org/2000/svg';
  const sel = document.createElementNS(NS, 'rect');
  sel.setAttribute('class', 'sel');
  sel.setAttribute('rx', 3);
  sel.setAttribute('y', g.padT);
  sel.setAttribute('width', g.bw);
  sel.setAttribute('height', g.H - g.padT - g.padB);
  sel.style.display = 'none';
  svg.appendChild(sel);
  svg.style.touchAction = 'pan-y';
  let cur = null;
  const at = (e) => {
    const r = svg.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * g.W;
    return Math.max(0, Math.min(n - 1, Math.floor((x - g.padL) / g.bw)));
  };
  const show = (i) => {
    cur = i;
    sel.style.display = i == null ? 'none' : '';
    if (i != null) sel.setAttribute('x', g.padL + i * g.bw);
    o.onPreview(i);
  };
  svg.addEventListener('pointerdown', (e) => { svg.setPointerCapture(e.pointerId); show(at(e)); });
  svg.addEventListener('pointermove', (e) => { if (cur != null) { const i = at(e); if (i !== cur) show(i); } });
  svg.addEventListener('pointerup', () => { const i = cur; if (i != null) { cur = null; o.onSelect(i); } });
  svg.addEventListener('pointercancel', () => show(null));
}

// x labels every 3 hours, divider at midnight
const priceAxis = (slots) => ({
  xLabel: (d) => {
    const t = new Date(d.start);
    const p = dtf({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(t);
    return p.endsWith(':00') && +p.slice(0, 2) % 3 === 0 && slots.length > 12 ? p.slice(0, 2) : null;
  },
  divider: (d, i) => (i > 0 && fmtTime(d.start) === '00:00' ? fmtDay(new Date(d.start)) : null),
});

function cheapest(slots, n) {
  let best = null;
  for (let i = 0; i + n <= slots.length; i++) {
    const avg = slots.slice(i, i + n).reduce((a, s) => a + s.price, 0) / n;
    if (!best || avg < best.avg) best = { avg, start: slots[i].start, end: slots[i + n - 1].end };
  }
  return best;
}

function tile(label, value, sub, band, big) {
  return `<div class="tile ${band ? `band-${band}` : ''} ${big ? 'big' : ''}">
    <div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub || '&nbsp;'}</div></div>`;
}
const priceVal = (p) => `${p1(p)}<small>p</small>`;
// "a bit pricier than usual" etc: a day's average against the usual (30-day) average
function verdict(avg, usual) {
  if (avg == null || usual == null || usual <= 0) return null;
  const pct = Math.round(((avg - usual) / usual) * 100);
  const text = pct >= 25 ? 'much pricier than usual' : pct >= 10 ? 'a bit pricier than usual'
    : pct <= -25 ? 'much cheaper than usual' : pct <= -10 ? 'cheaper than usual' : 'about usual';
  const cls = pct >= 10 ? 'hi' : pct <= -10 ? 'lo' : 'mid';
  return { text, cls, pct, html: `<span class="verdict ${cls}">${text}</span>` };
}
const vsUsual = (v, usual, what = 'usual') => (v ? `${v.pct > 0 ? '+' : ''}${v.pct}% vs ${what} ${p1(usual)}p` : '');
const bandSub = (band, extra) => `<span class="dot ${band}"></span>${BAND_LABEL[band] || ''}${extra ? ` · ${extra}` : ''}`;

// --- home ----------------------------------------------------------------------

function renderHome() {
  const e = state.energy;
  if (!e.enabled) {
    $('energy-tiles').innerHTML = tile('Octopus', '–', 'Not configured');
    $('energy-chart').innerHTML = '';
    $('usage-strip').innerHTML = '';
  } else {
    const t = [];
    t.push(e.current
      ? tile('Now', priceVal(e.current.price), bandSub(e.current.band, `to ${fmtTime(e.current.end)}`), e.current.band, true)
      : tile('Now', '–', 'Waiting for prices'));
    if (e.next) t.push(tile('Next', priceVal(e.next.price), bandSub(e.next.band, fmtTime(e.next.start)), e.next.band));
    for (const w of (e.windows || []).slice(0, 4 - t.length)) {
      t.push(tile(`Cheapest ${w.hours}h`, priceVal(w.price), `${fmtTime(w.start)}–${fmtTime(w.end)}`, w.band));
    }
    $('energy-tiles').innerHTML = t.join('');

    const chart = e.chart || [];
    const curStart = e.current && e.current.start;
    barChart($('energy-chart'), chart, {
      value: (d) => d.price,
      color: (d) => bandColor(d.band),
      past: (d) => curStart && d.start < curStart,
      lines: [{ v: e.thresholds.cheap }, { v: e.thresholds.expensive }],
      selected: chart.findIndex((d) => d.start === curStart), // outline the current half hour
      ...priceAxis(chart),
    });
    const hour = +dtf({ hour: '2-digit', hourCycle: 'h23' }).format(new Date());
    const tv = e.today && verdict(e.today.avg, e.typical_avg);
    $('energy-readout').innerHTML = e.today
      ? `Today ${p1(e.today.min)} / ${p1(e.today.avg)} / ${p1(e.today.max)}p` + (tv ? ` · ${tv.html}` : '') +
        (!e.tomorrow_available && hour >= 12 ? ' · tomorrow ~4pm' : '')
      : '';

    const shown = (e.appliances || []).filter((a) => a.best !== false);
    $('energy-advice').innerHTML = adviceRows(shown);
    $('energy-advice').classList.toggle('compact', shown.length > 2); // two columns, shorter text
    renderUsageStrip(e.usage || []);
  }
  renderTodoHome();
}

// "Dishwasher eco in 6h" - delay to set on the appliance for its cheapest run.
function adviceRows(list, all = false) {
  return list.map((a) => {
    if (!a.start) {
      return `<div class="advice-row"><span class="head">${esc(a.name)}</span>
        <span class="det">${a.partial ? "won't fit before tonight's prices end - tomorrow's due ~4pm" : 'no price data'}</span></div>`;
    }
    // "Finish in" machines (Siemens washers): say what to set on the timer
    const when = a.delay_h === 0 ? '<b>start now</b>'
      : a.finish ? `<span class="how">Finish in</span> <b>${fmtDelay(a.finish_in_h)}</b>` : `in <b>${fmtDelay(a.delay_h)}</b>`;
    const vs = a.now_avg != null && a.delay_h > 0 ? ` vs ${p1(a.now_avg)}p now` : '';
    const save = a.saving_p > 0.5 ? ` · save ${a.saving_p.toFixed(0)}p` : '';
    const caveat = a.partial ? ' · may change at 4pm' : '';
    // "beats eco 11.5p": the other programs of the same machine, by their own word
    // by the cost of a run when kWh are configured, else by average price
    const beats = (a.beats || []).map((b) => `${esc(b.name.split(' ').slice(1).join(' ') || b.name)} ${b.run_p != null ? `${b.run_p.toFixed(0)}p a run` : `${p1(b.avg)}p`}`);
    const run = a.run_p != null && a.beats && a.beats.length ? ` · ${a.run_p.toFixed(0)}p a run` : '';
    const alt = beats.length ? ` · beats ${beats.join(', ')}` : '';
    const tag = all && a.best && (a.beats || []).length ? ' <span class="pill best">best</span>' : '';
    return `<div class="advice-row band-${a.band}${all && a.best === false ? ' alt' : ''}"><span class="head">${esc(a.name)} ${when}${tag}</span>
      <span class="det">${fmtTime(a.start)}–${fmtTime(a.end)} · ${p1(a.avg)}p avg${run}${alt}${vs}${save}${caveat}</span>
      <span class="short">${fmtTime(a.start)}–${fmtTime(a.end)} · ${p1(a.avg)}p${a.saving_p > 0.5 ? ` · save ${a.saving_p.toFixed(0)}p` : ''}</span></div>`;
  }).join('');
}
const fmtDelay = (h) => (Number.isInteger(h) ? `${h}h` : `${Math.floor(h)}h${String(Math.round((h % 1) * 60)).padStart(2, '0')}`);

function renderUsageStrip(days) {
  const el = $('usage-strip');
  const last = [...days].reverse().find((d) => d.complete) || days[days.length - 1];
  if (!last) { el.innerHTML = tile('Usage', '–', 'No readings yet'); return; }
  const yest = ymd(new Date(Date.now() - 86400e3));
  const label = last.date === yest ? 'Yesterday' : fmtDay(dayDate(last.date));
  el.innerHTML =
    tile(label, `${last.kwh.toFixed(1)}<small>kWh</small>`, last.complete ? 'all readings in' : 'partial day') +
    tile('Cost', gbp(last.cost_p), 'incl. standing') +
    tile('Avg price', priceVal(last.avg_price), 'per kWh used') +
    `<div class="tile"><div class="label">Last ${days.length} days, £</div><div class="mini" style="height:58px"></div></div>`;
  const mini = el.querySelector('.mini');
  barChart(mini, days, {
    axis: false,
    value: (d) => d.cost_p / 100,
    color: () => 'var(--accent)',
    xLabel: (d) => dtf({ weekday: 'narrow' }).format(dayDate(d.date)),
  });
}

function renderStatus() {
  const chips = [];
  if (state && state.demo) chips.push('<span class="chip">Demo</span>');
  if (offline) chips.push(`<span class="chip err">Server offline${lastOk ? ` · since ${fmtTime(lastOk)}` : ''}</span>`);
  if (state) {
    const names = { energy: 'Octopus', seerr: 'Seerr', weather: 'Weather' };
    for (const [k, label] of Object.entries(names)) {
      if (state[k].error) chips.push(`<span class="chip warn" title="${esc(state[k].error)}">${label} ⚠</span>`);
    }
    if (state.todos && state.todos.error) chips.push(`<span class="chip warn" title="${esc(state.todos.error)}">To-do not saved ⚠</span>`);
  }
  $('status').innerHTML = chips.join('');
  renderTopChips();
}

// Weather + NAS chips in the top bar; both open their detail views.
function renderTopChips() {
  const w = state && state.weather;
  let html = '';
  if (w && w.current) {
    const next = w.rain_at ? `rain ${fmtTime(w.rain_at)}` : w.drying && w.current.day ? 'drying day' : 'dry';
    html += `<button class="tchip wx" data-href="#/weather">${wxIcon(w.current.icon, 20)}<b>${Math.round(w.current.temp)}°</b><span>${next}</span></button>`;
  }
  if (state && state.features && state.features.wifi) {
    html += `<button class="tchip wifi" data-href="#/wifi">${WIFI_ICON}<b class="tlabel">Wi-Fi</b></button>`;
  }
  const s = state && state.seerr;
  if (s && s.enabled) {
    const pending = (s.counts || {}).pending;
    html += `<button class="tchip req" data-href="#/seerr">${FILM_ICON}<b class="tlabel">Requests</b>${pending ? `<span class="badge">${pending}</span>` : ''}</button>`;
  }
  const n = state && state.nas;
  if (n && (n.enabled || n.error)) {
    const st = n.error && !n.state ? 'err' : n.state || 'err';
    const label = st === 'ok' ? 'NAS' : st === 'warn' ? 'NAS !' : 'NAS ✕';
    html += `<button class="tchip nas ${st}" data-href="#/nas"><i class="led"></i>${label}</button>`;
  }
  $('top-chips').innerHTML = html;
}

const WIFI_ICON = '<svg class="wxi" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 8.5a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M8.5 15.5a5 5 0 0 1 7 0"/><circle cx="12" cy="19" r="1.2" fill="currentColor" stroke="none"/></svg>';
const FILM_ICON = '<svg class="wxi" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>';

// Guest Wi-Fi: a QR code phones can scan to join, plus the details for typing them in.
function renderWifi(w) {
  $('detail-body').innerHTML = `<div class="card wifi-card">
    <img class="wifi-qr" src="/api/wifi/qr.svg" alt="Wi-Fi QR code">
    <div class="wifi-info">
      <p class="note">Point your phone camera at the code to join</p>
      <div><span class="note">Network</span><b>${esc(w.ssid)}</b></div>
      ${w.password ? `<div><span class="note">Password</span><b class="wifi-pw">${esc(w.password)}</b></div>` : '<div><span class="note">No password</span></div>'}
    </div></div>`;
}

// Small weather icons as inline SVG (the Pi's fonts have no emoji).
function wxIcon(name, size = 24) {
  const sun = '<circle cx="12" cy="12" r="4.5" fill="#fab219" stroke="none"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" stroke="#fab219"/>';
  const cloud = (y = 0, fill = '#c3c2b7') => `<path d="M7 ${18 + y}h10a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.5A3.3 3.3 0 0 0 7 ${18 + y}z" fill="${fill}" stroke="none"/>`;
  const shapes = {
    clear: sun,
    night: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" fill="#c3c2b7" stroke="none"/>',
    partly: '<g transform="translate(-3,-3) scale(.8)">' + sun + '</g>' + cloud(1),
    cloud: cloud(),
    rain: cloud(-3) + '<path d="M8 19l-1 3M12 19l-1 3M16 19l-1 3" stroke="#3987e5"/>',
    snow: cloud(-3) + '<path d="M8 20h.01M12 21h.01M16 20h.01" stroke="#f0efec" stroke-width="3"/>',
    storm: cloud(-3) + '<path d="M12 15l-2 4h3l-2 4" stroke="#fab219"/>',
    fog: '<path d="M4 9h16M6 13h12M4 17h16" stroke="#c3c2b7"/>',
  };
  return `<svg class="wxi" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">${shapes[name] || shapes.cloud}</svg>`;
}

// --- detail views ------------------------------------------------------------

const VIEWS = {
  energy: { title: 'Energy', tabs: [['today', 'Today'], ['tomorrow', 'Tomorrow'], ['usage', 'Usage']] },
  todo: { title: 'To do', tabs: [['list', 'List']] },
  seerr: { title: 'Requests', tabs: [['pending', 'Pending'], ['processing', 'Processing'], ['available', 'Available'], ['all', 'All'], ['search', 'Search'], ['trending', 'Trending']] },
  weather: { title: 'Weather', tabs: [['forecast', 'Forecast']] },
  nas: { title: 'NAS', tabs: [['health', 'Health']] },
  timers: { title: 'Timers', tabs: [['timers', 'Timers']] },
  wifi: { title: 'Guest Wi-Fi', tabs: [['qr', 'QR']] },
};

const route = () => {
  const [view, tab, arg] = location.hash.replace(/^#\/?/, '').split('/');
  return VIEWS[view] ? { view, tab: tab || VIEWS[view].tabs[0][0], arg } : { view: 'home' };
};

let renderSeq = 0; // drop responses from views the user already left

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  return r.json();
}

function showRoute() {
  const r = route();
  const home = r.view === 'home';
  $('view-home').hidden = !home;
  $('view-detail').hidden = home;
  $('back').hidden = home;
  $('view-title').textContent = home ? '' : VIEWS[r.view].title;
  closeSheet();
  if (r.view !== 'todo') { todoKeyboard = false; todoText = ''; }
  if (home) { if (state) renderHome(); return; }
  $('tabs').hidden = VIEWS[r.view].tabs.length < 2;
  $('tabs').innerHTML = VIEWS[r.view].tabs.map(([k, l]) =>
    `<button class="tab ${k === r.tab ? 'on' : ''}" data-href="#/${r.view}/${k}">${l}</button>`).join('');
  $('detail-body').scrollTop = 0;
  renderDetail(r);
}

function renderDetail(r = route(), quiet = false) {
  const body = $('detail-body');
  const seq = ++renderSeq;
  const guard = (fn) => (data) => { if (seq === renderSeq) fn(data); };
  const fail = (e) => { if (seq === renderSeq) body.innerHTML = `<div class="card"><div class="empty">Couldn't load: ${esc(e.message)}</div></div>`; };
  if (!quiet) body.innerHTML = '<div class="empty">Loading…</div>';
  if (!state) return;

  if (r.view === 'energy') {
    if (!state.energy.enabled) { body.innerHTML = '<div class="card"><div class="empty">Octopus not configured</div></div>'; return; }
    if (r.tab === 'usage' && r.arg) return getJSON(`/api/energy/usage/${r.arg}`).then(guard(renderUsageDay)).catch(fail);
    if (r.tab === 'usage') return getJSON('/api/energy/usage').then(guard(renderUsage)).catch(fail);
    const day = new Date(state.now);
    if (r.tab === 'tomorrow') day.setTime(day.getTime() + 86400e3);
    return getJSON(`/api/energy/prices?day=${ymd(day)}`).then(guard((d) => renderPrices(d, r.tab))).catch(fail);
  }
  if (r.view === 'todo') return renderTodo(r.arg === 'add');
  if (r.view === 'timers') return renderTimers();
  if (r.view === 'weather') return renderWeather();
  if (r.view === 'nas') return renderNas();
  if (r.view === 'wifi') return getJSON('/api/wifi').then(guard(renderWifi)).catch(fail);
  if (r.view === 'seerr' && r.tab === 'search') return renderSeerrSearch(seq);
  if (r.view === 'seerr' && r.tab === 'trending') return discoverGrid('/api/seerr/discover?kind=trending', seq);
  if (r.view === 'seerr') return pagedGrid(`/api/seerr/requests?filter=${r.tab}`, 15, seq, renderRequestRows);
}

// Paged lists: first page, then a "Load more" button appends.
function pagedGrid(base, take, seq, renderItems) {
  const body = $('detail-body');
  let skip = 0, items = [];
  const load = () => getJSON(`${base}&skip=${skip}&take=${take}`).then((d) => {
    if (seq !== renderSeq) return;
    items = items.concat(d.results);
    skip += d.results.length;
    const more = skip < d.total && d.results.length > 0;
    body.innerHTML = `<div class="card">${items.length ? renderItems(items) : '<div class="empty">Nothing here</div>'}</div>
      ${more ? `<button class="more">Load more (${d.total - skip})</button>` : ''}`;
    body.querySelectorAll('[data-item]').forEach((el) => el.addEventListener('click', () => openSheet(items[+el.dataset.item])));
    const btn = body.querySelector('.more');
    if (btn) btn.addEventListener('click', () => { btn.textContent = 'Loading…'; load(); });
  }).catch((e) => {
    if (seq === renderSeq) body.innerHTML = `<div class="card"><div class="empty">Couldn't load: ${esc(e.message)}</div></div>`;
  });
  return load();
}

function renderPrices(d, tab) {
  const body = $('detail-body');
  const slots = d.slots;
  if (!slots.length) {
    body.innerHTML = `<div class="card"><div class="empty">${tab === 'tomorrow'
      ? "Tomorrow's prices aren't out yet - Octopus usually publishes them around 4pm." : 'No prices for today.'}</div></div>`;
    return;
  }
  const curStart = tab === 'today' && state.energy.current ? state.energy.current.start : null;
  const th = state.energy.thresholds;
  const band = (p) => (p <= 0 ? 'plunge' : p < th.cheap ? 'cheap' : p < th.expensive ? 'normal' : 'expensive');
  const tiles = [
    tile('Lowest', priceVal(d.stats.min), '', band(d.stats.min)),
    tile('Average', priceVal(d.stats.avg), (() => { const v = verdict(d.stats.avg, d.typical_avg); return v ? `${v.html} · ${vsUsual(v, d.typical_avg, '30-day')}` : ''; })(), band(d.stats.avg)),
    tile('Highest', priceVal(d.stats.max), '', band(d.stats.max)),
  ];
  const future = curStart ? slots.filter((s) => s.start >= curStart) : slots;
  for (const h of [1, 3]) {
    const w = cheapest(future, h * 2);
    if (w) tiles.push(tile(`Cheapest ${h}h${curStart ? ' left' : ''}`, priceVal(w.avg), `${fmtTime(w.start)}–${fmtTime(w.end)}`, band(w.avg)));
  }
  const nowIdx = curStart ? slots.findIndex((s) => s.start === curStart) : -1;
  const appl = tab === 'today' && (state.energy.appliances || []).length
    ? `<div class="card"><h2>Appliances · timer to set for the cheapest run</h2><div class="advice">${adviceRows(state.energy.appliances, true)}</div></div>` : '';
  body.innerHTML = `<div class="card"><div class="tiles">${tiles.join('')}</div></div>${appl}
    <div class="card"><div class="chart-head"><span>p/kWh by half hour · tap a bar</span><span class="readout" id="price-readout"></span></div>
    <div class="chart big-chart" id="price-chart"></div>
    <div class="legend">${['plunge', 'cheap', 'normal', 'expensive'].map((b) => `<span><span class="dot ${b}"></span>${BAND_LABEL[b]}</span>`).join('')}
    <span>dashed lines: ${th.cheap}p / ${th.expensive}p</span></div></div>`;
  const select = (i) => {
    const s = slots[i];
    $('price-readout').textContent = `${fmtTime(s.start)}–${fmtTime(s.end)} · ${p1(s.price)}p · ${BAND_LABEL[s.band]}`;
    draw(i);
  };
  const draw = (sel) => barChart($('price-chart'), slots, {
    value: (s) => s.price,
    color: (s) => bandColor(s.band),
    past: (s) => curStart && s.start < curStart,
    lines: [{ v: th.cheap }, { v: th.expensive }],
    selected: sel,
    onSelect: select,
    ...priceAxis(slots),
  });
  select(nowIdx >= 0 ? nowIdx : 0);
}

function renderUsage(d) {
  const body = $('detail-body');
  const days = d.days;
  if (!days.length) { body.innerHTML = '<div class="card"><div class="empty">No readings yet - Octopus data usually arrives the next day.</div></div>'; return; }
  const sum = (arr, k) => arr.reduce((a, x) => a + (x[k] || 0), 0);
  const last7 = days.slice(-7);
  // The standing charge is the same every day (until the tariff changes), so it's shown once above the table.
  const sc = [...new Set(days.map((x) => x.standing_p))];
  const scNote = sc.length === 1 ? `${p1(sc[0])}p` : `${p1(Math.min(...sc))}–${p1(Math.max(...sc))}p`;
  body.innerHTML = `<div class="card"><div class="tiles">
      ${tile('Last 7 days', `${sum(last7, 'kwh').toFixed(0)}<small>kWh</small>`, gbp(sum(last7, 'cost_p')))}
      ${tile(`Last ${days.length} days`, `${sum(days, 'kwh').toFixed(0)}<small>kWh</small>`, gbp(sum(days, 'cost_p')))}
      ${tile('Avg per day', gbp(sum(days, 'cost_p') / days.length), `${(sum(days, 'kwh') / days.length).toFixed(1)} kWh`)}
    </div></div>
    <div class="card"><div class="chart-head"><span>Daily cost, £ · press and slide, let go to open</span><span class="readout" id="usage-readout"></span></div>
    <div class="chart" style="height:200px" id="usage-chart"></div></div>
    <div class="card"><div class="note days-note">Standing charge ${scNote} a day · Energy is what the kWh cost; Cost adds the standing charge</div>
    <table class="days"><thead><tr><th>Day</th><th>kWh</th><th>Avg p/kWh</th><th>Energy</th><th>Cost</th></tr></thead><tbody>
    ${[...days].reverse().map((x) => `<tr class="click" data-day="${x.date}"><td>${fmtDay(dayDate(x.date))}${x.complete ? '' : ' <small class="note">partial</small>'}</td>
      <td>${x.kwh.toFixed(2)}</td><td>${p1(x.avg_price)}</td><td>${gbp(x.energy_p)}</td><td>${gbp(x.cost_p)}</td></tr>`).join('')}
    </tbody></table></div>`;
  const open = (date) => { location.hash = `#/energy/usage/${date}`; };
  barChart($('usage-chart'), days, {
    value: (x) => x.cost_p / 100,
    color: () => 'var(--accent)',
    xLabel: (x, i) => (i % 3 === 0 ? dtf({ day: 'numeric' }).format(dayDate(x.date)) : null),
    onPreview: (i) => {
      const x = days[i];
      $('usage-readout').innerHTML = x ? `<b>${fmtDay(dayDate(x.date))}</b> · ${x.kwh.toFixed(1)} kWh · ${gbp(x.cost_p)}` : '';
    },
    onSelect: (i) => open(days[i].date),
  });
  body.querySelectorAll('tr[data-day]').forEach((tr) => tr.addEventListener('click', () => open(tr.dataset.day)));
}

function renderUsageDay(d) {
  const body = $('detail-body');
  const th = state.energy.thresholds;
  const band = (p) => (p == null ? null : p <= 0 ? 'plunge' : p < th.cheap ? 'cheap' : p < th.expensive ? 'normal' : 'expensive');
  const s = d.summary;
  const v = s && verdict(s.avg_price, d.typical_paid);
  const navBtn = (day, label) => (day
    ? `<button class="step" data-day="${day}">${label}</button>` : '<span class="step off"></span>');
  body.innerHTML = `<div class="card daynav">
      ${navBtn(d.prev, `‹<span class="nl"> ${d.prev ? esc(fmtDay(dayDate(d.prev))) : ''}</span>`)}
      <div class="dayname">${esc(dtf({ weekday: 'long', day: 'numeric', month: 'long' }).format(dayDate(d.date)))}</div>
      ${navBtn(d.next, `<span class="nl">${d.next ? esc(fmtDay(dayDate(d.next))) : ''} </span>›`)}
    </div>
    <div class="card"><div class="tiles">
      ${tile('Used', `${s ? s.kwh.toFixed(1) : '–'}<small>kWh</small>`, s && !s.complete ? 'partial day' : '')}
      ${tile('Cost', gbp(s && s.cost_p), s ? `incl. ${gbp(s.standing_p)} standing` : '')}
      ${tile('Avg price paid', priceVal(s && s.avg_price), v ? `${v.html} · ${vsUsual(v, d.typical_paid)}` : 'weighted by use')}
    </div></div>
    <div class="card"><div class="chart-head"><span>kWh per half hour, coloured by price · tap a bar</span><span class="readout" id="day-readout"></span></div>
    <div class="chart big-chart" id="day-chart"></div>
    <div class="legend">${['plunge', 'cheap', 'normal', 'expensive'].map((b) => `<span><span class="dot ${b}"></span>${BAND_LABEL[b]}</span>`).join('')}</div></div>
    <button class="more" id="back-usage">‹ All days</button>`;
  $('back-usage').addEventListener('click', () => { location.hash = '#/energy/usage'; });
  body.querySelectorAll('.daynav [data-day]').forEach((b) => b.addEventListener('click', () => { location.hash = `#/energy/usage/${b.dataset.day}`; }));
  const draw = (sel) => barChart($('day-chart'), d.slots, {
    value: (x) => x.kwh,
    color: (x) => bandColor(band(x.price)),
    selected: sel,
    onSelect: (i) => {
      const x = d.slots[i];
      $('day-readout').textContent = `${fmtTime(x.start)} · ${x.kwh.toFixed(2)} kWh · ${p1(x.price)}p · ${x.cost_p == null ? '–' : `${x.cost_p.toFixed(1)}p`}`;
      draw(i);
    },
    xLabel: (x) => { const t = fmtTime(x.start); return t.endsWith(':00') && +t.slice(0, 2) % 3 === 0 ? t.slice(0, 2) : null; },
  });
  draw(null);
}

function renderRequestRows(items) {
  return `<div class="rows">${items.map((it, i) => `<button class="row" data-item="${i}">
    ${poster(it.title, it.poster)}
    <div class="t"><div class="title">${esc(it.title)}${it.year ? ` <small>(${esc(it.year)})</small>` : ''}</div>
    <div class="meta">${it.type === 'tv' ? 'TV' : 'Movie'} · ${esc(it.user || 'unknown')} · ${ago(it.created)} ago</div></div>
    <span class="pill ${esc(it.status)}">${esc(it.status)}</span></button>`).join('')}</div>`;
}


// --- shared to-do list --------------------------------------------------------------
// Everyone edits the same list (phones and the kitchen screen); the server keeps it.

let todoText = '';
let todoKeyboard = false;
let todoRepeat = { every: null }; // the repeat picked for the next item added
const RECENT_TICK_MS = 60 * 60e3; // ticked items stay visible on home for an hour, to undo a stray tap

const CHECK_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const nth = (n) => n + ((n % 100 >= 11 && n % 100 <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th'));
const weekdayList = (on) => [].concat(on ?? []); // older items hold a single weekday
function repeatLabel(r) {
  if (r.every === 'day') return 'Daily';
  if (r.every === 'month') return `Monthly, ${nth(r.on)}`;
  const days = weekdayList(r.on);
  if (days.length === 7) return 'Daily';
  if (days.join() === '0,1,2,3,4') return 'Weekdays';
  if (days.join() === '5,6') return 'Weekends';
  return `Every ${days.map((d) => WEEKDAYS[d]).join(', ')}`;
}

function todoRow(it, withDelete) {
  const rep = it.repeat
    ? `<span class="trep">↻ ${esc(repeatLabel(it.repeat))}${it.waiting ? ` · next ${esc(fmtDay(dayDate(it.due)))}` : ''}</span>` : '';
  return `<li class="titem ${it.done ? 'done' : ''}">
    <button class="tcheck" data-tick="${esc(it.id)}" aria-label="${it.done ? 'Untick' : 'Tick'}">${it.done ? CHECK_ICON : ''}</button>
    <span class="ttext">${esc(it.text)}${rep}</span>
    ${withDelete ? `<button class="tdel" data-del="${esc(it.id)}" aria-label="Delete">✕</button>` : ''}</li>`;
}

function renderTodoHome() {
  const items = (state.todos && state.todos.items) || [];
  const now = Date.now();
  const open = items.filter((i) => !i.done && !i.waiting);
  const recent = items.filter((i) => i.done && now - Date.parse(i.done_at) < RECENT_TICK_MS);
  $('todo-home').innerHTML = open.length || recent.length
    ? open.concat(recent).map((i) => todoRow(i, false)).join('')
    : '<li class="empty">Nothing to do. Tap + Add, or add from your phone.</li>';
}

// True while someone is typing, so the 10 s refresh doesn't wipe their text.
const todoEditing = () => todoKeyboard || todoText.length > 0 || (document.activeElement && document.activeElement.id === 'tinput');

async function todoApi(method, url, body) {
  try {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    if (state) state.todos = await r.json();
  } catch (e) {
    offline = true;
    renderStatus();
  }
  if (route().view === 'home') renderTodoHome(); else if (route().view === 'todo') renderTodo(false, true);
}
const tickTodo = (id) => {
  const it = ((state.todos && state.todos.items) || []).find((i) => i.id === id);
  if (it) todoApi('PATCH', `/api/todos/${id}`, { done: !it.done });
};

function renderTodo(openKeyboard, quiet) {
  const body = $('detail-body');
  if (openKeyboard) todoKeyboard = true;
  const items = (state.todos && state.todos.items) || [];
  const open = items.filter((i) => !i.done && !i.waiting);
  const waiting = items.filter((i) => i.waiting).sort((a, b) => a.due.localeCompare(b.due));
  const done = items.filter((i) => i.done && !i.repeat);
  const list = `<div class="card"><ul class="todo-list big">${open.length ? open.map((i) => todoRow(i, true)).join('')
      : '<li class="empty">Nothing to do.</li>'}</ul>
    ${waiting.length ? `<div class="todo-done-head"><span class="note">Repeating · back on the day shown</span></div>
      <ul class="todo-list big">${waiting.map((i) => todoRow(i, true)).join('')}</ul>` : ''}
    ${done.length ? `<div class="todo-done-head"><span class="note">Done · cleared automatically after a week</span>
      <button class="label-btn" id="clear-done">Clear done (${done.length})</button></div>
      <ul class="todo-list big">${done.map((i) => todoRow(i, true)).join('')}</ul>` : ''}</div>`;
  if (quiet && $('todo-list-wrap')) { $('todo-list-wrap').innerHTML = list; bindTodoList(); return; }
  const phone = PHONE.matches;
  const box = phone
    ? `<form id="tform" class="tform"><input class="searchbox" id="tinput" maxlength="120" autocomplete="off"
         enterkeyhint="done" placeholder="Add an item…" value="${esc(todoText)}"><button class="big-btn primary">Add</button></form>`
    : `<button class="searchbox" id="tbox"><span id="tq"></span></button>
       <div class="kbd" id="tkbd" ${todoKeyboard ? '' : 'hidden'}>
        ${KEY_ROWS.map((row) => `<div class="krow">${[...row].map((k) => `<button class="key" data-k="${k}">${k}</button>`).join('')}${row === 'zxcvbnm' ? '<button class="key wide" data-k="back">⌫</button>' : ''}</div>`).join('')}
        <div class="krow"><button class="key wide" data-k="close">Done</button><button class="key space" data-k=" ">space</button><button class="key wide go" data-k="go">Add</button></div>
       </div>`;
  body.innerHTML = `<div class="card">${box}<div class="trepeat" id="trepeat"></div></div><div id="todo-list-wrap">${list}</div>`;
  renderRepeatPicker();
  const add = () => {
    const text = todoText.trim();
    if (!text) return;
    const repeat = todoRepeat.every ? todoRepeat : null;
    todoText = '';
    todoRepeat = { every: null };
    renderRepeatPicker();
    todoApi('POST', '/api/todos', { text, repeat });
  };
  if (phone) {
    $('tform').addEventListener('submit', (e) => { e.preventDefault(); todoText = $('tinput').value; add(); $('tinput').value = ''; });
    $('tinput').addEventListener('input', (e) => { todoText = e.target.value; });
  } else {
    const show = () => { $('tq').innerHTML = esc(todoText) || '<span class="note">Tap to type an item…</span>'; };
    show();
    $('tbox').addEventListener('click', () => { todoKeyboard = true; $('tkbd').hidden = false; });
    $('tkbd').addEventListener('click', (e) => {
      const k = e.target.closest('[data-k]');
      if (!k) return;
      const key = k.dataset.k;
      if (key === 'back') todoText = todoText.slice(0, -1);
      else if (key === 'close') { todoKeyboard = false; $('tkbd').hidden = true; }
      else if (key === 'go') add();
      else if (todoText.length < 120) todoText += todoText ? key : key.toUpperCase(); // capitalise the first letter
      show();
    });
  }
  bindTodoList();
}

// Once / Daily / Weekly (pick a day) / Monthly (pick a date), for the item being added.
function renderRepeatPicker() {
  const r = todoRepeat;
  const opt = (every, label) => `<button type="button" class="label-btn ${(r.every || '') === every ? 'on' : ''}" data-every="${every}">${label}</button>`;
  let more = '';
  if (r.every === 'week') {
    more = `<div class="trow">${WEEKDAYS.map((d, i) => `<button type="button" class="label-btn ${r.on.includes(i) ? 'on' : ''}" data-day="${i}">${d}</button>`).join('')}<span class="note">tap one or more</span></div>`;
  } else if (r.every === 'month') {
    more = `<div class="trow"><button type="button" class="step" data-on="${r.on === 1 ? 31 : r.on - 1}">−</button>
      <span class="tmonth">on the ${nth(r.on)}${r.on > 28 ? ' <span class="note">(or the last day)</span>' : ''}</span>
      <button type="button" class="step" data-on="${r.on === 31 ? 1 : r.on + 1}">+</button></div>`;
  }
  const el = $('trepeat');
  el.innerHTML = `<div class="trow"><span class="note">Repeat</span>${opt('', 'Once')}${opt('day', 'Daily')}${opt('week', 'Weekly')}${opt('month', 'Monthly')}</div>${more}`;
  el.querySelectorAll('[data-every]').forEach((b) => b.addEventListener('click', () => {
    const today = dayDate(ymd(new Date(serverNow())));
    const every = b.dataset.every || null;
    todoRepeat = { every, on: every === 'week' ? [(today.getUTCDay() + 6) % 7] : every === 'month' ? today.getUTCDate() : undefined };
    renderRepeatPicker();
  }));
  el.querySelectorAll('[data-day]').forEach((b) => b.addEventListener('click', () => {
    const d = Number(b.dataset.day), on = todoRepeat.on;
    if (on.includes(d) && on.length === 1) return; // keep at least one day
    todoRepeat = { ...todoRepeat, on: on.includes(d) ? on.filter((x) => x !== d) : [...on, d].sort() };
    renderRepeatPicker();
  }));
  el.querySelectorAll('[data-on]').forEach((b) => b.addEventListener('click', () => {
    todoRepeat = { ...todoRepeat, on: Number(b.dataset.on) };
    renderRepeatPicker();
  }));
}

function bindTodoList() {
  const wrap = $('todo-list-wrap');
  wrap.querySelectorAll('[data-tick]').forEach((b) => b.addEventListener('click', () => tickTodo(b.dataset.tick)));
  wrap.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => todoApi('DELETE', `/api/todos/${b.dataset.del}`)));
  const clear = $('clear-done');
  if (clear) clear.addEventListener('click', () => todoApi('POST', '/api/todos/clear-done'));
}

// --- sheet -----------------------------------------------------------------------

function openSheet(it) {
  if (it.tmdb_id) return openDiscoverSheet(it);
  const img = it.poster;
  const meta = [it.subtitle, it.year, it.type && { tv: 'TV', movie: 'Movie' }[it.type], it.user && `requested by ${it.user}`]
    .filter(Boolean).map(esc).join(' · ');
  $('sheet-body').innerHTML = `${poster(it.title, img)}<div>
    <h3>${esc(it.title)}</h3><div class="note">${meta}</div>
    ${it.status ? `<div style="margin-top:8px"><span class="pill ${esc(it.status)}">${esc(it.status)}</span></div>` : ''}
    <p>${esc(it.overview || 'No description.')}</p></div>`;
  $('sheet').hidden = false;
}
function closeSheet() { $('sheet').hidden = true; }

// --- seerr: search, trending, request ---------------------------------------------

const DISCOVER_STATUS = { unknown: null, pending: 'requested', processing: 'processing', partial: 'partial', available: 'available', blacklisted: 'blocked', deleted: null };
let seerrQuery = '';
let seerrKeyboard = true;

function discoverCards(items) {
  return `<div class="grid-posters">${items.map((it, i) => {
    const st = DISCOVER_STATUS[it.status];
    return `<button class="pcard" data-item="${i}">${poster(it.title, it.poster)}
      <div class="title">${esc(it.title)}</div>
      <div class="meta">${it.type === 'tv' ? 'TV' : 'Movie'}${it.year ? ` · ${esc(it.year)}` : ''}</div>
      ${st ? `<div><span class="pill ${esc(it.status)}">${st}</span></div>` : ''}</button>`;
  }).join('')}</div>`;
}

function showDiscover(el, items) {
  el.innerHTML = items.length ? discoverCards(items) : '<div class="empty">No results</div>';
  el.querySelectorAll('[data-item]').forEach((b) => b.addEventListener('click', () => openSheet(items[+b.dataset.item])));
}

function discoverGrid(url, seq) {
  const body = $('detail-body');
  body.innerHTML = '<div class="card" id="disc"><div class="empty">Loading…</div></div>';
  return getJSON(url).then((d) => { if (seq === renderSeq) showDiscover($('disc'), d.results); })
    .catch((e) => { if (seq === renderSeq) $('disc').innerHTML = `<div class="empty">Couldn't load: ${esc(e.message)}</div>`; });
}

const KEY_ROWS = ['1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm'];

function renderSeerrSearch(seq) {
  const body = $('detail-body');
  if (PHONE.matches) return renderSeerrSearchPhone(seq, body);
  body.innerHTML = `<div class="card">
      <button class="searchbox" id="sbox"><span id="sq">${esc(seerrQuery) || '<span class="note">Tap to type a film or series…</span>'}</span></button>
      <div class="kbd" id="kbd" ${seerrKeyboard ? '' : 'hidden'}>
        ${KEY_ROWS.map((row) => `<div class="krow">${[...row].map((k) => `<button class="key" data-k="${k}">${k}</button>`).join('')}${row === 'zxcvbnm' ? '<button class="key wide" data-k="back">⌫</button>' : ''}</div>`).join('')}
        <div class="krow"><button class="key wide" data-k="clear">Clear</button><button class="key space" data-k=" ">space</button><button class="key wide go" data-k="go">Search</button></div>
      </div>
    </div>
    <div class="card" id="disc" ${seerrQuery && !seerrKeyboard ? '' : 'hidden'}></div>`;
  const update = () => { $('sq').innerHTML = esc(seerrQuery) || '<span class="note">Tap to type a film or series…</span>'; };
  const run = () => {
    if (!seerrQuery.trim()) return;
    seerrKeyboard = false;
    $('kbd').hidden = true;
    $('disc').hidden = false;
    $('disc').innerHTML = '<div class="empty">Searching…</div>';
    getJSON(`/api/seerr/search?q=${encodeURIComponent(seerrQuery.trim())}`)
      .then((d) => { if (seq === renderSeq) showDiscover($('disc'), d.results); })
      .catch((e) => { if (seq === renderSeq) $('disc').innerHTML = `<div class="empty">Search failed: ${esc(e.message)}</div>`; });
  };
  $('sbox').addEventListener('click', () => { seerrKeyboard = true; $('kbd').hidden = false; });
  $('kbd').addEventListener('click', (e) => {
    const k = e.target.closest('[data-k]');
    if (!k) return;
    const key = k.dataset.k;
    if (key === 'back') seerrQuery = seerrQuery.slice(0, -1);
    else if (key === 'clear') seerrQuery = '';
    else if (key === 'go') return run();
    else if (seerrQuery.length < 60) seerrQuery += key;
    update();
  });
  if (seerrQuery && !seerrKeyboard) run();
}

function renderSeerrSearchPhone(seq, body) {
  body.innerHTML = `<div class="card"><form id="sform"><input class="searchbox" id="sinput" type="search"
      enterkeyhint="search" autocomplete="off" maxlength="60" placeholder="Film or series…" value="${esc(seerrQuery)}"></form></div>
    <div class="card" id="disc" ${seerrQuery ? '' : 'hidden'}></div>`;
  const run = () => {
    if (!seerrQuery.trim()) return;
    $('disc').hidden = false;
    $('disc').innerHTML = '<div class="empty">Searching…</div>';
    getJSON(`/api/seerr/search?q=${encodeURIComponent(seerrQuery.trim())}`)
      .then((d) => { if (seq === renderSeq) showDiscover($('disc'), d.results); })
      .catch((e) => { if (seq === renderSeq) $('disc').innerHTML = `<div class="empty">Search failed: ${esc(e.message)}</div>`; });
  };
  $('sform').addEventListener('submit', (e) => {
    e.preventDefault();
    seerrQuery = $('sinput').value;
    $('sinput').blur(); // close the keyboard to show results
    run();
  });
  if (seerrQuery) run();
}

function openDiscoverSheet(it) {
  const st = DISCOVER_STATUS[it.status];
  const canRequest = state.features && state.features.seerr_requests && !st;
  $('sheet-body').innerHTML = `${poster(it.title, it.poster)}<div style="flex:1;min-width:0">
    <h3>${esc(it.title)}</h3><div class="note">${it.type === 'tv' ? 'TV series' : 'Movie'}${it.year ? ` · ${esc(it.year)}` : ''}</div>
    ${st ? `<div style="margin-top:8px"><span class="pill ${esc(it.status)}">${st}</span></div>` : ''}
    <p>${esc(it.overview || 'No description.')}</p>
    ${canRequest ? `<button class="big-btn primary req" data-keep id="req">Request${it.type === 'tv' ? ' (all seasons)' : ''}</button>` : ''}
    <div class="note" id="req-msg" data-keep></div></div>`;
  $('sheet').hidden = false;
  const btn = $('req');
  if (!btn) return;
  let armed = false;
  btn.addEventListener('click', async () => {
    if (!armed) {
      armed = true;
      btn.textContent = 'Tap again to confirm';
      btn.classList.add('confirm');
      setTimeout(() => { if (armed && btn.isConnected && !btn.disabled) { armed = false; btn.textContent = 'Request'; btn.classList.remove('confirm'); } }, 4000);
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Requesting…';
    try {
      const r = await fetch('/api/seerr/request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: it.type, tmdb_id: it.tmdb_id }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      it.status = d.status === 'approved' ? 'processing' : 'pending';
      btn.remove();
      $('req-msg').textContent = d.status === 'approved' ? 'Requested and approved - downloading soon.' : 'Requested - waiting for approval.';
    } catch (e) {
      btn.disabled = false;
      armed = false;
      btn.textContent = 'Request';
      btn.classList.remove('confirm');
      $('req-msg').textContent = `Request failed: ${e.message}`;
    }
  });
}

// --- weather + NAS views ------------------------------------------------------

function renderWeather() {
  const w = state.weather;
  const body = $('detail-body');
  if (!w || !w.current) {
    body.innerHTML = `<div class="card"><div class="empty">${w && w.enabled ? 'Waiting for the forecast…' : 'Set LATITUDE and LONGITUDE in .env to show the weather.'}</div></div>`;
    return;
  }
  const c = w.current;
  const summary = w.rain_at ? `Rain likely from ${fmtTime(w.rain_at)}` : `Dry until at least ${fmtTime(w.dry_until)}`;
  const hours = w.hourly;
  body.innerHTML = `<div class="card wx-now">
      ${wxIcon(c.icon, 72)}
      <div><div class="wx-temp">${Math.round(c.temp)}°</div><div class="note">${esc(c.desc)} · feels ${Math.round(c.feels)}° · wind ${Math.round(c.wind)} mph</div></div>
      <div class="wx-sum"><div class="title">${summary}</div>
        ${w.today ? `<div class="note">Today ${Math.round(w.today.max)}° / ${Math.round(w.today.min)}° · rain chance up to ${w.today.pop ?? 0}%</div>` : ''}
        ${!c.day ? '' : w.drying ? '<span class="pill available">Good drying day</span>' : w.rain_at ? '<span class="pill pending">Washing in before the rain</span>' : ''}</div>
    </div>
    <div class="card"><div class="wx-hours">${hours.slice(0, 12).map((h) => `<div class="wx-h"><span class="note">${fmtTime(h.time).slice(0, 2)}</span>${wxIcon(h.icon, 26)}<b>${Math.round(h.temp)}°</b><span class="note">${h.pop ?? 0}%</span></div>`).join('')}</div></div>
    <div class="card"><div class="chart-head"><span>Chance of rain, next 24h, % · tap an hour</span><span class="readout" id="wx-readout"></span></div>
      <div class="chart" style="height:150px" id="wx-chart"></div></div>
    <div class="card"><div class="rows">${w.daily.map((d) => `<div class="row">
      <span style="width:110px">${fmtDay(dayDate(d.date))}</span>${wxIcon(d.icon, 30)}
      <span class="t"><span class="title">${esc(d.desc)}</span><span class="meta">${d.pop ?? 0}% rain${d.precip ? ` · ${d.precip} mm` : ''}</span></span>
      <span class="num"><b>${Math.round(d.max)}°</b> <span class="note">${Math.round(d.min)}°</span></span></div>`).join('')}</div></div>`;
  const draw = (sel) => barChart($('wx-chart'), hours, {
    value: (h) => h.pop || 0,
    color: () => 'var(--accent)',
    selected: sel,
    xLabel: (h, i) => (i % 3 === 0 ? fmtTime(h.time).slice(0, 2) : null),
    onSelect: (i) => {
      const h = hours[i];
      $('wx-readout').textContent = `${fmtTime(h.time)} · ${Math.round(h.temp)}° · ${h.pop ?? 0}% · ${h.desc}`;
      draw(i);
    },
  });
  draw(null);
}

function renderNas() {
  const n = state.nas;
  const body = $('detail-body');
  if (!n || (!n.enabled && !n.error)) { body.innerHTML = '<div class="card"><div class="empty">Set TRUENAS_URL and TRUENAS_API_KEY in .env.</div></div>'; return; }
  if (!n.state) { body.innerHTML = `<div class="card"><div class="empty">${n.error ? `Can't reach TrueNAS: ${esc(n.error)}` : 'Checking…'}</div></div>`; return; }
  const label = { ok: 'All good', warn: 'Needs a look', error: 'Problem' }[n.state];
  const up = n.uptime_s ? `up ${Math.floor(n.uptime_s / 86400)}d ${Math.floor((n.uptime_s % 86400) / 3600)}h` : '';
  body.innerHTML = `<div class="card"><div class="nas-head"><i class="led big ${n.state}"></i><div><div class="wx-temp" style="font-size:30px">${label}</div>
      <div class="note">${esc(n.version || '')}${up ? ` · ${up}` : ''}${n.error ? ` · last check failed: ${esc(n.error)}` : ''}</div></div></div></div>
    <div class="card"><h2>Pools</h2><div class="rows">${n.pools.map((p) => `<div class="row">
      <i class="led ${p.healthy ? 'ok' : 'error'}"></i><span class="t"><span class="title">${esc(p.name)}</span><span class="meta">${esc(p.status)}</span></span>
      ${p.used_pct != null ? `<span style="width:260px"><div class="bar"><i style="width:${p.used_pct}%;${p.used_pct >= 90 ? 'background:var(--expensive)' : ''}"></i></div><span class="note">${p.used_pct}% used</span></span>` : ''}</div>`).join('') || '<div class="empty">No pools</div>'}</div></div>
    <div class="card"><h2>Alerts</h2><div class="rows">${n.alerts.map((a) => `<div class="row">
      <span class="pill ${a.level === 'WARNING' ? 'pending' : ['INFO', 'NOTICE'].includes(a.level) ? '' : 'declined'}">${esc(a.level.toLowerCase())}</span>
      <span class="t" style="white-space:normal">${esc(a.text)}</span></div>`).join('') || '<div class="empty">No active alerts</div>'}</div></div>`;
}

// --- timers --------------------------------------------------------------------

const TIMER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" style="vertical-align:-3px"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/></svg>';
const PRESETS = [1, 2, 3, 4, 5, 8, 10, 12, 15, 20, 30, 45];
let timerLabel = '';
let customMin = 7;
let alarmSnoozedIds = new Set(); // ids whose alarm sound we already stopped locally
let renderedTimerIds = '';

const timerLeft = (t) => Math.ceil((Date.parse(t.ends) - serverNow()) / 1000);
function fmtLeft(sec) {
  if (sec <= 0) return 'Done';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}
const timerName = (t) => t.label || `${Math.round(t.seconds / 60) || 1} min`;

async function timerApi(method, url, body) {
  try {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    if (state) state.timers = await r.json();
  } catch (e) {
    offline = true;
    renderStatus();
  }
  renderedTimerIds = '';
  tickTimers();
}
const startTimer = (seconds) => timerApi('POST', '/api/timers', { seconds, label: timerLabel });
const addTime = (id, seconds) => { alarmSnoozedIds.delete(id); return timerApi('POST', `/api/timers/${id}/add`, { seconds }); };
const cancelTimer = (id) => timerApi('DELETE', `/api/timers/${id}`);

function renderTimers() {
  const labels = (state && state.timer_labels) || [];
  $('detail-body').innerHTML = `<div class="timers-layout">
    <div class="card" style="gap:12px">
      <div class="labels">${['', ...labels].map((l) =>
        `<button class="label-btn ${l === timerLabel ? 'on' : ''}" data-label="${esc(l)}">${l ? esc(l) : 'No label'}</button>`).join('')}</div>
      <div class="presets">${PRESETS.map((m) => `<button class="preset" data-start="${m * 60}">${m}<small>min</small></button>`).join('')}</div>
      <div class="custom">
        <button class="step" data-custom="-5">−5</button><button class="step" data-custom="-1">−1</button>
        <span class="val" id="custom-val">${customMin} min</span>
        <button class="step" data-custom="1">+1</button><button class="step" data-custom="5">+5</button>
        <button class="big-btn primary" id="custom-start">Start</button>
      </div>
    </div>
    <div class="card" id="timer-list" style="gap:8px"></div>
  </div>`;
  const body = $('detail-body');
  body.querySelectorAll('[data-label]').forEach((b) => b.addEventListener('click', () => { timerLabel = b.dataset.label; renderTimers(); }));
  body.querySelectorAll('[data-start]').forEach((b) => b.addEventListener('click', () => startTimer(+b.dataset.start)));
  body.querySelectorAll('[data-custom]').forEach((b) => b.addEventListener('click', () => {
    customMin = Math.min(600, Math.max(1, customMin + +b.dataset.custom));
    $('custom-val').textContent = `${customMin} min`;
  }));
  $('custom-start').addEventListener('click', () => startTimer(customMin * 60));
  renderedTimerIds = '';
  tickTimers();
}

// Called every second: countdowns in the top bar and timers view, plus the alarm.
function tickTimers() {
  const list = (state && state.timers) || [];
  $('timer-chips').innerHTML = list.length
    ? list.map((t) => { const left = timerLeft(t); return `<button class="tchip ${left <= 0 ? 'done' : ''}" data-href="#/timers">${esc(timerName(t))} ${fmtLeft(left)}</button>`; }).join('')
    : `<button class="tchip" data-href="#/timers">${TIMER_ICON}<span class="tlabel"> Timer</span></button>`;

  const box = $('timer-list');
  if (box) {
    const ids = list.map((t) => t.id).join();
    if (ids !== renderedTimerIds) {
      renderedTimerIds = ids;
      box.innerHTML = list.length ? list.map((t) => `<div class="trow" data-tid="${t.id}">
        <div class="top"><span>${esc(timerName(t))}</span><span class="left"></span></div>
        <div class="bar"><i></i></div>
        <div class="acts"><button data-act="add" data-id="${t.id}" data-s="60">+1 min</button>
        <button data-act="add" data-id="${t.id}" data-s="300">+5 min</button>
        <button data-act="del" data-id="${t.id}">Cancel</button></div></div>`).join('')
        : '<div class="empty">No timers running. Tap a time to start one.</div>';
      box.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () =>
        (b.dataset.act === 'add' ? addTime(b.dataset.id, +b.dataset.s) : cancelTimer(b.dataset.id))));
    }
    for (const t of list) {
      const row = box.querySelector(`[data-tid="${t.id}"]`);
      if (!row) continue;
      const left = timerLeft(t);
      row.classList.toggle('done', left <= 0);
      row.querySelector('.left').textContent = fmtLeft(left);
      row.querySelector('.bar i').style.width = `${Math.max(0, Math.min(100, (1 - left / t.seconds) * 100))}%`;
    }
  }

  const ringing = list.filter((t) => timerLeft(t) <= 0);
  $('alarm').hidden = !ringing.length;
  if (ringing.length) {
    $('alarm').dataset.id = ringing[0].id;
    $('alarm-title').textContent = `${timerName(ringing[0])} - done!`;
    if (ringing.some((t) => !alarmSnoozedIds.has(t.id) && serverNow() - Date.parse(t.ends) < 5 * 60e3)) beep();
  }
}

// Alarm sound via WebAudio, so there are no audio files to serve.
let audioCtx = null;
function beep() {
  try {
    audioCtx = audioCtx || new AudioContext();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    for (let i = 0; i < 3; i++) {
      const t0 = audioCtx.currentTime + i * 0.22;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.5, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t0);
      osc.stop(t0 + 0.18);
    }
  } catch (e) { /* no audio device */ }
}

// --- loop ----------------------------------------------------------------------

function tickClock() {
  const now = new Date();
  $('time').textContent = dtf({ hour: '2-digit', minute: '2-digit' }).format(now);
  $('date').textContent = dtf({ weekday: 'short', day: 'numeric', month: 'short' }).format(now);
  $('night-time').textContent = $('time').textContent;
  const night = !PHONE.matches && state && state.night && Date.now() > nightWakeUntil && Date.now() - lastTouch > 30_000;
  $('night').hidden = !night;
  if (!PHONE.matches && Date.now() - lastTouch > IDLE_MS) { closeSheet(); if (route().view !== 'home') location.hash = ''; }
  tickTimers();
}

async function poll() {
  try {
    const s = await getJSON('/api/state');
    if (build && s.build !== build) { location.reload(); return; }
    build = s.build;
    tz = s.tz;
    state = s;
    clockOffset = Date.parse(s.now) - Date.now();
    lastOk = new Date().toISOString();
    offline = false;
    const r = route();
    if (r.view === 'home') renderHome();
    else if (r.view === 'todo' && !todoEditing()) renderTodo(false, true);
    const c = s.energy.current;
    $('night-price').innerHTML = c ? `<span class="dot ${c.band}"></span>${p1(c.price)}p now · ${BAND_LABEL[c.band]}` : '';
  } catch (e) {
    offline = true; // keep showing the last good data
  }
  renderStatus();
  setTimeout(poll, POLL_MS);
}

document.addEventListener('pointerdown', () => { lastTouch = Date.now(); }, true);
document.addEventListener('click', (e) => {
  const tick = e.target.closest('#todo-home [data-tick]');
  if (tick) return tickTodo(tick.dataset.tick);
  const go = e.target.closest('[data-go]');
  if (go && route().view === 'home' && !e.target.closest('[data-href]')) location.hash = `#/${go.dataset.go}`;
  const href = e.target.closest('[data-href]');
  if (href) location.hash = href.dataset.href;
});
$('back').addEventListener('click', () => { location.hash = ''; });
$('sheet').addEventListener('click', (e) => { if (!e.target.closest('[data-keep]')) closeSheet(); });
$('alarm').addEventListener('click', (e) => {
  const b = e.target.closest('[data-alarm]');
  const id = $('alarm').dataset.id;
  if (!b || !id) return;
  if (b.dataset.alarm === 'dismiss') { alarmSnoozedIds.add(id); cancelTimer(id); } else addTime(id, +b.dataset.alarm);
});
// Browsers only allow sound after a touch; unlock audio on the first one.
document.addEventListener('pointerdown', () => { if (!audioCtx) try { audioCtx = new AudioContext(); } catch (e) { /* none */ } }, { once: true });
$('night').addEventListener('click', () => { nightWakeUntil = Date.now() + NIGHT_WAKE_MS; $('night').hidden = true; });
window.addEventListener('hashchange', showRoute);
// Redraw charts when the width changes; ignore phone address bars sliding in/out.
let lastWidth = window.innerWidth;
window.addEventListener('resize', () => {
  if (!state || window.innerWidth === lastWidth) return;
  lastWidth = window.innerWidth;
  route().view === 'home' ? renderHome() : renderDetail(route(), true);
});

// Installed on a phone: a service worker keeps the app shell for offline starts.
// Browsers only allow one over https (or localhost), so the kiosk never gets one.
if ('serviceWorker' in navigator && PHONE.matches) navigator.serviceWorker.register('/sw.js').catch(() => {});

setInterval(tickClock, 1000);
tickClock();
showRoute();
poll().then(() => { if (route().view !== 'home') renderDetail(); });
