# Production-shaped benchmark

Synthetic fixture shaped like the program's aircraft (`focus/data/production_fixture.py`); not live proof. Every engine
schedules the same admitted snapshot in one process, so the plans are compared byte for byte.

Command: `python tools/benchmark_production.py --sizes 1,5 --purposes FORECAST --engines native,python --max-moves 0 --budget-seconds 600.0`

| Aircraft | Jobs | Phases | Dependencies | People | Load s | Purpose | Engine | Plan s | First pass s | Re-plan s | Identical | Valid |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5,400 | 10,766 | 27,312 | 342 | 32.87 | FORECAST | native | 25.49 | 13.0 | 3.9 | yes | yes |
| 1 | 5,400 | 10,766 | 27,312 | 342 | 32.87 | FORECAST | python | 55.77 | 28.4 | 19.8 | yes | yes |
| 5 | 27,000 | 44,148 | 126,848 | 710 | 177.71 | FORECAST | native | 239.48 | 123.2 | 75.1 | yes | yes |
| 5 | 27,000 | 44,148 | 126,848 | 710 | 177.71 | FORECAST | python | 1186.19 | 619.1 | 528.1 | yes | yes |

Peak memory per size process (MB): 1 aircraft 499, 5 aircraft 1665.
