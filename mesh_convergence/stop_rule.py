"""Pure-Python stop rule for successive mesh levels (no OpenSees).

NSP / DDM: stop when ALL primary metric relative deltas vs the previous rung are
within ``tol`` (default 0.10 = 10%). Cap at ``max_rungs``.

NLRHA dual-gate (Michael 2026-09-09):
  Gate A — suite / 10/11: ≥10 of 11 records Ch.16-accepted (≤1 unacceptable).
            When A first passes, lock suite EDPs; do not re-run the full 11-record
            ladder solely to chase Gate B.
  Gate B — FC: force-controlled accepted (worst_FC_DC ≤ 1.0). If FC is refined
            across mesh levels, also require ~10% relative Δ on worst_FC_DC vs the
            previous FC refine level.
  Complete only when A ∧ B.
"""
from __future__ import annotations
from typing import Any, Mapping, Optional, Sequence

DEFAULT_TOL = 0.10
DEFAULT_MAX_RUNGS = 4

# Keys compared with relative numeric tolerance (per analysis).
NSP_KEYS = ("T1", "Vy", "Vpeak", "delta_t")
# Legacy combined NLRHA keys (suite + FC) — kept for evaluate_ladder_step callers.
NLRHA_KEYS = ("mean_drift_max", "roof_mean_X", "roof_mean_Y", "worst_FC_DC")
NLRHA_SUITE_KEYS = ("mean_drift_max", "roof_mean_X", "roof_mean_Y", "n_ok", "n_records", "n_unacceptable")
NLRHA_FC_KEYS = ("worst_FC_DC",)
NLRHA_STABLE_KEYS = ("ACCEPTABLE", "n_unacceptable")  # legacy stability check
DDM_KEYS = ("lambda_u", "lambda_G", "phi_s_lambda_u")

# Gate A: ≥10 of 11 Ch.16-accepted (≤1 unacceptable).
NLRHA_MIN_ACCEPTED = 10
NLRHA_MAX_UNACCEPTABLE = 1
# Gate B: FC D/C acceptance threshold.
NLRHA_FC_DC_LIMIT = 1.0

# Status strings for Research / SNL.
STATUS_CONTINUE = "continue"
STATUS_CONVERGED = "converged"
STATUS_CAP = "not_converged_within_cap"
STATUS_INSUFFICIENT = "insufficient_rungs"
STATUS_GATE_A_LOCKED_FC_PENDING = "gate_a_locked_fc_pending"  # A locked, B failed, no more FC rungs
STATUS_GATE_A_LOCKED_FC_REFINE = "gate_a_locked_fc_refine"    # A locked, advance FC-only refine (no full suite)
STATUS_NLRHA_COMPLETE = "nlrha_complete"  # A ∧ B

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


def _n_accepted(metrics: Mapping[str, Any]) -> Optional[int]:
    """Ch.16 accepted count: explicit n_accepted, else n_records - n_unacceptable."""
    if metrics.get("n_accepted") is not None:
        try:
            return int(metrics["n_accepted"])
        except (TypeError, ValueError):
            return None
    n_rec = metrics.get("n_records")
    n_un = metrics.get("n_unacceptable")
    if n_rec is None or n_un is None:
        return None
    try:
        return int(n_rec) - int(n_un)
    except (TypeError, ValueError):
        return None


