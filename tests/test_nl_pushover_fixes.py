"""Regression tests for the pushover fixes NL-01, NL-07, NL-09, NL-11, NL-16, NL-22, NL-28.

Small hand-built models (a one-bay portal) and synthetic run records keep these fast; the Ex22 example is
used only for a short push."""
import json, math, os, shutil, sys, tempfile
from pathlib import Path
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")


# --------------------------------------------------------------------------- a one-bay, one-storey SMF portal
def _portal(calc=None, beam="W24X76", col="W14X132", span=300.0, h=168.0, pinned_beam=False, base="fixed"):
    from pushover import package_reader as PR
    from pushover import sections_db as SDB
    m = PR.ElasticModel()
    m.nodes = {1: (0.0, 0.0, 0.0), 2: (span, 0.0, 0.0), 3: (0.0, 0.0, h), 4: (span, 0.0, h), 5: (span / 2, 0.0, h)}
    bf = [1] * 6 if base == "fixed" else [1, 1, 1, 1, 0, 1]          # "pinned": in-plane rotation (RY) free
    m.fixes = {1: list(bf), 2: list(bf), 5: [0, 0, 1, 1, 1, 0]}
    mass = 1.5
    m.masses = {5: [mass, mass, 0.0, 0.0, 0.0, mass * span ** 2 / 12.0]}
    m.transfs = {1: ("PDelta", 0.0, 1.0, 0.0), 2: ("Linear", 0.0, 0.0, 1.0)}
    pc, pb = SDB.props(col), SDB.props(beam)
    E, G = 29000.0, 11200.0
    m.elements = [
        dict(tag=1, n1=1, n2=3, A=pc["A"], E=E, G=G, J=pc.get("J", 5.0), Iy=pc["Iy"], Iz=pc["Ix"], transf=1, release=None),
        dict(tag=2, n1=2, n2=4, A=pc["A"], E=E, G=G, J=pc.get("J", 5.0), Iy=pc["Iy"], Iz=pc["Ix"], transf=1, release=None),
        dict(tag=3, n1=3, n2=4, A=pb["A"], E=E, G=G, J=pb.get("J", 5.0), Iy=pb["Ix"], Iz=pb["Iy"], transf=2,
             release=(["-releasey", 3] if pinned_beam else None)),
    ]
    m.diaphragms = [(3, 5, [3, 4])]
    sched = {1: dict(member="col", section=col), 2: dict(member="col", section=col), 3: dict(member="beam", section=beam)}
    b = PR.DesignBasis(SDS=1.0, SD1=0.6, R=8.0, Cd=5.5, Om0=3.0, Ie=1.0, system="SMF", W_kip=mass * 386.4,
                       V_design_kip=0.125 * mass * 386.4, T_design_s=0.5)
    return PR.Package(root=Path(tempfile.mkdtemp()), name="portal", model=m, schedule=sched, calc=calc or {}, basis=b, files={})


def _fr_params():
    from pushover import hinge_models as HM
    prm = json.loads(json.dumps(HM.load_params(os.path.join(EX22, "hinge_params_ex22_aisc342.json"))))
    prm["beam_flexure"].pop("modifiers", None)
    return prm


RBS_CALC = {"connections": [{"id": "conn-SMF-RBS-W24X76", "type": "SMF beam-to-column: AISC 358 Ch.5 Reduced Beam Section",
                             "section": "W24X76", "components": "RBS a=5.50 in, b=18.00 in, c=1.800 in; CJP flanges"}],
            "members": [{"id": "smf-beam", "inputs": {"kind": "beam", "section": "W24X76", "Lb_in": 96.0}}],
            "capacity_design": {"beam_bracing": {"Lb_provided_in": 90.0}}}


