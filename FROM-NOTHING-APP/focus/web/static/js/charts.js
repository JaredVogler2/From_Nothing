// Small SVG charts for the simulation dashboard, built with DOM calls (no innerHTML, no inline style attributes).
// Every chart has a title, labelled axes and a text summary for screen readers.

const NS = "http://www.w3.org/2000/svg";
export const PALETTE = ["#1764df", "#d9480f", "#2b8a3e", "#ae3ec9", "#e8590c", "#1098ad", "#5c940d", "#c2255c", "#3b5bdb", "#f08c00",
  "#0b7285", "#862e9c"];

function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    el.setAttribute(k, String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function niceMax(v) {
  if (!v || v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function fmtShort(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "k";
  if (a < 1 && a > 0) return v.toFixed(2);
  return Math.round(v * 10) / 10 + "";
}

function frame(title, width, height) {
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", class: "chart", "aria-label": title });
  root.appendChild(svg("title", {}, title));
  return root;
}

function yAxis(root, max, x0, y0, plotH, ticks = 4, fmt = fmtShort) {
  for (let i = 0; i <= ticks; i++) {
    const v = (max * i) / ticks;
    const y = y0 - (plotH * i) / ticks;
    root.appendChild(svg("line", { x1: x0, x2: "100%", y1: y, y2: y, class: "grid" }));
    root.appendChild(svg("text", { x: x0 - 6, y: y + 4, class: "tick", "text-anchor": "end" }, fmt(v)));
  }
}

// bars: [{label, value, color?, note?}]
export function barChart(bars, { title = "", width = 720, height = 260, yLabel = "", fmt = fmtShort, rotate = false } = {}) {
  const root = frame(title, width, height);
  const left = 56, bottom = rotate ? 70 : 34, top = 16;
  const plotH = height - bottom - top, plotW = width - left - 10;
  const max = niceMax(Math.max(0, ...bars.map((b) => b.value || 0)));
  yAxis(root, max, left, height - bottom, plotH, 4, fmt);
  const bw = plotW / Math.max(1, bars.length);
  const chars = Math.max(1, ...bars.map((b) => String(b.label).length));
  const every = Math.max(1, Math.ceil((rotate ? 13 : chars * 6.5 + 6) / bw));    // label every bar that has room
  bars.forEach((b, i) => {
    const hgt = ((b.value || 0) / max) * plotH;
    const x = left + i * bw + bw * 0.12;
    const rect = svg("rect", { x, y: height - bottom - hgt, width: Math.max(1, bw * 0.76), height: Math.max(0, hgt), fill: b.color || PALETTE[0], class: "chart-bar" },
      svg("title", {}, `${b.label}: ${fmt(b.value)}${b.note ? " — " + b.note : ""}`));
    root.appendChild(rect);
    if (i % every) return;
    const tx = x + bw * 0.38;
    const ty = height - bottom + 14;
    root.appendChild(svg("text", { x: tx, y: ty, class: "tick", "text-anchor": rotate ? "end" : "middle",
      transform: rotate ? `rotate(-50 ${tx} ${ty})` : null }, b.label));
  });
  if (yLabel) root.appendChild(svg("text", { x: 12, y: top + plotH / 2, class: "axis-label", transform: `rotate(-90 12 ${top + plotH / 2})`, "text-anchor": "middle" }, yLabel));
  return root;
}

// series: [{name, color, points: [{x(label), y}]}], all series share x labels
export function lineChart(series, { title = "", width = 720, height = 260, yLabel = "", fmt = fmtShort, yMax = null } = {}) {
  const root = frame(title, width, height);
  const left = 56, bottom = 46, top = 16;
  const plotH = height - bottom - top, plotW = width - left - 16;
  const labels = series.length ? series[0].points.map((p) => p.x) : [];
  const max = yMax || niceMax(Math.max(0, ...series.flatMap((s) => s.points.map((p) => p.y || 0))));
  yAxis(root, max, left, height - bottom, plotH, 4, fmt);
  const step = labels.length > 1 ? plotW / (labels.length - 1) : plotW;
  const every = Math.max(1, Math.ceil(labels.length / 14));
  labels.forEach((lab, i) => {
    if (i % every) return;
    const x = left + i * step;
    root.appendChild(svg("text", { x, y: height - bottom + 14, class: "tick", "text-anchor": "end", transform: `rotate(-40 ${x} ${height - bottom + 14})` }, lab));
  });
  series.forEach((s, si) => {
    const color = s.color || PALETTE[si % PALETTE.length];
    const pts = s.points.map((p, i) => (p.y === null || p.y === undefined ? null : [left + i * step, height - bottom - (p.y / max) * plotH]));
    let d = "";
    pts.forEach((pt) => { if (pt) d += (d ? " L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1); });
    root.appendChild(svg("path", { d, stroke: color, fill: "none", class: "line" }));
    pts.forEach((pt, i) => {
      if (!pt) return;
      root.appendChild(svg("circle", { cx: pt[0], cy: pt[1], r: 3, fill: color }, svg("title", {}, `${s.name} ${labels[i]}: ${fmt(s.points[i].y)}`)));
    });
  });
  const legend = svg("g", { class: "legend" });
  series.forEach((s, si) => {
    const x = left + si * 150;
    legend.appendChild(svg("rect", { x, y: 2, width: 10, height: 10, fill: s.color || PALETTE[si % PALETTE.length] }));
    legend.appendChild(svg("text", { x: x + 14, y: 11, class: "tick" }, s.name));
  });
  root.appendChild(legend);
  if (yLabel) root.appendChild(svg("text", { x: 12, y: top + plotH / 2, class: "axis-label", transform: `rotate(-90 12 ${top + plotH / 2})`, "text-anchor": "middle" }, yLabel));
  return root;
}

// stacks: [{label, parts: [{name, value, color}]}]
export function stackedBars(stacks, { title = "", width = 720, height = 260, fmt = fmtShort, names = [] } = {}) {
  const root = frame(title, width, height);
  // the legend wraps into rows of slots as wide as its longest name; the plot starts below it
  const slot = Math.min(220, 24 + 6.6 * Math.max(0, ...names.map((n) => String(n.name).length)));
  const perRow = Math.max(1, Math.floor((width - 56) / slot));
  const left = 56, bottom = 46, top = 22 + Math.max(0, Math.ceil(names.length / perRow) - 1) * 16;
  const plotH = height - bottom - top, plotW = width - left - 10;
  const max = niceMax(Math.max(0, ...stacks.map((s) => s.parts.reduce((a, p) => a + (p.value || 0), 0))));
  yAxis(root, max, left, height - bottom, plotH, 4, fmt);
  const bw = plotW / Math.max(1, stacks.length);
  const every = Math.max(1, Math.ceil(stacks.length / 14));
  stacks.forEach((s, i) => {
    let y = height - bottom;
    const x = left + i * bw + bw * 0.12;
    s.parts.forEach((p) => {
      const hgt = ((p.value || 0) / max) * plotH;
      y -= hgt;
      root.appendChild(svg("rect", { x, y, width: Math.max(1, bw * 0.76), height: Math.max(0, hgt), fill: p.color }, svg("title", {}, `${s.label} ${p.name}: ${fmt(p.value)}`)));
    });
    if (!(i % every)) {
      const tx = x + bw * 0.38, ty = height - bottom + 14;
      root.appendChild(svg("text", { x: tx, y: ty, class: "tick", "text-anchor": "end", transform: `rotate(-40 ${tx} ${ty})` }, s.label));
    }
  });
  names.forEach((n, i) => {
    const x = left + (i % perRow) * slot, y = 4 + Math.floor(i / perRow) * 16;
    root.appendChild(svg("rect", { x, y, width: 10, height: 10, fill: n.color }));
    root.appendChild(svg("text", { x: x + 14, y: y + 9, class: "tick" }, n.name));
  });
  return root;
}

// The server decides each cell's band (``/api/v1/sim/capacity``); the chart only colors it.
export const BAND_COLORS = { over: "#c92a2a", high: "#f08c00", busy: "#2b8a3e", light: "#8ce99a", idle: "#d3f9d8", none: "#eef2f8" };

// cells: [{row, col, value, band, label}] with value the load; rows/cols ordered lists. With ``width`` the columns
// share it (cells wider than tall); otherwise cells are ``cell`` square.
export function heatmap(rows, cols, cells, { title = "", cell = 22, width = null, fmt = (v) => Math.round(v * 100) + "%" } = {}) {
  const left = 52;
  const cw = width ? Math.max(cell, Math.min(140, Math.floor((width - left - 10) / Math.max(1, cols.length)))) : cell;
  const upright = cw >= 64;                                                      // room for a level column label
  const top = upright ? 26 : 56;
  const w = left + cols.length * cw + 10, height = top + rows.length * cell + 10;
  const root = frame(title, w, height);
  const at = new Map(cells.map((c) => [c.row + "|" + c.col, c]));
  cols.forEach((c, j) => {
    const x = left + j * cw + cw / 2;
    root.appendChild(upright ? svg("text", { x, y: top - 8, class: "tick", "text-anchor": "middle" }, c)
      : svg("text", { x, y: top - 6, class: "tick", "text-anchor": "start", transform: `rotate(-60 ${x} ${top - 6})` }, c));
  });
  rows.forEach((r, i) => {
    root.appendChild(svg("text", { x: left - 6, y: top + i * cell + cell * 0.68, class: "tick", "text-anchor": "end" }, r));
    cols.forEach((c, j) => {
      const found = at.get(r + "|" + c);
      const v = found ? found.value : null;
      const fill = BAND_COLORS[found ? found.band : "none"] || BAND_COLORS.none;
      root.appendChild(svg("rect", { x: left + j * cw + 1, y: top + i * cell + 1, width: cw - 2, height: cell - 2, rx: 3, fill },
        svg("title", {}, `${r} ${c}: ${v === null || v === undefined ? "no pool" : fmt(v)}${found && found.label ? " — " + found.label : ""}`)));
    });
  });
  return root;
}

// lanes: [{label, items: [{start, end, kind, note}]}] with start/end in minutes from t0
export function gantt(lanes, { title = "", t0 = 0, t1 = 480, width = 900, lane = 18, tickEvery = 60, tickLabel = (m) => m } = {}) {
  const left = 150, top = 24, right = 26;
  const height = top + lanes.length * lane + 10;
  const root = frame(title, width, height);
  const scale = (width - left - right) / Math.max(1, t1 - t0);
  for (let m = 0; m <= t1 - t0; m += tickEvery) {
    const x = left + m * scale;
    root.appendChild(svg("line", { x1: x, x2: x, y1: top - 4, y2: height - 6, class: "grid" }));
    root.appendChild(svg("text", { x, y: top - 8, class: "tick", "text-anchor": "middle" }, tickLabel(t0 + m)));
  }
  const color = { WORK: "#1764df", TRAVEL: "#f08c00", WAIT: "#868e96", TOOL: "#ae3ec9", OTHER: "#495057" };
  lanes.forEach((ln, i) => {
    const y = top + i * lane;
    root.appendChild(svg("text", { x: left - 6, y: y + lane * 0.68, class: "tick", "text-anchor": "end" }, ln.label));
    ln.items.forEach((it) => {
      const x = left + Math.max(0, it.start - t0) * scale;
      const w = Math.max(1.5, (Math.min(t1, it.end) - Math.max(t0, it.start)) * scale);
      root.appendChild(svg("rect", { x, y: y + 2, width: w, height: lane - 4, rx: 2, fill: color[it.kind] || "#1764df" }, svg("title", {}, it.note || it.kind)));
      if (it.label && w > 34) root.appendChild(svg("text", { x: x + w / 2, y: y + lane * 0.68, class: "bar-label", "text-anchor": "middle" }, it.label));
    });
  });
  return root;
}
