// Plan views: Management, Project, Schedule Budget, Commitments, Economics, Capacity, Staffing and Scenario
// (S15 views 2, 9, 10, 13, 14, 17, 5, 7). Unknown stays unknown, never zero; forecasts and virtual support are labeled.
import { api, dispositionPill, fmtCents, fmtDate, fmtDateTime, fmtMinutes, h, metric, mount, pill, stateCard, table } from "../core.js";
import { bars, card, crewList, dateControl, defaultDate, frame, hashParams, query, reasonList, selectControl, setHashParams, submitCommand,
  teamOptions, timeline, toolbar } from "./common.js";
import { register } from "./registry.js";

const STATUS_KIND = { known: "ok", conditional: "info", unknown: "warn", unsupported: "err" };

function completionCell(c) {
  if (!c) return "—";
  return [pill(c.status, STATUS_KIND[c.status] || "muted"), " ", c.completion_at ? fmtDateTime(c.completion_at) : "no date"];
}

function costCell(c) {
  if (!c) return h("span", { class: "muted" }, "withheld or none");
  return [pill(c.status, STATUS_KIND[c.status] || "muted"), " ", fmtCents(c.total_cents)];
}

// ---------------------------------------------------------------- 2 Management
async function renderManagement(host, context) {
  const env = await frame(host, "Management", "Whole fleet: modeled completion, risk, cost, burndown and capacity.", "/api/v1/fleet", null,
    "No aircraft in the admitted census.");
  if (!env) return;
  const d = env.data;
  const t = d.totals;
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Aircraft", t.aircraft, `${t.jobs} jobs in WIP`), metric("Verified complete jobs", t.verified_complete),
    metric("Completion known", t.completion_known, `${t.completion_conditional} conditional · ${t.completion_unknown} unknown`),
    metric("Economics", d.economics_visible ? "visible" : "withheld", d.economics_visible ? "modeled cost per aircraft" : "needs an economics grant")));
  host.appendChild(card("Fleet", table([
    { key: "line", label: "Line", num: true, render: (r) => h("a", { href: `#project?line=${r.line}` }, String(r.line)) },
    { key: "jobs_total", label: "Jobs", num: true },
    { key: "admitted", label: "Admitted / blocked / quarantined", render: (r) => `${r.admitted} / ${r.blocked} / ${r.quarantined}` },
    { key: "cs", label: "CS gate", render: (r) => (r.cs ? `${r.cs.station} gate ${fmtDate(r.cs.gate_date)}` : "—") },
    { key: "forecast_completion", label: "Forecast completion", render: (r) => completionCell(r.forecast_completion) },
    { key: "executable_completion", label: "Executable completion", render: (r) => completionCell(r.executable_completion) },
    { key: "cost", label: "Modeled cost", render: (r) => costCell(r.cost) },
    { key: "reasons", label: "Reasons", render: (r) => reasonList(r.reasons) },
  ], d.aircraft, { caption: "Fleet census" })));
  if (d.burndown) {
    const series = d.burndown.series.map((x) => Object.assign({ __label: fmtDate(x.date) }, x));
    host.appendChild(card(`Burndown (${d.burndown.purpose.toLowerCase()} plan)`,
      h("p", { class: "muted" }, `${d.burndown.open_jobs} open jobs; ${d.burndown.never_finishing} have no planned finish (unknown, never counted done).`),
      bars(series, { label: "Remaining open jobs by day", value: (x) => x.remaining, max: d.burndown.open_jobs })));
  }
  const cap = await api(`/api/v1/capacity${query({ date: defaultDate(context, null) })}`);
  if (cap.data && cap.data.pools) {
    host.appendChild(card("Capacity summary", table([
      { key: "team", label: "Team" }, { key: "shift", label: "Shift", render: (r) => `S${r.shift}` },
      { key: "activated", label: "Activated / cap", render: (r) => `${r.activated} / ${r.activation_cap}` },
      { key: "utilization_pct", label: "Utilization %", num: true, render: (r) => r.utilization_pct ?? "—" }],
    cap.data.pools.filter((r) => r.denominator > 0), { caption: "Capacity", empty: "No pools today." })));
  }
  if (d.gaps.length) host.appendChild(card("Readiness gaps", reasonList(d.gaps.map((g) => ({ code: g.code, detail: `${g.scope}: ${g.detail} (${g.effect})` })))));
}

