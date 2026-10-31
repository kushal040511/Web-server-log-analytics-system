/* Hand-drawn SVG charts. No library, no CDN, no build step — which means the
   page works offline and there is nothing to go stale. Every chart reads its
   colours from CSS custom properties, so theme switching is free.

   Interaction lives here too, so every page gets the same behaviour for free:
   marks are clickable and keyboard operable when a page passes onClick, time
   series can be brushed, and nothing registers a document-level listener that
   outlives a render (charts are rebuilt on every "render" event). */
"use strict";

const NS = "http://www.w3.org/2000/svg";
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

const fmt = {
  n: v => (v ?? 0).toLocaleString("en-US"),
  k: v => v >= 1e9 ? (v / 1e9).toFixed(1) + "B"
        : v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
        : v >= 1e3 ? (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + "K"
        : String(Math.round(v)),
  pct: (v, d = 2) => (+v).toFixed(d) + "%",
  bytes: v => v >= 1073741824 ? (v / 1073741824).toFixed(2) + " GB"
            : v >= 1048576 ? (v / 1048576).toFixed(1) + " MB"
            : v >= 1024 ? (v / 1024).toFixed(1) + " KB" : Math.round(v) + " B",
  date: s => { const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }); },
  dateLong: s => { const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" }); },
  /* A log timestamp as the server wrote it: "13 Jul 09:00:05". Parsed by hand
     rather than through Date, because the logs are in Florida local time and
     the viewer's time zone must not shift what the log line says. */
  time: iso => {
    const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(iso ?? ""));
    if (!m) return String(iso ?? "");
    return `${m[3]} ${MONTHS[+m[2] - 1] || m[2]} ${m[4]}:${m[5]}:${m[6] || "00"}`;
  },
  /* How long a period covers, in words — derived from the data, never hardcoded,
     so a different log window never leaves a stale claim on the page. */
  span: (a, b) => {
    const days = Math.round((new Date(b + "T00:00:00") - new Date(a + "T00:00:00")) / 86400000) + 1;
    if (days < 14) return `${days} day${days === 1 ? "" : "s"}`;
    if (days < 60) { const w = Math.round(days / 7); return `${w} week${w === 1 ? "" : "s"}`; }
    const m = Math.round(days / 30.4);
    return `${m} month${m === 1 ? "" : "s"}`;
  },
};

function el(tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}

/* Tooltip HTML flattened to a sentence for aria-label and the live region.
   Parsed inside a <template>, so nothing in it runs or loads. */
function chartPlain(html) {
  const t = document.createElement("template");
  t.innerHTML = String(html ?? "").replace(/<br\s*\/?>/gi, ", ");
  return t.content.textContent.replace(/\s+/g, " ").trim();
}
const chartEsc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------------------------------------- tooltip */
let tipEl = null;
function tipNode() {
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.id = "tip"; document.body.appendChild(tipEl); }
  return tipEl;
}
function showTip(evt, html) {
  const t = tipNode();
  t.innerHTML = html; t.style.opacity = 1;
  const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY - h - 10;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (x < 8) x = 8;
  if (y < 8) y = evt.clientY + pad;
  t.style.left = x + "px"; t.style.top = y + "px";
}
const hideTip = () => { if (tipEl) tipEl.style.opacity = 0; };

/* Keyboard focus has no pointer position, so the tooltip is anchored to the
   top centre of whatever is focused. */
function tipAt(node, html) {
  const bb = node.getBoundingClientRect();
  showTip({ clientX: bb.left + bb.width / 2, clientY: bb.top }, html);
}

function hoverable(node, tipFn, row) {
  node.addEventListener("pointerenter", e => showTip(e, tipFn(row)));
  node.addEventListener("pointermove", e => showTip(e, tipFn(row)));
  node.addEventListener("pointerleave", hideTip);
}

/* Screen pixels to chart units and back. getScreenCTM accounts for the
   viewBox scaling and letterboxing, which a plain bounding-box ratio does not. */
function toChart(svg, clientX, clientY) {
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  return new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
}
function toScreen(svg, x, y) {
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  return new DOMPoint(x, y).matrixTransform(ctm);
}

