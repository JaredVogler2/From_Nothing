// Performance Over Time (supplementary panel): the canonical score of a team, a superintendent or the business unit over
// any period, from GET /api/v1/performance/rollup. The server sums the seven components of every team-shift and applies the
// one formula; this module only arranges the result (no formula and no grading in the browser).
import { api, bindings, describeStatus, fmtRational, h, metric, mount, pill, stateCard, table, viewTitle } from "../core.js";
import { card, dateControl, hashParams, query, selectControl, setHashParams, toolbar } from "./common.js";
import { register } from "./registry.js";
import { componentsTable, gradePill, statusPill } from "./performance.js";

const VIEW = "rollups";
const LEVELS = [
  { value: "business_unit", label: "Business unit" },
  { value: "superintendent", label: "Superintendent" },
  { value: "team", label: "Team" },
];
const PERIODS = [
  { value: "shift", label: "Shift" }, { value: "day", label: "Day" }, { value: "week", label: "Week" },
  { value: "rolling30", label: "Rolling 30 days" }, { value: "month", label: "Month" }, { value: "quarter", label: "Quarter" },
  { value: "year", label: "Year" }, { value: "custom", label: "Custom dates" },
];
const BREAKDOWNS = [
  { value: "none", label: "No breakdown" }, { value: "shift", label: "By shift" }, { value: "day", label: "By day" },
  { value: "week", label: "By week" }, { value: "month", label: "By month" }, { value: "quarter", label: "By quarter" },
];

// The official score with its status, and the partial score when some team-shifts are not scored yet.
function resultCell(summary) {
  const o = summary.official;
  if (!o) return "—";
  return [o.score === null ? h("span", { class: "muted" }, "unknown") : o.score, " ", statusPill(o.status),
    summary.partial ? [" ", pill(`partial ${summary.partial.score ?? "—"}`, "warn")] : null];
}

function resultBlock(d) {
  const o = d.official;
  return card("Result",
    h("div", { class: "metric-grid" },
      metric("Score", o.score === null ? "—" : o.score, o.status === "unknown" ? "official score unknown" : `${o.status} policy`, "metric-score"),
      metric("Grade", o.grade || "—"),
      metric("Attainment A", o.attainment === null ? "—" : fmtRational(o.attainment, 3), "(planned work done + credited extra work) ÷ expected work"),
      metric("Adherence H", o.adherence === null ? "—" : fmtRational(o.adherence, 3), "planned work done ÷ expected work"),
      metric("Team-shifts", d.team_shifts, d.unscored ? `${d.unscored} not scored yet` : "all scored")),
    d.partial ? stateCard("unknown", `Partial score ${d.partial.score ?? "—"} (${d.partial.label}). Leaving out the missing shifts can `
      + "flatter a group, so this is not the official score.", []) : null,
    h("p", { class: "inline-pills" }, statusPill(o.status), " ", gradePill(o.grade), " ",
      pill(`policy ${String(o.policy_status || "").toLowerCase() || "unknown"}`, "muted")),
    componentsTable(o));
}

function spreadBlock(s) {
  if (!s) return null;
  return card("How the teams compare",
    h("p", { class: "muted" }, "The score above weights each team by its planned work. These show each team on its own."),
    h("div", { class: "metric-grid" },
      metric("On target", `${s.on_target} of ${s.scored}`, `score at or above ${s.target}`),
      metric("Lowest", s.lowest ? s.lowest.score : "—", s.lowest ? s.lowest.team : null),
      metric("Highest", s.highest ? s.highest.score : "—", s.highest ? s.highest.team : null),
      metric("Range", s.range ?? "—", s.not_scored ? `${s.not_scored} team(s) without a score` : "points from lowest to highest")),
    table([
      { key: "team", label: "Team" },
      { key: "score", label: "Score", num: true, render: (t) => (t.score === null ? "—" : t.score) },
      { key: "grade", label: "Grade", render: (t) => t.grade || "—" },
      { key: "planned", label: "Planned minutes", num: true, render: (t) => (t.planned === null ? "—" : fmtRational(t.planned, 0)) },
      { key: "team_shifts", label: "Team-shifts", num: true },
      { key: "unscored", label: "Not scored", num: true },
      { key: "partial_score", label: "Partial", num: true, render: (t) => t.partial_score ?? "—" },
    ], s.teams, { caption: "Teams" }));
}