# --------------------------------------------------------------------------- NL-01: fibre monitors
def test_fibre_end_rotation_matches_section_curvature_integral():
    """theta_p of a fibre end region == sum (kappa - M/EI) w L over the integration points (independent check)."""
    import openseespy.opensees as ops
    from pushover import nonlinear_model as NM
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    L = 100.0
    ops.node(1, 0, 0, 0); ops.node(2, L, 0, 0); ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.geomTransf("Linear", 1, 0, 0, 1)
    ops.uniaxialMaterial("Steel01", 1, 50.0, 29000.0, 0.01)
    ops.section("Fiber", 1, "-GJ", 1e6); ops.patch("rect", 1, 2, 40, -1.0, -5.0, 1.0, 5.0)   # 2 x 10, depth along local z
    ops.beamIntegration("Lobatto", 1, 1, 5)
    ops.element("forceBeamColumn", 1, 1, 2, 1, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 0, 0, 1, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack"); ops.test("NormDispIncr", 1e-10, 50); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 2, 3, 0.05); ops.analysis("Static")
    h = dict(kind="beam", form="fibre_end", segs=[1], comp=(3, 4), mode="integral")
    ops.analyze(4)                                                      # elastic: My = 50*2*10^2/6 = 833 kip-in, M = 4*0.05*... small
    assert abs(NM.fibre_end_rotation(h)) < 1e-9
    for _ in range(56):
        assert ops.analyze(1) == 0
    EI = 29000.0 * 2.0 * 10.0 ** 3 / 12.0
    xs = ops.eleResponse(1, "integrationWeights")
    ref = 0.0
    for ip in range(1, 6):
        k = ops.eleResponse(1, "section", ip, "deformation")[2]; M = ops.eleResponse(1, "section", ip, "force")[2]
        ref += (k - M / EI) * xs[ip - 1]
    th = NM.fibre_end_rotation(h)
    assert abs(th) > 5e-3                                              # well into the plastic range
    assert abs(abs(th) - abs(ref)) < 1e-2 * abs(ref), (th, ref)          # fibre-discretised vs closed-form EI
    ops.wipe()


def test_fibre_registers_beam_and_column_hinges_and_acceptance_evaluates():
    from pushover import nonlinear_model as NM, postprocess as PP
    pkg = _portal(); prm = _fr_params()
    loads, table = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="fibre", member_nseg=4)
    kinds = sorted(h["kind"] for h in hinges.values())
    assert kinds == ["beam", "beam", "col", "col", "col", "col"]
    assert all(h["form"] == "fibre_end" for h in hinges.values())
    assert stats["monitored_beam_ends"] == 2 and stats["monitored_col_ends"] == 4
    run = NM.pushover(pkg, hinges, "X", loads, prm, max_roof_drift=0.04, verbose=False, gravity_table=table, tail_strategies=())
    assert run["n_moment_frame_members"] == 1 and run["monitored"] == {"beam": 2, "col": 4}
    u_end = run["rec"]["u"][-1]
    acc = PP.acceptance(run, hinges, 0.9 * u_end, "BSE-2N")
    assert acc["status"] == PP.EVALUATED and acc["evaluated"]
    assert acc["monitored"]["beam"] == 2 and acc["monitored"]["col"] == 4
    assert acc["worst_DC"]["CP"] is not None and acc["worst_DC"]["CP"] > 0.0           # 4% drift: beams have yielded
    beams = [g for g in acc["groups"] if g["kind"] == "beam"]
    assert beams and beams[0]["n_yielded"] >= 1 and beams[0]["theta_pl_max"] > 0.005
    # the P-695 component limit can now fire on fibre beams / columns (grav list is no longer empty)
    assert max(run["rec"]["b_ratio"]) > 0.0
    # NL-R2-12: the eigen solve carries the modes for the 7.3.2.1 higher-mode test (1 storey: ratio 1.0)
    pat = run["pattern"]
    assert pat["modes"] and pat["modes_cum_frac"] >= 0.9 and any(m["mode"] == pat["mode"] for m in pat["modes"])
    hm = PP.higher_mode_check(pat, 1.0, 0.6)
    assert hm["status"] == "not_significant" and abs(hm["max_ratio"] - 1.0) < 1e-6


def test_ex22_example_fibre_monitors_every_fr_end():
    from pushover import package_reader as PR, nonlinear_model as NM, hinge_models as HM, postprocess as PP
    pkg = PR.load(EX22); prm = HM.load_params()
    loads, table = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="fibre", member_nseg=4)
    # same hinge census as the IMK path (660 = 2*558 member ends - 456 released ends)
    assert stats["monitored_ends"] == 660 and stats["monitored_beam_ends"] == 240 and stats["monitored_col_ends"] == 420
    run = NM.pushover(pkg, hinges, "X", loads, prm, max_roof_drift=0.003, verbose=False, gravity_table=table, tail_strategies=())
    acc = PP.acceptance(run, hinges, run["rec"]["u"][-1], "BSE-1N")
    assert acc["status"] == PP.EVALUATED and acc["monitored"]["beam"] == 240 and acc["monitored"]["col"] == 420
    assert run["analysis_objects"]["integrator"] <= 2                                       # NL-11: not one per step


# --------------------------------------------------------------------------- NL-01 / NL-09: acceptance verdicts
class _S:
    def __init__(self, **k): self.__dict__.update(k)


def _spec(kind):
    if kind == "brace":
        return _S(IO=1.0, LS=2.0, CP=3.0, IO_t=1.0, LS_t=2.0, CP_t=3.0, dc=0.5, dT=0.5, theta_y=0.5, a_pl=1.0, b_pl=3.0)
    return _S(IO=0.01, LS=0.03, CP=0.04, theta_y=0.008, a_pl=0.02, b_pl=0.04)


def _run(tags_kinds, u, pl_last, n_mf=1):
    n = len(u)
    hz = [t for t, _ in tags_kinds]
    pl = [[0.0] * len(hz) for _ in range(n - 1)] + [pl_last]
    return dict(rec=dict(u=list(u), V=list(np.linspace(0, 100, n)), story_u=[[x] for x in u], hinge_pl=pl, col_N=[]),
                hinge_tags=hz, heights=[120.0], n_moment_frame_members=n_mf), \
        {t: dict(kind=k, section="W", z=120.0, spec=_spec(k)) for t, k in tags_kinds}


def test_acceptance_moment_frame_without_beam_column_monitors_is_not_evaluated():
    from pushover import postprocess as PP
    run, hinges = _run([(1, "brace")], [0, 1, 2, 3], [0.1])
    a = PP.acceptance(run, hinges, 2.0, "BSE-1N")
    assert a["status"] == PP.NOT_EVALUATED and a["worst_DC"] == dict(IO=None, LS=None, CP=None)
    assert PP.level_verdict(a, "LS") is None
    run, hinges = _run([], [0, 1, 2, 3], [], n_mf=0)
    a = PP.acceptance(run, hinges, 2.0, "BSE-1N")
    assert a["status"] == PP.NOT_EVALUATED and PP.level_verdict(a, "LS") is None