/* A mark (bar, segment) becomes a button when the page passes onClick:
   focusable, labelled, Enter or Space opens it. Without onClick it stays a
   plain hover target, exactly as before. */
function markable(node, o, row, i) {
  node.classList.add("mark");
  if (o.selected) {
    // Only dim when something is actually selected, so a selection that falls
    // outside the rows on screen never greys out the whole chart.
    node.setAttribute("opacity", o.selected(row) ? 1 : 0.45);
  }
  hoverable(node, o.tip, row);
  if (!o.onClick) return;
  node.classList.add("clickable");
  node.setAttribute("tabindex", "0");
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", o.aria ? o.aria(row) : chartPlain(o.tip(row)));
  node.addEventListener("click", e => o.onClick(row, i, e));
  node.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    if (!e.repeat) o.onClick(row, i, e);
  });
  node.addEventListener("focus", () => { if (node.matches(":focus-visible")) tipAt(node, o.tip(row)); });
  node.addEventListener("blur", hideTip);
}

/* A transparent target covering a whole row or column, drawn under the mark,
   so a one-pixel bar is still easy to hover, click and tap. */
function hitArea(svg, attrs, o, row, i) {
  const hit = el("rect", { ...attrs, fill: "transparent", class: "hit" });
  hoverable(hit, o.tip, row);
  if (o.onClick) {
    hit.style.cursor = "pointer";
    hit.addEventListener("click", e => o.onClick(row, i, e));
  }
  svg.appendChild(hit);
  return hit;
}

/* o.selected dims the unselected marks, but only when at least one row on
   screen is selected; otherwise every mark keeps full opacity. */
function selectionFor(rows, o) {
  if (!o.selected) return o;
  return rows.some(r => o.selected(r)) ? o : { ...o, selected: null };
}

/* Drawing width in chart units. A card wider than 700px gets a wider drawing
   at 1:1 scale rather than a 700-unit one letterboxed in the middle; a
   narrower card scales the 700-unit drawing down, and the height follows the
   aspect ratio so no empty band is left above and below. */
const chartWidth = host => Math.max(700, Math.round(host.clientWidth || 0));

function frame(host, W, H) {
  host.innerHTML = "";
  hideTip();   // a re-render removes the hovered node before pointerleave can fire
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", style: "height:auto" });
  host.appendChild(svg);
  return svg;
}

function yAxis(svg, m, iw, ih, max, fy, steps = 3) {
  for (let i = 0; i <= steps; i++) {
    const v = max * i / steps, y = m.t + ih - (v / max) * ih;
    svg.appendChild(el("line", { x1: m.l, x2: m.l + iw, y1: y, y2: y, class: "gridline" }));
    svg.appendChild(el("text", { x: m.l - 7, y: y + 3.5, class: "axis", "text-anchor": "end" },
                      fy ? fy(v) : fmt.k(v)));
  }
}

