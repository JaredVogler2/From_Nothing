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
| `jobs:<b>` (1,024 buckets by job id) | Each job's phases, edges, the labels of the far edge ends, releases, durations, requirements and shortages | Its assignments and outcomes |
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
- hard edges into them, and their predecessor phases (with dispositions) and releases;
- what the readiness check of an emergency addition can read before its answer is fixed (`build.ready_reach`). The
  check (`performance.amendments._ready`) walks open AND release phases input by input and stops at the first input that
  can never be ready, so the part holds only that walk's reach. Including every input of every AND release instead made
  the parts 8 GB at 50 aircraft;
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

These are measured on the stored 50-aircraft run (298,009 jobs, 445,688 phases, 1,258,325 edges), November 1, 2026,
with the native encoder.

**Building the package.** The build added time but not peak memory. Each process peak stayed at the 7.0 GB of loading
the snapshot.

| Package | Built by | Time | Parts | Stored |
| --- | --- | --- | --- | --- |
| Snapshot | acquisition | 162 s | 7,933 | 4.8 GB |
| Executable plan (38,626 assignments, 114,311 duties) | signer | 10 s | 1,876 | 291 MB |
| Forecast plan (147,679 assignments, 471,310 duties) | signer | 23 s | 2,549 | 845 MB |

Most of the snapshot package is the team work parts (2.4 GB) and the job buckets (1.6 GB). A phase and its edges appear
in both, once for its job's detail and once for its team's dispatch.

The largest parts the web loads are:
- the holds, 19.7 MB once per snapshot;
- an aircraft, 8 MB;
- a team's work on one aircraft, 3.1 MB;
- a job bucket, 1.9 MB;
- a team-day, under 1 MB.

**The web process.** One fresh process opened every view against that store, then the team-lead view for five more
teams.
- After verifying both heads and loading the summaries it held 67 MB (0.6 s).
- It peaked at 857 MB, with the part cache at its 192 MB budget of stored JSON.
- Before the package, the same process had to load the snapshot (a 7.0 GB peak) and decode both artifacts (370 MB and
  599 MB of JSON) before its first page.

| View | Time | Response |
| --- | --- | --- |
| fleet, economics, plan view, history | under 0.1 s | up to 144 KB |
| aircraft | 1.0 s | 5.1 MB |
| jobs: a page near the end, or a team's first page | 0.01 to 1.0 s | 100 rows |
| job detail | 0.2 s | 6 KB |
| shift book, My Day, workforce | 0.1 to 0.8 s | up to 629 KB |
| capacity | about 4 s | 69 KB |
| budget (first page), Gantt (every team) | 1.3 s, 1.7 s | 1.0 MB, 8.4 MB |
| quality (every hold), standards (first page) | 2.0 s, 0.4 s | 3.8 MB, 3.7 MB |
| turnover | 3.1 s | 136 KB |
| team lead, first request for a team | 6 to 18 s | up to 3.9 MB |
| team lead, again | 2.4 s | |

**The team lead view.** Its first request for a team loads that team's work on every aircraft (about 80 MB stored),
because its emergency-addition proposals cover every open phase of the team (6,053 for T21). Later requests reuse the
cached parts.

**The part cache.** `FOCUS_DISPLAY_CACHE_MB` trades memory for reloads: Python objects take about three to four times
their stored JSON.

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
- `tests/unit/test_display_build.py` (141 tests), on random graphs:
  - spanning several aircraft, with completed phases, cycles, concurrency edges and edges between aircraft, the graph
    parts' ancestor walk equals `ValidationInput.ancestors`;
  - with AND release phases, completed inputs and releases of every status received before or after the request time,
    `_ready` on a slice holding only the readiness reach answers as on the whole graph.
- `tests/unit/test_seal_bytes.py` (16 tests). The single-encoding envelope writer gives the same bytes as `seal`, with
  and without the native encoder.
