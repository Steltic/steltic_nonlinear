"""NL-02 (BRB), NL-03 (EBF links), NL-10 (cyclic deterioration) -- element-level regression tests.

Every number is checked against a closed form:
  BRB   Q_CE = Ry*Fysc*Asc, Delta_y = Q_CE*L/(E*KF*Asc), point C = omega*Q_CE (tension) / beta*omega*Q_CE (compression)
        at Delta_y + 13.3 Delta_y (AISC 342-22 Table C3.3), strength lost beyond b.
  link  Vp = 0.6*Fye*(d-2tf)*tw, Ke = 12EI/(e^3 (1+eta)) (AISC 342-22 C-E2-1), Table C2.4 limits x e.
  IMK   Lambda from the template expression; monotonic backbone unchanged, cyclic peaks reduced.
Run: PYTHONPATH=. python -m pytest tests/test_nl_elements.py -q
"""
import copy, math, os, sys, types
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openseespy.opensees as ops
from pushover import hinge_models as HM, sections_db as SDB, nonlinear_model as NM


def _prm(**groups):
    p = copy.deepcopy(HM.load_params())
    for k, v in groups.items():
        p[k] = v
    return p


# ----------------------------------------------------------------------------- NL-02 BRB
def test_brb_label_parsing_and_props_guard():
    assert SDB.is_brb("BRB-Asc22.5") and SDB.parse_brb_label("BRB-Asc22.5") == 22.5
    assert SDB.parse_brb_label("brb 10.5") == 10.5 and SDB.parse_brb_label("W14X68") is None
    with pytest.raises(KeyError, match="buckling-restrained"):
        SDB.props("BRB-Asc22.5")


CALC = {"members": [{"inputs": {"kind": "brace", "section": "BRB-Asc22.5", "Asc_in2": 22.5, "Fysc_ksi": 38.0, "KF": 1.5,
                                "model_area_in2": 33.75}}],
        "capacity_design": {"adjusted_brace_strengths": {
            "basis": "F4.2a: compression beta*omega*Ry*Pysc, tension omega*Ry*Pysc; omega=1.36, beta=1.10 ASSUMED",
            "by_story": [{"story": 1, "Asc_in2": 22.5, "Pysc_kip": 855.0, "adjusted_T_kip": 1407.6, "adjusted_C_kip": 1548.4}]}}}


def test_brb_spec_from_package_closed_form():
    """Ex4 numbers: Asc from the package (not the 33.75 in2 truss area), omega/beta/Ry from the adjusted strengths."""
    pk = HM.brb_package_data(CALC)
    s = HM.brb_spec("BRB-Asc22.5", 394.4, HM.load_params(), A_model=33.75, pkg_data=pk)
    assert s.Asc == 22.5 and s.A_model == 33.75
    assert abs(s.omega - 1.36) < 1e-9 and abs(s.beta - 1548.4 / 1407.6) < 1e-9
    Ry = 1407.6 / 855.0 / 1.36
    assert abs(s.Ry - Ry) < 1e-9
    Q = Ry * 38.0 * 22.5
    assert abs(s.Pye_kip - Q) < 1e-6 and abs(s.Pcre_kip - Q) < 1e-6
    dy = Q * 394.4 / (29000.0 * 1.5 * 22.5)
    assert abs(s.dT - dy) < 1e-9 and abs(s.K_axial - Q / dy) < 1e-6
    # Table C3.3 (plastic) -> stored as TOTAL deformation (the monitors record total axial deformation)
    assert abs(s.IO - 4.0 * dy) < 1e-9 and abs(s.LS - 11.0 * dy) < 1e-9 and abs(s.CP - 14.3 * dy) < 1e-9
    assert abs(s.b_c - 14.3 * dy) < 1e-9 and s.IO_t == s.IO and s.CP_t == s.CP
    assert abs(s.omega * Q - 1407.6) < 1.0                          # = HR adjusted tension strength


