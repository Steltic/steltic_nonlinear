"""Smoke tests for fibre mesh-convergence stop rule (pure Python, no OpenSees)."""
from mesh_convergence.stop_rule import (
    relative_delta, metrics_within_tol, evaluate_ladder_step, walk_ladder,
    evaluate_nlrha_dual_gate, gate_a_suite, gate_b_fc, fc_refine_delta,
    merge_fc_probe_with_suite, finalize_nlrha_score_status,
    DEFAULT_TOL, NSP_KEYS, NLRHA_KEYS,
    STATUS_CONTINUE, STATUS_NLRHA_COMPLETE,
    STATUS_GATE_A_LOCKED_FC_REFINE, STATUS_GATE_A_LOCKED_FC_PENDING,
    STATUS_PARTIAL_OVERRIDE,
    FC_FAIL_EXCEEDS_1, FC_FAIL_NOT_COMPUTED, FC_FAIL_REFINE_DELTA, FC_FAIL_NULL_FORBIDDEN,
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

def _suite_row(*, n_unacc, worst_fc, n_rec=11, drift=0.03, roof_x=0.02, roof_y=0.015,
               n_fc_columns=None, force_controlled_columns=None):
    n_acc = n_rec - n_unacc
    if force_controlled_columns is None and worst_fc is not None:
        force_controlled_columns = [dict(ele=1, section="W14X90", DC=worst_fc)]
    if n_fc_columns is None:
        n_fc_columns = len(force_controlled_columns or [])
    return dict(
        mean_drift_max=drift,
        roof_mean_X=roof_x,
        roof_mean_Y=roof_y,
        worst_FC_DC=worst_fc,
        force_controlled_ok=(worst_fc is not None and n_fc_columns > 0 and worst_fc <= 1.0),
        force_controlled_columns=list(force_controlled_columns or []),
        n_fc_columns=n_fc_columns,
        n_ok=n_rec,
        n_records=n_rec,
        n_unacceptable=n_unacc,
        n_accepted=n_acc,
        ACCEPTABLE=(n_unacc <= 1 and worst_fc is not None and n_fc_columns > 0 and worst_fc <= 1.0),
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
    # Absolute only (no prev refine) — need nonempty FC columns for a valid pass
    bad = gate_b_fc(dict(worst_FC_DC=1.2, n_fc_columns=1, force_controlled_columns=[{"DC": 1.2}]))
    assert not bad["passed"] and not bad["accepted"]
    assert bad["fail_reason"] == "fc_exceeds_1"

    good = gate_b_fc(dict(worst_FC_DC=1.0, n_fc_columns=1, force_controlled_columns=[{"DC": 1.0}]))
    assert good["passed"] and good["accepted"]
    assert good["fail_reason"] is None

    # Refine: accepted but Δ > 10% vs prev → not passed
    prev = dict(worst_FC_DC=1.2, n_fc_columns=1, force_controlled_columns=[{"DC": 1.2}])
    curr = dict(worst_FC_DC=0.95, n_fc_columns=1, force_controlled_columns=[{"DC": 0.95}])  # ~20.8% drop
    mid = gate_b_fc(curr, prev_fc_metrics=prev, tol=0.10)
    assert mid["accepted"] and not mid["refine_ok"] and not mid["passed"]
    assert mid["fail_reason"] == "fc_refine_delta_gt_tol"

    # Refine within 10% and accepted
    prev2 = dict(worst_FC_DC=1.05, n_fc_columns=1, force_controlled_columns=[{"DC": 1.05}])
    curr2 = dict(worst_FC_DC=0.98, n_fc_columns=1, force_controlled_columns=[{"DC": 0.98}])  # ~6.7%
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


# ---------------------------------------------------------------------------
# Gate B null / empty FC must not pass (MC4 hollow-pass regression)
# ---------------------------------------------------------------------------

def test_gate_b_null_dc_fails():
    r = gate_b_fc(dict(worst_FC_DC=None, n_fc_columns=2, force_controlled_columns=[{"DC": None}, {"ele": 1}]))
    assert r["passed"] is False and r["accepted"] is False and r["refine_ok"] is True
    assert r["fail_reason"] in (FC_FAIL_NULL_FORBIDDEN, FC_FAIL_NOT_COMPUTED)


def test_gate_b_empty_fc_columns_fails():
    r = gate_b_fc(dict(worst_FC_DC=0.9, force_controlled_ok=True, n_fc_columns=0, force_controlled_columns=[]))
    assert r["passed"] is False and r["accepted"] is False
    assert r["fail_reason"] == FC_FAIL_NOT_COMPUTED
    # Vacuous True flag must not accept
    assert r["accepted"] is False


def test_gate_b_null_to_null_refine_fails():
    prev = dict(worst_FC_DC=None, n_fc_columns=0, force_controlled_columns=[])
    curr = dict(worst_FC_DC=None, n_fc_columns=0, force_controlled_ok=True, force_controlled_columns=[])
    r = gate_b_fc(curr, prev_fc_metrics=prev, tol=0.10)
    assert r["passed"] is False
    assert r["accepted"] is False
    assert r["refine_ok"] is False
    assert r["fail_reason"] in (FC_FAIL_NULL_FORBIDDEN, FC_FAIL_NOT_COMPUTED)


def test_gate_b_numeric_dc_le_1_with_columns_can_pass():
    r = gate_b_fc(dict(
        worst_FC_DC=0.95, force_controlled_ok=True, n_fc_columns=2,
        force_controlled_columns=[{"DC": 0.9}, {"DC": 0.95}],
    ))
    assert r["passed"] is True and r["accepted"] is True and r["refine_ok"] is True
    assert r["fail_reason"] is None


def test_fc_refine_delta_null_null_fails():
    out = fc_refine_delta(dict(worst_FC_DC=None), dict(worst_FC_DC=None), tol=0.10)
    assert out["ok"] is False
    assert out["fail_reason"] == FC_FAIL_NULL_FORBIDDEN


def test_merge_fc_probe_does_not_overwrite_suite_with_null():
    suite = dict(worst_FC_DC=1.225, force_controlled_ok=False,
                 force_controlled_columns=[{"DC": 1.225}], n_fc_columns=1)
    probe = dict(worst_FC_DC=None, force_controlled_ok=True, force_controlled_columns=[], n_fc_columns=0)
    m = merge_fc_probe_with_suite(suite, probe)
    assert m["suite_worst_FC_DC"] == 1.225
    assert m["probe_worst_FC_DC"] is None
    assert m["worst_FC_DC"] is None  # probe row stays null for Gate B
    assert m["fc_not_computed"] is True
    # Gate B on merged probe must fail
    gb = gate_b_fc(m, prev_fc_metrics=suite, tol=0.10)
    assert gb["passed"] is False
    assert gb["fail_reason"] in (FC_FAIL_NOT_COMPUTED, FC_FAIL_NULL_FORBIDDEN)


def test_mc4_hollow_pass_fixture_never_nlrha_complete():
    """Tiny fixture: M0 DC=1.225, M1/M2 probe DC=None → must NOT reach nlrha_complete.

    Mimics MC4 Gate B post-processed hollow null→null pass.
    """
    m0 = _suite_row(n_unacc=0, worst_fc=1.225)  # A would pass if used; FC fails absolute
    # For dual-gate walker we need A to lock then FC refine with nulls:
    rows = [
        _suite_row(n_unacc=0, worst_fc=1.225, drift=0.031),  # A locks; B fails (DC>1)
        dict(  # M1_fc probe — empty FC columns, null DC, vacuous True
            mean_drift_max=0.031, roof_mean_X=0.02, roof_mean_Y=0.015,
            worst_FC_DC=None, force_controlled_ok=True,
            force_controlled_columns=[], n_fc_columns=0,
            n_ok=0, n_records=1, n_unacceptable=1, n_accepted=0, ACCEPTABLE=False,
        ),
        dict(  # M2_fc probe — null→null
            mean_drift_max=0.031, roof_mean_X=0.02, roof_mean_Y=0.015,
            worst_FC_DC=None, force_controlled_ok=True,
            force_controlled_columns=[], n_fc_columns=0,
            n_ok=1, n_records=1, n_unacceptable=1, n_accepted=0, ACCEPTABLE=False,
        ),
    ]
    out = evaluate_nlrha_dual_gate(rows, tol=0.10, max_rungs=4)
    assert out["status"] != STATUS_NLRHA_COMPLETE
    assert out["gate_b"]["passed"] is False
    assert out["gate_a"]["passed"] is True
    # Absolute null checks
    gb_null = gate_b_fc(rows[1])
    assert gb_null["passed"] is False
    gb_nn = gate_b_fc(rows[2], prev_fc_metrics=rows[1])
    assert gb_nn["passed"] is False
    assert gb_nn["refine_ok"] is False


def test_override_failed_gate_a_never_silent_complete():
    ga = dict(passed=False, overridden=True, n_accepted=5, n_unacceptable=6)
    gb = dict(passed=True, accepted=True, refine_ok=True, worst_FC_DC=None)  # hollow
    # Even if someone stamps B passed, honesty layer refuses complete under override+failed A
    honest = finalize_nlrha_score_status(ga, gb, michael_override=True)
    assert honest["nlrha_complete"] is False
    assert honest["status"] == STATUS_PARTIAL_OVERRIDE
    assert honest["flags"]["partial"] is True
    assert honest["flags"]["override_locked"] is True


def test_scorecard_mc4_fixture_status(tmp_path):
    """Scorecard writer must not emit nlrha_complete for MC4-like hollow override."""
    from mesh_convergence.scorecard import write_scorecard
    from mesh_convergence.rungs import DEFAULT_RUNGS
    rows = [
        _suite_row(n_unacc=6, worst_fc=1.225),
        dict(worst_FC_DC=None, force_controlled_ok=True, force_controlled_columns=[],
             n_fc_columns=0, n_records=1, n_unacceptable=1, n_accepted=0,
             mean_drift_max=None, roof_mean_X=None, roof_mean_Y=None, n_ok=0, ACCEPTABLE=False),
        dict(worst_FC_DC=None, force_controlled_ok=True, force_controlled_columns=[],
             n_fc_columns=0, n_records=1, n_unacceptable=1, n_accepted=0,
             mean_drift_max=None, roof_mean_X=None, roof_mean_Y=None, n_ok=1, ACCEPTABLE=False),
    ]
    # Fake a "complete" ladder that would have been the hollow pass — honesty must downgrade.
    ladder = dict(
        status=STATUS_NLRHA_COMPLETE,
        tol=0.10, max_rungs=4, stop_level=0, steps=[],
        gate_a=dict(passed=False, overridden=True, n_accepted=5, n_unacceptable=6),
        gate_b=dict(passed=True, accepted=True, refine_ok=True, worst_FC_DC=None,
                    fail_reason=None),
        locked_suite=dict(n_accepted=5, worst_FC_DC=1.225),
    )
    path = write_scorecard(
        str(tmp_path), case="mc4-fixture", analysis="nlrha",
        rungs=DEFAULT_RUNGS[:3], ladder=ladder, metric_rows=rows,
        extra=dict(michael_override=True, suite_worst_FC_DC=1.225, probe_worst_FC_DC=None),
    )
    import json
    from pathlib import Path as _Path
    doc = json.loads(_Path(path).read_text())
    assert doc["status"] != STATUS_NLRHA_COMPLETE
    assert doc["status"] == STATUS_PARTIAL_OVERRIDE
    assert doc["extra"]["nlrha_complete"] is False
    assert doc["extra"]["suite_worst_FC_DC"] == 1.225
    assert doc["extra"]["probe_worst_FC_DC"] is None


def test_mc4_fixture_json_loaded():
    import json
    from pathlib import Path
    fix = json.loads((Path(__file__).parent / "fixtures" / "mc4_hollow_gate_b_null_fc.json").read_text())
    out = evaluate_nlrha_dual_gate(fix["metric_rows"], tol=0.10, max_rungs=4)
    assert out["status"] != fix["expect"]["status_ne"]
    assert out["status"] != STATUS_NLRHA_COMPLETE
    assert out["gate_b"]["passed"] is False


def test_early_abort_partial_metrics_keep_fc_fields():
    """2 NC early abort still exposes FC fields needed later for lock/refine."""
    from mesh_convergence.metrics import from_nlrha
    # Synthetic package mimicking early-abort write with partial FC columns
    import json, tempfile, os
    with tempfile.TemporaryDirectory() as td:
        pkg = {
            "acceptance": {
                "verdict": {
                    "n_records": 2, "n_unacceptable": 0, "force_controlled_ok": False,
                    "mean_drift_max": 0.04, "overall": False,
                },
                "force_controlled_columns": [{"ele": 10, "DC": 1.1}],
                "meta": {"early_aborted": True, "early_abort_nc": 2, "n_nc": 2},
                "story": [{"mean_X": 0.02, "mean_Y": 0.01}],
            },
            "results": [{"converged": False}, {"converged": False}],
        }
        open(os.path.join(td, "nlrha_package.json"), "w").write(json.dumps(pkg))
        row = from_nlrha(td)
        assert row["early_aborted"] is True
        assert row["n_fc_columns"] == 1
        assert row["worst_FC_DC"] == 1.1
        assert row["force_controlled_columns"]


def test_modimk_gate_a_fc_stays_imk_after_null_rules():
    from mesh_convergence.nlrha_ladder import lock_fc_plasticity, plan_after_row
    stage = dict(id="modimk", plasticity="imk", panel_zone="rigid")
    metrics = dict(n_records=11, n_unacceptable=0, n_accepted=11, worst_FC_DC=1.15,
                   force_controlled_ok=False, n_fc_columns=1,
                   force_controlled_columns=[{"DC": 1.15}])
    d = plan_after_row(stage, metrics)
    assert d["gate_a_passed"] and d["fc_settings"]["no_fibre_for_fc"] is True
    assert lock_fc_plasticity(stage)["plasticity"] == "imk"
