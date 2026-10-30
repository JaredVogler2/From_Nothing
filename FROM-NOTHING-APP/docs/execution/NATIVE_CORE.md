# Native scheduling core (trial)

**Status: optional and off by default.** The plans it produces are byte-identical to the Python engine's on every
fixture tested. It runs inside the existing Python application, so there is still one deployable app on Posit Connect
and Tanzu.

## What it is

`native/` is a Rust extension module, `focus_native`, built with PyO3 and maturin as a single wheel for any CPython
3.12+ (`abi3`). It ports the hot path of the named heuristic, line for line:

| Rust | Python reference | What it does |
| --- | --- | --- |
| `Ledger` | `focus/engine/ledger.py` (`DutyLedger`) | Per-person duty items, transfers re-derived around every change, windows, regular and overtime budgets, the activation cap, tools, cross-shift overlap. Transactions use an undo log. |
| `Ledger.choose_crew` | `focus/engine/crew_selection.py` | Qualification, per-person screening, preference order, cap-driven reuse and the bounded combination search. |
| `Context` | `focus/eligibility.py` (`HoldingIndex`) | The boolean holding checks over the whole interval, plus the exact whole-window qualification memo. |
| `Ledger.candidate_starts` | `greedy._candidate_starts` | Candidate start instants from pool members' duty ends. Every opening is kept; there is no per-shift cap. |
| `Ledger.search` | `greedy.Decoder._search` and `_book` | The whole time-slot search for one phase: each eligible shift, each candidate start, the calendar advance (regular before overtime), the assignment id, the crew choice, the booking and the WAIT duty through an in-process inspection. One call per phase. |

**Skipping starts that cannot work.** Before a full crew attempt at a start, `search` counts the pool members who are
qualified and free over the whole interval. A crew can only come from those people, so when there are fewer than the crew
needs, the attempt is certain to fail and is skipped. If the phase is not placed and the last failure the Python
reference would report came from a skipped start, that one attempt is made in full at the end to recover the exact
reason. Plans and reasons are therefore identical to the reference.

The Python code stays the rule authority. The decoder, the orchestrator, the economics, the search and the
independent validator are unchanged, and the validator re-checks every plan from source whichever core built it.

## Why the plans are identical

The same decisions are made in the same order:
- tie-breaks, including string order on ids;
- iteration order;
- the combination enumeration of `itertools.combinations`;
- every error code and message, since those reach plan dispositions as reasons.

`focus/engine/native.py` presents the Rust ledger through the `DutyLedger` interface. Tests:

- `tests/unit/test_native_core.py`:
  - **Random ledger operations:** four seeds of 450 operations each. The operations are bookings, removals, carry-in,
    nested transactions with rollback, conflicts, caps, tools and travel through breaks. Both ledgers must give the
    same items, errors and messages, capacity answers, free points, last-located duty and active sets.
  - **Identical plans:** the demo with 20 search moves, the P22 scale template at 12 aircraft and the production
    shape must produce the same candidate id and content hash with the native core on and off, for both purposes.
  - **Fallback:** with the setting on but no extension, the Python ledger runs and the run receipt says
    `engine: python`.
- `tools/benchmark_production.py --engines python,native` checks the same equality on production-shaped fleets and
  fails when the engines disagree.

## Build and enable

```bash
python -m pip install ./native
FOCUS_NATIVE_CORE=true python -m focus.cli schedule ...
```

- **Building** needs a Rust toolchain (stable) and pulls `maturin` and `pyo3` from the package indexes.
- **Prebuilt wheel:** `maturin build --release -o dist` writes `focus_native-0.1.0-cp312-abi3-manylinux_*_x86_64.whl`.
- **Receipt:** every schedule receipt carries `engine` (`requested`, `available`, `engine`). The candidate itself never
  records which core built it, which is what lets the plans stay byte-identical.

## Deployment

- **Posit Connect.** Build the wheel for the server's platform (manylinux x86_64) in CI, and vendor it or publish it to
  the internal package index. List `focus-native` beside `requirements.txt` in the deployment bundle, and set
  `FOCUS_NATIVE_CORE=true` in the content Vars. If the wheel is missing, the app runs the Python core and says so.
- **Tanzu.** Same wheel, installed through the Python buildpack from a vendored `vendor/` directory or the internal
  index; then `FOCUS_NATIVE_CORE=true` in the manifest or the user-provided service.
- **Windows workstations.** `maturin build` on Windows yields the matching wheel. The Python core remains available
  everywhere.

## Maintaining it with a coding agent

When a ledger or crew rule changes, change it in four places:
1. the policy, if the rule is configurable;
2. the Python reference;
3. `native/src/lib.rs`;
4. the tests.

The random ledger test and the plan-equality tests fail on any divergence, including a changed message. The independent
validator stays the referee for every published plan.
