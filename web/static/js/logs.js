/* Log explorer: search and live replay over the raw NCSA access logs.

   Everything lives inside one closure. app.js and charts.js declare their
   helpers as top-level consts, and a second top-level const with the same name
   in a classic script is a SyntaxError that would take the whole page down. */
"use strict";
(function () {

/* ------------------------------------------------------------- helpers */
const $id = id => document.getElementById(id);
const H = s => (typeof esc === "function")
  ? esc(String(s ?? ""))
  : String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const N = v => (+v || 0).toLocaleString("en-US");
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const pad = n => String(n).padStart(2, "0");
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

const LOG_MIN = "1995-07-01T00:00", LOG_MAX = "1995-08-31T23:59";
const PAGE = 200;

/* Log timestamps are local server time (-0400) with no zone attached. Treating
   them as UTC everywhere keeps the arithmetic free of the viewer's DST rules. */
function toMs(iso) {
  let s = String(iso || "");
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) s += "T00:00:00";
  else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s)) s += ":00";
  const v = Date.parse(s + "Z");
  return Number.isFinite(v) ? v : NaN;
}
const isoOf = ms => new Date(ms).toISOString().slice(0, 19);
const D = ms => new Date(ms);
function tSec(iso) {               // 13 Jul 09:00:05
  const d = D(toMs(iso));
  return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}
function tMin(iso) {               // 6 Jul 18:45
  const d = D(toMs(iso));
  return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}
function tClock(ms) { const d = D(ms); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; }
function tDay(ms) { const d = D(ms); return `${WD[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; }
function tLong(iso) { const ms = toMs(iso); return `${tDay(ms)}, ${tClock(ms)}`; }
function bytesH(v) {
  v = +v || 0;
  return v >= 1048576 ? (v / 1048576).toFixed(1) + " MB" : v >= 1024 ? (v / 1024).toFixed(1) + " KB" : v + " B";
}

/* The exact line a 1995 NCSA httpd would have written, rebuilt from the fields. */
function ncsa(r) {
  const d = D(toMs(r.ts));
  const stamp = `${pad(d.getUTCDate())}/${MON[d.getUTCMonth()]}/${d.getUTCFullYear()}:` +
                `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} -0400`;
  const req = r.method ? `${r.method} ${r.path} HTTP/1.0` : r.path;
  return `${r.host} - - [${stamp}] "${req}" ${r.status} ${r.bytes}`;
}

const STATUS_TEXT = { 200: "OK", 302: "Found", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden",
                      404: "Not Found", 500: "Internal Server Error", 501: "Not Implemented" };
const sclass = code => String(code)[0];
const pill = code => `<span class="lx-st s${sclass(code)}">${H(code)}</span>`;

/* Wrap every case-insensitive occurrence of any needle in <mark>, escaping
   everything in between. The text is arbitrary bytes from 1995 clients. */
function hl(text, needles) {
  text = String(text ?? "");
  const low = text.toLowerCase(), marks = [];
  for (const n of needles) {
    if (!n) continue;
    const nl = n.toLowerCase();
    for (let i = low.indexOf(nl); i >= 0; i = low.indexOf(nl, i + nl.length)) marks.push([i, i + nl.length]);
  }
  if (!marks.length || low.length !== text.length) return H(text);
  marks.sort((a, b) => a[0] - b[0]);
  let out = "", pos = 0;
  for (const [a, b] of marks) {
    if (b <= pos) continue;
    const s = Math.max(a, pos);
    out += H(text.slice(pos, s)) + "<mark>" + H(text.slice(s, b)) + "</mark>";
    pos = b;
  }
  return out + H(text.slice(pos));
}

function say(msg, opts) {
  if (typeof toast === "function") { try { toast(msg, opts); return; } catch { /* fall through */ } }
  const st = $id("lx-status"); if (st) st.dataset.flash = msg;
}

async function copyText(text, done) {
  try {
    await navigator.clipboard.writeText(text);
    say(done);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    say(ok ? done : "Could not copy. Select the text and press Ctrl+C.");
  }
}

const hasDetail = () => typeof Detail !== "undefined" && Detail && typeof Detail.open === "function";

function spark(host, values, o = {}) {
  if (typeof sparkline === "function") {
    try { sparkline(host, values, o); return; } catch { /* use the local one */ }
  }
  const W = 240, Hh = o.height || 36, max = o.max || Math.max(1, ...values);
  const n = values.length;
  const X = i => n <= 1 ? W / 2 : (i / (n - 1)) * W;
  const Y = v => Hh - 2 - (v / max) * (Hh - 4);
  const pts = values.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
  const col = getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim();
  host.innerHTML = `<svg viewBox="0 0 ${W} ${Hh}" preserveAspectRatio="none" width="100%" height="${Hh}" aria-hidden="true">
    ${n > 1 ? `<path d="M0,${Hh} L${pts.join(" L")} L${W},${Hh} Z" fill="${col}" opacity=".13"/>` : ""}
    <polyline points="${pts.join(" ")}" fill="none" stroke="${col}" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}

/* ------------------------------------------------------------- API */
function apiError(info) { return Object.assign(new Error(info.error), { info }); }

async function getJSON(url, signal) {
  let r;
  try {
    r = await fetch(url, { signal, cache: "no-store" });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw apiError({ error: "Could not reach the log server.", detail: e.message,
                     fix: "Check that the web app is still running, then try again." });
  }
  let j = null;
  try { j = await r.json(); } catch { j = null; }
  if (!r.ok) {
    throw apiError(j && j.error ? j : { error: `The server answered with HTTP ${r.status}.`, detail: "",
                                        fix: "Reload the page. If it keeps happening, check the server log." });
  }
  return j;
}

