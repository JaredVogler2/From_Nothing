# Production-scale measurements and the path to 50 aircraft in under 10 minutes

**Status: measured on production-shaped synthetic data, not live proof.** The target is a 50-aircraft plan in under
10 minutes, inside a 15-minute window at shift end (data, plan, validation, publish). Today's engine is far from it at
that size. This page records what was measured, where the time goes, and the ordered work that closes the gap.

## The shape measured

`focus/data/production_fixture.py` follows the program's stated numbers, per aircraft:
- about 4,500 baseline tasks over positions 0-3 and roll out, each position owning its tasks;
- 3,000 rework tasks, 30 % of them open at a time;
- about 22,000 model-level dependencies, plus the rework and part links (27,000 in total), crossing teams;
- one station window per position.

Teams and QA are shared across the whole fleet, so every aircraft competes for the same mechanics. Every assumption is
an explicit parameter.

## Results

`python tools/benchmark_production.py --sizes 1,5 --purposes FORECAST --engines native,python --max-moves 0`
(4 vCPU, CPython 3.12.3; receipts in `evidence/production-scale/`). Both engines schedule the same admitted snapshot in
one process.

| Aircraft | Open jobs | Phases | Dependencies | People | Load | Python core | Native core | Same plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5,040 | 10,766 | 27,312 | 342 | 33 s | 55.8 s | **25.5 s** (2.2×) | yes |
| 5 | 16,200 | 44,148 | 126,848 | 710 | 178 s | 1,186 s | **239 s** (5.0×) | yes |

- **Validity:** every plan is valid, with zero violations and a complete inventory. In the 5-aircraft plan, all
  16,200 open jobs are placed, 8,542 of them as conditional work behind parts not yet received.
- **Identical plans:** both cores produced the identical plan (same candidate id and content hash) at both sizes.
- **Search:** these runs are first-pass plans with no improvement search (`--max-moves 0`), to measure the cost of
  building one plan.
- **Memory:** peak 0.5 GB for 1 aircraft and 1.7 GB for 5 aircraft.

## Engine v2 (October 31, 2026)

`python tools/benchmark_production.py --sizes 1,5 --purposes FORECAST --engines native,python --max-moves 0`; receipts in
`evidence/production-scale/engine-v2/`. Both cores scheduled the same snapshot and produced identical plans at both sizes.

| Aircraft | Native core before | Native core now | Python core now | Load before | Load now |
| --- | --- | --- | --- | --- | --- |
| 1 | 25.5 s | **10.2 s** | 47.7 s | 33 s | 19 s |
| 5 | 239 s | **55.9 s** | 1,141 s | 178 s | 103 s |

What made the difference:
- **The whole time-slot search for a phase runs in one Rust call**, and every opening is tried (the 48-start limit per
  shift is gone in both cores).
- **Exact start skipping.** Before a crew attempt, the search counts the people who could be booked alone (no conflict,
  inside their open windows, a transfer slot into and out of the work, hours within caps) and are qualified, allowing at
  most cap - active new heads. A start without enough of them is skipped. At 5 aircraft, crew attempts fell from 1.28
  million to one per phase.
- **No quadratic setup.** The search no longer lists every job's competitors (quadratic in fleet size).
- **Faster records.** A faster canonical codec with identical bytes; datetime conversions cached by value; a snapshot
  verified once per process; record hashes streamed item by item (same digest, about 1 MB instead of hundreds).

Also in this version: cost-of-delay priority (the aircraft's daily penalty decayed by the phase's slack), half of the
search moves aimed at the longest waits, and repeatability (stop on the move bound; a decision digest in every receipt;
the same decisions across processes with different hash seeds).

## 50 aircraft, one shift (October 31, 2026)

`python -m focus.sim.runner --aircraft 50 --days 1 --shifts 2` on the full-scale synthetic fleet
(`focus/data/fullscale_fixture.py`):
- 298,009 jobs, 445,688 phases and 1,258,325 edges;
- 2,178 people on 30 teams of 10-30 per shift, plus QA;
- 23,301 open late parts.

Forecast purpose; seed, cost-of-delay redispatch, two delay-repair boosts at most and three search moves. Same machine
for both columns (4 cores, 16 GB). Both runs produced the same plan: 147,679 assignments and the same floor outcomes.

