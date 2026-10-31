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
| `Ledger.search` | `greedy.Decoder._search` and `_book` | The whole time-slot search for one phase: each eligible shift, each candidate start, the calendar advance (regular before overtime), the assignment id, the crew choice, the booking and the WAIT duty through an in-process inspection. One call per phase. It runs without the GIL. |
| `Canon` (`native/src/canon.rs`) | `focus/codec.py` (`canonical_dumps`, `content_hash`, `streamed_content_hash`), `focus/domain.py` (`digest_id`), `orchestrator.decision_digest` | Canonical JSON written straight from Python values (no JSON-native tree), with SHA-256 streamed while writing: snapshot and plan seals, lineage hashes, record ids, digest ids, decision digests. |

**Skipping starts that cannot work.** Before a full crew attempt at a start, `search` counts the pool members who are
qualified and free over the whole interval. A crew can only come from those people, so when there are fewer than the crew
needs, the attempt is certain to fail and is skipped. A whole shift is skipped the same way when fewer pool members
than the crew needs have as many free minutes left in that shift as the work takes (free minutes per person and the
sorted list per pool and shift are cached, and dropped for the people a booking or rollback touches). If the phase is
not placed and the last failure the Python reference would report came from a skipped start or shift, that one
attempt is made in full at the end to recover the exact reason. Plans and reasons are therefore identical to the
reference.

**Canonical JSON.** `Canon` writes exactly the bytes of `json.dumps(to_jsonable(value), sort_keys=True,
separators=(",", ":"), ensure_ascii=False, allow_nan=False)`:
- Python reports, once per type, the rule `codec._encoder_for` picks (`codec._native_kind`).
- Decimal and Fraction values go through `to_jsonable`, and datetimes through `format_utc`.
- Anything unusual and every error is handed back: the Python path then returns the same bytes or raises the exact
  error.

`enable_native_codec` turns it on with the native core (`build_services` does so when `FOCUS_NATIVE_CORE=true`). At 50
aircraft it cut the snapshot seal, lineage and graph-id work of admission from about 100 s to about 15 s of profiled time.

**Parallel search moves.** The search's GIL release lets the optimizer decode up to `SPECULATION` (3) search moves at
once on threads (`orchestrator._decode_all`). The moves are the draws a one-at-a-time run would make while none is
accepted. They are judged in order; after an acceptance the rest of the batch is discarded and drawing resumes from the
random state that followed the accepted move. The accepted sequence and the plan are therefore exactly those of one move
at a time (`tests/integration/test_max_pipeline.py::test_speculative_moves_accept_exactly_the_sequential_sequence`).
Hash maps use FxHash (`rustc-hash`). The core never depends on map iteration order, and std's per-process random hasher
would already have exposed any such dependence.

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
- **Protocol.** The extension reports `PROTOCOL`, the row formats its `Context` reads (2 since revocation windows). The
  Python side refuses a wheel built for another protocol and runs the Python core, recording the reason in the run
  receipt. Bump both sides together whenever a row format changes, and install the wheel built from the same commit.
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

When the codec's encoding rules change, change `codec._encoder_for` and `codec._native_kind` together (and
`native/src/canon.rs` for a new kind). `tests/unit/test_native_codec.py` compares both paths on every type, on random
nested values, on a real snapshot and plan, and on every error.

The random ledger test and the plan-equality tests fail on any divergence, including a changed message. The independent
validator stays the referee for every published plan.