def gate_a_suite(
    metrics: Mapping[str, Any],
    *,
    min_accepted: int = NLRHA_MIN_ACCEPTED,
    max_unacceptable: int = NLRHA_MAX_UNACCEPTABLE,
) -> dict:
    """Gate A — suite / 10/11: ≥min_accepted Ch.16-accepted (≤max_unacceptable).

    Returns dict with passed, n_accepted, n_unacceptable, n_records, locked_edps
    (suite EDP snapshot when passed, else None).
    """
    n_rec = metrics.get("n_records")
    try:
        n_rec_i = int(n_rec) if n_rec is not None else None
    except (TypeError, ValueError):
        n_rec_i = None
    n_un = metrics.get("n_unacceptable")
    try:
        n_un_i = int(n_un) if n_un is not None else None
    except (TypeError, ValueError):
        n_un_i = None
    n_acc = _n_accepted(metrics)

    # Prefer explicit counts; also accept ≤1 unacceptable when n_records known as 11.
    passed = False
    if n_acc is not None and n_acc >= min_accepted:
        passed = True
    elif n_un_i is not None and n_un_i <= max_unacceptable and (
        n_rec_i is None or n_rec_i >= min_accepted
    ):
        # ≤1 unacceptable and enough records to imply ≥10/11 when n_records≥10
        if n_rec_i is not None and (n_rec_i - n_un_i) >= min_accepted:
            passed = True
        elif n_rec_i is None and n_un_i <= max_unacceptable:
            # incomplete row — do not pass without accepted count
            passed = False

    locked = None
    if passed:
        locked = {k: metrics.get(k) for k in NLRHA_SUITE_KEYS}
        locked["n_accepted"] = n_acc if n_acc is not None else (
            (n_rec_i - n_un_i) if (n_rec_i is not None and n_un_i is not None) else None
        )
        locked["ACCEPTABLE"] = metrics.get("ACCEPTABLE")
    return dict(
        gate="A",
        name="suite_10_of_11",
        passed=passed,
        n_accepted=n_acc,
        n_unacceptable=n_un_i,
        n_records=n_rec_i,
        min_accepted=min_accepted,
        max_unacceptable=max_unacceptable,
        locked_edps=locked,
    )


def gate_b_fc(
    metrics: Mapping[str, Any],
    *,
    prev_fc_metrics: Optional[Mapping[str, Any]] = None,
    tol: float = DEFAULT_TOL,
    dc_limit: float = NLRHA_FC_DC_LIMIT,
) -> dict:
    """Gate B — FC: worst_FC_DC ≤ dc_limit; if refining, also ≤tol relative Δ vs prev.

    When ``prev_fc_metrics`` is None (first FC observation at this lock / single level),
    only the absolute acceptance check applies. When a previous FC refine level is
    provided, both absolute acceptance AND relative Δ ≤ tol are required.
    """
    dc = metrics.get("worst_FC_DC")
    try:
        dc_f = float(dc) if dc is not None else None
    except (TypeError, ValueError):
        dc_f = None

    # Prefer explicit package flag when present; else worst_FC_DC ≤ limit.
    if "force_controlled_ok" in metrics and metrics.get("force_controlled_ok") is not None:
        accepted = bool(metrics.get("force_controlled_ok"))
        if accepted and dc_f is None:
            pass  # flag alone is enough
        elif dc_f is not None and not (dc_f <= dc_limit + 1e-15):
            # DC present and over limit overrides a stale True flag
            accepted = False
    else:
        accepted = dc_f is not None and dc_f <= dc_limit + 1e-15
    delta = None
    refine_ok = True
    if prev_fc_metrics is not None:
        cmp = metrics_within_tol(prev_fc_metrics, metrics, NLRHA_FC_KEYS, tol=tol)
        delta = cmp["deltas"].get("worst_FC_DC")
        refine_ok = cmp["ok"]

    passed = bool(accepted and refine_ok)
    return dict(
        gate="B",
        name="force_controlled",
        passed=passed,
        accepted=accepted,
        refine_ok=refine_ok,
        worst_FC_DC=dc_f,
        dc_limit=dc_limit,
        delta_vs_prev=delta,
        tol=tol,
        had_prev_refine=prev_fc_metrics is not None,
    )


def fc_refine_delta(
    prev_fc: Mapping[str, Any],
    curr_fc: Mapping[str, Any],
    *,
    tol: float = DEFAULT_TOL,
) -> dict:
    """~10% relative Δ check on FC primary (worst_FC_DC) between FC refine levels."""
    return metrics_within_tol(prev_fc, curr_fc, NLRHA_FC_KEYS, tol=tol)


