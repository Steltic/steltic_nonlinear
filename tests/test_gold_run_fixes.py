"""Fixes from the gold run on the PC (13 Oct 2026, Python 3.14, 18 HR test buildings).

* Ex21 NLRHA: ARPACK stopped at its iteration limit without raising and returned eigenvectors of ~1e160; the modal
  participation overflowed (OverflowError on L**2) and the whole NLRHA step crashed. nlrha.model now validates the
  eigen solution and falls back to the dense solver when it is affordable.
* Ex28 DDM: the member direction came from the node TAGS; an off-grid node (truss panel point) decodes to a wrong
  (i, j), so an X beam was taken as a Y beam and its end pins released its torsion at both ends -> the GMNIA rebuild
  was singular. The direction now comes from the coordinates and the beam pins are oriented on the member.
"""
import math, os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


# ------------------------------------------------------------------------------------------------ member direction
def test_member_dirn_from_coordinates():
    from steltic_ddm.ingest import _member_dirn, decode_tag
    nodes = {
        180001: (0.0, 0.0, 144.0), 180002: (240.0, 0.0, 144.0),       # off-grid panel points along X
        100000: (0.0, 0.0, 144.0), 100: (0.0, 0.0, 0.0),
        100101: (300.0, 300.0, 144.0), 100001: (0.0, 300.0, 144.0),
        200000: (0.0, 0.0, 288.0), 200100: (300.0, 0.0, 300.0),       # rafter sloping in X-Z
    }
    i1, j1, _ = decode_tag(180001); i2, j2, _ = decode_tag(180002)
    assert i1 == i2 and j1 != j2                                   # the tags alone say "Y" -- the bug
    assert _member_dirn(nodes, 180001, 180002) == "X"
    assert _member_dirn(nodes, 100, 100000) == "Z"
    assert _member_dirn(nodes, 100001, 100101) == "X"
    assert _member_dirn(nodes, 100000, 100001) == "Y"
    assert _member_dirn(nodes, 100000, 100101) == "D"              # plan diagonal
    assert _member_dirn(nodes, 200000, 200100) == "X"              # sloped rafter keeps its plan direction
    assert _member_dirn({}, 100000, 100100) == "X"                 # no coordinates: the tag rule as before
    assert _member_dirn({}, 100000, 100001) == "Y"


def _off_grid_beam(relz=3, rely=0):
    """One X beam between two fully fixed OFF-GRID nodes, pinned (major axis) at both ends."""
    from steltic_ddm.ingest import NeutralModel, Member, _member_dirn
    nm = NeutralModel(job_dir=".", name="beam")
    nm.nodes[180001] = (0.0, 0.0, 144.0); nm.nodes[180002] = (360.0, 0.0, 144.0)
    nm.fixes[180001] = (1, 1, 1, 1, 1, 1); nm.fixes[180002] = (1, 1, 1, 1, 1, 1)
    m = Member(1, "beam", "W18X35", 180001, 180002, transf=3, relz=relz, rely=rely,
               dirn=_member_dirn(nm.nodes, 180001, 180002))
    m.role = "floor"
    nm.members.append(m)
    return nm


def _solve(load):
    import openseespy.opensees as ops
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for n, f in load:
        ops.load(n, *f)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-9, 50, 0); ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    return ops.analyze(1)


