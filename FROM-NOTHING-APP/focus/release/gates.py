"""The release gates this build cannot pass by itself, each with its exact operational effect (S21, P24).

Live release is a separate capability from the local build (S21 LIVE RELEASE). Every entry names what is missing,
what the application does about it today, who can resolve it and where the steps are. ``OPTIONAL`` entries are
disabled features whose effect is exact and acceptable for release; ``BLOCKING`` entries stop an executable live
release until resolved. This list is the one authority for the review verdict, FINAL_ACCEPTANCE.md and
RELEASE_CHECKLIST.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

BLOCKING = "BLOCKING"
OPTIONAL = "OPTIONAL"


@dataclass(frozen=True)
class Gate:
    id: str
    kind: str
    area: str
    missing: str
    effect: str
    resolve: str
    reference: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


GATES: tuple[Gate, ...] = (
    Gate("LG-01", BLOCKING, "SQL schema",
         "FOCUS_FN migrations 001-009 are not installed on any SQL Server.",
         "A live host starts with REPOSITORY_UNAVAILABLE: /health/live is 200, /health/ready is 503, nothing is stored and "
         "pipeline steps stop with typed unavailability.",
         "A DBA with DDL rights re-runs queries/discovery/fn_collisions.sql, then applies the reviewed bundle with "
         "`migrate --apply --catalog live --installer <subject> --confirm-manifest <sha256>`.",
         "docs/execution/INSTALL_AND_PIPELINE.md"),
    Gate("LG-02", BLOCKING, "Live repository adapter",
         "The mapping of the repository protocol onto the FOCUS_FN tables is not built; focus/data/sql_store.py refuses every write.",
         "Even with the schema installed, live storage stays REPOSITORY_UNAVAILABLE.",
         "Engineering builds the adapter and verifies it against an installed non-production database.",
         "focus/data/sql_store.py"),
    Gate("LG-03", BLOCKING, "Roles and grants",
         "sql/grants/focus_fn_roles.sql is generated and linted but not applied, and no login is mapped to a role.",
         "No live process holds its role's permissions; the separation of duties exists only as a reviewed script.",
         "A DBA applies the grants after 001-009 and maps one login per process (web, source, stager, signer, evaluator).",
         "docs/execution/INSTALL_AND_PIPELINE.md"),
    Gate("LG-04", BLOCKING, "Signing key custody",
         "No production signing key or FOCUS_SIGN_KEY_REF exists; only the fixture TEST key is present.",
         "Live publication refuses, so there is no executable acceptance and no executable dispatch.",
         "The security owner provisions the key, referenced only in the signer's environment.",
         "docs/execution/DEPLOYMENT.md"),
    Gate("LG-05", BLOCKING, "Pre-sign SQL readback",
         "The live stage, readback, validation and accept sequence has never run against SQL Server.",
         "An executable live release cannot be claimed; only the fixture rehearsal of the same protocol exists.",
         "Rehearse on a non-production database once LG-01 to LG-04 are resolved, and keep the receipts.",
         "docs/execution/INSTALL_AND_PIPELINE.md"),
    Gate("LG-06", BLOCKING, "Live source readiness",
         "PERSON_TEAM_MAP and ATTENDANCE_SHIFT were empty when the manual observed them (OD-10), and OD-01 to OD-14 have fixture choices only.",
         "Live snapshots have no executable crews; jobs that depend on an open semantic are blocked or conditional with typed reasons.",
         "Source owners populate the feeds and record decisions OD-01 to OD-14 in the policy.",
         "docs/execution/SOURCE_READINESS.md"),
    Gate("LG-07", BLOCKING, "Live observation producers",
         "OD-15: no approved producer writes lifecycle events, actual intervals, claims or decisions.",
         "The evaluator refuses live (LIVE_OBSERVATIONS_UNAVAILABLE), so there are no live scores, points or XP.",
         "Approve and install the producers, granted FOCUS_FN_ObservationProducer.",
         "docs/execution/DECISIONS.md"),
    Gate("LG-08", BLOCKING, "Score policy approval",
         "OD-16: score.policy_status is DEMONSTRATION with no approval reference.",
         "Every evaluation is labeled demonstration and personal grades stay disabled.",
         "The policy owner approves the score policy and records score.policy_approval_ref.",
         "docs/execution/DECISIONS.md"),
    Gate("LG-09", BLOCKING, "Excusal reviewers",
         "OD-17: no reviewer identity is mapped to the excusal_decider role on a live host.",
         "Claims can be filed, but no live decision can be recorded, so no relief is applied.",
         "The operations owner names the reviewers in FOCUS_GRANTS_JSON.",
         "docs/execution/DECISIONS.md"),
    Gate("LG-10", BLOCKING, "Host identity and grants",
         "FOCUS_GRANTS_JSON and the identity provider settings are not configured on any host "
         "(the Posit Connect credentials header, or the Tanzu trusted header).",
         "Every scoped read and command refuses with UNAUTHENTICATED or FORBIDDEN; only health and the landing page answer.",
         "The platform administrator configures the identity provider and the server-owned grants per host.",
         "docs/execution/DEPLOYMENT.md"),
    Gate("LG-11", BLOCKING, "Deployment",
         "Nothing was deployed to Posit Connect or Tanzu by this build: the session held no environment authority.",
         "No hosted instance exists.",
         "The platform owner deploys the reviewed commit (`rsconnect deploy api`, then `cf push`) after preflight passes.",
         "docs/execution/RELEASE_CHECKLIST.md"),
    Gate("LG-12", BLOCKING, "Independent review",
         "Only implementer self-checks exist in review/; no independent reviewer has examined this build.",
         "A real release still needs an independent verdict on the same hashes.",
         "An independent reviewer writes the next review/REVIEW-NNN.md and VERDICT-NNN.json against the bound hashes.",
         "docs/execution/RELEASE_CHECKLIST.md"),
    Gate("LG-13", BLOCKING, "CI and scheduling",
         "The GitLab CI draft was never pushed to a GitLab remote or activated; its protected environments and resource groups do not exist.",
         "Pipeline roles have no scheduler; they run only by hand or as Tanzu tasks.",
         "The CI owner activates .gitlab-ci.yml with protected environments per role.",
         ".gitlab-ci.yml"),
    Gate("OPT-01", OPTIONAL, "CP-SAT ordering stage",
         "solver.enabled is false and FOCUS_SOLVER_ENABLED is unset; ortools is not in requirements.txt.",
         "Plans come from the named heuristic alone, with the typed solver status DISABLED. Every rule is still enforced.",
         "The owner decides with SOLVER_ENABLEMENT.md and installs requirements-solver.txt.",
         "docs/execution/SOLVER_ENABLEMENT.md"),
    Gate("OPT-02", OPTIONAL, "BCAI explanations",
         "OD-18: no BCAI endpoint, credential or response adapter is approved.",
         "Explanations are the complete deterministic ones; the schedule and ranking are unchanged.",
         "Approve the provider and configure it; responses still pass the strict validators.",
         "docs/execution/DECISIONS.md"),
    Gate("OPT-03", OPTIONAL, "MCP endpoint",
         "FOCUS_MCP_ENABLED is false by default.",
         "POST /mcp answers 404. When enabled, it is read-only and uses the same identity, grants and scope as REST; "
         "commands, signing and grading are never exposed over MCP.",
         "Set FOCUS_MCP_ENABLED=true on the host that serves application-to-application callers.",
         "docs/execution/DEPLOYMENT.md"),
    Gate("OPT-04", OPTIONAL, "Unsupported economic curves",
         "Only the LINEAR_DAILY penalty curve is supported.",
         "A term with any other curve is UNSUPPORTED: its line cost is never zero, it is listed as unknown, and no "
         "improvement is claimed from it.",
         "Add the curve to the economics registry with its exact evaluator and tests.",
         "docs/execution/ECONOMICS_POLICY.md"),
)


def blocking() -> tuple[Gate, ...]:
    return tuple(g for g in GATES if g.kind == BLOCKING)
