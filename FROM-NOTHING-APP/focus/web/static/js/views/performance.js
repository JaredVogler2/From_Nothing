// Shift Performance (S15 view 8) and the shared performance partial reused by the team, factory, Arena and
// history panels in both layouts. Every score, grade and component is the persisted canonical evaluation
// returned by GET /api/v1/performance; this module only formats and arranges it (no formula, no grading).
import { api, bindings, envelopeGate, fmtDateTime, fmtRational, h, metric, mount, pill, stateCard, table, viewTitle } from "../core.js";
import { register } from "./registry.js";

const GRADE_KIND = { A: "ok", B: "ok", C: "info", D: "warn", F: "err" };
const STATUS_KIND = { official: "ok", demonstration: "info", neutral: "muted", unknown: "warn" };
const STATUS_NOTE = {
  official: "official policy",
  demonstration: "demonstration policy: not an official grade",
  neutral: "neutral: no obligation left to grade",
  unknown: "unknown: evidence or policy incomplete",
};

function statusKey(status) {
  return String(status || "unknown").toLowerCase();
}

export function statusPill(status) {
  return pill(statusKey(status), STATUS_KIND[statusKey(status)] || "muted");
}

export function gradePill(grade) {
  return grade ? pill(`grade ${grade}`, GRADE_KIND[grade] || "muted") : pill("no grade", "muted");
}

// The canonical score block for one evaluation summary (goal, pooled team/factory, or a history revision).
export function scoreSummary(summary, { title = null, testid = null } = {}) {
  if (!summary) return stateCard("unknown", "No canonical evaluation has been recorded yet.", []);
  const score = summary.score === null ? "—" : summary.score;
  return h("div", { class: "score-summary", "data-testid": testid, "data-evaluation-id": summary.evaluation_id },
    title ? h("h3", {}, title) : null,
    h("div", { class: "metric-grid" },
      metric("Score", score, STATUS_NOTE[statusKey(summary.status)] || summary.status, "metric-score"),
      metric("Grade", summary.grade || "—", summary.grade ? "bands apply to the displayed score" : "no grade without a score"),
      metric("Base", summary.base === null ? "—" : fmtRational(summary.base), "before the OOS penalty"),
      metric("Attainment A", summary.attainment === null ? "—" : fmtRational(summary.attainment, 3)),
      metric("Adherence H", summary.adherence === null ? "—" : fmtRational(summary.adherence, 3))),
    h("p", { class: "inline-pills" }, statusPill(summary.status), " ", gradePill(summary.grade), " ",
      summary.revision ? pill(`revision ${summary.revision}`, "muted") : pill("not stored", "warn"), " ",
      pill(`policy ${String(summary.policy_status || "").toLowerCase() || "unknown"}`, "muted")));
}

export function componentsTable(summary) {
  const rows = [
    ["B0", "frozen obligation at publication", summary.B0],
    ["B", "B0 plus approved feasible additions", summary.B],
    ["E", "approved relief (union, capped at missed effort)", summary.E],
    ["D", "D = B - E", summary.D],
    ["Cp", "verified planned completion", summary.Cp],
    ["U", "unapproved offplan output", summary.U],
    ["O", "once-only OOS penalty", summary.O],
  ].map(([k, meaning, v]) => ({ k, meaning, v }));
  return table([
    { key: "k", label: "Component" },
    { key: "meaning", label: "Meaning" },
    { key: "v", label: "Person-minutes", num: true, render: (r) => (r.v === null ? "unknown" : fmtRational(r.v)) },
  ], rows, { caption: "Canonical components", className: "components-table" });
}

function reasonsList(reasons) {
  if (!reasons || reasons.length === 0) return null;
  return h("ul", { class: "reason-list" }, reasons.map((r) => h("li", {}, h("code", {}, String(r).split(":")[0]), " ", String(r).split(":").slice(1).join(":"))));
}

function unitStatus(u) {
  const kind = { EARNED: "ok", MISSED: "warn", UNKNOWN: "err", PENDING: "info" }[u.status] || "muted";
  return [pill(u.status, kind), u.relief ? [" ", pill("relieved", "info")] : null, u.oos_charged ? [" ", pill("OOS", "err")] : null];
}

