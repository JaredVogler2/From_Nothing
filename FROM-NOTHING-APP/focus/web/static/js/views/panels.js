// Supply Chain, Quality, Industrial Engineering, Development (S15 views 6, 4, 12 + quality panel) and the
// Arena and History supplementary panels. Proposals stay proposals; recognition never certifies anyone.
import { fmtDate, fmtDateTime, fmtMinutes, fmtRational, h, metric, pill, table } from "../core.js";
import { card, crewList, frame, reasonList, submitCommand } from "./common.js";
import { register } from "./registry.js";
import { gradePill, statusPill } from "./performance.js";

const READINESS_KIND = { RECEIVED: "ok", RECEIVED_LATE: "warn", ETA_CONDITIONAL: "info", UNKNOWN_ETA: "err", UNLINKED: "err" };

// ---------------------------------------------------------------- 6 Supply Chain
async function renderSupply(host) {
  const env = await frame(host, "Supply Chain", "Shortages: used-on, root and descendant impact; ETA versus confirmed receipt.", "/api/v1/shortages", null,
    "No admitted shortages.");
  if (!env) return;
  const d = env.data;
  host.appendChild(h("div", { class: "metric-grid" }, metric("Shortages", d.shortages.length),
    ...Object.entries(d.by_status).map(([k, v]) => metric(k, v))));
  host.appendChild(card("Shortages", table([
    { key: "record_id", label: "Record", num: true }, { key: "line", label: "Line", num: true }, { key: "used_on", label: "Used on" },
    { key: "root_job", label: "Root", render: (r) => (r.root_job ? r.root_job.label : "unresolved") },
    { key: "affected_jobs", label: "Impact", render: (r) => `${r.affected_jobs.length} jobs (${r.descendants} descendants)` },
    { key: "ecd", label: "ECD", render: (r) => (r.ecd ? fmtDate(r.ecd) : "unknown") },
    { key: "readiness", label: "Receipt", render: (r) => pill(r.readiness, READINESS_KIND[r.readiness] || "muted") },
    { key: "release", label: "Release", render: (r) => (r.release ? `${r.release.status}${r.release.ready_at ? " " + fmtDateTime(r.release.ready_at) : ""}` : "—") },
    { key: "status", label: "Status" }, { key: "reasons", label: "Reasons", render: (r) => reasonList(r.reasons) }], d.shortages, { caption: "Shortages" })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

// ---------------------------------------------------------------- Quality (supplementary)
async function renderQuality(host) {
  const env = await frame(host, "Quality", "Every sourced in-process, final and other buyoff hold with inspector competition.", "/api/v1/quality", null, "No inspection holds.");
  if (!env) return;
  const d = env.data;
  host.appendChild(h("div", { class: "metric-grid" }, metric("Holds", d.counts.total), metric("Scheduled", d.counts.scheduled), metric("Blocked", d.counts.blocked)));
  host.appendChild(card("Inspection holds", table([
    { key: "label", label: "Hold" }, { key: "kind", label: "Kind", render: (r) => pill(r.kind, r.kind === "FINAL_QA" ? "info" : "muted") },
    { key: "work_minutes", label: "Inspector min", num: true }, { key: "status", label: "Status" },
    { key: "assignment", label: "Planned", render: (r) => (r.assignment ? `${fmtDateTime(r.assignment.start)}` : "not placed") },
    { key: "inspectors", label: "Inspectors", render: (r) => (r.assignment ? crewList(r.assignment.inspectors) : "—") },
    { key: "blocked_reasons", label: "Blocked by", render: (r) => reasonList(r.blocked_reasons) }], d.holds, { caption: "Holds" })));
  host.appendChild(card("Inspector competition by shift", table([
    { key: "shift_id", label: "Shift" }, { key: "demand_minutes", label: "Demand min", num: true },
    { key: "inspector_capacity_minutes", label: "Capacity min", num: true }, { key: "inspectors", label: "Inspectors", num: true },
    { key: "load_pct", label: "Load %", num: true, render: (r) => r.load_pct ?? "—" }], d.competition, { empty: "No QA pools." })));
}

// ---------------------------------------------------------------- 4 Industrial Engineering
async function renderIE(host, context) {
  const env = await frame(host, "Industrial Engineering", "Standards, requirements and team authority with provenance; the review queue.", "/api/v1/standards");
  if (!env) return;
  const d = env.data;
  host.appendChild(card(`Review queue (${d.review_queue.length})`, table([
    { key: "kind", label: "Kind", render: (r) => pill(r.kind, "info") }, { key: "subject", label: "Subject" }, { key: "reason", label: "Why" }], d.review_queue,
  { empty: "Nothing awaiting review." })));
  host.appendChild(card("Duration standards", table([
    { key: "job", label: "Job" }, { key: "team", label: "Team" }, { key: "elapsed_minutes", label: "Elapsed", num: true },
    { key: "standard_person_minutes", label: "Std person-min", num: true }, { key: "statistic", label: "Statistic" }, { key: "method", label: "Method" },
    { key: "crew_size", label: "Crew", num: true }, { key: "uncertainty", label: "Uncertainty" },
    { key: "job_id", label: "Propose", render: (r) => (context.identity.grants.includes("IE") ? proposeButton(r) : "—") }], d.durations, { caption: "Durations" })));
  host.appendChild(card("Certificate requirements", table([
    { key: "job", label: "Job" }, { key: "status", label: "Status", render: (r) => pill(r.status, r.status === "UNKNOWN" ? "warn" : "muted") },
    { key: "requirement", label: "Bundle" }, { key: "basis", label: "Basis" }], d.requirements, { caption: "Requirements" })));
  host.appendChild(card("Team authority", table([
    { key: "name", label: "Person", render: (r) => (r.redacted ? "outside your scope" : r.name) }, { key: "effective_team", label: "Effective team" },
    { key: "evidence_count", label: "Evidence", num: true }, { key: "top_share", label: "Top share", num: true, render: (r) => r.top_share ?? "—" },
    { key: "is_tie", label: "Tie", render: (r) => (r.is_tie ? pill("tie", "warn") : "—") }, { key: "effective_basis", label: "Basis" }], d.inference, { caption: "Team inference" })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

function proposeButton(row) {
  const status = h("div", { class: "command-status" });
  const button = h("button", { class: "btn btn-quiet", type: "button" }, "Propose correction");
  button.addEventListener("click", async () => {
    const holder = button.parentElement;
    const text = h("textarea", { maxlength: "2000", placeholder: "Proposed correction" });
    const why = h("textarea", { maxlength: "2000", placeholder: "Reason and evidence" });
    const send = h("button", { class: "btn btn-primary", type: "button" }, "Submit proposal");
    send.addEventListener("click", async () => {
      if (!text.value.trim() || !why.value.trim()) return;
      send.disabled = true;
      await submitCommand("/api/v1/ie-reviews", { job_id: row.job_id, standard_revision: row.stats_id || row.method || "current",
        proposed_correction: text.value.trim(), reason: why.value.trim() }, status, { prefix: "ie" });
      send.disabled = false;
    });
    holder.replaceChildren(text, why, send, status);
  });
  return button;
}

// ---------------------------------------------------------------- 12 Development
async function renderDevelopment(host) {
  const env = await frame(host, "Development", "Auditable proposals, skill gaps and team progression. XP is never certification.", "/api/v1/development", null,
    "No proposals, gaps or progression yet.");
  if (!env) return;
  const d = env.data;
  host.appendChild(card("Skill gaps and training candidates", table([
    { key: "team", label: "Team" }, { key: "shift_id", label: "Shift" }, { key: "requirement", label: "Bundle" },
    { key: "crew_size", label: "Crew size", num: true }, { key: "qualified_admitted", label: "Qualified", num: true },
    { key: "shortfall", label: "Shortfall", num: true, render: (r) => pill(String(r.shortfall), "err") },
    { key: "training_candidates", label: "Partially qualified (training proposal)", render: (r) => (r.training_candidates.length
      ? h("ul", { class: "crew-list" }, r.training_candidates.map((c) => h("li", {}, c.redacted ? "outside your scope" : c.name, h("span", { class: "muted" }, ` holds ${c.holds.join(", ")}`))))
      : "none") }], d.skill_gaps, { empty: "No certificate bundle is short of qualified people." })));
  host.appendChild(card("IE proposals", table([
    { key: "job_id", label: "Job" }, { key: "proposed_correction", label: "Proposal" }, { key: "reason", label: "Reason" },
    { key: "status", label: "Status", render: (r) => pill(r.status, "info") }, { key: "requested_at", label: "Requested", render: (r) => fmtDateTime(r.requested_at) }],
  d.proposals, { empty: "No proposals recorded." })));
  host.appendChild(card("Team progression", table([
    { key: "team", label: "Team" }, { key: "level", label: "Level", num: true }, { key: "xp", label: "XP", num: true, render: (r) => (r.xp ? fmtRational(`${r.xp.num}/${r.xp.den}`, 1) : "0") },
    { key: "badges", label: "Badges", render: (r) => r.badges.map((b) => [pill(b.revoked ? `${b.badge} (revoked)` : b.badge, b.revoked ? "muted" : "info"), " "]) },
    { key: "qualification_effect", label: "Qualification effect" }], d.progression, { empty: "No progression yet." })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

// ---------------------------------------------------------------- Arena (supplementary)
async function renderArena(host) {
  const env = await frame(host, "Arena & Recognition", "Team standings from stored evaluations (pooled exactly), XP and badges.", "/api/v1/arena", null, "No evaluated goals yet.");
  if (!env) return;
  const d = env.data;
  host.appendChild(card("Standings", table([
    { key: "position", label: "#", num: true, render: (r) => r.position ?? "—" }, { key: "team", label: "Team" },
    { key: "pooled", label: "Pooled score", num: true, render: (r) => (r.pooled ? [r.pooled.score ?? "—", " ", gradePill(r.pooled.grade), " ", statusPill(r.pooled.status)] : "unknown") },
    { key: "goals_evaluated", label: "Goals", num: true },
    { key: "level", label: "Level", num: true, render: (r) => r.level ?? "—" },
    { key: "badges", label: "Badges", render: (r) => r.badges.map((b) => [pill(b.badge, b.revoked ? "muted" : "info"), " "]) }], d.standings, { caption: "Arena standings" })));
  host.appendChild(card("Recent recognition", table([
    { key: "created_at", label: "When", render: (r) => fmtDateTime(r.created_at) }, { key: "team", label: "Team" }, { key: "type", label: "Event" },
    { key: "value", label: "XP", num: true, render: (r) => (r.value ? fmtRational(`${r.value.num}/${r.value.den}`, 1) : "0") },
    { key: "evidence", label: "Lineage", render: (r) => h("code", {}, r.evidence.slice(0, 2).join(" · ")) }], d.recent_events, { empty: "No recognition yet." })));
  host.appendChild(h("p", { class: "muted" }, d.note));
}

// ---------------------------------------------------------------- History (supplementary)
async function renderHistory(host) {
  const env = await frame(host, "History", "Accepted plans, evaluation revisions, corrections and delivery changes. History is immutable.", "/api/v1/history", null,
    "No plan has been accepted yet.");
  if (!env) return;
  const d = env.data;
  host.appendChild(card("Accepted plans", table([
    { key: "accepted_at", label: "Accepted", render: (r) => fmtDateTime(r.accepted_at) }, { key: "purpose", label: "Purpose" },
    { key: "capability", label: "Capability" }, { key: "current", label: "Current", render: (r) => (r.current ? pill("current", "ok") : pill("history", "muted")) },
    { key: "head_version", label: "Head version", num: true }, { key: "assignments", label: "Assignments", num: true },
    { key: "frozen_goals", label: "Frozen goals", num: true }, { key: "plan_id", label: "Plan" }], d.plans, { caption: "Accepted plans" })));
  host.appendChild(card("Evaluation revisions", table([
    { key: "team", label: "Team" }, { key: "shift_id", label: "Shift" },
    { key: "revisions", label: "Revisions (oldest first)", render: (r) => r.revisions.map((e) => [pill(`r${e.revision} ${e.score ?? e.status}`, e.revision === r.revisions.length ? "info" : "muted"), " "]) }],
  d.evaluation_series, { empty: "No evaluations yet." })));
  host.appendChild(card("Corrections", table([
    { key: "created_at", label: "When", render: (r) => fmtDateTime(r.created_at) }, { key: "team", label: "Team" },
    { key: "value", label: "XP", num: true, render: (r) => fmtRational(`${r.value.num}/${r.value.den}`, 1) }, { key: "supersedes", label: "Reverses" }],
  d.corrections, { empty: "No corrections." })));
  host.appendChild(card("Delivery changes", table([
    { key: "line", label: "Line", num: true }, { key: "kind", label: "Change" }, { key: "from_date", label: "From", render: (r) => fmtDate(r.from_date) },
    { key: "to_date", label: "To", render: (r) => fmtDate(r.to_date) }, { key: "detail", label: "Detail" }], d.delivery_changes, { empty: "No delivery changes." })));
}

register("supply", (host) => renderSupply(host));
register("quality", (host) => renderQuality(host));
register("ie", renderIE);
register("development", (host) => renderDevelopment(host));
register("arena", (host) => renderArena(host));
register("history", (host) => renderHistory(host));
export { fmtMinutes };