/* ---------------------------------------------------------- vertical bars */
function barChart(host, rows, o) {
  if (!rows || !rows.length) return;
  o = selectionFor(rows, o);
  const W = chartWidth(host), H = o.height || 195, m = { t: 10, r: 8, b: 26, l: 54 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const max = Math.max(...rows.map(o.y)) * 1.12 || 1;
  const svg = frame(host, W, H);
  yAxis(svg, m, iw, ih, max, o.fy);
  const bw = iw / rows.length;
  rows.forEach((r, i) => {
    const v = o.y(r), h = (v / max) * ih;
    const x = m.l + i * bw, y = m.t + ih - h;
    hitArea(svg, { x, y: m.t, width: bw, height: H - m.t }, o, r, i);
    const rect = el("rect", { x: x + bw * 0.13, y, width: bw * 0.74, height: Math.max(h, 1),
                              rx: 2, fill: o.color ? o.color(r, i) : cssv("--series-1") });
    markable(rect, o, r, i); svg.appendChild(rect);
    const lab = o.xLabel ? o.xLabel(r, i) : null;
    if (lab != null)
      svg.appendChild(el("text", { x: x + bw / 2, y: H - 8, class: "axis", "text-anchor": "middle",
                                   "pointer-events": "none" }, lab));
  });
  return svg;
}

/* -------------------------------------------------------- horizontal bars */
function hbarChart(host, rows, o) {
  if (!rows || !rows.length) return;
  o = selectionFor(rows, o);
  const rowH = o.rowH || 26, labelW = o.labelW || 190, valW = o.valW || 128;
  const W = chartWidth(host), H = rows.length * rowH + 6;
  const max = Math.max(...rows.map(o.x)) || 1;
  const iw = W - labelW - valW;
  const svg = frame(host, W, H);
  rows.forEach((r, i) => {
    const y = i * rowH + 3, v = o.x(r), w = Math.max((v / max) * iw, 1.5);
    hitArea(svg, { x: 0, y, width: W, height: rowH }, o, r, i);
    svg.appendChild(el("text", { x: labelW - 9, y: y + rowH * 0.63, class: "lbl", "text-anchor": "end",
                                 "pointer-events": "none" }, o.label(r)));
    const rect = el("rect", { x: labelW, y: y + rowH * 0.18, width: w, height: rowH * 0.62,
                              rx: 2.5, fill: o.color ? o.color(r, i) : cssv("--series-1") });
    markable(rect, o, r, i); svg.appendChild(rect);
    svg.appendChild(el("text", { x: labelW + w + 7, y: y + rowH * 0.63, class: "val", "pointer-events": "none" },
                      o.valueLabel ? o.valueLabel(r) : fmt.n(v)));
  });
  return svg;
}

/* ------------------------------------------------------------ time series */
/* Pointer: hover shows the nearest point, a click opens it (o.onClick), a
   horizontal drag selects a span of at least two points (o.onBrush). Escape
   cancels a drag. Touch keeps vertical scrolling; a sideways drag brushes.
   Keyboard, when either handler is given: Left/Right move between points,
   Shift extends a selection, Enter opens the point or applies the selection. */
function lineChart(host, rows, o) {
  if (!rows || !rows.length) return;
  const W = chartWidth(host), H = o.height || 210, m = { t: 12, r: 10, b: 26, l: 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const max = Math.max(...rows.map(o.y)) * 1.14 || 1;
  const X = i => m.l + (rows.length === 1 ? iw / 2 : (i / (rows.length - 1)) * iw);
  const Y = v => m.t + ih - (v / max) * ih;
  const svg = frame(host, W, H);
  yAxis(svg, m, iw, ih, max, o.fy);

  // Missing days are absent rows, not zeros — shade any calendar discontinuity
  // so a logging outage reads as a gap rather than a straight line.
  if (o.dateKey) {
    const DAY = 864e5, gaps = [];
    for (let i = 1; i < rows.length; i++) {
      const miss = Math.round((Date.parse(o.dateKey(rows[i])) - Date.parse(o.dateKey(rows[i - 1]))) / DAY) - 1;
      if (miss >= 1) gaps.push({ i, miss });
    }
    // all the shading first, then all the labels — otherwise a later band
    // paints over an earlier band's label
    gaps.forEach(g => svg.appendChild(el("rect", { x: X(g.i - 1), y: m.t,
      width: X(g.i) - X(g.i - 1), height: ih, fill: cssv("--surface-3") })));
    gaps.filter(g => g.miss >= 2).forEach(g => svg.appendChild(el("text", {
      x: (X(g.i - 1) + X(g.i)) / 2, y: m.t + 10, class: "axis", "text-anchor": "middle" },
      `${g.miss} days missing`)));
  }

  // A span covering points a..b, padded half a step each side so the end
  // points sit inside the band rather than on its edge.
  const half = rows.length > 1 ? iw / (rows.length - 1) / 2 : iw / 2;
  const spanX = (a, b) => {
    const lo = Math.min(a, b), hi = Math.max(a, b);
    const x1 = Math.max(m.l, X(lo) - half), x2 = Math.min(m.l + iw, X(hi) + half);
    return { x: x1, width: Math.max(x2 - x1, 1) };
  };

  // The range the page is currently filtered to, if it lies on this chart.
  if (o.selection && o.dateKey) {
    const keys = rows.map(r => String(o.dateKey(r)).slice(0, 10));
    const a = keys.findIndex(k => k >= o.selection.from);
    let b = -1;
    keys.forEach((k, i) => { if (k <= o.selection.to) b = i; });
    if (a !== -1 && b >= a) {
      const s = spanX(a, b);
      svg.appendChild(el("rect", { ...s, y: m.t, height: ih, class: "selection",
                                   fill: cssv("--series-1"), opacity: 0.1, "pointer-events": "none" }));
    }
  }

  const col = o.color || cssv("--series-1");
  const pts = rows.map((r, i) => [X(i), Y(o.y(r))]);
  const path = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(2) + " " + p[1].toFixed(2)).join(" ");
  if (o.area)
    svg.appendChild(el("path", { d: `${path} L${pts.at(-1)[0]} ${Y(0)} L${pts[0][0]} ${Y(0)} Z`,
                                 fill: col, opacity: .11 }));
  svg.appendChild(el("path", { d: path, fill: "none", stroke: col, "stroke-width": 2,
                               "stroke-linejoin": "round", "stroke-linecap": "round" }));
  if (o.mean != null) {
    const y = Y(o.mean);
    svg.appendChild(el("line", { x1: m.l, x2: m.l + iw, y1: y, y2: y, stroke: cssv("--text-muted"),
                                 "stroke-width": 1.4, "stroke-dasharray": "5 4" }));
    svg.appendChild(el("text", { x: m.l + 6, y: y - 5, class: "axis" }, o.meanLabel || ""));
  }
  const step = Math.max(1, Math.round(rows.length / 6));
  rows.forEach((r, i) => {
    if (i % step === 0)
      svg.appendChild(el("text", { x: X(i), y: H - 8, class: "axis", "text-anchor": "middle" }, o.xLabel(r)));
  });

  const brush = el("rect", { y: m.t, height: ih, x: m.l, width: 0, class: "brush", fill: cssv("--series-1"),
                             "fill-opacity": 0.16, stroke: cssv("--series-1"), "stroke-opacity": 0.6,
                             "stroke-width": 1, "pointer-events": "none", visibility: "hidden" });
  const cl = el("line", { y1: m.t, y2: m.t + ih, stroke: cssv("--text-muted"), "stroke-width": 1, opacity: 0,
                          "pointer-events": "none" });
  const dot = el("circle", { r: 4, fill: col, stroke: cssv("--surface-1"), "stroke-width": 2, opacity: 0,
                             "pointer-events": "none" });
  const ring = el("rect", { x: m.l - 3, y: m.t - 3, width: iw + 6, height: ih + 6, rx: 4, fill: "none",
                            stroke: cssv("--series-1"), "stroke-width": 2, "pointer-events": "none",
                            visibility: "hidden", class: "focus-ring" });
  svg.appendChild(brush); svg.appendChild(cl); svg.appendChild(dot); svg.appendChild(ring);
  const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", class: "hit" });
  svg.appendChild(hit);

  const interactive = !!(o.onClick || o.onBrush);
  const clampI = i => Math.max(0, Math.min(rows.length - 1, i));
  const indexAt = clientX => clampI(Math.round((toChart(svg, clientX, 0).x - m.l) / iw * (rows.length - 1)));
  const cursor = i => {
    cl.setAttribute("x1", X(i)); cl.setAttribute("x2", X(i)); cl.setAttribute("opacity", .5);
    dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(o.y(rows[i]))); dot.setAttribute("opacity", 1);
  };
  const uncursor = () => { cl.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); };
  const drawBrush = (a, b) => {
    const s = spanX(a, b);
    brush.setAttribute("x", s.x); brush.setAttribute("width", s.width);
    brush.setAttribute("visibility", "visible");
  };
  const clearBrush = () => brush.setAttribute("visibility", "hidden");
  const lbl = r => chartEsc(o.xLabel ? o.xLabel(r) : "");
  const spanTip = (a, b) => {
    const lo = Math.min(a, b), hi = Math.max(a, b);
    return `<b>${lbl(rows[lo])}</b> to <b>${lbl(rows[hi])}</b><br>${hi - lo + 1} points`;
  };

  // Screen-reader echo of what the tooltip shows while moving by keyboard.
  let live = null;
  if (interactive) {
    live = document.createElement("div");
    live.className = "sr-only"; live.setAttribute("aria-live", "polite");
    host.appendChild(live);
  }
  const say = html => { if (live) live.textContent = chartPlain(html); };
  const tipPoint = (i, html) => {
    const p = toScreen(svg, X(i), m.t);
    showTip({ clientX: p.x, clientY: p.y + 6 }, html);
  };

  /* pointer ------------------------------------------------------------ */
  let drag = null;   // {id, i0, x0, i1, moved}
  const endDrag = () => {
    if (!drag) return;
    window.removeEventListener("keydown", onDragKey, true);
    try { hit.releasePointerCapture(drag.id); } catch (_) { /* already released */ }
    drag = null;
  };
  // Registered only for the life of one drag, and removed when it ends, so a
  // re-render never leaves a stale listener behind.
  function onDragKey(e) {
    if (!svg.isConnected) { endDrag(); return; }
    if (e.key !== "Escape") return;
    e.preventDefault(); e.stopPropagation();
    clearBrush(); hideTip(); endDrag();
  }

  hit.addEventListener("pointerdown", e => {
    if (!interactive || (e.pointerType === "mouse" && e.button !== 0)) return;
    const i = indexAt(e.clientX);
    drag = { id: e.pointerId, i0: i, i1: i, x0: e.clientX, moved: false };
    if (o.onBrush) {
      try { hit.setPointerCapture(e.pointerId); } catch (_) { /* pointer gone */ }
      window.addEventListener("keydown", onDragKey, true);
    }
  });
  hit.addEventListener("pointermove", e => {
    const i = indexAt(e.clientX);
    cursor(i);
    if (drag && e.pointerId === drag.id) {
      if (!drag.moved && Math.abs(e.clientX - drag.x0) > 4) drag.moved = true;
      drag.i1 = i;
      if (drag.moved && o.onBrush) {
        e.preventDefault();
        drawBrush(drag.i0, i);
        showTip(e, spanTip(drag.i0, i));
        return;
      }
    }
    showTip(e, o.tip(rows[i]));
  });
  hit.addEventListener("pointerup", e => {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    endDrag();
    if (d.moved) {
      clearBrush();
      const i1 = indexAt(e.clientX);
      if (o.onBrush && i1 !== d.i0) {
        hideTip();
        const lo = Math.min(d.i0, i1), hi = Math.max(d.i0, i1);
        o.onBrush(rows[lo], rows[hi]);
      }
    } else if (o.onClick) {
      const i = indexAt(e.clientX);
      o.onClick(rows[i], i, e);
    }
  });
  hit.addEventListener("pointercancel", () => { clearBrush(); endDrag(); });
  hit.addEventListener("lostpointercapture", () => { if (drag) { clearBrush(); endDrag(); } });
  hit.addEventListener("pointerleave", () => {
    if (drag && drag.moved) return;   // captured drags keep going outside the plot
    uncursor(); hideTip();
  });

  if (interactive) {
    hit.classList.add("interactive");
    hit.style.cursor = o.onBrush ? "crosshair" : "pointer";
    // Let a finger scroll the page vertically; sideways movement brushes.
    if (o.onBrush) { hit.style.touchAction = "pan-y"; svg.style.touchAction = "pan-y"; }

    /* keyboard ---------------------------------------------------------- */
    let ki = rows.length - 1, anchor = null;
    hit.setAttribute("tabindex", "0");
    hit.setAttribute("role", "group");
    hit.setAttribute("aria-roledescription", "time series");
    const how = ["Left and Right arrow keys move between points"];
    if (o.onClick) how.push("Enter opens the point");
    if (o.onBrush) how.push("Shift with arrow keys selects a span, Enter applies it, Escape cancels");
    hit.setAttribute("aria-label", `${o.ariaLabel || "Chart"}. ${how.join(". ")}.`);

    const showKey = () => {
      cursor(ki);
      if (anchor != null && anchor !== ki) {
        drawBrush(anchor, ki);
        const html = spanTip(anchor, ki);
        tipPoint(ki, html); say(html);
      } else {
        clearBrush();
        const html = o.tip(rows[ki]);
        tipPoint(ki, html); say(html);
      }
    };
    hit.addEventListener("focus", () => {
      if (!hit.matches(":focus-visible")) return;
      ring.setAttribute("visibility", "visible");
      showKey();
    });
    hit.addEventListener("blur", () => {
      ring.setAttribute("visibility", "hidden");
      anchor = null; clearBrush(); uncursor(); hideTip();
    });
    hit.addEventListener("keydown", e => {
      const k = e.key;
      let next = null;
      if (k === "ArrowLeft" || k === "ArrowDown") next = ki - 1;
      else if (k === "ArrowRight" || k === "ArrowUp") next = ki + 1;
      else if (k === "Home") next = 0;
      else if (k === "End") next = rows.length - 1;
      else if (k === "PageDown") next = ki - 7;
      else if (k === "PageUp") next = ki + 7;
      if (next != null) {
        e.preventDefault();
        ring.setAttribute("visibility", "visible");
        if (e.shiftKey && o.onBrush) { if (anchor == null) anchor = ki; }
        else anchor = null;
        ki = clampI(next);
        showKey();
        return;
      }
      if (k === "Enter" || k === " ") {
        e.preventDefault();
        if (e.repeat) return;
        if (anchor != null && anchor !== ki && o.onBrush) {
          const lo = Math.min(anchor, ki), hi = Math.max(anchor, ki);
          anchor = null; clearBrush(); hideTip();
          o.onBrush(rows[lo], rows[hi]);
        } else if (o.onClick) {
          anchor = null; clearBrush(); hideTip();
          o.onClick(rows[ki], ki, e);
        }
        return;
      }
      if (k === "Escape" && anchor != null) {
        // Swallow it so an enclosing drawer does not close as well.
        e.preventDefault(); e.stopPropagation();
        anchor = null; showKey();
      }
    });
  }
  return svg;
}