def test_acceptance_at_first_step_reaching_target_and_target_not_reached():
    from pushover import postprocess as PP
    run, hinges = _run([(1, "beam"), (2, "col")], [0.0, 1.0, 2.0, 3.0], [0.02, 0.005])
    run["rec"]["hinge_pl"][2] = [0.012, 0.0]
    a = PP.acceptance(run, hinges, 1.5, "BSE-1N")                  # first u >= 1.5 is step 2
    assert a["step"] == 2 and a["roof_disp_in"] == 2.0 and abs(a["worst_DC"]["IO"] - 1.2) < 1e-12
    assert PP.level_verdict(a, "IO") is False and PP.level_verdict(a, "LS") is True
    run["rec"]["V"] = [0.0, 100.0, 90.0, 70.0]                    # NL-R2-24: strength loss (V <= 0.8 Vmax) before delta_t
    b = PP.acceptance(run, hinges, 3.5, "BSE-2N")                  # never reached
    assert b["status"] == PP.TARGET_NOT_REACHED and b["acceptable"] is False and b["shortfall"]["kind"] == PP.SHORT_STRENGTH
    assert b["worst_DC"]["CP"] is None and b["max_story_drift"] is None and b["roof_disp_in"] is None
    assert b["at_last_converged"]["roof_disp_in"] == 3.0          # diagnostic only, labelled
    assert PP.level_verdict(b, "CP") is False
    assert PP.step_at(run, 3.5) is None and PP.step_at(run, 0.0) == 0


def test_numerical_stop_at_peak_before_target_is_not_evaluated_not_unacceptable():
    """NL-R2-24 (Ex8 EBF: solver stop at 12.18 in, V/Vmax = 1.000, delta_t 12.98 in): NOT EVALUATED, never NOT ACCEPTABLE.
    NOT ACCEPTABLE stays for a genuine strength loss (V <= 0.8 Vmax) or rotation b before the target."""
    from pushover import postprocess as PP
    run, hinges = _run([(1, "beam"), (2, "col")], [0.0, 4.0, 8.0, 12.18], [0.02, 0.005])
    run["rec"]["V"] = [0.0, 900.0, 990.0, 1000.0]                 # still rising / at peak when the solver gave up
    run["stop_reason"] = "solver non-convergence at roof u=12.18 in (after 9 step halvings)"
    run["tail"] = dict(status="lower_bound", tried=[dict(strategy="fine_step"), dict(strategy="arclength")])
    a = PP.acceptance(run, hinges, 12.98, "BSE-2N")
    assert a["status"] == PP.NOT_EVALUATED and a["acceptable"] is None and a["reason"] == "stopped_before_target"
    assert a["shortfall"]["kind"] == PP.SHORT_NUMERICAL and a["shortfall"]["V_end_over_Vmax"] == 1.0
    assert "NOT EVALUATED -- analysis stopped numerically at V/Vmax = 1.000 before the target displacement" in a["note"]
    assert "NOT ACCEPTABLE" not in a["note"] and a["worst_DC"]["CP"] is None and a["max_story_drift"] is None
    assert PP.level_verdict(a, "CP") is None                       # not a pass, not a fail
    run["rec"]["V"] = [0.0, 900.0, 1000.0, 850.0]                  # descending but still above 0.8 Vmax: numerical
    assert PP.target_shortfall(run, 12.98)["kind"] == PP.SHORT_NUMERICAL
    run["tail"] = dict(status="component_limit", u_component_limit=8.0)   # rotation b before delta_t: a mechanism
    b = PP.acceptance(run, hinges, 12.98, "BSE-2N")
    assert b["status"] == PP.TARGET_NOT_REACHED and b["acceptable"] is False and PP.level_verdict(b, "CP") is False
    run["tail"] = dict(status="max_drift"); run["stop_reason"] = "reached max roof drift 8.0% of H"
    assert PP.target_shortfall(run, 12.98)["kind"] == PP.SHORT_DRIFT_CAP
    assert PP.acceptance(run, hinges, 12.98, "BSE-2N")["status"] == PP.NOT_EVALUATED
    assert PP.target_shortfall(run, 12.0) is None
    from pushover import report_supplement as RS
    txt = RS._reached_txt(dict(reached_target=False, target_shortfall=PP.target_shortfall(run, 12.98)))
    assert "NOT EVALUATED" in txt and "NOT ACCEPTABLE" not in txt


def _example_job():
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "*.html", "__pycache__"))
    shutil.copy(os.path.join(EX22, "report.html"), job)
    return job