function errorHtml(info, retryId) {
  let fix = H(info.fix || "");
  const m = /^(.*?:\s*)(.+)$/.exec(info.fix || "");
  if (m && /make |python|\//.test(m[2])) fix = `${H(m[1])}<code>${H(m[2])}</code>`;
  return `<div class="sqlerror lx-error" role="alert">
    <b>${H(info.error)}</b>
    ${info.detail ? `<div class="d">${H(info.detail)}</div>` : ""}
    ${info.fix ? `<div class="lx-fix">${fix}</div>` : ""}
    ${retryId ? `<div class="lx-errbtn"><button class="btn" type="button" id="${retryId}">Try again</button></div>` : ""}
  </div>`;
}

let META = null;
const metaReady = getJSON("/api/logs/meta").then(j => (META = j)).catch(e => { if (e.info) showFatal(e.info); return null; });

function showFatal(info) {
  const f = $id("lx-fatal");
  f.innerHTML = `<div class="card lx-fatalcard">${errorHtml(info)}</div>`;
  f.hidden = false;
}

function logBounds() {
  if (!META || !META.files || !META.files.length) return null;
  return { start: toMs(META.files[0].from), end: toMs(META.files.at(-1).to) };
}

/* ------------------------------------------------------------- URL hash */
/* Other owners keep their own keys here (range=, detail=); only ours change. */
const enc = v => encodeURIComponent(v).replace(/%(?:3A|2F|40|2C)/gi, decodeURIComponent);
function readHash() {
  const out = new Map(), h = location.hash.replace(/^#/, "");
  for (const part of h.split("&")) {
    if (!part) continue;
    const i = part.indexOf("=");
    let k = i < 0 ? part : part.slice(0, i), v = i < 0 ? "" : part.slice(i + 1);
    try { k = decodeURIComponent(k); } catch { /* keep raw */ }
    try { v = decodeURIComponent(v); } catch { /* keep raw */ }
    out.set(k, v);
  }
  return out;
}
function writeHash(updates) {
  const m = readHash();
  for (const [k, v] of Object.entries(updates)) {
    if (v == null || v === "") m.delete(k); else m.set(k, String(v));
  }
  const s = [...m].map(([k, v]) => enc(k) + (v === "" ? "" : "=" + enc(v))).join("&");
  const url = location.pathname + location.search + (s ? "#" + s : "");
  if (url !== location.pathname + location.search + location.hash) history.replaceState(history.state, "", url);
}

/* ============================================================ mode switch */
let mode = "search";
function setMode(m, { write = true } = {}) {
  mode = m === "replay" ? "replay" : "search";
  document.querySelectorAll(".lx-modes button").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $id("mode-search").hidden = mode !== "search";
  $id("mode-replay").hidden = mode !== "replay";
  if (mode !== "replay") Replay.pause();
  if (write) {
    if (mode === "replay") writeHash({ mode: "replay", start: $id("r-start").value, speed: Replay.speed });
    else writeHash({ mode: null, start: null, speed: null });
  }
  if (mode === "replay") Replay.drawAll();
}
document.querySelectorAll(".lx-modes button").forEach(b =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));

/* ================================================================ SEARCH */
const KEYS = ["q", "host", "path", "status", "method", "from", "to"];
const form = $id("lx-form");
const F = { q: $id("f-q"), host: $id("f-host"), path: $id("f-path"), status: $id("f-status"),
            method: $id("f-method"), from: $id("f-from"), to: $id("f-to") };
const tbody = $id("lx-rows");

const S = { filters: {}, rows: [], cursor: null, target: PAGE, mb: 0, lines: 0, reached: null, secs: 0,
            done: false, stopped: false, running: false, error: null, ctrl: null, sig: null, started: false };

function normFilters(src) {
  const f = {};
  for (const k of KEYS) f[k] = String(src[k] ?? "").trim();
  if (f.status && !/^([1-5]xx|[1-5]\d\d)$/i.test(f.status)) f.status = "";
  f.status = f.status.toLowerCase();
  f.method = f.method.toUpperCase();
  if (f.method && !/^[A-Z]{1,12}$/.test(f.method)) f.method = "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(f.from)) f.from += "T00:00";
  if (/^\d{4}-\d{2}-\d{2}$/.test(f.to)) f.to += "T23:59";
  return f;
}
const sigOf = f => KEYS.map(k => f[k] || "").join("");
const hasAny = f => KEYS.some(k => f[k]);

function ensureOption(sel, v, label) {
  if (v && ![...sel.options].some(o => o.value === v)) sel.add(new Option(label || v, v));
}
function setForm(f) {
  for (const k of KEYS) {
    if (k === "status") ensureOption(F.status, f.status);
    if (k === "method") ensureOption(F.method, f.method);
    F[k].value = f[k] || "";
  }
}
const formFilters = () => normFilters(Object.fromEntries(KEYS.map(k => [k, F[k].value])));

function validate(f) {
  const re = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;
  for (const [k, name] of [["from", "start"], ["to", "end"]]) {
    if (!f[k]) continue;
    if (!re.test(f[k]) || Number.isNaN(toMs(f[k])))
      return `The ${name} time is not a valid date and time. Pick one with the date picker, or clear it.`;
    if (f[k].slice(0, 16) < LOG_MIN || f[k].slice(0, 16) > LOG_MAX)
      return `The logs cover 1 Jul to 31 Aug 1995. Pick a ${name} time in that range, or clear it.`;
  }
  if (f.from && f.to && f.from > f.to) return "The start time is after the end time. Swap them, or clear one of them.";
  return null;
}
function formError(msg) {
  const e = $id("f-error");
  e.textContent = msg || ""; e.hidden = !msg;
}

function startSearch(f, { write = true } = {}) {
  f = normFilters(f);
  setForm(f);
  const err = validate(f);
  formError(err);
  if (err) return;
  if (S.ctrl) S.ctrl.abort();
  Object.assign(S, { filters: f, rows: [], cursor: null, target: PAGE, mb: 0, lines: 0, reached: null, secs: 0,
                     done: false, stopped: false, running: false, error: null, ctrl: null, sig: sigOf(f), started: true });
  if (write) writeHash(Object.fromEntries(KEYS.map(k => [k, f[k] || null])));
  tbody.innerHTML = "";
  $id("lx-empty").hidden = true;
  renderScope(); renderStatus(); renderFacets(); renderCount();
  pump();
}

async function pump() {
  if (S.running) return;
  const ctrl = new AbortController();
  S.ctrl = ctrl; S.running = true; S.stopped = false; S.error = null;
  renderStatus(); renderMore();
  const t0 = performance.now();
  let lastFacet = 0;
  try {
    while (S.rows.length < S.target) {
      const f = S.filters, p = new URLSearchParams();
      for (const k of ["q", "host", "path", "method", "status", "to"]) if (f[k]) p.set(k, f[k]);
      if (S.cursor) p.set("cursor", S.cursor); else if (f.from) p.set("from", f.from);
      p.set("limit", String(clamp(S.target - S.rows.length, 1, 500)));
      const j = await getJSON("/api/logs/search?" + p.toString(), ctrl.signal);
      if (ctrl !== S.ctrl) return;
      S.mb += +j.scanned_mb || 0; S.lines += +j.scanned_lines || 0;
      if (j.reached) S.reached = j.reached;
      S.cursor = j.cursor;
      appendRows(j.rows || []);
      if (!j.cursor) { S.done = true; break; }
      renderStatus();
      if (performance.now() - lastFacet > 400) { renderFacets(); lastFacet = performance.now(); }
    }
  } catch (e) {
    if (e.name !== "AbortError" && ctrl === S.ctrl) S.error = e.info || { error: e.message, fix: "Try the search again." };
  } finally {
    if (ctrl === S.ctrl) {
      S.running = false;
      S.secs += (performance.now() - t0) / 1000;
      renderStatus(); renderFacets(); renderCount(); renderMore(); renderEmpty();
    }
  }
}

function stopSearch() {
  if (!S.running || !S.ctrl) return;
  S.stopped = true;
  S.ctrl.abort();
}

function appendRows(raw) {
  if (!raw.length) return;
  const f = S.filters, start = S.rows.length;
  const html = raw.map((a, k) => {
    const r = { i: start + k, ts: a[0], host: a[1], method: a[2], path: a[3], status: a[4], bytes: a[5] };
    S.rows.push(r);
    return `<tr class="rowlink lx-row" tabindex="0" data-i="${r.i}" aria-expanded="false">
      <td class="lx-c-time">${H(tSec(r.ts))}</td>
      <td class="lx-c-host mono" title="${H(r.host)}">${hl(r.host, [f.host, f.q])}</td>
      <td class="lx-c-method">${H(r.method)}</td>
      <td class="lx-c-path mono" title="${H(r.path)}">${hl(r.path, [f.path, f.q])}</td>
      <td class="lx-c-status">${pill(r.status)}</td>
      <td class="lx-c-bytes num">${N(r.bytes)}</td></tr>`;
  }).join("");
  tbody.insertAdjacentHTML("beforeend", html);
  $id("lx-empty").hidden = true;
  renderCount();
}

/* --- status line ------------------------------------------------------ */
function progressFrac() {
  const b = logBounds();
  if (S.done) return 1;
  if (!b || !S.reached) return 0;
  const a = S.filters.from ? toMs(S.filters.from) : b.start;
  const z = S.filters.to ? toMs(S.filters.to) + 59000 : b.end;
  return z > a ? clamp((toMs(S.reached) - a) / (z - a), 0, 1) : 0;
}

function renderStatus() {
  const el = $id("lx-status");
  if (!S.started) { el.innerHTML = ""; return; }
  const n = S.rows.length, m = `${N(n)} match${n === 1 ? "" : "es"}`;
  const mb = `${S.mb.toFixed(S.mb < 10 ? 1 : 0)} MB`;
  const upto = S.reached ? `, up to ${tMin(S.reached)}` : "";
  const pct = Math.round(progressFrac() * 100);
  const bar = `<div class="lx-track" role="progressbar" aria-label="Search progress through the time range"
      aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><div class="lx-fill" style="width:${pct}%"></div></div>`;

  if (S.error) {
    el.innerHTML = errorHtml(S.error, "lx-retry");
    $id("lx-retry").addEventListener("click", () => pump());
    return;
  }
  if (S.running) {
    el.innerHTML = `<div class="lx-statusline"><span class="spin" aria-hidden="true"></span>
      <span>Scanned ${mb}${upto}, ${m}</span>
      <button class="btn" type="button" id="lx-stop">Stop</button></div>${bar}`;
    $id("lx-stop").addEventListener("click", stopSearch);
    return;
  }
  let text;
  if (S.done) text = `Search complete. ${m} in ${mb} scanned, ${S.secs.toFixed(1)} s.`;
  else if (S.stopped) text = `Stopped after scanning ${mb}${upto}. ${m} so far.`;
  else text = `Showing the first ${m}. Scanned ${mb}${upto}.`;
  el.innerHTML = `<div class="lx-statusline lx-idle"><span>${H(text)}</span></div>${S.done ? "" : bar}`;
}

function renderCount() {
  const n = S.rows.length;
  $id("lx-count").textContent = !S.started ? "Matching lines"
    : `${N(n)} matching line${n === 1 ? "" : "s"}${S.cursor && !S.running && !S.done ? " loaded" : ""}`;
  $id("lx-csv").disabled = !n;
}

function renderScope() {
  const f = S.filters, parts = [];
  if (f.q) parts.push(`text contains <b class="mono">${H(f.q)}</b>`);
  if (f.host) parts.push(`host contains <b class="mono">${H(f.host)}</b>`);
  if (f.path) parts.push(`path contains <b class="mono">${H(f.path)}</b>`);
  if (f.status) parts.push(`status <b>${H(f.status)}</b>`);
  if (f.method) parts.push(`method <b>${H(f.method)}</b>`);
  const from = f.from ? tMin(f.from) : "1 Jul 00:00", to = f.to ? tMin(f.to) : "31 Aug 23:59";
  const range = `${from} to ${to} 1995`;
  $id("lx-scope").innerHTML = (parts.length ? `Where ${parts.join(", ")}. ` : "Every line. ") + H(range);
}

function renderMore() {
  const wrap = $id("lx-more-wrap"), btn = $id("lx-more");
  const show = S.started && !S.running && !S.done && !S.error && !!S.cursor;
  wrap.hidden = !show;
  if (show) btn.textContent = S.rows.length < S.target ? "Keep searching" : `Load ${PAGE} more`;
}
$id("lx-more").addEventListener("click", () => {
  if (S.rows.length >= S.target) S.target = S.rows.length + PAGE;
  pump();
});

function renderEmpty() {
  const e = $id("lx-empty");
  if (!S.started || S.rows.length || S.running || S.error) { e.hidden = true; return; }
  const scanned = `${S.mb.toFixed(S.mb < 10 ? 1 : 0)} MB`;
  if (S.done) {
    e.innerHTML = `<div class="lx-empty"><h3>No lines match these filters</h3>
      <p>Searched every line in the range (${H(scanned)}). Try a shorter host or path fragment, a status class
      instead of an exact code, or a wider time range.</p>
      ${hasAny(S.filters) ? `<button class="btn" type="button" data-empty="clear">Clear filters</button>` : ""}</div>`;
  } else {
    e.innerHTML = `<div class="lx-empty"><h3>No matches yet</h3>
      <p>Scanned ${H(scanned)}${S.reached ? ` up to ${H(tMin(S.reached))}` : ""} without a match. The rest of the range has not been searched.</p>
      <button class="btn" type="button" data-empty="more">Keep searching</button></div>`;
  }
  e.hidden = false;
}
$id("lx-empty").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-empty]");
  if (!b) return;
  if (b.dataset.empty === "clear") startSearch({});
  else pump();
});