/* --------------------------------------------------------- stacked strip */
/* One row, segments proportional to value. Good for a share-of-total that
   doesn't deserve a whole chart. */
function stripChart(host, rows, o) {
  if (!rows || !rows.length) return;
  o = selectionFor(rows, o);
  // No fixed label columns here, so the strip can be drawn at the card's real
  // width even in a narrow card, which keeps its labels at full size.
  const W = Math.max(240, Math.round(host.clientWidth || 700)), H = 46;
  const total = rows.reduce((a, r) => a + o.x(r), 0) || 1;
  const svg = frame(host, W, H);
  let x = 0;
  rows.forEach((r, i) => {
    const w = (o.x(r) / total) * W;
    const rect = el("rect", { x: x + (i ? 1 : 0), y: 6, width: Math.max(w - (i ? 1 : 0), 0.5),
                              height: 22, rx: 2, fill: o.color(r, i) });
    markable(rect, o, r, i); svg.appendChild(rect);
    if (w > 54)
      svg.appendChild(el("text", { x: x + w / 2, y: 41, class: "axis", "text-anchor": "middle",
                                   "pointer-events": "none" }, o.label(r)));
    x += w;
  });
  return svg;
}

/* --------------------------------------------------------------- heatmap */
/* Rows by columns, one cell each (date by hour, weekday by hour). Shade is
   telemetry blue on a square-root scale, so a quiet night still shows some
   texture next to a busy afternoon; a cell with no data is drawn as a gap.
   With onClick the grid is a single tab stop: arrow keys move between cells,
   Home/End jump to the ends of a row, Enter or Space opens the cell. Every
   listener sits on the svg itself, so a thousand cells cost four handlers. */
