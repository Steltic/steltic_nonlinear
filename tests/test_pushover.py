"""Smoke test on the packaged example (Ex22_SMF): parse the package, build the hinge model, push X a few steps.
Run:  python -m pytest tests -q   (needs openseespy)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
EX = os.path.join(os.path.dirname(HERE), "examples", "Ex22_SMF")


def test_package_reader():
    from pushover import package_reader as PR
    pkg = PR.load(EX)
    assert len(pkg.model.nodes) == 251 and len(pkg.model.elements) == 558 and len(pkg.model.diaphragms) == 6
    assert pkg.basis.SDS == 1.0 and pkg.basis.R == 8.0 and pkg.basis.W_kip and pkg.basis.V_design_kip


def test_hinge_backbone():
    from pushover import hinge_models as HM
    prm = HM.load_params()
    h = HM.beam_hinge("W33X130", 360.0, prm)
    assert 0.005 < h.theta_y < 0.01 and h.a_pl > h.theta_y and h.b_pl > h.a_pl and 0 < h.c_res < 1
    c = HM.column_hinge("W14X311", 162.0, 300.0, prm)
    assert not c.force_controlled and c.a_pl > 0 and c.Mpe_kipin <= 603 * 55 + 1e-6


def test_short_push():
    from pushover import package_reader as PR, nonlinear_model as NM, hinge_models as HM
    pkg = PR.load(EX); prm = HM.load_params()
    loads, table = NM.gravity_loads(pkg, prm)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False)
    assert len(hinges) == 660 and stats["col"] == 210 and stats["beam"] == 348 and stats["released_ends"] == 456  # Ex22: pinned gravity framing
    run = NM.pushover(pkg, hinges, "X", loads, prm, max_roof_drift=0.004, verbose=False, gravity_table=table)
    assert run["rec"]["V"][-1] > 1000 and 0.8 < run["pattern"]["T1"] < 1.1
