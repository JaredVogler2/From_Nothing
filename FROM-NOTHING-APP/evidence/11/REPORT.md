# Stage P11: Named duty ledger, activation, travel and crew selection

Status: **fixture_verified**

## Changed paths

- `focus/engine/travel.py`: `TravelPolicy`.
  - same aircraft: 0 minutes; changed aircraft: 15 (fixture_default);
  - unknown origin: the explicit bounded `travel.unknown_origin_minutes`;
  - `MUSTER_AT_FIRST_ASSIGNMENT` at shift start;
  - no travel inside protected windows.
- `focus/engine/ledger.py`: the per-person duty ledger.
  - **Admitted windows.** Every duty item lies inside the person's admitted regular and
    authorized-OT windows. The regular cap and the OT cap are separate budgets.
  - **Transfers.** Transfers are re-derived per shift as real TRAVEL reservations that never
    cross a protected break.
  - **Activation.** The activation cap is `floor(85N/100)` with free reuse.
  - **Overlap.** Overlap is checked across shift labels, carry-in included.
  - **Tools and rollback.** Tool calendars are enforced, and `transaction()` rolls back
    insert, remove and swap atomically.
  - Error codes: `PERSON_OVERLAP`, `TRAVEL_INFEASIBLE`, `OUTSIDE_ADMITTED_WINDOW`, `REGULAR_CAP`,
    `OT_CAP`, `ACTIVATION_CAP`, `TOOL_UNAVAILABLE`, `POOL_MISMATCH`, ...
- `focus/engine/crew_selection.py`: exact full crews only.
  - **Search.** A bounded combinations search in preference order: usable applicable certs
    (scarcity), aircraft affinity (no transfer), duty already booked, then person id.
  - **Active heads.** Active heads come first when cap room is short.
  - **No shortcuts.** No clamp, loan or partial crew.
  - Reasons: `CREW_UNAVAILABLE`, `NOT_POOL_MEMBER`, `NOT_QUALIFIED`, `CREW_DUPLICATE_PERSON`,
    `CREW_SIZE_MISMATCH`, `CREW_DEMAND_UNAVAILABLE`, `CREW_SEARCH_BOUND`.
- `focus/engine/model.py`: `EngineContext` per purpose (EXECUTABLE / FORECAST).
  - It carries the windows, pool caps and members, placeability, release floors, line shifts,
    qualification checks and CPM tails.
  - Carry-in duty is a locked ledger item.
- `focus/validation/duty.py`: an independent duty reconstruction that never imports
  `focus.engine`. It covers V6 overlap/travel, V7 windows/caps, V8 activation and
  `TRANSFER_MISSING`.
- `config/policies/rebuild-v1.json`: `travel.unknown_origin_minutes` = 15 (fixture_default).
- `tests/unit/test_duty_ledger.py`, `tests/integration/test_named_crew.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_duty_ledger.py -q        -> 10 passed
.venv/bin/python -m pytest tests/integration/test_named_crew.py -q  -> 4 passed
.venv/bin/python -m pytest tests -q                                 -> 203 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A34 | **Exact crew or wait.** ROOT5 needs 3 distinct people, A AND (B OR C), and gets 3 individually qualified PROD-A S1 people. With two of the four qualified people busy, the result is `CREW_UNAVAILABLE: 2 of 3` and the phase waits: no clamp to two. **Bad crews.** Duplicate ids give `CREW_DUPLICATE_PERSON`. A short crew gives `CREW_SIZE_MISMATCH`. A PROD-B loan gives `NOT_POOL_MEMBER`. Demand 0 gives `CREW_DEMAND_UNAVAILABLE`. |
| A35 | **Boundary.** S1 work 420 plus two 15-minute transfers gives 450 duty = 430 regular + 20 OT. The transfers sit at 09:20 and 11:30, never inside the break or lunch. **No OT.** Without authorized OT the third booking is rejected (`OUTSIDE_ADMITTED_WINDOW`) and nothing is partially booked (265 minutes remain). |
| A36 | **490 and 491.** 490 = 430 + 60 inside the physical windows is allowed. 491 is rejected because no 61st OT minute exists. An OT cap of 59 gives `OT_CAP`. **Carry-in.** A carry-in duty from S3 that runs into next-day S1 gives `PERSON_OVERLAP` naming the S3 label; at the carry-in end the booking succeeds. |
| A37 | Caps are exactly 0/1/8 for N = 1/2/10 at the ledger level, with free reuse. N=0 gives 0, and duplicate or absent roster rows dedupe in admission (`test_pools`, `test_team_inference`). |
| A38 | **Activation refused.** With 8 of 10 MHE heads active, a forced 9th/10th crew gives `ACTIVATION_CAP` on every path: the decoder, repair and neighbourhood moves all book through the same ledger. **V8.** The seed passes the independent reconstruction. A forged final plan that adds two fresh heads is caught as `("V8", "ACTIVATION_CAP")`. |
| A39 | A 0–60, then B: the earliest B start is 75 (baseline 15). The return to A needs a second transfer. Both crew members carry real TRAVEL reservations (1301→1302, 1302→1301). |
| A40 | **Insert, remove, swap.** Inserting B between A and C re-derives both neighbouring transfers. Removing B restores A→C with no transfer. A failing swap inside `transaction()` rolls back to {A, C}. **Blocks.** Travel cannot cross the 09:00 break (`TRAVEL_INFEASIBLE`). A busy torque tool gives `TOOL_UNAVAILABLE`. |
| A41 | **Scarcity.** A-only people (A1-01..04) take A1, so B holders stay free for A2/B2. Usable applicable certs drive the order (A1-07 = 3 vs A1-01 = 1). **Affinity.** The same-aircraft person wins ties: no transfer. |
| A33 (P10 rest) | **Serialized inspectors.** With one QA inspector per shift, every QA phase uses that inspector and their duties never overlap. **WAIT duty.** With `qa.crew_during_ip = WAIT`, the crew before ROOT3's in-process inspection holds booked WAIT duty until the inspection ends, and that duty counts against their shift budget. |

## Fixture versus live

All of this runs on fixture crews, rosters, certificates and OT authorizations. Live
executable crews need the assignment and attendance feeds (empty live, OD-10), OT authority
(OD-04) and a shift-start location source (OD-11).

## Remaining gaps

- No live assignment, attendance or OT feeds (OD-04, OD-10). No live location source for the
  shift-start origin (OD-11).
- Tool calendars exist, but no tool-requirement source is mapped (no tool demand in S04).