function heatmap(host, o) {
  const rows = o.rows || [], cols = o.cols || [];
  if (!rows.length || !cols.length) return;
  const cellH = o.cellH || 14, labelW = o.labelW || 56;
  const rowLabel = o.rowLabel || (k => String(k));
  const colLabel = o.colLabel || (k => String(k));
  const W = chartWidth(host), m = { t: 2, r: 4, b: 22, l: labelW };
  const iw = W - m.l - m.r, ih = rows.length * cellH;
  const H = m.t + ih + m.b;
  const cw = iw / cols.length;
  const svg = frame(host, W, H);

  const vals = rows.map(rk => cols.map(ck => {
    const v = o.value(rk, ck);
    return v == null || Number.isNaN(+v) ? null : +v;
  }));
  const max = Math.max(0, ...vals.flat().filter(v => v != null)) || 1;
  const base = cssv("--series-1"), empty = cssv("--surface-3");
  const gap = cellH > 6 ? 1 : 0;

  rows.forEach((rk, ri) => {
    const lab = rowLabel(rk, ri);
    if (lab != null)
      svg.appendChild(el("text", { x: m.l - 7, y: m.t + ri * cellH + cellH / 2 + 3.5, class: "axis",
                                   "text-anchor": "end", "pointer-events": "none" }, lab));
  });
  cols.forEach((ck, ci) => {
    const lab = colLabel(ck, ci);
    if (lab != null)
      svg.appendChild(el("text", { x: m.l + ci * cw + cw / 2, y: H - 7, class: "axis",
                                   "text-anchor": "middle", "pointer-events": "none" }, lab));
  });

  const grid = el("g", o.onClick ? { role: "grid", "aria-label": o.ariaLabel || "Heatmap" } : {});
  svg.appendChild(grid);
  const cells = [];
  rows.forEach((rk, ri) => {
    const g = el("g", o.onClick ? { role: "row" } : {});
    grid.appendChild(g);
    cells.push(cols.map((ck, ci) => {
      const v = vals[ri][ci];
      const attrs = { x: m.l + ci * cw + gap / 2, y: m.t + ri * cellH + gap / 2,
                      width: Math.max(cw - gap, 0.5), height: Math.max(cellH - gap, 0.5),
                      rx: cellH >= 10 ? 1.5 : 0, class: "hm-cell", "data-r": ri, "data-c": ci };
      if (v == null) attrs.fill = empty;
      else { attrs.fill = base; attrs["fill-opacity"] = (0.06 + 0.94 * Math.sqrt(v / max)).toFixed(3); }
      const c = el("rect", attrs);
      if (o.onClick) {
        c.classList.add("clickable");
        c.setAttribute("role", "gridcell");
        c.setAttribute("tabindex", "-1");
        c.setAttribute("aria-label", chartPlain(o.tip ? o.tip(rk, ck, v) : `${rowLabel(rk, ri)} ${colLabel(ck, ci)}: ${v ?? "no data"}`));
      }
      g.appendChild(c);
      return c;
    }));
  });

  const outline = el("rect", { fill: "none", stroke: cssv("--text-primary"), "stroke-width": 1.5, rx: 2,
                               "pointer-events": "none", visibility: "hidden", class: "hm-hover" });
  const ring = el("rect", { fill: "none", stroke: cssv("--series-1"), "stroke-width": 2.5, rx: 2,
                            "pointer-events": "none", visibility: "hidden", class: "hm-focus" });
  svg.appendChild(outline); svg.appendChild(ring);
  const place = (node, ri, ci) => {
    node.setAttribute("x", m.l + ci * cw); node.setAttribute("y", m.t + ri * cellH);
    node.setAttribute("width", cw); node.setAttribute("height", cellH);
    node.setAttribute("visibility", "visible");
  };
  const tipHtml = (ri, ci) => o.tip ? o.tip(rows[ri], cols[ci], vals[ri][ci]) : null;
  const cellOf = t => (t && t.classList && t.classList.contains("hm-cell"))
    ? [+t.getAttribute("data-r"), +t.getAttribute("data-c")] : null;

  svg.addEventListener("pointermove", e => {
    const rc = cellOf(e.target);
    if (!rc) { outline.setAttribute("visibility", "hidden"); hideTip(); return; }
    place(outline, rc[0], rc[1]);
    const html = tipHtml(rc[0], rc[1]);
    if (html != null) showTip(e, html);
  });
  svg.addEventListener("pointerleave", () => { outline.setAttribute("visibility", "hidden"); hideTip(); });
  if (!o.onClick) return svg;

  svg.style.cursor = "default";
  cells.flat().forEach(c => { c.style.cursor = "pointer"; });
  // Roving tab stop: start on the first cell that has data.
  let fr = 0, fc = 0;
  outer: for (let r = 0; r < rows.length; r++)
    for (let c = 0; c < cols.length; c++) if (vals[r][c] != null) { fr = r; fc = c; break outer; }
  cells[fr][fc].setAttribute("tabindex", "0");

  const moveTo = (ri, ci) => {
    ri = Math.max(0, Math.min(rows.length - 1, ri));
    ci = Math.max(0, Math.min(cols.length - 1, ci));
    cells[fr][fc].setAttribute("tabindex", "-1");
    fr = ri; fc = ci;
    const c = cells[ri][ci];
    c.setAttribute("tabindex", "0");
    c.focus();
  };
  svg.addEventListener("click", e => {
    const rc = cellOf(e.target);
    if (!rc) return;
    cells[fr][fc].setAttribute("tabindex", "-1");
    [fr, fc] = rc;
    cells[fr][fc].setAttribute("tabindex", "0");
    o.onClick(rows[fr], cols[fc], vals[fr][fc]);
  });
  svg.addEventListener("focusin", e => {
    const rc = cellOf(e.target);
    if (!rc || !e.target.matches(":focus-visible")) return;
    place(ring, rc[0], rc[1]);
    const html = tipHtml(rc[0], rc[1]);
    if (html != null) tipAt(e.target, html);
  });
  svg.addEventListener("focusout", () => { ring.setAttribute("visibility", "hidden"); hideTip(); });
  svg.addEventListener("keydown", e => {
    const rc = cellOf(e.target);
    if (!rc) return;
    const [ri, ci] = rc;
    const moves = {
      ArrowUp: [ri - 1, ci], ArrowDown: [ri + 1, ci], ArrowLeft: [ri, ci - 1], ArrowRight: [ri, ci + 1],
      Home: e.ctrlKey ? [0, 0] : [ri, 0],
      End: e.ctrlKey ? [rows.length - 1, cols.length - 1] : [ri, cols.length - 1],
      PageUp: [ri - 7, ci], PageDown: [ri + 7, ci],
    };
    if (moves[e.key]) { e.preventDefault(); moveTo(...moves[e.key]); return; }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!e.repeat) o.onClick(rows[ri], cols[ci], vals[ri][ci]);
    }
  });
  return svg;
}

