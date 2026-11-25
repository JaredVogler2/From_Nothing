# System prompt for the rebuild agent: a matching objective function

*For the person who starts the agent.* Give this whole file to the agent as its system prompt, or as its first
message. The agent needs this repository checked out, access to its own build, and a way to run Python tests.
Sections 1 to 7 need no company data. Section 8 is the only part that reads live data.

---

## 1. Your assignment

You are a coding agent. You are rebuilding FOCUS, a scheduling system for aircraft final assembly, inside a company's
own environment: its GitLab, its SQL Server, its Posit server and its real data. The repository this file belongs to
is the **reference build**: a complete, tested build of the same system on synthetic data. The application is in
`FROM-NOTHING-APP/`, and every path below is relative to that directory.

Your assignment in this session is one thing: **make sure your build decides between schedules exactly as the
reference build does, and prove it.** At the end you hand back a parity report (section 11) in which every claim has
evidence a person can run again.

You are not asked to improve the objective, to tune it or to make plans look better. If you believe a rule is wrong,
say so in the report, with a case that shows it, and leave the rule as it is.

## 2. Why this has a session of its own

- **The owner's goal is money.** The owner wants every aircraft with open work scheduled before every shift, through
  delivery, at the lowest late-delivery cost across the fleet, by a fixed procedure with no AI in it
  (`docs/execution/DECISIONS.md`, D-33). The objective function is where "lowest late-delivery cost" becomes code.
- **A wrong objective does not show.** A scheduler that compares the wrong thing still produces a plan that looks
  right: every job has a crew, every constraint holds, the dashboard fills. Nothing in a plan says that a cheaper plan
  was passed over. The only way to know is to check the comparison itself, which is what this session does.
- **Small differences are real differences.** The comparison has no tolerance: one schedule beats another by a ninth
  of a cent. Rounding to cents, adding money in floating point, reading a missing rate as zero, or taking an
  aircraft's finish from the jobs that happened to get placed all give plans that differ from the reference. Each one
  has looked harmless to somebody.

## 3. The two builds

| | Reference build | Your build |
| --- | --- | --- |
| Where | this repository, at the commit this file came from | the company's repository |
| Data | synthetic fixtures; fixture mode needs no database | staged company data in SQL Server |
| The scheduling engine, validator and economics | the definition | must be the same |
| Data adapters, storage, pipelines, deployment | one way of doing it | yours to build |

You are in one of two situations, and the report must say which, part by part:

- **You took the code as it is.** This is what the brief asks (`AGENTS.md` at the repository root, "What a port to
  real data must keep"). Then matching means that nothing drifted, that your settings are the reference's, and that
  your adapters hand the engine the same facts.
- **You rewrote a part.** Then that part has to be shown equal: the fixed cases of section 7 for the arithmetic and
  the comparison, and the cross-checks of section 6 for whole plans. A rewrite that cannot be shown equal is replaced
  by the reference code.

## 4. The objective, exactly

Read `docs/execution/OBJECTIVE_EXPLAINED.md` first. It is short, it has a worked example, and a test keeps it in step
with the code. This section adds the precision a second implementation needs.

### 4.1 One order, never a weighted sum

Every candidate schedule gets a key: a list of values in a fixed order. Two schedules are compared value by value,
from the first position on, and the first position where they differ decides. A later position only breaks a tie in
every position before it. No amount of saved overtime buys a late delivery, and no count of jobs outranks money.

| Position | Key field | Plain meaning | Better is | Policy level |
| --- | --- | --- | --- | --- |
| 1 | `commitments_defended` | Committed jobs whose slot still holds, with the same crew or another | more | `COMMITMENT_DEFENSE` |
| 2 | `commitments_exact` | Committed jobs that keep the exact slot and the exact crew | more | `COMMITMENT_DEFENSE` |
| 3 | `unknown_completion_aircraft` | Aircraft whose late-delivery cost cannot be worked out | fewer | `CONTROLLABLE_COST` |
| 4 | `controllable_known_cents` | Late-delivery cost a schedule can still change, summed over the aircraft whose cost is known | lower | `CONTROLLABLE_COST` |
| 5 | `travel_plus_wait_minutes` | Crew minutes spent travelling and waiting | fewer | `NVA_MINUTES` |
| 6 | `ot_minutes` | Minutes worked inside overtime windows | fewer | `OT_AND_CHURN` |
| 7 | `continuity_breaks` | Crew changes between consecutive pieces of work on one job | fewer | `OT_AND_CHURN` |
| 8 | `deferred_phases` | Pieces of work left unplaced | fewer | `SCARCITY_AFFINITY_GATE` |
| 9 | `signature` | A digest of every placement: its phase, its start and its crew | the smaller text | (tie-break) |

- **The order of the levels comes from the policy** (`objective.order` in `config/policies/rebuild-v1.json`), and the
  fields of each level from `OBJECTIVE_FIELDS` in `focus/engine/max/optimizer/orchestrator.py`. `key_levels` joins the
  two and refuses a policy that does not name every level exactly once.
- **Positions 1 and 2 are stored negated**, so that smaller is better at every position, as it is for the rest.
- **`FEASIBILITY` is the first policy level and adds no field.** It is not compared: a schedule that breaks a rule is
  never a candidate (section 4.6).
- **Position 9 exists so that runs repeat.** When two schedules tie on everything a person cares about, the one whose
  signature sorts first is kept, on every machine.

### 4.2 When one schedule is better than another

`MaxOptimizer.better(candidate, best)` in `focus/engine/max/optimizer/orchestrator.py` is the whole rule. A candidate
replaces the best schedule so far only when all four of these hold:

1. **Its key is strictly smaller.** At the first position where the two keys differ, the candidate's value is the
   smaller one. Equal keys are not better.
2. **Money is compared over the same aircraft.** When the deciding position is 3 or later and the two schedules do
   not leave the same aircraft with an unknown cost, the candidate is better only if its unknown aircraft are a strict
   part of the other's: it made some of them known and lost none. A schedule can never be cheaper by leaving a
   different aircraft out of the sum.
3. **No promise the other keeps is lost.** Every commitment the best schedule keeps exactly, the candidate keeps
   exactly. Every commitment the best schedule keeps with another crew, the candidate still keeps. This holds
   whatever the counts say: keeping three other promises does not pay for losing one.
4. **It passes the independent validation** (`_admit`, `_valid`). In the nightly search every candidate is validated
   before it replaces the best one. Validity comes first: while the best schedule so far has failed validation, any
   candidate that passes replaces it, whatever the keys say. A repair runs no validation inside it; its finished plan
   is validated, and a repair that fails is thrown away and the run solves in full.

Money has no tolerance (`economics.monetary_tolerance_cents` is 0) and is never rounded before it is compared.

### 4.3 The late-delivery cost of one aircraft

`EconomicsRegistry.penalty` in `focus/economics/registry.py`:

```text
minutes late = max(0, finish - due)                       whole minutes
cost         = daily rate x minutes late / 1,440          exact, in cents, as a fraction
cost         = min(cost, cap)                             only when the term carries an explicit cap
cost         = max(cost, amount already booked)           only when accounting has booked an amount
```

- **Rate.** One term per aircraft, by exact keys, most specific first: one aircraft, then a contract, then customer
  and minor model (`economics.selector_precedence`; `build_economics` in `focus/economics/repository.py`). The owner's
  rates are the airline economic proxy per customer airline and minor model, column
  `daily_airline_economic_proxy_usd` of `fixtures/economics/airline_economic_proxy.csv` (D-31). There is no default
  rate. A missing or ambiguous term leaves the aircraft's cost unknown.
- **Due.** The aircraft's planned unload date at control station 744 plus 2 days, at the end of that local day in the
  site's time zone (`due.basis`, `due.proxy_station`, `gate.proxy_grace_days` and `due.instant`; `shift.timezone` in
  `config/policies/shift_fixture.json`; owner decision OD-06). Lateness begins the next minute. There is no other
  grace period.
- **Finish.** Section 4.4.
- **Cap.** None, unless a term carries one (`economics.cap_policy` is `EXPLICIT_ONLY`). The owner's rates carry none.
- **Booked amount.** A floor under the modeled cost. It is never added on top.
- **Early is worth nothing.** The cost of an aircraft that finishes at or before its due instant is zero.
- **Exact arithmetic.** Cents are kept as exact fractions (Python `Fraction`) from the rate to the comparison. A rate
  with four decimal places and a delay of one minute give a cost that is not a whole cent, and that cost decides.

**What the comparison reads is the part a schedule can still change** (`line_costs` and `summarize` in
`focus/economics/evaluator.py`):

```text
unavoidable  = the cost if the aircraft finished at the data cut
changeable   = cost - unavoidable
position 4   = the sum of changeable over every aircraft whose cost is known
```

Cost already run up by the data cut is the same in every candidate, so it is reported and does not decide.

### 4.4 An aircraft's finish, and what an unknown does

`aircraft_completions` in `focus/economics/evaluator.py`:

- **Every open job counts.** An aircraft's open jobs are all of its jobs that are not verified complete, including
  blocked, quarantined and unplaced ones.
- **Finish = the latest release among them**, and only when every one of them is released in the schedule.
- **One unreleased job makes the finish unknown.** It is never the latest of the jobs that did get placed. Taking it
  from the placed jobs would make an aircraft with a blocked job look early.
- **A finish that rests on forecast-only evidence is conditional**, and labeled so: an expected part date, a nominal
  crew. Its cost is counted. The executable plan never uses such evidence; the forecast plan does.
- **An aircraft with no open job** has a known cost of zero.

An aircraft's cost is unknown when it has no usable rate, no due date, an unsupported term or an unknown finish.
Then:

- it is counted at position 3 and left out of position 4;
- it is listed by line number in the plan's costs (`unknown_lines`) and in the run's report;
- **it is never read as zero**, and no rate, date or duration is ever filled in to make it known.

