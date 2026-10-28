# Stage P10: Quality holds, final buyoffs and phase conservation

Status: **fixture_verified**. Inspector non-overlap and crew WAIT duty complete with the duty
ledger in P11.

## Changed paths

- `focus/data/inspections.py`: explicit embedded-QA reconciliation
  (`qa.standard_includes_inspection_minutes`, `QA_RECONCILIATION_INVALID`). The total-IP45
  allocation, final45, parallel buyoffs and in-progress splitting were already in place and are
  now covered by tests.
- `focus/data/graph.py`: the AND release and FS successors from the release; QA demands carry the
  inspector role, team and certificate.
- `config/policies/rebuild-v1.json`: `qa.standard_includes_inspection_minutes` = false
  (fixture_default).
- `queries/quality/inspection_profile.sql` (read-only), `docs/execution/QA_POLICY.md`,
  `tests/unit/test_inspection_graph.py`.

## Commands and results

```
.venv/bin/python -m pytest tests/unit/test_inspection_graph.py -q   -> 7 passed
.venv/bin/python -m pytest tests -q                                 -> 148 passed
```

## Acceptance cases

| Case | Observed |
| --- | --- |
| A31 | **ROOT3 phases.** ROOT3 WORK 100 with holds after 40/80 gives WORK40 → IP20 → WORK40 → IP25 → WORK20 → FINAL45, with parallel ODA 20. Production elapsed is conserved (100), IP totals 45 (not 90), and effort is conserved (200 = 100 × crew 2). **Blocking.** Unknown hold locations block (`IP_HOLD_UNKNOWN_LOCATION`, no midpoint IP). 20 + 30 ≠ 45 blocks (`IP_HOLD_ALLOCATION_MISMATCH`). **Reconciliation.** An in-progress split keeps only the remaining work. An embedded-QA standard carves out 90 once. |
| A32 | **QA minutes.** IP45 plus final45 gives 90 QA minutes. **AND release.** Final QA and the ODA buyoff both feed the AND release. **Successors.** Both successors (SUCC3A/B) start from the release with hard FS edges. Equality and later starts are valid; an earlier start violates both. |
| A33 (P10 part) | **Inspector role.** Every QA phase demands one QA-certified inspector from the QA team. Only DEMO-Q1-01/02 qualify (Q1-03/04 hold an irrelevant certificate). **Capacity.** Two simultaneous inspections take two distinct inspectors; a third is `INFEASIBLE`. There is no unlimited or empty-certificate pool, and no production people are in the QA pool. Inspector non-overlap over time and retained-crew WAIT duty are verified with the named duty ledger in P11. |

## Fixture versus live

Holds, buyoffs and inspector certificates are fixture data. There is no live hold-location
producer, so live executable QA stays blocked for IP-requiring jobs.

## Remaining gaps

- No live IP hold-location producer.
- Voided/reopened buyoff lifecycle events arrive with P17.
- Inspector non-overlap and crew WAIT duty land with the ledger in P11.