function goalCard(goal) {
  const e = goal.evaluation;
  const card = h("section", { class: "card goal-card", "data-team": goal.team, "data-testid": `goal-${goal.team}` },
    h("div", { class: "card-header" },
      h("h2", {}, `${goal.team} · ${goal.shift_id}`),
      h("span", { class: "muted" }, `goal ${goal.goal_id} · window ${fmtDateTime(goal.window && goal.window.start)} to ${fmtDateTime(goal.window && goal.window.end)}`)));
  if (!e) {
    card.appendChild(stateCard("unknown", "This goal has no canonical evaluation yet.", [
      { code: "NOT_EVALUATED", message: "Observations have not been collected and evaluated for this shift.", next_action: "Collect observations, then evaluate the shift." }]));
    return card;
  }
  card.appendChild(scoreSummary(e, { testid: `score-${goal.team}` }));
  card.appendChild(componentsTable(e));
  const why = reasonsList(e.reasons);
  if (why) card.appendChild(h("div", {}, h("h3", {}, "Why"), why));
  if (goal.units && goal.units.length) {
    card.appendChild(h("h3", {}, "Frozen earning units"));
    card.appendChild(table([
      { key: "label", label: "Work" },
      { key: "origin", label: "Origin" },
      { key: "effort", label: "Effort (min)", num: true },
      { key: "status", label: "Status", render: unitStatus },
      { key: "completed_at", label: "Verified at", render: (u) => fmtDateTime(u.completed_at) },
    ], goal.units, { caption: `Units of ${goal.team}` }));
  }
  if (goal.offplan && goal.offplan.length) {
    card.appendChild(h("h3", {}, "Offplan output (U)"));
    card.appendChild(table([
      { key: "label", label: "Work" },
      { key: "effort", label: "Effort (min)", num: true, render: (o) => (o.effort === null ? "unknown" : o.effort) },
      { key: "completed_at", label: "Verified at", render: (o) => fmtDateTime(o.completed_at) },
    ], goal.offplan, { caption: "Offplan output" }));
  }
  if (goal.oos && goal.oos.length) {
    card.appendChild(h("h3", {}, "Sequence violations (OOS)"));
    card.appendChild(table([
      { key: "label", label: "Work" },
      { key: "affected_effort", label: "Affected (min)", num: true },
      { key: "charged", label: "Charged in O", render: (o) => (o.charged ? pill("charged once", "err") : pill("evidence only", "muted")) },
      { key: "reason", label: "Classification" },
    ], goal.oos, { caption: "Sequence violations" }));
  }
  return card;
}

function explanation() {
  return h("details", { class: "card explain" },
    h("summary", {}, "How the canonical score is formed"),
    h("p", {}, "The server computes one evaluation per frozen team-shift goal on exact rationals and stores it. D is the obligation B minus approved relief E. ",
      "Attainment A is verified planned completion over D, capped at one; adherence H is planned completion over all verified output. ",
      "The base blends A and H with the policy weights; the once-only OOS penalty O is charged against D; the result is clamped, rounded once and banded."),
    h("p", { class: "muted" }, "Pooled team and factory views sum the exact components before dividing, so they are never an average of percentages."));
}

export async function renderPerformance(host, context, { team = null } = {}) {
  const params = new URLSearchParams();
  const hash = new URLSearchParams((location.hash.split("?")[1]) || "");
  if (hash.get("shift_id")) params.set("shift_id", hash.get("shift_id"));
  if (team) params.set("team", team);
  const env = await api("/api/v1/performance" + (params.toString() ? `?${params}` : ""));
  const shifts = Array.from(new Set((env.data ? env.data.goals : []).map((g) => g.shift_id))).sort().reverse();
  let selected = hash.get("shift_id") || shifts.find((s) => (env.data.goals || []).some((g) => g.shift_id === s && g.evaluation)) || shifts[0] || null;
  let shown = env;
  if (selected && !hash.get("shift_id")) {
    shown = await api(`/api/v1/performance?shift_id=${encodeURIComponent(selected)}` + (team ? `&team=${encodeURIComponent(team)}` : ""));
  }
  const picker = h("select", { id: "perf-shift", "aria-label": "Shift" }, shifts.map((s) => h("option", { value: s, selected: s === selected }, s)));
  picker.addEventListener("change", () => { location.hash = `performance?shift_id=${encodeURIComponent(picker.value)}`; });
  mount(host,
    viewTitle("Shift Performance", "One canonical frozen-unit evaluation per goal of record; the same stored record on every surface."),
    shifts.length ? h("div", { class: "toolbar" }, h("label", { for: "perf-shift" }, "Shift", picker)) : null,
    bindings(shown));
  if (envelopeGate(host, shown, "No frozen goal of record in your scope yet.")) {
    host.appendChild(explanation());
    return;
  }
  const data = shown.data;
  if (data.pooled && data.goals.length > 1) {
    const pooledTitle = data.goals.length > 1 ? (context && context.scope && context.scope.all_teams ? "Factory (pooled)" : "Your scope (pooled)") : "Pooled";
    host.appendChild(h("section", { class: "card pooled-card", "data-testid": "pooled" },
      h("div", { class: "card-header" }, h("h2", {}, pooledTitle),
        h("span", { class: "muted" }, data.pooled.persisted ? "stored canonical aggregate" : "computed from stored member revisions; not stored")),
      scoreSummary(data.pooled, { testid: "pooled-score" }),
      componentsTable(data.pooled)));
  }
  for (const goal of data.goals) host.appendChild(goalCard(goal));
  host.appendChild(explanation());
}

register("performance", (host, context) => renderPerformance(host, context));