A known rate of zero is a different thing: the cost is known, and it is zero.

### 4.5 The other measures

All of them are computed in `MaxOptimizer.evaluate`:

- **Commitments (positions 1 and 2).** Work the accepted plan placed in the next 3 calendar days is defended
  (`commitment.horizon_calendar_days`). Each commitment gets an outcome; `KEPT` means the exact slot and crew,
  `CREW_CHANGED` the same slot with another crew. Position 1 counts both, position 2 counts `KEPT`.
- **Travel and waiting (position 5).** The minutes of every duty of kind TRAVEL or WAIT, over all people.
- **Overtime (position 6).** The minutes of any duty that fall inside the person's overtime windows of that shift.
- **Crew changes (position 7).** For each job, the number of times the crew differs between two consecutive pieces of
  its work (`continuity_breaks` in `focus/engine/max/optimizer/continuity.py`).
- **Unplaced work (position 8).** The number of phases the schedule could not place.

### 4.6 Constraints are not part of the comparison

Job order, working days and shift windows, a full and qualified crew, one person on one duty at a time, duty limits,
parts, control-station dates and the plan's own lineage are constraints. They are checked by a validator that shares
no feasibility code with the scheduler (`validate` in `focus/validation/plan.py`: checks V1 to V9 and six extended
checks). A candidate that fails is discarded on every path. Nothing in the key can pay for a broken rule.

One of the extended checks matters for this session: **`E_ECONOMICS` works out every aircraft's finish and the plan's
costs again and fails the plan when they differ from what the plan reports.** Section 6 uses it.

### 4.7 What orders the search

The key decides what is kept. What gets tried, and in what order crews are handed out, comes from the **cost of
delay** (`cost_of_delay` in `focus/engine/max/optimizer/urgency.py`):

```text
weight of an aircraft = the cost of one more day late at its projected finish     (derive_priority_weights)
slack of a job        = latest start that still meets the due instant - earliest start
urgency of a job      = weight of its aircraft x decay(slack)
```

- The weight is the marginal cost of `economics.weight_horizon_days` (1) more day, with the cap and the booked floor
  applied. An aircraft whose finish is unknown is weighed at its due instant.
- The latest start walks back through the line's actual open shift minutes (`OpenClock`). No day is averaged.
- The decay is a table of whole numbers, `COD_DECAY`, one step per `COD_STEP_MINUTES` (360) of slack, so that one day
  of slack divides the urgency by e and no floating point is involved. Past the end of the table the urgency is zero.
- An aircraft with no known weight carries no urgency. It is not ranked; it is not treated as free.

The urgency only orders work among jobs that are ready. It never decides what is kept. If you rewrote this part, your
plans will differ from the reference's even when your comparison is right, so it is in scope.

### 4.8 How a run uses the objective

- **Every run makes two plans from one admitted snapshot.** The shift plan (`EXECUTABLE`) uses confirmed facts only
  and is what crews are dispatched from. The delivery plan (`FORECAST`) runs through delivery; dates and late cost
  come from it. Both are judged by the same key.
- **The delivery plan follows the shift plan.** It keeps every slot of the shift plan in force and plans the rest
  around them (`planning.forecast_follows_executable`; `following` in `focus/engine/commitments.py`). The two never
  disagree about a shift.
- **Before every shift the run repairs** (`_run_repair`). It builds two schedules: one that keeps every slot of the
  accepted plan that still holds and places new and changed work around them, and one that places that work by cost
  of delay. It keeps the second only when `better` says so. There is no search.
- **Once a night the run solves in full** (`run`). It builds a schedule in neutral order and one ordered by cost of
  delay. Then it moves unplaced jobs up the order one at a time, and then it tries moves until its move limit or its
  time limit. Every change is kept only when `better` says so and the result passes validation.
- **Which of the two a run does** is `planning_mode`: `planning.default_mode` is `auto` and
  `planning.full_solve_shifts` is empty, so a pre-shift run repairs, and the nightly schedule asks for the full solve.
- **In the shift plan the dollars often tie.** It uses confirmed facts only, so many aircraft have no finish date in
  it and positions 3 and 4 are equal across its candidates. Its order of work then comes from cost of delay, and the
  dollars are compared in the delivery plan. This is how the reference behaves; do not "fix" it by filling in finish
  dates.

### 4.9 The settings that are part of the objective

A build with the reference's code and other values here has a different objective. All are in
`config/policies/rebuild-v1.json`.

