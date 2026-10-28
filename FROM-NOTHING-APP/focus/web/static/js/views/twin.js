// Digital Twin (S16): observed, planned, forecast and unknown layers of one canonical revision, reconciled exactly.
// The browser sums nothing: every total, state count and reconciliation check comes from GET /api/v1/twin.
import { fmtDate, fmtDateTime, fmtMinutes, h, metric, pill, table } from "../core.js";
import { card, dateControl, frame, hashParams, query, reasonList, selectControl, setHashParams, teamOptions, toolbar } from "./common.js";
import { register } from "./registry.js";

const STATE_KIND = { complete: "ok", in_progress: "info", no_start_observed: "muted", unknown: "warn", scheduled: "ok",
  verified_complete: "ok", conditional: "info", deferred: "warn", blocked: "warn", quarantined: "err", excluded: "muted", absent: "err" };

function states(counts) {
  const entries = Object.entries(counts || {});
  if (!entries.length) return "—";
  return entries.map(([k, v]) => [pill(`${k.replaceAll("_", " ")} ${v}`, STATE_KIND[k] || "muted"), " "]);
}

function rows(levelTotals, keyName) {
  return Object.entries(levelTotals || {}).map(([key, m]) => ({ [keyName]: key === "none" ? "no line" : key, ...m }));
}

function observedColumns(first) {
  return [first, { key: "goal_minutes", label: "Goal (B)", num: true, render: (r) => fmtMinutes(r.goal_minutes) },
    { key: "earned_minutes", label: "Earned (Cp)", num: true, render: (r) => fmtMinutes(r.earned_minutes) },
    { key: "missed_minutes", label: "Missed", num: true, render: (r) => fmtMinutes(r.missed_minutes) },
    { key: "relieved_minutes", label: "Relieved (E)", num: true, render: (r) => fmtMinutes(r.relieved_minutes) },
    { key: "pending_minutes", label: "Pending", num: true, render: (r) => fmtMinutes(r.pending_minutes) },
    { key: "offplan_minutes", label: "Offplan (U)", num: true,
      render: (r) => (r.offplan_effort_unknown ? `${fmtMinutes(r.offplan_minutes)} + ${r.offplan_effort_unknown} unknown` : fmtMinutes(r.offplan_minutes)) }];
}

function planColumns(first, forecast) {
  const cols = [first, { key: "standard_minutes", label: "Standard", num: true, render: (r) => fmtMinutes(r.standard_minutes) },
    { key: "assignments", label: "Assignments", num: true },
    { key: "work", label: "Work", num: true, render: (r) => fmtMinutes(r.duty_minutes.WORK) },
    { key: "nva", label: "Travel/wait/tool/other", num: true,
      render: (r) => fmtMinutes(r.duty_minutes.TRAVEL + r.duty_minutes.WAIT + r.duty_minutes.TOOL + r.duty_minutes.OTHER) },
    { key: "ot_minutes", label: "OT", num: true, render: (r) => fmtMinutes(r.ot_minutes) }];
  if (forecast) {
    cols.push({ key: "conditional_assignments", label: "Conditional", num: true },
      { key: "virtual_duty_minutes", label: "Virtual duty", num: true, render: (r) => fmtMinutes(r.virtual_duty_minutes) });
  }
  return cols;
}

function drill(label, params) {
  const a = h("a", { href: "#twin" + query(params) }, label);
  return a;
}