def test_brb_spec_params_override_and_refusal():
    g = dict(HM.load_params()["brb_axial"]); g.update(Fysc_ksi=42.0, Ry=1.0, omega=1.5, beta=1.2, KF=1.6)
    s = HM.brb_spec("BRB-Asc10.0", 200.0, _prm(brb_axial=g), A_model=15.0, pkg_data=HM.brb_package_data(CALC))
    assert (s.Fysc_ksi, s.Ry, s.omega, s.beta) == (42.0, 1.0, 1.5, 1.2)
    assert abs(s.dT - 420.0 * 200.0 / (29000.0 * 1.6 * 10.0)) < 1e-9
    g2 = dict(HM.load_params()["brb_axial"]); g2["Fysc_ksi"] = None
    with pytest.raises(ValueError, match="Fysc"):
        HM.brb_spec("BRB-Asc10.0", 200.0, _prm(brb_axial=g2), A_model=15.0, pkg_data={})
    g3 = dict(HM.load_params()["brb_axial"]); g3["Fysc_ksi"] = 38.0
    s3 = HM.brb_spec("BRB-Asc10.0", 200.0, _prm(brb_axial=g3), A_model=15.0, pkg_data={})
    assert (s3.omega, s3.beta) == (1.3, 1.1) and any("FALLBACK" in f for f in s3.flags)


def _brb_truss(material):
    g = dict(HM.load_params()["brb_axial"]); g.update(Fysc_ksi=42.0, Ry=1.1, omega=1.4, beta=1.15, KF=1.5, material=material)
    prm = _prm(brb_axial=g)
    L = 200.0
    s = HM.brb_spec("BRB-Asc10.0", L, prm, A_model=15.0)
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 3)
    ops.node(1, 0, 0, 0); ops.node(2, L, 0, 0)
    ops.fix(1, 1, 1, 1); ops.fix(2, 0, 1, 1)
    HM.make_brb_material(10, s, prm)
    ops.element("corotTruss", 1, 1, 2, s.A_model, 10)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 1.0, 0, 0)
    ops.constraints("Plain"); ops.numberer("Plain"); ops.system("BandGen"); ops.test("NormDispIncr", 1e-10, 50); ops.algorithm("Newton")
    return s


def _drive(targets, n=200):
    out, u = [], ops.nodeDisp(2, 1)
    for tg in targets:
        ops.integrator("DisplacementControl", 2, 1, (tg - u) / n); ops.analysis("Static")
        for _ in range(n):
            assert ops.analyze(1) == 0
            u = ops.nodeDisp(2, 1); out.append((u, ops.eleResponse(1, "axialForce")[0]))
    return out


@pytest.mark.parametrize("material", ["Steel4", "Hysteretic"])
def test_brb_single_element_backbone(material):
    s = _brb_truss(material); Q = s.Pye_kip
    assert abs(Q - 1.1 * 42.0 * 10.0) < 1e-9
    r = _drive([s.dT, s.a_t])
    assert 0.95 <= r[199][1] / Q <= 1.0 + 1e-6                     # yield at Delta_y (Steel4 R0 transition ~3%)
    assert abs(r[-1][1] / Q - 1.4) < 0.005                         # omega at Delta_y + a
    s = _brb_truss(material); r = _drive([-s.a_t])
    assert abs(-r[-1][1] / Q - 1.4 * 1.15) < 0.005                 # beta*omega in compression
    s = _brb_truss(material); r = _drive([1.05 * s.b_t])
    assert r[-1][1] / Q < 0.75                                     # strength lost beyond b (Steel4: 0, Hysteretic: descent)
    s = _brb_truss(material); r = _drive([5 * s.dT, -5 * s.dT, 10 * s.dT, -10 * s.dT, 0.0], n=100)
    u = np.array([x[0] for x in r]); f = np.array([x[1] for x in r])
    energy = float(np.sum(0.5 * (f[1:] + f[:-1]) * np.diff(u)))
    assert energy > 2.0 * Q * 5 * s.dT                             # full, stable loops (no pinching)
    assert f.max() / Q < 1.4 and -f.min() / Q < 1.4 * 1.15


