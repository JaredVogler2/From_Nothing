# Stage P06: Certification applicability and pure named workforce

Status: **fixture_verified**

## Changed paths

- `focus/eligibility.py`:
  - **Cache.** Cached decisions are keyed by a fingerprint of holdings, requirements, source run and
    policy hash, plus bundle content and the exact interval. A new qualification or assignment
    revision cannot reuse a stale decision.
  - **Scarcity** counts only usable applicable certificates.
  - **Matcher.** `RoleSlot`/`match_crew` is a deterministic bounded role-slot matcher (S07
    blueprint): individually qualified candidates only, most constrained slot first, distinct
    people, a feasibility veto, and `UNKNOWN` when the node bound is exceeded.
- `focus/data/qualifications.py`:
  - The sentinel is compared by parsed date, so all approved encodings agree.
  - An expiry that cannot be represented without year-10000 arithmetic is `UNKNOWN`, never
    unlimited.
  - Unavailable crew demand is 0, never clamped to 1.
  - `matrix_label_notes` reports convenience-matrix labels next to authoritative decisions as
    projections only.
- `focus/domain.py`: `RequirementBundle.count` allows 0 (= demand unavailable).
- `focus/data/graph.py`: the remaining crew default-to-1 for blocked jobs was removed.
- `focus/simulation/pools.py`: `admit_executable_crew` decides executable admission from stored
  origin evidence and pure-pool membership. Payload claims (`mock_data`, `synthetic`, `purpose`) are
  ignored.
- `focus/data/datasets.py` and `queries/source_inputs/cert_eligibility_matrix.sql`:
  `FOCUS_CERT_ELIGIBILITY` is a display projection, with no MECHANIC names and no authority. It is
  certified empty in the demo fixture because the fixture uses non-numeric keys.
- `focus/data/snapshot_builder.py`: matrix rows become `CERT_MATRIX_PROJECTION_ONLY` INFO gaps.
- `queries/qualifications/requirement_census.sql` and `holding_validity_profile.sql`: read-only
  readiness queries.
- `tests/unit/test_eligibility.py`, `tests/integration/test_pools.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_eligibility.py tests/integration/test_pools.py -q  -> 26 passed
.venv/bin/python -m pytest tests -q                                                          -> 113 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A10 | **Caps and denominators.** Activation caps are exact `floor(85N/100)` for N = 0/1/2/7/8/10/20 → 0/0/1/5/6/8/17, with no minimum-one exception. Each pool's N is the distinct admitted people for its purpose. **Demo pools.** PROD-A 10-26 S1 has N = 7, cap 5 (A1-03 absent); 10-27 has 8/6; 10-28 has 7/5 (A1-06 on leave). Nobody is in two pools for the same shift. |
| A11 | **No borrowing.** A C-requiring slot on PROD-B 10-27 S1 is `INFEASIBLE` (`SLOT_NO_ELIGIBLE_PERSON`). DEMO-A1-07 holds C but is never considered, and DEMO-B1-04's REVOKED C is not a phantom holder. **Admission** rejects cross-team (A1-07), cross-shift (B2-01) and duplicate seats. |
| A12 | **Individual supersets only.** Only the individual superset (A+B+C) satisfies A AND B. A-only plus B-only can never form a crew (`INFEASIBLE`, no pooling). **OR alternatives** are checked separately: A+B2 satisfies A AND (B OR B2) but not A AND B. **Matcher.** It keeps the only C holder for the C slot even when preferred elsewhere, falls back to the next distinct crew on a veto, and returns `UNKNOWN` at its bound. |
| A13 | **Encodings and expiry.** Slash, ISO and compact dates agree. An inclusive expiry on 10-27 ends at 10-28 local midnight (04:00Z EDT): a job ending exactly then is valid, one crossing it is `EXPIRES_DURING_INTERVAL`. **Rejections.** Future-valid, blank, malformed, out-of-range, missing start and unknown status all fail whole-interval eligibility. **Revocation** wins for its interval and reinstatement restores. **Sentinel.** Perpetual applies only under the explicit sentinel policy; without it, 9999/12/31 is `UNKNOWN`. |
| A14 | **Unknown blocks.** Rework without requirement rows is `UNKNOWN` (blocked, never cleared). **Affirmative none.** An affirmative no-cert census job proceeds. **SUBST** and 100% matrix labels grant nothing; the authoritative decision is reported as denied. |
| A64 (P06 part) | **Ten virtual people.** Exactly ten distinct virtual people per configured MHE/PSC/TST team and shift (cap 8), each holding the union of team-required certs (MHE-OP, TST-1; PSC is affirmatively none). They are forecast-only, with synthetic origin/namespace/purpose and no virtual people for unconfigured teams. **Spoofed flags.** Claims `mock_data=false`, `purpose=PRODUCTION` and `synthetic=false` are ignored. Rewriting the stored flags still leaves the synthetic namespace in the identity, so executable admission rejects the person. |

## Fixture versus live

Holdings, requirements, alternatives, census and rosters are fixture data. Live
`FOCUS_MECHANIC_CERTS` / `NEO_SOI_CERT_REQUIREMENTS` were not read.

## Remaining gaps

- No live source exists for approved OR alternatives, the affirmative no-cert census, or revocation
  events. They stay supplemental producers and block only executable capability.
- Live date encodings and status vocabularies must be profiled
  (`queries/qualifications/holding_validity_profile.sql`) before admission.
- The virtual support UI labelling is completed with the views in P21.
