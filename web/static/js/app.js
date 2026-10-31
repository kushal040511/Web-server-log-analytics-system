/* Shared application layer: data loading, URL-backed state, the date-range
   control, the table component, and the interactive shell every page shares:
   detail drawer, command palette, keyboard shortcuts, toasts and a freshness
   check against the server. */
"use strict";

const $  = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

/* Browser storage is a convenience here, never a dependency. Opened straight
   from disk (the static export) or in a locked-down browser, touching
   localStorage can throw outright — and a theme preference is not worth taking
   the whole page down for. */
const store = {
  get(k)    { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* not important */ } },
  sget(k)   { try { return sessionStorage.getItem(k); } catch { return null; } },
  sset(k, v){ try { sessionStorage.setItem(k, v); } catch { /* not important */ } },
};

/* Every path and host in these logs is whatever a 1995 client sent. Nothing
   from the data goes into innerHTML without passing through here. */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const IS_MAC = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");
/* The static export has no server behind it, so there is no raw log explorer. */
const HAS_LOGS = false;

const isTextField = t => !!t && (t.isContentEditable || t.tagName === "TEXTAREA" || t.tagName === "SELECT"
  || (t.tagName === "INPUT" && !/^(checkbox|radio|button|submit|reset|range|color|file)$/i.test(t.type)));

/* ------------------------------------------------------------------ theme */
function toggleTheme() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const next = dark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  store.set("theme", next);
  const btn = $("#theme");
  if (btn) btn.textContent = next === "dark" ? "Light" : "Dark";
  document.dispatchEvent(new CustomEvent("render"));   // charts re-read CSS vars
}
(function theme() {
  const saved = store.get("theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  addEventListener("DOMContentLoaded", () => {
    const btn = $("#theme");
    if (!btn) return;
    btn.textContent = document.documentElement.getAttribute("data-theme") === "dark" ? "Light" : "Dark";
    btn.addEventListener("click", toggleTheme);
  });
})();

/* On a narrow screen the tab strip scrolls, and the current page's tab can start
   off-screen — which reads as "this page isn't in the nav". Bring it into view. */
addEventListener("DOMContentLoaded", () => {
  const cur = document.querySelector('.tabs a[aria-current="page"]');
  const bar = document.querySelector("nav.tabs");
  if (!cur || !bar || bar.scrollWidth <= bar.clientWidth) return;
  bar.scrollLeft = cur.offsetLeft - (bar.clientWidth - cur.offsetWidth) / 2;
});

/* --------------------------------------------------------------- URL hash */
/* Shareable state lives in the fragment: range=1995-07-01..1995-07-15,
   detail=day:1995-07-13, and whatever the log explorer adds. Read with
   URLSearchParams so any writer's encoding parses; written with the common
   punctuation left readable. */
const encHash = s => encodeURIComponent(s)
  .replace(/%3A/gi, ":").replace(/%2F/gi, "/").replace(/%40/g, "@").replace(/%2C/gi, ",");
const serializeParams = p => [...p].map(([k, v]) => encHash(k) + "=" + encHash(v)).join("&");

const Hash = {
  params() { return new URLSearchParams(location.hash.replace(/^#/, "")); },
  get(k) { return this.params().get(k); },
  set(k, v) { this.update({ [k]: v }); },
  update(obj, { push = false } = {}) {
    const p = this.params();
    for (const k in obj) {
      if (obj[k] == null || obj[k] === "") p.delete(k); else p.set(k, String(obj[k]));
    }
    const s = serializeParams(p);
    const base = location.pathname + location.search;
    const url = base + (s ? "#" + s : "");
    if (!push && url === base + location.hash) return;
    try {
      if (push) history.pushState({ drawer: true }, "", url);
      else history.replaceState(history.state, "", url);
    } catch {
      // some file:// contexts refuse history rewrites; the fragment still works
      location.hash = s;
    }
  },
};

/* ------------------------------------------------------------------- data */
const isoAdd = (s, n) => {
  const d = new Date(s + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

const Store = {
  data: null,
  from: null,   // inclusive ISO date
  to: null,
  hash: Hash,
  hashRange: false,   // report pages mirror the range into the URL
  _p: null,

  /* Loads once; every caller shares the same promise. */
  ensure() {
    if (this.data) return Promise.resolve(this.data);
    if (!this._p) this._p = this._fetch().catch(e => { this._p = null; throw e; });
    return this._p;
  },
  load() { return this.ensure(); },
  async _fetch() {
    let data;
    if (typeof window.__DASHBOARD__ !== "undefined") data = window.__DASHBOARD__;
    else {
      const r = await fetch("/api/data", { cache: "no-store" });
      if (!r.ok) {
        let msg = "HTTP " + r.status;
        try { const j = await r.json(); if (j.error) msg = j.error; } catch { /* not JSON */ }
        throw new Error(msg);
      }
      data = await r.json();
    }
    this.data = data;
    this._initRange();
    document.dispatchEvent(new CustomEvent("store:ready"));
    return data;
  },
  get bounds() {
    const d = (this.data && this.data.daily) || [];
    return d.length ? { min: d[0].date, max: d.at(-1).date } : { min: null, max: null };
  },
  /* "a..b" -> [a, b] clamped to the data, or null if it cannot apply. */
  parseRange(s) {
    const m = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/.exec(s || "");
    const { min, max } = this.bounds;
    if (!m || !min) return null;
    let [f, t] = [m[1], m[2]];
    if (f > t) [f, t] = [t, f];
    if (f > max || t < min) return null;
    return [f < min ? min : f, t > max ? max : t];
  },
  _initRange() {
    const { min, max } = this.bounds;
    this.from = min; this.to = max;
    const fromHash = this.parseRange(Hash.get("range"));
    if (fromHash) { [this.from, this.to] = fromHash; return; }
    // remember the range across pages so a drill-down survives navigation
    const saved = store.sget("range");
    if (saved) {
      const r = this.parseRange(saved.replace("|", ".."));
      if (r) [this.from, this.to] = r;
    }
  },
  writeRangeHash() {
    if (!this.data) return;
    Hash.update({ range: this.isFullRange ? null : `${this.from}..${this.to}` });
  },
  setRange(from, to, opts = {}) {
    this.from = from; this.to = to;
    store.sset("range", from + "|" + to);
    if (this.hashRange) this.writeRangeHash();
    if (!opts.silent) document.dispatchEvent(new CustomEvent("render"));
  },
  inRange(dateStr) { return dateStr >= this.from && dateStr <= this.to; },
  get isFullRange() {
    const d = this.data.daily || [];
    return !d.length || (this.from === d[0].date && this.to === d.at(-1).date);
  },

  /* --- range-aware slices ------------------------------------------------ */
  daily()  { return (this.data.daily || []).filter(r => this.inRange(r.date)); },
  errors() { return (this.data.errors_daily || []).filter(r => this.inRange(r.date)); },

  /* Recomputed from the hour x date cross-tab, so picking a range genuinely
     changes the hour-of-day profile instead of showing period-wide totals. */
  byHour() {
    const src = this.data.hour_by_date;
    if (!src || !src.length) return this.data.by_hour || [];
    const acc = new Map();
    for (const r of src) if (this.inRange(r.date))
      acc.set(r.hour, (acc.get(r.hour) || 0) + r.hits);
    const out = [];
    for (let h = 0; h < 24; h++) out.push({ hour: h, hits: acc.get(h) || 0 });
    return out;
  },
  byStatus() {
    const src = this.data.status_by_date;
    if (!src || !src.length) return this.data.status || [];
    const acc = new Map();
    let total = 0;
    for (const r of src) if (this.inRange(r.date)) {
      acc.set(r.status, (acc.get(r.status) || 0) + r.hits); total += r.hits;
    }
    return [...acc.entries()]
      .map(([status, hits]) => ({ status, hits, pct: total ? 100 * hits / total : 0 }))
      .sort((a, b) => b.hits - a.hits);
  },
  /* Totals for the current range, so the KPI row responds to the filter too. */
  totals() {
    const d = this.daily();
    return {
      records: d.reduce((a, r) => a + r.hits, 0),
      days: d.length,
      peakDay: d.reduce((a, r) => (!a || r.hits > a.hits) ? r : a, null),
    };
  },
};

/* The URL is the source of truth for shareable state: someone editing the
   fragment, or the back button, re-applies it. */
function syncFromHash() {
  const detail = Hash.get("detail");
  if (Drawer.expectingPop) {
    // our own history.back() after closing a drawer: keep the live range
    Drawer.expectingPop = false;
    if (Store.hashRange) Store.writeRangeHash();
    return;
  }
  if (!detail && Drawer.isOpen && Drawer.key) {
    Drawer.close({ fromHistory: true });
    if (Store.hashRange) Store.writeRangeHash();
    return;
  }
  if (Store.data) {
    const r = Store.parseRange(Hash.get("range"));
    if (r && (r[0] !== Store.from || r[1] !== Store.to)) Store.setRange(r[0], r[1]);
  }
  if (detail && detail !== Drawer.key) openFromKey(detail, { fromHistory: true });
}
addEventListener("hashchange", syncFromHash);
addEventListener("popstate", syncFromHash);

function openFromKey(key, o = {}) {
  if (key === "shortcuts") return Shortcuts.open(o);
  const i = key.indexOf(":");
  if (i < 1) return;
  Detail.open(key.slice(0, i), key.slice(i + 1), o);
}

/* ------------------------------------------------------------------ toast */
function toast(message, opts = {}) {
  let root = document.getElementById("toast-root");
  if (!root) {
    root = document.createElement("div"); root.id = "toast-root";
    root.setAttribute("aria-live", "polite"); document.body.appendChild(root);
  }
  // the same message twice just restarts its timer
  for (const t of root.children) if (t._msg === message && t._restart) { t._restart(); return t._api; }
  while (root.children.length >= 3) root.firstElementChild._close();

  const node = document.createElement("div");
  node.className = "toast" + (opts.tone === "error" ? " error" : "");
  node.setAttribute("role", opts.tone === "error" ? "alert" : "status");
  const msg = document.createElement("span");
  msg.className = "toast-msg"; msg.textContent = message;
  node.appendChild(msg);
  if (opts.action) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "toast-action"; b.textContent = opts.action.label;
    b.addEventListener("click", () => { close(); opts.action.onClick && opts.action.onClick(); });
    node.appendChild(b);
  }
  const x = document.createElement("button");
  x.type = "button"; x.className = "toast-close"; x.setAttribute("aria-label", "Dismiss notification");
  x.textContent = "×";
  x.addEventListener("click", () => close());
  node.appendChild(x);

  const ms = opts.timeout ?? (opts.action ? 8000 : 4000);
  let timer = null;
  const arm = () => { clearTimeout(timer); if (ms > 0) timer = setTimeout(close, ms); };
  function close() {
    clearTimeout(timer);
    if (!node.isConnected) return;
    node.classList.add("leaving");
    setTimeout(() => node.remove(), matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 160);
  }
  node.addEventListener("pointerenter", () => clearTimeout(timer));
  node.addEventListener("pointerleave", arm);
  node.addEventListener("focusin", () => clearTimeout(timer));
  node.addEventListener("focusout", arm);
  node._msg = message; node._restart = arm; node._close = close;
  node._api = { close };
  root.appendChild(node);
  arm();
  return node._api;
}

async function copyText(text, okMessage) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text); ok = true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;opacity:0;top:0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
  }
  if (ok) toast(okMessage || "Copied");
  else toast("Could not copy: the browser blocked clipboard access. Copy it from the address bar instead.",
             { tone: "error" });
}

/* ------------------------------------------------------------- range bar */
function rangePresets(all) {
  const min = all[0].date, max = all.at(-1).date;
  const out = [{ label: "All dates", from: min, to: max }];
  const months = new Map();
  for (const r of all) {
    const k = r.date.slice(0, 7);
    const m = months.get(k) || { from: r.date, to: r.date };
    m.to = r.date; months.set(k, m);
  }
  const years = new Set([...months.keys()].map(k => k.slice(0, 4)));
  if (months.size > 1) for (const [k, m] of months) {
    const d = new Date(k + "-01T00:00:00");
    const label = years.size > 1
      ? d.toLocaleDateString("en-GB", { month: "short", year: "numeric" })
      : d.toLocaleDateString("en-GB", { month: "long" });
    out.push({ label, from: m.from, to: m.to });
  }
  const last7 = isoAdd(max, -6);
  out.push({ label: "Last 7 days", from: last7 < min ? min : last7, to: max });
  return out;
}

function mountRangeBar(host, opts = {}) {
  const all = Store.data.daily || [];
  if (!all.length) return;
  const min = all[0].date, max = all.at(-1).date;
  const presets = rangePresets(all);
  host.innerHTML = `
    <div class="rb-dates">
      <label for="rb-from">From</label>
      <input type="date" id="rb-from" min="${min}" max="${max}" value="${Store.from}">
      <label for="rb-to">to</label>
      <input type="date" id="rb-to" min="${min}" max="${max}" value="${Store.to}">
    </div>
    <div class="seg" role="group" aria-label="Date presets">${presets.map((p, i) =>
      `<button type="button" data-i="${i}" aria-pressed="false">${esc(p.label)}</button>`).join("")}</div>
    <span class="spacer"></span>
    <span id="rb-summary"></span>
    <button type="button" class="btn" id="rb-copy">Copy link</button>`;
  const from = host.querySelector("#rb-from"), to = host.querySelector("#rb-to");

  const sync = () => {
    if (document.activeElement !== from) from.value = Store.from;
    if (document.activeElement !== to) to.value = Store.to;
    host.querySelectorAll(".seg button").forEach(b => {
      const p = presets[+b.dataset.i];
      b.setAttribute("aria-pressed", String(p.from === Store.from && p.to === Store.to));
    });
    const t = Store.totals();
    host.querySelector("#rb-summary").innerHTML = Store.isFullRange
      ? `All <b>${t.days}</b> days, <b>${fmt.n(t.records)}</b> requests`
      : `<b>${t.days}</b> of ${all.length} days, <b>${fmt.n(t.records)}</b> requests`;
  };

  /* Chrome fires "change" as each segment of a typed date completes, so a
     half-typed year would briefly be a real (and absurd) range. Apply only
     in-bounds dates immediately; anything else is settled on blur. */
  const apply = (final) => {
    let f = from.value, t = to.value;
    if (!f || !t) { if (final) { from.value = Store.from; to.value = Store.to; } return; }
    const inBounds = f >= min && f <= max && t >= min && t <= max;
    if (!inBounds && !final) return;
    if (f < min) f = min; if (f > max) f = max;
    if (t < min) t = min; if (t > max) t = max;
    if (f > t) [f, t] = [t, f];
    if (final) { from.value = f; to.value = t; }
    if (f !== Store.from || t !== Store.to) Store.setRange(f, t);
  };
  for (const inp of [from, to]) {
    inp.addEventListener("change", () => apply(false));
    inp.addEventListener("blur", () => apply(true));
  }
  host.querySelector(".seg").addEventListener("click", e => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    const p = presets[+b.dataset.i];
    Store.setRange(p.from, p.to);
  });
  host.querySelector("#rb-copy").addEventListener("click", () => copyText(location.href, "Link copied"));

  if (host._rbSync) document.removeEventListener("render", host._rbSync);
  host._rbSync = sync;
  document.addEventListener("render", sync);
  sync();
}