@pytest.mark.parametrize("mode", ["not_evaluated", "legacy_empty", "target_not_reached", "evaluated", "stopped_numerically", "partial"])
def test_compare_bpon_never_turns_missing_into_pass(mode):
    from snl import compare
    job = _example_job()
    pj = os.path.join(job, "pushover", "pushover_package.json")
    po = json.load(open(pj))
    for d in po["directions"].values():
        for lvl, a in d["acceptance"].items():
            if mode == "not_evaluated":
                a.update(status="not_evaluated", groups=[], worst_DC=dict(IO=None, LS=None, CP=None))
            elif mode == "legacy_empty":                              # pre-fix package: D/C 0.00, no groups
                a.update(groups=[], worst_DC=dict(IO=0.0, LS=0.0, CP=0.0)); a.pop("status", None)
            elif mode == "target_not_reached" and lvl == "BSE-2N":
                a.update(status="target_not_reached", groups=[], worst_DC=dict(IO=None, LS=None, CP=None),
                         max_story_drift=None, story_drifts=[], census=[], roof_disp_in=None)
            elif mode == "stopped_numerically" and lvl == "BSE-2N":      # NL-R2-24
                a.update(status="not_evaluated", reason="stopped_before_target", shortfall=dict(kind="numerical", V_end_over_Vmax=1.0),
                         groups=[], worst_DC=dict(IO=None, LS=None, CP=None), max_story_drift=None, story_drifts=[], census=[], roof_disp_in=None)
            elif mode in ("evaluated", "partial"):
                a.update(worst_DC=dict(IO=0.5, LS=0.2, CP=0.1))
    if mode == "partial":                                          # NL-R2-14: Y still running
        po["directions_pending"] = ["Y"]; po["complete"] = False
    json.dump(po, open(pj, "w"))
    compare.build(job)
    s = json.load(open(os.path.join(job, "snl_summary.json")))["pushover"]
    t = open(os.path.join(job, "four_analyses.html"), encoding="utf-8").read()
    if mode in ("not_evaluated", "legacy_empty"):
        assert s["bpon_ok"] is None and "NOT EVALUATED" in t and "both pass" not in t
    elif mode == "target_not_reached":
        assert s["bpon_ok"] is False and "target displacement not reached" in t
    elif mode == "partial":
        assert s["bpon_ok"] is None and s["directions_pending"] == ["Y"] and "PARTIAL pushover" in t and "both pass" not in t
    elif mode == "stopped_numerically":
        assert s["bpon_ok"] is None and s["bpon_stopped_before_target"] and "stopped numerically" in t
        assert "target displacement not reached" not in t and "both pass" not in t
    else:
        assert s["bpon_ok"] is True and "both pass" in t
        # NL-R2-12: the example package predates the 7.3.2.1 higher-mode test -> never "NSP permitted"
        assert s["nsp_status"] == "not_evaluated" and not s["nsp_permitted"] and "applicability NOT EVALUATED" in t


# --------------------------------------------------------------------------- NL-16: idealisation and coefficients
def _curve(Ke=100.0, Vy=1000.0, a1=0.05, a2=None, u_peak=None, n=400, umax=60.0):
    u = np.linspace(0, umax, n)
    uy = Vy / Ke
    V = np.where(u <= uy, Ke * u, Vy + a1 * Ke * (u - uy))
    if a2 is not None:
        Vp = Vy + a1 * Ke * (u_peak - uy)
        V = np.where(u <= u_peak, V, Vp + a2 * Ke * (u - u_peak))
    return u, np.maximum(V, 1.0)


def test_idealize_recovers_bilinear_and_uses_least_of_target_and_peak():
    from pushover import postprocess as PP
    u, V = _curve()
    ide = PP.idealize(u, V, 30.0)
    assert abs(ide["Ke"] - 100.0) < 0.5 and abs(ide["Vy"] - 1000.0) < 5.0 and abs(ide["alpha1"] - 0.05) < 0.003
    assert abs(ide["ud"] - 30.0) < 1e-9
    # degrading curve: peak at 25 in; a target of 40 in must idealise to Delta_d = u at Vmax, not 40
    u, V = _curve(a2=-0.1, u_peak=25.0)
    ide = PP.idealize(u, V, 40.0)
    assert abs(ide["ud"] - u[int(np.argmax(V))]) < 1e-9 and ide["Vy"] <= V.max() + 1e-9


def test_nsp_coefficients_asce41_23():
    from pushover import postprocess as PP, package_reader as PR
    import copy
    u, V = _curve(Ke=200.0, Vy=500.0, a1=0.02, umax=40.0)
    phi = {1: 0.5, 2: 1.0}; mk = {1: 1.0, 2: 1.0}
    run = dict(rec=dict(u=list(u), V=list(V), story_u=[[x * 0.5, x] for x in u]), pattern=dict(T1=0.15, phi=phi, masses=mk),
               H=240.0, heights=[120.0, 120.0], gravity_table_QG=[100.0, 100.0])
    from pushover import hinge_models as HM
    prm = copy.deepcopy(HM.load_params())
    b = PR.DesignBasis(SDS=1.0, SD1=0.45, W_kip=1000.0, system="SMF")
    n = PP.nsp_target(run, b, prm, 1.0)
    Te = n["Te"]; mu = n["mu_strength"]
    # C1 Eq. (7-30) with Te < 0.2 s -> evaluated at 0.2 s; C2 Eq. (7-31) with the actual Te
    assert Te < 0.2
    assert abs(n["C1"] - (1 + (mu - 1) / (60 * 0.2 ** 2))) < 1e-9
    assert abs(n["C2"] - (1 + ((mu - 1) / Te) ** 2 / 800.0)) < 1e-9
    assert n["Cm"] == 1.0                                         # 2 storeys -> Table 7-4 1.0
    # lambda on S_X1 for BSE-2N: 0.45 * 1.5 = 0.675 >= 0.6 -> 0.8 even at BSE-1N
    assert n["lambda_nf"] == 0.8 and abs(n["SX1_BSE2N"] - 0.675) < 1e-12
    assert n["Vy"] <= max(V) + 1e-9
    # 3 storeys: Cm 0.9 for SMF, 1.0 for an unrecognised / BRBF system, 1.0 when T1 > 1 s
    assert PP._cm(b, prm, 3, 0.8)[0] == 0.9
    assert PP._cm(PR.DesignBasis(system="BRBF"), prm, 3, 0.8)[0] == 1.0
    assert PP._cm(b, prm, 3, 1.2)[0] == 1.0


