"""Smoke tests for fibre mesh-convergence stop rule (pure Python, no OpenSees)."""
from mesh_convergence.stop_rule import (
    relative_delta, metrics_within_tol, evaluate_ladder_step, walk_ladder,
    evaluate_nlrha_dual_gate, gate_a_suite, gate_b_fc, fc_refine_delta,
    DEFAULT_TOL, NSP_KEYS, NLRHA_KEYS,
    STATUS_CONTINUE, STATUS_NLRHA_COMPLETE,
    STATUS_GATE_A_LOCKED_FC_REFINE, STATUS_GATE_A_LOCKED_FC_PENDING,
)
from mesh_convergence.rungs import DEFAULT_RUNGS, rung_knobs


def test_default_tol_is_10_percent():
    assert abs(DEFAULT_TOL - 0.10) < 1e-15


def test_relative_delta():
    assert relative_delta(100.0, 110.0) == 0.10
    assert relative_delta(100.0, 109.0) < 0.10
    assert relative_delta(None, 1.0) is None


def test_metrics_within_tol_nsp():
    prev = dict(T1=1.0, Vy=100.0, Vpeak=150.0, delta_t=10.0)
    ok = dict(T1=1.05, Vy=102.0, Vpeak=148.0, delta_t=10.5)
    bad = dict(T1=1.15, Vy=102.0, Vpeak=148.0, delta_t=10.5)
    r_ok = metrics_within_tol(prev, ok, NSP_KEYS, tol=0.10)
    r_bad = metrics_within_tol(prev, bad, NSP_KEYS, tol=0.10)
    assert r_ok["ok"] and not r_ok["failures"]
    assert not r_bad["ok"] and "T1" in r_bad["failures"]


def test_boundary_exactly_10_percent_is_ok():
    prev = dict(T1=1.0, Vy=100.0, Vpeak=100.0, delta_t=10.0)
    curr = dict(T1=1.1, Vy=110.0, Vpeak=110.0, delta_t=11.0)
    r = metrics_within_tol(prev, curr, NSP_KEYS, tol=0.10)
    assert r["ok"], r


def test_nlrha_verdict_instability_forces_continue():
    """Legacy pairwise helper: unstable Ch.16 flags force continue."""
    prev = dict(mean_drift_max=0.03, roof_mean_X=0.02, roof_mean_Y=0.015,
                worst_FC_DC=1.0, ACCEPTABLE=True, n_unacceptable=0)
    curr = dict(mean_drift_max=0.0305, roof_mean_X=0.0201, roof_mean_Y=0.0151,
                worst_FC_DC=1.01, ACCEPTABLE=False, n_unacceptable=1)
    ev = evaluate_ladder_step("nlrha", prev, curr, tol=0.10, rung_index=1, max_rungs=4)
    assert ev["status"] == "continue"
    assert any(f.startswith("stable:") for f in ev["failures"])


def test_walk_ladder_converges_and_caps():
    rows = [
        dict(T1=1.0, Vy=100.0, Vpeak=150.0, delta_t=10.0),
        dict(T1=1.20, Vy=120.0, Vpeak=180.0, delta_t=12.0),
        dict(T1=1.22, Vy=122.0, Vpeak=182.0, delta_t=12.2),
    ]
    out = walk_ladder("nsp", rows, tol=0.10, max_rungs=4)
    assert out["status"] == "converged"
    assert out["stop_level"] == 2

    moving = [
        dict(T1=1.0, Vy=100.0, Vpeak=100.0, delta_t=10.0),
        dict(T1=1.2, Vy=120.0, Vpeak=120.0, delta_t=12.0),
        dict(T1=1.45, Vy=145.0, Vpeak=145.0, delta_t=14.5),
        dict(T1=1.75, Vy=175.0, Vpeak=175.0, delta_t=17.5),
    ]
    cap = walk_ladder("nsp", moving, tol=0.10, max_rungs=4)
    assert cap["status"] == "not_converged_within_cap"


def test_rung_knobs_fibre_and_ddm():
    m1 = DEFAULT_RUNGS[1]
    kn = rung_knobs(m1, "nlrha")
    assert kn["plasticity"] == "fibre"
    assert kn["member_nseg"] == 4
    assert kn["env"]["SNL_FIBRE_NF_FLANGE"] == "8,4"
    d = rung_knobs(m1, "ddm")
    assert d["nsub"] == (4, 4, 4)


