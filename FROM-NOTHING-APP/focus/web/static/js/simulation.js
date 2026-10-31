// Fleet simulation dashboard: one page, ten modules over the read-only /api/v1/sim/* operations (synthetic data).
import { api, h, mount, loadingCard, errorCard, metric, table, stateCard, setCsrf } from "./core.js";
import { barChart, lineChart, stackedBars, heatmap, gantt, PALETTE, fmtShort } from "./charts.js";

const TABS = [
  ["overview", "Fleet overview"], ["economics", "Economics"], ["aircraft", "Aircraft"], ["performance", "Shift performance"],
  ["dispatch", "Dispatch"], ["gantt", "Worker Gantt"], ["capacity", "Capacity"], ["supply", "Supply chain"],
  ["methods", "How the plan was made"], ["profile", "Data profile"],
];
const FULL = 1200, HALF = 590;                 // chart widths in SVG units: about one unit per pixel in a full or half card
const host = document.getElementById("main");
const bar = document.getElementById("sim-bar");
const tabs = document.getElementById("sim-tabs");
const ui = { tab: "overview", shift: null, shifts: [], team: "T01", line: null, teams: [] };
let token = 0;

const money = (v) => (v === null || v === undefined ? "—" : "$" + fmtShort(v));
const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 1000) / 10 + "%");
const num = (v, d = 0) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));
const signed = (v, f = num) => (v === null || v === undefined ? "" : (v > 0 ? "+" : "") + f(v));
const when = (iso) => (iso ? iso.replace("T", " ").slice(5, 16) + "Z" : "—");
const PRIOR = { KEPT: "kept", CREW_CHANGED: "kept, new crew", UNSAFE: "moved: prior slot no longer safe", PENDING_MOVE: "moved", MOVED: "moved" };
const customerColor = new Map();
function colorFor(key) {
  if (!customerColor.has(key)) customerColor.set(key, PALETTE[customerColor.size % PALETTE.length]);
  return customerColor.get(key);
}
function q(params) {
  const parts = Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
  return parts.length ? "?" + parts.join("&") : "";
}
function card(title, ...children) {
  return h("section", { class: "card" }, h("div", { class: "card-header" }, h("h2", {}, title)), ...children);
}
function chartBox(node, note) {
  return h("figure", { class: "chart-box" }, node, note ? h("figcaption", { class: "muted" }, note) : null);
}
function deltaNote(value, fmt = num, betterHigher = true) {
  if (value === null || value === undefined || value === 0) return "no change vs prior";
  const good = betterHigher ? value > 0 : value < 0;
  return h("span", { class: good ? "delta-good" : "delta-bad" }, `${signed(value, fmt)} vs prior`);
}

// ------------------------------------------------------------------ shell
function renderTabs() {
  mount(tabs, TABS.map(([id, label]) => h("a", { href: "#" + id, class: "chip", "aria-selected": ui.tab === id ? "true" : "false" }, label)));
}

function renderBar() {
  const select = h("select", { id: "shift-select", "aria-label": "Shift", onchange: (e) => { ui.shift = Number(e.target.value); route(); } },
    ui.shifts.map((s) => h("option", { value: s.shift_no, selected: s.shift_no === ui.shift ? true : null },
      `${s.production_day} S${s.shift_number} (week ${s.week})${s.valid ? "" : " — INVALID"}`)));
  const prev = h("button", { class: "btn btn-quiet", onclick: () => { if (ui.shift > 0) { ui.shift -= 1; route(); } } }, "◀ Prior shift");
  const next = h("button", { class: "btn btn-quiet", onclick: () => { if (ui.shift < ui.shifts.length - 1) { ui.shift += 1; route(); } } }, "Next shift ▶");
  mount(bar, h("div", { class: "toolbar" }, h("label", {}, "Published shift", select), prev, next,
    h("span", { class: "muted" }, "Synthetic fleet simulation — every number comes from the simulation tables.")));
}

