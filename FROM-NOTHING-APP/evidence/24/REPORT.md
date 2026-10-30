# Stage P24: end-to-end acceptance, versioned review and release

Status: **fixture_verified, local build complete.** Every case A01-A72 and requirement C01-C39 passed, from one
acceptance run of commit `17f011e` in a fresh clone and a fresh Python 3.12 environment. **Live release is blocked by
13 gates.**

Not performed by this build:

- no SQL was installed and no grant applied;
- no plan was signed with a live key;
- nothing was deployed and no CI was activated;
- no independent review took place.

`review/REVIEW-001` is an implementer self-check and says so.

## Changed paths

- `focus/release/` (new):
  - `hashes.py`: exact tree hashes. Text is hashed with LF endings. The selection is tracked plus new files from
    git, else a filtered walk.
  - `acceptance.py`: the runner and judge, with static caller-path receipts.
  - `pytest_receipts.py`: a per-node outcome plugin.
  - `review.py`: versioned, exclusive-create reviews and `--verify`.
  - `gates.py`: the single list of 13 blocking and 4 optional release gates.
- `tests/acceptance/` (new):
  - `map.json`: the register of 72 cases, 39 requirements with 149 caller paths, 5 counterexamples and 10
    verification themes.
  - `test_traceability.py`: coverage, collection, rule and path receipts, and a match with the manual.
  - `test_release.py`: A72, covering the fresh checkout, review history, stale and incomplete acceptance, and the
    gates.
- `focus/web/security.py`: `Cache-Control: no-store` on every response except static assets. The fix and its test
  are in FINAL_ACCEPTANCE section 6.
- `tests/integration/test_web_runtime.py`: the page cache test.
- `tests/integration/test_api_scope.py`: REST and MCP parity for every read operation.
- `focus/cli.py`: the `acceptance` and `review` commands, and the corrected `scenario --changes` help.
- `tools/final_acceptance.py`, `tools/release_review.py` (new wrappers).
- Docs:
  - `docs/execution/FINAL_ACCEPTANCE.md`, `RELEASE_CHECKLIST.md` and `ROLLBACK.md` (new);
  - `README.md`, with the P24 commands;
  - `docs/execution/STATUS.json`, stage 24 and the capability register.
- `review/REVIEW-001.md`, `review/VERDICT-001.json` (new).
- `evidence/24/`:
  - `acceptance.json`, `ACCEPTANCE.md`, `receipts.json`, `pytest.log` and `pip_freeze.txt`;
  - `fresh/` (the run-through receipts) and `fresh_environment.json`;
  - this report.

## Bound hashes

| Item | SHA-256 |
| --- | --- |
| Manual | `f81939211ef01f900ef5a3fe965e27f97f8c71a82832f5d541b3865ce205e5d5` (unchanged) |
| Implementation (292 files) | `76c22dfafa789c61c6da4aa84bc007611223162c7ed9c83ada3a97a5e25e1928` |
| Tests (53 files) | `a62146080601193733c46acc87c6e9c6743a92e8def9d4d927ca4aefc10ba715` |
| Fixture source (55 files) | `04df23dcaaa2a9a1aa5eed7f118814a89901d6ecc5ddbd8cd8200e28d8622649` |
| Policy `rebuild-v1` | `98a57f5eb641a65d1c332e524d399fcf94d46a6c9954cdf3bdb97104c71f8ac4` (unchanged since P22) |
| Acceptance run | `fcf333dbdbafe52ff5e8b729fe374aa7948b14566e32c883a90482292e0cfd58` |

The live source is `NOT_ACQUIRED`: no live source run exists.

## Commands and results

| Command | Result |
| --- | --- |
| `git clone`, `python3.12 -m venv`, `pip install --no-deps -r requirements.lock`, `pip install --no-deps -e .` | Fresh environment: CPython 3.12.3, 27 pinned packages |
| `python -m focus.cli acceptance --out <out>` (fresh clone) | **PASS**. 483 collected: 480 passed, 0 failed, 3 skipped (the opt-in benchmark sizes, which no case maps to). 892 s |
| Run-through in the fresh clone (`evidence/24/fresh/`) | Every documented command ok, from the empty runtime to the served app (FINAL_ACCEPTANCE section 1) |
| `python -m focus.cli review --acceptance evidence/24/acceptance.json` | `REVIEW-001` / `VERDICT-001`: `LOCAL_BUILD_COMPLETE`, live release `BLOCKED` |
| `python -m focus.cli review --verify 1` | `MATCH` for the manual, implementation, tests, fixture source and policy |
| `python -m pytest tests/acceptance tests/integration/test_api_scope.py tests/integration/test_web_runtime.py tests/unit/test_imports.py tests/unit/test_hosting_files.py -q` (working tree, before the commit) | 39 passed |
| `python tools/check_imports.py` | ok: 110 modules, no optional package loaded, no runtime writes |

## Acceptance cases

**All 72 cases pass.** Per-case tests and outcomes are in `evidence/24/ACCEPTANCE.md`.

A72 is this stage's own case:

- the fresh checkout completes the documented fixture flow;
- a new review in a folder holding `REVIEW-001` and a partial `002` becomes `003`, never overwriting, and binds exact
  hashes;
- a stale acceptance is refused;
- an incomplete build is reported with its blockers;
- the gates are listed precisely, and the documents list the same IDs.

All 39 requirements pass, with 149/149 caller paths verified. The five named counterexamples pass:

- in-progress edge;
- activation repair;
- rework P75;
- same-run CS serialization and readback;
- OOS double charge.

All ten verification themes pass, including security scope, cache and CSRF, every crash-recovery boundary, and
hosting with REST and MCP parity.

## Fixture versus live

| Area | Fixture | Live |
| --- | --- | --- |
| Whole application, A01-A72 | Verified in a fresh clone and environment | Not verified: no live system was reachable or authorized |
| Release gates | Listed, with exact effects | LG-01 to LG-13 open (`focus/release/gates.py`) |
| Review | Implementer self-check bound to hashes | Independent review pending (LG-12) |

## Remaining exact gaps

All gaps are external; each has its effect and owner in `docs/execution/RELEASE_CHECKLIST.md`:

- LG-01 schema installation;
- LG-02 live repository adapter;
- LG-03 grants;
- LG-04 key custody;
- LG-05 pre-sign SQL readback;
- LG-06 source readiness and OD-01 to OD-14;
- LG-07 observation producers;
- LG-08 score policy approval;
- LG-09 excusal reviewers;
- LG-10 host identity and grants;
- LG-11 deployment to Posit Connect, then Tanzu;
- LG-12 independent review;
- LG-13 CI activation.

Optional and disabled, with exact effects:

- OPT-01, the CP-SAT stage;
- OPT-02, BCAI;
- OPT-03, MCP (read-only when enabled);
- OPT-04, curves other than LINEAR_DAILY.

Signing key rotation fails closed (`ROLLBACK.md` section 7): verifying old signatures after a rotation needs a
verification-only key context, which is not built.