# --------------------------------------------------------------------------- NL-R2-12: 7.3.2.1 higher-mode test
def _shear_building(n, T1, m=1.0):
    """Uniform n-storey shear building (level masses m, equal storey stiffness) scaled to period T1; returns the
    pattern dict modal_pattern would produce (modes in ascending-frequency order, phi in the push direction)."""
    K = np.zeros((n, n)); k = 1.0
    for i in range(n):
        K[i, i] += k
        if i + 1 < n:
            K[i, i] += k; K[i, i + 1] -= k; K[i + 1, i] -= k
    w2, V = np.linalg.eigh(K / m)
    w2 = w2 * (2 * math.pi / T1) ** 2 / w2[0]
    modes = []
    for j in range(n):
        phi = {i + 1: float(V[i, j]) for i in range(n)}
        Ln = sum(m * phi[i] for i in phi); Mn = sum(m * phi[i] ** 2 for i in phi)
        modes.append(dict(mode=j + 1, T=2 * math.pi / math.sqrt(w2[j]), gamma=Ln / Mn, meff_frac=Ln ** 2 / Mn / (n * m), phi=phi))
    return dict(T1=T1, mode=1, modes=modes, masses={i + 1: m for i in range(n)}, phi=modes[0]["phi"])


def test_higher_mode_check_asce41_7321():
    from pushover import postprocess as PP
    # closed form, 4 storeys (mode 1 = 88 % < 90 % -> modes 1+2): CQC story shears from the modal level forces,
    # recomputed here independently of the module
    pat = _shear_building(4, 0.6)
    SXS, SX1 = 1.0, 0.6
    hm = PP.higher_mode_check(pat, SXS, SX1)
    assert pat["modes"][0]["meff_frac"] < 0.9 <= pat["modes"][0]["meff_frac"] + pat["modes"][1]["meff_frac"]
    assert hm["n_modes_used"] == 2 and hm["cum_mass_frac"] >= 0.9
    vs = []
    for md in pat["modes"][:2]:
        sa = PP.spectrum_sa(md["T"], SXS, SX1) * 386.4
        F = [md["gamma"] * md["phi"][k] * sa for k in (1, 2, 3, 4)]
        vs.append([sum(F[i:]) for i in range(4)])
    T1, T2 = pat["modes"][0]["T"], pat["modes"][1]["T"]
    r = T2 / T1; xi = 0.05
    rho = 8 * xi ** 2 * (1 + r) * r ** 1.5 / ((1 - r * r) ** 2 + 4 * xi ** 2 * r * (1 + r) ** 2)
    for i in range(4):
        exp = math.sqrt(vs[0][i] ** 2 + vs[1][i] ** 2 + 2 * rho * vs[0][i] * vs[1][i]) / abs(vs[0][i])
        assert hm["ratios"][i]["ratio"] == pytest.approx(exp, rel=1e-9)
        assert hm["ratios"][i]["V_mode1_kip"] == pytest.approx(abs(vs[0][i]), rel=1e-9)
    assert hm["status"] == "not_significant" and hm["max_ratio"] <= 1.30                  # stiff 4-storey: first mode governs
    hm1 = PP.higher_mode_check(_shear_building(2, 0.5), SXS, SX1)                         # mode 1 alone >= 90 %: ratio 1
    assert hm1["n_modes_used"] == 1 and hm1["max_ratio"] == pytest.approx(1.0) and hm1["status"] == "not_significant"
    # 20 storeys, T1 = 3.5 s (Ex29-like): the 2nd and 3rd modes sit on a higher spectral ordinate -> significant
    hm = PP.higher_mode_check(_shear_building(20, 3.5), 1.0, 0.6)
    assert hm["status"] == "significant" and hm["max_ratio"] > 1.30 and hm["story_max"] > 10 and hm["cum_mass_frac"] >= 0.9
    assert PP.nsp_status(True, hm) == "permitted_with_LDP" and PP.nsp_status(False, hm) == "not_permitted"
    # too few modes for 90 % mass, or no modal data: NOT EVALUATED, never "permitted"
    p = _shear_building(20, 3.5); p["modes"] = p["modes"][:1]
    hm = PP.higher_mode_check(p, 1.0, 0.6)
    assert hm["status"] == "not_evaluated" and "90%" in hm["reason"]
    assert PP.higher_mode_check(dict(T1=1.0, phi={1: 1.0}, masses={1: 1.0}), 1.0, 0.6)["status"] == "not_evaluated"
    assert PP.nsp_status(True, hm) == "not_evaluated" and PP.nsp_status(True, None) == "not_evaluated"
    assert PP.nsp_status(True, dict(status="not_significant")) == "permitted"


def test_nsp_target_gates_permitted_on_both_7321_tests():
    from pushover import postprocess as PP, package_reader as PR, hinge_models as HM
    import copy
    u, V = _curve(Ke=200.0, Vy=500.0, a1=0.02, umax=40.0)
    prm = copy.deepcopy(HM.load_params())
    b = PR.DesignBasis(SDS=1.0, SD1=0.45, W_kip=1000.0, system="SMF")
    def run_for(pat):
        return dict(rec=dict(u=list(u), V=list(V), story_u=[[x * 0.5, x] for x in u]), pattern=pat, H=240.0,
                    heights=[120.0, 120.0], gravity_table_QG=[100.0, 100.0])
    pat = _shear_building(2, 0.5)
    n = PP.nsp_target(run_for(pat), b, prm, 1.0)
    assert n["nsp_strength_ok"] and n["higher_modes"]["status"] == "not_significant"
    assert n["nsp_status"] == "permitted" and n["nsp_permitted"] is True
    n = PP.nsp_target(run_for(dict(T1=0.5, phi=pat["phi"], masses=pat["masses"])), b, prm, 1.0)   # pre-NL-R2-12 pattern
    assert n["nsp_strength_ok"] and n["nsp_status"] == "not_evaluated" and n["nsp_permitted"] is False
    assert "NOT EVALUATED" in n["nsp_status_text"]
    # Ex29-like: the 20-storey modes on the same curve -> not permitted alone
    p20 = _shear_building(20, 3.5)
    r = run_for(p20); r["pattern"]["T1"] = 0.5
    n = PP.nsp_target(r, b, prm, 1.0)
    assert n["nsp_status"] == "permitted_with_LDP" and n["nsp_permitted"] is False and "supplementary LDP" in n["nsp_status_text"]