/* --- rows: expand, actions, keyboard --------------------------------- */
function detailHtml(r) {
  const st = STATUS_TEXT[r.status] ? `${r.status} ${STATUS_TEXT[r.status]}` : String(r.status);
  const bits = [
    `<button class="btn" type="button" data-act="host">Filter by this host</button>`,
    `<button class="btn" type="button" data-act="path">Filter by this path</button>`,
    hasDetail() ? `<button class="btn" type="button" data-act="hostdetail">Host details</button>` : "",
    hasDetail() ? `<button class="btn" type="button" data-act="pathdetail">Path details</button>` : "",
    `<button class="btn" type="button" data-act="replay">Replay from here</button>`,
    `<button class="btn" type="button" data-act="copy">Copy line</button>`,
  ].join("");
  return `<div class="lx-detail-body">
    <pre class="lx-raw"><code>${H(ncsa(r))}</code></pre>
    <dl class="lx-fields">
      <div><dt>Time</dt><dd>${H(tLong(r.ts))} <span class="lx-muted">-0400</span></dd></div>
      <div><dt>Status</dt><dd>${pill(r.status)} <span class="lx-muted">${H(STATUS_TEXT[r.status] || "")}</span></dd></div>
      <div><dt>Size</dt><dd>${H(bytesH(r.bytes))} <span class="lx-muted">(${N(r.bytes)} bytes)</span></dd></div>
      <div><dt>Request</dt><dd class="mono">${H(r.method || "none")}</dd></div>
    </dl>
    <div class="actions lx-actions">${bits}</div>
    <p class="lx-muted lx-small">Rebuilt from the parsed fields, so the protocol is shown as HTTP/1.0. ${H(st)}.</p>
  </div>`;
}

