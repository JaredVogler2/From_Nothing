# Display package: what the web reads instead of snapshots and plan artifacts

**Owner request (2026-11-01).** The pipeline publishes small, partitioned view data. The web app on Posit Connect reads
only that and never loads a snapshot.

## Why

At 50 aircraft the web loaded too much for any single page. A cold cache meant all of these:

- **The admitted snapshot.** It is 1.7 GB stored, and the lean load still peaks at 7.0 GB (`PRODUCTION_SCALE.md`).
- **Both accepted plan artifacts.** The executable artifact is 370 MB of canonical JSON and the forecast is 599 MB. In
  each, 233 MB is the snapshot's copied dependency graph.
- **Every accepted executable artifact in history**, to build the goal registry behind the shift book, team lead,
  performance, claims, arena, turnover and history views.

A Posit Connect process cannot hold that.

## What the pipeline writes

Two pipeline steps already hold the whole object in memory, so they write the package:

- **Acquisition (source role)** writes the snapshot's parts and manifest before the source head moves.
- **The signer** writes each plan's parts and manifest, and signs the manifest, before the plan head moves.

Parts hold the snapshot's and plan's own contract records (jobs, phases, edges, assignments, duties, ...) without their
evidence references. Each part is stored once, under the SHA-256 of its canonical JSON.

| Part | Snapshot | Accepted plan |
| --- | --- | --- |
| `summary` | Census, CS facts, calendars, economics, gaps, people, certificates, carry-in, a pool index, counts of jobs by aircraft, team and disposition, and shortages by aircraft and status | Header, costs, completions, counts, burndown, unscheduled reason codes, team-day index |
| `line:<n>` | One aircraft's jobs, dispositions and shortages | Its job outcomes |
| `standards:<n>` | One aircraft's duration standards and requirements | |
| `jobs:<b>` (1,024 buckets by job id) | Each job's phases, graph neighborhood, releases, durations, requirements and shortages | Its assignments and outcomes |
| `labels:<b>` | Phase labels of the bucket | |
| `pools:<team>:<date>` | One team's pools and availability on one day | |
| `work:<team>:<n>` | One team's phases on one aircraft, with the dispatch rule's inputs (below) | |
| `graph:<n>` | One aircraft's hard dependency graph by index, for the dispatch rule's ancestor walk | |
| `holds` | Every inspection-hold phase | The plan's assignments of them |
| `releases` | Every release milestone | |
| `team-day:<team>:<date>` | | One team's assignments of one day, the duties of its pool members, and the plan snapshot's phases and jobs for them |
| `goals:<date>` | | The frozen goal units' planned start, end, crew and commitment |
| `commitments` | | Task-commitment results with their phases and jobs |

**Dispatch facts.** `work:<team>:<n>` carries every record `ValidationInput` and the dispatch rule read for those phases:
- hard edges into them;
- predecessor phases and their dispositions;
- the inputs of AND release phases;
- release predecessors;
- the shortages naming the jobs.

The rule also reads the unconfirmed PART releases among a phase's ancestors, up to verified completion. Those can sit
anywhere upstream. So each aircraft's `graph:<n>` part holds its dependency graph by index: its phases and their whole
upstream closure, which phases are verified complete, and the unconfirmed part releases with their records.

The web walks that graph for the phases a request asks about (`records.unreceived_ancestors`), which is the same walk as
`ValidationInput.ancestors`. So the team lead and turnover views give the same dispatch reasons and emergency-addition
proposals as the whole snapshot. Precomputing the ancestors of every phase was tried and dropped: at 50 aircraft that
relation is too large to store, hundreds of MB.

The scheduler also writes a small `CandidateDigest` beside each stored candidate, so the plan view never loads a
candidate.

## Trust

- **Snapshot.** Its manifest must name the source run's snapshot id and hash. That is the trust the snapshot itself had:
  the web checked its self-hash and read it through the run record.
- **Plan.** A `DisplayReceipt` is signed with the plan key under its own domain, `FOCUS_DISPLAY_V1`. It covers the plan
  id, the accepted payload hash, scope, purpose, snapshot hash, policy hash and the manifest's hash.
  - The web verifies the acceptance receipt's signature (no payload read) and the display signature.
  - The display receipt must agree with the acceptance on every binding, and the manifest must hash to the signed value.
  - Neither signature can stand in for the other.
- **Parts.** Every part must hash to its manifest entry before it is decoded.

