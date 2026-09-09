"""Fibre mesh-convergence ladder for NSP, NLRHA, and DDM (HR first; CFS-reusable API).

Default stop band: 10% relative on primary metrics (Michael 2026-09-09).
NLRHA uses dual-gate stop (Gate A suite 10/11 + Gate B FC).
See docs/FIBRE_MESH_CONVERGENCE.md and ddm_exemplars strategy memo.
"""
from .stop_rule import (
    relative_delta, metrics_within_tol, evaluate_ladder_step, walk_ladder,
    evaluate_nlrha_dual_gate, gate_a_suite, gate_b_fc, fc_refine_delta,
    DEFAULT_TOL, DEFAULT_MAX_RUNGS,
    STATUS_CONTINUE, STATUS_CONVERGED, STATUS_CAP, STATUS_NLRHA_COMPLETE,
    STATUS_GATE_A_LOCKED_FC_REFINE, STATUS_GATE_A_LOCKED_FC_PENDING,
)
from .rungs import DEFAULT_RUNGS, rung_knobs

__all__ = [
    "relative_delta", "metrics_within_tol", "evaluate_ladder_step", "walk_ladder",
    "evaluate_nlrha_dual_gate", "gate_a_suite", "gate_b_fc", "fc_refine_delta",
    "DEFAULT_TOL", "DEFAULT_MAX_RUNGS", "DEFAULT_RUNGS", "rung_knobs",
    "STATUS_CONTINUE", "STATUS_CONVERGED", "STATUS_CAP", "STATUS_NLRHA_COMPLETE",
    "STATUS_GATE_A_LOCKED_FC_REFINE", "STATUS_GATE_A_LOCKED_FC_PENDING",
]
