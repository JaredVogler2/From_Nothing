# FOCUS From Nothing — decisions register

The manual (`FOCUS_FROM_NOTHING_MASTER.txt`) is the only specification. This
register records how its rules map to code, which values are user
requirements versus fixture choices, and which live semantics stay open.
The machine-readable policy is `config/policies/rebuild-v1.json`, which
includes `shift_fixture.json` and `grading-demo-v1.json`. Every policy value
carries an authority label:

| Label | Meaning |
| --- | --- |
| `user_requirement` | A constant the user specified (S01/S09). Code never changes it, and it is not re-approved. |
| `manual_specified` | A value the manual fixes explicitly. |
| `fixture_default` | An explicit demonstration choice, valid in fixture mode only. |
| `demo_default` | A proposed S14 grading/recognition demo default, never an approved labor grade. |
| `open_decision` | A live semantic with no owner decision yet. The fixture choice is recorded and the live effect is a precise readiness gap (OD-01..OD-14 in the policy file). |

## Capabilities (P01 step 3)

Four separate products are kept apart (S01). Each candidate plan carries
exactly one purpose.

| Capability | Allowed inputs | Never allowed |
| --- | --- | --- |
| Complete inventory | Every census job, including blocked, quarantined and excluded jobs, each with a reason. | Silent exclusion; a fixed line cap. |
| `FORECAST` | Conditional part ETAs (precision-aware floors), virtual MHE/PSC/TST support pools (10 synthetic people per team/shift), inferred nominal availability. Every such fact is labeled conditional. | Claiming a part is received or a named mechanic is present. |
| `SCENARIO` | Same as forecast, inside an isolated namespace copied from an immutable snapshot. | Writing source/plan heads, goals, commitments, actuals or official grades. |
| `EXECUTABLE` | Real named people with admitted roster/attendance availability, confirmed part releases, known requirements, sourced QA hold locations. | Synthetic or virtual people; conditional ETAs; unknown requirement census. |
| Current dispatch | An accepted `EXECUTABLE` plan, rechecked against current certs, attendance, graph, QA, parts, calendar, CS and source freshness. | Treating a stale accepted plan as executable. |

Inferred home team (S07) is nominal forecast evidence. It is never current
attendance. A conditional ETA is never physical receipt.

## Architecture decisions