# ----------------------------------------------------------------------------- NL-03 EBF links
def test_link_specs_ex8_W14X74():
    """Ex8: W14X74, e = 36 in: Vp = 0.6*55*(d-2tf)*tw = 188 kip, 1.6 Mp/Vp = 59 in -> shear-controlled."""
    sh, fl, info = HM.link_specs("W14X74", 36.0, HM.load_params())
    p = SDB.props("W14X74")
    Vp = 0.6 * 55.0 * (p["d"] - 2 * p["tf"]) * p["tw"]
    assert abs(sh.Vp_kip - Vp) < 1e-6 and 187 < Vp < 189
    assert sh.link_class == "shear" and abs(1.6 * sh.Mp_kipin / sh.Vp_kip - 59.1) < 0.5
    EI = 29000.0 * p["Ix"]; As = p["d"] * p["tw"]; eta = 12 * EI / (36.0 ** 2 * 11200.0 * As)
    assert abs(sh.Ke - 12 * EI / (36.0 ** 3 * (1 + eta))) < 1e-6
    assert abs(sh.CP - 0.16 * 36.0) < 1e-9 and abs(sh.LS - 0.14 * 36.0) < 1e-9 and abs(sh.IO - 0.005 * 36.0) < 1e-9
    assert abs(sh.b_pl - 0.17 * 36.0) < 1e-9 and sh.theta_y == pytest.approx(Vp / sh.Ks)
    assert fl.CP <= 1e-5                                          # flexural yielding of a shear link: not a permitted deformation
    # intermediate link: both tables interpolated
    sh2, fl2, info2 = HM.link_specs("W14X74", 1.6 * 36.9 * 1.3, HM.load_params())   # rho ~ 2.08
    assert info2["link_class"] == "intermediate" and 0.3 < info2["f_shear"] < 0.7 and fl2.CP > 1e-4


def _single_link(sec="W14X74", e=36.0):
    p = SDB.props(sec)
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes={1: (0., 0., 100.), 2: (e, 0., 100.)}))
    el = dict(tag=7, n1=1, n2=2, A=p["A"], E=29000., G=11200., J=p["J"], Iy=p["Ix"], Iz=p["Iy"], transf=3, release=None)
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0., 0., 100.); ops.node(2, e, 0., 100.)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 1, 1, 0, 1, 1, 1)      # end 2: vertical translation only (link deformed shape)
    ops.geomTransf("Linear", 3, 0, 0, 1)
    ops.uniaxialMaterial("Elastic", 1, NM.RIGID_T); ops.uniaxialMaterial("Elastic", 2, NM.RIGID_R)
    hinges, stats = {}, {}
    NM.build_link_imk(pkg, el, sec, HM.load_params(), NM.MAT_BASE, hinges, stats, {})
    return hinges, stats


def test_single_link_push_vs_Vp():
    hinges, stats = _single_link()
    tags = [t for t, h in hinges.items() if h["kind"] == "link"]
    assert len(tags) == 1 and stats["links"] == 1 and len(hinges) == 3      # shear spring + 2 flexural end springs
    sh = hinges[tags[0]]["spec"]; e = 36.0
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 0, 0, 1.0, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack"); ops.test("NormDispIncr", 1e-9, 50); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 2, 3, 0.002); ops.analysis("Static")
    u, V = [], []
    while not u or u[-1] < 0.19 * e:
        assert ops.analyze(1) == 0
        ops.reactions(); u.append(ops.nodeDisp(2, 3)); V.append(-ops.nodeReaction(1, 3))
    u, V = np.array(u), np.array(V)
    assert abs(V[0] / u[0] / sh.Ke - 1) < 0.01                       # elastic stiffness = AISC 342 Eq. C-E2-1
    gy = sh.Vp_kip / sh.Ke
    assert abs(np.interp(1.2 * gy, u, V) / sh.Vp_kip - 1.0) < 0.02      # yields at Vp = 0.6 Fye Alw
    assert np.interp(0.10 * e, u, V) > sh.Vp_kip                      # hardening within a
    assert V.max() / sh.Vp_kip <= sh.Vc_over_Vy + 1e-6
    assert V[-1] < 0.05 * sh.Vp_kip                                   # beyond b: strength lost
    # the monitor quantity the pushover / NLRHA use: deformation - V/K0 = plastic transverse displacement (gamma_p * e)
    d = ops.eleResponse(tags[0], "deformation")
    assert d[2] > sh.b_pl