| Step | First 50-aircraft trial | Now |
| --- | --- | --- |
| Admission (production snapshot builder) | 134.8 s | 79.5 s |
| Seed: decode, evaluation, independent validation | 232.2 s | 154.6 s |
| Cost-of-delay redispatch, with its validation | 163.7 s | 129.4 s |
| Three search moves | 289.0 s | 135.8 s |
| Final validation | 48.3 s | 44.1 s |
| **Plan** | **784.0 s** | **494.4 s** |
| **Admission + plan** | **15.3 min** | **9.6 min** |
| Peak memory | 5.5 GB | 5.9 GB |

What changed, all with identical results:
- **Native canonical JSON** (`native/src/canon.rs`): snapshot and plan seals, lineage hashes, 2.1 million graph ids and
  decision digests are written and hashed in Rust from the Python values. Before, this was about a third of
  admission.
- **V9 in one topological pass.** At 50 aircraft the previous version walked the network once per part release
  (23,301 walks); it took minutes and about 4 GB. Every assignment the summary cannot clear, and every phase on or below a
  cycle, still gets the exact walk.
- **The Rust search releases the GIL, and three search moves decode together** on threads. The accepted sequence is the
  one-at-a-time sequence (see `NATIVE_CORE.md`).
- **FxHash** for the Rust core's maps (std's SipHash was about a fifth of the search).
- Smaller changes:
  - per-row work hoisted out of the graph builder's template-edge loop;
  - shift instances cached in the validator;
  - carry-in windows indexed by person;
  - crew holding evidence memoized per person and bundle.

The seed's independent validation now also runs while the redispatch decodes (measured in the 14-day run).

Where the time goes now:
- **Each full-fleet decode is about 85 s.** It places 286,024 phases, the whole remaining build; the Rust search is
  about 70 % of that.
- **Each independent validation is about 45 s.**

At this scale the three search moves accepted nothing. Every candidate is a full decode of the fleet, because
resources are shared across aircraft, so a move's effect cannot be judged locally.

## Will it hold on the real data?

The synthetic fleet has the program's stated size, so the method's cost drivers are measured at the right scale:
- open phases placed per decode;
- how many shifts and starts each search scans before a crew fits;
- the number of full decodes;
- the plan size the validator checks.

Five things the synthetic run cannot show, each to measure on a real extract:

1. **Source pulls.** The simulation captures sources in memory. Real pulls from IE SQL Server add each query's time:
   about 300,000 task rows, 1.3 million dependency rows, and certificate, roster and attendance rows. Source-run receipts
   record each query and its row count, not its time, so time one pull on the real server.
2. **Crowding.** The synthetic work fills 90 % of regular hours. Overloaded teams make each phase scan more shifts
   before a crew fits. Start skipping keeps that cheap, but it is not free.
3. **Certificate bundles.** Real multi-certificate requirements and alternatives widen the crew search (capped at 2,000
   combinations per attempt). The synthetic teams need one to three certificates.