function toggleRow(tr, force) {
  const open = tr.getAttribute("aria-expanded") === "true";
  const want = force ?? !open;
  if (want === open) return;
  const i = +tr.dataset.i;
  if (!want) {
    const nx = tr.nextElementSibling;
    if (nx && nx.classList.contains("lx-detail")) nx.remove();
    tr.setAttribute("aria-expanded", "false");
    return;
  }
  const r = S.rows[i];
  const id = `lx-d-${i}`;
  tr.insertAdjacentHTML("afterend",
    `<tr class="lx-detail" id="${id}" data-i="${i}"><td colspan="6">${detailHtml(r)}</td></tr>`);
  tr.setAttribute("aria-expanded", "true");
  tr.setAttribute("aria-controls", id);
}

tbody.addEventListener("click", ev => {
  const act = ev.target.closest("button[data-act]");
  if (act) {
    const r = S.rows[+act.closest("tr.lx-detail").dataset.i];
    rowAction(act.dataset.act, r, act);
    return;
  }
  const tr = ev.target.closest("tr.lx-row");
  if (!tr) return;
  if (String(getSelection && getSelection()).length) return;   // let people select text
  toggleRow(tr);
});
tbody.addEventListener("keydown", ev => {
  const tr = ev.target.closest("tr.lx-row");
  if (!tr || ev.target !== tr) return;
  if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggleRow(tr); }
  else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    let n = tr;
    do { n = ev.key === "ArrowDown" ? n.nextElementSibling : n.previousElementSibling; }
    while (n && !n.classList.contains("lx-row"));
    if (n) n.focus();
  } else if (ev.key === "Escape") toggleRow(tr, false);
});

function rowAction(act, r, btn) {
  if (!r) return;
  if (act === "host") startSearch({ ...S.filters, host: r.host });
  else if (act === "path") startSearch({ ...S.filters, path: r.path });
  else if (act === "hostdetail" && hasDetail()) Detail.open("host", r.host);
  else if (act === "pathdetail" && hasDetail()) Detail.open("endpoint", r.path);
  else if (act === "copy") copyText(ncsa(r), "Line copied");
  else if (act === "replay") {
    Replay.seek(isoOf(toMs(r.ts) - 15000));
    setMode("replay");
    Replay.play();
    $id("r-play").focus();
  }
}

/* --- facets ----------------------------------------------------------- */
function topN(rows, key, n) {
  const m = new Map();
  for (const r of rows) m.set(r[key], (m.get(r[key]) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]))).slice(0, n);
}

function facetList(kind, entries, total, isOn, labelFn, mono) {
  const max = entries.length ? entries[0][1] : 1;
  return `<ul class="lx-facet">${entries.map(([v, c]) => {
    const on = isOn(v);
    const verb = on ? "Remove filter" : "Filter to";
    return `<li><button type="button" data-facet="${kind}" data-v="${H(v)}" aria-pressed="${on}"
        aria-label="${H(`${verb} ${kind} ${v}, ${N(c)} of ${N(total)} loaded lines`)}" title="${H(v)}">
      <span class="fill" style="width:${(100 * c / max).toFixed(1)}%"></span>
      <span class="lab${mono ? " mono" : ""}">${labelFn(v)}</span><span class="n">${N(c)}</span></button></li>`;
  }).join("")}</ul>`;
}