def _portal_results(d="X"):
    """A short real push of the portal and the per-direction results the CLI builds (nsp / p695 / acceptance)."""
    from pushover import nonlinear_model as NM, postprocess as PP, performance as PF
    pkg = _portal(); prm = _fr_params()
    loads, table = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="fibre", member_nseg=2)
    run = NM.pushover(pkg, hinges, d, loads, prm, max_roof_drift=0.03, verbose=False, gravity_table=table, tail_strategies=())
    nsp = {lvl: PP.nsp_target(run, pkg.basis, prm, f) for lvl, f in prm["nsp"]["hazard_levels"].items()}
    acc = {lvl: PF.augment(PP.acceptance(run, hinges, n["target_disp_in"], lvl), run, hinges) for lvl, n in nsp.items()}
    return pkg, prm, table, stats, run, dict(nsp=nsp, p695=PP.p695_factors(run, pkg.basis, nsp["BSE-1N"]), acc=acc, hinges=hinges)


def test_report_states_both_7321_tests(tmp_path):
    from pushover import report_supplement as RS
    pkg, prm, table, stats, run, R = _portal_results()
    n = R["nsp"]["BSE-1N"]
    assert n["higher_modes"]["status"] == "not_significant" and n["nsp_status"] in ("permitted", "not_permitted")
    html = open(RS.write(str(tmp_path), pkg, prm, {"X": run}, {"X": R}, table, stats, 1.0), encoding="utf-8").read()
    assert "Higher-mode significance — ASCE 41-23 §7.3.2.1 item 2" in html and "NSP applicability (§7.3.2.1)" in html
    assert "perform with the linear package's RS results" not in html                  # no longer left as a to-do note
    po = json.load(open(tmp_path / "pushover_package.json"))
    hm = po["directions"]["X"]["nsp"]["BSE-1N"]["higher_modes"]
    assert hm["ratios"] and hm["clause"] == "ASCE 41-23 7.3.2.1 item 2"


def test_each_direction_written_when_done_and_progress_lines(tmp_path, capsys):
    """NL-R2-14 (no time limit, user decision): X is on disk, marked partial, before Y starts -- a Y that hangs or dies
    never loses X -- and the push prints progress lines (step, roof drift, V/Vmax, elapsed)."""
    import time
    from pushover import cli, nonlinear_model as NM
    pkg = _portal(); prm = _fr_params()
    loads, table = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    seen = {}

    def push(pkg_, hinges, d, loads_, prm_, **kw):
        if d == "Y":                                       # X must already be written when Y starts
            seen["po"] = json.load(open(tmp_path / "pushover_package.json"))
            seen["html"] = (tmp_path / "pushover_report.html").read_text(encoding="utf-8")
            seen["csv"] = (tmp_path / "curve_X.csv").exists()
            raise KeyboardInterrupt("Y stopped by hand after 90 min")
        kw.update(max_roof_drift=0.03, tail_strategies=())
        return NM.pushover(pkg_, hinges, d, loads_, prm_, progress_s=0, **kw)

    with pytest.raises(KeyboardInterrupt):
        cli.run_directions(pkg, prm, ["X", "Y"], str(tmp_path), loads, table, PG, time.time(), site_class="D", push=push)
    po = seen["po"]
    assert list(po["directions"]) == ["X"] and po["directions_pending"] == ["Y"] and po["complete"] is False
    assert po["directions"]["X"]["curve_u_in"] and seen["csv"] and "PARTIAL RESULTS" in seen["html"]
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.startswith("[pushover X push] step ")]
    assert lines and all("roof u" in l and "drift" in l and "V/Vmax" in l and "elapsed" in l for l in lines)
    assert ">> [X] results written" in out and "still to run: Y" in out and "[X NSP 7.3.2.1]" in out
    # the finished X survives on disk; a later complete run clears the partial flag
    assert json.load(open(tmp_path / "pushover_package.json"))["directions_pending"] == ["Y"]
    capsys.readouterr()
    runs, results, stats = cli.run_directions(pkg, prm, ["X"], str(tmp_path), loads, table, PG, time.time(), push=push)
    po = json.load(open(tmp_path / "pushover_package.json"))
    assert po["complete"] is True and po["directions_pending"] == [] and "PARTIAL" not in (tmp_path / "pushover_report.html").read_text(encoding="utf-8")


def test_progress_line_throttled_by_wall_clock():
    from pushover import nonlinear_model as NM
    import io, contextlib
    clock = [0.0]
    p = NM._Progress("Y", 1200.0, every_s=60.0)
    p._time = lambda: clock[0]; p.t0 = p.last = 0.0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for k in range(1, 400):                            # one step per second for 400 s -> 6 lines, not 400
            clock[0] = float(k)
            p("push", k, 0.01 * k, 900.0, 1000.0)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 6 and lines[0] == "[pushover Y push] step 60  roof u 0.60 in (drift 0.05% H)  V/Vmax 0.900  elapsed 1m00s"


# --------------------------------------------------------------------------- NL-11: memory stays flat
def _rss_mb():
    return int(open("/proc/self/status").read().split("VmRSS:")[1].split()[0]) / 1024.0