def _ebf_members(system_d=False):
    """One-storey bay, x = 0 .. 240 in, z = 0 / 144 in. Split-K EBF: braces from the bases to link ends at 102 / 138."""
    nodes = {101: (0, 0, 0), 301: (240, 0, 0), 100101: (0, 0, 144), 100301: (240, 0, 144),
             101101: (102, 0, 144), 101301: (138, 0, 144)}
    mem = [dict(tag=1, kind="col", section="W14X90", n1=101, n2=100101, released=False),
           dict(tag=2, kind="col", section="W14X90", n1=301, n2=100301, released=False),
           dict(tag=3, kind="beam", section="W14X74", n1=100101, n2=101101, released=False),
           dict(tag=4, kind="beam", section="W14X74", n1=101101, n2=101301, released=False),
           dict(tag=5, kind="beam", section="W14X74", n1=101301, n2=100301, released=False),
           dict(tag=6, kind="brace", section="HSS8X8X1/2", n1=101, n2=101101, released=True),
           dict(tag=7, kind="brace", section="HSS8X8X1/2", n1=301, n2=101301, released=True)]
    return nodes, mem


def test_find_links_topology():
    nodes, mem = _ebf_members()
    prm = HM.load_params()
    links = HM.find_links(nodes, mem, prm, "EBF")
    assert list(links) == [4] and links[4]["link_class"] == "shear" and links[4]["e_in"] == 36.0
    # chevron CBF: both braces meet at ONE beam node -> no segment between work points -> no link
    nodes2 = {101: (0, 0, 0), 301: (240, 0, 0), 100101: (0, 0, 144), 100301: (240, 0, 144), 100201: (120, 0, 144)}
    mem2 = [dict(tag=1, kind="col", section="W14X90", n1=101, n2=100101), dict(tag=2, kind="col", section="W14X90", n1=301, n2=100301),
            dict(tag=3, kind="beam", section="W24X84", n1=100101, n2=100201), dict(tag=4, kind="beam", section="W24X84", n1=100201, n2=100301),
            dict(tag=6, kind="brace", section="HSS8X8X1/2", n1=101, n2=100201), dict(tag=7, kind="brace", section="HSS8X8X1/2", n1=301, n2=100201)]
    assert HM.find_links(nodes2, mem2, prm, "SCBF") == {}
    # manual tags / exclusions / detection off
    g = dict(prm["ebf_link"]); g.update(detect="off", element_tags=[3])
    assert list(HM.find_links(nodes, mem, _prm(ebf_link=g), "EBF")) == [3]
    g = dict(prm["ebf_link"]); g.update(exclude_tags=[4])
    assert HM.find_links(nodes, mem, _prm(ebf_link=g), "EBF") == {}


# ----------------------------------------------------------------------------- NL-10 cyclic deterioration
def test_cyclic_lambda_expressions():
    prm = HM.load_params()
    p = SDB.props("W24X84"); Fye = 55.0
    lam, c, flag = HM.cyclic_lambda("beam", p, Fye, 360.0, prm)
    ref = 495 * p["h_tw"] ** -1.34 * p["bf_2tf"] ** -0.595 * (Fye * 6.894757 / 355) ** -0.360
    assert abs(lam - min(ref, 3.0)) < 1e-9 and 0.5 < lam < 3.0 and c == 1.0
    h = HM.beam_hinge("W24X84", 360.0, prm)
    assert abs(h.Lambda - lam) < 1e-12
    off = _prm(cyclic_deterioration=dict(prm["cyclic_deterioration"], mode="none"))
    assert HM.cyclic_lambda("beam", p, Fye, 360.0, off)[0] == 0.0 and HM.beam_hinge("W24X84", 360.0, off).Lambda == 0.0
    col = HM.column_hinge("W14X311", 162.0, 300.0, prm)
    assert 0 < col.Lambda <= 3.0