def test_off_grid_pinned_beam_keeps_torsion_and_frees_major_bending():
    import openseespy.opensees as ops
    from steltic_ddm.model_gmnia import GMNIAModel
    nm = _off_grid_beam()
    assert nm.members[0].dirn == "X"
    g = GMNIAModel(nm, {"Fy": 50.0}, nsub=(2, 2, 2), residual="none", out_of_plumb=(None, 0.0), bow=0.0, brace_bow=0.0)
    g.build()
    mid = [n for n in ops.getNodeTags() if abs(ops.nodeCoord(n)[0] - 180.0) < 1e-6]
    assert mid, "no mid-span node"
    # a torque at mid-span: with torsion released at both ends the stiffness is singular and this fails
    assert _solve([(mid[0], (0.0, 0.0, 0.0, 10.0, 0.0, 0.0))]) == 0
    twist = ops.nodeDisp(mid[0], 4)
    assert math.isfinite(twist) and abs(twist) < 1.0
    # gravity: simply supported about the major axis -> 5wL^4/384EI-like deflection, not a fixed-fixed one
    ops.wipe()
    g = GMNIAModel(nm, {"Fy": 50.0}, nsub=(2, 2, 2), residual="none", out_of_plumb=(None, 0.0), bow=0.0, brace_bow=0.0)
    g.build()
    mid = [n for n in ops.getNodeTags() if abs(ops.nodeCoord(n)[0] - 180.0) < 1e-6][0]
    P = -2.0
    assert _solve([(mid, (0.0, 0.0, P, 0.0, 0.0, 0.0))]) == 0
    d = ops.nodeDisp(mid, 3)
    E, Ix, L = 29000.0, 510.0, 360.0                              # W18X35
    ss = P * L ** 3 / (48 * E * Ix); ff = P * L ** 3 / (192 * E * Ix)
    assert abs(d - ss) < 0.15 * abs(ss), (d, ss, ff)


# --------------------------------------------------------------------------------------------------- modal fallback
class _Proxy:
    """openseespy with eigen('-genBandArpack') returning what ARPACK returned on the PC for Ex21."""
    def __init__(self, real, arpack):
        self._real, self._arpack, self.calls = real, arpack, []

    def __getattr__(self, k):
        return getattr(self._real, k)

    def eigen(self, solver, n):
        self.calls.append(solver)
        if solver == "-genBandArpack":
            return self._arpack(n)
        return self._real.eigen(solver, n)

    def nodeEigenvector(self, n, i):
        v = self._real.nodeEigenvector(n, i)
        if self.calls[-1] == "-genBandArpack" and self._arpack is _bad_vectors:
            return [x * 1e160 + 1e160 for x in v]
        return v


def _bad_vectors(n):
    import openseespy.opensees as ops
    return ops.eigen("-genBandArpack", n)


def _shear_building(nst=3):
    import openseespy.opensees as ops
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.geomTransf("Linear", 1, 0.0, 1.0, 0.0)
    ops.node(1, 0.0, 0.0, 0.0); ops.fix(1, 1, 1, 1, 1, 1, 1)
    for k in range(1, nst + 1):
        ops.node(k + 1, 0.0, 0.0, 144.0 * k)
        ops.mass(k + 1, 1.0, 1.2, 0.0, 0.0, 0.0, 0.0)
        ops.element("elasticBeamColumn", k, k, k + 1, 50.0, 29000.0, 11200.0, 1000.0, 800.0, 900.0, 1)


def test_modal_rejects_overflowing_arpack_vectors_and_uses_dense(monkeypatch):
    import openseespy.opensees as real
    from nlrha import model as NLM
    _shear_building()
    md = NLM._mass_dofs()
    good = NLM._modal_n(md, 5)
    px = _Proxy(real, _bad_vectors)
    monkeypatch.setattr(NLM, "ops", px)
    res = NLM._modal_n(md, 5)
    assert px.calls == ["-genBandArpack", "-fullGenLapack"]
    for a, b in zip(good["modes"], res["modes"]):
        assert abs(a["T"] - b["T"]) < 1e-6 * a["T"]
        assert abs(a["fx"] - b["fx"]) < 1e-6 and abs(a["fy"] - b["fy"]) < 1e-6


def test_modal_arpack_exception_or_short_result_falls_back(monkeypatch):
    import openseespy.opensees as real
    from nlrha import model as NLM
    _shear_building()
    md = NLM._mass_dofs()
    for arpack in (lambda n: (_ for _ in ()).throw(RuntimeError("ARPACK info -9")), lambda n: [], lambda n: [float("nan")] * n):
        px = _Proxy(real, arpack)
        monkeypatch.setattr(NLM, "ops", px)
        res = NLM._modal_n(md, 4)
        assert px.calls[-1] == "-fullGenLapack"
        assert all(m["T"] > 0 for m in res["modes"])