function renderFacets() {
  const host = $id("lx-facets"), rows = S.rows, f = S.filters;
  if (!rows.length) {
    host.innerHTML = `<h2>Facets</h2><p class="note lx-sidenote">${S.running
      ? "Counts appear as matches arrive." : "Counts of status, host and path appear here once lines match."}</p>`;
    return;
  }
  const vals = [], B = 40;
  const t0 = toMs(rows[0].ts), t1 = toMs(rows.at(-1).ts);
  for (let i = 0; i < B; i++) vals.push(0);
  for (const r of rows) {
    const k = t1 > t0 ? Math.min(B - 1, Math.floor((toMs(r.ts) - t0) / (t1 - t0) * B)) : 0;
    vals[k]++;
  }
  host.innerHTML = `
    <h2>Facets</h2>
    <p class="note lx-sidenote">From the ${N(rows.length)} loaded line${rows.length === 1 ? "" : "s"}. Select a value to filter.</p>
    <h3>Matches over time</h3>
    <div class="lx-spark" id="lx-spark"></div>
    <div class="lx-sparkaxis"><span>${H(tMin(rows[0].ts))}</span><span>${H(tMin(rows.at(-1).ts))}</span></div>
    <h3>Status</h3>
    ${facetList("status", topN(rows, "status", 8), rows.length, v => String(v) === f.status, v => pill(v))}
    <h3>Hosts</h3>
    ${facetList("host", topN(rows, "host", 8), rows.length, v => v === f.host, v => H(v), true)}
    <h3>Paths</h3>
    ${facetList("path", topN(rows, "path", 8), rows.length, v => v === f.path, v => H(v), true)}`;
  spark($id("lx-spark"), vals, { height: 40, area: true });
}

$id("lx-facets").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-facet]");
  if (!b) return;
  const k = b.dataset.facet, v = b.dataset.v;
  const on = b.getAttribute("aria-pressed") === "true";
  startSearch({ ...S.filters, [k]: on ? "" : v });
});

/* --- form, examples, tools ------------------------------------------- */
form.addEventListener("submit", ev => { ev.preventDefault(); startSearch(formFilters()); });
[F.status, F.method].forEach(sel => sel.addEventListener("change", () => startSearch(formFilters())));
$id("f-clear").addEventListener("click", () => { startSearch({}); F.q.focus(); });
$id("lx-examples").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-ex]");
  if (!b) return;
  const f = {};
  for (const part of b.dataset.ex.split("&")) { const [k, v] = part.split("="); f[k] = v; }
  startSearch(f);
});
$id("lx-copylink").addEventListener("click", () => copyText(location.href, "Link copied"));