def _imk_response(h, path):
    ops.wipe(); ops.model("basic", "-ndm", 1, "-ndf", 1)
    HM.make_imk_material(1, h, 10 * 6 * 29000.0 * 2370.0 / 360.0)
    ops.testUniaxialMaterial(1)
    out = []
    for e in path:
        ops.setStrain(e); out.append(ops.getStress())
    return np.array(out)


def test_imk_monotonic_unchanged_cyclic_deteriorates():
    prm = HM.load_params()
    h_on = HM.beam_hinge("W24X84", 360.0, prm)
    h_off = HM.beam_hinge("W24X84", 360.0, _prm(cyclic_deterioration=dict(prm["cyclic_deterioration"], mode="none")))
    mono = list(np.linspace(0, 1.5 * h_on.a_pl, 300))
    assert np.allclose(_imk_response(h_on, mono), _imk_response(h_off, mono))      # NSP backbone unchanged
    amp = 0.8 * h_on.a_pl + h_on.theta_y
    cyc = []
    for _ in range(6):
        cyc += list(np.linspace(0, amp, 60)) + list(np.linspace(amp, -amp, 120)) + list(np.linspace(-amp, 0, 60))
    on, off = _imk_response(h_on, cyc), _imk_response(h_off, cyc)
    assert on[-240:].max() < 0.97 * off[-240:].max()                                  # last cycle weaker with Lambda


def test_template_groups_and_fallback():
    t = HM.load_params()
    for g in ("brb_axial", "ebf_link", "cyclic_deterioration"):
        assert g in t
    b = t["brb_axial"]
    assert (b["a_over_dy"], b["b_over_dy"], b["c_residual"], b["IO_over_dy"], b["LS_over_dy"], b["CP_over_dy"]) == (13.3, 13.3, 1.0, 3.0, 10.0, 13.3)
    s = t["ebf_link"]["shear"]
    assert (s["a"], s["b"], s["c"], s["IO"], s["LS"], s["CP"]) == (0.15, 0.17, 0.8, 0.005, 0.14, 0.16)
    old = {k: v for k, v in t.items() if k not in ("brb_axial", "ebf_link", "cyclic_deterioration")}   # a pre-NL-02 params file
    g, from_tmpl = HM.param_group(old, "brb_axial")
    assert from_tmpl and g["CP_over_dy"] == 13.3
    sh, fl, info = HM.link_specs("W14X74", 36.0, old)
    assert any("TEMPLATE" in f for f in sh.flags)


def test_physical_theory_brace_buckles_degrades_fractures():
    """NL-10 (NLRHA): HSS brace with camber + Steel02/Fatigue: tension yield ~ Pye, first buckling ~ Pcre (1.14 Fcre A),
    post-buckling compression strength degrades, the brace eventually fractures (tension strength lost)."""
    prm = _prm(); prm["_analysis"] = "nlrha"
    sec, L = "HSS6X6X3/8", 200.0
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes={1: (0., 0., 0.), 2: (L, 0., 0.)}))
    e = dict(tag=5, n1=1, n2=2, etype="Truss", raw=["Truss", 5, 1, 2, 7.58, 1])
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0., 0., 0.); ops.node(2, L, 0., 0.)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 0, 1, 1, 1, 1, 1)
    ops.uniaxialMaterial("Elastic", 1, NM.RIGID_T); ops.uniaxialMaterial("Elastic", 2, NM.RIGID_R)
    hinges, stats = {}, dict(brace_nonlinear=0)
    NM.build_brace(pkg, e, sec, prm, NM.MAT_BASE, hinges, stats, dict(brb={}, links={}, pt_secs={}, pt_builder=None))
    h = hinges[5]; s = h["spec"]
    assert h.get("form") == "physical_theory" and stats["brace_physical_theory"] == 1
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 1.0, 0, 0, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack")
    u, peaks = 0.0, []
    for a in (1, -1, 2, -2, 4, -4, 8, -8, 12):
        tgt = a * s.dT; n = 40; du = (tgt - u) / n; seg = []
        for _ in range(n):
            ok = -1
            for sub in (1, 4, 16):
                ops.integrator("DisplacementControl", 2, 1, du / sub); ops.analysis("Static")
                for alg in (("Newton",), ("KrylovNewton",), ("ModifiedNewton", "-initial")):
                    ops.algorithm(*alg); ops.test("NormDispIncr", 1e-6, 200, 0)
                    ok = ops.analyze(sub)
                    if ok == 0:
                        break
                if ok == 0:
                    break
            if ok != 0:
                break
            u = ops.nodeDisp(2, 1); seg.append(HM.brace_axial_force(5, h))
            assert abs(ops.eleResponse(5, "deformation")[0] - u) < 1e-6        # the monitor truss measures the brace
        peaks.append((a, max(seg) if a > 0 else min(seg)) if seg else (a, 0.0))
    P = dict(peaks)
    assert 0.9 < P[2] / s.Pye_kip < 1.1                                       # tension yield
    assert 0.8 < -P[-1] / s.Pcre_kip < 1.15                                   # first buckling
    assert -P[-2] < 0.9 * -P[-1]                                              # post-buckling degradation
    assert P[12] < 0.5 * s.Pye_kip                                            # fractured (low-cycle fatigue)