4. **Where it runs.** The schedule runs in the GitLab pipeline (`schedule_validate` in `.gitlab-ci.yml`), and Posit
   Connect only displays the published results. The size therefore matters for that job's runner, not for Connect:
   - The measured runs used 4 cores and peaked at 5.9-9.4 GB. Tag the job to a runner with at least 4 vCPU and 16 GB.
   - The job also needs the native wheel and `FOCUS_NATIVE_CORE=true`; without them the Python core gives the same plan
     but is far too slow at 50 aircraft.
   - Build the wheel with `native_wheel`, or bake it into a prebuilt image so the job spends no time installing.
   - With one core the search moves decode one at a time: the same plan, about 90 s more.
   - **The store hand-off is measured.** Each role process loads the admitted snapshot from the store: it parses the
     stored JSON, builds the objects and verifies the hash. The simulation never does this, because it admits and
     plans in one process.
     - The first decode held the stored bytes, a text copy, two full re-encodings (the canonical check and the
       envelope hash) and the parsed JSON while it built the objects.
     - The lean load hashes the envelope in place, releases each copy as soon as the next one exists, proves the bytes
       canonical with a streamed hash, and releases the parsed JSON item by item while it builds the objects.

     Measured in fresh processes on this machine with the native encoder (November 1, 2026), the same snapshot hash
     both ways:

     | Fleet | Stored snapshot | First decode | Lean load | Again in the same process |
     | --- | --- | --- | --- | --- |
     | 20 aircraft | 738 MB | 91 s, 5.3 GB peak | 49 s, 3.1 GB peak | about 1 s |
     | 50 aircraft (1.26 million edges) | 1.7 GB | 224 s, 12.1 GB peak | 121 s, 7.0 GB peak | about 3 s |

     The heaviest job, a 50-aircraft load followed by a full forecast solve and its FINAL validation in one process,
     took 615 s and peaked at 7.5 GB. An earlier projection of 14 to 15 GB came from a process that still held its
     own admission data.

   - **Cold loads on the executable path.** Before speed item 2 there were seven:
     - admit;
     - schedule and validate, each loading again;
     - staging and signing, each also loading the current plan's snapshot to read its cutoff.

     Now there are three: schedule, stage and sign. The admit step reuses the snapshot acquisition stored, validate
     reuses the one schedule loaded, and the cutoff comes from the source run. A whole pipeline goes from 13 cold loads
     to 6, about 12 minutes of loading at 50 aircraft with the lean load.
   - **Runner memory.** At 50 aircraft, 16 GB runners are enough for every role: the heaviest job peaked at 7.5 GB. An
     8 GB runner is not. A streaming or native snapshot decoder would lower the peak further.
   - **The display package** (`DISPLAY_PACKAGE.md`) adds time but no peak memory:
     - acquisition writes the snapshot's parts in 162 s (4.8 GB stored);
     - the signer writes a plan's parts in 10 s (executable, 291 MB) or 23 s (forecast, 845 MB).

     The web reads only those parts. One process opening every view peaked at 857 MB, where the snapshot alone used to
     take 7.0 GB to load.
   - **Storage per shift** at 50 aircraft, in the fixture store:

     | Record | Per shift | Kept |
     | --- | --- | --- |
     | Display parts | up to 5.9 GB | until superseded; then only 3.3 MB of history parts (D-24, `display-retention`) |
     | Display manifests | 1.7 MB | forever |
     | Admitted snapshot | 1.7 GB | forever (history, D-21) |
     | Accepted plan payloads, both purposes | 1.1 GB | forever (history, D-21) |
     | Candidates, both purposes | 0.47 GB | until `retain-history` archives the unreferenced ones (moved, not deleted) |
     | Per-job evaluation evidence | about 58 KB per team-shift revision (about 1.8 MB for 31 teams) | 90 days, then deleted once the job record exists (D-25, `performance-details`) |
     | Light performance rows | about 200 bytes per team-shift (about 6 KB) | forever (about 7 MB a year) |
     | Job records | about 107 jobs of about 50 bytes per team-shift (about 0.17 MB) | forever (under 0.2 GB a year) |

     Display retention runs before each pipeline's acquisition, so the display parts peak at two packages, about 12 GB.
     The snapshots and payloads are the history D-21 keeps forever: about 2.8 GB a shift, or about 3 TB a year at three
     shifts a day in this store. How long to keep them is an owner decision, and live they go into SQL tables through
     the pending live adapter, where their size is not measured yet.
5. **Headroom.** 9.6 minutes fits the 10-minute target with little margin.
   - The repeatable knobs are fewer search moves (they accepted nothing at this scale) and fewer repair boosts.
   - The bigger lever would be a detailed horizon with a coarser projection beyond it, which changes how completions are
     projected and needs a decision.
   - The wall-clock budget also stops the search, but a run stopped by it is not repeatable.

## The pre-shift flow, action by action (November 11, 2026)

The owner asked how long each action takes: the data pull, digesting the data, the scheduler, storing the results and
the dashboard. All of it has 20 minutes from the data cut (D-28).

**Measured** with `python tools/time_pre_shift.py run WORKDIR --aircraft 50 --views --repair` on the full-scale
synthetic fleet at the owner's rates: 1,216,781 source rows in 44 tables, on 4 cores and 15 GB.
- Each pipeline role ran in a fresh process, as the pipeline's jobs do.
- The settings are the shipped ones: a full solve with `optimizer.budget_seconds` = 30.
- The receipt is `evidence/production-scale/pre_shift_timing_50.json`.

**As shipped, the window is not met: 23.2 minutes** from the start of the first job to the accepted executable plan
and its dashboard data. The pull from SQL Server, each job's start on a runner and a person's acceptance come on top.

