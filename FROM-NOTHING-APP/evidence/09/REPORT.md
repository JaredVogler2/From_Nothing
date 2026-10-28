# Stage P09: Precedence, rework and material-release graph

Status: **fixture_verified**

## Changed paths

- `focus/data/graph.py`:
  - **Candidate precedence check.** `precedence_violations` checks a candidate placement against
    every hard typed edge, keeping signed lag. An incomplete predecessor missing from the
    candidate is a violation.
  - **In-progress successors** keep their incomplete predecessors. `SEQUENCE_VIOLATION` is
    recorded (never legalized), with `IN_PROGRESS_CONTINUATION_HOLD` for FS/SS and
    `IN_PROGRESS_CLOSURE_HOLD` for FF/SF.
  - **Started roots.** Policy `rework.started_root_hold` gives `REWORK_CONTINUATION_HOLD`
    (fixture) or `REWORK_CLOSURE_HOLD`.
  - **Verified-complete root with open rework.** `ROOT_CLOSURE_REOPENED` is raised and the root's
    successors wait for the rework release (`REWORK_REOPENED_ROOT`).
  - **NEO/census disagreement** gives `REWORK_STATUS_CONFLICT` (executable-gated).
  - **Template counts.** Templates whose predecessor or successor is absent from a line's full
    census are counted and reported, never treated as done.
- `focus/data/parts.py`:
  - **Releases.** Sourced `ETA` events give timestamp floors (ceiling to the minute). `RECEIVED`
    and `DELETED` evidence confirm a release; `REOPENED` re-blocks.
  - **Dates.** Date-only ECD floors at the end of the local ECD date in the dataset's own zone.
    `PAST_ECD_NOT_RECEIPT` is labelled.
- `config/policies/rebuild-v1.json`: `rework.started_root_hold`.
- `queries/graph/template_edge_profile.sql`, `queries/graph/rework_linkage.sql`,
  `queries/parts/shortage_profile.sql`: read-only readiness.
- `tests/unit/test_constraint_graph.py`, `tests/integration/test_parts_root.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_constraint_graph.py tests/integration/test_parts_root.py -q   -> 13 passed
.venv/bin/python -m pytest tests -q                                                                       -> 148 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A25 | **Missing predecessor.** An incomplete A missing from a candidate gives `PREDECESSOR_MISSING_FROM_CANDIDATE`; A finishing after B starts gives `PRECEDENCE_FS`. FS equality and later starts are valid. **Equations.** Signed lag −30 is kept. SS/FF/SF are checked exactly. Advisory links are not enforced. **Self-edge and cycle.** These are quarantined with evidence, and both cycle edges remain in the snapshot. The downstream FGI is `PREDECESSOR_NOT_SCHEDULABLE`, never "done". |
| A26 | **ROOT4 history.** ROOT4 keeps its actual start (10-23 12:00Z), performed 40 and remaining 60. Only the 60 remaining minutes are phased. **Predecessor edge.** The hard FS edge PRE4 release → ROOT4 remaining work is retained. `SEQUENCE_VIOLATION` and `IN_PROGRESS_CONTINUATION_HOLD` are recorded. **Day-0 proposal.** ROOT4 placed before PRE4 (earliest day 2) is a precedence violation; after PRE4 it is clean. |
| A27 | **Unstarted root.** NC3 and PU3 each inject one gate into unstarted ROOT3's first WORK phase (2 edges, no duplicates). Defect origin (SUPPLIER-DEMO) is kept apart from the DAG direction. **Started root.** ROOT3 started → 2 `REWORK_CONTINUATION_HOLD` edges; under the CLOSURE policy → 2 `REWORK_CLOSURE_HOLD` edges into the ROOT3 release only. |
| A28 | **Reversed linkage.** Reversed parent linkage (ROOT3 ↔ NC3) quarantines both, with link status QUARANTINED. **Status conflict.** A NEO COMPLETE versus census AVAILABLE conflict is `REWORK_STATUS_CONFLICT` and the gates stay. **Reopened root.** A root closed while its rework is open gets `ROOT_CLOSURE_REOPENED`, and its successor NEXT5 waits for the CHILD5 release. No edge is deleted. |
| A29 | **Root gating.** E5 on CHILD5 gates CHILD5 and ROOT5 (exact line-1305 chain); NEXT5 is gated through the graph. FGI on line 1301 is gated while FGI on 1302 is not. **Multiple and unknown.** Two CHILD5 shortages bind at the latest floor (10-30 04:00Z). An unknown-ECD shortage blocks PRE4, and ROOT4 downstream. **Blank USED_ON** is kept and exposes line 1306 scope only. |
| A30 | **Floors.** A timed ETA of 14:15 floors exactly; 14:15:30 floors at 14:16. A date-only ECD 10-27 floors at 10-28 04:00Z and is labelled. **Past ECD** without receipt stays conditional (`PAST_ECD_NOT_RECEIPT`, executable gated). **Receipt then reopen** is conditional again (`REOPENED`). An unrevoked receipt is CONFIRMED at the ceiling minute. |

## Fixture versus live

Graph, rework and shortage data are S18 fixture data. The readiness SQL was not run against a
live database.

## Remaining gaps

- There is no live producer for rework classification, part ETA/receipt/reopen/deletion events,
  or sourced safety dispositions. In-progress holds therefore apply unconditionally.
- Hard CONCURRENT/NON-CONCURRENT semantics (OD-07) remain open for live use.
