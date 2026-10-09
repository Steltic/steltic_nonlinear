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
    """Lb <= L/8 -> continuous; round(L/Lb) >= 2 -> discrete brace points; Lb ~ L -> unbraced; no diaphragm or no
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


def test_x_crossing_tie_raises_the_compression_diagonal_buckling_load():
    """Two pin-ended HSS diagonals of a 600 x 384 in bay, the lower ends fixed in translation, the upper ends pushed
    so that one diagonal is in compression and the other in tension (end rotations about X, Z held): with the crossing
    tied (mid nodes share their
    translations) the compression diagonal carries ~4x the force it carries untied (half vs full buckling length;
    measured 216 vs 58 kip)."""
    import math
    import openseespy.opensees as ops
    from steltic_ddm.sections_fiber import FiberSectionBuilder

    def run(tie):
        ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
        H, B = 384.0, 600.0
        ends = {1: (0, 0, 0), 2: (B, 0, H), 3: (B, 0, 0), 4: (0, 0, H)}
        for t, c in ends.items():
            ops.node(t, *c)
        for t in (1, 3):
            ops.fix(t, 1, 1, 1, 1, 0, 1)
        for t in (2, 4):                         # top: pushed together in X (axial shortening of 1-2, lengthening of 3-4)
            ops.fix(t, 0, 1, 1, 1, 0, 1)
        ops.geomTransf("Corotational", 1, 0.0, 1.0, 0.0)
        FiberSectionBuilder(ops, Fy=50.0, residual="none").hss_rect(1, "HSS5X5X3/8", residual="none")
        ops.beamIntegration("Lobatto", 1, 1, 5)
        mids, et = {}, 0
        for k, (a, b) in enumerate(((1, 2), (3, 4))):
            pa, pb = ends[a], ends[b]
            chain = [a]
            for s in range(1, 4):
                f = s / 4.0
                off = 0.001 * math.dist(pa, pb) * math.sin(math.pi * f)
                t = 100 + 10 * k + s
                ops.node(t, pa[0] + (pb[0] - pa[0]) * f, off, pa[2] + (pb[2] - pa[2]) * f)
                chain.append(t)
            chain.append(b)
            mids[k] = chain[2]
            for s in range(4):
                et += 1
                ops.element("forceBeamColumn", et, chain[s], chain[s + 1], 1, 1)
        if tie:
            ops.equalDOF(mids[0], mids[1], 1, 2, 3)
        ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
        ops.load(2, -1.0, 0, 0, 0, 0, 0); ops.load(4, -1.0, 0, 0, 0, 0, 0)
        ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
        ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
        ops.integrator("DisplacementControl", 2, 1, -0.01); ops.analysis("Static")
        nmin = 0.0
        for _ in range(150):
            if ops.analyze(1) != 0:
                break
            nmin = min(nmin, ops.eleResponse(1, "basicForce")[0])
        return -nmin                              # peak compression in the 1-2 diagonal (kip)

    free, tied = run(False), run(True)
    assert tied > 2.5 * free