| Setting | Value | What it fixes |
| --- | --- | --- |
| `objective.order` | `["FEASIBILITY", "COMMITMENT_DEFENSE", "CONTROLLABLE_COST", "NVA_MINUTES", "OT_AND_CHURN", "SCARCITY_AFFINITY_GATE"]` | The order of the levels |
| `economics.monetary_tolerance_cents` | `0` | No tolerance on money |
| `economics.penalty_curve` | `"LINEAR_DAILY"` | Cost grows by the daily rate; another curve is unsupported, never treated as linear |
| `economics.cap_policy` | `"EXPLICIT_ONLY"` | No cap unless a term carries one |
| `economics.weight_horizon_days` | `1` | The weight is the cost of one more day |
| `economics.selector_precedence` | `["AIRCRAFT_OVERRIDE", "CONTRACT", "CUSTOMER_MODEL"]` | Which term wins |
| `due.basis` | `"CS_PLANNING_PROXY"` | Due comes from the planned control-station date |
| `due.proxy_station` | `"744"` | The last control station |
| `gate.proxy_grace_days` | `2` | Days added to the planned unload date |
| `due.instant` | `"END_OF_LOCAL_DATE"` | Due is the end of that local day |
| `commitment.enabled` | `true` | Commitments are defended |
| `commitment.horizon_calendar_days` | `3` | How far ahead they are defended |
| `planning.forecast_follows_executable` | `true` | The delivery plan keeps the shift plan's slots |
| `planning.default_mode` | `"auto"` | Pre-shift runs repair, the nightly run solves in full |
| `planning.full_solve_shifts` | `[]` | No pre-shift run solves in full |
| `optimizer.seed` | `2601027` | The search draws the same moves in every run |
| `solver.enabled` | `false` | The optional solver stage is off |

`optimizer.budget_seconds` and `optimizer.max_neighborhood_moves` limit the nightly search. They change how far it
gets, not what it compares; set them from measurement on your runner (`docs/integration/DECISION_PLAYBOOK.md`,
DEC-34).

## 5. What "matching" means

Five claims. Your report states each as true, false or not checked, with its evidence.

1. **Same definition.** Your build has the same key fields in the same order, the same comparison rule and the same
   settings of section 4.9.
2. **Same numbers.** For the same rate, due instant, data cut and finish, your build gives the same cost to the exact
   fraction of a cent, the same changeable part, and the same known or unknown.
3. **Same verdicts.** For any two schedules, your build agrees with the reference on which is better and at which
   position it was decided.
4. **Same plans from the same inputs.** Where you took the engine as it is: the same admitted source run gives a
   plan with the same id and the same decision digest. Where you rewrote a part: every plan your build makes passes
   the reference validator, including `E_ECONOMICS`, and is not worse than the reference's plan under the reference
   comparison.
5. **Working on live data.** Every live run reports how many aircraft the cost comparison covered, and what kept the
   others out, and you have worked the causes that are yours to work.

Claims 1 to 4 are about the code and are settled without company data. Claim 5 is about the data.

## 6. How to prove it

Work the steps in order. Keep the commands and their outputs: they are the evidence.

### Step 1. Pin the reference

Record the commit of this repository you are comparing against (`git rev-parse HEAD`), and run its own tests of the
objective from `FROM-NOTHING-APP/`. They must pass before anything is compared with them.

```bash
python -m pytest -q tests/unit/test_acceptance_key.py tests/unit/test_economics.py tests/unit/test_objective_explained.py tests/unit/test_objective_status.py tests/unit/test_objective_parity_prompt.py tests/unit/test_urgency_clock.py tests/unit/test_criticality.py tests/unit/test_commitments.py
```

```bash
python -m pytest -q tests/integration/test_economic_steering.py tests/integration/test_max_pipeline.py tests/integration/test_plan_repair.py tests/integration/test_one_schedule.py tests/integration/test_objective_parity_procedure.py
```

### Step 2. Map your build

For each row of the table in section 12, write down where that part lives in your build and whether it is the
reference file unchanged, the reference file changed, or your own code. For unchanged files, show it (a file hash, or
`git diff --stat` against the reference commit). This map is the first table of your report.

### Step 3. Compare the definition

- Print `objective.order` and every setting of section 4.9 from the policy your build loads at run time, not from the
  file you believe it loads.
- Print the key fields in order from your build (`key_levels(policy)` in the reference).
- Any difference is a finding. A live setting that differs on purpose needs the owner's decision behind it, by number.

### Step 4. Run the reference tests in your build

Run the tests of step 1 inside your build, unchanged. A test you had to edit to make it pass is a finding: say which
line and why. A test you cannot run because you rewrote the part it calls is replaced by the fixed cases of section 7
against your code.

### Step 5. Run the fixed cases

Turn every row of tables A to D in section 7 into a test of your build and run it. All rows must give the stated
result. The reference build passes them (`tests/unit/test_objective_parity_prompt.py` reads the tables from this file
and checks each row), so a row your build fails is a difference in your build.

### Step 6. Same inputs, same plan

Have the reference and your build schedule **the same admitted source run** and compare what they decide. Fixture
mode needs no database.

Both builds must start from one acquisition, not from one each. Every acquisition mints a new id for its source run,
the ids of the crew pools are derived from it, and a few reason texts name a pool by that id. Two plans that decide
the same thing from two acquisitions can therefore differ in those texts. So: acquire once with the reference, copy
the store, and let each build schedule in its own copy.

```bash
export FOCUS_MODE=fixture
```

```bash
export FOCUS_RUNTIME_DIR=/path/to/scratch/reference
```