def evaluate_nlrha_dual_gate(
    metric_rows: Sequence[Mapping[str, Any]],
    *,
    tol: float = DEFAULT_TOL,
    max_rungs: int = DEFAULT_MAX_RUNGS,
) -> dict:
    """Walk NLRHA rows with dual-gate A/B separation.

    - Continues full-suite climbs while Gate A fails (``schedule_full_suite=True``).
    - On first Gate A pass: locks suite EDPs; **stops** further full 11-suite rungs.
    - Immediately evaluates Gate B on the lock level. If B fails and rungs remain,
      status ``gate_a_locked_fc_refine`` with ``schedule_fc_refine=True`` (FC-only
      path — do **not** schedule another full ``--n 11`` suite).
    - Subsequent rows after lock are treated as FC refine levels (10% Δ on
      ``worst_FC_DC`` vs previous FC level, plus absolute FC acceptance).
    - Complete only when A ∧ B (``nlrha_complete``).
    """
    def _result(**kw):
        base = dict(
            analysis="nlrha",
            tol=tol,
            max_rungs=max_rungs,
            n_rows=len(metric_rows),
            locked_suite=None,
            lock_level=None,
            stop_level=None,
            fc_level=None,
            steps=[],
            schedule_full_suite=False,
            schedule_fc_refine=False,
            stop_full_suite=True,
            stop=True,
        )
        base.update(kw)
        return base

    if not metric_rows:
        return _result(
            status=STATUS_INSUFFICIENT,
            gate_a=dict(passed=False),
            gate_b=dict(passed=False),
            message="no NLRHA metric rows",
        )

    steps = []
    lock_level = None
    locked_suite = None
    gate_a_state = dict(passed=False)
    gate_b_state = dict(passed=False)
    prev_fc = None  # previous FC refine level metrics
    last_i = -1

    for i, row in enumerate(metric_rows):
        if i >= max_rungs:
            break
        last_i = i
        mode = "full_suite" if lock_level is None else "fc_refine"

        if lock_level is None:
            ga = gate_a_suite(row)
            gb = gate_b_fc(row, prev_fc_metrics=None, tol=tol)
            step = dict(rung_index=i, mode=mode, gate_a=ga, gate_b=gb)
            gate_a_state = ga
            gate_b_state = gb
            steps.append(step)

            if ga["passed"]:
                lock_level = i
                locked_suite = dict(ga["locked_edps"] or {})
                prev_fc = {k: row.get(k) for k in NLRHA_FC_KEYS}
                if "force_controlled_ok" in row:
                    prev_fc["force_controlled_ok"] = row.get("force_controlled_ok")
                step["gate_a_locked"] = True
                step["locked_suite"] = locked_suite
                gate_a_state = dict(ga, passed=True, lock_level=lock_level, locked_edps=locked_suite)

                if gb["passed"]:
                    return _result(
                        status=STATUS_NLRHA_COMPLETE,
                        stop=True,
                        stop_full_suite=True,
                        schedule_full_suite=False,
                        schedule_fc_refine=False,
                        gate_a=gate_a_state,
                        gate_b=dict(gb, passed=True, fc_level=i),
                        locked_suite=locked_suite,
                        lock_level=lock_level,
                        stop_level=lock_level,
                        fc_level=i,
                        steps=steps,
                        message="NLRHA complete: Gate A locked and Gate B (FC) accepted",
                    )
                # A locked, B failed — immediately move to FC refine (no more full suite).
                # Keep walking if later FC refine rows are already present; else signal refine.
                continue
            # A not met — keep full-suite climb
            continue

        # ---- FC refine path (after Gate A lock): no suite re-eval for Gate A ----
        gb = gate_b_fc(row, prev_fc_metrics=prev_fc, tol=tol)
        ga_locked = dict(
            passed=True,
            lock_level=lock_level,
            locked_edps=locked_suite,
            n_accepted=(locked_suite or {}).get("n_accepted"),
            n_unacceptable=(locked_suite or {}).get("n_unacceptable"),
            n_records=(locked_suite or {}).get("n_records"),
        )
        step = dict(rung_index=i, mode="fc_refine", gate_a=ga_locked, gate_b=gb)
        steps.append(step)
        gate_a_state = ga_locked
        gate_b_state = gb

        if gb["passed"]:
            return _result(
                status=STATUS_NLRHA_COMPLETE,
                stop=True,
                stop_full_suite=True,
                schedule_full_suite=False,
                schedule_fc_refine=False,
                gate_a=gate_a_state,
                gate_b=dict(gb, passed=True, fc_level=i),
                locked_suite=locked_suite,
                lock_level=lock_level,
                stop_level=lock_level,
                fc_level=i,
                steps=steps,
                message=(
                    "NLRHA complete: Gate A locked at level %s; Gate B accepted at FC level %s"
                    % (lock_level, i)
                ),
            )
        # Update prev FC baseline for next refine Δ (chain successive FC levels)
        prev_fc = {k: row.get(k) for k in NLRHA_FC_KEYS}
        if "force_controlled_ok" in row:
            prev_fc["force_controlled_ok"] = row.get("force_controlled_ok")

    # ---- finished available rows ----
    at_cap = last_i >= max_rungs - 1 or len(metric_rows) >= max_rungs
    more_rungs = (last_i + 1) < max_rungs

    if lock_level is None:
        status = STATUS_CAP if at_cap else STATUS_CONTINUE
        return _result(
            status=status,
            stop=at_cap,
            stop_full_suite=at_cap,
            schedule_full_suite=not at_cap,
            schedule_fc_refine=False,
            gate_a=gate_a_state,
            gate_b=gate_b_state,
            stop_level=(max_rungs - 1) if at_cap else None,
            steps=steps,
            message=(
                "Gate A (10/11) not met within cap"
                if at_cap else
                "Gate A (10/11) not met — continue full-suite mesh climb"
            ),
        )

    # A locked, B not yet — advance FC refine if rungs remain
    if more_rungs and not gate_b_state.get("passed"):
        return _result(
            status=STATUS_GATE_A_LOCKED_FC_REFINE,
            stop=False,  # overall ladder continues on FC-only path
            stop_full_suite=True,
            schedule_full_suite=False,
            schedule_fc_refine=True,
            gate_a=gate_a_state,
            gate_b=dict(gate_b_state, passed=False),
            locked_suite=locked_suite,
            lock_level=lock_level,
            stop_level=lock_level,
            steps=steps,
            next_fc_rung_index=last_i + 1,
            message=(
                "Gate A locked at mesh level %s; advancing FC-only refine "
                "(do not re-run full 11-record suite for Gate B)"
                % lock_level
            ),
        )

    # A locked, B still failing, no more FC rungs available
    return _result(
        status=STATUS_GATE_A_LOCKED_FC_PENDING,
        stop=True,
        stop_full_suite=True,
        schedule_full_suite=False,
        schedule_fc_refine=False,
        gate_a=gate_a_state,
        gate_b=dict(gate_b_state, passed=False),
        locked_suite=locked_suite,
        lock_level=lock_level,
        stop_level=lock_level,
        steps=steps,
        message=(
            "Gate A locked at level %s; Gate B (FC) not accepted — FC refine "
            "exhausted/pending; suite EDPs remain locked (no further full 11-suite)"
            % lock_level
        ),
    )


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

    For NLRHA, prefer ``evaluate_nlrha_dual_gate`` / ``walk_ladder`` — this pairwise
    helper keeps legacy numeric+stability behaviour for unit tests that still call it.
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
        status = STATUS_CONVERGED
        stop = True
    elif at_cap:
        status = STATUS_CAP
        stop = True
    else:
        status = STATUS_CONTINUE
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

    For NLRHA, uses the dual-gate walker (Gate A suite lock + Gate B FC).
    For NSP/DDM: metric_rows[0] is M0 (no comparison). Stops at first converged or at cap.
    """
    analysis_l = analysis.lower()
    if analysis_l == "pushover":
        analysis_l = "nsp"
    if analysis_l == "nlrha":
        out = evaluate_nlrha_dual_gate(metric_rows, tol=tol, max_rungs=max_rungs)
        # Map nlrha_complete → also expose status aliases used by scorecard
        if out["status"] == STATUS_NLRHA_COMPLETE:
            out = dict(out, status=STATUS_NLRHA_COMPLETE, converged=True)
        return out

    steps = []
    stop_level = None
    final_status = STATUS_INSUFFICIENT if len(metric_rows) < 2 else STATUS_CONTINUE
    for i in range(1, min(len(metric_rows), max_rungs)):
        ev = evaluate_ladder_step(
            analysis_l, metric_rows[i - 1], metric_rows[i],
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
            if steps and steps[-1]["status"] == STATUS_CONTINUE:
                steps[-1] = dict(steps[-1], status=STATUS_CAP, stop=True)
                final_status = STATUS_CAP
                stop_level = len(steps)
    return dict(
        analysis=analysis_l,
        tol=tol,
        max_rungs=max_rungs,
        n_rows=len(metric_rows),
        status=final_status,
        stop_level=stop_level,
        steps=steps,
        schedule_full_suite=final_status == STATUS_CONTINUE,
        stop_full_suite=final_status != STATUS_CONTINUE,
    )
