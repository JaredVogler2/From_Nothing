# QA policy (S10, P10)

How in-process and final inspections, parallel buyoffs and the release milestone are modelled.
`focus.data.inspections` builds the phases and `focus.data.graph` wires them; every engine path
consumes the same phases and edges.

## Facts and their authority

| Fact | Source | Proves | Never proves |
| --- | --- | --- | --- |
| Which inspections a job requires | `FOCUS_BUYOFF_PLAN` (FINAL / IN_PROCESS / MANUFACTURING / QUALITY / CC / ODA unit member / partner QT) | Final, IP and parallel buyoff requirements | Where an IP hold sits |
| IP hold locations and allocations | Supplemental `inspection_holds` (exact line/SOI, sequence, production-minute position, inspector minutes) | Hold positions and the 45-minute allocation | Inspector authorization |
| Inspection counts | `FOCUS_INSPECTION_COUNTS` | Cross-check only; disagreement is reported | Hold locations or release |
| Inspector capability | QA pool, roster/attendance, `QA` certificate holdings | A qualified, available inspector | Production people as inspectors |

## Phases

- **IP QA**: 45 minutes total (`qa.total_ip_minutes`, user requirement) of active inspector
  effort across the sourced holds. Allocations must sum to exactly 45, otherwise the job gets
  `IP_HOLD_ALLOCATION_MISMATCH`. It is never 45 per hold.
  - **Fixture example (ROOT3, WORK 100):** holds after 40 and 80 give
    WORK 40 → IP 20 → WORK 40 → IP 25 → WORK 20 → FINAL 45 → AND release.
  - **Unknown hold location:** a required IP with no hold location gives
    `IP_HOLD_UNKNOWN_LOCATION` (blocked); there is no midpoint insertion.
- **Final QA**: a separate 45 minutes (`qa.final_minutes`, user requirement) after all
  production and IP phases.
- **Parallel buyoffs**: every other required buyoff is a parallel branch after the last
  production segment. Durations come from `FOCUS_INSPECTION_STANDARDS` `PARALLEL_BRANCH` rows (for
  example ODA unit member 20), else `qa.other_buyoff_minutes`.
- **AND release**: final QA and every parallel buyoff feed one `RELEASE` milestone. Each successor
  of the job starts from that release (FS, meaning ≥, not forced immediacy). No single convenient
  tail is chosen.
- **Conservation**: production elapsed is kept across segments (each records the parent
  elapsed), and standard person effort is distributed exactly. Crew demand stays on every WORK
  segment.
- **Embedded standards**: if a flow standard already contains the inspection minutes
  (`qa.standard_includes_inspection_minutes`, fixture false), IP and final minutes are carved out
  once (no duplicate 45). A standard too short to hold them gives `QA_RECONCILIATION_INVALID`.
- **In-progress jobs**: only the remaining work is split. A hold at or before the performed
  minutes without a sourced buyoff gives `IP_HOLD_STATUS_UNKNOWN` (executable-gated); actual
  history is kept.

## Inspectors and waiting crews

- Inspector demand is a real role: team `QA`, certificate group `QA`, one distinct person per
  inspection. Only qualified QA pool members are candidates: in the fixture, DEMO-Q1-01 and
  DEMO-Q1-02. DEMO-Q1-03 and DEMO-Q1-04 hold an irrelevant certificate.
- Two simultaneous inspections need two inspectors; a third waits. There is no unlimited or
  empty-certificate QA pool, and production people are never borrowed as inspectors.
- Production crew during IP is set by `qa.crew_during_ip`: RELEASE in the fixture, so the crew
  can take other feasible work. With WAIT, the retained crew consumes WAIT duty. Inspector
  non-overlap and the WAIT duty are enforced by the named duty ledger (P11).
- Voided or reopened buyoffs re-block release through ordered lifecycle facts (P17 events).

## Live status

There is no live producer of IP hold locations, so live executable QA stays blocked for jobs that
require IP inspections. `queries/quality/inspection_profile.sql` (read-only) lists the jobs that
need hold locations and any count disagreements. It has never been run against a live database.