def test_static_analysis_reuses_objects_memory_flat():
    """4.5k-DOF cantilever, 600 displacement-controlled steps through the pushover's step function, with a
    step change every 50 steps. Re-issuing the integrator each step grew RSS ~0.19 MB/step at this size
    (35 -> 192 MB in 800 steps); reusing it must keep growth to a few MB."""
    import openseespy.opensees as ops
    from pushover import nonlinear_model as NM
    n = 1500
    ops.wipe(); ops.model("basic", "-ndm", 2, "-ndf", 3)
    for i in range(n + 1):
        ops.node(i, float(i), 0.0)
    ops.fix(0, 1, 1, 1); ops.geomTransf("Linear", 1)
    for i in range(n):
        ops.element("elasticBeamColumn", i + 1, i, i + 1, 10.0, 29000.0, 100.0, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(n, 0.0, 1.0, 0.0)
    an = NM.StaticAnalysis(constraints=("Plain",))
    dU = 1e-4
    for _ in range(20):                                           # warm-up (allocator pools)
        assert NM._try_analyze(an, dU, n, 2)
    r0 = _rss_mb()
    for k in range(600):
        if k and k % 50 == 0:
            dU = 1e-4 if dU != 1e-4 else 5e-5
        assert NM._try_analyze(an, dU, n, 2)
    growth = _rss_mb() - r0
    assert an.issued["integrator"] <= 14 and an.issued["algorithm"] <= 14
    assert growth < 12.0, "RSS grew %.1f MB over 600 steps" % growth
    ops.wipe()


# --------------------------------------------------------------------------- NL-22: tail status
def test_tail_status_lower_bound_when_escalation_disabled():
    """A push that stops on non-convergence above 0.8 Vmax with --tail none is a LOWER BOUND, never 'captured'."""
    from pushover import nonlinear_model as NM
    import openseespy.opensees as ops
    pkg = _portal(); prm = _fr_params()
    loads, table = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="fibre", member_nseg=2)
    orig = NM._try_analyze
    calls = {"n": 0}
    def flaky(an, dU, ctrl, dof, algos=NM.ALGOS):               # converge 15 steps, then never again
        calls["n"] += 1
        return orig(an, dU, ctrl, dof, algos) if calls["n"] <= 15 else False
    NM._try_analyze = flaky
    try:
        run = NM.pushover(pkg, hinges, "X", loads, prm, max_roof_drift=0.05, verbose=False, gravity_table=table, tail_strategies=())
    finally:
        NM._try_analyze = orig
    assert run["stop_reason"].startswith("solver")
    assert run["tail"]["status"] == "lower_bound" and run["tail"]["captured"] is False


# --------------------------------------------------------------------------- NL-07: RBS from the package
def test_rbs_detection_structured_variants():
    from pushover import package_reader as PR
    variants = [
        ({"members": [{"id": "m", "inputs": {"section": "W36X182", "RBS": {"a_in": 7.5, "b_in": 27.25, "c_in": 2.875}}}]}, "W36X182", 2.875),
        ({"connections": [{"id": "c", "type": "SMF beam-to-column: AISC 358 Ch.5 Reduced Beam Section", "section": "W36X182",
                           "components": "RBS a=7.50 in, b=27.25 in, c=2.875 in (48% flange reduction)"}]}, "W36X182", 2.875),
        ({"connections": [{"id": "conn-IMF-RBS-W27X94-W14X176", "type": "IMF beam-to-column, prequalified RBS (AISC 358-22 Ch. 5)",
                           "section": None, "components": "RBS (AISC 358 Ch. 5) W27X94 to W14X176: a=6.00 in, b=20.17 in, c=2.00 in"}]}, "W27X94", 2.0),
    ]
    for calc, sec, c in variants:
        d = PR.beam_details(_portal(calc=calc))
        assert d["rbs_frame"] and abs(d["rbs"][sec]["c_in"] - c) < 1e-9
    # a gravity shear tab is not an RBS, and free text elsewhere is not evidence
    d = PR.beam_details(_portal(calc={"connections": [{"id": "g", "type": "single-plate shear tab", "section": "W24X76",
                                                         "components": "PL 3/8 x 21, a=1 in"}]}))
    assert not d["rbs_frame"] and not d["rbs"]
    # connection type names the RBS but records no cut -> frame-level evidence, AISC 358 default per beam
    d = PR.beam_details(_portal(calc={"connections": [{"id": "x", "type": "RBS moment connection (A358)"}]}))
    assert d["rbs_frame"] and not d["rbs"]


def test_beam_hinge_uses_per_beam_z_rbs_and_package_lb():
    from pushover import nonlinear_model as NM, hinge_models as HM, sections_db as SDB
    pkg = _portal(calc=RBS_CALC)
    prm = _fr_params()
    prm["beam_flexure"]["rbs_c_in"] = 3.25                       # a stale global cut must not win over the package
    view, info = NM.beam_params_for(pkg, prm, "W24X76", fr=True)
    assert abs(info["rbs"]["c_in"] - 1.8) < 1e-12
    h = HM.beam_hinge("W24X76", 300.0, view)
    p = SDB.props("W24X76")
    Fye = prm["material"]["Fy_ksi"] * prm["material"]["Ry_expected"]
    Z_rbs = p["Zx"] - 2 * 1.8 * p["tf"] * (p["d"] - p["tf"])        # AISC 358-22 Eq. 5.7-4
    assert abs(h.Mpe_kipin - Z_rbs * Fye) < 1e-6
    # Lb = max(member 96 in, bracing 90 in) = 96 in -> Lb/ry
    assert abs(view["beam_flexure"]["Lb_over_ry"] - 96.0 / p["ry"]) < 1e-9 and not view["beam_flexure"].get("Lb_divisor")
    assert "Lb/ry=%.1f" % (96.0 / p["ry"]) in " ".join(h.flags)
    # RBS frame, section without a recorded cut -> AISC 358-22 5.7 default c = 0.25 bf
    view2, info2 = NM.beam_params_for(pkg, prm, "W24X62", fr=True)
    assert abs(info2["rbs"]["c_in"] - 0.25 * SDB.props("W24X62")["bf"]) < 1e-9
    # pinned (non-FR) beam of an RBS section: no cut
    view3, info3 = NM.beam_params_for(pkg, prm, "W24X76", fr=False)
    assert info3["rbs"] is None and view3["beam_flexure"]["rbs_c_in"] == 0.0


