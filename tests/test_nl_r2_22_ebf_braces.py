"""NL-R2-22: EBF braces are force-controlled (critical) -- AISC 341-22 Appendix 1 Table A-1.7.3 ('Brace / Axial /
Force / Critical'), AISC 342-22 E2.4a(b). They are modelled elastic in an EBF (and only there) and their axial force is
checked by ASCE 7-22 16.4.2.1. Closed-form checks.
Run: PYTHONPATH=. python -m pytest tests/test_nl_r2_22_ebf_braces.py -q
"""
import copy, math, os, sys, types
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openseespy.opensees as ops
from pushover import hinge_models as HM, sections_db as SDB, nonlinear_model as NM
from nlrha import acceptance as AC


def _pkg(system="EBF"):
    """One-storey split-K bay (x = 0..240 in, z = 0 / 144 in), link 102..138 in; braces from the bases to the link ends."""
    nodes = {101: (0.0, 0.0, 0.0), 301: (240.0, 0.0, 0.0), 100101: (0.0, 0.0, 144.0), 100301: (240.0, 0.0, 144.0),
             101101: (102.0, 0.0, 144.0), 101301: (138.0, 0.0, 144.0)}
    els = [dict(tag=1, n1=101, n2=100101), dict(tag=2, n1=301, n2=100301), dict(tag=3, n1=100101, n2=101101, release=["-releasey", 1]),
           dict(tag=4, n1=101101, n2=101301, release=None), dict(tag=5, n1=101301, n2=100301, release=["-releasey", 2]),
           dict(tag=6, n1=101, n2=101101, A=13.5, E=29000.0), dict(tag=7, n1=301, n2=101301, A=13.5, E=29000.0)]
    sched = {1: dict(member="col", section="W14X90"), 2: dict(member="col", section="W14X90"), 3: dict(member="beam", section="W14X74"),
             4: dict(member="beam", section="W14X74"), 5: dict(member="beam", section="W14X74"),
             6: dict(member="brace", section="HSS8X8X1/2"), 7: dict(member="brace", section="HSS8X8X1/2")}
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes=nodes, elements=els), schedule=sched,
                                basis=types.SimpleNamespace(system=system), root=".")
    pkg._cfg_exec = ({}, "test")
    return pkg


def _ctx(pkg, prm):
    return dict(links=HM.find_links_pkg(pkg, prm, NM.member_kind), brb={}, pt_secs={}, pt_builder=None, pt_count=0)


def test_gate_only_ebf_braces_framing_into_a_link():
    prm = HM.load_params()
    pkg = _pkg("EBF"); ctx = _ctx(pkg, prm)
    assert list(ctx["links"]) == [4]
    b6, b7 = pkg.model.elements[5], pkg.model.elements[6]
    assert NM.ebf_brace_force_controlled(pkg, ctx, prm, b6) and NM.ebf_brace_force_controlled(pkg, ctx, prm, b7)
    # a brace of the same building that does not frame into a link end keeps its buckling model
    assert not NM.ebf_brace_force_controlled(pkg, ctx, prm, dict(tag=9, n1=301, n2=100101))
    # SCBF / OCBF / BRBF: never (no link, and the system gate)
    for sysname in ("SCBF", "OCBF", "BRBF", "SMF"):
        p2 = _pkg(sysname); c2 = _ctx(p2, prm)
        assert not NM.ebf_brace_force_controlled(p2, c2, prm, b6)
        c2["links"] = ctx["links"]                                     # even with a (manual) link list, the system gate holds
        assert not NM.ebf_brace_force_controlled(p2, c2, prm, b6)
    # opt-out restores the buckling brace
    p3 = copy.deepcopy(prm); p3.setdefault("brace_axial", {})["ebf_brace_model"] = "buckling"
    assert not NM.ebf_brace_force_controlled(pkg, _ctx(pkg, p3), p3, b6)