def test_degradation_statement():
    from nlrha import model as MD
    st = dict(plasticity="imk", degradation=dict(plasticity="imk", members="IMK hinges: ... deterioration ON (cyclic_deterioration.mode=expressions)",
                                                  braces="Hysteretic truss on the Table C3.4 backbone: ... NO cyclic degradation", brb="BRB (2): ..."))
    d = MD.degradation_statement(HM.load_params(), st)
    assert not d["demonstrated"] and "Buckling braces" in d["text"] and "NOT DEMONSTRATED" in d["text"]
    st["degradation"]["braces"] = "physical-theory fibre braces with Steel02 + Fatigue"
    assert MD.degradation_statement(HM.load_params(), st)["demonstrated"]
    st2 = dict(plasticity="fibre", degradation=dict(plasticity="fibre", members="NONE"))
    assert not MD.degradation_statement(HM.load_params(), st2)["demonstrated"]


# ----------------------------------------------------------------------------- DDM (GMNIA): BRB truss + link shear spring
def _neutral(kind="ebf"):
    from steltic_ddm.ingest import Member, NeutralModel
    nm = NeutralModel(job_dir=".", name="unit")
    nm.nodes = {101: (0., 0., 0.), 301: (240., 0., 0.), 100101: (0., 0., 144.), 100301: (240., 0., 144.), 199999: (120., 0., 144.)}
    # bases pinned for X sway (rotation about Y free), restrained about X / Z so the frame is stable out of plane.
    # (Do not fix DOFs of the diaphragm master: under the Transformation handler that silently drops the diaphragm.)
    nm.fixes = {101: (1, 1, 1, 1, 0, 1), 301: (1, 1, 1, 1, 0, 1)}
    nm.masses = {199999: (1.0, 1.0, 0, 0, 0, 1.0)}
    nm.levels = [0.0, 144.0]
    M = []
    M.append(Member(1, "col", "W14X90", 101, 100101, 2, 0, 0, 26.5, role="lateral_col", dirn="Z"))
    M.append(Member(2, "col", "W14X90", 301, 100301, 2, 0, 0, 26.5, role="lateral_col", dirn="Z"))
    if kind == "ebf":
        nm.nodes.update({101101: (102., 0., 144.), 101301: (138., 0., 144.)})
        M += [Member(3, "beam", "W14X74", 100101, 101101, 3, 1, 0, 21.8, role="roof", dirn="X"),     # pinned at the column,
              Member(4, "beam", "W14X74", 101101, 101301, 3, 0, 0, 21.8, role="roof", dirn="X"),     # continuous through the link
              Member(5, "beam", "W14X74", 101301, 100301, 3, 2, 0, 21.8, role="roof", dirn="X"),
              Member(6, "brace", "HSS8X8X1/2", 101, 101101, 0, 3, 3, 13.5, role="brace", dirn="D"),
              Member(7, "brace", "HSS8X8X1/2", 301, 101301, 0, 3, 3, 13.5, role="brace", dirn="D")]
        cfg, calc = {"Fy": 50.0, "system": "EBF"}, None
    else:
        M += [Member(3, "beam", "W24X84", 100101, 100301, 3, 3, 0, 24.7, role="roof", dirn="X"),
              Member(6, "brace", "BRB-Asc10.0", 101, 100301, 0, 3, 3, 15.0, role="brace", dirn="D")]
        cfg = {"Fy": 50.0, "system": "BRBF"}
        calc = {"members": [{"inputs": {"kind": "brace", "section": "BRB-Asc10.0", "Asc_in2": 10.0, "Fysc_ksi": 38.0, "KF": 1.5}}]}
    nm.members = M; nm.diaphragms = {199999: [100101, 100301]}; nm.cfg = cfg; nm.calc_package = calc
    return nm


