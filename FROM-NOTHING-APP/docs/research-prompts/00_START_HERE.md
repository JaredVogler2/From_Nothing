# FOCUS + NEO Research Evaluation — Agent Entry Point
Date: 2026-10-29. Repository: JaredVogler2/From_Nothing.
## Source of truth
Research PDFs are at repository ROOT (not in a research subfolder). Discover by filename, read complete PDF text, verify exact claims/equations/assumptions, record page citations. Do not invent findings. Current root PDFs include CoRS, Coordinating from Memory (GSEM), and Solving Distributed Flexible Job Shop Using SAT. Other methods (Benders and CNC lot-sizing) are provisional until their PDFs are actually present; do not claim to have read missing papers.
Read root FOCUS_FROM_NOTHING_MASTER.txt (S00-S23, especially S09-S16 and S19-S21), FROM-NOTHING-APP/README.md, current app modules, tests and schemas BEFORE proposing edits. Preserve source-of-truth hard constraints, nine-check independent validation, immutable snapshots, existing SQL discovery and economics.
## Execution
Execute prompts 01 through 08 sequentially; each prompt must generate code/tests/evidence only when warranted, with a feature flag and rollback. Record findings in FROM-NOTHING-APP/docs/research-evaluation/ and evidence in FROM-NOTHING-APP/evidence/research-evaluation/. Never overwrite master specification. Do not create or alter production SQL tables without separately approved migrations. Never use real production data in repo or agent logs.
## Decision standard
Baseline first, paired seeded synthetic scenarios, equal time budgets, confidence intervals, no leakage, zero hard-constraint violations, and independently verified economic value. Accept only demonstrable Pareto improvements (quality, latency, stability, reliability) with documented tradeoffs. Otherwise reject or retain as research-only.
## Prompts
01 Inventory and research extraction
02 Shared benchmark and validator
03 GSEM recovery memory (NEO+FOCUS)
04 CoRS state caching (NEO+FOCUS)
05 SAT solver experiment (FOCUS)
06 Benders fleet decomposition (FOCUS)
07 Supply and sequencing integration (FOCUS+NEO; SAGE-ready)
08 Adversarial synthesis, adoption decisions and rollout
Start by reading 01 and completing its deliverables. Stop at each research gate if prerequisites fail.