def test_ebf_brace_is_elastic_truss_with_design_model_area():
    prm = HM.load_params(); prm["_analysis"] = "nlrha"                # NLRHA would otherwise build a physical-theory brace
    pkg = _pkg("EBF"); ctx = _ctx(pkg, prm)
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, xyz in pkg.model.nodes.items():
        ops.node(t, *xyz)
    for t in pkg.model.nodes:                                          # only the brace's top node is free (along X)
        ops.fix(t, *((0, 1, 1, 1, 1, 1) if t == 101101 else (1, 1, 1, 1, 1, 1)))
    hinges, stats = {}, dict(brace_nonlinear=0)
    e = pkg.model.elements[5]
    mat = NM.build_brace(pkg, e, "HSS8X8X1/2", prm, NM.MAT_BASE, hinges, stats, ctx)
    assert hinges == {} and stats["brace_ebf_elastic"] == 1 and stats["brace_nonlinear"] == 0
    meta = stats["ebf_braces"][6]
    L = math.dist(pkg.model.nodes[101], pkg.model.nodes[101101])
    assert meta["A_model"] == 13.5 and abs(meta["L_in"] - L) < 1e-9 and meta["section"] == "HSS8X8X1/2"
    K = float((prm.get("brace_axial") or {}).get("K_effective", 1.0))
    assert abs(meta["Lc_in"] - K * L) < 1e-6                           # no design length in cfg -> K_effective x L
    # axial stiffness E A / L (small load: corotTruss geometry stays linear)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    ux = (102.0 / L)
    ops.load(101101, -20.0 * ux, 0, 0, 0, 0, 0)
    ops.system("UmfPack"); ops.numberer("RCM"); ops.constraints("Plain"); ops.test("NormDispIncr", 1e-10, 20)
    ops.algorithm("Newton"); ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    assert ops.analyze(1) == 0
    N = ops.eleResponse(6, "axialForce")[0]
    dx = ops.nodeDisp(101101, 1)
    assert N < 0 and abs(dx - (-20.0 * ux) / (29000.0 * 13.5 / L * ux * ux)) < 1e-3 * abs(dx)   # linear, no buckling


def test_scbf_brace_unchanged():
    prm = HM.load_params()
    pkg = _pkg("SCBF"); ctx = _ctx(pkg, prm)
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, xyz in pkg.model.nodes.items():
        ops.node(t, *xyz)
    hinges, stats = {}, dict(brace_nonlinear=0)
    NM.build_brace(pkg, pkg.model.elements[5], "HSS8X8X1/2", prm, NM.MAT_BASE, hinges, stats, ctx)
    assert "ebf_braces" not in stats and hinges[6]["kind"] == "brace" and stats["brace_nonlinear"] == 1


def test_brace_capacity_closed_form():
    prm = HM.load_params(); prm.setdefault("brace_axial", {})["Fy_ksi"] = 50.0
    p = SDB.props("HSS8X8X1/2")
    Lc = 220.0
    c = AC.ebf_brace_capacity("HSS8X8X1/2", Lc, prm)
    KLr = Lc / min(p["rx"], p["ry"]); Fe = math.pi ** 2 * 29000.0 / KLr ** 2
    Fcr = 0.658 ** (50.0 / Fe) * 50.0 if KLr <= 4.71 * math.sqrt(29000.0 / 50.0) else 0.877 * Fe
    assert abs(c["phiPn"] - 0.9 * Fcr * p["A"]) < 1e-6 and abs(c["phiTn"] - 0.9 * 50.0 * p["A"]) < 1e-9
    assert c["notes"] == []                                           # HSS8X8X1/2 walls are non-slender


def test_brace_fc_check_closed_form():
    """Qu = suite mean of the record peaks; Pr = (1.2 + 0.12 SMS) D + 0.5 L + 1.3 Ie (Qu - Qns); tension by 16.4-2."""
    prm = HM.load_params(); prm.setdefault("brace_axial", {})["Fy_ksi"] = 50.0
    meta = {6: dict(section="HSS8X8X1/2", Lc_in=220.0, z=144.0, Lc_source="test"),
            7: dict(section="HSS8X8X1/2", Lc_in=220.0, z=144.0, Lc_source="test")}
    runs = [dict(stats=dict(ebf_braces=meta), ebf_brace_env={6: dict(Pg=-10.0, Pc=300.0, Pt=250.0), 7: dict(Pg=-10.0, Pc=100.0, Pt=50.0)}),
            dict(stats=dict(ebf_braces=meta), ebf_brace_env={6: dict(Pg=-10.0, Pc=500.0, Pt=150.0), 7: dict(Pg=-10.0, Pc=120.0, Pt=60.0)})]
    SMS, Ie, frac_D = 1.5, 1.0, 0.8
    rows, notes = AC.ebf_brace_fc(runs, runs, frac_D, SMS, Ie, 1.3, 1.0, prm, lambda v: np.asarray(v, float).mean(axis=0))
    assert len(rows) == 1 and rows[0]["ele"] == 6                     # worst of the (section, level) group
    r = rows[0]
    cap = AC.ebf_brace_capacity("HSS8X8X1/2", 220.0, prm)
    Pns = 10.0; D = Pns * frac_D; hL = Pns - D
    Pr1 = (1.2 + 0.12 * SMS) * D + hL + 1.3 * Ie * (400.0 - Pns)
    Pr2 = (0.9 - 0.12 * SMS) * D - 1.3 * Ie * (Pns + 200.0)
    assert abs(r["demand_comp"] - Pr1) < 1e-9 and abs(r["demand_tens"] - (-Pr2)) < 1e-9
    assert abs(r["DC"] - max(Pr1 / cap["phiPn"], -Pr2 / cap["phiTn"])) < 1e-12
    assert "critical" in r["criticality"]
    # no EBF braces in the runs (any other system) -> nothing checked, nothing changed
    assert AC.ebf_brace_fc([dict(stats={})], [dict(stats={})], frac_D, SMS, Ie, 1.3, 1.0, prm, np.mean) == ([], [])


