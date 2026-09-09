"""Pure-Python tests for product rules 1–9 (no OpenSees)."""
from __future__ import annotations
import argparse, json
import pytest

from mesh_convergence.nlrha_ladder import (
    should_early_abort, count_nc, lock_fc_plasticity, plan_after_row,
    evaluate_product_nlrha, METHOD_STAGES, EARLY_ABORT_NC,
)
from mesh_convergence.stop_rule import (
    gate_a_suite, evaluate_nlrha_dual_gate, walk_ladder,
    STATUS_GATE_A_LOCKED_FC_REFINE, STATUS_NLRHA_COMPLETE, DEFAULT_TOL,
)
from mesh_convergence.rungs import DEFAULT_RUNGS, rung_knobs
from steltic_ddm.cfs_fidelity import cfs_ddm_fidelity_gate, CFS_DDM_MIN_FIDELITY


def test_early_abort_on_2_nc():
    assert EARLY_ABORT_NC == 2
    assert not should_early_abort([{"converged": True}, {"converged": False}])
    assert should_early_abort([{"converged": False}, {"converged": False}])
    assert should_early_abort([{"converged": False}, {"converged": True}, {"converged": False}])
    assert count_nc([{"converged": False}] * 3) == 3


def test_modimk_gate_a_no_fibre_for_fc():
    stage = dict(id="modimk", plasticity="imk", panel_zone="rigid")
    fc = lock_fc_plasticity(stage)
    assert fc["plasticity"] == "imk"
    assert fc["no_fibre_for_fc"] is True
    assert fc["stay_modimk_for_fc"] is True

    # PZ once lock also stays imk
    fc2 = lock_fc_plasticity(dict(id="pz_once", plasticity="imk", panel_zone="scissors"))
    assert fc2["no_fibre_for_fc"] is True

    # Fibre lock may use fibre for FC
    fc3 = lock_fc_plasticity(dict(id="fibre_M1", plasticity="fibre", panel_zone="rigid"))
    assert fc3["plasticity"] == "fibre"
    assert fc3["no_fibre_for_fc"] is False


def test_plan_after_modimk_fail_tries_pz():
    metrics = dict(n_records=11, n_unacceptable=6, n_accepted=5, worst_FC_DC=1.2)
    d = plan_after_row(dict(id="modimk", plasticity="imk", panel_zone="rigid"), metrics)
    assert not d["gate_a_passed"]
    assert d["advance_method"] is True


def test_plan_after_pz_fail_starts_fibre():
    metrics = dict(n_records=11, n_unacceptable=3, n_accepted=8, worst_FC_DC=1.2)
    d = plan_after_row(dict(id="pz_once", plasticity="imk", panel_zone="scissors"), metrics)
    assert d["start_fibre_mesh"] is True


def test_plan_gate_a_lock_schedules_fc_no_fibre():
    metrics = dict(n_records=11, n_unacceptable=0, n_accepted=11, worst_FC_DC=1.15,
                   force_controlled_ok=False)
    d = plan_after_row(dict(id="modimk", plasticity="imk", panel_zone="rigid"), metrics)
    assert d["gate_a_passed"] and d["lock_fc"]
    assert d["fc_settings"]["no_fibre_for_fc"] is True


def test_gate_a_lock_fc_only_dual_gate():
    rows = [
        dict(mean_drift_max=0.04, roof_mean_X=0.02, roof_mean_Y=0.015,
             worst_FC_DC=1.3, force_controlled_ok=False,
             n_ok=11, n_records=11, n_unacceptable=6, n_accepted=5, ACCEPTABLE=False,
             _stage=dict(id="modimk", plasticity="imk", panel_zone="rigid")),
        dict(mean_drift_max=0.031, roof_mean_X=0.02, roof_mean_Y=0.015,
             worst_FC_DC=1.12, force_controlled_ok=False,
             n_ok=11, n_records=11, n_unacceptable=0, n_accepted=11, ACCEPTABLE=True,
             _stage=dict(id="modimk", plasticity="imk", panel_zone="rigid")),
    ]
    out = evaluate_product_nlrha(rows, tol=0.10, max_rungs=4)
    assert out["status"] == STATUS_GATE_A_LOCKED_FC_REFINE
    assert out["schedule_full_suite"] is False
    assert out["schedule_fc_refine"] is True
    assert out.get("no_fibre_for_fc") is True
    assert out["fc_settings"]["plasticity"] == "imk"


def test_ten_percent_stop_nsp():
    rows = [
        dict(T1=1.0, Vy=100.0, Vpeak=150.0, delta_t=10.0),
        dict(T1=1.20, Vy=120.0, Vpeak=180.0, delta_t=12.0),
        dict(T1=1.22, Vy=122.0, Vpeak=182.0, delta_t=12.2),
    ]
    out = walk_ladder("nsp", rows, tol=0.10, max_rungs=4)
    assert out["status"] == "converged"
    assert abs(DEFAULT_TOL - 0.10) < 1e-15


def test_cfs_tier2_fidelity_gate():
    assert CFS_DDM_MIN_FIDELITY == 2
    bad = cfs_ddm_fidelity_gate({"analysis_fidelity": 1})
    assert not bad["ok"]
    forced = cfs_ddm_fidelity_gate({"analysis_fidelity": 1}, force=True)
    assert forced["ok"] and forced["forced"]
    good = cfs_ddm_fidelity_gate({"analysis_fidelity": 2})
    assert good["ok"] and good["fidelity"] == 2
    missing = cfs_ddm_fidelity_gate({})
    assert not missing["ok"]


def test_hr_ddm_fibre_mesh_knobs():
    m1 = DEFAULT_RUNGS[1]
    kn = rung_knobs(m1, "ddm")
    assert kn["nsub"] == (4, 4, 4)
    assert kn["nip"] == 5
    # GMNIAModel accepts rigid_end_offset
    import inspect
    from steltic_ddm.model_gmnia import GMNIAModel
    assert "rigid_end_offset" in inspect.signature(GMNIAModel.__init__).parameters


def test_nsp_fibre_default_knobs():
    kn = rung_knobs(DEFAULT_RUNGS[1], "nsp")
    assert kn["plasticity"] == "fibre"
    assert kn["member_nseg"] == 4
    assert kn["env"]["SNL_PLASTICITY"] == "fibre"


def test_method_stages_modimk_then_pz():
    assert METHOD_STAGES[0]["id"] == "modimk"
    assert METHOD_STAGES[0]["panel_zone"] == "rigid"
    assert METHOD_STAGES[1]["id"] == "pz_once"
    assert METHOD_STAGES[1]["panel_zone"] == "scissors"
    assert METHOD_STAGES[1]["plasticity"] == "imk"


def test_dry_run_nlrha_product_ladder(tmp_path):
    from mesh_convergence.driver import main
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
    assert doc["extra"]["nlrha_method_ladder"] is True
    assert doc["extra"]["early_abort_nc"] == 2
    assert (tmp_path / "out" / "mesh_convergence_scorecard_nlrha.md").exists()
    assert (tmp_path / "out" / "mesh_convergence_summary.md").exists()


def test_dry_run_nsp_fibre(tmp_path):
    from mesh_convergence.driver import main
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
    assert main(a) == 0
    doc = json.loads((tmp_path / "out" / "mesh_convergence_scorecard_nsp.json").read_text())
    assert doc["method"] == "fibre"
