// Shared helpers for the operational views (both layouts). Views only arrange canonical API facts:
// no scheduling, qualification, grading or assignment happens in the browser.
import { api, ApiFailure, bindings, envelopeGate, fmtDate, fmtDateTime, fmtMinutes, fmtTime, h, idempotencyKey, mount, pill, stateCard, viewTitle } from "../core.js";

export function hashParams() {
  return new URLSearchParams((location.hash.split("?")[1]) || "");
}

export function setHashParams(viewId, params) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ""));
  const qs = new URLSearchParams(clean).toString();
  location.hash = viewId + (qs ? "?" + qs : "");
}

export function query(params) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ""));
  const qs = new URLSearchParams(clean).toString();
  return qs ? "?" + qs : "";
}

// Labeled <select> whose change rewrites the view hash (so filters are shareable and the back button works).
export function selectControl(id, label, options, value, onChange) {
  const select = h("select", { id, name: id }, options.map((o) => {
    const opt = typeof o === "object" ? o : { value: String(o), label: String(o) };
    return h("option", { value: opt.value, selected: String(opt.value) === String(value) }, opt.label);
  }));
  select.addEventListener("change", () => onChange(select.value));
  return h("label", { for: id }, label, select);
}

export function dateControl(id, label, value, onChange) {
  const input = h("input", { id, name: id, type: "date", value: value || "" });
  input.addEventListener("change", () => onChange(input.value));
  return h("label", { for: id }, label, input);
}

export function toolbar(...controls) {
  return h("div", { class: "toolbar", role: "group", "aria-label": "Filters" }, controls.filter(Boolean));
}

export function teamOptions(context) {
  const teams = (context && context.choices && context.choices.teams) || [];
  return teams.map((t) => ({ value: t, label: t }));
}

export function defaultTeam(context, requested) {
  const teams = (context && context.choices && context.choices.teams) || [];
  if (requested && teams.includes(requested)) return requested;
  return teams[0] || null;
}

export function defaultDate(context, requested) {
  if (requested) return requested;
  return (context && context.choices && context.choices.default_date) || null;
}

// Load an envelope and render the title, bindings and any non-ready state; returns null when the view should stop.
export async function frame(host, title, subtitle, path, controls = null, emptyMessage = "No records in your scope.") {
  const env = await api(path);
  mount(host, viewTitle(title, subtitle), controls, bindings(env));
  if (envelopeGate(host, env, emptyMessage)) return null;
  return env;
}

export function card(title, ...children) {
  return h("section", { class: "card" }, title ? h("h2", {}, title) : null, ...children);
}

export function crewList(crew) {
  if (!crew || crew.length === 0) return h("span", { class: "muted" }, "no crew");
  return h("ul", { class: "crew-list" }, crew.map((c) => h("li", {},
    c.redacted ? h("span", { class: "muted" }, "outside your scope") : (c.name || c.person_id || "unknown"),
    c.role ? h("span", { class: "muted" }, ` · ${c.role}`) : null,
    c.synthetic ? [" ", pill("virtual", "warn")] : null)));
}

export function reasonList(reasons) {
  if (!reasons || reasons.length === 0) return h("span", { class: "muted" }, "none");
  return h("ul", { class: "reason-list compact" }, reasons.map((r) => {
    const code = typeof r === "string" ? r.split(":")[0] : r.code;
    const detail = typeof r === "string" ? r.split(":").slice(1).join(":") : (r.detail || r.message || "");
    return h("li", {}, h("code", {}, code), detail ? " " + detail : "");
  }));
}

export function span(start, end) {
  return `${fmtDateTime(start)} – ${fmtTime(end)}`;
}

export { fmtDate, fmtDateTime, fmtMinutes, fmtTime };

// ---------------------------------------------------------------- commands with honest worker status
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function runCard(run, note) {
  const kind = { queued: "unknown", running: "unknown", completed: "ready", failed: "error", cancelled: "blocked" }[run.status] || "unknown";
  const body = [h("div", { class: "state-title" }, `Run ${run.status}`), h("p", {}, `${run.kind} run ${run.run_id}${run.replayed ? " (replayed)" : ""}`)];
  if (note) body.push(h("p", { class: "muted" }, note));
  if (run.status === "failed" && run.error) body.push(h("p", {}, h("code", {}, run.error.code), " ", run.error.message || ""));
  if (run.status === "completed" && run.result_receipt) {
    const receipt = run.result_receipt;
    body.push(h("dl", { class: "kv" }, Object.entries(receipt).filter(([, v]) => typeof v !== "object" || v === null).slice(0, 10)
      .map(([k, v]) => [h("dt", {}, k), h("dd", {}, v === null ? "—" : String(v))])));
  }
  return h("section", { class: `card state-${kind} run-card`, role: "status", "aria-live": "polite", "data-run-status": run.status }, body);
}