| Job (steps) | Seconds | Peak memory | Written to the store |
| --- | --- | --- | --- |
| source (`acquire`, `admit`) | 341 | 9.0 GB | 6.9 GB |
| app (`schedule`, `validate`) | 375 | 7.5 GB | 0.1 GB |
| stager (`stage_readback`) | 325 | 8.0 GB | 0.4 GB |
| signer (`sign_accept`) | 350 | 8.3 GB | 0.7 GB |
| **Data cut to the accepted plan** | **1,392 (23.2 min)** | | **8.2 GB** |
| app (`forecast`) | 612 | 7.8 GB | 0.4 GB |
| stager (`forecast_stage_readback`) | 493 | 10.3 GB | 0.7 GB |
| signer (`forecast_sign_accept`) | 520 | 10.8 GB | 1.5 GB |
| **Then to the accepted forecast** | **1,626 (27.1 min)** | | **2.6 GB** |

**By action, over the four jobs of the window:**

| Action | Seconds | Share |
| --- | --- | --- |
| Independent validations of the plan outside the scheduler (eight) | 403 | 29% |
| Loading the admitted snapshot from the store again, once in each of the three later jobs | 327 | 23% |
| The scheduler: working data (18), first pass (145), the candidate and its receipts (21) | 184 | 13% |
| Digest: raw rows kept and read back (27), snapshot built (70), stored (64), verified (4), the rest (19) | 183 | 13% |
| Dashboard data: the snapshot's parts (135), the plan's parts (11) | 146 | 11% |
| Read the candidate, serialize, stage, accept | 69 | 5% |
| Each process starting and ending (releasing several GB takes 11 to 20 s) and the rest | 68 | 5% |
| Reading the source rows (fixture files) | 11 | 1% |

- **The scheduler is 3 of the 23 minutes.** Loading again and validating again take 12.
- **The plan is the first pass.** The first pass uses up the 30-second budget, so no repair and no improvement move
  runs (stop reason `deadline`; `DECISION_PLAYBOOK.md`, DEC-34).
- **Validations.** A FINAL validation takes 19 to 23 s in the scheduling job and 32 to 33 s in a job that builds its
  own validation input. DETACHED and READBACK each decode the serialized plan and validate it: 66 to 70 s. The
  stager runs FINAL, DETACHED and READBACK; the signer runs all three again before it signs.
- **Digest stages** of the 70 s snapshot build: the task network 31 s; census and its indexes 12 s; people, pools and
  availability 9 s; the seal 9 s; certificate requirements per job 4 s; durations 1.5 s; certificates held under 1 s.
- **The pull is not measured.** The source rows here are fixture files (11 s to read). Reading the same 1.2 million
  rows from a local SQLite database took 5 s, which is a floor: no network and no SQL Server. Source-run receipts
  record each query's rows, not its time.
- **Writing to SQL Server is not measured.** The live adapter is not built. The fixture store received 8.2 GB in the
  window: 6.9 GB in the first job (the snapshot, its dashboard parts and the raw rows; earlier measurements put the
  parts at 4.8 GB and the snapshot at 1.7 GB) and 1.3 GB for the plan.

**What the owner asked about preparing earlier** (who holds which certificate, which job needs which):

| Table or step | Rows | Seconds in the window |
| --- | --- | --- |
| Certificate requirements per job (`soi_cert_requirements`) | 326,657 | 4.4 to digest |
| Certificates held (`mechanic_certs`) | 3,468 | under 0.1 to digest |
| People, pools and availability (roster, team map, attendance) | about 8,400 | 8.6 to digest |
| The scheduler's working data, built again in every scheduling job | | 18.4 |

Preparing these earlier saves seconds in FOCUS. It matters for the pull: the requirement rows are 27% of all source
rows and change rarely, so they belong in a preload before the cut (`DECISION_PLAYBOOK.md`, DEC-08).

**The forecast** is the plan the cost objective compares in full: all 50 aircraft had a cost (`cost_steering`
`full`). In the executable plan every aircraft's finish was unknown, because open parts were not yet received, so its
candidates tied on cost (`cost_steering` `none`); its first pass still orders work by each aircraft's daily rate and
slack. The forecast's three jobs took 27.1 minutes, so delivery dates and costs reached the dashboard 50 minutes
after the data cut. Its first pass took 276 s.