def test_dry_run_driver(tmp_path):
    from mesh_convergence.driver import main
    import argparse, json
    a = argparse.Namespace(
        package=str(tmp_path / "fake_job"),
        analyses=["nsp"],
        out=str(tmp_path / "out"),
        steltic_engine=None,
        params=None,
        tol=0.10,
        max_rungs=4,
        site_class="D",
        risk_category=None,
        n_records=11,
        parallel=1,
        dt=0.01,
        dry_run=True,
        early_abort_nc=2,
        rigid_end_offset=None,
        no_rigid_end_offset=False,
    )
    (tmp_path / "fake_job").mkdir()
    rc = main(a)
    assert rc == 0
    sc = tmp_path / "out" / "mesh_convergence_scorecard_nsp.json"
    assert sc.exists()
    doc = json.loads(sc.read_text())
    assert doc["tol"] == 0.10
    assert doc["method"] == "fibre"
    assert doc["status"] in ("converged", "not_converged_within_cap", "continue")


# ---------------------------------------------------------------------------
# NLRHA dual-gate
# ---------------------------------------------------------------------------

def _suite_row(*, n_unacc, worst_fc, n_rec=11, drift=0.03, roof_x=0.02, roof_y=0.015):
    n_acc = n_rec - n_unacc
    return dict(
        mean_drift_max=drift,
        roof_mean_X=roof_x,
        roof_mean_Y=roof_y,
        worst_FC_DC=worst_fc,
        force_controlled_ok=(worst_fc is not None and worst_fc <= 1.0),
        n_ok=n_rec,
        n_records=n_rec,
        n_unacceptable=n_unacc,
        n_accepted=n_acc,
        ACCEPTABLE=(n_unacc <= 1 and worst_fc is not None and worst_fc <= 1.0),
    )


def test_gate_a_suite_10_of_11():
    fail = gate_a_suite(_suite_row(n_unacc=6, worst_fc=1.2))
    assert not fail["passed"]
    assert fail["n_accepted"] == 5

    ok = gate_a_suite(_suite_row(n_unacc=1, worst_fc=1.2))
    assert ok["passed"]
    assert ok["n_accepted"] == 10
    assert ok["locked_edps"]["mean_drift_max"] == 0.03
    assert "n_unacceptable" in ok["locked_edps"]

    ok11 = gate_a_suite(_suite_row(n_unacc=0, worst_fc=0.9))
    assert ok11["passed"] and ok11["n_accepted"] == 11


def test_gate_b_fc_absolute_and_refine():
    # Absolute only (no prev refine)
    bad = gate_b_fc(dict(worst_FC_DC=1.2))
    assert not bad["passed"] and not bad["accepted"]

    good = gate_b_fc(dict(worst_FC_DC=1.0))
    assert good["passed"] and good["accepted"]

    # Refine: accepted but Δ > 10% vs prev → not passed
    prev = dict(worst_FC_DC=1.2)
    curr = dict(worst_FC_DC=0.95)  # ~20.8% drop
    mid = gate_b_fc(curr, prev_fc_metrics=prev, tol=0.10)
    assert mid["accepted"] and not mid["refine_ok"] and not mid["passed"]

    # Refine within 10% and accepted
    prev2 = dict(worst_FC_DC=1.05)
    curr2 = dict(worst_FC_DC=0.98)  # ~6.7%
    ok = gate_b_fc(curr2, prev_fc_metrics=prev2, tol=0.10)
    assert ok["passed"] and ok["refine_ok"] and ok["accepted"]


def test_fc_refine_delta_10_percent():
    prev = dict(worst_FC_DC=1.0)
    within = fc_refine_delta(prev, dict(worst_FC_DC=1.10), tol=0.10)
    assert within["ok"]
    over = fc_refine_delta(prev, dict(worst_FC_DC=1.15), tol=0.10)
    assert not over["ok"] and "worst_FC_DC" in over["failures"]


def test_dual_gate_a_not_met_continues_full_suite():
    """A not met → continue; schedule_full_suite=True; no A lock."""
    rows = [
        _suite_row(n_unacc=6, worst_fc=1.3),  # M0 like MC4
        _suite_row(n_unacc=3, worst_fc=1.2),  # still <10 accepted
    ]
    out = evaluate_nlrha_dual_gate(rows, tol=0.10, max_rungs=4)
    assert out["status"] == STATUS_CONTINUE
    assert out["schedule_full_suite"] is True
    assert out["schedule_fc_refine"] is False
    assert out["locked_suite"] is None
    assert out["stop"] is False