async function route() {
  const t = ++token;
  ui.tab = (location.hash || "#overview").slice(1) || "overview";
  renderTabs();
  if (ui.shifts.length) renderBar();
  mount(host, loadingCard());
  const body = h("div", { class: "view-body" });
  try {
    await (RENDER[ui.tab] || RENDER.overview)(body);
  } catch (err) {
    mount(body, errorCard(err));
  }
  if (t === token) mount(host, body);
}

async function get(path, extra = {}) {
  const env = await api(`/api/v1/sim/${path}${q({ shift: ui.shift, ...extra })}`);
  if (env.status === "empty") {
    throw Object.assign(new Error((env.reasons[0] || {}).message || "no simulation"), { code: "SIMULATION_NOT_RUN", status: 404, next: (env.reasons[0] || {}).next_action });
  }
  return env.data;
}

// ------------------------------------------------------------------ modules
const RENDER = {
  async overview(body) {
    const d = await get("overview");
    const k = d.kpis || {}, dl = d.delta || {};
    const s = ui.shifts[ui.shift] || {};
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Fleet overview"),
        h("p", { class: "muted" }, `Published plan for ${s.production_day} shift ${s.shift_number} (${d.purpose.toLowerCase()} plan, ${num(d.fleet.length)} aircraft). ` +
          "Each shift's plan is a static snapshot; the trend shows how it changed shift by shift.")),
      h("div", { class: "metric-grid" },
        metric("Projected penalty", money(k.projected_penalty_usd), deltaNote(dl.projected_penalty_usd, money, false)),
        metric("Late aircraft", num(k.late_aircraft), deltaNote(dl.late_aircraft, num, false)),
        metric("Mean days late", num(k.mean_late_days, 2), deltaNote(dl.mean_late_days, (v) => num(v, 2), false)),
        metric("Unknown completion", num(k.unknown_completion), "an undated part blocks the rest of the aircraft"),
        metric("Open jobs", num(k.open_jobs), deltaNote(dl.open_jobs, num, false)),
        metric("Tasks done this shift", `${num(k.completed_tasks)} of ${num(k.planned_tasks)}`, k.adherence !== undefined ? pct(k.adherence) + " of planned" : null),
        metric("Utilization", pct(k.utilization), "labor hours over present people's regular hours"),
        metric("Fleet score", num(k.score, 1), deltaNote(dl.score, (v) => num(v, 1)))),
      card("Days late by aircraft", chartBox(barChart(d.fleet.map((f) => ({ label: String(f.line), value: f.late_days || 0,
        color: colorFor(`${f.customer} 787-${f.minor_model}`), note: `${f.customer} 787-${f.minor_model}, ${money(f.cost_usd)} projected` })),
        { title: "Projected days late per aircraft", yLabel: "days late", rotate: true, width: FULL }),
        "Bar color = customer and minor model. Hover for the projected penalty.")),
      h("div", { class: "grid grid-2" },
        card("Projected penalty by shift", chartBox(lineChart([{ name: "Projected penalty", points: d.trend.map((t) => ({ x: t.label, y: t.penalty_usd })) }],
          { title: "Fleet projected penalty per published plan", fmt: (v) => "$" + fmtShort(v), width: HALF, height: 300 }))),
        card("Late aircraft and work done", chartBox(lineChart([
          { name: "Late aircraft", points: d.trend.map((t) => ({ x: t.label, y: t.late_aircraft })) },
          { name: "Tasks done (÷10)", points: d.trend.map((t) => ({ x: t.label, y: t.completed === null ? null : t.completed / 10 })) }],
          { title: "Late aircraft and tasks finished per shift", width: HALF, height: 300 })))),
      card("Every aircraft", table([
        { label: "Line", render: (r) => h("a", { href: "#aircraft", onclick: () => { ui.line = r.line; } }, String(r.line)) },
        { label: "Customer", key: "customer" }, { label: "Model", render: (r) => "787-" + r.minor_model }, { label: "Station", key: "station" },
        { label: "Due", render: (r) => when(r.due_utc) }, { label: "Projected", render: (r) => when(r.completion_utc) },
        { label: "Days late", num: true, render: (r) => num(r.late_days, 1) }, { label: "Daily penalty", num: true, render: (r) => money(r.daily_penalty_usd) },
        { label: "Projected cost", num: true, render: (r) => money(r.cost_usd) }, { label: "Open jobs", num: true, render: (r) => num(r.jobs_open) },
        { label: "Late parts", num: true, render: (r) => num(r.shortages_open) }, { label: "Status", key: "status" }], d.fleet)));
  },

  async economics(body) {
    const d = await get("overview");
    const fleet = [...d.fleet].sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0));
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Economics"),
        h("p", { class: "muted" }, "Lateness is priced per customer and minor model (ticket-revenue proxy). The planner orders work by cost of delay: the aircraft's daily penalty, reduced by the task's slack.")),
      h("div", { class: "grid grid-2" },
        card("Projected penalty by customer and model", chartBox(barChart(d.by_customer.map((c) => ({ label: c.customer_model, value: c.penalty_usd, color: colorFor(c.customer_model),
          note: `${c.aircraft} aircraft, ${money(c.daily_penalty_usd)}/day` })), { title: "Projected penalty by customer and minor model", rotate: true, fmt: (v) => "$" + fmtShort(v), width: HALF, height: 320 }))),
        card("Daily penalty rate (cost of one day late)", chartBox(barChart(d.by_customer.map((c) => ({ label: c.customer_model, value: c.daily_penalty_usd, color: colorFor(c.customer_model) })),
          { title: "Daily penalty per customer and minor model", rotate: true, fmt: (v) => "$" + fmtShort(v), width: HALF, height: 320 })))),
      card("Costliest aircraft in this plan", table([
        { label: "Line", key: "line" }, { label: "Customer", render: (r) => `${r.customer} 787-${r.minor_model}` },
        { label: "Days late", num: true, render: (r) => num(r.late_days, 1) }, { label: "Daily penalty", num: true, render: (r) => money(r.daily_penalty_usd) },
        { label: "Projected cost", num: true, render: (r) => money(r.cost_usd) }], fleet.slice(0, 15))));
  },

  async aircraft(body) {
    const d = await get("aircraft", { line: ui.line });
    ui.line = d.line;
    const f = d.facts || {};
    const pick = h("select", { "aria-label": "Aircraft", onchange: (e) => { ui.line = Number(e.target.value); route(); } },
      d.lines.map((l) => h("option", { value: l, selected: l === d.line ? true : null }, String(l))));
    const hist = d.history.filter((x) => x.purpose === (d.history.find((y) => y.purpose === "EXECUTABLE") ? "EXECUTABLE" : d.history[0] && d.history[0].purpose));
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, `Aircraft ${d.line}`), h("p", { class: "muted" }, `${f.customer || ""} 787-${f.minor_model || ""}, station ${f.station || "—"}`)),
      h("div", { class: "toolbar" }, h("label", {}, "Aircraft", pick)),
      h("div", { class: "metric-grid" },
        metric("Due", when(f.due_utc)), metric("Daily penalty", money(f.daily_penalty_usd)), metric("Open jobs", num(f.jobs_open)),
        metric("Done jobs", num(f.jobs_done)), metric("Open late parts", num(f.shortages_open)), metric("Open rework", num(f.rework_open))),
      card("Projected days late, plan by plan", chartBox(lineChart([{ name: "Days late", points: hist.map((x) => ({ x: "S" + x.shift_no, y: x.late_days })) }],
        { title: "Projected days late in each published plan", width: FULL, height: 240 }))),
      h("div", { class: "grid grid-2" },
        card("Open work by station and team", table([{ label: "Station", key: "station" }, { label: "Team", key: "team" },
          { label: "Open jobs", num: true, key: "open_jobs" }, { label: "Work min", num: true, render: (r) => num(r.work_minutes) },
          { label: "Late parts", num: true, key: "short" }], d.stations)),
        card("Done and slipped per shift", chartBox(stackedBars(d.execution.map((e) => ({ label: "S" + e.shift_no,
          parts: [{ name: "done", value: e.completed, color: "#2b8a3e" }, { name: "slipped", value: e.slipped, color: "#c92a2a" }] })),
          { title: "Tasks done and slipped per shift", names: [{ name: "done", color: "#2b8a3e" }, { name: "slipped", color: "#c92a2a" }], width: HALF, height: 300 })))),
      card("Open part shortages", table([{ label: "Task", key: "soi" }, { label: "Station", key: "station" }, { label: "Team", key: "team" },
        { label: "Expected", key: "shortage_ecd" }, { label: "Station opens", render: (r) => when(r.cs_start) }], d.shortages)));
  },

  async performance(body) {
    const d = await get("performance");
    const c = d.compare;
    const row = (label, x) => {
      const cur = x.current || {}, dl = x.delta || {};
      return { label, score: cur.score, ds: dl.score, adherence: cur.adherence, da: dl.adherence, on_time: cur.on_time_rate, dot: dl.on_time_rate,
        util: cur.utilization, du: dl.utilization, done: cur.completed, planned: cur.planned };
    };
    const rows = [row(`This shift vs prior shift`, c.shift), row(`This day (${d.day}) vs prior day`, c.day), row(`Week ${d.week} vs prior week`, c.week),
      row("Whole simulation", c.overall)];
    const slipNames = [["slipped_part", "part", "#e8590c"], ["slipped_absence", "absence", "#ae3ec9"], ["slipped_predecessor", "predecessor", "#1098ad"], ["slipped_overrun", "overrun", "#c92a2a"]];
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Shift performance"),
        h("p", { class: "muted" }, d.scoring)),
      card("Comparisons", table([{ label: "Period", key: "label" },
        { label: "Score", num: true, render: (r) => [num(r.score, 1), " ", h("span", { class: r.ds >= 0 ? "delta-good" : "delta-bad" }, r.ds === undefined ? "" : signed(r.ds, (v) => num(v, 1)))] },
        { label: "Finished", num: true, render: (r) => [pct(r.adherence), " ", h("span", { class: r.da >= 0 ? "delta-good" : "delta-bad" }, r.da === undefined ? "" : signed(r.da * 100, (v) => num(v, 1) + "pt"))] },
        { label: "On time", num: true, render: (r) => [pct(r.on_time), " ", h("span", { class: r.dot >= 0 ? "delta-good" : "delta-bad" }, r.dot === undefined ? "" : signed(r.dot * 100, (v) => num(v, 1) + "pt"))] },
        { label: "Utilization", num: true, render: (r) => [pct(r.util), " ", h("span", { class: "muted" }, r.du === undefined ? "" : signed(r.du * 100, (v) => num(v, 1) + "pt"))] },
        { label: "Tasks", num: true, render: (r) => `${num(r.done)} / ${num(r.planned)}` }], rows)),
      h("div", { class: "grid grid-2" },
        card("Score and finish rate by shift", chartBox(lineChart([
          { name: "Score", points: d.shifts.map((s) => ({ x: s.label, y: s.score })) },
          { name: "Finished %", points: d.shifts.map((s) => ({ x: s.label, y: s.adherence === null ? null : s.adherence * 100 })) },
          { name: "On time %", points: d.shifts.map((s) => ({ x: s.label, y: s.on_time_rate === null ? null : s.on_time_rate * 100 })) }],
          { title: "Fleet score, finish and on-time rates per shift", yMax: 100, width: HALF, height: 300 }))),
        card("Why tasks slipped", chartBox(stackedBars(d.shifts.map((s) => ({ label: s.label, parts: slipNames.map(([k, n, col]) => ({ name: n, value: s[k], color: col })) })),
          { title: "Slipped tasks per shift by cause", names: slipNames.map(([, n, col]) => ({ name: n, color: col })), width: HALF, height: 300 })))),
      h("div", { class: "grid grid-2" },
        card("By day", chartBox(barChart(d.days.map((x) => ({ label: x.day.slice(5), value: x.score, note: `${num(x.completed)} of ${num(x.planned)} tasks` })),
          { title: "Fleet score per production day", width: HALF, height: 300, yLabel: "score" }))),
        card("Week against week", table([{ label: "Week", key: "week" }, { label: "Score", num: true, render: (r) => num(r.score, 1) },
          { label: "Finished", num: true, render: (r) => pct(r.adherence) }, { label: "On time", num: true, render: (r) => pct(r.on_time_rate) },
          { label: "Utilization", num: true, render: (r) => pct(r.utilization) }, { label: "Tasks", num: true, render: (r) => `${num(r.completed)} / ${num(r.planned)}` }], d.weeks))),
      card("Teams this shift", table([{ label: "Team", key: "team" }, { label: "Grade", key: "grade" }, { label: "Score", num: true, render: (r) => num(r.score, 1) },
        { label: "vs prior", num: true, render: (r) => (r.score_delta === null ? "—" : h("span", { class: r.score_delta >= 0 ? "delta-good" : "delta-bad" }, signed(r.score_delta, (v) => num(v, 1)))) },
        { label: "Planned", num: true, key: "planned" }, { label: "Done", num: true, key: "completed" }, { label: "On time", num: true, key: "on_time" },
        { label: "Part", num: true, key: "slipped_part" }, { label: "Absence", num: true, key: "slipped_absence" }, { label: "Pred.", num: true, key: "slipped_predecessor" },
        { label: "Overrun", num: true, key: "slipped_overrun" }, { label: "Utilization", num: true, render: (r) => pct(r.utilization) },
        { label: "Present", num: true, key: "present" }], d.teams)));
  },

  async dispatch(body) {
    const d = await get("dispatch", { team: ui.team });
    ui.teams = d.teams.map((t) => t.team);
    const pick = h("select", { "aria-label": "Team", onchange: (e) => { ui.team = e.target.value; route(); } },
      ui.teams.map((t) => h("option", { value: t, selected: t === ui.team ? true : null }, t)));
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Dispatch (shift book)"),
        h("p", { class: "muted" }, `The static snapshot published for ${d.window.production_day} shift ${d.window.shift_number}: what each team works, on which aircraft, with which crew. Outcomes come from the simulated execution.`)),
      card("Teams this shift", chartBox(barChart(d.teams.map((t) => ({ label: t.team, value: t.labor_hours, note: `${t.tasks} tasks on ${t.aircraft} aircraft` })),
        { title: "Planned labor hours per team in the published shift", yLabel: "labor hours", rotate: true, width: FULL }))),
      h("div", { class: "toolbar" }, h("label", {}, "Team", pick)),
      card(`${ui.team} task list`, table([{ label: "Start", render: (r) => when(r.start_utc) }, { label: "End", render: (r) => when(r.end_utc) },
        { label: "Aircraft", key: "line" }, { label: "Task", key: "soi" }, { label: "Kind", key: "kind" }, { label: "Crew", render: (r) => r.crew.join(", ") },
        { label: "Prior plan", render: (r) => (r.committed ? PRIOR[r.commitment_outcome] || r.commitment_outcome || "committed" : "new") },
        { label: "Conditional", render: (r) => (r.conditional ? "part ETA" : "") },
        { label: "Outcome", render: (r) => (r.outcome ? (r.outcome === "completed" ? "done" : `slipped (${r.cause})`) : "—") }], d.tasks, { empty: "No tasks for this team in the shift." })));
  },

  async gantt(body) {
    const d = await get("gantt", { team: ui.team });
    if (!ui.teams.length) ui.teams = Array.from({ length: 30 }, (_, i) => "T" + String(i + 1).padStart(2, "0")).concat(["QA"]);
    const pick = h("select", { "aria-label": "Team", onchange: (e) => { ui.team = e.target.value; route(); } },
      ui.teams.map((t) => h("option", { value: t, selected: t === ui.team ? true : null }, t)));
    const t0 = Date.parse(d.window.start_utc) / 60000, t1 = Date.parse(d.window.end_utc) / 60000;
    const byPerson = new Map(d.people.map((p) => [p.person_id, { label: p.display, items: [] }]));
    d.duties.forEach((x) => {
      const lane = byPerson.get(x.person);
      if (lane) lane.items.push({ start: Date.parse(x.start_utc) / 60000, end: Date.parse(x.end_utc) / 60000, kind: x.kind,
        label: x.kind === "WORK" && x.aircraft_to ? String(x.aircraft_to) : null, note: `${x.kind} aircraft ${x.aircraft_to || "—"} ${when(x.start_utc)}–${when(x.end_utc)}` });
    });
    const lanes = Array.from(byPerson.values());
    const label = (m) => new Date(m * 60000).toISOString().slice(11, 16);
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Worker Gantt (My Day)"), h("p", { class: "muted" }, "Each person's published duties in the shift: work (blue, labelled with the aircraft), transfer between aircraft (orange), wait (gray). Times in UTC.")),
      h("div", { class: "toolbar" }, h("label", {}, "Team", pick)),
      card(`${d.team} — ${lanes.length} people`, chartBox(gantt(lanes, { title: `${d.team} duties in the published shift`, t0, t1, tickLabel: label, width: FULL }))));
  },

  async capacity(body) {
    const d = await get("capacity");
    const teams = [...new Set(d.cells.map((c) => c.team))].sort();
    const cols = [...new Set(d.cells.map((c) => `${c.day.slice(5)} S${c.shift_number}`))].sort();
    const cells = d.cells.map((c) => ({ row: c.team, col: `${c.day.slice(5)} S${c.shift_number}`, value: c.load, band: c.band, label: `${num(c.planned_hours, 1)} of ${num(c.available_hours, 1)} h, ${c.members} people` }));
    const legend = d.bands.map((b) => `${b.band}: ${b.label}`).join(" · ");
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Capacity and staffing"), h("p", { class: "muted" }, `Planned labor hours against each team's pool (members × regular hours), from the published shift through the next three production days. Bands, set by the server: ${legend}.`)),
      card("Load by team and shift", chartBox(heatmap(teams, cols, cells, { title: "Planned load per team and shift", width: FULL }),
        d.idle_support_pools.length ? `Support pools with no planned work in these days are not shown: ${d.idle_support_pools.join(", ")}. Hover a cell for hours and people.` : "Hover a cell for hours and people.")),
      card("Actual utilization over the simulation", chartBox(barChart(d.actual.map((a) => ({ label: a.team, value: a.available ? a.actual / a.available : 0 })),
        { title: "Labor hours worked over present people's regular hours", fmt: (v) => Math.round(v * 100) + "%", rotate: true, width: FULL }))));
  },

  async supply(body) {
    const d = await get("supply");
    const shifts = [...new Set(d.events.map((e) => e.shift_no))].sort((a, b) => a - b);
    const kinds = [["part_arrived", "arrived", "#2b8a3e"], ["part_slipped", "slipped", "#c92a2a"], ["shortage_found_undated", "found undated", "#ae3ec9"], ["shortage_dated", "dated", "#1098ad"]];
    const ev = new Map(d.events.map((e) => [e.shift_no + "|" + e.kind, e.n]));
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Supply chain"), h("p", { class: "muted" }, "Open part shortages in the shift's snapshot. A task cannot start before its part's expected date; an undated part blocks the task and everything after it.")),
      h("div", { class: "grid grid-2" },
        card("Open shortages by aircraft", chartBox(barChart(d.by_aircraft.map((a) => ({ label: String(a.line), value: a.open_shortages, color: colorFor(`${a.customer} 787-${a.minor_model}`), note: `${a.undated} undated` })),
          { title: "Open part shortages per aircraft", rotate: true, width: HALF, height: 300 }))),
        card("When open parts are expected", chartBox(barChart(d.spread.map((s) => ({ label: s.days_out + "d", value: s.n })), { title: "Open shortages by days until expected", width: HALF, height: 300 }),
          "Days from the shift's cutoff until each open shortage's expected date."))),
      h("div", { class: "grid grid-2" },
        card("Part events per shift", chartBox(stackedBars(shifts.map((s) => ({ label: "S" + s, parts: kinds.map(([k, n, col]) => ({ name: n, value: ev.get(s + "|" + k) || 0, color: col })) })),
          { title: "Part arrivals, slips and datings per shift", names: kinds.map(([, n, col]) => ({ name: n, color: col })), width: HALF, height: 300 }))),
        card("Tasks the floor could not start for a part", d.blocked.some((b) => b.n > 0)
          ? chartBox(barChart(d.blocked.map((b) => ({ label: "S" + b.shift_no, value: b.n, color: "#e8590c" })), { title: "Tasks blocked by a missing part per shift", width: HALF, height: 300 }))
          : stateCard("empty", "No published task found its part missing.", [{ code: "NO_PART_SLIPS", message: "Every published start was at or after its part's expected date, and no part slipped after the plan was published.", next_action: "Late arrivals show as slips in the chart beside this one." }]))));
  },

  async methods(body) {
    const d = await get("methods");
    const r = d.run || {};
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "How this plan was made"), h("p", { class: "muted" }, "Data pulled, the admitted snapshot, every optimizer stage, accepted search moves, the independent validator's checks and each step's time.")),
      h("div", { class: "metric-grid" },
        metric("Plan valid", r.valid ? "yes" : "NO"), metric("Engine", r.engine || "—"), metric("Stopped on", r.stop_reason || "—", "move bound = repeatable"),
        metric("Search moves", num(r.moves)), metric("Load", `${num(r.load_s, 1)} s`), metric("Plan + validate", `${num(r.plan_s, 1)} s`),
        metric("Execute", `${num(r.execute_s, 1)} s`), metric("Peak memory", `${num(r.rss_mb)} MB`)),
      card("Decision digest", h("p", { class: "mono" }, r.decision_digest || "—"), h("p", { class: "muted" }, "Same inputs give the same digest: it covers every assignment, person, time, disposition, completion and cost.")),
      h("div", { class: "grid grid-2" },
        card("Data pulled for this shift", table([{ label: "Table", key: "table" }, { label: "Rows written", num: true, render: (x) => num(x.written) }, { label: "Rows removed", num: true, render: (x) => num(x.removed) }], d.pulled)),
        card("Admitted snapshot", table([{ label: "Item", key: "item" }, { label: "Count", num: true, render: (x) => num(x.value) }], d.counts))),
      card("Optimizer stages", chartBox(barChart(d.stages.map((s) => ({ label: s.stage, value: s.runtime_ms / 1000, note: s.note })), { title: "Seconds per optimizer stage", yLabel: "seconds", width: FULL, height: 240 })),
        table([{ label: "Purpose", key: "purpose" }, { label: "Stage", key: "stage" }, { label: "ms", num: true, render: (s) => num(s.runtime_ms) },
          { label: "Candidates", num: true, key: "candidates" }, { label: "Accepted", num: true, key: "accepted" }, { label: "Note", key: "note" }], d.stages)),
      h("div", { class: "grid grid-2" },
        card("Accepted search moves", table([{ label: "#", key: "seq" }, { label: "Move", key: "move" }], d.moves, { empty: "No move improved the plan this shift." })),
        card("Independent validation", table([{ label: "Check", key: "check_name" }, { label: "Checked", num: true, render: (x) => num(x.count) }, { label: "Violations", num: true, key: "violations" }], d.checks))));
  },

  async profile(body) {
    const d = await get("profile");
    const f = d.facts || {};
    const stations = new Map();
    d.stations.forEach((s) => { const k = s.station; const v = stations.get(k) || { done: 0, open: 0 }; if (s.status === "COMPLETE") v.done += s.n; else v.open += s.n; stations.set(k, v); });
    mount(body,
      h("div", { class: "view-title" }, h("h1", {}, "Data profile"), h("p", { class: "muted" }, "What the synthetic fleet was built from: every pulled table, the work by station and team, the customer mix, late parts and rework.")),
      h("div", { class: "metric-grid" },
        metric("Mechanics per shift", num(f.mechanics_per_shift)), metric("QA per shift", num(f.qa_per_shift)), metric("Task length scale", num(f.scale, 3), "calibrated to 90 % utilization"),
        metric("Late parts (open)", num(Object.entries(f.late_parts || {}).filter(([k]) => k !== "history").reduce((a, [, v]) => a + v, 0))),
        metric("Late parts (history)", num((f.late_parts || {}).history)), metric("Planning horizon to", f.calendar_to || "—")),
      h("div", { class: "grid grid-2" },
        card("Tasks by control station", chartBox(stackedBars([...stations.entries()].sort().map(([k, v]) => ({ label: k, parts: [{ name: "done", value: v.done, color: "#2b8a3e" }, { name: "open", value: v.open, color: "#1764df" }] })),
          { title: "Tasks per control station at the start", names: [{ name: "done", color: "#2b8a3e" }, { name: "open", color: "#1764df" }], width: HALF, height: 300 }))),
        card("Open work, rework and late parts by shift", chartBox(lineChart([
          { name: "Open jobs (÷10)", points: d.rework.map((x) => ({ x: "S" + x.shift_no, y: x.jobs_open / 10 })) },
          { name: "Open rework", points: d.rework.map((x) => ({ x: "S" + x.shift_no, y: x.rework_open })) },
          { name: "Open late parts", points: d.rework.map((x) => ({ x: "S" + x.shift_no, y: x.shortages_open })) }], { title: "Open jobs, rework and late parts per shift", width: HALF, height: 300 })))),
      h("div", { class: "grid grid-2" },
        card("Customer and minor-model mix", table([{ label: "Customer", key: "customer" }, { label: "Model", render: (x) => "787-" + x.minor_model },
          { label: "Aircraft", num: true, key: "aircraft" }, { label: "Daily penalty", num: true, render: (x) => money(x.daily_penalty_usd) }], d.mix)),
        card("Pulled tables", table([{ label: "Table", key: "table" }, { label: "Initial rows", num: true, render: (x) => num(x.initial_rows) },
          { label: "Changes during the simulation", num: true, render: (x) => num(x.later_changes) }], d.sizes))),
      card("Tasks by team", chartBox(barChart(d.teams.map((t) => ({ label: t.team, value: t.tasks })), { title: "Tasks per team at the start", rotate: true, width: FULL }))));
  },
};

async function main() {
  try {
    const ctx = await api("/api/v1/context");
    setCsrf(ctx.data.csrf_token);
    if (!ctx.data.identity.authenticated) {
      mount(host, stateCard("blocked", "Sign in to see the simulation.", [{ code: "UNAUTHENTICATED", message: "No trusted identity for this request.", next_action: "Use the landing page demo sign-in." }]));
      return;
    }
    const env = await api("/api/v1/sim/overview");
    if (env.status === "empty") {
      mount(host, stateCard("empty", "No simulation has been run yet.", env.reasons));
      return;
    }
    ui.shifts = env.data.shifts;
    ui.shift = env.data.shift;
  } catch (err) {
    mount(host, errorCard(err));
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}

main();
