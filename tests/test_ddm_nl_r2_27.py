"""NL-R2-27: DDM sweeps stopping on 'step size exhausted' below the true capacity.

Root causes fixed (see the commit message for the Ex13 / Ex30 / Ex29 evidence):
  * the sweep gave up when displacement control failed at a BIFURCATION the control DOF does not see (Ex13: a beam
    twisting in inelastic lateral-torsional buckling) although equilibrium existed at higher lambda -- now a
    load-controlled bridge decides, and the sweep says whether it ended at a limit point;
  * the GMNIA beams had no lateral / torsional bracing although the HR design (and the building) braces them with the
    deck and infill (Lb in the calc package) -- they buckled laterally at ~0.7 of the design load with the Lehigh
    residual stresses;
  * the HSS (and ECCS) residual-stress patterns were not self-equilibrating (net axial force locked in at zero load).
"""
import os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

ENGINE = os.environ.get("STELTIC_ENGINE_DIR")
needs_engine = pytest.mark.skipif(not ENGINE, reason="STELTIC_ENGINE_DIR not set")


# ---------------------------------------------------------------------------------------------- residual stresses
class _Rec:
    """Stand-in for openseespy: records materials (residual stress per tag) and fibres."""
    def __init__(self):
        self.sig, self.fibres, self.wrap = {}, [], {}

    def uniaxialMaterial(self, kind, tag, *a):
        if kind == "InitStressMaterial":
            self.sig[tag] = a[1]
        else:
            self.sig.setdefault(tag, 0.0)

    def section(self, *a):
        pass

    def fiber(self, y, z, A, mat):
        self.fibres.append((y, z, A, self.sig[mat]))


@pytest.mark.parametrize("label,residual", [("W14X90", "lehigh"), ("W14X90", "eccs"), ("W24X68", "eccs"),
                                            ("HSS7X7X1/2", "cf_hss_membrane"), ("HSS8X4X1/2", "cf_hss_membrane")])
def test_residual_patterns_are_self_equilibrating(label, residual):
    """No net axial force or moment at zero strain. The old HSS membrane pattern (-0.15 + 0.30 frac^2) locked a net
    compression of ~0.05 Fy A into every brace (Ex13: brace -27 kip at lambda = 0); the old ECCS web (+a uniform) a net
    tension a Fy Aw into every W-shape."""
    from steltic_ddm.sections_fiber import FiberSectionBuilder
    rec = _Rec()
    b = FiberSectionBuilder(rec, Fy=50.0, residual=residual)
    if label.startswith("HSS"):
        b.hss_rect(1, label, residual=residual)
    else:
        b.w_shape(1, label, axis="y")
    A = sum(f[2] for f in rec.fibres)
    N = sum(f[2] * f[3] for f in rec.fibres)
    My = sum(f[2] * f[3] * f[0] for f in rec.fibres)
    Mz = sum(f[2] * f[3] * f[1] for f in rec.fibres)
    assert any(abs(f[3]) > 1.0 for f in rec.fibres)              # a residual pattern is really there
    assert abs(N) < 1e-9 * 50.0 * A
    assert abs(My) < 1e-9 * 50.0 * A * 10 and abs(Mz) < 1e-9 * 50.0 * A * 10


