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
