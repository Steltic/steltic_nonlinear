"""Fibre / product mesh-convergence ladder for NSP, NLRHA, and DDM.

NSP & HR DDM: fibre + mesh 10%.
NLRHA: ModIMK → PZ×1 → fibre+mesh; dual-gate A∧B; early abort on 2 NC;
       ModIMK Gate A ⇒ stay ModIMK for FC (no fibre switch).

See docs/FIBRE_MESH_CONVERGENCE.md and docs/PRODUCT_DEFAULTS.md.
"""
from .stop_rule import (
    relative_delta, metrics_within_tol, evaluate_ladder_step, walk_ladder,
    evaluate_nlrha_dual_gate, gate_a_suite, gate_b_fc, fc_refine_delta,
    DEFAULT_TOL, DEFAULT_MAX_RUNGS,
    STATUS_CONTINUE, STATUS_CONVERGED, STATUS_CAP, STATUS_NLRHA_COMPLETE,
    STATUS_GATE_A_LOCKED_FC_REFINE, STATUS_GATE_A_LOCKED_FC_PENDING,
)
from .rungs import DEFAULT_RUNGS, rung_knobs
from .nlrha_ladder import (
    should_early_abort, count_nc, lock_fc_plasticity, plan_after_row,
    evaluate_product_nlrha, METHOD_STAGES, EARLY_ABORT_NC,
)

__all__ = [
    "relative_delta", "metrics_within_tol", "evaluate_ladder_step", "walk_ladder",
    "evaluate_nlrha_dual_gate", "gate_a_suite", "gate_b_fc", "fc_refine_delta",
    "DEFAULT_TOL", "DEFAULT_MAX_RUNGS", "DEFAULT_RUNGS", "rung_knobs",
    "STATUS_CONTINUE", "STATUS_CONVERGED", "STATUS_CAP", "STATUS_NLRHA_COMPLETE",
    "STATUS_GATE_A_LOCKED_FC_REFINE", "STATUS_GATE_A_LOCKED_FC_PENDING",
    "should_early_abort", "count_nc", "lock_fc_plasticity", "plan_after_row",
    "evaluate_product_nlrha", "METHOD_STAGES", "EARLY_ABORT_NC",
]