// ---------------------------------------------------------------- 9 Project
async function renderProject(host, context) {
  const p = hashParams();
  const lines = (context.choices && context.choices.lines) || [];
  const line = p.get("line") || (lines[0] ? String(lines[0]) : "");
  const controls = toolbar(selectControl("project-line", "Aircraft line", lines.map(String), line, (v) => setHashParams("project", { line: v })));
  if (!line) {
    mount(host, h("div", { class: "view-title" }, h("h1", {}, "Project")), stateCard("unknown", "No aircraft in the admitted census.", []));
    return;
  }
  const env = await frame(host, "Project", "Aircraft timeline, stations, gate and due, conditional completion and history.", `/api/v1/aircraft/${encodeURIComponent(line)}`, controls);
  if (!env) return;
  const d = env.data;
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Line", d.line, `${d.census.model || "model ?"} · ${d.census.customer || "customer ?"}`),
    metric("CS gate", d.cs ? fmtDate(d.cs.gate_date) : "—", d.cs ? `station ${d.cs.station}, due proxy ${fmtDate(d.cs.due_proxy)}` : "no CS fact"),
    metric("Forecast completion", d.forecast_completion ? (d.forecast_completion.completion_at ? fmtDateTime(d.forecast_completion.completion_at) : "unknown") : "—",
      d.forecast_completion ? d.forecast_completion.status : ""),
    metric("Modeled cost", d.cost ? fmtCents(d.cost.total_cents) : "withheld", d.cost ? d.cost.status : "")));
  const dated = d.timeline.filter((r) => r.start && r.end);
  if (dated.length) {
    const from = dated.reduce((a, r) => (r.start < a ? r.start : a), dated[0].start);
    const to = dated.reduce((a, r) => (r.end > a ? r.end : a), dated[0].end);
    host.appendChild(card("Timeline", timeline(dated.map((r) => ({ label: `${r.soi} (${r.team || "?"})`,
      bars: [{ start: r.start, end: r.end, kind: r.conditional ? "wait" : "work", text: r.disposition }] })), from, to)));
  }
  host.appendChild(card("Jobs", table([
    { key: "soi", label: "SOI" }, { key: "team", label: "Team" }, { key: "station", label: "Station" },
    { key: "disposition", label: "Disposition", render: (r) => (r.disposition ? dispositionPill(r.disposition) : "—") },
    { key: "start", label: "Planned", render: (r) => (r.start ? `${fmtDateTime(r.start)} → ${fmtDateTime(r.end)}` : "not placed") },
    { key: "reasons", label: "Reasons", render: (r) => reasonList(r.reasons) }], d.timeline, { caption: "Aircraft jobs" })));
  host.appendChild(card("Completion history (accepted plans)", table([
    { key: "accepted_at", label: "Accepted", render: (r) => fmtDateTime(r.accepted_at) }, { key: "purpose", label: "Purpose" },
    { key: "status", label: "Status", render: (r) => pill(r.status, STATUS_KIND[r.status] || "muted") },
    { key: "completion_at", label: "Completion", render: (r) => fmtDateTime(r.completion_at) }, { key: "plan_id", label: "Plan" }],
  d.completion_history, { empty: "No accepted plan yet." })));
  if (d.shortages.length) host.appendChild(card("Shortages", table([
    { key: "record_id", label: "Record" }, { key: "used_on", label: "Used on" }, { key: "status", label: "Status" },
    { key: "ecd", label: "ECD", render: (r) => fmtDate(r.ecd) }, { key: "readiness", label: "Receipt" }], d.shortages)));
  if (d.calendar) host.appendChild(card("Line calendar", h("p", {}, `${d.calendar.status} ${fmtDate(d.calendar.min_date)} – ${fmtDate(d.calendar.max_date)}`),
    d.calendar.holes.length ? reasonList(d.calendar.holes.map((x) => ({ code: "CALENDAR_HOLE", detail: x }))) : null));
}