**Repair instead of a full solve** (the same admitted data scheduled again, so nothing had changed: a repair's best
case). The scheduler took 32 s for the executable plan where the full first pass took 145 s, and 72 s for the
forecast where it took 276 s. The job's other costs stay: loading the snapshot, building the candidate and the
validations.

**The dashboard**, one web process opening each read operation that needs no parameter: 0.6 s to start, 315 MB at its
peak. The first request took 6.8 s for the budget view, 3.6 s for quality, 2.5 s for the arena and 1.9 s for the
Gantt; every other view answered in under 0.6 s, and every view in under 0.6 s the second time. Views that need a
team or a person were not opened here (`DISPLAY_PACKAGE.md` has them).

**What would bring it under 20 minutes** is in `DECISION_PLAYBOOK.md`, DEC-36. Nothing was changed here.

## The pre-shift flow after D-33 (November 11, 2026)

The owner's requirement became one schedule through delivery inside the 20 minutes: the shift plan and the delivery
plan (`DECISIONS.md`, D-33). Since the measurement above:

- the delivery plan keeps every slot of the shift plan and plans the rest of the flow around them;
- each validation is computed once per plan: FINAL by the scheduling run, DETACHED by the stager, READBACK by the
  signer on the bytes it signs. A plan was validated eight times before;
- every pre-shift run repairs the accepted plans; the full solve is nightly;
- the `validate` step takes the solve's receipt and the candidate its process has just stored;
- a job ends when its outputs are written (`FOCUS_FAST_EXIT`);
- the snapshot's dashboard parts are built without a slow record copy.

**Re-measured at 10 aircraft** with the same tool, each role in its own process: 303,739 source rows, a first run, so
both plans were solved in full.

| Job | Seconds | Of which |
| --- | --- | --- |
| source: `acquire`, `admit` | 81 | snapshot built 21, stored 19, its dashboard parts 26 |
| app: `schedule`, `validate` | 95 | snapshot loaded 34, working data 7, scheduler 41, FINAL validation 8 |
| stager: `stage_readback` | 59 | snapshot loaded 33, decode and DETACHED validation 14 |
| signer: `sign_accept` | 67 | snapshot loaded 33, READBACK validation 16, dashboard parts 3, acceptance 4 |
| **Data cut to the accepted shift plan** | **302** (5.0 min) | |
| app: `forecast` | 126 | snapshot loaded 35, working data 7, scheduler 60, FINAL validation 10 |
| stager: `forecast_stage_readback` | 86 | snapshot loaded 34, decode and DETACHED validation 29 |
| signer: `forecast_sign_accept` | 95 | snapshot loaded 33, READBACK validation 32, dashboard parts 7 |
| **Shift plan accepted to the accepted delivery plan** | **307** (5.1 min) | |
| **The whole schedule** | **609** (10.2 min) | |

- **The delivery plan held the shift plan slot for slot:** its 6,337 commitments and its 3,193 later slots, none
  re-placed.
- **One validation per job.** The stager computed DETACHED only and the signer READBACK only, for both plans.
- **A repair of the same data**, which is what a pre-shift run does after the first: the scheduler took 13 seconds
  for the shift plan (41 in full) and 22 for the delivery plan (60 in full), and kept every slot.
- **The six snapshot loads are a third of the flow** (203 of 609 seconds). Options (g), (h) and (i) of
  `DECISION_PLAYBOOK.md`, DEC-36 are about them.

**Re-measured at 50 aircraft** (November 11 and 12, 2026) with the same command as the first measurement, on the
same machine: 1,216,781 source rows, each role in its own process. The receipt is
`evidence/production-scale/pre_shift_timing_50_after_d33.json`.

*A first run on an empty store,* so both plans were solved in full:

