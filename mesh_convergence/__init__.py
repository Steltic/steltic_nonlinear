"""Fibre mesh-convergence ladder for NSP, NLRHA, and DDM (HR first; CFS-reusable API).

Default stop band: 10% relative on primary metrics (Michael 2026-09-09).
See docs/FIBRE_MESH_CONVERGENCE.md and ddm_exemplars strategy memo.
"""
from .stop_rule import relative_delta, metrics_within_tol, evaluate_ladder_step, DEFAULT_TOL, DEFAULT_MAX_RUNGS
from .rungs import DEFAULT_RUNGS, rung_knobs

__all__ = [
    "relative_delta", "metrics_within_tol", "evaluate_ladder_step",
    "DEFAULT_TOL", "DEFAULT_MAX_RUNGS", "DEFAULT_RUNGS", "rung_knobs",
]
