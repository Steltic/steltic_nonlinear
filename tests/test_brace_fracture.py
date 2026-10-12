"""NL-R2-20 (round 4): a physical-theory brace whose section fractures through is removed from the NLRHA model
(nlrha.brace_fracture). Run: PYTHONPATH=. python -m pytest tests/test_brace_fracture.py -q"""
import copy, math, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openseespy.opensees as ops
from nlrha import brace_fracture as BF
from pushover import hinge_models as HM, nonlinear_model as NM

SEC, L = "HSS6X6X3/8", 200.0
K_AUX = 0.05                                     # kip/in: a weak elastic tie keeps the static problem defined after removal


def _prm():
    prm = copy.deepcopy(HM.load_params()); prm["_analysis"] = "nlrha"
    return prm


def _single():
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes={1: (0., 0., 0.), 2: (L, 0., 0.)}))
    e = dict(tag=5, n1=1, n2=2, etype="Truss", raw=["Truss", 5, 1, 2, 7.58, 1])
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0., 0., 0.); ops.node(2, L, 0., 0.)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 0, 1, 1, 1, 1, 1)
    ops.mass(2, 1.0, 1.0, 1.0, 0., 0., 0.)
    hinges, stats = {}, dict(brace_nonlinear=0)
    NM.build_brace(pkg, e, SEC, _prm(), NM.MAT_BASE, hinges, stats, dict(brb={}, links={}, pt_secs={}, pt_builder=None))
    ops.uniaxialMaterial("Elastic", 9, K_AUX * L)
    ops.element("Truss", 9, 1, 2, 1.0, 9)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 1.0, 0, 0, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack")
    return hinges


def _cycle(hinges, amps, monitor=None, n=40):
    """Displacement-controlled cycles at node 2 (multiples of dT); monitor.check after every converged step.
    Returns the node-2 force history (brace + weak tie) and the fracture events."""
    s = hinges[5]["spec"]; u, hist = ops.nodeDisp(2, 1), []
    for a in amps:
        du = (a * s.dT - u) / n
        for _ in range(n):
            ok = -1
            for sub in (1, 4, 16):
                ops.integrator("DisplacementControl", 2, 1, du / sub); ops.analysis("Static")
                for alg in (("Newton",), ("KrylovNewton",)):
                    ops.algorithm(*alg); ops.test("NormDispIncr", 1e-6, 200, 0)
                    ok = ops.analyze(sub)
                    if ok == 0:
                        break
                if ok == 0:
                    break
            assert ok == 0
            u = ops.nodeDisp(2, 1)
            if monitor is not None and monitor.check(ops.getTime()):
                ops.wipeAnalysis(); ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack")   # as nlrha.run
            ops.reactions(); hist.append((u, -ops.nodeReaction(1, 1), len(monitor.events) if monitor else 0))
    return hist


def test_builder_records_the_buckling_unit():
    h = _single()[5]
    (u,) = h["pt_units"]
    assert u["segs"] == h["segments"] and len(u["pins"]) == 2 and u["ends"] == (1, 2)
    assert all(n >= NM.PT_NODE_BASE for n in u["nodes"]) and len(u["nodes"]) == len(u["segs"]) + 1
    tags = set(ops.getEleTags())
    assert set(u["segs"]) <= tags and set(u["pins"]) <= tags and h["nip"] >= 3


def test_no_fracture_is_bit_identical():
    """Below fracture the monitor only reads responses: the history is identical with and without it."""
    amps = (1, -1, 2, -2)
    ref = _cycle(_single(), amps)
    hinges = _single(); mon = BF.FractureMonitor(hinges)
    got = _cycle(hinges, amps, mon)
    assert got == ref and not mon.events and mon.watch            # buckled (watched), not fractured


