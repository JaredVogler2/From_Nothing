// FOCUS front-end core: prefix-safe API client, safe DOM builders and shared state rendering.
// Text is always inserted with createTextNode / textContent (never innerHTML with data).

const meta = (name) => {
  const el = document.querySelector(`meta[name="${name}"]`);
  return el ? el.getAttribute("content") || "" : "";
};

export const BASE = meta("focus-base").replace(/\/$/, "");
let csrfToken = meta("focus-csrf") || null;

export function url(path) {
  return BASE + path;
}

export function setCsrf(token) {
  if (token) csrfToken = token;
}

export class ApiFailure extends Error {
  constructor(message, status, code, requestId) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export async function api(path, { method = "GET", body = null, signal } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(url(path), {
    method,
    headers,
    body: body === null ? null : JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  let json = null;
  try {
    json = await response.json();
  } catch (_err) {
    json = null;
  }
  if (!response.ok) {
    const err = json && json.error ? json.error : {};
    throw new ApiFailure(err.message || response.statusText, response.status, err.code || "HTTP_" + response.status, err.request_id);
  }
  return json;
}

export function idempotencyKey(prefix = "ui") {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return prefix + "-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------- DOM builders
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(host, ...children) {
  clear(host);
  appendChildren(host, children);
  return host;
}

// ---------------------------------------------------------------- formatting
const TZ = "America/New_York";

export function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString("en-US", { timeZone: TZ, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
}

export function fmtDate(iso) {
  if (!iso) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(iso);
  return d.toLocaleDateString("en-CA", { timeZone: TZ });
}

export function fmtMinutes(min) {
  if (min === null || min === undefined) return "unknown";
  const m = Number(min);
  if (Math.abs(m) < 60) return `${m} min`;
  const hours = Math.floor(Math.abs(m) / 60);
  const rest = Math.abs(m) % 60;
  return `${m < 0 ? "-" : ""}${hours}h${rest ? " " + rest + "m" : ""}`;
}

// Exact rational cents {num, den} or integer cents -> dollars text. Null is "unknown", never $0.
export function fmtCents(value) {
  if (value === null || value === undefined) return "unknown";
  let cents;
  if (typeof value === "object" && "num" in value) cents = value.num / value.den;
  else cents = Number(value);
  return (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

// Exact rational text from the API ("1234" or "617/5") -> decimal text for display only. Null stays "unknown".
export function fmtRational(text, digits = 1) {
  if (text === null || text === undefined) return "unknown";
  const [num, den] = String(text).split("/");
  const value = Number(num) / (den === undefined ? 1 : Number(den));
  if (!Number.isFinite(value)) return String(text);
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined) return "unknown";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function pill(text, kind = "muted") {
  return h("span", { class: `pill pill-${kind}` }, text);
}

export function dispositionPill(disposition) {
  const kind = {
    scheduled: "ok",
    verified_complete: "ok",
    conditional: "info",
    deferred: "warn",
    blocked: "warn",
    quarantined: "err",
    excluded: "muted",
  }[disposition] || "muted";
  return pill(disposition, kind);
}

// ---------------------------------------------------------------- shared states
const STATE_TITLES = {
  loading: "Loading",
  empty: "Nothing to show",
  unknown: "Unknown",
  blocked: "Blocked",
  stale: "Stale",
  error: "Error",
};

export function stateCard(kind, message, reasons = []) {
  const card = h("section", { class: `card state-${kind}`, role: kind === "error" ? "alert" : "status" },
    h("div", { class: "state-title" }, STATE_TITLES[kind] || kind),
    h("p", {}, message));
  if (reasons && reasons.length) {
    card.appendChild(h("ul", { class: "reason-list" }, reasons.map((r) =>
      h("li", {}, h("code", {}, r.code || ""), " ", r.message || "", r.next_action ? h("div", { class: "muted" }, "Next: " + r.next_action) : null))));
  }
  return card;
}

export function loadingCard(lines = 3) {
  const card = h("section", { class: "card state-loading", "aria-busy": "true" }, h("div", { class: "state-title" }, "Loading"));
  for (let i = 0; i < lines; i += 1) card.appendChild(h("div", { class: "skeleton" }));
  return card;
}

export function errorCard(err) {
  return stateCard("error", `${err.message || err}`, [
    { code: err.code || "ERROR", message: err.requestId ? `request ${err.requestId}` : "", next_action: err.status === 401 ? "Sign in through the configured identity provider." : null },
  ]);
}

// Render an envelope's non-ready status honestly; returns true when the caller should stop.
export function envelopeGate(host, env, emptyMessage = "No records in your scope.") {
  if (!env) return true;
  if (env.status === "ready") return false;
  if (env.status === "empty") {
    host.appendChild(stateCard("empty", emptyMessage, env.reasons));
    return true;
  }
  host.appendChild(stateCard(env.status, describeStatus(env.status), env.reasons));
  return env.data === null || env.data === undefined;
}

export function describeStatus(status) {
  return {
    unknown: "The canonical facts needed for this view are not available yet.",
    blocked: "This view is blocked by a missing source, policy or authority.",
    stale: "The data is older than its freshness policy; it is shown for history only.",
    empty: "No records in your scope.",
  }[status] || status;
}

export function metric(label, value, note = null, extraClass = "") {
  return h("div", { class: `metric card ${extraClass}` },
    h("div", { class: "metric-label" }, label),
    h("div", { class: "metric-value" }, value === null || value === undefined ? "unknown" : String(value)),
    note ? h("div", { class: "metric-note" }, note) : null);
}

export function table(columns, rows, { caption = "", empty = "No rows.", className = "" } = {}) {
  if (!rows || rows.length === 0) return stateCard("empty", empty);
  const thead = h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col", class: c.num ? "num" : null }, c.label))));
  const tbody = h("tbody", {}, rows.map((row) => h("tr", {}, columns.map((c) => {
    const value = c.render ? c.render(row) : row[c.key];
    return h("td", { class: c.num ? "num" : null }, value === null || value === undefined ? "—" : value);
  }))));
  return h("div", { class: "table-wrap" }, h("table", { class: `data-table ${className}`.trim() }, caption ? h("caption", { class: "visually-hidden" }, caption) : null, thead, tbody));
}

// A labelled key/value list (definition list) for facts such as envelope bindings.
export function facts(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== undefined).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v === null ? "—" : v)]));
}

// The envelope's bindings: which source run, plan, policy and evaluation the view shows.
export function bindings(env) {
  const parts = [];
  if (env.source_run_id) parts.push(`source ${env.source_run_id}`);
  if (env.plan_id) parts.push(`plan ${env.plan_id}`);
  if (env.purpose) parts.push(env.purpose.toLowerCase());
  if (env.policy_id) parts.push(`policy ${env.policy_id}`);
  if (env.evaluation_id) parts.push(`evaluation ${env.evaluation_id}`);
  parts.push(`as of ${fmtDateTime(env.as_of_utc)}`);
  return h("p", { class: "muted bindings", "data-testid": "bindings" }, parts.join(" · "));
}

// Non-blocking banner for reasons that accompany usable data (stale source, withheld economics, ...).
export function reasonBanner(env) {
  if (!env.reasons || env.reasons.length === 0) return null;
  const kind = env.status === "stale" ? "stale" : "unknown";
  return stateCard(kind, env.status === "stale" ? describeStatus("stale") : "Some facts are qualified:", env.reasons);
}

export function viewTitle(title, subtitle) {
  return h("div", { class: "view-title" }, h("h1", {}, title), subtitle ? h("p", { class: "muted" }, subtitle) : null);
}