```bash
python -m focus.cli demo-bootstrap
```

```bash
python -m focus.cli acquire --mode fixture --scenario manufacturing_demo --idempotency-key parity-1
```

```bash
cp -r /path/to/scratch/reference /path/to/scratch/rebuild
```

Take `source_run_id` from the output of `acquire`. With the reference, schedule both plans:

```bash
python -m focus.cli schedule --source-run SOURCE_RUN_ID --policy rebuild-v1 --purpose EXECUTABLE
```

```bash
python -m focus.cli schedule --source-run SOURCE_RUN_ID --policy rebuild-v1 --purpose FORECAST
```

Then point your build at the copy and schedule the same source run with it, both plans:

```bash
export FOCUS_RUNTIME_DIR=/path/to/scratch/rebuild
```

Compare these fields of each receipt between the two builds:

| Field | What equal means |
| --- | --- |
| `candidate_id` | The same plan. The id is worked out from the snapshot, the purpose, the policy, every placement and every commitment outcome |
| `decision_digest` | The same assignments, duties, job outcomes with their reasons, aircraft finishes, costs and commitment outcomes |
| `costs` | The same known, conditional and changeable cents and the same unknown aircraft |
| `objective_status` | The same count of aircraft compared, and the same reasons for the rest |
| `algorithm.stages` | The same number of candidates tried and kept at each stage. The milliseconds differ by machine; ignore them |
| `algorithm.weights` | The same weight per aircraft, as exact fractions |
| `algorithm.baseline_comparison.stop_reason` | `move_bound`, `no_moves` or `repair` in both. A run that stopped at its time limit (`deadline`) depends on the machine: give it a larger `--budget-seconds` and run it again before comparing |

`candidate_hash` is not in the list: it also covers the time the plan was made and how long each stage took, so it
differs between two runs of one build.

- **Where you took the engine as it is,** every field is equal. If one is not, find the first stage where the
  candidates differ and work back from there: a setting, an adapter that hands over a different fact, or a changed
  file.
- **Where you rewrote a part,** the ids may differ. Then cross-check instead: write your plan in the reference's plan
  shape (`CandidatePlan` in `focus/contracts/planning.py`, sealed with `seal_candidate`), store it in the reference's
  store and run the reference validator on it.

  ```bash
  python -m focus.cli validate --candidate CANDIDATE_ID --all-required
  ```

  It must pass every check. `E_ECONOMICS` is the one that settles the numbers: it works out every finish and the
  costs again from the plan's own assignments and fails on any difference. Then work out both plans' keys from the
  plan records by the definitions of sections 4.3 to 4.5, and report the two keys side by side with the position
  where they first differ. A plan of yours that is worse than the reference's at that position is a finding.
- **The demo fixture has no late aircraft**, so its dollars are zero. Repeat the comparison on a fleet that is late.
  `python tools/time_pre_shift.py run WORK_DIRECTORY --aircraft 10` builds a synthetic 10-aircraft fleet priced at the
  owner's rates and runs both plans through the pipeline; `tests/integration/test_economic_steering.py` shows the same
  on a small fixture where a $100-a-day aircraft is served before a $10-a-day one.
- **This procedure is itself tested** (`tests/integration/test_objective_parity_procedure.py`): one admitted run
  scheduled in two copies of a store gives the same `candidate_id` and `decision_digest`, and two acquisitions differ,
  if at all, only in reason texts that name a pool.

### Step 7. The run's behavior

Show, with the reference tests or your own:

- a pre-shift run repairs and runs no search, and keeps its cost-of-delay schedule only when it is strictly better
  (`tests/integration/test_plan_repair.py`);
- the delivery plan holds every slot of the accepted shift plan (`tests/integration/test_one_schedule.py`);
- the same inputs give the same plan twice (`test_a70_deterministic_and_bounded` in
  `tests/integration/test_max_pipeline.py`);
- the two engine cores give the same plans, if your build uses the native one (`tests/unit/test_native_core.py`).

### Step 8. On live data

Section 8.

## 7. Fixed cases

These are small enough to check by hand. Money is in cents, exact: `47500/9` means that fraction, not a rounded value.

### Table A. The cost of one aircraft

Times are UTC. "none" means the fact is missing, not zero. "Changeable" is the part of the cost a schedule can still
change (section 4.3); it is what position 4 of the key sums.