function childrenBlock(d) {
  if (d.level === "superintendent" || !d.children.length) return null;     // a superintendent's teams are in the comparison
  const label = d.level === "business_unit" ? "Superintendents" : "Shifts";
  return card(label, table([
    { key: "node", label: d.level === "business_unit" ? "Superintendent" : "Shift" },
    { key: "teams", label: "Teams", render: (c) => c.teams.join(", ") },
    { key: "score", label: "Score", render: (c) => resultCell(c) },
    { key: "team_shifts", label: "Team-shifts", num: true },
  ], d.children, { caption: label }));
}

function seriesBlock(d) {
  if (!d.series.length) return null;
  const bar = (b) => {
    const value = b.official && b.official.score !== null ? Number(b.official.score) : null;
    return value === null ? "—" : h("span", { class: "rollup-track", "aria-hidden": "true" },
      h("span", { class: "rollup-bar", style: `width:${Math.max(2, Math.round(value))}%` }));
  };
  return card("Breakdown", table([
    { key: "bucket", label: "Period" },
    { key: "score", label: "Score", render: (b) => resultCell(b) },
    { key: "bar", label: "", render: bar },
    { key: "team_shifts", label: "Team-shifts", num: true },
    { key: "unscored", label: "Not scored", num: true },
  ], d.series, { caption: "Breakdown" }));
}

// The jobs behind the score: planned jobs done, missed, excused, open or out of sequence, and unplanned work done instead.
function jobsBlock(env, d) {
  if (!env || env.status !== "ready") return null;
  const j = env.data;
  const perAircraft = j.group === "job_aircraft";
  const jobName = (r) => [r.soi || r.job || "unknown job", r.name ? h("div", { class: "muted" }, r.name) : null];
  const n = (k) => (r) => (r.count[k] ? `${r.count[k]} · ${fmtRational(String(r.minutes[k]), 0)} min` : "—");
  const causes = (r) => Object.keys(r.causes).slice(0, 2).join(", ") || "—";
  const adherence = j.totals.adherence_from_jobs === null ? "—" : `${fmtRational(j.totals.adherence_from_jobs, 3)}`;
  return card(perAircraft ? "Jobs behind the score" : "Jobs behind the score, across aircraft",
    h("p", { class: "muted" }, perAircraft
      ? "Each planned job of the selection and what happened to it; unplanned work is extra work, credited below a planned job."
      : "The same job (shop order id) on every aircraft of the selection, worst first: missed, unplanned and out-of-sequence minutes."),
    h("div", { class: "metric-grid" },
      metric("Done as planned", j.totals.count.done, `${fmtRational(String(j.totals.minutes.done), 0)} min`),
      metric("Missed", j.totals.count.missed, `${fmtRational(String(j.totals.minutes.missed), 0)} min, plus ${j.totals.count.excused} excused`),
      metric("Unplanned", j.totals.count.unplanned, `${fmtRational(String(j.totals.minutes.unplanned), 0)} min`),
      metric("Adherence from jobs", adherence, j.totals.unplanned_minutes_unknown
        ? `${j.totals.unplanned_minutes_unknown} unplanned job(s) have no standard minutes, so the extra-work credit is unknown`
        : "planned done ÷ (planned done + missed)")),
    table([
      { key: "soi", label: "Job", render: jobName },
      { key: "kind", label: "Phase", render: (r) => (r.kind || "—").toLowerCase().replace("_", " ") },
      { key: "aircraft", label: perAircraft ? "Aircraft" : "Aircraft", num: true, render: (r) => (perAircraft ? r.line : r.aircraft) },
      { key: "done", label: "Done", render: n("done") },
      { key: "missed", label: "Missed", render: n("missed") },
      { key: "excused", label: "Excused", render: n("excused") },
      { key: "oos", label: "Out of sequence", render: n("out_of_sequence") },
      { key: "unplanned", label: "Unplanned", render: n("unplanned") },
      // work on a team-shift no plan covered: its score is neutral, so this work earns no extra-work credit
      j.totals.count.no_plan ? { key: "no_plan", label: "No plan", render: n("no_plan") } : null,
      { key: "causes", label: "Causes", render: causes },
    ].filter(Boolean), j.jobs, { caption: "Jobs", empty: "No job record for this selection." }),
    j.jobs_total > j.jobs.length ? h("p", { class: "muted" }, `Showing the worst ${j.jobs.length} of ${j.jobs_total} jobs.`) : null);
}