def test_hss_residual_stress_does_not_load_a_restrained_member():
    """A fixed-fixed HSS strut with the membrane residual pattern, no load: zero axial force (old: ~ -0.05 Fy A)."""
    import openseespy.opensees as ops
    from steltic_ddm.sections_fiber import FiberSectionBuilder
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0); ops.node(2, 0.0, 0.0, 72.0); ops.node(3, 0.0, 0.0, 144.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(3, 1, 1, 1, 1, 1, 1)
    ops.geomTransf("Corotational", 1, 1.0, 0.0, 0.0)
    props = FiberSectionBuilder(ops, Fy=50.0, residual="cf_hss_membrane").hss_rect(1, "HSS7X7X1/2", residual="cf_hss_membrane")
    ops.beamIntegration("Lobatto", 1, 1, 5)
    ops.element("forceBeamColumn", 1, 1, 2, 1, 1); ops.element("forceBeamColumn", 2, 2, 3, 1, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack"); ops.test("NormDispIncr", 1e-10, 25, 0)
    ops.algorithm("Newton"); ops.integrator("LoadControl", 0.0); ops.analysis("Static")
    assert ops.analyze(1) == 0
    N = ops.eleResponse(1, "basicForce")[0]
    assert abs(N) < 1e-6 * 50.0 * props["A"]
    ops.wipe()


# ---------------------------------------------------------------------------------------------- deck / infill bracing
def _cp(Lb, roles=("floor", "roof"), section="W18X35"):
    return dict(members=[dict(id="%s-%s" % (r, section), inputs=dict(role=r, section=section, Lb_in=Lb)) for r in roles])


@needs_engine
def test_beams_braced_where_the_hr_design_braces_them():
    """HR Lb = 0 (deck) -> every interior chain node braced; Lb = 100 on a 300-in girder -> chain nodes AT the third
    points, both braced (lateral + twist only); no Lb or no diaphragm -> unbraced, chain unchanged."""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    import openseespy.opensees as ops
    from test_ddm_r6 import _hr_box
    from steltic_ddm.model_gmnia import GMNIAModel, BRACE_ELE0, BRACE_NODE0
    from steltic_ddm import loads

    nm, cfg = _hr_box(SX=300.0, SY=300.0)                          # deck spans X, infill @ 90 in
    g0 = GMNIAModel(nm, cfg, nsub=(2, 2, 2), rigid_end_offset=0.05)
    g0.build().prepare()
    assert g0.braced == {} and not any(t >= BRACE_ELE0 for t in ops.getEleTags())
    chain0 = dict(g0.sub_nodes)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    W0 = g0.apply_gravity(1.2, 1.6, 0.0, loads.present_sets(nm))

    nm.calc_package = _cp(100.0)
    g = GMNIAModel(nm, cfg, nsub=(2, 2, 2), rigid_end_offset=0.05)
    g.build().prepare()
    beams = [m for m in nm.members if m.kind == "beam"]
    assert set(g.braced) == {m.tag for m in beams}
    for m in beams:
        mode, xs, lb = g.braced[m.tag]
        assert mode == "discrete" and lb == 100.0 and xs == (100.0, 200.0)
        inner = g.sub_nodes[m.tag][2:-2]                           # [end, stub i, ..., stub j, end]
        assert len(inner) == 2
        p1 = ops.nodeCoord(m.n1)
        for n, x in zip(inner, xs):
            assert abs(sum((a - b) ** 2 for a, b in zip(ops.nodeCoord(n), p1)) ** 0.5 - x) < 1e-6
    braces = [t for t in ops.getEleTags() if t >= BRACE_ELE0]
    assert len(braces) == 2 * len(beams)
    deck = [t for t in ops.getNodeTags() if t >= BRACE_NODE0]
    assert len(deck) == len(braces)
    # the gravity is unchanged by the re-meshed chains (applied by position, NL-R2-03)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    assert abs(g.apply_gravity(1.2, 1.6, 0.0, loads.present_sets(nm)) - W0) < 1e-9 * W0
    assert any("beam bracing (NL-R2-27)" in str(row[-1]) for row in g.builder.log)

    # continuous bracing (deck): regular chain, every interior node braced
    nm.calc_package = _cp(0.0)
    g2 = GMNIAModel(nm, cfg, nsub=(2, 2, 2), rigid_end_offset=0.05)
    g2.build().prepare()
    assert all(v[0] == "continuous" for v in g2.braced.values()) and len(g2.braced) == len(beams)
    assert all(g2.sub_nodes[m.tag] == chain0[m.tag] for m in beams)
    assert len([t for t in ops.getEleTags() if t >= BRACE_ELE0]) == len(beams)     # nsub 2 -> the mid node


@needs_engine
def test_brace_restrains_only_lateral_and_twist():
    """The deck brace must not carry gravity or axial force: under gravity alone the brace forces are ~0 and the
    braced beam deflects like the unbraced one."""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    import openseespy.opensees as ops
    from test_ddm_r6 import _hr_box
    from steltic_ddm.model_gmnia import GMNIAModel, BRACE_ELE0
    from steltic_ddm import loads, solver

    def mid_defl(cp):
        nm, cfg = _hr_box(SX=300.0, SY=300.0)
        nm.calc_package = cp
        g = GMNIAModel(nm, cfg, nsub=(2, 2, 2), rigid_end_offset=0.05, residual="none")
        g.build().prepare()
        ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
        g.apply_gravity(1.2, 1.6, 0.0, loads.present_sets(nm))
        solver._solver_settings(); ops.integrator("LoadControl", 0.5); ops.analysis("Static")
        assert ops.analyze(1) == 0
        beam = [m for m in nm.members if m.kind == "beam" and m.dirn == "Y"][0]
        ch = g.sub_nodes[beam.tag]
        dz = min(ops.nodeDisp(n, 3) for n in ch)
        fb = [max(abs(v) for v in ops.eleResponse(t, "force")) for t in ops.getEleTags() if t >= BRACE_ELE0]
        return dz, fb

    dz0, _ = mid_defl(None)
    dz1, fb = mid_defl(_cp(0.0))
    assert fb and max(fb) < 0.05                                    # kip / kip-in: practically nothing
    assert abs(dz1 - dz0) < 1e-3 * abs(dz0)


def test_brace_plan_rules():
    """Lb <= L/8 -> continuous; floor(L/Lb) >= 2 -> discrete brace points (spacing never below Lb); Lb ~ L -> unbraced; no diaphragm or no
    HR Lb -> unbraced; conflicting groups -> the largest Lb."""
    from test_ddm_r6 import _frame
    from steltic_ddm.model_gmnia import GMNIAModel
    nm, cfg = _frame()
    beam = [m for m in nm.members if m.kind == "beam"][0]
    g = GMNIAModel(nm, cfg)
    g._grav_geo = {"stub": True}
    nm.calc_package = _cp(12.0)
    assert g._brace_plan(beam, 300.0, 12.0)[0] == "continuous"
    g._lb_map = None; nm.calc_package = _cp(100.0)
    assert g._brace_plan(beam, 300.0, 12.0) == ("discrete", [100.0, 200.0], 100.0, True)
    g._lb_map = None; nm.calc_package = _cp(250.0)
    assert g._brace_plan(beam, 300.0, 12.0) is None
    # review: L/Lb = 2.6 -> two spaces of 150 in (>= Lb 115.4), not three of 100 in (0.87 Lb, the old round())
    g._lb_map = None; nm.calc_package = _cp(300.0 / 2.6)
    assert g._brace_plan(beam, 300.0, 12.0)[:2] == ("discrete", [150.0])
    g._lb_map = None; nm.calc_package = _cp(160.0)                 # L/Lb 1.9: unbraced (spacing 300 >= 160), not 150
    assert g._brace_plan(beam, 300.0, 12.0) is None
    g._lb_map = None; nm.calc_package = None
    assert g._brace_plan(beam, 300.0, 12.0) is None
    g._lb_map = None
    nm.calc_package = dict(members=[dict(inputs=dict(role=beam.role, section="W18X35", Lb_in=0.0)),
                                    dict(inputs=dict(role=beam.role, section="W18X35", Lb_in=150.0))])
    assert g._brace_plan(beam, 300.0, 12.0) == ("discrete", [150.0], 150.0, True)
    assert g._lb_conflicts
    g.beam_bracing = False
    assert g._brace_plan(beam, 300.0, 12.0) is None
    g.beam_bracing = True; g._lb_map = None; nm.calc_package = _cp(0.0)
    g._slave_master = {}                                           # no diaphragm -> no deck
    assert g._brace_plan(beam, 300.0, 12.0) is None


# ---------------------------------------------------------------------------------------------- solver: bridge / limit
class _Gate:
    """Makes ops.analyze fail for DisplacementControl above lam_dc and for LoadControl above lam_lc (a bifurcation the
    control DOF does not see / a genuine limit point), on top of a real small model."""
    def __init__(self, solver, lam_dc, lam_lc):
        self.ops = solver.ops
        self.real_an, self.real_int = self.ops.analyze, self.ops.integrator
        self.kind, self.arg, self.lam_dc, self.lam_lc = None, None, lam_dc, lam_lc

    def integrator(self, kind, *a):
        self.kind, self.arg = kind, a
        return self.real_int(kind, *a)

    def analyze(self, n=1):
        lam = self.ops.getLoadFactor(1)
        if self.kind == "DisplacementControl" and lam > self.lam_dc:
            return -3
        if self.kind == "LoadControl" and lam + (self.arg[0] if self.arg else 0.0) > self.lam_lc:
            return -3
        return self.real_an(n)


def _sweep_gated(monkeypatch, lam_dc, lam_lc, **kw):
    from test_ddm_r6 import _frame
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm import solver
    nm, cfg = _frame()
    g = GMNIAModel(nm, cfg, nsub=(2, 2, 2))
    gate = _Gate(solver, lam_dc, lam_lc)
    monkeypatch.setattr(solver.ops, "analyze", gate.analyze)
    monkeypatch.setattr(solver.ops, "integrator", gate.integrator)
    combo = ("1.2D+1.0WX+", 1.2, 0.0, 0.0, {1: (20.0, 0.0, 0.0)}, False)
    pres = {1: {(1, 1), (1, 2), (2, 1), (2, 2)}}
    r = solver.sweep(g, combo, pres, dlam=0.05, verbose=False, **kw)
    return r, solver.classify(r, g)


def test_bifurcation_is_bridged_by_load_control(monkeypatch):
    """Displacement control fails above lambda 0.3, load control converges: the old sweep stopped at 0.3
    ('step size exhausted'); now it continues in load control and lambda keeps rising (not a limit point)."""
    r, c = _sweep_gated(monkeypatch, lam_dc=0.3, lam_lc=99.0, max_steps=30)
    assert r["bridges"] >= 1
    assert any("load control converged" in l for l in r["log"])
    assert r["lambda_u"] > 0.6
    assert r["termination"]["kind"] == "budget"                     # still rising when the step budget ran out
    lams = [l for l, _ in r["hist"]]
    assert all(b > a for a, b in zip(lams, lams[1:]))
    from steltic_ddm import phi_s
    assert phi_s.is_lower_bound(r)


def test_genuine_limit_is_reported_as_such(monkeypatch):
    """Neither control converges above lambda 0.42: the sweep ends there, at a reported limit (not a solver stop)."""
    r, c = _sweep_gated(monkeypatch, lam_dc=0.42, lam_lc=0.42, max_steps=60)
    t = r["termination"]
    assert t["kind"] == "limit" and abs(t["lam"] - r["lambda_u"]) < 1e-9
    assert 0.35 < r["lambda_u"] < 0.42 + 0.05                      # the last DC step may start just below 0.42
    assert "no equilibrium above lambda" in t["detail"]
    assert any("step size exhausted --" in l for l in r["log"])
    from steltic_ddm import phi_s
    assert not phi_s.is_lower_bound(r)
    assert c["cls"] != "numerical"


def test_lower_bound_never_fails():
    """A lambda_u that is only a lower bound (solver / budget stop while rising) passes when phi*lambda >= 1 and is
    otherwise NOT EVALUATED -- never a FAIL (NL-R2-02 rule extended); a limit keeps PASS / FAIL."""
    from steltic_ddm import phi_s
    assert phi_s.check(0.85, 1.5, "ductile", lower_bound=True) == (1.275, "PASS")
    assert phi_s.check(0.85, 0.9, "ductile", lower_bound=True) == (None, "NOT EVALUATED")
    assert phi_s.check(0.85, 0.9, "ductile") == (0.765, "FAIL")
    assert phi_s.is_lower_bound(dict(termination=dict(kind="numerical")))
    assert phi_s.is_lower_bound(dict(termination=dict(kind="budget")))
    for k in ("limit", "post-peak", "plateau", "disp-cap"):
        assert not phi_s.is_lower_bound(dict(termination=dict(kind=k)))
    assert not phi_s.is_lower_bound(dict())                          # results written before NL-R2-27


# ---------------------------------------------------------------------------------------------- NL-R2-28 (DDM): X crossings
def test_x_pairs_finds_the_two_diagonals_of_a_bay_only():
    from steltic_ddm.model_gmnia import GMNIAModel
    br = [(1, (0, 0, 0), (600, 0, 384)), (2, (600, 0, 0), (0, 0, 384)),          # X in bay 0 (XZ plane)
          (3, (600, 0, 0), (1200, 0, 384)),                                       # single diagonal (no partner)
          (4, (0, 600, 0), (0, 1200, 384)), (5, (0, 1200, 0), (0, 600, 384)),     # X in a Y frame
          (6, (0, 0, 384), (600, 0, 768))]                                        # storey above: no shared mid
    assert sorted(tuple(sorted(p)) for p in GMNIAModel.x_pairs(br)) == [(1, 2), (4, 5)]
    # chevron halves meeting at the beam mid-span share an END, not their mid-lengths -> no tie
    assert GMNIAModel.x_pairs([(7, (0, 0, 0), (300, 0, 384)), (8, (600, 0, 0), (300, 0, 384))]) == []


class _NM:
    def __init__(self, members):
        self.calc_package = {"members": [{"inputs": m} for m in members]}


@pytest.mark.parametrize("inp,expected", [
    (dict(role="brace", section="HSS5-1/2X5-1/2X3/8", Lc_in=356.2, Lwp_in=712.4,
          configuration="X-bracing, diagonals connected at the crossing"), {"HSS5-1/2X5-1/2X3/8"}),
    (dict(role="brace", section="HSS5X5X3/8", Lc_in=201.2, length_in=402.5, workpoint_length_in=402.5,
          Lc_note="X-bracing connected at the crossing: design Lc = 0.5 x work-point length"), {"HSS5X5X3/8"}),
    (dict(role="brace", section="HSS7X7X1/2", Lc_in=225.2, length_in=225.2, configuration="chevron"), set()),
    (dict(role="brace", section="HSS6X6X1/2", Lc_in=700.0, Lwp_in=712.4, configuration="X, not connected at the crossing"), set()),
])
def test_hr_x_crossing_sections_follow_the_calc_package(inp, expected):
    """Only braces the HR design treats as X-braces connected at the crossing (Lc <= 0.6 Lwp) are tied (Ex21 / Ex30
    calc packages); chevrons (Ex13) and full-length X designs are left alone."""
    from steltic_ddm.model_gmnia import GMNIAModel
    g = GMNIAModel.__new__(GMNIAModel)
    g.nm = _NM([inp])
    assert g._hr_x_crossing_sections() == expected


def _x_peak(tie, nsub=8, residual="none"):
    """Two pin-ended HSS5X5X3/8 diagonals of a 600 x 384 in bay (all end rotations free; torsion held by a spring), the
    lower ends fixed in translation, the upper ends pushed together in X: peak compression (kip) in diagonal 1-2.
    The bows are the GMNIA's (GMNIAModel.bow_offset, L/1000; tied diagonals with the half-length seed)."""
    import math
    import openseespy.opensees as ops
    from steltic_ddm.sections_fiber import FiberSectionBuilder
    from steltic_ddm.model_gmnia import GMNIAModel
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    H, B = 384.0, 600.0
    ends = {1: (0, 0, 0), 2: (B, 0, H), 3: (B, 0, 0), 4: (0, 0, H)}
    for t, c in ends.items():
        ops.node(t, *c)
    for t in (1, 3):
        ops.fix(t, 1, 1, 1, 0, 0, 0)
    for t in (2, 4):
        ops.fix(t, 0, 1, 1, 0, 0, 0)
    ops.uniaxialMaterial("Elastic", 99, 1e10)
    for t, (a, b) in ((1, (1, 2)), (3, (3, 4)), (2, (1, 2)), (4, (3, 4))):     # torsion only (pins otherwise)
        pa, pb = ends[a], ends[b]
        ops.node(900 + t, *ends[t]); ops.fix(900 + t, 1, 1, 1, 1, 1, 1)
        ops.element("zeroLength", 900 + t, 900 + t, t, "-mat", 99, "-dir", 4, "-orient",
                    *[pb[q] - pa[q] for q in range(3)], 0, 1, 0)
    ops.geomTransf("Corotational", 1, 0.0, 1.0, 0.0)
    FiberSectionBuilder(ops, Fy=50.0, residual=residual).hss_rect(1, "HSS5X5X3/8", residual=residual)
    ops.beamIntegration("Lobatto", 1, 1, 5)
    mids, et = {}, 0
    for k, (a, b) in enumerate(((1, 2), (3, 4))):
        pa, pb = ends[a], ends[b]
        L = math.dist(pa, pb)
        chain = [a]
        for s in range(1, nsub):
            f = s / nsub
            t = 100 + 20 * k + s
            ops.node(t, pa[0] + (pb[0] - pa[0]) * f, GMNIAModel.bow_offset(f, L, 0.001, tied=tie), pa[2] + (pb[2] - pa[2]) * f)
            chain.append(t)
        chain.append(b)
        mids[k] = chain[nsub // 2]
        for s in range(nsub):
            et += 1
            ops.element("forceBeamColumn", et, chain[s], chain[s + 1], 1, 1)
    if tie:
        ops.equalDOF(mids[0], mids[1], 1, 2, 3)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    ops.load(2, -1.0, 0, 0, 0, 0, 0); ops.load(4, -1.0, 0, 0, 0, 0, 0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 2, 1, -0.005); ops.analysis("Static")
    nmin = 0.0
    for _ in range(400):
        if ops.analyze(1) != 0:
            break
        nmin = min(nmin, ops.eleResponse(1, "basicForce")[0])
    return -nmin


def test_x_crossing_tie_gives_the_half_length_capacity_not_more():
    """Review D2: pinned X, HSS5X5X3/8 (r 1.87 in), diagonal 712 in. Untied: ~ Euler on the full length (12.2 kip).
    Tied: the crossing halves the buckling length -- the capacity must be close to the half-length Euler load (48.8) and
    above the AISC E3 Pn on the half length (42.8), NOT 81 kip (what the full-length bow alone gave: it is symmetric about
    the crossing and never seeds the half-length mode)."""
    import math
    from pushover import sections_db as SDB
    p = SDB.props("HSS5X5X3/8")
    L = math.hypot(600.0, 384.0)
    def euler(Lc):
        return math.pi ** 2 * 29000.0 / (Lc / p["rx"]) ** 2 * p["A"]
    e3_half = 0.877 * euler(L / 2)                         # KL/r 190 > 4.71 sqrt(E/Fy): elastic range, E3-3
    free, tied = _x_peak(False), _x_peak(True)
    assert free <= 1.1 * euler(L)
    assert e3_half <= tied <= 1.1 * euler(L / 2)
    assert tied > 3.0 * free


def test_tied_diagonal_bow_has_the_half_length_seed():
    from steltic_ddm.model_gmnia import GMNIAModel
    L = 712.0
    assert abs(GMNIAModel.bow_offset(0.5, L, 0.001, tied=True) - 0.712) < 1e-9          # crossing: the full bow only
    q1 = GMNIAModel.bow_offset(0.25, L, 0.001, tied=True) - GMNIAModel.bow_offset(0.25, L, 0.001)
    q3 = GMNIAModel.bow_offset(0.75, L, 0.001, tied=True) - GMNIAModel.bow_offset(0.75, L, 0.001)
    assert abs(q1 - 0.356) < 1e-9 and abs(q3 + 0.356) < 1e-9                             # (L/2)/1000, antisymmetric


# ---------------------------------------------------------------------------------------------- review D1: bridge vs limit
def _von_mises(L=100.0, h=10.0, EA=1.0e4):
    import openseespy.opensees as ops
    ops.wipe(); ops.model("basic", "-ndm", 2, "-ndf", 2)
    ops.node(1, 0, 0); ops.node(2, L, h); ops.node(3, 2 * L, 0)
    ops.fix(1, 1, 1); ops.fix(3, 1, 1); ops.fix(2, 1, 0)
    ops.uniaxialMaterial("Elastic", 1, EA)
    ops.element("corotTruss", 1, 1, 2, 1.0, 1); ops.element("corotTruss", 2, 2, 3, 1.0, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 0.0, -1.0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-6, 25, 0); ops.algorithm("Newton")
    return ops


def _vm_residual(ops, L=100.0, h=10.0, EA=1.0e4):
    import math
    lam, v = ops.getLoadFactor(1), ops.nodeDisp(2, 2)
    y = h + v; Lc = math.hypot(L, y); L0 = math.hypot(L, h)
    return -2 * EA * (Lc - L0) / L0 * y / Lc - lam


def _vm_limit_and_slope():
    ops = _von_mises(); ops.integrator("LoadControl", 0.05); ops.analysis("Static"); ops.analyze(1)
    slope = ops.nodeDisp(2, 2) / 0.05
    ops = _von_mises(); ops.integrator("DisplacementControl", 2, 2, -0.05); ops.analysis("Static")
    lim = 0.0
    for _ in range(400):
        assert ops.analyze(1) == 0
        lim = max(lim, ops.getLoadFactor(1))
    return lim, slope


def _vm_up_to(frac):
    ops = _von_mises(); ops.integrator("DisplacementControl", 2, 2, -0.05); ops.analysis("Static")
    lim, _ = _VM
    while ops.getLoadFactor(1) < (1 - frac) * lim:
        assert ops.analyze(1) == 0
    return ops


_VM = None


@pytest.mark.parametrize("frac", [0.0005, 0.002, 0.01, 0.03])
@pytest.mark.parametrize("dl_frac", [0.02, 0.05, 0.2])
def test_bridge_never_reports_above_a_snap_through_limit(frac, dl_frac):
    """Von Mises shallow truss (true limit 3.8108). Started 0.05-3 % below the limit, the old bridge (0.5 dlam first,
    NormDispIncr with Krylov / line search) reported equilibrium at 1.001-1.016 x the limit, on the snapped branch
    (v -4 -> -21.6) or out of equilibrium. Now an accepted bridge step is an equilibrium state below the limit; a step
    that only converges by snapping is rejected with the reason."""
    global _VM
    from steltic_ddm import solver as S
    if _VM is None:
        _VM = _vm_limit_and_slope()
    lim, slope = _VM
    assert abs(lim - 3.8108) < 2e-3
    ops = _vm_up_to(frac)
    dlam = dl_frac * lim
    dl, why = S._bridge(dlam, 2, 2, slope, -1)
    if dl is not None:
        assert ops.getLoadFactor(1) <= lim * (1 + 1e-9)
        assert abs(_vm_residual(ops)) < 1e-4
        assert ops.nodeDisp(2, 2) > -8.0                              # still on the near (pre-snap) branch
    # continuing in load control (as the sweep does) never passes the limit either
    while dl is not None:
        dl, why = S._bridge(dlam, 2, 2, slope, -1)
        if dl is not None:
            assert ops.getLoadFactor(1) <= lim * (1 + 1e-9) and abs(_vm_residual(ops)) < 1e-4
    if why is not None:
        assert "snap" in why


def test_gmnia_ties_x_diagonals_with_seed_and_fine_chain():
    """Model level (review D2): an X of HSS5X5X3/8 in the X bay of the R6 one-storey frame, calc package 'connected at the
    crossing' -> the pair is tied at coincident mid nodes, each diagonal has >= 8 sub-elements (default nsub 4) and the
    quarter nodes carry the antisymmetric half-length seed."""
    import math
    import openseespy.opensees as ops
    from test_ddm_r6 import _frame
    from steltic_ddm.ingest import Member
    from steltic_ddm.model_gmnia import GMNIAModel, TIED_BRACE_NSUB
    nm, cfg = _frame()
    tg = max(m.tag for m in nm.members) + 1
    nm.members.append(Member(tg, "brace", "HSS5X5X3/8", 101, 100201, transf=4, dirn="X"))
    nm.members.append(Member(tg + 1, "brace", "HSS5X5X3/8", 201, 100101, transf=4, dirn="X"))
    L = math.hypot(300.0, 144.0)
    nm.calc_package = {"members": [{"inputs": dict(role="brace", section="HSS5X5X3/8", Lc_in=L / 2, Lwp_in=L,
                                                   configuration="X-bracing, diagonals connected at the crossing")}]}
    g = GMNIAModel(nm, cfg, nsub=(2, 2, 4)).build()
    assert g.x_ties == {tg: tg + 1, tg + 1: tg}
    for t in (tg, tg + 1):
        assert len(g.sub_nodes[t]) - 1 == TIED_BRACE_NSUB
    ma, mb = g.sub_nodes[tg][TIED_BRACE_NSUB // 2], g.sub_nodes[tg + 1][TIED_BRACE_NSUB // 2]
    assert math.dist(ops.nodeCoord(ma), ops.nodeCoord(mb)) < 1e-6
    q1 = ops.nodeCoord(g.sub_nodes[tg][TIED_BRACE_NSUB // 4])
    assert abs(abs(q1[1]) - 0.001 * L * (math.sin(math.pi / 4) + 0.5)) < 1e-6        # full bow + (L/2)/1000 seed
    assert any("X-brace crossings" in str(r[4]) for r in g.builder.log)
    # without the calc-package statement nothing is tied and the chains keep the default count
    nm.calc_package = {}
    g = GMNIAModel(nm, cfg, nsub=(2, 2, 4)).build()
    assert g.x_ties == {} and len(g.sub_nodes[tg]) - 1 == 4