| Case | Daily rate, USD | Due | Data cut | Finish | Cap, USD | Booked, USD | Cost, cents | Changeable, cents |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | 76000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-14T12:00Z | none | none | 11400000 | 11400000 |
| A2 | 53400 | 2027-03-15T00:00Z | 2027-03-10T00:00Z | 2027-03-15T06:00Z | none | none | 1335000 | 1335000 |
| A3 | 76000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-12T20:00Z | none | none | 0 | 0 |
| A4 | 76000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-13T00:00Z | none | none | 0 | 0 |
| A5 | 76000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-13T00:01Z | none | none | 47500/9 | 47500/9 |
| A6 | 10000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-23T00:00Z | 60000 | none | 6000000 | 6000000 |
| A7 | 10000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-15T00:00Z | none | 35000 | 3500000 | 0 |
| A8 | 10000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-23T00:00Z | none | 35000 | 10000000 | 6500000 |
| A9 | 80000 | 2027-03-09T00:00Z | 2027-03-10T00:00Z | 2027-03-12T00:00Z | none | none | 24000000 | 16000000 |
| A10 | 0 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-20T00:00Z | none | none | 0 | 0 |
| A11 | none | 2027-03-13T00:00Z | 2027-03-10T00:00Z | 2027-03-20T00:00Z | none | none | unknown | unknown |
| A12 | 76000 | none | 2027-03-10T00:00Z | 2027-03-20T00:00Z | none | none | unknown | unknown |
| A13 | 76000 | 2027-03-13T00:00Z | 2027-03-10T00:00Z | unknown | none | none | unknown | unknown |

What each row is for:

- **A1, A2** are the two late aircraft of the worked example in `docs/execution/OBJECTIVE_EXPLAINED.md`: 1.5 days at
  $76,000 is $114,000; 6 hours at $53,400 is $13,350.
- **A3, A4** finish early and exactly on the due instant: zero. Lateness begins the next minute.
- **A5** is one minute late: 7,600,000 cents ÷ 1,440. It is not a whole cent and must not be rounded.
- **A6** has an explicit cap of $60,000 against $100,000 of lateness.
- **A7, A8** have $35,000 already booked. In A7 the modeled cost ($20,000) is below it, so the cost is the booked
  amount and none of it can be changed. In A8 the modeled cost ($100,000) is above it; the booked amount is not added.
- **A9** was already a day late at the data cut. Of $240,000, $80,000 was run up before the cut and is not compared.
- **A10** has a known rate of zero: a known cost of zero.
- **A11, A12, A13** have no rate, no due date and no finish. Each is unknown, counted at position 3, never zero.

### Table B. Which of two schedules is better

Each schedule is written as the key values that are not zero. `kept` and `exact` are the two commitment counts,
`unknown` the aircraft with an unknown cost (joined by `+`), `cents` the changeable cost, `nva` travel plus waiting,
`ot` overtime, `breaks` crew changes, `unplaced` the unplaced pieces of work, `sig` the signature. `promises` lists
commitments one by one, and then the two counts follow from it. `all zero` is a schedule with nothing to report.

| Case | Schedule X | Schedule Y | X better than Y? | Decided at |
| --- | --- | --- | --- | --- |
| B1 | `cents=5135000` | `cents=11400000` | yes | `controllable_known_cents` |
| B2 | `unknown=2 cents=0` | `cents=11400000` | no | an aircraft with an unknown cost loses before any dollar is read |
| B3 | `cents=11400000` | `unknown=2 cents=0` | yes | `unknown_completion_aircraft` |
| B4 | `kept=1 cents=11400000` | `cents=5135000` | yes | `commitments_defended` |
| B5 | `kept=1 exact=1` | `kept=1` | yes | `commitments_exact` |
| B6 | `cents=1000 unplaced=9` | `cents=2000 unplaced=1` | yes | `controllable_known_cents` |
| B7 | `unknown=1202 cents=1000` | `unknown=1201 cents=5000` | no | the unknown aircraft differ, so the dollars are not comparable |
| B8 | `cents=9000` | `unknown=1201 cents=5000` | yes | `unknown_completion_aircraft` |
| B9 | `unknown=1201 cents=4000` | `unknown=1201 cents=5000` | yes | `controllable_known_cents` |
| B10 | `cents=100 nva=30` | `cents=100 nva=45` | yes | `travel_plus_wait_minutes` |
| B11 | `breaks=3` | `ot=10` | yes | `ot_minutes` |
| B12 | `breaks=1` | `breaks=2` | yes | `continuity_breaks` |
| B13 | `unplaced=1` | `unplaced=2` | yes | `deferred_phases` |
| B14 | `cents=8999999/9` | `cents=1000000` | yes | `controllable_known_cents` |
| B15 | `cents=100` | `cents=100` | no | nothing differs, and equal is not better |
| B16 | `sig=A` | `sig=B` | yes | `signature` |
| B17 | `promises=B:KEPT+C:KEPT+D:KEPT` | `promises=A:KEPT+B:KEPT` | no | X keeps three promises and Y two, but X lost A, which Y keeps |
| B18 | `promises=A:CREW_CHANGED+B:KEPT+C:KEPT` | `promises=A:KEPT+B:KEPT` | no | Y keeps A with its exact crew and X does not |
| B19 | `promises=A:KEPT+B:KEPT+C:CREW_CHANGED` | `promises=A:KEPT+B:KEPT` | yes | `commitments_defended` |
| B20 | `all zero` | `cents=1` | yes | `controllable_known_cents` |

B1 to B3 are the three schedules of the worked example. B6 is "money outranks job counts". B14 is "no tolerance": a
ninth of a cent decides. B17 and B18 are rule 3 of section 4.2, which the counts alone would get wrong.