// ---------------------------------------------------------------- 10 Schedule Budget
async function renderBudget(host, context) {
  const p = hashParams();
  const date = defaultDate(context, p.get("date"));
  const purpose = p.get("purpose") || "EXECUTABLE";
  const controls = toolbar(dateControl("budget-date", "Date", date, (v) => setHashParams("budget", { date: v, purpose })),
    selectControl("budget-purpose", "Plan", ["EXECUTABLE", "FORECAST"], purpose, (v) => setHashParams("budget", { date, purpose: v })));
  const env = await frame(host, "Schedule Budget", "Standard person effort against planned duty, non-value-added time and OT.",
    `/api/v1/budget${query({ date, purpose })}`, controls);
  if (!env) return;
  const d = env.data;
  host.appendChild(card("Team-shifts", table([
    { key: "team", label: "Team" }, { key: "shift", label: "Shift", render: (r) => `S${r.shift}` },
    { key: "planned_standard_person_minutes", label: "Std person-min", num: true },
    { key: "work_minutes", label: "Work", num: true }, { key: "nva_minutes", label: "NVA", num: true },
    { key: "nva_pct", label: "NVA %", num: true, render: (r) => r.nva_pct ?? "—" }, { key: "ot_minutes", label: "OT", num: true },
    { key: "regular_capacity_minutes", label: "Regular capacity", num: true }], d.team_shifts, { caption: "Budget by team-shift" })));
  host.appendChild(card("Jobs: flow budget versus plan", table([
    { key: "job", label: "Job" }, { key: "team", label: "Team" }, { key: "disposition", label: "Disposition", render: (r) => dispositionPill(r.disposition) },
    { key: "flow_minutes", label: "Flow budget", num: true, render: (r) => (r.flow_minutes === null ? "unknown" : fmtMinutes(r.flow_minutes)) },
    { key: "planned_span_minutes", label: "Planned span", num: true, render: (r) => (r.planned_span_minutes === null ? "not placed" : fmtMinutes(r.planned_span_minutes)) },
    { key: "conditional", label: "Conditional", render: (r) => (r.conditional ? pill("conditional", "info") : "—") }], d.jobs, { caption: "Job budgets" })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

// ---------------------------------------------------------------- 13 Commitments
async function renderCommitments(host) {
  const env = await frame(host, "Commitments", "Delivery dates with pending confirmation and raw risk; three-day task defense outcomes.", "/api/v1/commitments");
  if (!env) return;
  const d = env.data;
  host.appendChild(card("Delivery commitments", table([
    { key: "line", label: "Line", num: true }, { key: "original_date", label: "Original", render: (r) => fmtDate(r.original_date) },
    { key: "current_date", label: "Current", render: (r) => fmtDate(r.current_date) }, { key: "raw_projection", label: "Raw projection", render: (r) => fmtDate(r.raw_projection) },
    { key: "pending_target", label: "Pending", render: (r) => (r.pending_target ? [fmtDate(r.pending_target), " ", pill(`${r.confirmations} confirmations`, "info")] : "—") },
    { key: "risk", label: "Raw risk", render: (r) => (r.risk ? pill(r.risk, "warn") : "—") }, { key: "alert", label: "Alert", render: (r) => (r.alert ? pill(r.alert, "err") : "—") },
    { key: "changes", label: "Change ledger", render: (r) => (r.changes.length ? h("ul", { class: "reason-list compact" }, r.changes.map((c) =>
      h("li", {}, h("code", {}, c.kind), ` ${fmtDate(c.from_date)} → ${fmtDate(c.to_date)}`))) : "—") }],
  d.delivery, { caption: "Delivery commitments", empty: "No delivery commitments established yet." })));
  host.appendChild(card("Task commitments (two attempts, then minimal move)", h("p", { class: "inline-pills" },
    Object.entries(d.task_outcomes).map(([k, v]) => [pill(`${k} ${v}`, k === "KEPT" ? "ok" : "info"), " "])),
  table([
    { key: "phase_label", label: "Work" }, { key: "day_offset", label: "Day", num: true }, { key: "outcome", label: "Outcome", render: (r) => pill(r.outcome, r.outcome === "KEPT" ? "ok" : "info") },
    { key: "attempt1", label: "Attempt 1" }, { key: "attempt2", label: "Attempt 2" }, { key: "reason", label: "Reason" }], d.tasks, { caption: "Task commitments" })));
}

// ---------------------------------------------------------------- 14 Economics
async function renderEconomics(host, context) {
  if (!context.capabilities.economics) {
    mount(host, h("div", { class: "view-title" }, h("h1", {}, "Economics")),
      stateCard("blocked", "Cost and contract terms need an economics grant.", [{ code: "ECONOMICS_RESTRICTED", message: "Withheld, not zero." }]));
    return;
  }
  const env = await frame(host, "Economics", "Known, conditional and unknown modeled cost; terms and their authority.", "/api/v1/economics");
  if (!env) return;
  const d = env.data;
  for (const [purpose, plan] of Object.entries(d.plans)) {
    host.appendChild(card(`${purpose} plan ${plan.plan_id}`, h("div", { class: "metric-grid" },
      metric("Known total", fmtCents(plan.known_total_cents), plan.complete ? "complete fleet" : "fleet incomplete"),
      metric("Conditional total", fmtCents(plan.conditional_total_cents)), metric("Controllable known", fmtCents(plan.controllable_known_cents)),
      metric("Unknown lines", plan.unknown_lines.length, plan.unknown_lines.join(", ") || "none")),
    table([
      { key: "line", label: "Line", num: true }, { key: "status", label: "Status", render: (r) => pill(r.status, STATUS_KIND[r.status] || "muted") },
      { key: "authority", label: "Authority" }, { key: "delay_minutes", label: "Delay", num: true, render: (r) => (r.delay_minutes === null ? "unknown" : fmtMinutes(r.delay_minutes)) },
      { key: "total_cents", label: "Total", num: true, render: (r) => fmtCents(r.total_cents) },
      { key: "unavoidable_cents", label: "Unavoidable", num: true, render: (r) => fmtCents(r.unavoidable_cents) },
      { key: "controllable_cents", label: "Controllable", num: true, render: (r) => fmtCents(r.controllable_cents) },
      { key: "marginal_day_cents", label: "Marginal day", num: true, render: (r) => fmtCents(r.marginal_day_cents) }], plan.lines, { caption: `${purpose} costs` })));
  }
  host.appendChild(card("Terms", table([
    { key: "line", label: "Line", num: true }, { key: "status", label: "Status" }, { key: "authority", label: "Authority" }, { key: "selector", label: "Selector" },
    { key: "daily_rate_cents", label: "Daily rate", num: true, render: (r) => fmtCents(r.daily_rate_cents) },
    { key: "cap_cents", label: "Cap", num: true, render: (r) => fmtCents(r.cap_cents) },
    { key: "accrued_floor_cents", label: "Accrued floor", num: true, render: (r) => fmtCents(r.accrued_floor_cents) },
    { key: "due_at", label: "Due", render: (r) => fmtDateTime(r.due_at) }, { key: "reasons", label: "Reasons", render: (r) => reasonList(r.reasons) }],
  d.terms, { caption: "Economic terms" })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

// ---------------------------------------------------------------- 17 Capacity
async function renderCapacity(host, context) {
  const p = hashParams();
  const date = defaultDate(context, p.get("date"));
  const purpose = p.get("purpose") || "EXECUTABLE";
  const controls = toolbar(dateControl("capacity-date", "Date", date, (v) => setHashParams("capacity", { date: v, purpose })),
    selectControl("capacity-purpose", "Plan", ["EXECUTABLE", "FORECAST"], purpose, (v) => setHashParams("capacity", { date, purpose: v })));
  const env = await frame(host, "Capacity", "Activation against the cap is distinct from utilization of capacity.", `/api/v1/capacity${query({ date, purpose })}`, controls);
  if (!env) return;
  const d = env.data;
  const canScenario = context.identity.grants.some((g) => g === "scenario_planner" || g === "manager");
  const status = h("div", { class: "command-status" });
  host.appendChild(card("Pools", table([
    { key: "team", label: "Team" }, { key: "shift", label: "Shift", render: (r) => `S${r.shift}${r.synthetic ? " (incl. virtual)" : ""}` },
    { key: "denominator", label: "Denominator", num: true }, { key: "activated", label: "Activated / cap", render: (r) => `${r.activated} / ${r.activation_cap}` },
    { key: "activation_pct", label: "Activation %", num: true, render: (r) => r.activation_pct ?? "—" },
    { key: "utilization_pct", label: "Utilization %", num: true, render: (r) => r.utilization_pct ?? "—" },
    { key: "ot_minutes", label: "OT", num: true }, { key: "unknown_attendance", label: "Unknown attendance", num: true },
    { key: "skills", label: "Skill bottlenecks", render: (r) => (r.skills.filter((s) => s.shortfall > 0).length
      ? reasonList(r.skills.filter((s) => s.shortfall > 0).map((s) => ({ code: `SHORT_${s.shortfall}`, detail: `${s.requirement}: ${s.qualified_admitted} qualified for a crew of ${s.crew_size}` }))) : "—") },
    { key: "pool_id", label: "What-if", render: (r) => (canScenario && context.source ? whatIf(r, context, status) : "—") }],
  d.pools, { caption: "Capacity by pool" }), status));
  const codes = Object.entries(d.unscheduled_reason_codes).map(([k, v]) => ({ __label: k, v }));
  if (codes.length) host.appendChild(card("Weighted bottlenecks (unscheduled reason codes)", bars(codes, { label: "Unscheduled jobs by reason", value: (x) => x.v })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

function whatIf(row, context, status) {
  const button = h("button", { class: "btn btn-quiet", type: "button", title: "Scenario only: never executable" }, "+1 virtual (scenario)");
  button.addEventListener("click", async () => {
    button.disabled = true;
    await submitCommand("/api/v1/scenarios", { source_run_id: context.source.run_id, budget_seconds: 20,
      changes: [{ type: "virtual_staffing", team: row.team, date: row.date, shift: row.shift, add: 1 }] }, status,
    { prefix: "whatif", onDone: (run) => { location.hash = `scenario?id=${run.result_receipt.scenario_id}`; } });
    button.disabled = false;
  });
  return button;
}

// ---------------------------------------------------------------- 5 Staffing
async function renderStaffing(host, context) {
  const p = hashParams();
  const date = defaultDate(context, p.get("date"));
  const shift = p.get("shift") || "";
  const controls = toolbar(dateControl("staffing-date", "Date", date, (v) => setHashParams("staffing", { date: v, shift })),
    selectControl("staffing-shift", "Shift", [{ value: "", label: "All" }, { value: "1", label: "S1" }, { value: "2", label: "S2" }, { value: "3", label: "S3" }], shift,
      (v) => setHashParams("staffing", { date, shift: v })));
  const env = await frame(host, "Staffing", "Pure pools: attendance, denominators, activation caps and skill demand.", `/api/v1/workforce${query({ date, shift })}`, controls);
  if (!env) return;
  const cap = await api(`/api/v1/capacity${query({ date })}`);
  const skills = new Map(((cap.data && cap.data.pools) || []).map((r) => [r.pool_id, r]));
  for (const pool of env.data.pools) {
    const c = skills.get(pool.pool_id);
    host.appendChild(card(`${pool.team} · ${fmtDate(pool.date)} S${pool.shift}${pool.synthetic ? " (incl. virtual support, forecast only)" : ""}`,
      h("div", { class: "metric-grid" },
        metric("Executable N / cap", `${pool.n_executable} / ${pool.cap_executable}`, "admitted attendance"),
        metric("Forecast N / cap", `${pool.n_forecast} / ${pool.cap_forecast}`, "incl. nominal and virtual"),
        metric("Unknown attendance", c ? c.unknown_attendance : "—"), metric("Activated", c ? `${c.activated} / ${c.activation_cap}` : "—")),
      c && c.skills.length ? table([
        { key: "requirement", label: "Skill demand" }, { key: "crew_size", label: "Crew size", num: true },
        { key: "seat_assignments", label: "Seat-assignments", num: true }, { key: "demand_minutes", label: "Minutes", num: true },
        { key: "qualified_admitted", label: "Qualified admitted", num: true },
        { key: "shortfall", label: "Shortfall", num: true, render: (r) => (r.shortfall ? pill(String(r.shortfall), "err") : "0") }], c.skills, { caption: "Skill demand" }) : null,
      table([
        { key: "name", label: "Member", render: (r) => (r.redacted ? "outside your scope" : r.name) },
        { key: "executable", label: "Executable", render: (r) => (r.executable ? pill("yes", "ok") : pill("no", "muted")) },
        { key: "admitted_presence", label: "Presence", render: (r) => (r.admitted_presence === null ? pill("unknown", "warn") : r.admitted_presence ? "admitted" : "not admitted") },
        { key: "regular_cap", label: "Regular cap", num: true, render: (r) => (r.regular_cap === null ? "unknown" : r.regular_cap) },
        { key: "basis", label: "Basis" }, { key: "synthetic", label: "Origin", render: (r) => (r.synthetic ? pill("virtual", "warn") : r.origin) }],
      pool.members, { caption: "Pool members" })));
  }
}

// ---------------------------------------------------------------- 7 Scenario
async function renderScenario(host, context) {
  const p = hashParams();
  const id = p.get("id");
  if (id) {
    const env = await frame(host, "Scenario", "An isolated what-if: never publishable, executable or written to live heads.", `/api/v1/scenarios/${encodeURIComponent(id)}`);
    if (!env) return;
    host.appendChild(scenarioResult(env.data));
    host.appendChild(h("p", {}, h("a", { href: "#scenario" }, "All scenarios")));
    return;
  }
  const env = await frame(host, "Scenario", "Explicit isolated what-if inputs compared with a same-settings baseline under a bounded budget.", "/api/v1/scenarios",
    null, "No scenario has been run yet.");
  if (env && env.data && env.data.inputs) host.appendChild(scenarioForm(env.data.inputs, context));
  if (!env || !env.data) return;
  host.appendChild(card("Stored scenarios", table([
    { key: "created_at", label: "Created", render: (r) => fmtDateTime(r.created_at) }, { key: "status", label: "Status", render: (r) => pill(r.status, r.status === "COMPLETED" ? "ok" : "warn") },
    { key: "changes", label: "Changes", render: (r) => r.changes.map((c) => c.type).join(", ") },
    { key: "scenario_id", label: "Result", render: (r) => h("a", { href: `#scenario?id=${r.scenario_id}` }, "open") }], env.data.scenarios, { empty: "None yet." })));
}

function scenarioForm(inputs, context) {
  const canRun = context.identity.grants.some((g) => g === "scenario_planner" || g === "manager");
  if (!canRun) return card("New scenario", h("p", { class: "muted" }, "Running scenarios needs the scenario planner or manager grant."));
  const status = h("div", { class: "command-status" });
  const pool = h("select", { id: "scn-pool" }, [h("option", { value: "" }, "no extra staffing")].concat(inputs.pools.map((x) =>
    h("option", { value: `${x.team}|${x.date}|${x.shift}` }, `${x.team} ${x.date} S${x.shift} (forecast N ${x.n_forecast})`))));
  const add = h("input", { id: "scn-add", type: "number", min: "1", max: "10", value: "1" });
  const stat = h("select", { id: "scn-stat" }, inputs.statistics.map((s) => h("option", { value: s, selected: s === inputs.current_statistic }, s)));
  const rel = h("select", { id: "scn-release" }, [h("option", { value: "" }, "no ETA change")].concat(inputs.releases.map((r) => h("option", { value: r.release_id }, `${r.label} (${r.status})`))));
  const eta = h("input", { id: "scn-eta", type: "datetime-local" });
  const run = h("button", { class: "btn btn-primary", type: "button" }, "Run scenario");
  run.addEventListener("click", async () => {
    const changes = [];
    if (pool.value) {
      const [team, date, shift] = pool.value.split("|");
      changes.push({ type: "virtual_staffing", team, date, shift: Number(shift), add: Number(add.value) || 1 });
    }
    if (stat.value !== inputs.current_statistic) changes.push({ type: "duration_statistic", statistic: stat.value });
    if (rel.value && eta.value) changes.push({ type: "eta_forecast", release_id: rel.value, forecast_at: new Date(eta.value).toISOString().replace(/\.\d+Z$/, "Z") });
    if (!changes.length) {
      mount(status, stateCard("unknown", "Choose at least one change.", []));
      return;
    }
    run.disabled = true;
    await submitCommand("/api/v1/scenarios", { source_run_id: inputs.source_run_id, changes, budget_seconds: 20 }, status,
      { prefix: "scn", onDone: (r) => { location.hash = `scenario?id=${r.result_receipt.scenario_id}`; } });
    run.disabled = false;
  });
  return card("New scenario",
    h("p", { class: "muted" }, "Virtual people are synthetic and forecast-only; a scenario never changes live plans, goals, commitments or grades."),
    h("div", { class: "toolbar" }, h("label", { for: "scn-pool" }, "Virtual staffing", pool), h("label", { for: "scn-add" }, "People", add),
      h("label", { for: "scn-stat" }, "Duration statistic", stat), h("label", { for: "scn-release" }, "Part ETA", rel), h("label", { for: "scn-eta" }, "New ETA", eta)),
    run, status);
}

function scenarioResult(d) {
  const c = d.comparison || {};
  return h("div", {},
    h("div", { class: "metric-grid" },
      metric("Status", d.status, d.capability), metric("Changes", d.changes.length, d.changes.map((x) => x.type).join(", ")),
      metric("Baseline valid", d.baseline_valid === null ? "—" : String(d.baseline_valid)), metric("Scenario valid", d.scenario_valid === null ? "—" : String(d.scenario_valid))),
    card("Notes", reasonList(d.notes.map((n) => ({ code: "CHANGE", detail: n })))),
    c.lines ? card("Completion by aircraft", table([
      { key: "line", label: "Line", num: true },
      { key: "baseline", label: "Baseline", render: (r) => (r.baseline ? [pill(r.baseline.status, STATUS_KIND[r.baseline.status] || "muted"), " ", fmtDateTime(r.baseline.completion_at)] : "—") },
      { key: "scenario", label: "Scenario", render: (r) => [pill(r.scenario.status, STATUS_KIND[r.scenario.status] || "muted"), " ", fmtDateTime(r.scenario.completion_at)] },
      { key: "delta_minutes", label: "Change", num: true, render: (r) => (r.delta_minutes === null ? "unknown" : fmtMinutes(r.delta_minutes)) }], c.lines, { caption: "Scenario comparison" })) : null,
    c.baseline ? card("Plans", table([
      { key: "name", label: "" }, { key: "assignments", label: "Assignments", num: true }, { key: "virtual_assignments", label: "With virtual crew", num: true },
      { key: "dispositions", label: "Dispositions", render: (r) => Object.entries(r.dispositions).map(([k, v]) => `${k} ${v}`).join(", ") }],
    [Object.assign({ name: "baseline" }, c.baseline), Object.assign({ name: "scenario" }, c.scenario)])) : null,
    c.note ? h("p", { class: "muted" }, c.note) : null);
}

register("management", renderManagement);
register("project", renderProject);
register("budget", renderBudget);
register("commitments", (host) => renderCommitments(host));
register("economics", renderEconomics);
register("capacity", renderCapacity);
register("staffing", renderStaffing);
register("scenario", renderScenario);
export { crewList };
