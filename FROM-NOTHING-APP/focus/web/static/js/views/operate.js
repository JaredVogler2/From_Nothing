// Operate views: Team Lead, Mechanic, My Day, Shift Book, Worker Gantt and Turnover (S15 views 1, 3, 11, 15, 16 + turnover panel).
// Every fact is a canonical API projection; actions are commands that return worker runs (never a browser assignment).
import { dispositionPill, fmtDateTime, fmtMinutes, fmtTime, h, metric, pill, stateCard, table } from "../core.js";
import { card, crewList, csvButton, dateControl, defaultDate, defaultTeam, frame, hashParams, legend, query, reasonList, selectControl,
  setHashParams, submitCommand, teamOptions, timeline, toolbar } from "./common.js";
import { register } from "./registry.js";
import { scoreSummary } from "./performance.js";

function teamShiftControls(view, context, params, { withShift = true } = {}) {
  const team = defaultTeam(context, params.get("team"));
  const date = defaultDate(context, params.get("date"));
  const shift = params.get("shift") || "1";
  const update = (key) => (value) => setHashParams(view, { team, date, shift, [key]: value });
  return {
    team, date, shift,
    controls: toolbar(
      teamOptions(context).length ? selectControl(`${view}-team`, "Team", teamOptions(context), team, update("team")) : null,
      dateControl(`${view}-date`, "Shift start date", date, update("date")),
      withShift ? selectControl(`${view}-shift`, "Shift", ["1", "2", "3"].map((s) => ({ value: s, label: `S${s}` })), shift, update("shift")) : null),
  };
}

// ---------------------------------------------------------------- 1 Team Lead
async function renderTeamLead(host, context) {
  const p = hashParams();
  const { team, date, shift, controls } = teamShiftControls("team-lead", context, p);
  if (!team) {
    host.appendChild(stateCard("blocked", "Your scope has no team to lead.", [{ code: "NO_TEAM_SCOPE", message: "Ask an administrator for a team scope." }]));
    return;
  }
  const env = await frame(host, "Team Lead", "Server-ranked actions with exact crews and current dispatch reasons.",
    `/api/v1/team-lead${query({ team, date, shift })}`, controls, "No accepted assignments for this team-shift.");
  if (!env) return;
  const d = env.data;
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Actions", d.counts.actions, d.shift_id),
    metric("Dispatchable now", d.counts.dispatchable, `assessed ${fmtDateTime(d.assessed_at)}`),
    metric("Blocked", d.counts.blocked, "with reasons below"),
    metric("Goal B0", d.goal ? fmtMinutes(d.goal.B0) : "—", d.goal ? `goal ${d.goal.goal_id}` : "no goal of record")));
  host.appendChild(card("Ranked actions", table([
    { key: "rank", label: "#", num: true },
    { key: "phase_label", label: "Work" },
    { key: "start", label: "Window", render: (r) => `${fmtTime(r.start)}–${fmtTime(r.end)}` },
    { key: "crew", label: "Crew", render: (r) => crewList(r.crew) },
    { key: "requirement", label: "Certificates" },
    { key: "dispatchable", label: "Now", render: (r) => (r.dispatchable ? pill("dispatchable", "ok") : pill("blocked", "warn")) },
    { key: "reasons", label: "Reasons", render: (r) => reasonList(r.reasons) },
    { key: "commitment_outcome", label: "Commitment", render: (r) => (r.committed ? pill(r.commitment_outcome || "committed", "info") : "—") },
  ], d.actions, { caption: "Ranked actions" })));
  const status = h("div", { class: "command-status" });
  const reason = h("textarea", { id: "amend-reason", maxlength: "2000", placeholder: "Why this work must be added to the shift (required, at least 10 characters)" });
  const canAmend = d.goal && context.identity.grants.some((g) => g === "team_lead" || g === "manager");
  const proposals = card("Emergency additions (proposals)",
    d.proposal_reasons.length ? reasonList(d.proposal_reasons) : null,
    canAmend && d.proposals.some((r) => r.feasible) ? h("label", { for: "amend-reason" }, "Reason", reason) : null,
    d.proposal_reasons.length && !d.proposals.length ? null : table([
      { key: "label", label: "Open team work" },
      { key: "standard_person_minutes", label: "Std person-min", num: true },
      { key: "requirement", label: "Certificates" },
      { key: "feasible", label: "Feasible now", render: (r) => (r.feasible ? pill(r.basis || "feasible", "ok") : reasonList(r.reasons)) },
      { key: "phase_id", label: "Action", render: (r) => (r.feasible && canAmend ? addButton(r, d, status, reason) : "—") },
    ], d.proposals, { caption: "Amendment proposals", empty: "No open team work to propose." }),
    status);
  host.appendChild(proposals);
  host.appendChild(h("p", { class: "muted" }, d.note));
}