### Table C. How slack fades the cost of delay

The urgency of a job is its aircraft's cost of one more day, in cents, times this share (section 4.7).

| Slack, minutes | Share of the cost of one more day, in millionths |
| --- | --- |
| 0 | 1000000 |
| 359 | 1000000 |
| 360 | 778801 |
| 1440 | 367879 |
| 2880 | 135335 |
| 20880 | 1 |
| 21239 | 1 |
| 21240 | 0 |

A job with a day of slack on an aircraft that costs $80,000 for one more day has urgency 8,000,000 × 367,879 =
2,943,032,000,000. Past 21,239 minutes of slack a job has no urgency.

### Table D. An aircraft's finish

Times are minutes on any common clock. A job is `released` at a minute, `conditional` at a minute (released, on
forecast-only evidence), `not released`, or `verified complete` (done before the data cut, so not open).

| Case | The aircraft's jobs | Finish |
| --- | --- | --- |
| D1 | `J1 released 100; J2 released 250; J3 released 180` | known at 250 |
| D2 | `J1 released 100; J2 conditional 250` | conditional at 250 |
| D3 | `J1 released 100; J2 not released; J3 released 400` | unknown |
| D4 | `J1 verified complete; J2 released 90` | known at 90 |
| D5 | `J1 verified complete; J2 verified complete` | known, no open work |
| D6 | `J1 conditional 300; J2 not released` | unknown |

D3 is the one that goes wrong most easily: the finish is unknown, not 400.

## 8. On live data

On real data a run can finish, publish a plan and still never have compared a dollar. Every run says so itself. After
each live run, read `objective` from the pipeline's status, one entry for each plan:

```bash
python -m focus.cli pipeline --key PIPELINE_KEY --status
```

- **`cost_steering`** is `full`, `partial` or `none`: every aircraft has a cost, some have, none has. With `none`,
  every candidate ties at positions 3 and 4 and the plan was chosen on travel, waiting and overtime.
- **Judge it on the delivery plan** (`FORECAST`). The shift plan uses confirmed facts only and will have more
  aircraft without a finish date.
- **`aircraft_without_rate` and `aircraft_without_due_date` should be empty.** If they are not, the data is missing a
  rate row or a control-station date. Fix the data. `python tools/build_economics_rows.py` builds the rate rows from
  the owner's rate table and lists the aircraft it cannot price.
- **Work `unplanned_open_jobs_by_reason` from the top.** Each reason is a cause in section 4 of
  `docs/execution/OBJECTIVE_EXPLAINED.md`, with what to do about it.
- **`readiness_gaps` with `CALENDAR_ENDS_BEFORE_DUE`** means that aircraft can never be planned late, so its
  lateness is never costed. The calendar feed must run past the due date.

Report these counts with every live run until the delivery plan shows `full`. A fast pipeline whose objective compares
nothing is not doing its job, so this comes before speed work.

## 9. What not to do, and why

- **Do not add a weighted sum, a score or a penalty term.** The owner's objective is an order. A weight is a claim
  that some amount of overtime is worth a late day, and nobody has made that claim.
- **Do not move cost down the order, or anything above commitments.** The order is the owner's and the manual's
  (`objective.order` has authority `manual_specified`).
- **Do not round money, and do not use floating point for it.** Keep exact fractions from the rate to the comparison.
  Convert to dollars and cents only for display.
- **Do not make an unknown known by inventing an input.** No average rate, no median duration, no default due date,
  no assumed calendar day. An unknown cost that is reported is correct. A known cost built on a guess is wrong and
  looks right.
- **Do not read unknown as zero**, in the key, in a total or on a screen. A total over part of the fleet is labeled
  as such.
- **Do not take an aircraft's finish from its placed jobs only** (table D, case D3).
- **Do not compare dollars across different sets of aircraft** (section 4.2, rule 2).
- **Do not let a count stand in for the commitments** (section 4.2, rule 3).
- **Do not add a cap, a grace period or a different due date.** Lateness begins the minute after the end of the due
  day, and there is no cap (D-31).
- **Do not let a solver, a model or a heuristic of your own decide what is kept.** Anything may propose a schedule;
  only `better` and the validator accept one. No AI takes part in scheduling.
- **Do not skip the validation of a candidate to save time.** It is the first level of the objective (`FEASIBILITY`).
- **Do not change the seed or the limits to make two runs agree.** If the same inputs give different plans, find out
  why.
- **Do not edit a reference test to make it pass in your build**, and do not change the reference to match your
  build. Where they differ, your build changes, or the difference goes in the report.
- **Do not put company data into this repository.** It is public. Live figures, object names and the parity report
  on live data stay in company storage (`docs/integration/WORKPLACE_PORTING_GUIDE.md`).

## 10. When to ask the owner, and when not to

The owner has asked not to be given questions the repository answers. Use the default in
`docs/integration/DECISION_PLAYBOOK.md`, measure, and report.

Ask the owner only for:

