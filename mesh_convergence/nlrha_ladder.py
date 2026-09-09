"""NLRHA product method ladder (rules 1, 5, 7).

Sequence:
  1. ModIMK (rigid PZ) — full suite
  2. PZ once (scissors) — single try, still ModIMK hinges
  3. Fibre + mesh 10% (M0→M3) if Gate A still <10/11

If Gate A hits on ModIMK (or ModIMK+PZ): stay that plasticity for Gate B FC —
do **not** switch to fibre for FC.

Early abort: once 2 records are NC in a suite, abandon the rest and advance
to the next method / mesh rung.
"""
from __future__ import annotations
from typing import Any, Mapping, Optional, Sequence

from .stop_rule import (
    DEFAULT_MAX_RUNGS, DEFAULT_TOL, evaluate_nlrha_dual_gate, gate_a_suite,
    STATUS_GATE_A_LOCKED_FC_REFINE, STATUS_GATE_A_LOCKED_FC_PENDING,
    STATUS_NLRHA_COMPLETE, STATUS_CONTINUE, STATUS_CAP,
)

EARLY_ABORT_NC = 2

# Method stages before fibre mesh climb (max ~4 total product rungs when combined
# with fibre M0–M3 — fibre uses remaining mesh budget after method tries).
METHOD_STAGES = (
    dict(id="modimk", plasticity="imk", panel_zone="rigid",
         intent="ModIMK baseline (PZ rigid)"),
    dict(id="pz_once", plasticity="imk", panel_zone="scissors",
         intent="PZ scissors try ×1 (still ModIMK)"),
)


def count_nc(results: Sequence[Mapping[str, Any]]) -> int:
    """Count non-converged records (solver NC / crawl abort)."""
    n = 0
    for r in results:
        if r.get("converged") is False:
            n += 1
        elif r.get("converged") is None and r.get("status") in ("NC", "nc", "nonconverged"):
            n += 1
    return n


def should_early_abort(results: Sequence[Mapping[str, Any]], *, max_nc: int = EARLY_ABORT_NC) -> bool:
    """True once ≥max_nc records are NC — abandon rest of suite, next mesh/method."""
    return count_nc(results) >= max_nc


def method_stage_plan(*, include_fibre_mesh: bool = True, max_rungs: int = DEFAULT_MAX_RUNGS):
    """Ordered product stages for NLRHA mesh-converge.

    Returns list of stage dicts with keys: id, plasticity, panel_zone, mesh_level,
    intent, mode ('method' | 'fibre_mesh' | 'fc_refine').
    """
    stages = []
    for s in METHOD_STAGES:
        stages.append(dict(s, mesh_level=None, mode="method"))
    if include_fibre_mesh:
        # Cap total product rungs ~4: method tries consume first slots.
        fibre_budget = max(1, max_rungs - len(METHOD_STAGES) + 2)
        # Prefer up to 4 fibre mesh levels but respect overall cap narrative.
        from .rungs import DEFAULT_RUNGS
        for r in DEFAULT_RUNGS[:max(1, min(fibre_budget, max_rungs))]:
            stages.append(dict(
                id="fibre_%s" % r["level"],
                plasticity="fibre",
                panel_zone="rigid",
                mesh_level=r["level"],
                rung=r,
                intent="fibre + mesh %s" % r["level"],
                mode="fibre_mesh",
            ))
    return stages


def lock_fc_plasticity(gate_a_stage: Mapping[str, Any]) -> dict:
    """Plasticity/PZ to keep for Gate B FC after Gate A lock (never upgrade to fibre)."""
    plast = str(gate_a_stage.get("plasticity") or "imk").lower()
    if plast in ("fiber", "distributed"):
        plast = "fibre"
    # If A locked on fibre, FC refine stays fibre; if on ModIMK/PZ, stay imk.
    return dict(
        plasticity=plast,
        panel_zone=gate_a_stage.get("panel_zone") or ("rigid" if plast == "fibre" else "rigid"),
        stay_modimk_for_fc=(plast == "imk"),
        no_fibre_for_fc=(plast == "imk"),
    )


