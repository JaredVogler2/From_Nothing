"""Shift-by-shift fleet simulation over SQLite tables (synthetic; never live data).

Each shift: the sources as loaded so far are admitted into a snapshot (the production snapshot builder), planned by the
production engine with the previous shift's published plan defended, independently validated, published as one static
snapshot, then executed by a seeded model of the shop floor whose outcomes become the next shift's source rows.
Everything lands in tables (``focus.sim.db``): what was pulled, what was calculated, how the plan was made, the plan,
what happened, and each team's performance.
"""
