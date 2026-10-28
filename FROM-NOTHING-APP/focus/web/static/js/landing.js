// Landing page: source/plan readiness summary from GET /api/v1/context.
import { api, h, mount, fmtDateTime, setCsrf } from "./core.js";

function card(label, value, note, state) {
  return h("div", { class: `metric card state-${state}` },
    h("div", { class: "metric-label" }, label),
    h("div", { class: "metric-value" }, value),
    note ? h("div", { class: "metric-note" }, note) : null);
}

async function main() {
  const host = document.getElementById("readiness");
  const meta = document.getElementById("header-meta");
  try {
    const env = await api("/api/v1/context");
    const data = env.data;
    setCsrf(data.csrf_token);
    const source = data.source;
    const plan = data.plans ? data.plans.EXECUTABLE : null;
    mount(host,
      card("Source run", source ? source.run_id : "none", source ? `admitted ${fmtDateTime(source.updated_at)}` : "No admitted source run", source ? "ready" : "unknown"),
      card("Accepted executable plan", plan ? plan.plan_id : "none", plan ? `accepted ${fmtDateTime(plan.updated_at)}` : "No accepted plan yet", plan ? "ready" : "unknown"),
      card("Policy", data.policy.policy_id || "unavailable", data.policy.status === "ready" ? `hash ${data.policy.hash.slice(0, 12)}…` : data.policy.reason, data.policy.status === "ready" ? "ready" : "blocked"));
    host.setAttribute("aria-busy", "false");
    meta.textContent = `${data.mode} mode · ${data.platform} · scope ${data.scope_key}` + (data.identity.demo ? " · DEMO" : "");
  } catch (err) {
    mount(host, card("Context", "error", err.message, "error"));
    host.setAttribute("aria-busy", "false");
    meta.textContent = "Context unavailable";
  }
}

main();