def plan_after_row(
    stage: Mapping[str, Any],
    metrics: Mapping[str, Any],
    *,
    early_aborted: bool = False,
) -> dict:
    """Decide next action after one NLRHA suite/method attempt.

    Returns dict with keys: gate_a_passed, advance_method, start_fibre_mesh,
    lock_fc, schedule_fc_refine, abandon_suite (early abort), message.
    """
    ga = gate_a_suite(metrics)
    plast = str(stage.get("plasticity") or "imk").lower()
    if early_aborted or should_early_abort(
        metrics.get("record_results") or metrics.get("results") or []
    ):
        # Treat early abort as Gate A not met for this stage; advance.
        if plast == "fibre":
            return dict(
                gate_a_passed=False,
                advance_method=False,
                start_fibre_mesh=False,
                advance_fibre_mesh=True,
                lock_fc=False,
                schedule_fc_refine=False,
                abandon_suite=True,
                message="early abort (≥%d NC) — next mesh size" % EARLY_ABORT_NC,
                gate_a=ga,
            )
        return dict(
            gate_a_passed=False,
            advance_method=True,
            start_fibre_mesh=(stage.get("id") == "pz_once"),
            advance_fibre_mesh=False,
            lock_fc=False,
            schedule_fc_refine=False,
            abandon_suite=True,
            message="early abort (≥%d NC) — next method/mesh" % EARLY_ABORT_NC,
            gate_a=ga,
        )

    if ga["passed"]:
        fc = lock_fc_plasticity(stage)
        return dict(
            gate_a_passed=True,
            advance_method=False,
            start_fibre_mesh=False,
            advance_fibre_mesh=False,
            lock_fc=True,
            schedule_fc_refine=True,  # caller checks Gate B
            abandon_suite=False,
            fc_settings=fc,
            message=(
                "Gate A locked on %s — stay %s for FC (no fibre switch)"
                % (stage.get("id"), fc["plasticity"])
                if fc["no_fibre_for_fc"] else
                "Gate A locked on fibre — FC refine stays fibre"
            ),
            gate_a=ga,
            fc_settings_detail=fc,
        )

    # Gate A not met
    if stage.get("id") == "modimk":
        return dict(
            gate_a_passed=False, advance_method=True, start_fibre_mesh=False,
            advance_fibre_mesh=False, lock_fc=False, schedule_fc_refine=False,
            abandon_suite=False, gate_a=ga,
            message="Gate A <10/11 on ModIMK — try PZ once",
        )
    if stage.get("id") == "pz_once":
        return dict(
            gate_a_passed=False, advance_method=False, start_fibre_mesh=True,
            advance_fibre_mesh=False, lock_fc=False, schedule_fc_refine=False,
            abandon_suite=False, gate_a=ga,
            message="Gate A <10/11 after PZ×1 — revert to fibre + mesh 10%",
        )
    # fibre mesh level
    return dict(
        gate_a_passed=False, advance_method=False, start_fibre_mesh=False,
        advance_fibre_mesh=True, lock_fc=False, schedule_fc_refine=False,
        abandon_suite=False, gate_a=ga,
        message="Gate A <10/11 on fibre mesh — next mesh rung",
    )


def evaluate_product_nlrha(
    rows: Sequence[Mapping[str, Any]],
    *,
    tol: float = DEFAULT_TOL,
    max_rungs: int = DEFAULT_MAX_RUNGS,
) -> dict:
    """Walk product NLRHA rows that each carry ``_stage`` metadata.

    Each row should include dual-gate metrics plus optional:
      _stage: {id, plasticity, panel_zone, mesh_level, mode}
      n_nc / early_aborted
    """
    # Reuse dual-gate on metric fields; annotate product method lock.
    dual = evaluate_nlrha_dual_gate(rows, tol=tol, max_rungs=max_rungs)
    lock_stage = None
    if dual.get("lock_level") is not None and dual["lock_level"] < len(rows):
        lock_stage = (rows[dual["lock_level"]] or {}).get("_stage") or {}
    fc_settings = lock_fc_plasticity(lock_stage) if lock_stage else None
    if fc_settings and dual.get("schedule_fc_refine"):
        dual = dict(dual)
        dual["fc_settings"] = fc_settings
        dual["no_fibre_for_fc"] = fc_settings.get("no_fibre_for_fc", False)
        if fc_settings.get("no_fibre_for_fc"):
            dual["message"] = (
                (dual.get("message") or "")
                + " | FC refine stays ModIMK (Gate A lock method); do not switch to fibre"
            ).strip(" |")
    elif fc_settings and dual.get("status") == STATUS_NLRHA_COMPLETE:
        dual = dict(dual, fc_settings=fc_settings,
                    no_fibre_for_fc=fc_settings.get("no_fibre_for_fc", False))
    return dual