def test_dual_gate_a_met_b_fail_locks_and_schedules_fc_refine():
    """A met, B fail → lock A; no full-suite continue; advance FC refine."""
    rows = [
        _suite_row(n_unacc=6, worst_fc=1.3),
        _suite_row(n_unacc=0, worst_fc=1.15),  # A passes, FC still > 1.0
    ]
    out = evaluate_nlrha_dual_gate(rows, tol=0.10, max_rungs=4)
    assert out["status"] == STATUS_GATE_A_LOCKED_FC_REFINE
    assert out["schedule_full_suite"] is False
    assert out["schedule_fc_refine"] is True
    assert out["stop_full_suite"] is True
    assert out["stop"] is False  # overall continues on FC path
    assert out["lock_level"] == 1
    assert out["locked_suite"]["n_unacceptable"] == 0
    assert out["locked_suite"]["mean_drift_max"] == 0.03
    assert out["gate_a"]["passed"] is True
    assert out["gate_b"]["passed"] is False


def test_dual_gate_a_and_b_complete():
    """A ∧ B at same level → nlrha_complete."""
    rows = [
        _suite_row(n_unacc=6, worst_fc=1.3),
        _suite_row(n_unacc=1, worst_fc=0.95),  # A+B both pass
    ]
    out = evaluate_nlrha_dual_gate(rows, tol=0.10, max_rungs=4)
    assert out["status"] == STATUS_NLRHA_COMPLETE
    assert out["stop"] is True
    assert out["schedule_full_suite"] is False
    assert out["schedule_fc_refine"] is False
    assert out["gate_a"]["passed"] and out["gate_b"]["passed"]
    assert out["lock_level"] == 1


def test_dual_gate_fc_refine_then_complete():
    """After A lock, FC refine rows (no suite re-eval) reach B with 10% Δ."""
    rows = [
        _suite_row(n_unacc=6, worst_fc=1.4, drift=0.04),
        _suite_row(n_unacc=0, worst_fc=1.12, drift=0.031),  # A locks; B fail
        # FC refine — suite counts ignored for Gate A (already locked)
        _suite_row(n_unacc=0, worst_fc=1.04, drift=0.99),   # still >1.0 or Δ
        _suite_row(n_unacc=0, worst_fc=0.98, drift=0.99),   # accepted; Δ vs 1.04 ~5.8%
    ]
    out = evaluate_nlrha_dual_gate(rows, tol=0.10, max_rungs=4)
    assert out["status"] == STATUS_NLRHA_COMPLETE
    assert out["lock_level"] == 1
    assert out["fc_level"] == 3
    # Locked suite keeps Gate-A lock-level drift, not FC refine junk
    assert out["locked_suite"]["mean_drift_max"] == 0.031


def test_dual_gate_walk_ladder_routes_nlrha():
    rows = [_suite_row(n_unacc=0, worst_fc=0.9)]
    out = walk_ladder("nlrha", rows, tol=0.10, max_rungs=4)
    assert out["status"] == STATUS_NLRHA_COMPLETE
    assert out["schedule_full_suite"] is False


def test_dual_gate_a_locked_no_more_rungs_pending():
    """A locks on final rung with B fail → pending (no FC refine schedule)."""
    rows = [
        _suite_row(n_unacc=0, worst_fc=1.2),
    ]
    out = evaluate_nlrha_dual_gate(rows, tol=0.10, max_rungs=1)
    assert out["status"] == STATUS_GATE_A_LOCKED_FC_PENDING
    assert out["schedule_full_suite"] is False
    assert out["schedule_fc_refine"] is False
    assert out["locked_suite"] is not None


def test_dry_run_nlrha_dual_gate_driver(tmp_path):
    """Driver dry-run: after A locks must not keep climbing full-suite for FC."""
    from mesh_convergence.driver import main
    import argparse, json
    a = argparse.Namespace(
        package=str(tmp_path / "fake_job"),
        analyses=["nlrha"],
        out=str(tmp_path / "out"),
        steltic_engine=None,
        params=None,
        tol=0.10,
        max_rungs=4,
        site_class="D",
        risk_category=None,
        n_records=11,
        parallel=1,
        dt=0.01,
        dry_run=True,
        early_abort_nc=2,
        rigid_end_offset=None,
        no_rigid_end_offset=False,
    )
    (tmp_path / "fake_job").mkdir()
    rc = main(a)
    assert rc == 0
    sc = tmp_path / "out" / "mesh_convergence_scorecard_nlrha.json"
    doc = json.loads(sc.read_text())
    assert doc["extra"]["nlrha_dual_gate"] is True
    # Should complete or be in FC refine/pending — never silently treat as NSP-style only
    assert doc["status"] in (
        STATUS_NLRHA_COMPLETE,
        STATUS_GATE_A_LOCKED_FC_REFINE,
        STATUS_GATE_A_LOCKED_FC_PENDING,
        "not_converged_within_cap",
    )
    # Full-suite must not be scheduled once A has a chance to lock in dry-run ladder
    assert doc["extra"]["schedule_full_suite"] is False
