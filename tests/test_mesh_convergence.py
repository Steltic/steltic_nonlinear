"""Smoke tests for fibre mesh-convergence stop rule (pure Python, no OpenSees)."""
from mesh_convergence.stop_rule import (
    relative_delta, metrics_within_tol, evaluate_ladder_step, walk_ladder,
    DEFAULT_TOL, NSP_KEYS, NLRHA_KEYS,
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
    ok = dict(T1=1.05, Vy=102.0, Vpeak=148.0, delta_t=10.5)  # all <= 5% actually, within 10%
    bad = dict(T1=1.15, Vy=102.0, Vpeak=148.0, delta_t=10.5)  # T1 15%
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
        dict(T1=1.20, Vy=120.0, Vpeak=180.0, delta_t=12.0),  # 20% — continue
        dict(T1=1.22, Vy=122.0, Vpeak=182.0, delta_t=12.2),  # ~1.7% — converge
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
    import argparse
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
    )
    (tmp_path / "fake_job").mkdir()
    rc = main(a)
    assert rc == 0
    sc = tmp_path / "out" / "mesh_convergence_scorecard_nsp.json"
    assert sc.exists()
    import json
    doc = json.loads(sc.read_text())
    assert doc["tol"] == 0.10
    assert doc["method"] == "fibre"
    assert doc["status"] in ("converged", "not_converged_within_cap", "continue")