Any failure makes nothing current, and the view returns a typed reason:
- `DISPLAY_NOT_BUILT` when a package is missing;
- `DISPLAY_NOT_VERIFIED` when a part, manifest or snapshot binding fails;
- `PLAN_NOT_VERIFIED` (`SECURITY_FAILURE`) when a plan receipt or signature fails.

## What the web reads

`serializers.Canon.load` verifies the heads and loads the two kinds of summary only. Each projection then asks for the
slice it reads:
- an aircraft;
- a job's bucket;
- the team-days of a shift;
- a team's work;
- the holds.

A slice exposes exactly what it loaded. Reading anything else raises `NotInSlice`, so a projection can never mistake a
partial collection for the whole fleet.

Verified parts are kept in an LRU keyed by their hash and bounded by their stored bytes (`FOCUS_DISPLAY_CACHE_MB`,
default 192). The goal registry is built from the signed summaries and the `goals:<date>` parts
(`GoalRegistry.from_display`), with no artifact decoded.

**Labels from the plan's own snapshot.** Phases and jobs that come with a plan part are the plan snapshot's records.
- Right after every pipeline they are the current records.
- When a newer source run is admitted before the next plan, the plan's rows keep the labels and teams they were planned
  with. Before, they lost them when a phase id changed.
- Current facts (dispositions, pools, dispatch reasons) always come from the current snapshot.

**Whole-fleet lists page.** At 50 aircraft some lists are hundreds of thousands of rows: jobs, shortages, standards and
the budget's per-job list. They now page with the same signed, scope-bound cursors as `/api/v1/jobs`, optionally for
one aircraft (`line`):
- `/api/v1/jobs` and `/api/v1/shortages` page by row;
- `/api/v1/standards` and `/api/v1/budget` page by aircraft in job-label order, and team inference and data gaps come
  with the first page.

The counts in the summary give totals without loading any part. A small fleet still fits one page.

**The digital twin** reconciles every job, assignment and duty of the fleet on each request, which the package does not
precompute yet.
- By default `/api/v1/twin` returns the typed state `TWIN_NEEDS_FULL_LOAD`.
- `FOCUS_TWIN_FULL_LOAD=true` restores the whole-snapshot twin on a host with the memory for it.
- Moving it onto the package means precomputing its fleet-wide base in the evaluator step, which is the open follow-up.

## Operations

- **Pipeline.** Nothing new to run: `acquire` and the two `sign_accept` steps write the package.
- **Backfill** for stores from before the package:
  - `python -m focus.cli display --current-source` (source role);
  - `python -m focus.cli display --current-plans` (signer role: it signs). It writes the same bytes the pipeline writes.
- **Recovery.**
  - An acquisition replay rebuilds a missing snapshot package before promoting.
  - A publication retry in the signer role signs a missing plan package. The stager never does, and reports
    `display_pending`.
- **Not yet done:**
  - Retention of old parts.
  - The live SQL mapping of the three display collections, which is the same pending live adapter as every other
    collection (`focus/data/sql_store.py`).

## Measured at 50 aircraft

A 50-aircraft measurement is running: the cost of building the package in acquisition and in the signer, and the web
process opening every view. Its results will be added here.

## Tests

- `tests/integration/test_display_views.py` (28 tests). Every projection, for every demo persona and every filter the
  demo exercises, gives the same output through the display package as `tests/support/reference_views.py`. That file is
  the projections as they were, loading the whole snapshot and every artifact. The goal registries are equal field by
  field.
- `tests/integration/test_display_package.py` (13 tests). It covers:
  - the package of the source head and both plans;
  - every read endpoint answering with snapshot loading and artifact decoding switched off;
  - changed parts, manifests, signatures, swapped or missing receipts;
  - idempotent builds and the byte-identical CLI backfill;
  - acquisition replay and publication-retry recovery;
  - paged lists covering every row once.
- `tests/integration/test_display_dispatch.py` (3 tests). On a synthetic two-aircraft fleet with unconfirmed parts
  upstream of most work, each team's work parts give `ValidationInput`, `assess_addition` and `assess_action` the same
  answers as the whole snapshot.
- `tests/unit/test_display_build.py` (61 tests). On random graphs spanning several aircraft, with completed phases,
  cycles, concurrency edges and edges between aircraft, the graph parts' ancestor walk equals `ValidationInput.ancestors`.