// POST a command (idempotency key added), then poll /runs/{id} until a terminal status. Never claims success while queued.
export async function submitCommand(path, body, statusHost, { prefix = "ui", onDone = null } = {}) {
  const payload = Object.assign({ idempotency_key: idempotencyKey(prefix) }, body);
  let env;
  try {
    env = await api(path, { method: "POST", body: payload });
  } catch (err) {
    mount(statusHost, stateCard("error", err.message || String(err), [{ code: err.code || "ERROR", message: err.requestId ? `request ${err.requestId}` : "" }]));
    return null;
  }
  let run = env.data;
  mount(statusHost, runCard(run, "Queued for the background worker; a queued run is not a result."));
  const started = Date.now();
  while (!TERMINAL.has(run.status) && Date.now() - started < 120000) {
    await new Promise((resolve) => setTimeout(resolve, 800));
    try {
      run = (await api(`/api/v1/runs/${encodeURIComponent(run.run_id)}`)).data;
    } catch (err) {
      if (!(err instanceof ApiFailure)) throw err;
      break;
    }
    mount(statusHost, runCard(run, TERMINAL.has(run.status) ? null : "Still running…"));
  }
  if (onDone && run.status === "completed") onDone(run);
  return run;
}

// ---------------------------------------------------------------- export
export function csvButton(label, filename, columns, rows) {
  const button = h("button", { class: "btn btn-quiet", type: "button" }, label);
  button.addEventListener("click", () => {
    const escape = (v) => {
      const text = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [columns.map((c) => escape(c.label)).join(",")].concat(rows.map((r) => columns.map((c) => escape(c.value(r))).join(",")));
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
    const link = h("a", { href: URL.createObjectURL(blob), download: filename });
    document.body.appendChild(link);
    link.click();
    link.remove();
  });
  return button;
}

// ---------------------------------------------------------------- simple accessible charts
export function bars(series, { label, value, max = null, unit = "" }) {
  if (!series || series.length === 0) return stateCard("empty", "No series to chart.");
  const top = max || Math.max(1, ...series.map(value));
  return h("div", { class: "hbars", role: "list", "aria-label": label }, series.map((item) => {
    const v = value(item);
    const pct = Math.max(0, Math.min(100, (100 * v) / top));
    return h("div", { class: "hbar-row", role: "listitem" },
      h("span", { class: "hbar-label" }, label === null ? "" : item.__label || ""),
      h("span", { class: "hbar-track" }, h("span", { class: "hbar-fill", style: `width:${pct.toFixed(1)}%` })),
      h("span", { class: "hbar-value" }, `${v}${unit}`));
  }));
}

// Horizontal time bars (Gantt): rows of {label, bars:[{start,end,kind,text,ot}]} over [from, to].
export function timeline(rows, from, to, { shifts = [] } = {}) {
  const t0 = new Date(from).getTime();
  const t1 = new Date(to).getTime();
  const pos = (iso) => Math.max(0, Math.min(100, (100 * (new Date(iso).getTime() - t0)) / Math.max(1, t1 - t0)));
  const ticks = [];
  for (let t = t0; t <= t1; t += 3 * 3600 * 1000) ticks.push(t);
  const scale = h("div", { class: "gantt-scale", "aria-hidden": "true" }, h("div", {}),
    h("div", { class: "gantt-ticks" }, ticks.map((t) => h("span", { class: "gantt-tick", style: `left:${pos(new Date(t).toISOString())}%` }, fmtTime(new Date(t).toISOString())))));
  const body = rows.map((row) => h("div", { class: "gantt-row" },
    h("div", { class: "gantt-label", title: row.label }, row.label),
    h("div", { class: "gantt-track" },
      shifts.map((s) => h("span", { class: "gantt-shift", style: `left:${pos(s.start)}%`, title: `S${s.shift} start` })),
      row.bars.map((b) => {
        const left = pos(b.start);
        const width = Math.max(0.6, pos(b.end) - left);
        const kind = String(b.kind || "work").toLowerCase();
        return h("span", { class: `gantt-bar bar-${kind}${b.ot ? " bar-ot" : ""}`, style: `left:${left}%;width:${width}%`,
          title: `${b.text || b.kind} ${fmtTime(b.start)}–${fmtTime(b.end)}${b.ot ? " (OT)" : ""}` }, b.text || b.kind);
      }))));
  return h("div", { class: "gantt", role: "img", "aria-label": "Timeline" }, h("div", { class: "gantt-inner" }, scale, body));
}

export function legend(items) {
  return h("div", { class: "legend" }, items.map(([kind, text]) => h("span", {}, h("span", { class: `swatch bar-${kind}` }), text)));
}