function addButton(row, d, status, reasonInput) {
  const button = h("button", { class: "btn btn-secondary", type: "button" }, "Add to goal");
  button.addEventListener("click", async () => {
    const reason = reasonInput.value.trim();
    if (reason.length < 10) {
      reasonInput.focus();
      reasonInput.setAttribute("aria-invalid", "true");
      return;
    }
    button.disabled = true;
    await submitCommand("/api/v1/amendments", {
      plan_id: d.goal.plan_id, expected_version: d.goal.versions.amendments, reason,
      change: { kind: "ADD_EMERGENCY_WORK", team: d.team, shift_id: d.shift_id, phase_id: row.phase_id },
    }, status, { prefix: "amend" });
    button.disabled = false;
  });
  return button;
}

// ---------------------------------------------------------------- 16 My Day and 3 Mechanic share one projection
function dutyTable(d) {
  return table([
    { key: "start", label: "When", render: (r) => [`${fmtTime(r.start)}–${fmtTime(r.end)}`, r.current ? [" ", pill("now", "info")] : null] },
    { key: "kind", label: "Duty", render: (r) => [pill(r.kind, r.kind === "WORK" ? "ok" : "muted"), r.ot_minutes ? [" ", pill(`OT ${r.ot_minutes}m`, "err")] : null] },
    { key: "job", label: "Aircraft / work", render: (r) => (r.job ? `${r.job.line}/${r.job.soi} · ${r.phase ? r.phase.label : ""}` :
      r.aircraft_to ? `to line ${r.aircraft_to}` : "—") },
    { key: "minutes", label: "Minutes", num: true },
    { key: "crewmates", label: "Crewmates", render: (r) => (r.crewmates.length ? crewList(r.crewmates) : "—") },
    { key: "readiness", label: "Readiness", render: (r) => (r.readiness ? reasonList([].concat(r.readiness.conditional_reasons, r.readiness.blocked_reasons)) : "—") },
    { key: "cert_check", label: "Certificates", render: (r) => (r.cert_check
      ? pill(`${r.cert_check.status}: ${r.cert_check.requirement}`, String(r.cert_check.status).toLowerCase() === "allowed" ? "ok" : "err") : "—") },
  ], d.duties, { caption: "Ordered duties", empty: "No planned duty on this date." });
}

async function renderMyDay(host, context, { mechanic = false } = {}) {
  const p = hashParams();
  const view = mechanic ? "mechanic" : "myday";
  const person = p.get("person") || (context.scope && context.scope.person) || "";
  const date = p.get("date") || "";
  const personInput = h("input", { id: `${view}-person`, type: "text", value: person, placeholder: "badge / person key", autocomplete: "off" });
  const go = h("button", { class: "btn btn-quiet", type: "button" }, "Open");
  go.addEventListener("click", () => setHashParams(view, { person: personInput.value.trim(), date }));
  const controls = toolbar(
    context.scope && context.scope.person && !context.scope.all_teams ? null : h("label", { for: `${view}-person` }, "Person", personInput),
    context.scope && context.scope.person && !context.scope.all_teams ? null : go,
    dateControl(`${view}-date`, "Date", date, (v) => setHashParams(view, { person, date: v })));
  if (!person) {
    host.replaceChildren();
    host.appendChild(h("div", { class: "view-title" }, h("h1", {}, mechanic ? "Mechanic" : "My Day")));
    host.appendChild(controls);
    host.appendChild(stateCard("unknown", "Choose a person to open their day.", [{ code: "PERSON_REQUIRED", message: "Your scope has no person of its own." }]));
    return;
  }
  const env = await frame(host, mechanic ? "Mechanic" : "My Day",
    mechanic ? "Your scope, current duty and certificate readiness. Reporting is a claim, never a check-off." : "Ordered duty, aircraft, crewmates, arrival and OT.",
    `/api/v1/myday${query({ person, date })}`, controls, "No planned duty on this date.");
  if (!env) return;
  const d = env.data;
  const t = d.totals_minutes;
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Person", d.person.name || "—", d.teams.join(", ")),
    metric("First arrival", d.first_arrival ? fmtTime(d.first_arrival) : "—", d.date || ""),
    metric("Work", fmtMinutes(t.WORK), `travel ${fmtMinutes(t.TRAVEL)} · wait ${fmtMinutes(t.WAIT)}`),
    metric("OT", fmtMinutes(t.ot), `regular ${fmtMinutes(t.regular)}`)));
  if (d.planned_dates && d.planned_dates.length) {
    host.appendChild(h("p", { class: "inline-pills" }, "Planned dates: ", d.planned_dates.map((x) => {
      const a = h("a", { href: `#${view}${query({ person, date: x })}`, class: "chip" }, x);
      return [a, " "];
    })));
  }
  host.appendChild(card("Duties", dutyTable(d)));
  if (d.carry_in.length) host.appendChild(card("Carry-in duty", table([
    { key: "kind", label: "Kind" }, { key: "start", label: "From", render: (r) => fmtDateTime(r.start) }, { key: "end", label: "To", render: (r) => fmtDateTime(r.end) },
    { key: "line", label: "Line" }], d.carry_in)));
  if (mechanic) {
    host.appendChild(card("Certificates held", table([
      { key: "cert", label: "Certificate" }, { key: "status", label: "Status", render: (r) => pill(r.status, r.status === "ACTIVE" ? "ok" : "err") },
      { key: "validity", label: "Validity" }, { key: "valid_to", label: "Valid to", render: (r) => fmtDateTime(r.valid_to) },
      { key: "revoked_at", label: "Revoked", render: (r) => (r.revoked_at ? fmtDateTime(r.revoked_at) : "—") }],
    d.certifications, { empty: "No certificate holdings recorded." })));
    host.appendChild(reportForm(d));
  }
}

