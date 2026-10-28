"""The 17 operational views plus supplementary panels (S15), shared by iOS and Classic layouts."""
from __future__ import annotations

VIEWS: tuple[dict[str, str], ...] = (
    {"id": "team-lead", "title": "Team Lead", "group": "Operate", "icon": "TL", "summary": "Qualified feasible next actions with exact full crews and reasons."},
    {"id": "management", "title": "Management", "group": "Plan", "icon": "MG", "summary": "Fleet completion, risk, cost, burndown and capacity."},
    {"id": "mechanic", "title": "Mechanic", "group": "Operate", "icon": "MC", "summary": "Individual scope, current duty and certification readiness."},
    {"id": "ie", "title": "Industrial Engineering", "group": "Improve", "icon": "IE", "summary": "Standards, role and data review queue with provenance."},
    {"id": "staffing", "title": "Staffing", "group": "Plan", "icon": "ST", "summary": "Pure-pool attendance, activation, load and skill demand."},
    {"id": "supply", "title": "Supply Chain", "group": "Operate", "icon": "SC", "summary": "Shortage used-on/root/descendant impact; ECD versus ready."},
    {"id": "scenario", "title": "Scenario", "group": "Plan", "icon": "SN", "summary": "Isolated what-if inputs compared with the baseline."},
    {"id": "performance", "title": "Shift Performance", "group": "Perform", "icon": "SP", "summary": "Canonical B0/B/E/Cp/U/O/base/final/grade with explanation."},
    {"id": "project", "title": "Project", "group": "Plan", "icon": "PJ", "summary": "Aircraft timeline, stations, gate/due and conditional completion."},
    {"id": "budget", "title": "Schedule Budget", "group": "Plan", "icon": "SB", "summary": "Standard elapsed/person effort, planned duty, NVA and OT."},
    {"id": "gantt", "title": "Worker Gantt", "group": "Operate", "icon": "WG", "summary": "Every person's work, travel, holds and shift boundaries."},
    {"id": "development", "title": "Development", "group": "Improve", "icon": "DV", "summary": "Auditable standard/training/improvement proposals; XP is not certification."},
    {"id": "commitments", "title": "Commitments", "group": "Plan", "icon": "CM", "summary": "Original/current/raw/pending dates, three-day defense and change ledger."},
    {"id": "economics", "title": "Economics", "group": "Plan", "icon": "EC", "summary": "Marginal and full cost, cap/floor/terms, unknown liability."},
    {"id": "shiftbook", "title": "Shift Book", "group": "Operate", "icon": "BK", "summary": "Team/date/shift exact jobs and full crews."},
    {"id": "myday", "title": "My Day", "group": "Operate", "icon": "MD", "summary": "Ordered duty, aircraft, crewmates, arrival and OT."},
    {"id": "capacity", "title": "Capacity", "group": "Plan", "icon": "CP", "summary": "Weighted bottlenecks; utilization distinct from activation."},
)

SUPPLEMENTARY: tuple[dict[str, str], ...] = (
    {"id": "quality", "title": "Quality", "group": "Supplementary", "icon": "QA", "summary": "Sourced inspection holds and inspector competition."},
    {"id": "twin", "title": "Digital Twin", "group": "Supplementary", "icon": "DT", "summary": "Team/shift/line layers with reconciled canonical totals."},
    {"id": "arena", "title": "Arena & Recognition", "group": "Supplementary", "icon": "AR", "summary": "Frozen-value XP, badges and earning lineage."},
    {"id": "turnover", "title": "Turnover", "group": "Supplementary", "icon": "TO", "summary": "Shift handoff: holds, carry-in and open actions."},
    {"id": "history", "title": "History", "group": "Supplementary", "icon": "HS", "summary": "Accepted plans, evaluations and corrections over time."},
)

MOBILE_TABS = ("team-lead", "myday", "management", "performance")

ALL_VIEW_IDS = tuple(v["id"] for v in VIEWS + SUPPLEMENTARY)