def test_nsp_brace_force_check_eq_7_41():
    """ASCE 41-23 Eq. (7-41): gamma chi (Q_UF - Q_G) + Q_G <= Q_CL, gamma = 1.3 (critical), chi 1.0 CP / 1.3 LS, gamma chi <= 1.5."""
    from pushover import postprocess as PP
    prm = HM.load_params(); prm.setdefault("brace_axial", {})["Fy_ksi"] = 50.0
    run = dict(ebf_brace_tags=[6], ebf_braces={6: dict(section="HSS8X8X1/2", Lc_in=220.0, z=144.0)},
               rec=dict(ebf_N=[[-10.0], [-400.0]]))
    f = PP.ebf_brace_fc_nsp(run, 1, prm)
    Pn = AC.column_Pn("HSS8X8X1/2", 220.0, Fy=50.0)[0]                 # inelastic range: no 0.85 factor
    assert abs(f["worst_DC"]["CP"] - (1.3 * 390.0 + 10.0) / Pn) < 1e-9
    assert abs(f["worst_DC"]["LS"] - (1.5 * 390.0 + 10.0) / Pn) < 1e-9   # 1.3 x 1.3 capped at 1.5
    acc = dict(status=PP.EVALUATED, groups=[dict(x=1)], worst_DC=dict(IO=0.1, LS=0.1, CP=0.1), ebf_brace_fc=f)
    assert PP.level_verdict(acc, "CP") is ((1.3 * 390.0 + 10.0) / Pn <= 1.0)
    acc["ebf_brace_fc"] = dict(f, worst_DC=dict(CP=1.2, LS=1.4, IO=1.4))
    assert PP.level_verdict(acc, "CP") is False
    assert PP.ebf_brace_fc_nsp(dict(rec={}), 1, prm) is None             # no EBF braces: nothing added