function reportForm(d) {
  const status = h("div", { class: "command-status" });
  const work = d.duties.filter((x) => x.job && x.phase);
  if (!work.length) return card("Report a completion", h("p", { class: "muted" }, "No planned work to report on this date."));
  const select = h("select", { id: "report-phase" }, work.map((x) => h("option", { value: `${x.job.job_id}|${x.phase.phase_id}|${x.end}` },
    `${x.job.line}/${x.job.soi} · ${x.phase.label} (planned end ${fmtTime(x.end)})`)));
  const notes = h("textarea", { id: "report-notes", maxlength: "400", placeholder: "What you observed (a claim; verification comes from the source)" });
  const send = h("button", { class: "btn btn-primary", type: "button" }, "Report completion (claim)");
  send.addEventListener("click", async () => {
    const [job, phase, end] = select.value.split("|");
    send.disabled = true;
    await submitCommand("/api/v1/reported-observations", { job_id: job, phase_id: phase, kind: "COMPLETED", effective_at: end,
      notes: notes.value.slice(0, 400) }, status, { prefix: "report" });
    send.disabled = false;
  });
  return card("Report a completion", h("p", { class: "muted" }, "A report is recorded as a claim. It never earns credit; only a source-verified completion does."),
    h("label", { for: "report-phase" }, "Work", select), h("label", { for: "report-notes" }, "Notes", notes), send, status);
}

// ---------------------------------------------------------------- 15 Shift Book
async function renderShiftBook(host, context) {
  const p = hashParams();
  const { team, date, shift, controls } = teamShiftControls("shiftbook", context, p);
  if (!team) {
    host.appendChild(stateCard("blocked", "Your scope has no team shift book.", []));
    return;
  }
  const env = await frame(host, "Shift Book", "The team's jobs and full crews for one shift, in order.", `/api/v1/shiftbook${query({ team, date, shift })}`,
    controls, "No accepted assignments for this team-shift.");
  if (!env) return;
  const d = env.data;
  const columns = [
    { label: "Start", value: (r) => r.start }, { label: "End", value: (r) => r.end }, { label: "Line", value: (r) => r.line },
    { label: "SOI", value: (r) => r.soi }, { label: "Work", value: (r) => r.phase_label },
    { label: "Crew", value: (r) => r.crew.map((c) => c.name || "redacted").join("; ") }];
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Assignments", d.assignments.length, d.shift_id), metric("People", d.crew_count, "distinct crew members"),
    metric("Goal B0", d.goal ? fmtMinutes(d.goal.B0) : "—", d.goal ? `${d.goal.units} frozen units` : "no goal of record")));
  host.appendChild(card("Assignments", h("div", { class: "card-actions" }, csvButton("Export CSV", `shiftbook-${team}-${d.shift_id}.csv`, columns, d.assignments)),
    table([
      { key: "start", label: "When", render: (r) => `${fmtTime(r.start)}–${fmtTime(r.end)}` },
      { key: "line", label: "Line", num: true }, { key: "soi", label: "SOI" }, { key: "phase_label", label: "Work" },
      { key: "crew", label: "Full crew", render: (r) => crewList(r.crew) },
      { key: "conditional_reasons", label: "Conditions", render: (r) => reasonList(r.conditional_reasons.concat(r.blocked_reasons)) },
      { key: "committed", label: "Commitment", render: (r) => (r.committed ? pill(r.commitment_outcome || "committed", "info") : "—") },
    ], d.assignments, { caption: "Shift book" })));
  if (d.evaluation) host.appendChild(card("Canonical evaluation of this goal", scoreSummary(d.evaluation, { testid: "shiftbook-score" })));
}

