"""NL-R2-20 (residual): a failed transient step must leave a clean state for the retry (nlrha.run.transient_step).

OpenSees (3.7/3.8, Transformation handler, HHT or Newmark): after a failed ops.analyze the nodes, elements and
integrator vectors are reverted, but the DOF groups of MP-constrained nodes (rigid-diaphragm / equalDOF slaves) keep
state from the failed iterations, so the retry starts from a corrupted state. Ex20 (SCBF, two-storey X work points at
beam mid-span) Gilroy #3: after one diverged step every halved retry diverged again (one Newton iteration of the retry
gave |du| ~1e5..1e26 rad at diaphragm-slave rotations, largest at the work points) -> non-convergence.
transient_step calls ops.domainChange() after a failure, which rebuilds the DOF groups from the committed state.
Run: PYTHONPATH=. python -m pytest tests/test_nlrha_retry_state.py -q
"""
import copy, math, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openseespy.opensees as ops
from nlrha import run as RN
from pushover import hinge_models as HM, nonlinear_model as NM


# ----------------------------------------------------------------------------- minimal reproduction
def _portal(integrator):
    ops.wipe(); ops.model("basic", "-ndm", 2, "-ndf", 3)
    ops.node(1, 0, 0); ops.node(2, 0, 100); ops.node(3, 100, 100); ops.node(4, 100, 0)
    ops.fix(1, 1, 1, 1); ops.fix(4, 1, 1, 1)
    ops.geomTransf("Linear", 1)
    ops.element("elasticBeamColumn", 1, 1, 2, 10, 29000, 100, 1)
    ops.element("elasticBeamColumn", 2, 4, 3, 10, 29000, 100, 1)
    ops.equalDOF(2, 3, 1)                                  # node 3: slave in X, own Y and rotation
    ops.mass(2, 1, 1e-6, 1e-6); ops.mass(3, 1e-6, 1e-6, 1e-6)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 10.0, 0, 0); ops.load(3, 0, 0, 50.0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack"); ops.algorithm("Newton")
    ops.integrator(*integrator); ops.analysis("Transient")


def _retry_after_failure(step):
    """Converge one step, fail the next one (unreachable tolerance), retry it; return the slave node's response."""
    ops.test("NormDispIncr", 1e-8, 10, 0); assert step(0.01) == 0
    ops.test("NormDispIncr", 1e-30, 3, 0); assert step(0.01) != 0
    ops.test("NormDispIncr", 1e-8, 10, 0); assert step(0.01) == 0
    return ops.nodeDisp(3)


def _clean(integrator):
    _portal(integrator); ops.test("NormDispIncr", 1e-8, 10, 0)
    assert ops.analyze(1, 0.01) == 0 and ops.analyze(1, 0.01) == 0
    return ops.nodeDisp(3)


def _close(a, b, rel=1e-9):
    return all(abs(x - y) <= rel * (1 + abs(y)) for x, y in zip(a, b))


def test_retry_after_failed_step_matches_a_clean_run():
    for integ in (("HHT", 0.9), ("Newmark", 0.5, 0.25)):
        ref = _clean(integ)
        _portal(integ)
        bad = _retry_after_failure(lambda h: ops.analyze(1, h))
        # documents the OpenSees behaviour this guards against; if it ever stops failing, transient_step's
        # domainChange is merely redundant
        assert not _close(bad, ref), "OpenSees now reverts MP-slave state after a failed step"
        _portal(integ)
        good = _retry_after_failure(RN.transient_step)
        assert _close(good, ref), (integ, good, ref)


def test_run_record_steps_through_transient_step():
    import inspect
    src = inspect.getsource(RN.run_record)
    assert "transient_step(h)" in src and "ops.analyze(1, h)" not in src


# ----------------------------------------------------------------------------- X-brace work point at beam mid-span
SEC, H, B = "HSS7X7X5/8", 150.0, 360.0


def _xbay():
    """One bay, two storeys: a two-storey X (chevron below, V above) meeting at the level-1 beam mid-span work point;
    beam halves pinned at the columns with hinge springs at the work point (rigid torsion); physical-theory braces
    (clear length 0.8 x work-point length, gusset end zones); the floor ties the in-plane sway (equalDOF: the work point
    and the right-hand joints are MP slaves, as the diaphragm slaves of Ex20 nodes 590003 / 990001), out-of-plane
    translation and RZ held; floor masses on the column joints."""
    prm = copy.deepcopy(HM.load_params()); prm["_analysis"] = "nlrha"
    nodes = {1: (0., 0., 0.), 2: (B, 0., 0.), 11: (0., 0., H), 12: (B, 0., H), 21: (0., 0., 2 * H), 22: (B, 0., 2 * H),
             15: (B / 2, 0., H)}
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, x in nodes.items():
        ops.node(t, *x)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 1, 1, 1, 1, 1, 1)
    for t in (11, 12, 15, 21, 22):                         # floor: out-of-plane translation and RZ held
        ops.fix(t, 0, 1, 0, 0, 0, 1)
        ops.mass(t, *([1e-8] * 6))
    for t in (11, 12, 21, 22):
        ops.mass(t, 0.5, 1e-8, 1e-8, 1e-8, 1e-8, 1e-8)
    ops.uniaxialMaterial("Elastic", 1, NM.RIGID_T); ops.uniaxialMaterial("Elastic", 2, NM.RIGID_R)
    ops.geomTransf("PDelta", 1, 0., 1., 0.); ops.geomTransf("Linear", 3, 0., 0., 1.)
    for t, a, b in ((101, 1, 11), (102, 11, 21), (103, 2, 12), (104, 12, 22)):
        ops.element("elasticBeamColumn", t, a, b, 38.8, 29000., 11200., 12.3, 548., 1530., 1)
    ops.element("elasticBeamColumn", 201, 21, 22, 24.7, 29000., 11200., 3.7, 2370., 94.4, 3, "-releasey", 3)
    for t, a, b, endwp in ((301, 11, 15, 2), (302, 15, 12, 1)):
        hn = NM.HN_BASE + t * 10 + endwp
        ops.node(hn, *nodes[15])
        ni, nj = (a, hn) if endwp == 2 else (hn, b)
        ops.element("elasticBeamColumn", t, ni, nj, 24.7, 29000., 11200., 3.7, 2370. * 1.1, 94.4, 3, "-releasey", 1 if endwp == 2 else 2)
        ops.uniaxialMaterial("Elastic", 5000 + t, 10 * 6 * 29000 * 2370 / (B / 2))
        ops.element("zeroLength", NM.ZL_BASE + t * 10 + endwp, 15, hn, "-mat", 1, 1, 1, 2, 5000 + t, 2, "-dir", 1, 2, 3, 4, 5, 6)
    braces = [(401, 1, 15), (402, 2, 15), (403, 15, 21), (404, 15, 22)]
    pkg = types.SimpleNamespace(model=types.SimpleNamespace(nodes=nodes))
    ctx = dict(brb={}, links={}, pt_secs={}, pt_builder=None)
    ctx["brace_geom"] = dict(lc={t: (0.8 * math.dist(nodes[a], nodes[b]), "test") for t, a, b in braces}, cross={},
                             cfg_source="test", factor=0.8)
    hinges, stats, mat = {}, dict(brace_nonlinear=0), NM.MAT_BASE
    for t, a, b in braces:
        mat = NM.build_brace(pkg, dict(tag=t, n1=a, n2=b, etype="Truss", raw=["Truss", t, a, b, 14.0, 1]), SEC, prm, mat,
                             hinges, stats, ctx)
    ops.equalDOF(21, 22, 1); ops.equalDOF(11, 12, 1); ops.equalDOF(11, 15, 1)     # in-plane diaphragm: MP slaves 12, 15, 22
    return hinges, stats


