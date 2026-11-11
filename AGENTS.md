# Brief for a coding agent working on FOCUS

Read this page first. It tells you what FOCUS is for, what to read before you change or port anything, and what a
port to real data must keep. Reading needs no approval; approval is for changes.

## What FOCUS is for

1. **Schedule every aircraft with open work, through delivery, at the lowest late-delivery cost**, without breaking
   a constraint. The scheduler is a fixed, rule-based procedure. There is no AI in it.
2. **Run before every shift**, three times a day, within 20 minutes from the data cut to the plan and its dashboard
   being ready. A pipeline gathers the data, schedules, validates and publishes; the web application only displays
   what the pipeline published. As shipped, that took 23.2 minutes at 50 aircraft on the test machine, and the
   scheduler was 3 of them: `FROM-NOTHING-APP/docs/integration/DECISION_PLAYBOOK.md`, DEC-36 lists what to change.
3. **Score each team's shift** against the plan accepted before that shift started, with an excusal process so a
   team is not marked down for causes outside its control.

If your build does not do all three, it is not finished, however well the data is wired.

## Read these, in this order, before you write code

| # | Page | What you must be able to explain afterwards |
| --- | --- | --- |
| 1 | `FROM-NOTHING-APP/docs/integration/HANDOFF.md` | The data flow, one shift end to end, the staging contract, the pipeline jobs, and what counts toward the pre-shift window |
| 2 | `FROM-NOTHING-APP/docs/execution/OBJECTIVE_EXPLAINED.md` | The order in which two schedules are compared, how late-delivery cost is worked out, and how to tell whether the cost objective is working on your data |
| 3 | `FROM-NOTHING-APP/docs/execution/SCORING_EXPLAINED.md` | How one team-shift is scored and graded, with the worked example |
| 4 | `FROM-NOTHING-APP/docs/integration/DECISION_PLAYBOOK.md` | Which decisions are made, which are yours to take, and which need the owner |
| 5 | `FROM-NOTHING-APP/docs/data/README.md` and its pages | Every input, column by column |

Then read the code those pages name. The two explainer pages list each rule with its function and its tests.

**Check yourself.** Before building, write the answers to these in your progress file. If you cannot, read again.

- In what order are two candidate schedules compared, and where does money come in that order?
- What makes an aircraft's cost unknown, and what does the scheduler do with an unknown cost?
- A team was expected to do 900 minutes, did 720 planned and 80 extra. What is its score, and why?
- Which plan is a shift scored against, and what happens to a job finished just before the shift starts?
- What does the web application read, and what does it never read?

## What a port to real data must keep

- **The same rules.** Take the scheduling engine, the validator, the economics, the scoring and the excusal code as
  they are. Change the data adapters, not the rules.
- **The same tests.** The test suite is the definition of the behavior. A port passes it, and adds tests for its
  adapters.
- **One formula for scores**, in `focus/performance/score.py`. Never compute a score in SQL, in the browser or in a
  second module. A proposed rule is tested as a tool outside `focus/`, as `tools/right_work_score.py` is
  (`DECISION_PLAYBOOK.md`, DEC-33): nothing that grades, stores or shows a score reads it, and if the owner adopts it
  the formula in `score.py` changes.
- **Unknown stays unknown.** Never fill a missing rate, duration, crew size, date or calendar day with an average, a
  median or a default. Report how many rows are affected, by reason, and work the cause.
- **Report three things with every live run**, all from `python -m focus.cli pipeline --key K --status`:
  - `objective`: how many aircraft have a rate, a due date and a finish date, and the open jobs that could not be
    planned, by reason. Every aircraft in work should have a due date. A run that publishes a plan while every cost
    is unknown has not optimized cost.
  - `readiness_gaps`: the admitted data's gaps by code. `CALENDAR_ENDS_BEFORE_DUE` on a line means that aircraft
    can never be planned late, so its lateness is never costed; the calendar feed must run past the due date.
  - `window`: the minutes from the data cut to the accepted plan and its dashboard data, against the owner's 20.
- **Rates come from data, never from a guess.** Build the economics rows from a rate file with
  `python tools/build_economics_rows.py`; it lists the aircraft it cannot price and leaves them unknown. The owner's
  rate is the airline economic proxy per customer airline and minor model, column
  `daily_airline_economic_proxy_usd` of the owner's rate table
  (`FROM-NOTHING-APP/fixtures/economics/airline_economic_proxy.csv`). Lateness begins at 744 unload plus 2 days, with
  no other grace period and no cap (`FROM-NOTHING-APP/docs/execution/DECISIONS.md`, D-31). An aircraft the table does
  not cover stays unpriced and listed until the owner supplies a row or a rule.
- **The fleet is whichever aircraft have open tasks** at the data cut (D-32). No list of aircraft, no list of
  customers and no count is fixed; 50 is a planning size. Never tune a setting to one fleet.
- **One definition of criticality**, from the cost objective (`OBJECTIVE_EXPLAINED.md` §5): the aircraft's cost of
  one more day late in the accepted forecast. Do not rank jobs by a hand-made list, a job type or a second formula,
  and do not weight scores by it until the owner decides (`DECISION_PLAYBOOK.md`, DEC-31).
- **Keep what the scoring needs.** Verified completions, claims, decisions and the accepted plans are the record
  every score is built from. Do not trim or overwrite them to save time.
- **Work in the order the owner cares about:** correct first (constraints, objective, scores), then complete (every
  aircraft priced, every shift scored), then fast.

## How to work

- Keep one progress file. Each session starts by reading it and ends by updating it: what was done, what was
  measured, what is blocked and on whom.
- Record the owner's requirements in it in their own words, with the date.
- When an older codebase and this one disagree, this one is the specification. Use older code for connection
  details and query shapes only.
- When the code and a document disagree, report both with file and line. Change nothing until the owner says
  which is right.
- Never claim an install, a signature, an acceptance, a deploy or a review that did not happen.
- This repository is public and holds synthetic data only. Company names, object names, queries, credentials, rows
  and scores stay in company storage.
