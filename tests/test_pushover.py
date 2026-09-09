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
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="imk")
    assert len(hinges) == 660 and stats["col"] == 210 and stats["beam"] == 348 and stats["released_ends"] == 456  # Ex22: pinned gravity framing
    run = NM.pushover(pkg, hinges, "X", loads, prm, max_roof_drift=0.004, verbose=False, gravity_table=table)
    assert run["rec"]["V"][-1] > 1000 and 0.8 < run["pattern"]["T1"] < 1.1


def test_panel_zones_default_rigid():
    """Default hinge_params leave panel zones rigid (no scissors springs)."""
    from pushover import package_reader as PR, nonlinear_model as NM, hinge_models as HM
    pkg = PR.load(EX); prm = HM.load_params()
    assert HM.panel_zone_mode(prm) == "rigid"
    loads, _ = NM.gravity_loads(pkg, prm)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="imk")
    assert stats.get("panel_zone_mode", "rigid") == "rigid"
    assert stats.get("panel_zones", 0) == 0
    assert len(hinges) == 660


def test_panel_zones_scissors_builds():
    """Opt-in scissors: FR joints get zeroLength PZ springs; hinge count unchanged."""
    from pushover import package_reader as PR, nonlinear_model as NM, hinge_models as HM
    import copy
    pkg = PR.load(EX)
    prm = copy.deepcopy(HM.load_params())
    prm.setdefault("panel_zones", {})
    prm["panel_zones"]["mode"] = "scissors"
    prm["panel_zones"]["material"] = "elastic"
    plan = NM.fr_joint_plan(pkg)
    assert len(plan) > 0
    loads, table = NM.gravity_loads(pkg, prm)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="imk")
    assert stats["panel_zone_mode"] == "scissors"
    assert stats["panel_zones"] == len(plan)
    assert len(hinges) == 660  # IMK/brace registry unchanged; PZ kept in stats registry
    assert len(stats["panel_zone_registry"]) == stats["panel_zones"]
    sample = next(iter(stats["panel_zone_registry"].values()))
    assert sample["specs"][0]["K_theta"] > 0
    assert sample["kind"] == "panel_zone"


def test_panel_zones_scissors_push_plausible():
    """Scissors short push: T1 near rigid twin (slightly softer); V same order of magnitude.

    Root cause fixed 2026-09-08: equalDOF to a rigidDiaphragm slave under Transformation produced
    spurious short T1 and collapsed base shear. Scissors now uses a 6-DOF zeroLength (rigid on
    non-PZ DOFs) instead of equalDOF. Rigid default path is unchanged.
    """
    from pushover import package_reader as PR, nonlinear_model as NM, hinge_models as HM
    import copy
    pkg = PR.load(EX)
    prm_r = HM.load_params()
    loads, table = NM.gravity_loads(pkg, prm_r, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    h_r, _ = NM.build_nonlinear(pkg, prm_r, PG, verbose=False, plasticity="imk")
    run_r = NM.pushover(pkg, h_r, "X", loads, prm_r, max_roof_drift=0.004, verbose=False,
                        gravity_table=table, tail_strategies=())
    prm_s = copy.deepcopy(prm_r)
    prm_s.setdefault("panel_zones", {})
    prm_s["panel_zones"]["mode"] = "scissors"
    prm_s["panel_zones"]["material"] = "elastic"
    h_s, stats = NM.build_nonlinear(pkg, prm_s, PG, verbose=False, plasticity="imk")
    run_s = NM.pushover(pkg, h_s, "X", loads, prm_s, max_roof_drift=0.004, verbose=False,
                        gravity_table=table, tail_strategies=())
    assert stats["panel_zones"] > 0
    T_r, T_s = run_r["pattern"]["T1"], run_s["pattern"]["T1"]
    V_r, V_s = run_r["rec"]["V"][-1], run_s["rec"]["V"][-1]
    # Slightly softer than rigid (panel flexibility); not a spurious short mode
    assert 0.95 * T_r <= T_s <= 1.40 * T_r, (T_r, T_s)
    assert run_s["pattern"]["meff_frac"] > 0.70
    # Same order of magnitude at 0.4% roof drift (elastic PZ flexibility lowers V some)
    assert V_s > 0.40 * V_r, (V_r, V_s)
    assert V_s > 1000.0
    # Infinite-K scissors must recover rigid twin (topology check)
    prm_k = copy.deepcopy(prm_s)
    prm_k["panel_zones"]["K_theta"] = 1.0e14
    h_k, _ = NM.build_nonlinear(pkg, prm_k, PG, verbose=False)
    run_k = NM.pushover(pkg, h_k, "X", loads, prm_k, max_roof_drift=0.004, verbose=False,
                        gravity_table=table, tail_strategies=())
    assert abs(run_k["pattern"]["T1"] - T_r) / T_r < 0.01
    assert abs(run_k["rec"]["V"][-1] - V_r) / V_r < 0.01


def test_panel_zone_hysteretic_spec():
    """Hysteretic Gupta–Krawinkler envelope is well-ordered (import-level; no OpenSees required beyond material create)."""
    from pushover import hinge_models as HM
    prm = HM.load_params()
    prm = {**prm, "panel_zones": {**(prm.get("panel_zones") or {}), "mode": "scissors", "material": "hysteretic"}}
    spec = HM.panel_zone_spec(1, 5, "W14X311", ["W33X130", "W33X130"], prm)
    assert spec.material == "hysteretic"
    assert 0 < spec.theta_y < spec.theta_p < spec.theta_r
    assert 0 < spec.My <= spec.Mp
