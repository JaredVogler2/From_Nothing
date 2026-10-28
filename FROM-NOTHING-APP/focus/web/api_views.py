"""Read-projection operations (fleet, jobs, plans, shift book, my day, workforce, ...).

These operations are registered in :data:`focus.web.operations.REGISTRY` as
their canonical projections are implemented (P19/P20). Each one reads
immutable snapshots, accepted plans and evaluations from the repositories and
never computes a second scheduling, qualification or scoring authority.
"""
