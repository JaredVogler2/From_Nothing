# Production-shaped benchmark

Synthetic fixture shaped like the program's aircraft (`focus/data/production_fixture.py`); not live proof. Every engine
schedules the same admitted snapshot in one process, so the plans are compared byte for byte.

Command: `python tools/benchmark_production.py --sizes 1,5 --purposes FORECAST --engines native,python --max-moves 0 --budget-seconds 600.0`

| Aircraft | Jobs | Phases | Dependencies | People | Load s | Purpose | Engine | Plan s | First pass s | Re-plan s | Identical | Valid |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5,400 | 10,766 | 27,312 | 342 | 19.09 | FORECAST | native | 10.24 | 4.7 | 1.0 | yes | yes |
| 1 | 5,400 | 10,766 | 27,312 | 342 | 19.09 | FORECAST | python | 47.68 | 23.5 | 18.7 | yes | yes |
| 5 | 27,000 | 44,148 | 126,848 | 710 | 103.14 | FORECAST | native | 55.9 | 24.2 | 12.9 | yes | yes |
| 5 | 27,000 | 44,148 | 126,848 | 710 | 103.14 | FORECAST | python | 1141.48 | 594.2 | 527.9 | yes | yes |

Peak memory per size process (MB): 1 aircraft 487, 5 aircraft 1624.