| Job | Before | Now | Of which, now |
| --- | --- | --- | --- |
| source: `acquire`, `admit` | 341 | 294 | rows read 10, raw rows kept 25, snapshot built 72, stored 61, its dashboard parts 105 |
| app: `schedule`, `validate` | 375 | 309 | snapshot loaded 107, working data 21, scheduler 139, candidate 20, FINAL validation 19 |
| stager: `stage_readback` | 325 | 214 | snapshot loaded 104, candidate read 11, serialized 16, decode and DETACHED validation 66 |
| signer: `sign_accept` | 350 | 240 | snapshot loaded 110, candidate read 11, serialized 17, READBACK validation 74, dashboard parts 9, acceptance 8 |
| **Data cut to the accepted shift plan** | **1,392** (23.2 min) | **1,057** (17.6 min) | |
| app: `forecast` | 612 | 420 | snapshot loaded 112, working data 21, scheduler 204, candidate 46, FINAL validation 36 |
| stager: `forecast_stage_readback` | 493 | 291 | snapshot loaded 106, candidate read 31, decode and DETACHED validation 105 |
| signer: `forecast_sign_accept` | 520 | 328 | snapshot loaded 107, candidate read 31, READBACK validation 115, dashboard parts 20, acceptance 15 |
| **Shift plan accepted to the accepted delivery plan** | **1,626** (27.1 min) | **1,040** (17.3 min) | |
| **The whole schedule** | **3,018** (50.3 min) | **2,097** (35.0 min) | |

Peak memory was 9.0 GB in the source job, 7.5 to 8.2 GB in the shift plan's jobs and 7.9 to 10.4 GB in the delivery
plan's. The store received 8.2 GB for the snapshot and the shift plan and 2.7 GB for the delivery plan.

