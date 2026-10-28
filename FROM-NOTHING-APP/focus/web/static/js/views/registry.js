// View registry: maps each of the 17 views (+ supplementary panels) to its renderer.
// Renderers read canonical /api/v1 projections only; no client-side grading or assignment.
import { h, mount, stateCard } from "../core.js";

const VIEW_IDS = [
  "team-lead", "management", "mechanic", "ie", "staffing", "supply", "scenario", "performance", "project",
  "budget", "gantt", "development", "commitments", "economics", "shiftbook", "myday", "capacity",
  "quality", "twin", "arena", "turnover", "history",
];

const renderers = {};

export function register(viewId, renderer) {
  renderers[viewId] = renderer;
}

export function knownViews() {
  return VIEW_IDS;
}

export async function renderView(viewId, host, context) {
  const renderer = renderers[viewId];
  if (!renderer) {
    mount(host, h("div", { class: "view-title" }, h("h1", {}, viewId)),
      stateCard("unknown", "This view's canonical projection is not available in this build.", []));
    return;
  }
  await renderer(host, context);
}