/* ------------------------------------------------------------- sparkline */
/* A trend the size of a word, for table cells and KPI tiles. No axes, no
   tooltip; null values break the line instead of dropping it to zero. The
   stroke keeps its width however far the cell stretches the drawing. */
function sparkline(host, values, o = {}) {
  host.innerHTML = "";
  const vs = (values || []).map(v => v == null || Number.isNaN(+v) ? null : +v);
  const H = o.height || 36, W = 100, pad = 2;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", class: "spark",
                          style: `display:block;width:100%;height:${H}px;overflow:visible`,
                          "aria-hidden": "true", focusable: "false" });
  host.appendChild(svg);
  const nums = vs.filter(v => v != null);
  if (!nums.length) return svg;
  const max = o.max != null ? o.max : Math.max(...nums) || 1;
  const col = o.color || cssv("--series-1");
  const X = i => vs.length === 1 ? W / 2 : (i / (vs.length - 1)) * W;
  const Y = v => pad + (H - pad * 2) * (1 - Math.min(v, max) / max);

  // contiguous runs of non-null values
  const runs = [];
  let cur = [];
  vs.forEach((v, i) => {
    if (v == null) { if (cur.length) runs.push(cur); cur = []; }
    else cur.push([X(i), Y(v)]);
  });
  if (cur.length) runs.push(cur);

  runs.forEach(run => {
    const d = run.map((p, i) => (i ? "L" : "M") + p[0].toFixed(2) + " " + p[1].toFixed(2)).join(" ");
    if (o.area && run.length > 1)
      svg.appendChild(el("path", { d: `${d} L${run.at(-1)[0].toFixed(2)} ${H} L${run[0][0].toFixed(2)} ${H} Z`,
                                   fill: col, opacity: .14, stroke: "none" }));
    if (run.length > 1)
      svg.appendChild(el("path", { d, fill: "none", stroke: col, "stroke-width": 1.5,
                                   "vector-effect": "non-scaling-stroke",
                                   "stroke-linejoin": "round", "stroke-linecap": "round" }));
    else
      svg.appendChild(el("line", { x1: run[0][0], x2: run[0][0], y1: run[0][1], y2: run[0][1] + 0.01,
                                   stroke: col, "stroke-width": 3, "stroke-linecap": "round",
                                   "vector-effect": "non-scaling-stroke" }));
  });
  return svg;
}