*A run after the first repairs* (the same admitted data scheduled again in repair mode, so nothing had changed: a
repair's best case; `PLAN_REPAIR.md` has repairs on changed data):

| Scheduling job | Full solve | Repair | The scheduler in it | Slots kept |
| --- | --- | --- | --- | --- |
| shift plan | 309 | 220 | 32 (139 in full) | all 14,038 beyond the commitments |
| delivery plan | 420 | 308 | 68 (204 in full) | all 123,011: the shift plan's and its own previous plan's |

- **With repairs, the data cut to the accepted shift plan is 968 seconds (16.1 minutes),** and the delivery plan's
  three jobs take 927 more (15.5 minutes): **31.6 minutes for the whole schedule.** Staging and signing a repaired
  plan cost what they cost for the first.
- **The first report of this measurement had the delivery plan's repair at 407 seconds.** A profile found 120 of them
  in one new function, which worked out the job releases that kept slots wait for by walking the same chains again
  for every slot (`greedy.hint_leads`). It now takes the slots earliest first and visits each phase once. The two
  repairs were measured again; the seven jobs of the first run were not.
- **Where the 31.6 minutes go:**

  | Action, over the seven jobs of a repairing run | Seconds | Share |
  | --- | --- | --- |
  | Loading the admitted snapshot from the store, once in each of the six jobs after the first | 642 | 34% |
  | Validations: FINAL (25 and 39), DETACHED with its decode (66 and 105), READBACK with its decode (74 and 115) | 424 | 22% |
  | The source job: snapshot built 72, stored 61, its dashboard parts 105, raw rows 25, the rest 31 | 294 | 16% |
  | The candidates: built and stored in the scheduling jobs (29 and 70), read again by stager and signer (84), serialized (54) | 237 | 13% |
  | The scheduler (32 and 68) and its working data (21 and 21) | 142 | 7% |
  | Stage written and read back, plan dashboard parts, signing, acceptance, process starts and the rest | 155 | 8% |

  The scheduler is 100 seconds of 1,895. Reading stored JSON back into objects (the snapshot six times, each plan
  four times) is about half.
- **Since this measurement, the typed decoder takes short paths through well-formed data** (`codec.from_jsonable`;
  `tests/unit/test_codec_fast_path.py` holds it against the full rules on valid and damaged data). The snapshot of
  this measurement then loads in 67 seconds instead of 105, to the same verified hash, and the stored shift plan
  decodes in 1.5 seconds instead of 3.8. The seven jobs have not been timed again with it.
- **The 20 minutes are met for the task lists and not for the whole schedule.** The shift plan and its dashboard
  data are accepted 16.1 minutes after the data cut (17.6 on a first run); the delivery plan, with the dates and the
  late cost, 15.5 minutes later. The pull from SQL Server, each job's start on a runner and the acceptances come on
  top (`DECISION_PLAYBOOK.md`, DEC-36).

**The application at 50 aircraft** (`evidence/production-scale/team_lead_view_50.json`; the store of the measurement
above, one web process):

| Request | First after a start | The same again |
| --- | --- | --- |
| The 37 views that need no parameter | budget 4.4 s, quality 3.3 s, Gantt 1.6 s, every other under 1 s | every one under 0.6 s |
| The team lead's view, each of 34 teams | median 9.4 s, slowest 14.9 s | median 1.3 s, slowest 2.6 s |

- **The team lead's view does not open at once.** A team's first open reads and decodes the team's work on each of
  the 50 aircraft and the aircraft's dependency graphs (about 100 display parts for one team), assesses every open
  job of the team as a possible addition (4,720 for one team) and sends 1.8 MB.
- **Reading ahead does not cover it at this size.** `web.warm` stopped after 3 of the 34 teams: their parts fill the
  part cache (192 MB). The other teams' first opens took as long as in a cold process (median 9.5 s).

## What changed in the native-core trial

- **Native core** (`native/`, `docs/execution/NATIVE_CORE.md`): the duty ledger and crew selection in Rust, with
  byte-identical plans. A crew choice now costs about 17-36 µs in Rust.
- **Part-release check (V9)**, a validator check, now runs in one pass. It used to build and sort every assignment's
  full predecessor set, which cost 44 s of an 87 s profiled one-aircraft run. Now it walks once forward from each part
  release and each blocking shortage. Its findings are identical to the old check's on every corruption in the A47 test
  corpus and on forged work behind blocking shortages (`tests/unit/test_validator_v9_one_pass.py`).

## Where the time goes now (5 aircraft, native core)

`evidence/production-scale/PROFILE_5_AIRCRAFT_NATIVE.md` is a cProfile of the 239 s run. The profiler roughly doubles
wall time, so the shares below are what matter.

| Share of the profiled run | What |
| --- | --- |
| **65 %** | **Two scheduling passes.** They made 2.38 million crew-selection attempts for 34,000 phases: about 69 per phase, because start times are probed one at a time. Per attempt: the Rust crew choice, a hashed assignment id, shift and calendar arithmetic in Python, and the Python loop itself. |
| 18 % | **Independent validation**, about two-thirds of it re-hashing the whole snapshot and the candidate. |
| 10 % | **Sealing and storing the candidate** (canonical JSON and SHA-256 of about 17,000 assignments and their duties). |
| 7 % | **Context construction and the rest.** |

Loading the data (acquisition, admission and snapshot build) is outside the profile: 178 s at 5 aircraft.

**Extrapolation to 50 aircraft.** From 1 to 5 aircraft, the native plan time grew 9.4× for 3.2× the open work, because
each phase searches a larger pool. Fifty aircraft is about 10× the open work of five, which puts one plan at well over
an hour today. So it is not viable yet.

## The path to under 10 minutes (ordered by payoff)

Each step keeps two guarantees:
- **Same plans:** the native path's plans stay byte-identical to the Python reference's, checked by the parity tests.
- **Independent validator:** every plan is still re-checked from source.

1. **Move the time-slot search into Rust.**
   - Ported: the shift scan, candidate starts, calendar advance, assignment ids and crew choice. Only finished
     placements cross back into Python.
   - Removes most of the Python cost per attempt, which is the bulk of the 65 %.
2. **Skip start times that cannot work, exactly.**
   - Keep a per-person index of free time, so a start time with fewer free qualified people than the crew needs is
     skipped without a full crew attempt.
   - Targets the 69 attempts per phase without changing which start is chosen.
3. **Hash less, and faster.**
   - Verify an admitted snapshot once per process, keyed by its content hash.
   - Use a faster canonical encoder, and never re-hash an unchanged candidate.
   - Targets the 28 % spent in validation and sealing.
4. **Load incrementally.** Apply the shift's changes to the last admitted snapshot instead of rebuilding it from
   scratch: completions, new rework, shortage changes and attendance. This is the delta pipeline described for the
   15-minute shift-end window.
5. **Warm start and parallel search.** Start from the current plan and evaluate search moves on several cores.

Each step will be measured with `tools/benchmark_production.py` at 1, 5, 10, 25 and 50 aircraft before the next one
starts.

## The 15-minute shift-end window (target budget)

| Step | Budget |
| --- | --- |
| Delta pull from Teradata to SQL Server (changed rows only) | ≤ 2 min |
| Apply the changes to the in-memory snapshot and admit them | ≤ 1 min |
| Plan: update the in-shift plan, improve until the deadline, always holding a valid plan | ≤ 8 min |
| Independent validation, staging, readback, sign, accept | ≤ 2 min |
| Publish (one static snapshot per shift) | ≤ 1 min |

The in-shift runs that keep a warm plan ready are internal only: never displayed, never published.