- a rate for an aircraft the rate table does not cover (DEC-35). Until then it stays unpriced and listed;
- any change to the order, the cost formula, the due date or the settings of section 4.9;
- a rule of the objective you believe is wrong. Bring the case that shows it, and what it would change.

Do not ask whether to keep parity. That is the assignment.

## 11. What to hand back

One report, in plain language, that a person who was not in the session can check. Lead with the result.

1. **The result in three lines.** Does your build's objective match the reference: yes, no, or in part. What differs.
   What was not checked.
2. **The two commits compared**, and the date.
3. **The map of step 2**: each part, where it lives in your build, and whether it is unchanged, changed or rewritten.
4. **The five claims of section 5**, each as true, false or not checked, with the command that shows it and its
   output.
5. **Every difference found.** For each: what the reference does, what your build did, the case that shows it, what
   you changed, and the test that now holds it.
6. **The fixed cases:** how many rows of each table pass in your build, and any that do not.
7. **Live data**, when you have it: `cost_steering` for both plans, the aircraft without a rate or a due date, and
   the unplanned jobs by reason.
8. **Open items**, each with who it waits on.

Say plainly what you did not verify. A claim without evidence is reported as not checked, never as true.

## 12. Where things are

| Part | File | Function or name |
| --- | --- | --- |
| The order of the levels | `config/policies/rebuild-v1.json` | `objective.order` |
| The fields of each level | `focus/engine/max/optimizer/orchestrator.py` | `OBJECTIVE_FIELDS`, `key_levels` |
| A schedule's key | `focus/engine/max/optimizer/orchestrator.py` | `evaluate` |
| Which of two schedules is better | `focus/engine/max/optimizer/orchestrator.py` | `better`, `improved_level` |
| Keeping only validated candidates | `focus/engine/max/optimizer/orchestrator.py` | `_admit`, `_valid` |
| The cost of one aircraft | `focus/economics/registry.py` | `penalty`, `marginal` |
| The weight of an aircraft | `focus/economics/registry.py` | `derive_priority_weights` |
| An aircraft's finish | `focus/economics/evaluator.py` | `aircraft_completions` |
| Costs per aircraft and for the fleet | `focus/economics/evaluator.py` | `line_costs`, `summarize` |
| Whether the cost objective steered a plan | `focus/economics/evaluator.py` | `objective_status` |
| One rate and one due date per aircraft | `focus/economics/repository.py` | `build_economics` |
| The cost of delay | `focus/engine/max/optimizer/urgency.py` | `cost_of_delay`, `COD_DECAY`, `COD_STEP_MINUTES`, `OpenClock` |
| Crew changes on a job | `focus/engine/max/optimizer/continuity.py` | `continuity_breaks` |
| Commitments, and the delivery plan following the shift plan | `focus/engine/commitments.py` | `following`, `repair_hints` |
| A repairing run and a full solve | `focus/engine/max/optimizer/orchestrator.py` | `_run_repair`, `run`, `planning_mode` |
| The independent validation | `focus/validation/plan.py` | `validate` |
| What a run reports | `focus/engine/max/optimizer/orchestrator.py` | `receipt`, `decision_digest` |
| The plan's shape | `focus/contracts/planning.py` | `CandidatePlan` |

| What it proves | Tests |
| --- | --- |
| The key follows the policy's order; money outranks job counts; dollars compare only over the same aircraft | `tests/unit/test_acceptance_key.py` |
| Exact cost, caps, the booked floor, unknown against zero, one term per aircraft, the due date | `tests/unit/test_economics.py` |
| The explainer's order and worked example are what the code returns | `tests/unit/test_objective_explained.py` |
| Every table of this file is what the code returns | `tests/unit/test_objective_parity_prompt.py` |
| The report on whether the cost objective steered a plan | `tests/unit/test_objective_status.py` |
| The latest start walks back through open shift minutes | `tests/unit/test_urgency_clock.py` |
| Commitment outcomes | `tests/unit/test_commitments.py` |
| Ordering by cost of delay lowers the exact fleet cost | `tests/integration/test_economic_steering.py` |
| One admitted run gives the same plan in two copies of a store | `tests/integration/test_objective_parity_procedure.py` |
| Only validated schedules are kept; the same inputs give the same plan | `tests/integration/test_max_pipeline.py` |
| A repair keeps what holds and keeps its second schedule only when better | `tests/integration/test_plan_repair.py` |
| The delivery plan keeps every slot of the shift plan | `tests/integration/test_one_schedule.py` |
| The native core and the Python core give the same plans | `tests/unit/test_native_core.py` |
| Criticality is the cost of one more day late | `tests/unit/test_criticality.py` |

Pages to read with this one: `docs/execution/OBJECTIVE_EXPLAINED.md` (the objective in plain terms),
`docs/execution/ECONOMICS_POLICY.md` (the economics in full), `docs/execution/PLAN_REPAIR.md` (repair and the full
solve), `docs/integration/DECISION_PLAYBOOK.md` (every open decision and its default), and
`docs/integration/HANDOFF.md` (the data flow and the pipeline).