def _shake(step, amp=1.2, T=0.4, tend=6.0):
    hinges, stats = _xbay()
    assert stats.get("brace_physical_theory") == 4
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(15, 0, 0, -40.0, 0, 0, 0)
    for n in (11, 12, 21, 22):
        ops.load(n, 0, 0, -100.0, 0, 0, 0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton"); ops.integrator("LoadControl", 0.1); ops.analysis("Static")
    assert ops.analyze(10) == 0
    ops.loadConst("-time", 0.0)
    dtr = 0.005
    vals = [amp * 386.4 * math.sin(2 * math.pi * i * dtr / T) * min(1.0, i * dtr / 3.0) for i in range(int(tend / dtr) + 2)]
    ops.timeSeries("Path", 2, "-dt", dtr, "-values", *vals); ops.pattern("UniformExcitation", 2, 1, "-accel", 2)
    ops.rayleigh(0.02 * 2 * math.pi / 0.3, 0.0, 0.0, 0.0)
    ops.wipeAnalysis(); ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-5, 30, 0); ops.algorithm("Newton"); ops.integrator("HHT", 0.9); ops.analysis("Transient")
    t, dt0 = 0.0, 0.01; dt, nok, fails, cons, peak = dt0, 0, 0, 0, 0.0
    while t < tend - 1e-9:                                  # the run_record halving loop
        if step(dt) != 0:
            fails += 1; cons += 1; dt *= 0.5
            if dt < dt0 / 32 or cons > 12:
                return dict(ok=False, t=t, fails=fails, peak=peak, hinges=hinges)
            continue
        t += dt; cons = 0; nok += 1
        if dt < dt0 and nok >= 8:
            dt = min(dt0, 2 * dt); nok = 0
        peak = max(peak, abs(ops.nodeDisp(21, 1)))
    return dict(ok=True, t=t, fails=fails, peak=peak, hinges=hinges)


def test_work_point_x_brace_passes_the_buckling_snap():
    plain = _shake(lambda h: ops.analyze(1, h))             # the pre-fix loop: retries start from a corrupted state
    assert not plain["ok"], "OpenSees now reverts MP-slave state after a failed step"
    r = _shake(RN.transient_step)
    assert r["ok"] and r["fails"] > 0, {k: v for k, v in r.items() if k != "hinges"}
    assert 0.01 * 2 * H < r["peak"] < 0.05 * 2 * H          # braces buckled (1-5 % drift), no runaway
    spec = HM.brace_spec(SEC, math.dist((0, 0, 0), (B / 2, 0, H)), dict(HM.load_params(), _analysis="nlrha"))
    for t in (401, 402, 403, 404):                          # committed state is physical (no garbage forces)
        assert abs(HM.brace_axial_force(t, r["hinges"][t])) < 1.5 * spec.Pye_kip