def test_modal_dense_fallback_refused_when_too_large(monkeypatch):
    import openseespy.opensees as real
    from nlrha import model as NLM
    _shear_building()
    md = NLM._mass_dofs()
    px = _Proxy(real, lambda n: [])
    px.systemSize = lambda: 20000                                   # 3 x 8 x 20000^2 / 1e9 = 9.6 GB
    monkeypatch.setattr(NLM, "ops", px)
    with pytest.raises(RuntimeError, match="dense"):
        NLM._modal_n(md, 4)
    assert "-fullGenLapack" not in px.calls


def test_participation_validation():
    from nlrha import model as NLM
    _shear_building()
    md = NLM._mass_dofs()
    assert NLM._participation(md, [float("inf")]) is None
    assert NLM._participation(md, [-1.0]) is None


def test_modal_caps_modes_at_mass_dofs():
    from nlrha import model as NLM
    _shear_building()                                               # 6 translational mass DOFs
    res = NLM.modal(None, nmodes=12)                                # 12 > 5: capped, never an ARPACK request it cannot meet
    assert 1 <= len(res["modes"]) <= 5


def test_modal_failed_arpack_count_moves_to_more_modes_before_dense(monkeypatch):
    """Ex21: ARPACK failed at 12 modes and converged in seconds at 24 -> no dense solve."""
    import openseespy.opensees as real
    from nlrha import model as NLM
    _shear_building(nst=8)                                          # 16 translational mass DOFs
    px = _Proxy(real, lambda n: [] if n == 2 else real.eigen("-genBandArpack", n))
    monkeypatch.setattr(NLM, "ops", px)
    res = NLM.modal(None, nmodes=2)
    assert "-fullGenLapack" not in px.calls
    assert px.calls[:2] == ["-genBandArpack", "-genBandArpack"]
    assert res["n_modes"] >= 4 and len(res["modes"]) == res["n_modes"]


# ------------------------------------------------------------------------------------- beam Lb per HR member group
def test_beam_lb_from_hr_member_groups():
    """Ex32: roof W21X44 record Lb_in = 360 (the largest group) with Lb_in_by_group X girders 100 (infill), Y moment-frame
    360, Y gravity 0 (deck). The X gravity girders must be braced every ~100 in, not left unbraced over 30 ft."""
    from steltic_ddm.ingest import NeutralModel, Member
    from steltic_ddm.model_gmnia import GMNIAModel
    nm = NeutralModel(job_dir=".", name="lb")
    nm.calc_package = dict(members=[dict(id="roof-W21X44", inputs=dict(
        role="roof", section="W21X44", Lb_in=360.0,
        Lb_in_by_group={"X girders (infill-braced)": 100.0, "Y moment-frame girders": 360.0, "Y gravity girders": 0.0}))])
    g = GMNIAModel(nm, {"Fy": 50.0}, nsub=(2, 2, 2), residual="none")

    def beam(dirn, relz):
        b = Member(1, "beam", "W21X44", 100000, 100100, transf=3, relz=relz, dirn=dirn); b.role = "roof"
        return b
    assert g._member_lb(beam("X", 3)) == 100.0          # X gravity girder, braced by the infill
    assert g._member_lb(beam("X", 0)) == 100.0          # X moment-frame girder: the X group names no connection type
    assert g._member_lb(beam("Y", 0)) == 360.0          # Y moment-frame girder
    assert g._member_lb(beam("Y", 3)) == 0.0            # Y gravity girder: deck, continuous
    assert g._member_lb(beam("D", 3)) == 360.0          # no group matches -> the largest, as before
    # a record without groups: Lb_in as before
    nm.calc_package["members"][0]["inputs"].pop("Lb_in_by_group")
    g = GMNIAModel(nm, {"Fy": 50.0}, nsub=(2, 2, 2), residual="none")
    assert g._member_lb(beam("X", 3)) == 360.0


def test_lb_group_names_need_a_whole_word_direction():
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm.ingest import Member
    b = Member(1, "beam", "W21X44", 1, 2, transf=3, relz=3, dirn="X")
    assert GMNIAModel._lb_group_match("X girders (infill-braced)", b)
    assert GMNIAModel._lb_group_match("girders along x", b)
    assert not GMNIAModel._lb_group_match("Y gravity girders", b)
    assert not GMNIAModel._lb_group_match("X moment-frame girders", b)   # pinned beam
    assert GMNIAModel._lb_group_match("W21X44 gravity", b)               # the X inside a section name is not a direction