def test_fractured_brace_is_removed_and_carries_nothing():
    hinges = _single(); mon = BF.FractureMonitor(hinges); h = hinges[5]
    hist = []
    for a in (1, -1, 2, -2, 4, -4) + (8, -8) * 3 + (12, -12) * 6:
        hist += _cycle(hinges, (a,), mon)
        if mon.events and len(hist) - next(i for i, x in enumerate(hist) if x[2]) > 60:
            break
    assert len(mon.events) == 1, mon.events
    ev = mon.events[0]
    assert ev["brace"] == 5 and ev["whole_brace"] and h["fractured"] and h["pt_units"][0]["removed"]
    tags = set(ops.getEleTags()); nodes = set(ops.getNodeTags())
    assert not (set(h["segments"]) & tags) and not (set(h["pt_units"][0]["pins"]) & tags)
    assert not (set(h["pt_units"][0]["nodes"]) & nodes) and 5 in tags        # the monitor truss stays
    assert HM.brace_axial_force(5, h) == 0.0
    # chain mass moved to the attach nodes (node 2 had 1.0)
    assert ops.nodeMass(2)[0] > 1.0 and abs(ops.nodeMass(2)[0] - 1.0 - 0.5 * ev["mass_moved"]) < 1e-12
    i0 = next(i for i, x in enumerate(hist) if x[2])           # the step that removed it
    before = [f for u, f, n in hist[:i0]]; after = hist[i0 + 1:]  # after the removal only the weak tie resists
    assert len(after) > 20 and max(before) > 0.9 * h["spec"].Pye_kip
    assert all(abs(f - K_AUX * u) < 1e-6 * max(1.0, abs(u)) for u, f, n in after), after[:3]
    assert "fractured through" in mon.summary() and "5 (HSS6X6X3/8" in mon.summary()


def _x_pair():
    """Two diagonals of one bay crossing at mid-height (X connected at the crossing)."""
    B, Hh = 240.0, 160.0
    nodes = {1: (0., 0., 0.), 2: (B, 0., 0.), 3: (0., 0., Hh), 4: (B, 0., Hh)}
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes=nodes))
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, x in nodes.items():
        ops.node(t, *x)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 1, 1, 1, 1, 1, 1)
    ops.fix(3, 0, 1, 1, 1, 1, 1); ops.fix(4, 0, 1, 1, 1, 1, 1)
    ops.equalDOF(3, 4, 1)
    X = [B / 2, 0., Hh / 2]
    ctx = dict(brb={}, links={}, pt_secs={}, pt_builder=None)
    ctx["brace_geom"] = dict(lc={}, cross={11: dict(partner=12, s=0.5, X=X), 12: dict(partner=11, s=0.5, X=X)}, cfg_source="test", factor=None)
    hinges, stats, mat = {}, dict(brace_nonlinear=0), NM.MAT_BASE
    for t, a, b in ((11, 1, 4), (12, 2, 3)):
        mat = NM.build_brace(pkg, dict(tag=t, n1=a, n2=b, etype="Truss", raw=["Truss", t, a, b, 7.58, 1]), SEC, _prm(), mat, hinges, stats, ctx)
    return hinges