// ---------------------------------------------------------------- 11 Worker Gantt
async function renderGantt(host, context) {
  const p = hashParams();
  const team = p.get("team") || "";
  const date = defaultDate(context, p.get("date"));
  const purpose = p.get("purpose") || "EXECUTABLE";
  const controls = toolbar(
    selectControl("gantt-team", "Team", [{ value: "", label: "All in scope" }].concat(teamOptions(context)), team, (v) => setHashParams("gantt", { team: v, date, purpose })),
    dateControl("gantt-date", "Date", date, (v) => setHashParams("gantt", { team, date: v, purpose })),
    selectControl("gantt-purpose", "Plan", ["EXECUTABLE", "FORECAST"], purpose, (v) => setHashParams("gantt", { team, date, purpose: v })));
  const env = await frame(host, "Worker Gantt", "Every person's work, travel, wait, tool and OT with shift boundaries.",
    `/api/v1/gantt${query({ team, date, purpose })}`, controls, "No people in scope for this date.");
  if (!env) return;
  const d = env.data;
  const rows = d.people.map((r) => ({ label: `${r.person.name || "redacted"} (${r.team})`,
    bars: r.bars.map((b) => ({ start: b.start, end: b.end, kind: b.phase_kind && b.phase_kind.includes("QA") ? "qa" : b.kind, ot: b.ot_minutes > 0,
      text: b.kind === "WORK" ? (b.job || b.label || "work") : b.kind.toLowerCase() })).concat(r.carry_in.map((c) => ({ start: c.start, end: c.end, kind: "other", text: "carry-in" }))) }));
  const from = d.shifts[0].start;
  const to = d.shifts[2].end;
  host.appendChild(legend([["work", "work"], ["qa", "inspection"], ["travel", "travel"], ["wait", "wait"], ["tool", "tool / setup"], ["ot", "overtime"]]));
  host.appendChild(card(`People (${rows.length})`, timeline(rows, from, to, { shifts: d.shifts })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

// ---------------------------------------------------------------- Turnover (supplementary)
async function renderTurnover(host, context) {
  const p = hashParams();
  const { team, date, shift, controls } = teamShiftControls("turnover", context, p);
  if (!team) {
    host.appendChild(stateCard("blocked", "Your scope has no team to hand over.", []));
    return;
  }
  const env = await frame(host, "Turnover", "What this shift leaves open and what the next shift inherits.", `/api/v1/turnover${query({ team, date, shift })}`,
    controls, "Nothing to hand over.");
  if (!env) return;
  const d = env.data;
  host.appendChild(h("div", { class: "metric-grid" },
    metric("Open units", d.open_units.length, d.shift_id), metric("Pending claims", d.pending_claims.length, "awaiting review"),
    metric("Next shift actions", d.next_shift_actions.length, d.next_shift_id), metric("Open QA holds", d.open_quality_holds.length)));
  host.appendChild(card("Open frozen units", table([
    { key: "label", label: "Work" }, { key: "status", label: "Status", render: (r) => pill(r.status, r.status === "MISSED" ? "warn" : "info") },
    { key: "effort", label: "Effort", num: true }, { key: "relief", label: "Relief", render: (r) => (r.relief ? pill("relieved", "info") : "—") }],
  d.open_units, { empty: "Every frozen unit is earned." })));
  host.appendChild(card("Next shift", table([
    { key: "label", label: "Work" }, { key: "start", label: "Window", render: (r) => `${fmtTime(r.start)}–${fmtTime(r.end)}` },
    { key: "crew", label: "Crew", render: (r) => crewList(r.crew) }, { key: "blocked", label: "Blocked by", render: (r) => reasonList(r.blocked) }],
  d.next_shift_actions, { empty: "No planned actions next shift." })));
  if (d.pending_claims.length) host.appendChild(card("Claims awaiting review", table([
    { key: "cause", label: "Cause" }, { key: "units", label: "Units", num: true }, { key: "reported_at", label: "Reported", render: (r) => fmtDateTime(r.reported_at) }],
  d.pending_claims)));
  host.appendChild(card("Open quality holds", table([{ key: "label", label: "Hold" }, { key: "kind", label: "Kind" }, { key: "job", label: "Job" }],
    d.open_quality_holds, { empty: "No open holds." })));
  if (d.carry_in_next_shift.length) host.appendChild(card("Carry-in next shift", table([
    { key: "person", label: "Person", render: (r) => r.person.name || "redacted" }, { key: "kind", label: "Kind" },
    { key: "start", label: "From", render: (r) => fmtDateTime(r.start) }, { key: "end", label: "To", render: (r) => fmtDateTime(r.end) }], d.carry_in_next_shift)));
}

register("team-lead", renderTeamLead);
register("myday", (host, context) => renderMyDay(host, context));
register("mechanic", (host, context) => renderMyDay(host, context, { mechanic: true }));
register("shiftbook", renderShiftBook);
register("gantt", renderGantt);
register("turnover", renderTurnover);
export { dispositionPill };