async function renderTwin(host, context) {
  const params = hashParams();
  const sel = { team: params.get("team") || "", date: params.get("date") || "", shift: params.get("shift") || "", line: params.get("line") || "" };
  const update = (key) => (value) => setHashParams("twin", { ...sel, [key]: value });
  const lines = ((context.choices && context.choices.lines) || []).map((l) => ({ value: String(l), label: String(l) }));
  const controls = toolbar(
    selectControl("twin-team", "Team", [{ value: "", label: "All in scope" }, ...teamOptions(context)], sel.team, update("team")),
    dateControl("twin-date", "Shift date", sel.date, update("date")),
    selectControl("twin-shift", "Shift", [{ value: "", label: "All" }, { value: "1", label: "S1" }, { value: "2", label: "S2" }, { value: "3", label: "S3" }],
      sel.shift, update("shift")),
    selectControl("twin-line", "Line", [{ value: "", label: "All" }, ...lines], sel.line, update("line")));
  const env = await frame(host, "Digital Twin", "Observed, planned, forecast and unknown layers of one canonical revision, reconciled exactly.",
    "/api/v1/twin" + query(sel), controls, "No twin cells in your scope.");
  if (!env) return;
  const d = env.data;
  const rec = d.reconciliation;
  const failed = rec.checks.filter((c) => !c.ok);
  host.appendChild(h("section", { class: `card ${rec.ok ? "state-reconciled" : "state-blocked"}`, role: "status", "data-reconciled": String(rec.ok) },
    h("strong", {}, rec.ok ? `Reconciled: all ${rec.checks.length} checks pass` : `${failed.length} of ${rec.checks.length} reconciliation checks fail`),
    h("p", { class: "muted" }, "Cells sum exactly to team, shift, line and total; job states partition the census; planned effort equals the frozen "
      + "B0 of the plan of record; observed effort equals each stored evaluation; duty equals the ledger."),
    failed.length ? reasonList(failed.map((c) => ({ code: c.check, message: JSON.stringify(c.mismatch) }))) : null));

  const o = d.totals.observed.grand, p = d.totals.planned.grand, f = d.totals.forecast.grand;
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Observed earned (Cp)", fmtMinutes(o.earned_minutes), `of ${fmtMinutes(o.goal_minutes)} goal effort (B)`, "layer-observed"),
    metric("Planned standard", fmtMinutes(p.standard_minutes), `${p.assignments} assignments`, "layer-planned"),
    metric("Forecast standard", fmtMinutes(f.standard_minutes), `${f.conditional_assignments} conditional · virtual ${fmtMinutes(f.virtual_duty_minutes)}`, "layer-forecast"),
    metric("Unknown jobs", d.unknown.jobs.length, `${d.inventory.total.jobs} jobs in the census`, "layer-unknown")));

  const c = d.cutoffs;
  host.appendChild(card("Revision and cutoffs", h("dl", { class: "kv" },
    h("dt", {}, "Source"), h("dd", {}, `${d.revision.source_run_id} · cut off ${fmtDateTime(c.source.cutoff)}`, c.source.stale ? pill("stale", "warn") : null),
    h("dt", {}, "Planned"), h("dd", {}, c.planned ? `${c.planned.plan_id} · accepted ${fmtDateTime(c.planned.accepted_at)}` : "no accepted executable plan"),
    h("dt", {}, "Forecast"), h("dd", {}, c.forecast ? `${c.forecast.plan_id} · accepted ${fmtDateTime(c.forecast.accepted_at)}` : "no accepted forecast plan"),
    h("dt", {}, "Observed"), h("dd", {}, c.observed ? `${c.observed.observation_run_id} · as of ${fmtDateTime(c.observed.as_of)}` : "no observations collected",
      c.observed && c.observed.stale ? pill("stale", "warn") : null),
    h("dt", {}, "Policy"), h("dd", {}, d.revision.policy_id))));

  const teamCol = { key: "team", label: "Team", render: (r) => drill(r.team, { ...sel, team: r.team }) };
  const shiftCol = { key: "shift", label: "Shift" };
  const lineCol = { key: "line", label: "Line", render: (r) => (r.line === "no line" ? "no line" : drill(r.line, { ...sel, line: r.line })) };
  host.appendChild(card("Observed (measured: stored canonical evaluation evidence)",
    table(observedColumns(teamCol), rows(d.totals.observed.by_team, "team"), { caption: "Observed by team", empty: "No evaluated team-shift in this selection." }),
    table(observedColumns(lineCol), rows(d.totals.observed.by_line, "line"), { caption: "Observed by line", empty: "No evaluated effort by line." })));
  host.appendChild(card("Planned (accepted EXECUTABLE plan)",
    table(planColumns(teamCol, false), rows(d.totals.planned.by_team, "team"), { caption: "Planned by team", empty: "No planned work in this selection." }),
    table(planColumns(shiftCol, false), rows(d.totals.planned.by_shift, "shift"), { caption: "Planned by shift" }),
    table(planColumns(lineCol, false), rows(d.totals.planned.by_line, "line"), { caption: "Planned by line" })));
  host.appendChild(card("Forecast (accepted FORECAST plan, conditional)",
    h("p", {}, Object.entries(d.forecast_conditions).map(([k, v]) => [pill(`${k} ${v}`, "info"), " "])),
    table(planColumns(teamCol, true), rows(d.totals.forecast.by_team, "team"), { caption: "Forecast by team", empty: "No forecast work in this selection." }),
    table(planColumns(lineCol, true), rows(d.totals.forecast.by_line, "line"), { caption: "Forecast by line" })));

  host.appendChild(card("Inventory by line (every census job once per layer)", table([
    { key: "line", label: "Line", num: true, render: (r) => drill(String(r.line), { ...sel, line: r.line }) }, { key: "jobs", label: "Jobs", num: true },
    { key: "observed", label: "Observed", render: (r) => states(r.observed) }, { key: "planned", label: "Planned", render: (r) => states(r.planned) },
    { key: "forecast", label: "Forecast", render: (r) => states(r.forecast) }, { key: "unknown", label: "Unknown", num: true }],
  d.inventory.by_line, { caption: "Inventory", empty: "No census jobs in this selection." })));

  host.appendChild(card("Unknown (what no layer can state)",
    table([{ key: "job", label: "Job" }, { key: "team", label: "Team" }, { key: "observed", label: "Observed" }, { key: "planned", label: "Planned" },
      { key: "forecast", label: "Forecast" }, { key: "reasons", label: "Reasons", render: (r) => r.reasons.join(", ") }], d.unknown.jobs,
    { caption: "Unknown jobs", empty: "Every job is complete or placed in a plan." }),
    table([{ key: "team", label: "Team" }, { key: "shift_id", label: "Shift" }, { key: "code", label: "State" }, { key: "detail", label: "Detail" }],
      d.unknown.evaluations, { caption: "Evaluations", empty: "Every goal of record has a stored evaluation." }),
    table([{ key: "team", label: "Team" }, { key: "shift", label: "Shift" }, { key: "unknown_attendance", label: "Unknown attendance", num: true }],
      d.unknown.attendance, { caption: "Attendance", empty: "No unknown attendance in planned shifts." })));

  host.appendChild(card(`Cells (${d.cells.length})`, table([
    { key: "team", label: "Team", render: (r) => r.key.team }, { key: "shift", label: "Shift", render: (r) => `${fmtDate(r.key.shift_start_date)} S${r.key.shift_number}` },
    { key: "line", label: "Line", render: (r) => (r.key.line === null ? "no line" : r.key.line) },
    { key: "observed", label: "Observed earned / goal", render: (r) => (r.observed ? `${fmtMinutes(r.observed.earned_minutes)} / ${fmtMinutes(r.observed.goal_minutes)}` : "not measured") },
    { key: "planned", label: "Planned std", render: (r) => (r.planned ? fmtMinutes(r.planned.standard_minutes) : "—") },
    { key: "forecast", label: "Forecast std", render: (r) => (r.forecast ? fmtMinutes(r.forecast.standard_minutes) : "—") },
    { key: "evaluation", label: "Evaluation", render: (r) => (r.key.evaluation_id ? h("code", {}, `${r.key.evaluation_id} r${r.key.evaluation_revision}`) : "—") }],
  d.cells, { caption: "Twin cells" })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

register("twin", renderTwin);
