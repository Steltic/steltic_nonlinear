"""Pure-Python stop rule for successive mesh levels (no OpenSees).

Stop when ALL primary metric relative deltas vs the previous rung are within ``tol``
(default 0.10 = 10%) AND (for NLRHA) ACCEPTABLE / n_unacceptable are unchanged.
Cap at ``max_rungs``; report not_converged_within_cap if still moving.
"""
from __future__ import annotations
from typing import Any, Mapping, Optional, Sequence

DEFAULT_TOL = 0.10
DEFAULT_MAX_RUNGS = 4

# Keys compared with relative numeric tolerance (per analysis).
NSP_KEYS = ("T1", "Vy", "Vpeak", "delta_t")
NLRHA_KEYS = ("mean_drift_max", "roof_mean_X", "roof_mean_Y", "worst_FC_DC")
NLRHA_STABLE_KEYS = ("ACCEPTABLE", "n_unacceptable")  # must be identical
DDM_KEYS = ("lambda_u", "lambda_G", "phi_s_lambda_u")

PRIMARY_KEYS = {
    "nsp": NSP_KEYS,
    "pushover": NSP_KEYS,
    "nlrha": NLRHA_KEYS,
    "ddm": DDM_KEYS,
}


def relative_delta(prev: Optional[float], curr: Optional[float]) -> Optional[float]:
    """|curr-prev| / max(|prev|, eps). None if either value is None."""
    if prev is None or curr is None:
        return None
    try:
        p = float(prev); c = float(curr)
    except (TypeError, ValueError):
        return None
    denom = max(abs(p), 1e-30)
    return abs(c - p) / denom


def metrics_within_tol(
    prev: Mapping[str, Any],
    curr: Mapping[str, Any],
    keys: Sequence[str],
    tol: float = DEFAULT_TOL,
    *,
    skip_missing: bool = True,
) -> dict:
    """Compare ``keys`` with relative tolerance. Missing keys skipped if skip_missing.

    Returns dict: ok (bool), deltas {key: float|None}, failures [keys exceeding tol].
    """
    deltas = {}
    failures = []
    for k in keys:
        if k not in prev and k not in curr:
            if skip_missing:
                continue
            deltas[k] = None
            failures.append(k)
            continue
        if skip_missing and (prev.get(k) is None or curr.get(k) is None):
            # both present as None -> skip; one present -> fail open as missing data
            if prev.get(k) is None and curr.get(k) is None:
                continue
            deltas[k] = None
            failures.append(k)
            continue
        d = relative_delta(prev.get(k), curr.get(k))
        deltas[k] = d
        if d is None or d > tol + 1e-15:
            failures.append(k)
    return dict(ok=not failures, deltas=deltas, failures=failures, tol=tol)


def _stable_flags(prev: Mapping[str, Any], curr: Mapping[str, Any], keys: Sequence[str]) -> dict:
    changed = []
    for k in keys:
        if k not in prev and k not in curr:
            continue
        if prev.get(k) != curr.get(k):
            changed.append(k)
    return dict(ok=not changed, changed=changed)


def evaluate_ladder_step(
    analysis: str,
    prev_metrics: Mapping[str, Any],
    curr_metrics: Mapping[str, Any],
    *,
    tol: float = DEFAULT_TOL,
    rung_index: int = 1,
    max_rungs: int = DEFAULT_MAX_RUNGS,
) -> dict:
    """Decide whether to stop after ``curr`` vs ``prev`` at this rung index (0-based curr).

    rung_index: index of curr in the ladder (0 = M0, first comparison is at 1).
    Returns scorecard fragment with converged / continue / not_converged_within_cap.
    """
    analysis = analysis.lower()
    if analysis == "pushover":
        analysis = "nsp"
    keys = PRIMARY_KEYS.get(analysis)
    if not keys:
        raise ValueError("unknown analysis %r; expected nsp|nlrha|ddm" % analysis)

    cmp = metrics_within_tol(prev_metrics, curr_metrics, keys, tol=tol)
    stable = dict(ok=True, changed=[])
    if analysis == "nlrha":
        stable = _stable_flags(prev_metrics, curr_metrics, NLRHA_STABLE_KEYS)
        if not stable["ok"]:
            cmp = dict(cmp)
            cmp["ok"] = False
            cmp["failures"] = list(cmp["failures"]) + ["stable:" + c for c in stable["changed"]]

    at_cap = rung_index >= max_rungs - 1
    if cmp["ok"] and stable["ok"]:
        status = "converged"
        stop = True
    elif at_cap:
        status = "not_converged_within_cap"
        stop = True
    else:
        status = "continue"
        stop = False

    return dict(
        analysis=analysis,
        status=status,
        stop=stop,
        tol=tol,
        rung_index=rung_index,
        max_rungs=max_rungs,
        deltas=cmp["deltas"],
        failures=cmp["failures"],
        nlrha_stable=stable,
        prev=dict(prev_metrics),
        curr=dict(curr_metrics),
    )


def walk_ladder(
    analysis: str,
    metric_rows: Sequence[Mapping[str, Any]],
    *,
    tol: float = DEFAULT_TOL,
    max_rungs: int = DEFAULT_MAX_RUNGS,
) -> dict:
    """Walk successive metric dicts; return stop decision + per-step evaluations.

    metric_rows[0] is M0 (no comparison). Stops at first converged or at cap.
    """
    steps = []
    stop_level = None
    final_status = "insufficient_rungs" if len(metric_rows) < 2 else "continue"
    for i in range(1, min(len(metric_rows), max_rungs)):
        ev = evaluate_ladder_step(
            analysis, metric_rows[i - 1], metric_rows[i],
            tol=tol, rung_index=i, max_rungs=max_rungs,
        )
        steps.append(ev)
        if ev["stop"]:
            stop_level = i
            final_status = ev["status"]
            break
    else:
        if len(metric_rows) >= max_rungs:
            # compared through max_rungs-1; if last step not appended with stop, mark cap
            if steps and steps[-1]["status"] == "continue":
                steps[-1] = dict(steps[-1], status="not_converged_within_cap", stop=True)
                final_status = "not_converged_within_cap"
                stop_level = len(steps)
    return dict(
        analysis=analysis,
        tol=tol,
        max_rungs=max_rungs,
        n_rows=len(metric_rows),
        status=final_status,
        stop_level=stop_level,
        steps=steps,
    )