def test_x_brace_half_removal_keeps_the_crossing():
    hinges = _x_pair(); h = hinges[11]
    xn = NM.PT_NODE_BASE + 11 * 100 + 99
    assert len(h["pt_units"]) == 2
    for u in h["pt_units"]:
        assert xn not in u["nodes"] and xn in u["ends"]
    BF.remove_unit(h, 0)
    assert not h.get("fractured") and h["force_ele"] == h["pt_units"][1]["segs"][0] and xn in ops.getNodeTags()
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(3, 1.0, 0, 0, 0, 0, 0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    assert ops.analyze(5) == 0                                   # brace 12 and the remaining half of 11 still resist
    assert 0.0 < ops.nodeDisp(3, 1) < 0.1
    BF.remove_unit(h, 1)
    assert h["fractured"] and HM.brace_axial_force(11, h) == 0.0


# ----------------------------------------------------------------------------- NL-R2-20 (round 4, test)
def _transient_with_removal(rebuild, n=300, at=150):
    """The single brace (frame node 2, mass 1.0) under a ground-motion pulse in X, HHT 0.9; at step `at` its buckling
    unit is removed (as the monitor does) and the analysis continues: rebuild = 'nlrha' (nlrha.run.transient_analysis:
    wipe and re-create with UmfPack, what run_record does) or 'band' (unshrunk BandGeneral + domainChange, reference)."""
    import math
    from nlrha import run as RN
    hinges = _single(); h = hinges[5]
    ops.remove("loadPattern", 1)
    ops.rayleigh(0.2, 0.0, 0.0, 0.0)
    acc = [60.0 * math.sin(2 * math.pi * 2.0 * i * 0.01) * math.exp(-0.5 * i * 0.01) for i in range(n + 10)]
    ops.timeSeries("Path", 2, "-dt", 0.01, "-values", *acc); ops.pattern("UniformExcitation", 2, 1, "-accel", 2)
    RN.transient_analysis(1e-10, "hht", system=("UmfPack" if rebuild == "nlrha" else "BandGeneral"))
    out = []
    for i in range(n):
        assert ops.analyze(1, 0.01) == 0, i
        if i == at:
            BF.remove_unit(h, 0)
            if rebuild == "nlrha":
                RN.transient_analysis(1e-10, "hht")
            else:
                ops.domainChange()
        out.append((ops.getTime(), ops.nodeDisp(2, 1), ops.nodeVel(2, 1), ops.nodeAccel(2, 1)))
    ops.wipe()
    return out


def test_rebuild_after_removal_equals_an_unshrunk_bandgeneral_run():
    """nlrha.run rebuilds the analysis (wipeAnalysis + the same objects) after a fracture removal because UmfPack cannot
    shrink on a domainChange; the transient integrator re-reads the committed state, so the continuation is the same as
    a BandGeneral run that simply took a domainChange (reviewer scratch_verify4/rebuild.py, here with the real brace)."""
    a = _transient_with_removal("nlrha"); b = _transient_with_removal("band")
    umax = max(abs(x[1]) for x in b)
    assert umax > 1.0                                             # the frame moves well after the removal
    assert all(abs(p[0] - q[0]) < 1e-12 for p, q in zip(a, b))
    assert max(abs(p[1] - q[1]) for p, q in zip(a, b)) < 1e-8 * umax
    assert max(abs(p[2] - q[2]) for p, q in zip(a, b)) < 1e-8 * max(abs(x[2]) for x in b)


def test_x_diagonal_fractured_in_both_halves_moves_the_crossing_mass():
    """Both halves of diagonal 11 gone: the crossing node's translational mass goes to 11's frame ends (total kept);
    both diagonals gone: the two crossing nodes and their equalDOF are removed and a transient analysis still runs."""
    from nlrha import run as RN
    hinges = _x_pair(); h, p = hinges[11], hinges[12]
    xn, xp = h["pt_cross"]["node"], p["pt_cross"]["node"]
    assert p["pt_cross"]["constrained"] and not h["pt_cross"]["constrained"] and h["pt_cross"]["frame"] == (1, 4)
    def total():
        return sum(ops.nodeMass(n)[0] for n in ops.getNodeTags())
    m0 = total(); f0 = ops.nodeMass(1)[0] + ops.nodeMass(4)[0]; x0 = ops.nodeMass(xn)[0]
    m1 = BF.remove_unit(h, 0, hinges)
    assert ops.nodeMass(xn)[0] > 0.0                              # one half left: the crossing still belongs to it
    m2 = BF.remove_unit(h, 1, hinges)
    assert h["fractured"] and ops.nodeMass(xn)[0] == 0.0 and ops.nodeMass(xn)[3] > 0.0
    assert h["pt_cross"]["mass_moved"] > 0.0
    # the frame ends got both chains and the crossing node's own mass; nothing lost
    assert abs(ops.nodeMass(1)[0] + ops.nodeMass(4)[0] - f0 - (m1 + m2 + x0)) < 1e-12 and abs(total() - m0) < 1e-12
    assert xn in ops.getNodeTags() and xp in ops.getNodeTags()   # still tied to the intact partner
    BF.remove_unit(p, 0, hinges); BF.remove_unit(p, 1, hinges)
    assert p["fractured"] and xn not in ops.getNodeTags() and xp not in ops.getNodeTags()
    assert abs(total() - m0) < 1e-9                               # only the crossing nodes' rotational inertia left
    ops.mass(3, 1.0, 1.0, 1.0, 0, 0, 0); ops.mass(4, 1.0, 1.0, 1.0, 0, 0, 0)
    ops.uniaxialMaterial("Elastic", 9, 10.0); ops.element("zeroLength", 9, 1, 3, "-mat", 9, "-dir", 1)
    ops.timeSeries("Path", 2, "-dt", 0.01, "-values", *([0.0] + [20.0] * 50)); ops.pattern("UniformExcitation", 2, 1, "-accel", 2)
    RN.transient_analysis(1e-8, "hht")
    assert ops.analyze(20, 0.01) == 0                             # no singular crossing pair left
    ops.wipe()