- **D-01 One package, one host.** The installable `focus` package (Python 3.12) and one Flask app built by `create_app(settings, repositories, clock)`. Importing it is side-effect free: no database, acquisition, file write, solver or port action.
- **D-02 One rule authority per concern.** Calendar/shift windows live in `focus/calendar.py`, eligibility in `focus/eligibility.py`, graph construction in `focus/data/graph.py`, duty accounting in `focus/engine/ledger.py`, economics in `focus/economics/`, scoring in `focus/performance/score.py`. The optimizer, repair, validator, serializer and UI consume these services. None of them re-implements a rule with different units.
- **D-03 Independent validator.** `focus/validation/` reconstructs duty, crews, windows, graph releases and parts floors from the immutable snapshot plus the candidate's assignments. It never reads optimizer feasibility flags or candidate-declared hashes. It shares only the pure rule authorities from D-02 and reimplements duty reconstruction (`validation/duty.py`) separately from the ledger.
- **D-04 Integer UTC minutes inside the engine.** Domain contracts carry aware UTC datetimes (half-open). The engine converts them once to integer minutes since the Unix epoch, so arithmetic stays exact and DST is handled by actual instants, never by a packed day. Source timestamps with seconds become "not before" floors by ceiling to the minute.
- **D-05 Exact money.** Integer cents or `fractions.Fraction` cents. Floats are never an authority. A decimal daily rate that converts to a fractional cent stays an exact rational.
- **D-06 Canonical JSON.** UTF-8, sorted keys, compact separators, `allow_nan=False`. Decimals are strings, fractions are `{num,den}`, dates ISO, datetimes RFC3339 UTC with preserved precision. Decoding rejects duplicate keys, unknown fields and non-finite numbers. Hashes cover the canonical payload, excluding the record's own hash/signature.
- **D-07 Lossless identity.** `JobKey(site, program, line, native_id, revision, operation, effectivity)` keeps native text byte-for-byte. Transport IDs are SHA-256 digests mapped reversibly in the snapshot index, and construction rejects collisions. The same SOI on two lines, or in two revisions, always gives two keys.
- **D-08 Phases.** Each job expands into typed phases: `WORK` segments, `IP_QA` holds, `FINAL_QA`, `OTHER_BUYOFF` branches and a zero-work `RELEASE` (AND) milestone. Successors depend on `RELEASE`. Work longer than `duration.max_segment_minutes` is split into chained segments that sum back to the parent's elapsed time and standard effort.
- **D-09 Duty ledger with derived transfers.** The ledger stores anchor duties (WORK/WAIT/TOOL/OTHER/carry-in) per person. TRAVEL duties are derived from each consecutive anchor pair inside a shift. Insert, remove and swap therefore always re-derive both neighbors, and a failed mutation leaves nothing behind.
- **D-10 Rich MAX = list scheduling + exact-cost local search.** The neutral named seed is a serial schedule-generation pass over the ready frontier (committed first, then critical-path urgency, then FIFO availability, then stable key). `EconomicsRegistry.derive_priority_weights` then reorders dispatch. Delay repair, continuity and bounded neighborhoods (priority swaps plus atomic ledger relocations) are kept only when the exact complete-fleet lexicographic comparison improves and the independent validator passes. `global_optimality_proven` is always false for the heuristic.
- **D-11 Fixture repository = the same protocol as SQL.** `fixtures/runtime/<scenario>/` stores canonical immutable envelopes with atomic writes (temp file + `os.replace`), idempotency indexes, scoped lock directories and versioned heads that are written last. The SQL repository implements the same interface against the proposed `FOCUS_FN_*` tables (S06). Production never falls back to fixture storage.
- **D-12 Test signing keys are fixture-only.** HMAC-SHA256 with domain separation `FOCUS_PLAN_V1\0`. A fixture key ID always starts with `TEST-` and cannot accept an executable live plan.
- **D-13 Operation registry (user directive: sound API, MCP-ready).** `focus/web/operations.py` declares every API operation once. Flask routes, `/api/v1/openapi.json` (OpenAPI 3.1) and the MCP catalog (`focus/mcp/`) are generated from it. Read operations are safe and idempotent GETs. Commands are POSTs that require trusted scope, a CSRF token, an idempotency key and an expected version, and return `202` with a worker run ID.
- **D-14 Hosting (user directive).** Posit Connect first, then VMware Tanzu. The WSGI entrypoint is `app:app`. URLs are prefix-safe (`request.script_root` plus a `<meta name="focus-base">` read by the JS). Identity providers: `fixture` (demo login, visibly labeled), `posit_connect` (trusted `RStudio-Connect-Credentials` header) and `trusted_header` (Tanzu SSO / oauth2-proxy header named by `FOCUS_TRUSTED_USER_HEADER`). Grants are always server-owned (`FOCUS_GRANTS_JSON`). Tanzu `$PORT` and `VCAP_SERVICES` are read only by `focus/config.py`.
- **D-15 Python 3.12 (user directive).** The development, lock and test interpreter is CPython 3.12. `requires-python = ">=3.12"` per S02.

## Semantic gaps (live), each a precise readiness effect

These are the policy file's `open_decisions`. None of them blocks local
fixture implementation (S00). Each one blocks the matching live executable
capability until the owner admits the semantic:

- OD-01 TOTAL_MECHANICS simultaneity
- OD-02 raw clock intervals for elapsed P75
- OD-03 QA hold locations/inspector roles
- OD-04 labor breaks and OT authority
- OD-05 CS offsets/date basis
- OD-06 commercial due
- OD-07 concurrency meanings
- OD-08 rework classification source
- OD-09 affirmative no-cert census
- OD-10 assignment/attendance feeds (currently empty)
- OD-11 shift-start travel origin
- OD-12 economics terms/caps/checkpoints
- OD-13 master shop-order status meanings
- OD-14 which accepted plan purpose drives delivery commitments (fixture: FORECAST)

## Confirmation (P01 detailed requirement)

This manual contains every specification needed to implement the
application. No prior code, chat, donor application or separate document is
a dependency. Constants supplied by the user were recorded as
`user_requirement` without asking for blanket re-approval.