def test_nc_reason_names_links_past_capping():
    """The non-convergence reason says how many EBF links are past a / b (Table C2.4) at the last converged state."""
    from nlrha import run as RN
    sh, _, _ = HM.link_specs("W14X74", 36.0, HM.load_params())
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0, 0, 0); ops.node(2, 0, 0, 0)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 1, 1, 0, 1, 1, 1)
    ops.uniaxialMaterial("Elastic", 1, 1.0e3)
    ops.element("zeroLength", 30000043, 1, 2, "-mat", 1, 1, 1, 1, 1, 1, "-dir", 1, 2, 3, 4, 5, 6)
    hinges = {30000043: dict(kind="link", ele=4, spec=sh, dof=3)}
    k = [0]
    def to(d):
        ops.wipeAnalysis()
        if k[0]:
            ops.remove("loadPattern", k[0])
        k[0] += 1
        ops.timeSeries("Constant", k[0]); ops.pattern("Plain", k[0], k[0]); ops.sp(2, 3, d)
        ops.system("UmfPack"); ops.numberer("Plain"); ops.constraints("Transformation"); ops.test("NormDispIncr", 1e-10, 10)
        ops.algorithm("Newton"); ops.integrator("LoadControl", 1.0); ops.analysis("Static"); assert ops.analyze(1) == 0
    to(0.5 * sh.a_pl)
    assert RN.link_state_note(hinges) == ""                             # below a: nothing to say
    to(-(sh.theta_y + sh.a_pl + 0.01))
    note = RN.link_state_note(hinges)
    assert "1 of 1 at or past the capping rotation a" in note and "0 past b" in note
    to(sh.theta_y + sh.b_pl + 0.05)
    assert "1 past b" in RN.link_state_note(hinges)
    # the production link material (Hysteretic + MinMax at Delta_y + b e): once failed, V = 0 for good, also when the
    # deformation comes back below b -- the link must still be counted as past b
    to(0.0)                                                            # back to the origin before the swap
    HM.make_link_shear_material(77, sh, HM.load_params())
    ops.remove("element", 30000043)
    ops.element("zeroLength", 30000043, 1, 2, "-mat", 1, 1, 77, 1, 1, 1, "-dir", 1, 2, 3, 4, 5, 6)
    for f in (0.3, 0.6, 0.9):
        to(sh.theta_y + f * sh.a_pl)
    to(sh.theta_y + 0.5 * (sh.a_pl + sh.b_pl))                         # between a and b: on the descending branch, V > 0
    d, V = NM.zero_length_spring(30000043, 3)
    assert abs(V) > 0.1 * sh.Vp_kip
    note = RN.link_state_note(hinges)
    assert "1 of 1 at or past the capping rotation a" in note and "0 past b" in note
    to(sh.theta_y + sh.b_pl + 0.05)                                    # MinMax fails
    to(sh.theta_y + 0.5 * (sh.a_pl + sh.b_pl))                         # back below b: still failed (V = 0)
    d, V = NM.zero_length_spring(30000043, 3)
    assert V == 0.0 and abs(d) < sh.theta_y + sh.b_pl
    assert "1 past b (shear strength lost)" in RN.link_state_note(hinges)


def _one_link_level(analysis):
    """A link (W14X74, e = 36 in) at level z = 100 in whose floor mass sits on the master node 900."""
    p = SDB.props("W14X74"); e = 36.0
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes={1: (0., 0., 100.), 2: (e, 0., 100.), 900: (18., 50., 100.)},
                                                            masses={900: (2.0, 2.0, 0.0, 0.0, 0.0, 500.0)}))
    el = dict(tag=7, n1=1, n2=2, A=p["A"], E=29000., G=11200., J=p["J"], Iy=p["Ix"], Iz=p["Iy"], transf=3, release=None)
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, xyz in pkg.model.nodes.items():
        ops.node(t, *xyz)
    ops.mass(900, 2.0, 2.0, 0.0, 0.0, 0.0, 500.0)
    ops.geomTransf("Linear", 3, 0, 0, 1)
    ops.uniaxialMaterial("Elastic", 1, NM.RIGID_T); ops.uniaxialMaterial("Elastic", 2, NM.RIGID_R)
    prm = HM.load_params(); prm["_analysis"] = analysis
    hinges, stats, ctx = {}, {}, {}
    NM.build_link_imk(pkg, el, "W14X74", prm, NM.MAT_BASE, hinges, stats, ctx)
    return pkg, stats, ctx, p["A"] * e * NM.STEEL_DENSITY_KIP_IN3 / NM.G_IN


def test_link_own_mass_nlrha_only_and_balanced(monkeypatch):
    """NL-R2-22: in the NLRHA the link's own steel mass sits on its three internal nodes (inertia for the Table C2.4 C->D
    drop) and comes off the floor mass of its level: total translational mass unchanged. The NSP model is unchanged."""
    pkg, stats, ctx, m = _one_link_level("nlrha")
    inner = [NM.HN_BASE + 7 * 10 + k for k in (NM.LINK_END, 1, 2)]
    assert abs(sum(ops.nodeMass(n)[0] for n in inner) - m) < 1e-12 and 5e-4 < m < 7e-4      # 74 lb/ft x 3 ft
    monkeypatch.setattr(NM, "levels", lambda pkg: [(1, 100.0, 900, [1, 2])])
    NM._link_mass_balance(pkg, ctx, stats)
    assert abs(ops.nodeMass(900)[0] - (2.0 - m)) < 1e-12 and abs(ops.nodeMass(900)[1] - (2.0 - m)) < 1e-12
    assert stats["link_mass_kip_s2_in"] == pytest.approx(m, abs=1e-6)
    pkg, stats, ctx, m = _one_link_level("nsp")
    assert all(ops.nodeMass(n)[0] == 0.0 for n in inner) and not ctx.get("link_mass")
