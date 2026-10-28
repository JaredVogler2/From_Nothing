// Workspace controller shared by the iOS and Classic layouts: navigation, routing, header and view dispatch.
import { api, h, mount, loadingCard, errorCard, fmtDateTime, setCsrf, stateCard } from "./core.js";
import { renderView, knownViews } from "./views/registry.js";
import "./views/performance.js";

const host = document.querySelector(".view-host");
const nav = document.getElementById("side-nav");
let context = null;
let currentView = null;

function setCurrent(viewId) {
  document.querySelectorAll("[data-view]").forEach((el) => {
    const active = el.dataset.view === viewId;
    if (el.classList.contains("chip")) el.setAttribute("aria-selected", active ? "true" : "false");
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
}

function renderMore() {
  const links = Array.from(document.querySelectorAll(".side-nav .nav-link")).map((a) =>
    h("li", {}, h("a", { href: a.getAttribute("href"), class: "nav-link" }, a.textContent)));
  mount(host, h("section", { class: "card" }, h("h1", {}, "All views"), h("ul", { class: "nav-list" }, links)));
}

async function route() {
  const requested = (location.hash || "#team-lead").slice(1).split("?")[0] || "team-lead";
  const viewId = requested === "more" || knownViews().includes(requested) ? requested : "team-lead";
  currentView = viewId;
  setCurrent(viewId);
  if (nav) nav.classList.remove("open");
  host.setAttribute("aria-busy", "true");
  if (viewId === "more") {
    renderMore();
    host.setAttribute("aria-busy", "false");
    return;
  }
  mount(host, loadingCard());
  try {
    await renderView(viewId, host, context);
  } catch (err) {
    if (currentView === viewId) mount(host, errorCard(err));
  } finally {
    if (currentView === viewId) {
      host.setAttribute("aria-busy", "false");
      const heading = host.querySelector("h1");
      if (heading) heading.setAttribute("tabindex", "-1");
    }
  }
}

function headerMeta(data) {
  const meta = document.getElementById("header-meta");
  const parts = [];
  parts.push(data.source ? `Source ${data.source.run_id}` : "No source run");
  const plan = data.plans && data.plans.EXECUTABLE;
  parts.push(plan ? `Plan ${plan.plan_id}` : "No accepted plan");
  if (data.source) parts.push(`as of ${fmtDateTime(data.source.updated_at)}`);
  parts.push(data.scope.label || "no scope");
  meta.textContent = parts.join(" · ");
}

async function main() {
  document.querySelectorAll("[data-action='toggle-nav']").forEach((btn) => btn.addEventListener("click", () => {
    const open = !nav.classList.contains("open");
    if (window.matchMedia("(max-width: 860px)").matches) nav.classList.toggle("open", open);
    else nav.classList.toggle("collapsed");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }));
  window.addEventListener("hashchange", route);
  try {
    const env = await api("/api/v1/context");
    context = env.data;
    setCsrf(context.csrf_token);
    headerMeta(context);
    if (!context.identity.authenticated) {
      mount(host, stateCard("blocked", "Sign in to see your scoped views.", [{ code: "UNAUTHENTICATED", message: "No trusted identity for this request.", next_action: "Use the landing page demo sign-in (fixture) or your organization's sign-in." }]));
      host.setAttribute("aria-busy", "false");
      return;
    }
  } catch (err) {
    mount(host, errorCard(err));
    host.setAttribute("aria-busy", "false");
    return;
  }
  route();
}

main();