def test_fibre_rbs_mesh_reduces_beam_strength():
    """The fibre RBS segments carry the circular cut: a pinned-base portal (beam-hinging sway mechanism,
    V ~ 2 M_face / h) with the cut is weaker than without, by about Z_RBS / Zx x L/(L - 2 S_h)."""
    from pushover import nonlinear_model as NM, fibre_model as FM, sections_db as SDB
    import openseespy.opensees as ops
    assert FM.rbs_cut_depth(0.0, 18.0, 1.8) == pytest.approx(1.8) and FM.rbs_cut_depth(9.0, 18.0, 1.8) == 0.0
    prm = _fr_params()
    Vmax = {}
    for key, calc in (("plain", {}), ("rbs", RBS_CALC)):
        pkg = _portal(calc=calc, col="W14X311", base="pinned")
        loads, table = NM.gravity_loads(pkg, prm, verbose=False)
        PG = NM.column_gravity_axials(pkg, loads)
        hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="fibre", member_nseg=4)
        if key == "rbs":
            assert stats["rbs_beams"] == 1 and stats["rbs_ends"] == 2
            beam_h = [h for h in hinges.values() if h["kind"] == "beam"]
            assert all(h["rbs"] and len(h["segs"]) == 2 and abs(h["Lp_in"] - (5.5 + 18.0)) < 1e-9 for h in beam_h)
        run = NM.pushover(pkg, hinges, "X", loads, prm, max_roof_drift=0.03, verbose=False, gravity_table=table, tail_strategies=())
        Vmax[key] = max(run["rec"]["V"])
    p = SDB.props("W24X76")
    ratio = (p["Zx"] - 2 * 1.8 * p["tf"] * (p["d"] - p["tf"])) / p["Zx"]
    expect = ratio * 300.0 / (300.0 - 2 * (5.5 + 9.0))             # moment at the column face when the RBS centre yields
    assert Vmax["rbs"] < Vmax["plain"]
    assert abs(Vmax["rbs"] / Vmax["plain"] - expect) < 0.08, (Vmax, expect)


# --------------------------------------------------------------------------- NL-28: per-joint doublers
def test_per_joint_doublers_from_hr():
    from pushover import package_reader as PR
    calc = {"capacity_design": {"panel_zone": {"by_joint": [
        {"joint": "X-frame L1 X-int", "column": "W27X368", "beams": "W33X221+W33X221", "doubler_in": 1.0625},
        {"joint": "X-frame L1 X-next-to-corner", "column": "W27X368", "beams": "W33X221+W33X221", "doubler_in": 0.9375},
        {"joint": "X-frame L2 X-int", "column": "W27X368", "beams": "W33X221+W33X221", "doubler_in": 1.0},
        {"joint": "floor 4 interior two-beam SMF joint", "level": 4, "column": "W14X398", "beams": "2 x W36X160", "doubler_in": 0.5625},
        {"joint": "conn-IMF-RBS-W27X94-W14X176", "doubler_in": 0}]}}}
    recs = PR.pz_doublers(_portal(calc=calc))
    assert recs[0]["level"] == 1 and recs[3]["beams"] == ["W36X160", "W36X160"] and recs[4]["column"] == "W14X176"
    t, why = PR.doubler_for_joint(recs, 2, "W27X368", ["W33X221", "W33X221"])
    assert t == 1.0
    t, why = PR.doubler_for_joint(recs, 1, "W27X368", ["W33X221", "W33X221"])
    assert t == 0.9375 and "thinnest" in why
    assert PR.doubler_for_joint(recs, 4, "W14X398", ["W36X160", "W36X160"])[0] == 0.5625
    assert PR.doubler_for_joint(recs, 3, "W14X176", ["W27X94"])[0] == 0.0
    assert PR.doubler_for_joint(recs, 5, "W14X398", ["W36X160"])[0] is None


def test_scissors_panel_zone_uses_joint_doubler():
    from pushover import nonlinear_model as NM, sections_db as SDB
    calc = {"capacity_design": {"panel_zone": {"by_joint": [
        {"joint": "L1 corner", "level": 1, "column": "W14X132", "beams": "1 x W24X76", "doubler_in": 0.75}]}}}
    pkg = _portal(calc=calc)
    prm = _fr_params(); prm.setdefault("panel_zones", {}).update(mode="scissors", material="elastic", doubler_t_in=0.0)
    loads, _ = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="imk")
    reg = list(stats["panel_zone_registry"].values())
    assert len(reg) == 2 and stats["pz_doublers"]["per_joint"] == 2
    tw = SDB.props("W14X132")["tw"]
    for r in reg:
        assert abs(r["specs"][0]["tp"] - (tw + 0.75)) < 1e-9
        assert any("by_joint" in f for f in r["specs"][0]["flags"])
