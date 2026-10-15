"""Gold-run rerun (Ex28 STMF): beams framing into off-grid truss panel points were never braced by the deck.

* HR slaves only the 12 grid nodes of each Ex28 floor to the rigid diaphragm, so a 60 ft W33X118 filler between
  truss panel points had no diaphragm at its ends and the GMNIA left it unbraced: it buckled laterally-torsionally
  at lambda 0.33 (HR: deck-braced, Lb = 0, D/C 0.62) and the DDM reported FAIL 0.28.
* The Ex28 records give Lb only in their flexure checks (checks[].Lb_in), not in inputs.Lb_in.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _floor(with_lb_in_checks=True):
    from steltic_ddm.ingest import NeutralModel, Member
    nm = NeutralModel(job_dir=".", name="stmf")
    # one floor at z = 192: four grid nodes slaved to master 199999; two off-grid panel points NOT slaved
    grid = {100000: (0.0, 0.0, 192.0), 100100: (2160.0, 0.0, 192.0), 100001: (0.0, 528.0, 192.0),
            100101: (2160.0, 528.0, 192.0)}
    nm.nodes.update(grid)
    nm.nodes[199999] = (1080.0, 264.0, 192.0)
    nm.diaphragms = {199999: list(grid)}
    nm.nodes[112004] = (0.0, 264.0, 192.0); nm.nodes[120102] = (720.0, 264.0, 192.0)
    nm.nodes[130000] = (720.0, 264.0, 400.0)                     # another elevation
    nm.nodes[140000] = (5000.0, 264.0, 192.0)                    # same elevation, outside the floor
    chk = [dict(limit_state="F2 flexure", Lb_in=0.0), dict(limit_state="G2 shear")]
    nm.calc_package = dict(members=[dict(id="floor-filler-W33X118",
                                         inputs=dict(role="floor", kind="beam", section="W33X118"),
                                         checks=chk if with_lb_in_checks else [])])
    b = Member(3019, "beam", "W33X118", 112004, 120102, transf=3, relz=3, rely=3, dirn="X")
    b.role = "floor"
    return nm, b


def _model(nm):
    from steltic_ddm.model_gmnia import GMNIAModel
    return GMNIAModel(nm, {"Fy": 50.0}, nsub=(2, 2, 2), residual="none")


def test_off_grid_node_takes_the_diaphragm_at_its_elevation():
    nm, b = _floor()
    g = _model(nm)
    assert g._diaphragm_of(112004) is None                       # not slaved by HR
    assert g._deck_master(112004) == 199999 and g._deck_master(120102) == 199999
    assert g._deck_master(100000) == 199999                      # a slave keeps its own diaphragm
    assert g._deck_master(130000) is None                        # other elevation
    assert g._deck_master(140000) is None                        # outside the floor's plan extent


def test_lb_from_the_flexure_checks_when_inputs_have_none():
    nm, b = _floor()
    g = _model(nm)
    assert g._member_lb(b) == 0.0
    plan = g._brace_plan(b, 720.0, 0.0)
    assert plan is not None and plan[0] == "continuous"
    nm2, b2 = _floor(with_lb_in_checks=False)
    assert _model(nm2)._brace_plan(b2, 720.0, 0.0) is None       # no Lb anywhere -> unbraced, as before


# ------------------------------------------------------------------- sweeps that left the equilibrium path (Ex28, Ex18)
def _res(lam, kt):
    return dict(snapshot={}, lambda_u=lam, step_at_max=12, lam_at_1p25d=None, plateau=False,
                termination=dict(kind="limit", lam=lam, tangent=kt))


def test_absurd_lambda_or_tangent_is_not_evaluated():
    from types import SimpleNamespace
    from steltic_ddm import solver, phi_s
    model = SimpleNamespace(elems=[], _bt={})
    for lam, kt in ((9282.958, 2405724.97), (169.8, 22989.9), (1.504, 53.18)):   # Ex28 gold-run rerun sweeps
        c = solver.classify(_res(lam, kt), model)
        assert c["cls"] == "numerical", (lam, kt)
        assert phi_s.check(0.85, lam, c["cls"]) == (None, "NOT EVALUATED")
    for lam, kt in ((1.553, 1.2458), (2.179, 1.97), (1.471, 6.67)):               # real limits keep their verdict
        assert solver.classify(_res(lam, kt), model)["cls"] != "numerical", (lam, kt)


# --------------------------------------------------------- framing points of a girder split by the fillers (Ex28 roof)
def test_girder_split_at_filler_framing_points_is_braced_there():
    """Ex28: the 60 ft roof W44X262 girder is five members split at the W33X118 filler connections (HR Lb 192 in, braced
    by the fillers). Each off-grid framing point gets a deck brace (lateral normal to the girder + twist); grid ends,
    collinear-only joints and members longer than Lb do not."""
    import openseespy.opensees as ops
    from steltic_ddm.ingest import NeutralModel, Member
    from steltic_ddm.model_gmnia import GMNIAModel
    nm = NeutralModel(job_dir=".", name="roof")
    z = 1368.0
    grid = {800100: (720.0, 0.0, z), 800101: (720.0, 720.0, z), 800000: (0.0, 0.0, z), 800001: (0.0, 720.0, z)}
    nm.nodes.update(grid); nm.nodes[899999] = (360.0, 360.0, z); nm.diaphragms = {899999: list(grid)}
    ys = [0.0, 132.0, 264.0, 456.0, 588.0, 720.0]
    tags = [800100, 820101, 820102, 820103, 820104, 800101]
    for t, y in zip(tags, ys):
        nm.nodes.setdefault(t, (720.0, y, z))
    nm.nodes[812002] = (0.0, 132.0, z)                                   # filler's far end (off-grid too)
    girder = [Member(3325 + i, "beam", "W44X262", tags[i], tags[i + 1], transf=3, relz=0, dirn="Y") for i in range(5)]
    filler = Member(3345, "beam", "W33X118", 812002, 820101, transf=3, relz=3, rely=3, dirn="X")
    for m in girder + [filler]:
        m.role = "roof"; nm.members.append(m)
    nm.calc_package = dict(members=[dict(id="roof-girder-W44X262", inputs=dict(role="roof", section="W44X262"),
                                         checks=[dict(limit_state="F2", Lb_in=192.0)])])
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, c in nm.nodes.items():
        ops.node(t, *c)
    ops.uniaxialMaterial("Elastic", 1, 1e6); ops.uniaxialMaterial("Elastic", 2, 1e9)
    g = GMNIAModel(nm, {"Fy": 50.0}, nsub=(2, 2, 2), residual="none")
    g._nbrace, g._brace_slaves = 0, {}
    g._brace_framing_points()
    assert set(g.framing_braced) == {820101}                      # only the joint where the filler frames in
    assert g.framing_braced[820101][1] == (3345,)
    assert list(g._brace_slaves) == [899999] and len(g._brace_slaves[899999]) == 1
    # a girder member longer than Lb is _brace_plan's business, not this pass
    nm.calc_package["members"][0]["checks"][0]["Lb_in"] = 100.0
    g._lb_map = None; g._nbrace, g._brace_slaves = 0, {}
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, c in nm.nodes.items():
        ops.node(t, *c)
    ops.uniaxialMaterial("Elastic", 1, 1e6); ops.uniaxialMaterial("Elastic", 2, 1e9)
    g._brace_framing_points()
    assert not g.framing_braced


# ------------------------------------------------------------- a converged but non-physical step is never committed
def test_runaway_step_is_rejected_and_ends_the_sweep_as_numerical(monkeypatch):
    """Ex28 rerun 3: after five halvings a displacement-controlled step 'converged' at lambda 2265 (from 2.197) for a
    control-DOF move of 0.001 in. Such a step is rejected: the sweep ends at the last physical state, NUMERICAL."""
    import sys as _s
    _s.path.insert(0, HERE)
    import openseespy.opensees as real
    from test_ddm_r6 import _frame
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm import solver
    nm, cfg = _frame()
    g = GMNIAModel(nm, cfg, nsub=(2, 2, 2))
    combo = ("0.9D+1.0WY-", 0.9, 0.0, 0.0, {1: (0.0, -20.0, 0.0)}, False)
    pres = {1: {(1, 1), (1, 2), (2, 1), (2, 2)}}

    class Proxy:
        def __init__(self):
            self.n = 0

        def __getattr__(self, k):
            return getattr(real, k)

        def analyze(self, *a):
            self.n += 1
            return real.analyze(*a)

        def getLoadFactor(self, p):
            v = real.getLoadFactor(p)
            return v + 2262.0 if self.n >= 8 else v           # the 8th analysis step onwards "converges" at lambda ~ 2265

    monkeypatch.setattr(solver, "ops", Proxy())
    r = solver.sweep(g, combo, pres, dlam=0.05, max_steps=20, verbose=False)
    assert r["termination"]["kind"] == "numerical"
    assert "non-physical step rejected" in r["termination"]["detail"]
    assert r["lambda_u"] < 20 and all(l < 20 for l, _ in r["hist"])