function csvCell(v) {
  let s = String(v ?? "");
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;               // keep spreadsheets from evaluating it
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
$id("lx-csv").addEventListener("click", () => {
  if (!S.rows.length) return;
  const lines = [["time", "host", "method", "path", "status", "bytes"].join(",")];
  for (const r of S.rows) lines.push([r.ts, r.host, r.method, r.path, r.status, r.bytes].map(csvCell).join(","));
  const f = S.filters;
  const slug = KEYS.filter(k => f[k]).map(k => `${k}-${f[k]}`).join("_").replace(/[^A-Za-z0-9_.-]+/g, "-").slice(0, 80);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv" }));
  a.download = `access-log-${slug || "all"}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  say(`Downloaded ${N(S.rows.length)} rows`);
});

/* ================================================================ REPLAY */
const Replay = (() => {
  const tail = $id("r-tail");
  const MAX_DOM = 400, CAP_PER_FRAME = 160;
  const R = {
    playing: false, speed: 60, simT: toMs("1995-07-01T09:00"), buf: [], bi: 0, fetchedUntil: 0,
    fetching: false, ctrl: null, gen: 0, lastFrame: 0, raf: 0, follow: true, played: 0, shown: 0,
    notDrawn: 0, fPlayed: 0, fShown: 0, recent: [], noMore: false, ended: false, error: null, statsAt: 0, lineH: 22,
    ids: new Map(), nextId: 1, noticeTimer: 0, buffering: false,
  };
  const chunkSecs = () => clamp(R.speed * 10, 10, 3600);
  const filterText = () => $id("r-filter").value.trim().toLowerCase();
  const filterStatus = () => $id("r-status").value;
  const matches = r => {
    const s = filterStatus(), t = filterText();
    if (s && String(r.status)[0] !== s) return false;
    if (t && !(r.host.toLowerCase().includes(t) || r.path.toLowerCase().includes(t) || r.method.toLowerCase().includes(t)))
      return false;
    return true;
  };

  function reset(startMs) {
    R.gen++;
    if (R.ctrl) R.ctrl.abort();
    Object.assign(R, { simT: startMs, fetchedUntil: startMs, buf: [], bi: 0, fetching: false, ctrl: null,
                       played: 0, shown: 0, notDrawn: 0, fPlayed: 0, fShown: 0, recent: [], noMore: false, ended: false, error: null,
                       buffering: false });
    R.ids.clear();
    tail.innerHTML = "";
    $id("r-inspect").innerHTML = "";
    notice(null);
    drawAll();
  }

  /* --- fetching ahead ------------------------------------------------- */
  function gaps() {
    const out = [];
    const fl = (META && META.files) || [];
    for (let i = 0; i + 1 < fl.length; i++) {
      const a = toMs(fl[i].to) + 1000, b = toMs(fl[i + 1].from);
      if (b > a) out.push({ a, b, fromIso: fl[i].to, toIso: fl[i + 1].from });
    }
    return out;
  }

  function ensureBuffer() {
    if (R.fetching || R.noMore || R.error) return;
    if (R.fetchedUntil - R.simT > chunkSecs() * 1000 * 0.6) return;
    if (R.buf.length - R.bi > 40000) return;
    fetchChunk();
  }

  async function fetchChunk() {
    await metaReady;
    if (R.fetching || R.noMore || R.error) return;
    R.fetching = true;
    const gen = R.gen, ctrl = new AbortController();
    R.ctrl = ctrl;
    const from = R.fetchedUntil, secs = chunkSecs();
    try {
      const b = logBounds();
      if (b && from > b.end) { pushMarker({ t: from, end: true }); R.noMore = true; return; }
      const j = await getJSON(`/api/logs/window?start=${isoOf(from)}&seconds=${secs}`, ctrl.signal);
      if (gen !== R.gen) return;
      let rows = j.rows || [];
      let until = from + secs * 1000;
      if (j.truncated && rows.length) {
        const lastSec = rows.at(-1)[0];
        until = toMs(lastSec);
        rows = rows.filter(r => r[0] !== lastSec);
      }
      if (R.bi > 4000) { R.buf = R.buf.slice(R.bi); R.bi = 0; }
      for (const a of rows)
        R.buf.push({ t: toMs(a[0]), ts: a[0], host: a[1], method: a[2], path: a[3], status: a[4], bytes: a[5] });
      // The trace has holes between files. When this window reaches into one,
      // leave a marker where the traffic stops; the clock jumps when it gets there.
      const g = gaps().find(g => from < g.b && until > g.a);
      if (g) {
        pushMarker({ t: Math.max(from, g.a), gap: g });
        R.fetchedUntil = g.b;
      } else {
        R.fetchedUntil = until;
        if (b && until > b.end && !rows.length) { pushMarker({ t: Math.max(from, b.end + 1000), end: true }); R.noMore = true; }
      }
    } catch (e) {
      if (e.name === "AbortError" || gen !== R.gen) return;
      R.error = e.info || { error: e.message, fix: "Press Play to try again." };
      pause();
      notice(null);
      $id("r-inspect").innerHTML = errorHtml(R.error, "r-retry");
      $id("r-retry").addEventListener("click", () => { R.error = null; $id("r-inspect").innerHTML = ""; play(); });
    } finally {
      if (gen === R.gen) R.fetching = false;
    }
  }
  function pushMarker(m) { R.buf.push(m); }

  /* --- the clock ------------------------------------------------------- */
  function frame(now) {
    R.raf = 0;
    if (!R.playing) return;
    const dt = Math.min(250, Math.max(0, now - (R.lastFrame || now)));
    R.lastFrame = now;
    let target = R.simT + dt * R.speed;
    // never run the clock ahead of lines we have not fetched yet
    R.buffering = !R.noMore && target > R.fetchedUntil;
    if (R.buffering) target = Math.max(R.simT, R.fetchedUntil);
    R.simT = target;
    release();
    ensureBuffer();
    drawClock();
    if (now - R.statsAt > 200) { drawStats(); R.statsAt = now; }
    if (R.playing) R.raf = requestAnimationFrame(frame);
  }

  function release() {
    const due = [];
    let sys = null;
    while (R.bi < R.buf.length && R.buf[R.bi].t <= R.simT) {
      const r = R.buf[R.bi++];
      if (r.gap) {
        const g = r.gap;
        R.simT = Math.max(R.simT, g.b);
        sys = `No traffic was logged between ${tMin(g.fromIso)} and ${D(g.b).getUTCDate()} ${MON[D(g.b).getUTCMonth()]}; skipping ahead`;
        due.push({ sys });
        notice(sys);
        continue;
      }
      if (r.end) {
        const b = logBounds();
        const msg = `Reached the end of the logs${b ? ` at ${tSec(isoOf(b.end))}` : ""}.`;
        due.push({ sys: msg });
        R.ended = true;
        notice(msg);
        pause();
        break;
      }
      R.played++; R.fPlayed++;
      if (!matches(r)) continue;
      R.fShown++;
      R.recent.push(r);
      due.push(r);
    }
    if (!due.length) return;
    R.shown += due.filter(d => !d.sys).length;
    let draw = due;
    if (due.length > CAP_PER_FRAME) {
      // More lines fell due in one frame than anyone can read. Draw the newest;
      // the dropped ones still count in the statistics.
      R.notDrawn += due.length - CAP_PER_FRAME;
      draw = due.slice(-CAP_PER_FRAME);
      if (sys && !draw.some(d => d.sys)) draw.unshift({ sys });
    }
    appendLines(draw);
  }

  function lineHtml(r, id) {
    if (r.sys) return `<div class="lx-line lx-sys" data-id="${id}">${H(r.sys)}</div>`;
    return `<div class="lx-line s${sclass(r.status)}" data-id="${id}">` +
      `<span class="t">${tClock(r.t)}</span><span class="st">${H(r.status)}</span>` +
      `<span class="m">${H(r.method)}</span><span class="p">${H(r.path)}</span>` +
      `<span class="h">${H(r.host)}</span><span class="b">${H(bytesH(r.bytes))}</span></div>`;
  }

  function appendLines(items) {
    let html = "";
    for (const r of items) {
      const id = R.nextId++;
      if (!r.sys) R.ids.set(id, r);
      html += lineHtml(r, id);
    }
    tail.insertAdjacentHTML("beforeend", html);
    let removed = 0;
    while (tail.childElementCount > MAX_DOM) {
      const first = tail.firstElementChild;
      R.ids.delete(+first.dataset.id);
      first.remove();
      removed++;
    }
    if (R.follow) {
      R.autoScroll = true;
      tail.scrollTop = tail.scrollHeight;
    } else if (removed) {
      const lh = tail.firstElementChild ? tail.firstElementChild.offsetHeight || R.lineH : R.lineH;
      tail.scrollTop = Math.max(0, tail.scrollTop - removed * lh);
    }
  }

  /* --- drawing --------------------------------------------------------- */
  function drawClock() {
    $id("r-clock").textContent = tClock(R.simT);
    $id("r-date").textContent = tDay(R.simT);
    const st = $id("r-state");
    let s;
    if (R.error) s = "Stopped by an error";
    else if (R.ended) s = "End of the logs";
    else if (!R.playing) s = "Paused";
    else if (R.buffering && R.fetching) s = "Loading lines";
    else s = `Playing at ${R.speed}x`;
    st.textContent = s;
    st.classList.toggle("on", R.playing && !R.buffering);
    const f = filterText() || filterStatus();
    $id("r-count").textContent = !R.played ? "No lines played yet"
      : f ? `${N(R.fShown)} of ${N(R.fPlayed)} lines match since the filter was set`
          : `${N(R.played)} line${R.played === 1 ? "" : "s"} played`;
  }

  function drawStats() {
    const cut = R.simT - 60000;
    let k = 0;
    while (k < R.recent.length && R.recent[k].t <= cut) k++;
    if (k) R.recent = R.recent.slice(k);
    const nowSec = Math.floor(R.simT / 1000), per = new Array(60).fill(0);
    for (const r of R.recent) {
      const idx = 59 - (nowSec - Math.floor(r.t / 1000));
      if (idx >= 0 && idx < 60) per[idx]++;
    }
    const avg = R.recent.length / 60, peak = Math.max(0, ...per);
    $id("r-rps").textContent = avg < 10 ? avg.toFixed(1) : N(Math.round(avg));
    $id("r-rps-peak").textContent = R.recent.length ? `average, peak ${N(peak)} in one second` : "average";
    spark($id("r-spark"), per, { height: 44, area: true, max: Math.max(4, peak) });

    const cls = { 2: 0, 3: 0, 4: 0, 5: 0 };
    for (const r of R.recent) { const c = sclass(r.status); if (c in cls) cls[c]++; }
    const tot = R.recent.length;
    const names = { 2: "2xx success", 3: "3xx redirect or cached", 4: "4xx client error", 5: "5xx server error" };
    const filtered = !!(filterText() || filterStatus());
    $id("r-mix-note").textContent = `Last simulated minute${filtered ? ", matching lines only" : ""}`;
    $id("r-paths-note").textContent = `Last simulated minute${filtered ? ", matching lines only" : ""}. Select one to filter the tail.`;
    $id("r-mix").innerHTML = !tot ? `<p class="empty-inline lx-muted">No lines in the last simulated minute.</p>`
      : `<div class="lx-mix" role="img" aria-label="${H(Object.keys(cls).map(c => `${names[c]} ${cls[c]}`).join(", "))}">
          ${Object.keys(cls).filter(c => cls[c]).map(c => `<span class="m${c}" style="flex:${cls[c]}"></span>`).join("")}</div>
         <ul class="lx-legend">${Object.keys(cls).map(c => `<li><i class="m${c}"></i><span>${names[c]}</span>
           <b>${N(cls[c])}</b><span class="lx-muted">${pctLabel(cls[c], tot)}</span></li>`).join("")}</ul>`;

    const tops = topN(R.recent, "path", 6);
    const cur = filterText();
    $id("r-paths").innerHTML = !tops.length ? `<li class="empty-inline lx-muted">No lines in the last simulated minute.</li>`
      : tops.map(([p, c]) => `<li><button type="button" data-path="${H(p)}" aria-pressed="${cur === p.toLowerCase()}"
          aria-label="${H(`Filter the tail to ${p}, ${c} requests`)}" title="${H(p)}">
          <span class="fill" style="width:${(100 * c / tops[0][1]).toFixed(1)}%"></span>
          <span class="lab mono">${H(p)}</span><span class="n">${N(c)}</span></button></li>`).join("");
  }

  function pctLabel(c, tot) {
    if (!tot || !c) return "0%";
    const v = 100 * c / tot;
    return v < 1 ? "<1%" : Math.round(v) + "%";
  }

  function drawAll() { drawClock(); drawStats(); syncButtons(); }

  function notice(msg) {
    const n = $id("r-notice");
    clearTimeout(R.noticeTimer);
    if (!msg) { n.hidden = true; n.textContent = ""; return; }
    n.textContent = msg; n.hidden = false;
    if (!R.ended) R.noticeTimer = setTimeout(() => { n.hidden = true; }, 6000);
  }

  function syncButtons() {
    const b = $id("r-play");
    b.textContent = R.playing ? "Pause" : (R.ended ? "Replay from start" : "Play");
    b.setAttribute("aria-pressed", String(R.playing));
    document.querySelectorAll("#r-speed button").forEach(x =>
      x.setAttribute("aria-pressed", String(+x.dataset.speed === R.speed)));
    const f = $id("r-follow");
    f.setAttribute("aria-pressed", String(R.follow));
    f.textContent = R.follow ? "Following" : "Follow";
    $id("r-jump").hidden = R.follow || !tail.childElementCount;
  }

  /* --- controls -------------------------------------------------------- */
  function play() {
    if (R.ended) { reset(toMs($id("r-start").value) || toMs("1995-07-01T09:00")); }
    if (R.error) { R.error = null; $id("r-inspect").innerHTML = ""; }
    if (R.playing) return;
    R.playing = true;
    R.lastFrame = 0;
    ensureBuffer();
    syncButtons(); drawClock();
    if (!R.raf) R.raf = requestAnimationFrame(frame);
  }
  function pause() {
    R.playing = false;
    if (R.raf) cancelAnimationFrame(R.raf);
    R.raf = 0;
    syncButtons(); drawClock();
  }
  function seek(iso) {
    let v = String(iso || "");
    const b = logBounds();
    let ms = toMs(v);
    if (Number.isNaN(ms)) ms = toMs("1995-07-01T09:00");
    if (b) ms = clamp(ms, b.start, b.end);
    else ms = clamp(ms, toMs(LOG_MIN), toMs(LOG_MAX));
    $id("r-start").value = isoOf(ms);
    reset(ms);
    if (mode === "replay") writeHash({ start: isoOf(ms) });
  }
  function setSpeed(s) {
    s = [1, 10, 60, 600].includes(+s) ? +s : 60;
    R.speed = s;
    syncButtons(); drawClock();
    if (mode === "replay") writeHash({ speed: s });
  }
  function setFollow(on) {
    R.follow = on;
    if (on) { R.autoScroll = true; tail.scrollTop = tail.scrollHeight; }
    syncButtons();
  }

  $id("r-play").addEventListener("click", () => (R.playing ? pause() : play()));
  $id("r-speed").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-speed]"); if (b) setSpeed(b.dataset.speed);
  });
  $id("r-start").addEventListener("change", () => {
    if (!$id("r-start").value) return;
    const was = R.playing;
    pause(); seek($id("r-start").value);
    if (was) play();
  });
  $id("r-follow").addEventListener("click", () => setFollow(!R.follow));
  $id("r-jump").addEventListener("click", () => { setFollow(true); tail.focus(); });
  /* Auto-scroll always lands on the bottom in the same task as the append, so a
     scroll event that reports "not at the bottom" can only come from the user. */
  const stopFollowing = () => { if (R.follow) { R.follow = false; syncButtons(); } };
  tail.addEventListener("scroll", () => {
    const atBottom = tail.scrollHeight - tail.scrollTop - tail.clientHeight < 8;
    if (R.follow && !atBottom) stopFollowing();
    else if (!R.follow && atBottom) $id("r-jump").hidden = true;
  }, { passive: true });
  tail.addEventListener("wheel", ev => { if (ev.deltaY < 0) stopFollowing(); }, { passive: true });
  tail.addEventListener("keydown", ev => {
    if (["ArrowUp", "PageUp", "Home"].includes(ev.key)) stopFollowing();
  });

  function refilter() {
    for (const node of tail.children) {
      const r = R.ids.get(+node.dataset.id);
      if (r) node.hidden = !matches(r);
    }
    R.recent = R.recent.filter(matches);
    R.fPlayed = 0; R.fShown = 0;          // count matches from the moment the filter changed
    drawStats(); drawClock();
    if (R.follow) { R.autoScroll = true; tail.scrollTop = tail.scrollHeight; }
  }
  $id("r-filter").addEventListener("input", refilter);
  $id("r-status").addEventListener("change", refilter);
  $id("r-paths").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-path]"); if (!b) return;
    const on = b.getAttribute("aria-pressed") === "true";
    $id("r-filter").value = on ? "" : b.dataset.path;
    refilter();
  });

  /* Click a line: pause and inspect it, the way a live tail is actually used. */
  tail.addEventListener("click", ev => {
    const node = ev.target.closest(".lx-line");
    if (!node) return;
    const r = R.ids.get(+node.dataset.id);
    if (!r) return;
    if (String(getSelection && getSelection()).length) return;
    pause();
    tail.querySelectorAll(".lx-line.sel").forEach(n => n.classList.remove("sel"));
    node.classList.add("sel");
    const box = $id("r-inspect");
    box.innerHTML = `<div class="card lx-inspect">
      <div class="lx-inspect-head"><h2>Line at ${H(tSec(r.ts))}</h2>
        <button class="btn" type="button" data-ract="close">Close</button></div>
      <pre class="lx-raw"><code>${H(ncsa(r))}</code></pre>
      <div class="actions lx-actions">
        <button class="btn primary" type="button" data-ract="resume">Resume replay</button>
        <button class="btn" type="button" data-ract="search">Search this host</button>
        ${hasDetail() ? `<button class="btn" type="button" data-ract="host">Host details</button>` : ""}
        <button class="btn" type="button" data-ract="copy">Copy line</button>
      </div></div>`;
    box._row = r;
  });
  $id("r-inspect").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-ract]"); if (!b) return;
    const r = $id("r-inspect")._row;
    const a = b.dataset.ract;
    if (a === "close" || a === "resume") {
      $id("r-inspect").innerHTML = "";
      tail.querySelectorAll(".lx-line.sel").forEach(n => n.classList.remove("sel"));
      if (a === "resume") play();
      $id("r-play").focus();
    } else if (a === "search" && r) {
      setMode("search");
      startSearch({ host: r.host, from: r.ts.slice(0, 10) + "T00:00", to: r.ts.slice(0, 10) + "T23:59" });
    } else if (a === "host" && r && hasDetail()) Detail.open("host", r.host);
    else if (a === "copy" && r) copyText(ncsa(r), "Line copied");
  });

  return {
    get speed() { return R.speed; },
    get playing() { return R.playing; },
    play, pause, seek, setSpeed, drawAll,
    toggle() { R.playing ? pause() : play(); },
    init(start, speed) {
      R.speed = [1, 10, 60, 600].includes(+speed) ? +speed : 60;
      const ms = toMs(start);
      const v = Number.isNaN(ms) ? toMs("1995-07-01T09:00") : clamp(ms, toMs(LOG_MIN), toMs(LOG_MAX));
      $id("r-start").value = isoOf(v);
      reset(v);
    },
    _state: R,
  };
})();

/* ============================================================ keyboard */
document.addEventListener("keydown", ev => {
  if (mode !== "replay" || ev.key !== " " || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const t = ev.target;
  if (t.closest && t.closest("input, textarea, select, button, a[href], [contenteditable=''], [contenteditable='true'], [role=button], [role=dialog]"))
    return;
  if (document.querySelector("#drawer-root [role=dialog], #drawer-root .open")) return;
  ev.preventDefault();
  Replay.toggle();
});

/* ============================================================ boot */
function applyHash({ initial = false } = {}) {
  const h = readHash();
  const m = h.get("mode") === "replay" ? "replay" : "search";
  if (initial || h.has("start") || h.has("speed")) {
    if (initial) Replay.init(h.get("start") || "1995-07-01T09:00", h.get("speed") || 60);
    else {
      if (h.has("speed") && +h.get("speed") !== Replay.speed) Replay.setSpeed(h.get("speed"));
      if (h.has("start") && h.get("start") !== $id("r-start").value) Replay.seek(h.get("start"));
    }
  }
  setMode(m, { write: false });

  const f = normFilters(Object.fromEntries(KEYS.map(k => [k, h.get(k) || ""])));
  if (initial) {
    // Land on something: the requested search, or the first lines of the log.
    if (m === "search" || hasAny(f)) startSearch(f, { write: false });
    else setForm(f);
  } else if (sigOf(f) !== S.sig && (hasAny(f) || S.started)) {
    startSearch(f, { write: false });
    if (hasAny(f) && m !== "search") setMode("search");
  }
}

addEventListener("hashchange", () => applyHash());
renderFacets();
applyHash({ initial: true });

window.LogExplorer = { search: startSearch, replay: Replay, state: S };
})();