async function renderRollups(host, context) {
  const p = hashParams();
  const allTeams = !!(context && context.scope && context.scope.all_teams);
  const params = {
    level: p.get("level") || (allTeams ? "business_unit" : "team"),
    node: p.get("node") || "",
    period: p.get("period") || "week",
    date: p.get("date") || "",
    from: p.get("from") || "",
    to: p.get("to") || "",
    shift: p.get("shift") || "",
    by: p.get("by") || "day",
  };
  const request = { ...params };
  if (request.level === "business_unit") request.node = "";
  if (request.period !== "custom") { request.from = ""; request.to = ""; }
  if (request.period !== "shift") request.shift = "";
  const env = await api(`/api/v1/performance/rollup${query(request)}`);
  const d = env.data || {};
  const choices = d.choices || { teams: [], superintendents: [], business_unit: null };
  const update = (key) => (value) => setHashParams(VIEW, { ...params, [key]: value, ...(key === "level" ? { node: "" } : {}) });
  const nodes = params.level === "team" ? choices.teams : params.level === "superintendent" ? choices.superintendents : [];
  const anchor = params.date || (d.period && d.period.anchor) || "";
  const controls = toolbar(
    selectControl(`${VIEW}-level`, "Level", LEVELS.filter((l) => l.value !== "business_unit" || choices.business_unit), params.level, update("level")),
    nodes.length ? selectControl(`${VIEW}-node`, params.level === "team" ? "Team" : "Superintendent", nodes.map((n) => ({ value: n, label: n })),
      d.node || params.node, update("node")) : null,
    selectControl(`${VIEW}-period`, "Period", PERIODS, params.period, update("period")),
    params.period === "custom" ? dateControl(`${VIEW}-from`, "From", params.from, update("from")) : dateControl(`${VIEW}-date`, "Date", anchor, update("date")),
    params.period === "custom" ? dateControl(`${VIEW}-to`, "To", params.to, update("to")) : null,
    params.period === "shift" ? selectControl(`${VIEW}-shift`, "Shift", ["1", "2", "3"].map((s) => ({ value: s, label: `S${s}` })),
      params.shift || String((d.period && d.period.shift_number) || 1), update("shift")) : null,
    selectControl(`${VIEW}-by`, "Breakdown", BREAKDOWNS, params.by, update("by")));
  mount(host, viewTitle("Performance Over Time", "The canonical score of a team, a superintendent or the business unit over any period."),
    controls, bindings(env));
  if (env.status !== "ready") {
    host.appendChild(stateCard(env.status, env.status === "empty" ? "No evaluated team-shift for this group and period." : describeStatus(env.status),
      env.reasons));
    return;
  }
  const level = LEVELS.find((l) => l.value === d.level);
  const shift = d.period.shift_number ? ` · shift ${d.period.shift_number}` : "";
  host.appendChild(h("p", { class: "muted", "data-testid": "rollup-scope" },
    `${level ? level.label : d.level} ${d.node} · ${d.period.start} to ${d.period.end}${shift} · ${d.teams.length} team(s)`));
  if (env.reasons && env.reasons.length) host.appendChild(stateCard("unknown", "Some facts are qualified:", env.reasons));
  host.appendChild(resultBlock(d));
  [spreadBlock(d.spread), childrenBlock(d), seriesBlock(d)].forEach((block) => { if (block) host.appendChild(block); });
  const group = d.level === "team" && params.period === "shift" ? "job_aircraft" : "job";
  const jobs = await api(`/api/v1/performance/jobs${query({ ...request, node: d.level === "business_unit" ? "" : d.node, group, limit: 20, by: "" })}`)
    .catch(() => null);
  const block = jobsBlock(jobs, d);
  if (block) host.appendChild(block);
}

register(VIEW, renderRollups);