/* Where the per-table CSV/JSON buttons point. Served by Flask this is the
   /download route; the static export sets it to the tables/ folder sitting next
   to the page, because these links are built here in JS and so are invisible to
   the exporter's HTML rewriting. */
const DL_BASE = (typeof window !== "undefined" && window.__DL_BASE__) || "/download/";

/* --------------------------------------------------------- data tables */
/* Sortable, searchable, paginated. Given rows + column specs it handles the
   rest, so every table on the site behaves identically. Cell values are
   escaped unless a column supplies its own render(). */
function dataTable(host, rows, cols, opts = {}) {
  const pageSize = opts.pageSize || 15;
  // sortKey: null explicitly means "keep the order you were given" — SQL results
  // arrive already ordered by the user's own ORDER BY.
  const noSort = opts.sortKey === null;
  let sortKey = noSort ? null : (opts.sortKey || cols.find(c => c.num)?.key || cols[0].key);
  let sortDir = opts.sortDir || "desc";
  let query = "", page = 0, slice = [];

  const searchable = cols.filter(c => !c.num).map(c => c.key);
  const maxOf = k => Math.max(...rows.map(r => +r[k] || 0)) || 1;
  const barMax = opts.barKey ? maxOf(opts.barKey) : 0;

  host.innerHTML = `
    <div class="tabletools">
      ${opts.search === false ? "" :
        `<input type="text" placeholder="${esc(opts.placeholder || "Search…")}" aria-label="Search table">`}
      <span class="count"></span>
      ${opts.downloads ? `<span class="dl">
         <a href="${DL_BASE}${esc(opts.downloads)}.csv">CSV</a>
         <a href="${DL_BASE}${esc(opts.downloads)}.json">JSON</a></span>` : ""}
    </div>
    <div class="tablewrap"><table><thead><tr></tr></thead><tbody></tbody></table></div>
    <div class="pager"></div>`;

  const thead = host.querySelector("thead tr");
  const tbody = host.querySelector("tbody");
  const count = host.querySelector(".count");
  const pager = host.querySelector(".pager");
  const search = host.querySelector('input[type=text]');

  cols.forEach(c => {
    const th = document.createElement("th");
    th.className = (c.num ? "num " : "") + (c.sortable === false ? "" : "sortable");
    th.innerHTML = esc(c.label) + (c.sortable === false ? "" : ' <span class="arrow" aria-hidden="true">▾</span>');
    if (c.sortable !== false) {
      const sortBy = () => {
        if (sortKey === c.key) sortDir = sortDir === "asc" ? "desc" : "asc";
        else { sortKey = c.key; sortDir = c.num ? "desc" : "asc"; }
        page = 0; draw();
      };
      // headers are the only sort control, so they must work from the keyboard too
      th.tabIndex = 0;
      th.addEventListener("click", sortBy);
      th.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(); }
      });
    }
    thead.appendChild(th);
  });

  if (search) search.addEventListener("input", () => { query = search.value.toLowerCase(); page = 0; draw(); });

  if (opts.onRowClick) {
    const rowOf = e => {
      const tr = e.target.closest("tr.rowlink");
      return tr && tbody.contains(tr) ? slice[+tr.dataset.i] : null;
    };
    tbody.addEventListener("click", e => {
      if (e.target.closest("a,button,input,select,textarea")) return;
      if (String(getSelection && getSelection()).length) return;   // selecting text, not opening
      const r = rowOf(e); if (r) opts.onRowClick(r, e);
    });
    tbody.addEventListener("keydown", e => {
      if (e.key !== "Enter" || !e.target.matches("tr.rowlink")) return;
      const r = rowOf(e); if (r) { e.preventDefault(); opts.onRowClick(r, e); }
    });
  }

  function filtered() {
    let out = rows;
    if (query) out = out.filter(r => searchable.some(k => String(r[k] ?? "").toLowerCase().includes(query)));
    if (!sortKey) return out;
    const c = cols.find(x => x.key === sortKey) || {};
    return [...out].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = c.num ? (+av || 0) - (+bv || 0) : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
  }

  function draw() {
    const rs = filtered();
    const pages = Math.max(1, Math.ceil(rs.length / pageSize));
    page = Math.min(page, pages - 1);
    slice = rs.slice(page * pageSize, (page + 1) * pageSize);

    thead.querySelectorAll("th").forEach((th, i) => {
      const c = cols[i];
      if (c.key === sortKey) { th.setAttribute("aria-sort", sortDir === "asc" ? "ascending" : "descending");
        const a = th.querySelector(".arrow"); if (a) a.textContent = sortDir === "asc" ? "▴" : "▾"; }
      else th.removeAttribute("aria-sort");
    });

    const link = !!opts.onRowClick;
    tbody.innerHTML = slice.map((r, i) =>
      (link ? `<tr class="rowlink" tabindex="0" data-i="${i}">` : "<tr>") + cols.map(c => {
      const raw = r[c.key];
      const val = c.render ? c.render(raw, r) : (c.num ? fmt.n(raw) : esc(raw ?? ""));
      if (c.key === opts.barKey) {
        const pct = ((+raw || 0) / barMax * 100).toFixed(1);
        return `<td class="num bar-cell"><span class="fill" style="width:${pct}%"></span><span>${val}</span></td>`;
      }
      const cls = (c.num ? "num" : "") + (c.trunc ? " trunc mono" : "");
      const title = c.trunc ? ` title="${esc(raw ?? "")}"` : "";
      return `<td class="${cls}"${title}>${val}</td>`;
    }).join("") + "</tr>").join("");

    count.textContent = query
      ? `${rs.length} of ${rows.length} rows`
      : `${rows.length} row${rows.length === 1 ? "" : "s"}`;

    pager.innerHTML = pages > 1
      ? `<button class="btn" ${page === 0 ? "disabled" : ""} data-d="-1">Prev</button>
         <span>Page ${page + 1} of ${pages}</span>
         <button class="btn" ${page >= pages - 1 ? "disabled" : ""} data-d="1">Next</button>` : "";
    pager.querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => { page += +b.dataset.d; draw(); }));
  }
  draw();
  return { redraw: draw };
}