def _push_ddm(g, u_max, n=150):
    from steltic_ddm import solver
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(199999, 1.0, 0, 0, 0, 0, 0)
    solver._solver_settings(tol=1e-6, iters=50)
    ops.integrator("DisplacementControl", 199999, 1, u_max / n); ops.analysis("Static")
    V = []
    for _ in range(n):
        if ops.analyze(1) != 0:
            ops.algorithm("KrylovNewton"); assert ops.analyze(1) == 0; ops.algorithm("Newton")
        V.append(ops.getLoadFactor(1))
    return np.array(V)


def test_ddm_ebf_link_shear_spring_limits_capacity():
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm import solver
    nm = _neutral("ebf")
    g = GMNIAModel(nm, nm.cfg, nsub=(2, 2, 2), residual="none", hardening=0.002, rigid_end_offset=False).build().prepare()
    assert set(g.links) == {4}
    sp = [e for e in g.elems if e["kind"] == "link_shear"]
    assert len(sp) == 1
    p = SDB.props("W14X74")
    Vn = 0.6 * 50.0 * (p["d"] - 2 * p["tf"]) * p["tw"]
    assert abs(sp[0]["Vn"] - Vn) < 1e-6
    V = _push_ddm(g, 1.0)
    ys = solver.yield_state(g, 50.0 / 29000.0)
    assert ys[sp[0]["tag"]] > 3.0                                        # link shear has yielded (plastic)
    Vs = ops.eleResponse(sp[0]["tag"], "force")[2]
    assert abs(abs(Vs) / Vn - 1.0) < 0.06                                # spring force at Vn (+ small hardening)
    # storey shear at the link mechanism: V = Vn*L/h for the split-K bay (beam ends pinned) -> capacity is link shear
    assert V.max() < 1.25 * Vn * 240.0 / 144.0
    # elastic (transfer-gate) build keeps the link shear-rigid like the Steltic model
    ge = GMNIAModel(nm, nm.cfg, nsub=(2, 2, 2), residual="none", elastic=True, rigid_end_offset=False).build()
    assert any("RIGID" in n for n in ge.special_log)


def test_ddm_brb_truss_does_not_buckle():
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm import solver
    nm = _neutral("brb")
    g = GMNIAModel(nm, nm.cfg, nsub=(2, 2, 2), residual="none", hardening=0.002, rigid_end_offset=False).build().prepare()
    assert g.nonbuckling == {6}
    brb = [e for e in g.elems if e.get("brb")][0]
    L = math.dist(nm.nodes[101], nm.nodes[100301])
    V = _push_ddm(g, 4.0)
    N = ops.eleResponse(brb["tag"], "axialForce")[0]
    Pysc = 38.0 * 10.0
    assert abs(abs(N) / Pysc - 1.0) < 0.05                               # yield at Fysc*Asc (core area, nominal)
    assert solver.yield_state(g, 50.0 / 29000.0)[brb["tag"]] > 1.0
    assert solver.brace_state(g) == {}                                   # BRB excluded from the buckling census
    # elastic stiffness = the HR truss (A = KF*Asc = 15 in2)
    assert abs(brb["L"] - L) < 1e-9