/* -------------------------------------------------------------- helpers */
function kpiRow(host, items) {
  host.innerHTML = items.map(([v, l, accent]) =>
    `<div class="kpi${accent ? " accent" : ""}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
}

function chips(host, pairs) {
  host.innerHTML = pairs.map(([l, v]) => `<span>${l} <b>${v}</b></span>`).join("");
}

/* Report tables are capped top-N lists. Say so on the page, so "300 rows" is
   never mistaken for the whole population. */
function capNote(shown, total, noun, fallback) {
  return total > shown
    ? `The ${fmt.n(shown)} busiest of ${fmt.n(total)} ${noun}. ${fallback}`
    : fallback;
}

/* ---------------------------------------------------------- modal layer */
/* Overlays make everything behind them inert, so Tab, clicks and screen
   readers stay inside. Each overlay remembers exactly what it disabled. */
const OVERLAY_IDS = ["drawer-root", "palette-root", "toast-root", "tip"];
function inertBehind(keepIds) {
  const changed = [];
  for (const n of document.body.children) {
    if (keepIds.includes(n.id) || n.tagName === "SCRIPT" || n.inert) continue;
    n.inert = true; changed.push(n);
  }
  return () => changed.forEach(n => { n.inert = false; });
}
function rootEl(id) {
  let r = document.getElementById(id);
  if (!r) { r = document.createElement("div"); r.id = id; document.body.appendChild(r); }
  return r;
}
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),'
  + 'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function trapTab(e, container) {
  if (e.key !== "Tab") return;
  const f = [...container.querySelectorAll(FOCUSABLE)].filter(n => n.getClientRects().length);
  if (!f.length) { e.preventDefault(); return; }
  const first = f[0], last = f.at(-1), idx = f.indexOf(document.activeElement);
  if (idx === -1) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
  else if (e.shiftKey && idx === 0) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && idx === f.length - 1) { e.preventDefault(); first.focus(); }
}
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ----------------------------------------------------------------- drawer */
const Drawer = {
  isOpen: false, key: null, opener: null, pushed: false, expectingPop: false,
  onKey: null, _restoreInert: null,

  open(o = {}) {
    const root = rootEl("drawer-root");
    const wasOpen = this.isOpen;
    let panel = root.querySelector(".drawer");
    const focusKey = wasOpen && document.activeElement && document.activeElement.dataset
      ? document.activeElement.dataset.fk : null;
    const prevScroll = wasOpen ? root.querySelector(".drawer-body").scrollTop : 0;

    if (!wasOpen) {
      const ae = document.activeElement;
      // opening from the palette: its opener is the element to return to
      this.opener = (Palette.isOpen || (ae && ae.closest && ae.closest("#palette-root")))
        ? Palette.opener : ae;
      this.pushed = false;
      root.innerHTML = `
        <div class="drawer-backdrop"></div>
        <div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" tabindex="-1">
          <div class="drawer-head">
            <div class="drawer-titles">
              <h2 id="drawer-title" tabindex="-1"></h2>
              <div class="drawer-sub"></div>
            </div>
            <button type="button" class="btn drawer-close">Close</button>
          </div>
          <div class="drawer-body"></div>
        </div>`;
      panel = root.querySelector(".drawer");
      root.querySelector(".drawer-backdrop").addEventListener("click", () => this.close());
      root.querySelector(".drawer-close").addEventListener("click", () => this.close());
      root.querySelector(".drawer-body").addEventListener("click", e => { if (this.onClick) this.onClick(e); });
      panel.addEventListener("keydown", e => this._key(e));
      this._restoreInert = inertBehind(OVERLAY_IDS);
      document.documentElement.classList.add("overlay-lock");
      root.classList.remove("open");
      void panel.offsetWidth;   // commit the closed state so the slide animates
      root.classList.add("open");
    }

    panel.classList.toggle("wide", !!o.wide);
    const title = root.querySelector("#drawer-title");
    title.textContent = o.title || "";
    title.classList.toggle("mono", !!o.titleMono);
    const sub = root.querySelector(".drawer-sub");
    sub.textContent = o.subtitle || "";
    sub.hidden = !o.subtitle;
    const body = root.querySelector(".drawer-body");
    body.innerHTML = o.html || "";
    this.onKey = o.onKey || null;
    this.onClick = o.onClick || null;
    if (o.mount) {
      try { o.mount(body); }
      catch (err) { console.error(err); }
    }
    body.scrollTop = o.keepScroll ? prevScroll : 0;

    const refocus = focusKey && body.querySelector(`[data-fk="${focusKey}"]:not([disabled])`);
    if (refocus) refocus.focus();
    else if (!wasOpen || !panel.contains(document.activeElement)) title.focus({ preventScroll: true });

    // shareable state: detail=<key>; the back button closes what a click opened
    const key = o.key || null;
    if (key) {
      if (Hash.get("detail") !== key) {
        if (!wasOpen && !o.fromHistory) { Hash.update({ detail: key }, { push: true }); this.pushed = true; }
        else Hash.update({ detail: key });
      }
    } else if (wasOpen && this.key) {
      Hash.update({ detail: null });
    }
    this.key = key;
    this.isOpen = true;
  },

  close({ fromHistory = false } = {}) {
    if (!this.isOpen) return;
    const root = rootEl("drawer-root");
    root.classList.remove("open");
    this.isOpen = false;
    setTimeout(() => { if (!this.isOpen) root.innerHTML = ""; }, reducedMotion() ? 0 : 220);
    if (this._restoreInert) { this._restoreInert(); this._restoreInert = null; }
    if (!Palette.isOpen) document.documentElement.classList.remove("overlay-lock");
    hideTipSafe();
    const hadKey = this.key;
    this.key = null; this.onKey = null; this.onClick = null;
    if (hadKey && !fromHistory) {
      if (this.pushed && history.state && history.state.drawer) { this.expectingPop = true; history.back(); }
      else Hash.update({ detail: null });
    }
    this.pushed = false;
    const op = this.opener;
    this.opener = null;
    if (op && op.isConnected && typeof op.focus === "function" && op !== document.body) op.focus({ preventScroll: true });
  },

  _key(e) {
    if (e.key === "Tab") { trapTab(e, e.currentTarget); return; }
    if (this.onKey && !e.defaultPrevented && !isTextField(e.target) && !e.altKey && !e.metaKey && !e.ctrlKey)
      this.onKey(e);
  },
};
function hideTipSafe() { if (typeof hideTip === "function") hideTip(); }

/* ----------------------------------------------------------------- detail */
const STATUS_NAMES = {
  200: "OK", 201: "Created", 204: "No content", 206: "Partial content", 301: "Moved permanently",
  302: "Found", 304: "Not modified", 400: "Bad request", 401: "Unauthorized", 403: "Forbidden",
  404: "Not found", 405: "Method not allowed", 500: "Internal server error", 501: "Not implemented",
  502: "Bad gateway", 503: "Service unavailable",
};
const statusClass = c => c >= 500 ? "Server error" : c >= 400 ? "Client error"
  : c >= 300 ? "Redirect or cached" : c >= 200 ? "Success" : "Informational";
const statusColor = c => c >= 400 ? cssv("--critical") : c === 302 ? cssv("--series-1-soft")
  : c >= 300 ? cssv("--series-1") : c >= 200 ? cssv("--series-3") : cssv("--text-muted");
const hh = h => String(h).padStart(2, "0");
const extOf = p => {
  const m = /\.([A-Za-z0-9]{1,10})$/.exec(String(p).split(/[?#]/)[0]);
  return m ? m[1].toLowerCase() : null;
};
const logsHref = params => "/logs#" + serializeParams(new URLSearchParams(params));

/* Derived lookups, computed once per loaded dataset. */
const _derived = new WeakMap();
function derived() {
  const D = Store.data;
  let x = _derived.get(D);
  if (x) return x;
  const s5 = new Map(), statusDay = new Map();
  for (const r of D.status_by_date || []) {
    if (r.status >= 500) s5.set(r.date, (s5.get(r.date) || 0) + r.hits);
    statusDay.set(r.date + "|" + r.status, r.hits);
  }
  x = {
    s5, statusDay,
    daily: new Map((D.daily || []).map(r => [r.date, r])),
    errors: new Map((D.errors_daily || []).map(r => [r.date, r])),
    flagged: new Set((D.bots && D.bots.flagged_examples) || []),
  };
  _derived.set(D, x);
  return x;
}

const statGrid = items => `<div class="kpis dstats">${items.map(([v, l, delta]) =>
  `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div>${delta || ""}</div>`).join("")}</div>`;

function deltaHtml(val, mean, label, badWhenUp = false) {
  if (!isFinite(val) || mean == null) return "";
  // A percentage of a handful of events is noise; say the counts instead.
  if (mean < 10 && Number.isInteger(val)) {
    const diff = val - mean, m = mean.toFixed(1);
    if (Math.abs(diff) < 0.5) return `<div class="delta flat">Level with the ${esc(label)} of ${m}</div>`;
    const bad = badWhenUp && diff >= 2 && val > mean * 1.5;
    return `<div class="delta ${diff > 0 ? "up" : "down"}${bad ? " bad" : ""}">${diff > 0 ? "+" : "−"}`
      + `${Math.abs(diff).toFixed(1)} vs the ${esc(label)} of ${m}</div>`;
  }
  if (!mean) return "";
  const pct = (val - mean) / mean * 100;
  if (Math.abs(pct) < 0.5) return `<div class="delta flat">Level with the ${esc(label)}</div>`;
  const up = pct > 0;
  const size = Math.abs(pct) >= 10 ? Math.abs(pct).toFixed(0) : Math.abs(pct).toFixed(1);
  return `<div class="delta ${up ? "up" : "down"}${badWhenUp && up ? " bad" : ""}">`
    + `${up ? "+" : "−"}${size}% vs the ${esc(label)}</div>`;
}

/* A drill-down list: each row opens another detail. */
const xlist = rows => rows.length ? `<ul class="xlist">${rows.map(r =>
  `<li><button type="button" class="xrow" data-detail="${esc(r.detail)}">
     <span class="xname${r.mono ? " mono" : ""}">${esc(r.name)}</span>
     <span class="xval">${esc(r.value)}</span></button></li>`).join("")}</ul>` : "";

function actionRow(items) {
  return `<div class="actions">${items.filter(Boolean).map(a => a.href
    ? `<a class="btn${a.primary ? " primary" : ""}" href="${esc(a.href)}">${esc(a.label)}</a>`
    : `<button type="button" class="btn${a.primary ? " primary" : ""}" ${a.attrs || ""}>${esc(a.label)}</button>`
  ).join("")}</div>`;
}

/* The topbar link for a route, in both the served site and the static export
   (where /traffic became traffic.html). */
function navLink(route) {
  const file = route === "/" ? "index.html" : route.slice(1) + ".html";
  return $$("nav.tabs a").find(n => [route, file].includes(n.getAttribute("href"))) || null;
}
function pageHref(route) {
  const a = navLink(route);
  return a ? a.getAttribute("href") : (HAS_LOGS ? route : null);
}

const Detail = {
  current: null,

  async open(type, value, o = {}) {
    type = String(type); value = String(value ?? "");
    const build = this.builders[type];
    if (!build) { this._bad(type, value); return; }
    try { await Store.ensure(); }
    catch (e) {
      toast(`Could not load the report data (${e.message}). Run make dashboard, then reload the page.`,
            { tone: "error" });
      return;
    }
    let spec;
    try { spec = build.call(this, value); } catch (err) { console.error(err); spec = null; }
    if (!spec) { this._bad(type, value); return; }
    Drawer.open({
      ...spec,
      key: `${type}:${value}`,
      fromHistory: o.fromHistory,
      keepScroll: o.refresh,
      onClick: e => this._click(e),
    });
    this.current = { type, value };
    if (!o.refresh && !o.fromHistory) Recent.add(type, value);
  },

  refresh() {
    if (Drawer.isOpen && this.current && Drawer.key === `${this.current.type}:${this.current.value}`)
      this.open(this.current.type, this.current.value, { refresh: true });
  },

  _bad(type, value) {
    if (Hash.get("detail") === `${type}:${value}`) Hash.update({ detail: null });
    toast("That link points to a detail this site cannot show. Check the address, or search for it instead.",
          { tone: "error", action: { label: "Open search", onClick: () => Palette.open() } });
  },

  _click(e) {
    const t = e.target.closest("[data-detail],[data-go],[data-copy],[data-day-only],[data-nav]");
    if (!t) return;
    if (t.dataset.detail) { const k = t.dataset.detail, i = k.indexOf(":"); this.open(k.slice(0, i), k.slice(i + 1)); }
    else if (t.dataset.go) this.open(t.dataset.go.split(":")[0], t.dataset.go.slice(t.dataset.go.indexOf(":") + 1));
    else if (t.dataset.copy != null) copyText(t.dataset.copy, t.dataset.copyMsg || "Copied");
    else if (t.dataset.dayOnly) {
      const prev = [Store.from, Store.to], day = t.dataset.dayOnly;
      Store.setRange(day, day);
      toast(`Showing ${fmt.dateLong(day)} only`, {
        action: { label: "Undo", onClick: () => Store.setRange(prev[0], prev[1]) } });
    }
  },

  /* Baseline for "vs mean": the selected range, or every date when the
     range is a single day (comparing a day to itself says nothing). */
  _baseline() {
    const r = Store.daily();
    const rows = r.length >= 2 ? r : (Store.data.daily || []);
    return { rows, label: `${rows.length}-day mean` };
  },

  builders: {
    day(value) {
      if (!ISO_DAY.test(value) || isNaN(Date.parse(value))) return null;
      const D = Store.data, X = derived(), { min, max } = Store.bounds;
      const row = X.daily.get(value);
      const prev = isoAdd(value, -1), next = isoAdd(value, 1);
      const nav = `<div class="daynav">
          <button type="button" class="btn" data-fk="prev" data-go="day:${prev}" ${prev < min ? "disabled" : ""}>Previous day</button>
          <button type="button" class="btn" data-fk="next" data-go="day:${next}" ${next > max ? "disabled" : ""}>Next day</button>
          <span class="hint">or press <kbd>←</kbd> <kbd>→</kbd></span></div>`;
      const onKey = e => {
        if (e.key === "ArrowLeft" && prev >= min) { e.preventDefault(); Detail.open("day", prev); }
        if (e.key === "ArrowRight" && next <= max) { e.preventDefault(); Detail.open("day", next); }
      };
      const inRange = Store.inRange(value);
      const subtitle = !row ? "No records for this day"
        : inRange ? "Inside the selected date range"
        : `Outside the selected range (${fmt.date(Store.from)} to ${fmt.date(Store.to)})`;

      if (!row) {
        let why;
        if (value < min || value > max) {
          why = `The logs cover ${esc(fmt.dateLong(min))} to ${esc(fmt.dateLong(max))}, so there is nothing recorded for this date.`;
        } else {
          const all = D.daily;
          const before = all.filter(r => r.date < value).at(-1), after = all.find(r => r.date > value);
          const gap = Math.round((Date.parse(after.date) - Date.parse(before.date)) / 864e5) - 1;
          why = `The server logged nothing on this day. It sits in ${gap === 1 ? "a one-day gap" : `a ${gap}-day gap`}
            in the trace: records stop on ${esc(fmt.dateLong(before.date))} and resume on
            ${esc(fmt.dateLong(after.date))}. Missing days are left out of every report rather than counted as zero.`;
        }
        return {
          title: fmt.dateLong(value), subtitle, onKey,
          html: `${nav}<div class="empty-inline">${why}</div>`,
        };
      }

      const base = Detail._baseline();
      const mean = k => base.rows.reduce((a, r) => a + (+r[k] || 0), 0) / base.rows.length;
      const e = X.errors.get(value);
      const baseErr = base.rows.map(r => X.errors.get(r.date)).filter(Boolean);
      const mean404 = baseErr.length ? baseErr.reduce((a, r) => a + +r.pct_404, 0) / baseErr.length : 0;
      const s5 = X.s5.get(value) || 0;
      const mean5 = base.rows.reduce((a, r) => a + (X.s5.get(r.date) || 0), 0) / base.rows.length;

      const hours = [];
      for (let h = 0; h < 24; h++) hours.push({ hour: h, hits: 0 });
      for (const r of D.hour_by_date || []) if (r.date === value) hours[r.hour].hits = r.hits;
      const peak = hours.reduce((a, r) => r.hits > a.hits ? r : a, hours[0]);
      const logged = hours.filter(r => r.hits > 0).length;
      const st = (D.status_by_date || []).filter(r => r.date === value).sort((a, b) => b.hits - a.hits);
      const stTotal = st.reduce((a, r) => a + r.hits, 0) || 1;
      const nextMissing = next <= max && !X.daily.has(next);

      const html = `${nav}
        ${statGrid([
          [fmt.n(row.hits), "requests", deltaHtml(row.hits, mean("hits"), base.label)],
          [fmt.n(row.visitors), "distinct visitors", deltaHtml(row.visitors, mean("visitors"), base.label)],
          [e ? fmt.pct(+e.pct_404) : "—", "of requests returned 404", e ? deltaHtml(+e.pct_404, mean404, base.label, true) : ""],
          [fmt.n(s5), "server errors (5xx)", deltaHtml(s5, mean5, base.label, true)],
        ])}
        ${actionRow([
          !(Store.from === value && Store.to === value) && { label: "Show only this day", attrs: `data-day-only="${value}"` },
          HAS_LOGS && { label: "Search raw logs", href: logsHref({ from: value + "T00:00", to: value + "T23:59" }) },
        ])}
        ${logged < 24 || nextMissing ? `<div class="empty-inline">${logged < 24
          ? `Only ${logged} of 24 hours have records, so this is a partial day.` : ""}
          ${nextMissing ? ` The trace has no records for the following day.` : ""}</div>` : ""}
        <section class="dsec">
          <h3>Requests by hour</h3>
          <p class="dnote">Busiest at ${hh(peak.hour)}:00 with ${fmt.n(peak.hits)} requests. Select an hour to compare it across the selected range.</p>
          <div id="dd-hours"></div>
        </section>
        <section class="dsec">
          <h3>Status codes</h3>
          <div id="dd-status"></div>
          ${xlist(st.map(r => ({ detail: `status:${r.status}`,
            name: `${r.status} ${STATUS_NAMES[r.status] || ""}`.trim(),
            value: `${fmt.n(r.hits)}  (${(100 * r.hits / stTotal).toFixed(2)}%)` })))}
        </section>`;

      return {
        title: fmt.dateLong(value), subtitle, html, onKey,
        mount: body => {
          barChart(body.querySelector("#dd-hours"), hours, {
            height: 170, y: r => r.hits,
            xLabel: r => r.hour % 3 === 0 ? hh(r.hour) : null,
            tip: r => `<b>${fmt.n(r.hits)}</b> requests<br>${hh(r.hour)}:00 to ${hh(r.hour)}:59`,
            aria: r => `${hh(r.hour)}:00, ${fmt.n(r.hits)} requests. Open this hour`,
            selected: r => r.hour === peak.hour,
            onClick: r => Detail.open("hour", r.hour),
          });
          if (st.length) stripChart(body.querySelector("#dd-status"), st, {
            x: r => r.hits, color: r => statusColor(r.status), label: r => String(r.status),
            tip: r => `<b>${r.status}</b> ${esc(STATUS_NAMES[r.status] || "")}<br>${fmt.n(r.hits)} responses`,
            aria: r => `Status ${r.status}, ${fmt.n(r.hits)} responses. Open status detail`,
            onClick: r => Detail.open("status", r.status),
          });
        },
      };
    },

    hour(value) {
      if (!/^\d{1,2}$/.test(value) || +value > 23) return null;
      const h = +value, D = Store.data;
      const counts = new Map();
      for (const r of D.hour_by_date || []) if (r.hour === h && Store.inRange(r.date)) counts.set(r.date, r.hits);
      const rows = Store.daily().map(d => ({ date: d.date, hits: counts.get(d.date) || 0 }));
      const total = rows.reduce((a, r) => a + r.hits, 0);
      const rangeTotal = Store.totals().records || 1;
      const peak = rows.reduce((a, r) => (!a || r.hits > a.hits) ? r : a, null);
      const prevH = (h + 23) % 24, nextH = (h + 1) % 24;
      const onKey = e => {
        if (e.key === "ArrowLeft") { e.preventDefault(); Detail.open("hour", prevH); }
        if (e.key === "ArrowRight") { e.preventDefault(); Detail.open("hour", nextH); }
      };
      const nav = `<div class="daynav">
          <button type="button" class="btn" data-fk="prev" data-go="hour:${prevH}">Previous hour</button>
          <button type="button" class="btn" data-fk="next" data-go="hour:${nextH}">Next hour</button>
          <span class="hint">or press <kbd>←</kbd> <kbd>→</kbd></span></div>`;
      const busiest = [...rows].sort((a, b) => b.hits - a.hits).slice(0, 5).filter(r => r.hits);
      return {
        title: `${hh(h)}:00 to ${hh(h)}:59`,
        subtitle: `Each day from ${fmt.date(Store.from)} to ${fmt.date(Store.to)}`,
        onKey,
        html: `${nav}
          ${statGrid([
            [fmt.n(total), "requests in this hour"],
            [fmt.pct(100 * total / rangeTotal, 1), "of requests in the range"],
            [fmt.n(Math.round(total / (rows.length || 1))), "per day on average"],
            [peak && peak.hits ? fmt.date(peak.date) : "—", peak && peak.hits ? `peak day, ${fmt.n(peak.hits)} requests` : "peak day"],
          ])}
          <section class="dsec">
            <h3>Requests at ${hh(h)}:00 by date</h3>
            <p class="dnote">Select a point to open that day.</p>
            ${rows.length ? `<div id="dh-line"></div>` : `<div class="empty-inline">No days in the selected range.</div>`}
          </section>
          ${busiest.length ? `<section class="dsec"><h3>Busiest days at this hour</h3>
            ${xlist(busiest.map(r => ({ detail: `day:${r.date}`, name: fmt.dateLong(r.date), value: fmt.n(r.hits) })))}</section>` : ""}`,
        mount: body => {
          const host = body.querySelector("#dh-line");
          if (host) lineChart(host, rows, {
            height: 200, y: r => r.hits, area: true, xLabel: r => fmt.date(r.date), dateKey: r => r.date,
            tip: r => `<b>${fmt.n(r.hits)}</b> requests<br>${hh(h)}:00 on ${fmt.dateLong(r.date)}`,
            onClick: r => Detail.open("day", r.date),
          });
        },
      };
    },

    endpoint(path) {
      if (!path || path.length > 4000) return null;
      const D = Store.data, m = D.meta || {};
      const tu = D.top_urls || [], bl = D.broken_links || [];
      const ti = tu.findIndex(r => r.endpoint === path), bi = bl.findIndex(r => r.endpoint === path);
      const top = tu[ti], broken = bl[bi];
      const ext = extOf(path);
      const ct = ext && (D.content_types || []).find(r => String(r.ext).toLowerCase() === ext);
      const stats = [];
      if (top) {
        stats.push([fmt.n(top.hits), `requests, rank ${ti + 1} of ${tu.length}`]);
        if (m.records) stats.push([fmt.pct(100 * top.hits / m.records, 3), "of all requests"]);
      }
      if (broken) {
        stats.push([fmt.n(broken.hits), `404 responses, rank ${bi + 1} of ${bl.length} broken links`]);
        stats.push([fmt.n(broken.distinct_visitors), "distinct visitors got the 404"]);
      }
      const slash = path.lastIndexOf("/");
      const dir = slash >= 0 ? path.slice(0, slash + 1) : "";
      const siblings = dir ? tu.filter(r => r.endpoint !== path && r.endpoint.startsWith(dir)
        && !r.endpoint.slice(dir.length).includes("/")).slice(0, 6) : [];
      return {
        title: path, titleMono: true,
        subtitle: top ? `Ranked ${ti + 1} of ${tu.length} most requested URLs`
          : broken ? `Ranked ${bi + 1} of ${bl.length} broken links` : "Not in the precomputed top lists",
        html: `
          ${stats.length ? statGrid(stats) : `<div class="empty-inline">This path is not among the
            ${fmt.n(tu.length)} most requested URLs or the ${fmt.n(bl.length)} most frequent broken links,
            so the reports hold no totals for it.${HAS_LOGS ? " Search the raw logs to count it directly." : ""}</div>`}
          ${broken && top ? `<p class="dnote">Both served and broken: the URL was requested successfully
            and also returned 404, which usually means it moved or was removed during the two months.</p>` : ""}
          ${actionRow([
            HAS_LOGS && { label: "Search raw logs", href: logsHref({ path }) },
            { label: "Copy path", attrs: `data-copy="${esc(path)}" data-copy-msg="Path copied"` },
          ])}
          ${ct ? `<section class="dsec"><h3>File type</h3>${xlist([{ detail: `ext:${ct.ext}`, name: "." + ct.ext,
            value: `${fmt.n(ct.hits)} requests, ${ct.gb} GB` }])}</section>` : ""}
          ${siblings.length ? `<section class="dsec"><h3>Popular in <span class="mono">${esc(dir)}</span></h3>
            ${xlist(siblings.map(r => ({ detail: `endpoint:${r.endpoint}`, name: r.endpoint, mono: true, value: fmt.n(r.hits) })))}
            </section>` : ""}`,
      };
    },

    host(name) {
      if (!name || name.length > 1000) return null;
      const D = Store.data, m = D.meta || {}, X = derived();
      const th = D.top_hosts || [];
      const i = th.findIndex(r => r.host === name), row = th[i];
      const flagged = X.flagged.has(name);
      const bots = pageHref("/bots");
      return {
        title: name, titleMono: true,
        subtitle: row ? `Ranked ${i + 1} of ${th.length} busiest hosts` : "Not in the busiest hosts list",
        html: `
          ${flagged ? `<p class="flag"><span class="badge auto">flagged as automated</span>
            The bot detection model placed this host in the automated cluster.
            ${bots ? `<a href="${esc(bots)}">See how bots were detected</a>` : ""}</p>` : ""}
          ${row ? statGrid([
            [fmt.n(row.hits), "requests"],
            [m.records ? fmt.pct(100 * row.hits / m.records, 3) : "—", "of all requests"],
            [fmt.n(row.mb) + " MB", "transferred"],
            [fmt.bytes(row.mb * 1048576 / (row.hits || 1)), "per request on average"],
          ]) : `<div class="empty-inline">This host is not among the ${fmt.n(th.length)} busiest,
            so the reports hold no totals for it.${HAS_LOGS ? " Search the raw logs to see its requests." : ""}</div>`}
          ${actionRow([
            HAS_LOGS && { label: "Search raw logs", href: logsHref({ host: name }) },
            { label: "Copy host", attrs: `data-copy="${esc(name)}" data-copy-msg="Host copied"` },
          ])}`,
      };
    },

    status(value) {
      if (!/^\d{3}$/.test(value)) return null;
      const code = +value, D = Store.data, X = derived();
      const all = (D.status || []).find(r => r.status === code);
      const inRange = Store.byStatus().find(r => r.status === code);
      if (!all && !inRange) return {
        title: `${code} ${STATUS_NAMES[code] || ""}`.trim(), subtitle: statusClass(code),
        html: `<div class="empty-inline">The server never answered with ${code} in these logs.</div>`,
      };
      const rows = Store.daily().map(d => ({ date: d.date, hits: X.statusDay.get(d.date + "|" + code) || 0 }));
      const peak = rows.reduce((a, r) => (!a || r.hits > a.hits) ? r : a, null);
      const col = statusColor(code);
      let extra = "";
      if (code === 404) extra = `<section class="dsec"><h3>Most requested broken links</h3>
        ${xlist((D.broken_links || []).slice(0, 6).map(r => ({ detail: `endpoint:${r.endpoint}`, name: r.endpoint, mono: true, value: fmt.n(r.hits) })))}</section>`;
      const worstDays = rows.filter(r => r.hits).sort((a, b) => b.hits - a.hits).slice(0, 5);
      if (code >= 400 && code !== 404 && worstDays.length) extra = `<section class="dsec"><h3>Days with the most ${code}s</h3>
        ${xlist(worstDays.map(r => ({ detail: `day:${r.date}`, name: fmt.dateLong(r.date), value: fmt.n(r.hits) })))}</section>`;
      return {
        title: `${code} ${STATUS_NAMES[code] || ""}`.trim(),
        subtitle: statusClass(code),
        html: `
          ${statGrid([
            [fmt.n(inRange ? inRange.hits : 0), "responses in the range"],
            [fmt.pct(inRange ? inRange.pct : 0, 3), "of requests in the range"],
            [all ? fmt.pct(all.pct, 3) : "—", "of requests across all dates"],
            [peak && peak.hits ? fmt.date(peak.date) : "—", peak && peak.hits ? `busiest day, ${fmt.n(peak.hits)}` : "busiest day"],
          ])}
          ${actionRow([HAS_LOGS && { label: "Search raw logs", href: logsHref({ status: String(code) }) }])}
          <section class="dsec"><h3>${code} responses by date</h3>
            <p class="dnote">Select a point to open that day.</p>
            <div id="ds-line"></div></section>
          ${extra}`,
        mount: body => {
          if (rows.length) lineChart(body.querySelector("#ds-line"), rows, {
            height: 200, y: r => r.hits, color: col, area: true,
            xLabel: r => fmt.date(r.date), dateKey: r => r.date,
            tip: r => `<b>${fmt.n(r.hits)}</b> ${code} responses<br>${fmt.dateLong(r.date)}`,
            onClick: r => Detail.open("day", r.date),
          });
        },
      };
    },

    ext(value) {
      const ext = value.replace(/^\./, "").toLowerCase();
      if (!/^[a-z0-9_()\-]{1,16}$/.test(ext)) return null;
      const D = Store.data, m = D.meta || {};
      const ct = (D.content_types || []).find(r => String(r.ext).toLowerCase() === ext);
      const label = ext === "(none)" ? "No file extension" : "." + ext;
      if (!ct) return {
        title: label, subtitle: "File type",
        html: `<div class="empty-inline">No requests for ${esc(label)} files are among the
          ${(D.content_types || []).length} file types the reports break out.</div>
          ${HAS_LOGS && ext !== "(none)" ? actionRow([{ label: "Search raw logs", href: logsHref({ path: "." + ext }) }]) : ""}`,
      };
      const pReq = m.records ? 100 * ct.hits / m.records : 0;
      const pGb = m.gb ? 100 * ct.gb / m.gb : 0;
      const urls = (D.top_urls || []).filter(r => ext === "(none)" ? !extOf(r.endpoint) : extOf(r.endpoint) === ext).slice(0, 8);
      return {
        title: label, subtitle: "File type",
        html: `
          ${statGrid([
            [fmt.n(ct.hits), "requests"],
            [fmt.pct(pReq, 2), "of all requests"],
            [ct.gb + " GB", "transferred"],
            [fmt.pct(pGb, 2), "of all bytes"],
          ])}
          ${actionRow([HAS_LOGS && ext !== "(none)" && { label: "Search raw logs", href: logsHref({ path: "." + ext }) }])}
          <section class="dsec"><h3>Requests compared with bytes</h3>
            <p class="dnote">${pGb > pReq * 1.2 ? "Heavier than average: these files take a larger share of bandwidth than of requests."
              : pGb < pReq / 1.2 ? "Lighter than average: many requests, comparatively few bytes."
              : "Roughly proportional: its share of bytes matches its share of requests."}
              ${ct.hits ? ` Average size ${fmt.bytes(ct.gb * 1073741824 / ct.hits)}.` : ""}</p>
            <div id="de-bars"></div></section>
          ${urls.length ? `<section class="dsec"><h3>Most requested ${esc(label)} URLs</h3>
            ${xlist(urls.map(r => ({ detail: `endpoint:${r.endpoint}`, name: r.endpoint, mono: true, value: fmt.n(r.hits) })))}</section>` : ""}`,
        mount: body => hbarChart(body.querySelector("#de-bars"), [
          { k: "Share of requests", v: pReq, c: cssv("--series-1") },
          { k: "Share of bytes", v: pGb, c: cssv("--series-2") },
        ], { labelW: 150, valW: 90, rowH: 30, x: r => r.v, label: r => r.k, color: r => r.c,
             valueLabel: r => fmt.pct(r.v, 2), tip: r => `<b>${fmt.pct(r.v, 2)}</b> ${r.k.toLowerCase()}` }),
      };
    },
  },
};

/* Theme flips and range changes re-render an open detail in place. */
document.addEventListener("render", () => Detail.refresh());

/* Recently opened details, offered when the palette opens empty. */
const Recent = {
  list() { try { return JSON.parse(store.get("recent") || "[]"); } catch { return []; } },
  add(type, value) {
    const l = this.list().filter(r => !(r.type === type && r.value === value));
    l.unshift({ type, value });
    store.set("recent", JSON.stringify(l.slice(0, 5)));
  },
};

/* ------------------------------------------------------ keyboard shortcuts */
const NAV_KEYS = [["o", "/", "Overview"], ["t", "/traffic", "Traffic"],
  ["e", "/errors", "Errors"], ["p", "/performance", "Performance"]];

const Shortcuts = {
  open(o = {}) {
    const mod = IS_MAC ? "⌘" : "Ctrl";
    const row = (keys, what) => `<tr><td class="keys">${keys}</td><td>${what}</td></tr>`;
    const k = s => `<kbd>${esc(s)}</kbd>`;
    const navRows = NAV_KEYS.filter(([, route]) => navLink(route))
      .map(([key, , label]) => row(`${k("g")} then ${k(key)}`, `Go to ${label === "SQL" ? label : label.toLowerCase()}`)).join("");
    Drawer.open({
      title: "Keyboard shortcuts", key: "shortcuts", fromHistory: o.fromHistory,
      html: `
        <section class="dsec first"><h3>Anywhere</h3>
          <table class="keytable"><tbody>
            ${row(`${k(mod)} ${k("K")}`, "Open search")}
            ${row(k("/"), "Open search")}
            ${row(k("?"), "Show this list")}
            ${row(k("Esc"), "Close the search or the open panel")}
          </tbody></table></section>
        <section class="dsec"><h3>Search</h3>
          <table class="keytable"><tbody>
            ${row(`${k("↑")} ${k("↓")}`, "Move between results")}
            ${row(k("Enter"), "Open the highlighted result")}
          </tbody></table>
          <p class="dnote">Type a date such as 13 jul or 1995-07-13, a path such as /shuttle, a host, a status code or a file type.</p></section>
        <section class="dsec"><h3>Charts, tables and panels</h3>
          <table class="keytable"><tbody>
            ${row(k("Tab"), "Move to the next bar, point or table row")}
            ${row(`${k("Enter")} or ${k("Space")}`, "Open the focused bar or row")}
            ${row(`${k("←")} ${k("→")}`, "Previous or next day (or hour) in an open panel")}
          </tbody></table></section>
        ${navRows ? `<section class="dsec"><h3>Go to a page</h3>
          <table class="keytable"><tbody>${navRows}</tbody></table></section>` : ""}`,
    });
  },
};

let gPending = 0;
document.addEventListener("keydown", e => {
  const k = e.key;
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (k === "k" || k === "K")) {
    e.preventDefault();
    Palette.isOpen ? Palette.close() : Palette.open();
    return;
  }
  if (k === "Escape" && !e.defaultPrevented) {
    if (Palette.isOpen) { e.preventDefault(); Palette.close(); return; }
    if (Drawer.isOpen) { e.preventDefault(); Drawer.close(); return; }
  }
  // arrow keys with focus lost to <body> still page through an open day
  if (Drawer.isOpen && !Palette.isOpen && (e.target === document.body || e.target === document.documentElement)
      && Drawer.onKey && !e.metaKey && !e.ctrlKey && !e.altKey) { Drawer.onKey(e); if (e.defaultPrevented) return; }

  if (e.metaKey || e.ctrlKey || e.altKey || isTextField(e.target) || Palette.isOpen || e.defaultPrevented) return;
  if (k === "/") { e.preventDefault(); Palette.open(); return; }
  if (k === "?") { e.preventDefault(); Shortcuts.open(); return; }
  if (Drawer.isOpen) return;
  if (gPending && Date.now() - gPending < 1200) {
    gPending = 0;
    const hit = NAV_KEYS.find(([key]) => key === k.toLowerCase());
    const a = hit && navLink(hit[1]);
    if (a) { e.preventDefault(); location.href = a.href; }
    return;
  }
  gPending = (k === "g") ? Date.now() : 0;
});

/* --------------------------------------------------------- command palette */
const PAL_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const PAL_MONTHS_LONG = ["january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december"];

function scoreKey(text, q) {
  if (text === q) return 100;
  if (text.startsWith(q)) return 80 - Math.min(20, (text.length - q.length) / 4);
  const i = text.indexOf(q);
  if (i < 0) return 0;
  return (/[\/\s._\-:]/.test(text[i - 1]) ? 55 : 35) - Math.min(15, i / 8);
}
function scoreItem(item, q, tokens) {
  let best = 0;
  for (const key of item.keys) best = Math.max(best, scoreKey(key, q));
  if (!best && tokens.length > 1) {
    const all = item.keys.join(" ");
    if (tokens.every(t => all.includes(t))) best = 25;
  }
  return best;
}
function highlight(text, q) {
  const i = q ? text.toLowerCase().indexOf(q) : -1;
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
}

const Palette = {
  isOpen: false, opener: null, items: [], active: 0, _restoreInert: null, _index: null, _indexFor: null,

  open() {
    if (this.isOpen) { this.input.focus(); return; }
    const root = rootEl("palette-root");
    this.opener = document.activeElement;
    root.innerHTML = `
      <div class="palette-backdrop"></div>
      <div class="palette" role="dialog" aria-modal="true" aria-label="Search">
        <div class="palette-field">
          <input type="text" id="palette-input" role="combobox" aria-expanded="true" aria-autocomplete="list"
            aria-controls="palette-list" autocomplete="off" spellcheck="false"
            placeholder="Search pages, days, paths, hosts, status codes" aria-label="Search">
        </div>
        <div id="palette-list" role="listbox" aria-label="Results"></div>
        <div class="palette-foot" aria-hidden="true">
          <span><kbd>↑</kbd> <kbd>↓</kbd> to move</span><span><kbd>Enter</kbd> to open</span><span><kbd>Esc</kbd> to close</span>
        </div>
      </div>`;
    this.root = root;
    this.input = root.querySelector("input");
    this.list = root.querySelector("#palette-list");
    root.querySelector(".palette-backdrop").addEventListener("click", () => this.close());
    this.input.addEventListener("input", () => { this.active = 0; this.search(); });
    root.querySelector(".palette").addEventListener("keydown", e => this._key(e));
    this.list.addEventListener("pointermove", e => {
      const opt = e.target.closest("[role=option]");
      if (opt && +opt.dataset.i !== this.active) this.setActive(+opt.dataset.i, false);
    });
    this.list.addEventListener("click", e => {
      const opt = e.target.closest("[role=option]");
      if (opt) this.choose(+opt.dataset.i);
    });
    this._restoreInert = inertBehind(["palette-root", "toast-root", "tip"]);
    document.documentElement.classList.add("overlay-lock");
    this.isOpen = true;
    root.classList.add("open");
    this.input.focus();
    this.search();
    if (!Store.data) Store.ensure().then(() => { if (this.isOpen) this.search(); }, () => {
      this.loadFailed = true; if (this.isOpen) this.search(); });
  },

  close({ restoreFocus = true } = {}) {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.root.classList.remove("open");
    this.root.innerHTML = "";
    if (this._restoreInert) { this._restoreInert(); this._restoreInert = null; }
    if (!Drawer.isOpen) document.documentElement.classList.remove("overlay-lock");
    const op = this.opener;
    if (restoreFocus && op && op.isConnected && typeof op.focus === "function") op.focus({ preventScroll: true });
  },

  _key(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!this.items.length) return;
      const n = this.items.length;
      this.setActive((this.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n, true);
    } else if (e.key === "Home" && e.ctrlKey || e.key === "PageUp") {
      e.preventDefault(); if (this.items.length) this.setActive(0, true);
    } else if (e.key === "End" && e.ctrlKey || e.key === "PageDown") {
      e.preventDefault(); if (this.items.length) this.setActive(this.items.length - 1, true);
    } else if (e.key === "Enter") {
      e.preventDefault(); if (this.items.length) this.choose(this.active);
    } else if (e.key === "Tab") {
      e.preventDefault();   // one field, one list: Tab would only leave the dialog
    }
  },

  setActive(i, scroll) {
    const prev = this.list.querySelector('[aria-selected="true"]');
    if (prev) prev.setAttribute("aria-selected", "false");
    this.active = i;
    const opt = this.list.querySelector(`[data-i="${i}"]`);
    if (!opt) return;
    opt.setAttribute("aria-selected", "true");
    this.input.setAttribute("aria-activedescendant", opt.id);
    if (scroll) opt.scrollIntoView({ block: "nearest" });
  },

  choose(i) {
    const item = this.items[i];
    if (!item) return;
    if (item.detail) {
      this.close();
      openFromKey(item.detail);
    } else {
      this.close({ restoreFocus: !item.href });
      if (item.href) location.href = item.href;
      else if (item.run) item.run();
    }
  },

  /* Every searchable thing in the data, built once per dataset. */
  index() {
    const D = Store.data;
    if (!D) return [];
    if (this._indexFor === D) return this._index;
    const out = [], X = derived();
    const { min, max } = Store.bounds;
    if (min) for (let d = min; d <= max; d = isoAdd(d, 1)) {
      const [y, mo, da] = d.split("-"), mi = +mo - 1, n = String(+da);
      const wd = new Date(d + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short" }).toLowerCase();
      const row = X.daily.get(d);
      out.push({ group: "Days", title: fmt.dateLong(d), meta: row ? `${fmt.n(row.hits)} requests` : "No records",
        detail: `day:${d}`, order: d,
        keys: [d, `${n} ${PAL_MONTHS[mi]}`, `${da} ${PAL_MONTHS[mi]}`, `${n} ${PAL_MONTHS_LONG[mi]}`, `${PAL_MONTHS[mi]} ${n}`,
          `${PAL_MONTHS[mi]} ${da}`, `${PAL_MONTHS_LONG[mi]} ${n}`, `${n}/${mo}`, `${da}/${mo}`, `${da}/${mo}/${y}`,
          `${n} ${PAL_MONTHS[mi]} ${y}`, `${wd} ${n} ${PAL_MONTHS[mi]}`] });
    }
    const seen = new Map();
    for (const r of D.top_urls || []) seen.set(r.endpoint, { group: "Endpoints", title: r.endpoint, mono: true,
      meta: `${fmt.n(r.hits)} requests`, detail: `endpoint:${r.endpoint}`, weight: r.hits, keys: [r.endpoint.toLowerCase()] });
    for (const r of D.broken_links || []) if (!seen.has(r.endpoint)) seen.set(r.endpoint, { group: "Endpoints",
      title: r.endpoint, mono: true, meta: `${fmt.n(r.hits)} not found`, detail: `endpoint:${r.endpoint}`,
      weight: r.hits, keys: [r.endpoint.toLowerCase()] });
    for (const it of seen.values()) {
      const base = it.title.split("/").filter(Boolean).at(-1);
      if (base) it.keys.push(base.toLowerCase());
      out.push(it);
    }
    const hosts = new Map();
    for (const r of D.top_hosts || []) hosts.set(r.host, { group: "Hosts", title: r.host, mono: true,
      meta: `${fmt.n(r.hits)} requests${X.flagged.has(r.host) ? ", flagged as automated" : ""}`,
      detail: `host:${r.host}`, weight: r.hits, keys: [r.host.toLowerCase()] });
    for (const h of (D.bots && D.bots.flagged_examples) || []) if (!hosts.has(h)) hosts.set(h, { group: "Hosts",
      title: h, mono: true, meta: "Flagged as automated", detail: `host:${h}`, weight: 0, keys: [h.toLowerCase()] });
    out.push(...hosts.values());
    for (const r of D.status || []) {
      const name = STATUS_NAMES[r.status] || "";
      out.push({ group: "Status codes", title: `${r.status} ${name}`.trim(), meta: `${fmt.pct(r.pct, 2)} of requests`,
        detail: `status:${r.status}`, weight: r.hits,
        keys: [String(r.status), `${r.status} ${name}`.toLowerCase(), name.toLowerCase(), statusClass(r.status).toLowerCase()] });
    }
    for (const r of D.content_types || []) {
      const e = String(r.ext).toLowerCase();
      out.push({ group: "File types", title: e === "(none)" ? "No file extension" : "." + e,
        meta: `${fmt.n(r.hits)} requests, ${r.gb} GB`, detail: `ext:${r.ext}`, weight: r.hits,
        keys: e === "(none)" ? ["no extension", "(none)", "none"] : [e, "." + e] });
    }
    this._index = out; this._indexFor = D;
    return out;
  },

  staticItems(q) {
    const cur = $('nav.tabs a[aria-current="page"]');
    const pages = $$("nav.tabs a").map(a => ({ group: "Pages", title: a.textContent.trim(),
      meta: a === cur ? "Current page" : "", href: a.getAttribute("href"), keys: [a.textContent.trim().toLowerCase()] }));
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const actions = [
      { group: "Actions", title: "Toggle theme", meta: dark ? "Switch to light" : "Switch to dark", run: toggleTheme,
        keys: ["toggle theme", "theme", "dark mode", "light mode"] },
      { group: "Actions", title: "Copy link", meta: "Date range and open panel",
        run: () => copyText(location.href, "Link copied"), keys: ["copy link", "share", "url"] },
      Store.data && !Store.isFullRange && { group: "Actions", title: "Reset date range",
        meta: `Currently ${fmt.date(Store.from)} to ${fmt.date(Store.to)}`,
        run: () => { const { min, max } = Store.bounds; Store.setRange(min, max); toast("Showing all dates"); },
        keys: ["reset date range", "all dates", "clear range"] },
      { group: "Actions", title: "Show keyboard shortcuts", meta: "?", run: () => Shortcuts.open(),
        keys: ["keyboard shortcuts", "help", "keys"] },
    ].filter(Boolean);
    if (q && HAS_LOGS) actions.push({ group: "Actions", title: `Search raw logs for "${q}"`, meta: "Log explorer",
      href: logsHref({ q }), keys: [], always: true });
    return [...pages, ...actions];
  },

  search() {
    const raw = this.input.value.trim();
    const q = raw.toLowerCase().replace(/\s+/g, " ");
    const tokens = q.split(" ").filter(Boolean);
    const groups = new Map();
    const push = (it, score) => {
      if (!groups.has(it.group)) groups.set(it.group, []);
      groups.get(it.group).push({ ...it, score });
    };
    const ORDER = ["Recent", "Pages", "Actions", "Days", "Endpoints", "Hosts", "Status codes", "File types"];

    if (!q) {
      if (Store.data) for (const r of Recent.list()) {
        const it = this.index().find(x => x.detail === `${r.type}:${r.value}`);
        if (it) push({ ...it, group: "Recent" }, 1);
        else if (r.type === "hour") push({ group: "Recent", title: `${hh(r.value)}:00 to ${hh(r.value)}:59`,
          meta: "Hour of day", detail: `hour:${r.value}`, keys: [] }, 1);
      }
      for (const it of this.staticItems("")) push(it, 1);
    } else {
      for (const it of this.staticItems(raw)) {
        const s = it.always ? 1 : scoreItem(it, q, tokens);
        if (s) push(it, s);
      }
      for (const it of this.index()) {
        const s = scoreItem(it, q, tokens);
        if (s) push(it, s);
      }
    }
    const caps = { Days: 10, Endpoints: 12, Hosts: 10, "Status codes": 8, "File types": 8 };
    const ranked = [...groups.entries()].map(([name, items]) => {
      items.sort((a, b) => b.score - a.score || (a.order && b.order ? a.order.localeCompare(b.order) : 0)
        || (b.weight || 0) - (a.weight || 0));
      return [name, items.slice(0, caps[name] || 12)];
    });
    if (q) ranked.sort((a, b) => {
      const sa = Math.max(...a[1].map(i => i.always ? 0 : i.score)), sb = Math.max(...b[1].map(i => i.always ? 0 : i.score));
      return sb - sa || ORDER.indexOf(a[0]) - ORDER.indexOf(b[0]);
    });
    this.items = [];
    let html = "";
    for (const [name, items] of ranked) {
      const room = 50 - this.items.length;
      if (room <= 0) break;
      const gid = "pg-" + ORDER.indexOf(name);
      html += `<div role="group" aria-labelledby="${gid}"><div class="pal-group" id="${gid}" role="presentation">${esc(name)}</div>`;
      for (const it of items.slice(0, room)) {
        const i = this.items.length;
        this.items.push(it);
        html += `<div role="option" id="po-${i}" data-i="${i}" aria-selected="false" class="pal-opt">
          <span class="pal-title${it.mono ? " mono" : ""}">${highlight(it.title, q)}</span>
          ${it.meta ? `<span class="pal-meta">${esc(it.meta)}</span>` : ""}</div>`;
      }
      html += `</div>`;
    }
    if (!this.items.length) {
      html = `<div class="pal-empty">No matches for “${esc(raw)}”. Try a date like 13 jul, a path like /shuttle, or a status code like 404.</div>`;
    }
    if (q && !Store.data) html += this.loadFailed
      ? `<div class="pal-empty">Report data could not be loaded, so only pages and actions are searchable.</div>`
      : `<div class="pal-empty"><span class="spin" aria-hidden="true"></span>Loading days, paths and hosts</div>`;
    this.list.innerHTML = html;
    this.input.removeAttribute("aria-activedescendant");
    if (this.items.length) this.setActive(Math.min(this.active, this.items.length - 1), true);
  },
};

addEventListener("DOMContentLoaded", () => {
  const trigger = $("#cmdk");
  if (trigger) {
    const kbd = trigger.querySelector("kbd");
    if (kbd && !IS_MAC) kbd.textContent = "Ctrl K";
    trigger.addEventListener("click", () => Palette.open());
  }
  // a shared link that names a detail reopens it
  const detail = Hash.get("detail");
  if (detail) openFromKey(detail, { fromHistory: true });
});

/* ------------------------------------------------------------ freshness */
/* Spark can rewrite dashboard.json while a page is open. Check quietly and
   offer a reload rather than showing numbers that no longer match the files. */
(function freshness() {
  if (!/^https?:$/.test(location.protocol) || !HAS_LOGS) return;
  let baseline = null, announced = null, last = 0;
  async function check() {
    if (document.visibilityState !== "visible") return;
    last = Date.now();
    try {
      const r = await fetch("/healthz", { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      const gen = j.generated_at || null;
      const known = (Store.data && Store.data.generated_at) || baseline;
      if (known == null) { baseline = gen; return; }
      if (gen && gen !== known && gen !== announced) {
        announced = gen;
        toast("Spark wrote new data", { timeout: 0, action: { label: "Reload", onClick: () => location.reload() } });
      }
    } catch { /* server restarting; try again next tick */ }
  }
  setInterval(check, 30000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - last > 30000) check();
  });
  addEventListener("load", () => { if (!Store.data) setTimeout(check, 1500); });
})();

/* Boot: load once, render, then re-render on range change or theme flip. */
async function boot(render) {
  try {
    await Store.ensure();
  } catch (e) {
    document.querySelector(".wrap").innerHTML =
      `<div class="empty"><h2>Could not load the data</h2>
       <p>${esc(e.message)}. Try <code>make dashboard</code>, then reload.</p></div>`;
    return;
  }
  Store.hashRange = true;
  Store.writeRangeHash();
  const rb = $("#rangebar");
  if (rb) mountRangeBar(rb);
  document.addEventListener("render", render);
  render();
}
