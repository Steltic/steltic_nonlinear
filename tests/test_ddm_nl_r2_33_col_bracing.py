"""NL-R2-33: GMNIA columns braced where the HR design braces them.

Ex21 (1-storey OCBF, 32 ft eave) governing gravity sweep ended with the perimeter W10X45 columns buckling about
the weak axis over the full 32 ft (KL/r 191) although the HR design braces them at mid-height (calc_package Lcy = Lb =
192 in: girts + flange brace). The GMNIA now braces the column chain at the HR brace points: weak-axis lateral
displacement held relative to the column chord (strongback, no axial / vertical / strong-axis / sway stiffness) and twist.
"""
import math, os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

ENGINE = os.environ.get("STELTIC_ENGINE_DIR")
needs_engine = pytest.mark.skipif(not ENGINE, reason="STELTIC_ENGINE_DIR not set")

L_COL = 384.0


def _cp(**inp):
    base = dict(role="lateral_col", kind="col", section="W10X45", length_in=L_COL)
    base.update(inp)
    return dict(members=[dict(id="%s-%s" % (base["role"], base["section"]), inputs=base)])


def _column(cp, transf=2, base=(1, 1, 1, 0, 0, 0), top=(1, 1, 0, 0, 0, 1), section="W10X45"):
    """One pinned column, top held laterally (and against twist) -- an Euler strut in a NeutralModel."""
    from steltic_ddm.ingest import NeutralModel, Member
    nm = NeutralModel(job_dir=".", name="col")
    nm.nodes[0] = (0.0, 0.0, 0.0); nm.nodes[100000] = (0.0, 0.0, L_COL)
    nm.fixes[0] = base
    if top:
        nm.fixes[100000] = top
    m = Member(1, "col", section, 0, 100000, transf=transf, dirn="Z")
    m.role = "lateral_col"
    nm.members.append(m)
    nm.calc_package = cp
    return nm


def _model(nm, **kw):
    from steltic_ddm.model_gmnia import GMNIAModel
    opts = dict(nsub=(2, 2, 4), residual="none")
    opts.update(kw)
    return GMNIAModel(nm, {"Fy": 50.0}, **opts)


def _static(load, ctrl=None):
    import openseespy.opensees as ops
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for n, f in load:
        ops.load(n, *f)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-9, 50, 0); ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    assert ops.analyze(1) == 0


# ---------------------------------------------------------------------------------------------- plan from the HR design
def test_column_brace_plan_from_hr_design():
    """Ex21 record (Lcx 384 / Lcy 192 / Lb 192 on a 384-in W10X45): weak axis and twist braced at mid-height, strong
    axis not; L / Lc < 2, a different member length, a 'collector' role, another section or the elastic (transfer-gate)
    build -> unbraced."""
    g = _model(_column(_cp(Lcx_in=384.0, Lcy_in=192.0, Lb_in=192.0)))
    m = g.nm.members[0]
    p = g._col_brace_plan(m, L_COL)
    assert p["weak"] == [192.0] and p["twist"] == [192.0] and p["strong"] == []
    assert "Lcy_in" in p["source"]
    # floor, never round: Lcy 150 on 384 -> 2 segments (192), not 3 (128 < 150)
    assert _model(_column(_cp(Lcy_in=150.0, Lb_in=150.0)))._col_brace_plan(m, L_COL)["weak"] == [192.0]
    # third points
    assert _model(_column(_cp(Lcy_in=128.0, Lb_in=384.0)))._col_brace_plan(m, L_COL)["weak"] == pytest.approx([128.0, 256.0])
    for cp in (_cp(Lcy_in=200.0, Lb_in=200.0),                       # L / Lc < 2
               _cp(Lcy_in=192.0, Lb_in=192.0, length_in=300.0),      # record of another member length
               _cp(Lcy_in=192.0, Lb_in=192.0, role="collector", kind="beam"),
               _cp(Lcy_in=192.0, Lb_in=192.0, section="W10X49"),
               _cp(Lb_in=384.0)):                                    # Ex13-type record: Lb = L, nothing braced
        assert _model(_column(cp))._col_brace_plan(m, L_COL) is None, cp
    assert _model(_column(_cp(Lcy_in=192.0, Lb_in=192.0)), elastic=True)._col_brace_plan(m, L_COL) is None
    nob = _model(_column(_cp(Lcy_in=192.0, Lb_in=192.0))); nob.column_bracing = False
    assert nob._col_brace_plan(m, L_COL) is None
    # brace_points_in elevations (Ex33 crane column 0/168/336/456 cut at a 336-in element) -> the 168-in piece
    cp = dict(members=[dict(id="c", inputs=dict(role="gravity_col", section="W10X45", length_in=456.0,
                                                 brace_points_in=[0.0, 168.0, 336.0, 456.0]))])
    p = _model(_column(cp))._col_brace_plan(m, 336.0)
    assert p["weak"] == [168.0] and p["twist"] == [168.0] and p["strong"] == []


def test_braced_chain_and_segment_imperfection():
    """Each braced segment gets >= 2 sub-elements with a brace point AT a chain node; the bow between brace points is an
    alternating half-sine of Lseg/1000 on top of the full-length L/1000 bow (zero at the brace points)."""
    from steltic_ddm.model_gmnia import GMNIAModel as G
    plan = dict(weak=[192.0], strong=[], twist=[192.0])
    assert G.col_chain_fracs(L_COL, plan, 2) == pytest.approx([0.25, 0.5, 0.75])
    assert G.col_chain_fracs(L_COL, plan, 8) == pytest.approx([q / 8 for q in range(1, 8)])
    assert G.col_chain_fracs(L_COL, dict(weak=[128.0, 256.0], strong=[], twist=[]), 2) == pytest.approx([q / 6 for q in range(1, 6)])
    cuts = [0.0, 192.0, L_COL]
    assert G.segment_seed(0.25, L_COL, 1e-3, cuts) == pytest.approx(0.192)
    assert G.segment_seed(0.75, L_COL, 1e-3, cuts) == pytest.approx(-0.192)
    assert abs(G.segment_seed(0.5, L_COL, 1e-3, cuts)) < 1e-12


# ---------------------------------------------------------------------------------------------- mechanics of the brace
def _mid(g):
    return g.sub_nodes[1][len(g.sub_nodes[1]) // 2]


def _disp_under(nm, load_fn, dof, node_fn, braced=True):
    import openseespy.opensees as ops
    g = _model(nm, bow=0.0)                       # straight member: the comparisons isolate the brace itself
    g.column_bracing = braced
    g.build().prepare()
    _static(load_fn(g))
    d = ops.nodeDisp(node_fn(g), dof)
    ops.wipe()
    return d, g


def test_brace_restrains_only_the_braced_axis():
    """W10X45, transf 2 (weak axis = global Y): a mid-height load in Y is carried by the brace (deflection ~0), in X
    (strong axis) the column deflects exactly as unbraced; axial shortening and twist-free top unchanged; the mid
    node's twist is held. Pure sway of the pinned column (top moved in Y) is resisted by nothing (the strongback
    rotates with the chord)."""
    cp = _cp(Lcx_in=384.0, Lcy_in=192.0, Lb_in=192.0)
    # weak axis (Y): braced
    dy_b, g = _disp_under(_column(cp), lambda g: [(_mid(g), (0, 1.0, 0, 0, 0, 0))], 2, _mid)
    dy_u, _ = _disp_under(_column(cp), lambda g: [(_mid(g), (0, 1.0, 0, 0, 0, 0))], 2, _mid, braced=False)
    assert 1 in g.col_braced and dy_u > 0.01 and abs(dy_b) < 2e-3 * dy_u
    # strong axis (X): identical
    dx_b, _ = _disp_under(_column(cp), lambda g: [(_mid(g), (1.0, 0, 0, 0, 0, 0))], 1, _mid)
    dx_u, _ = _disp_under(_column(cp), lambda g: [(_mid(g), (1.0, 0, 0, 0, 0, 0))], 1, _mid, braced=False)
    assert dx_b == pytest.approx(dx_u, rel=1e-6)
    # axial: identical shortening under a top load
    top = lambda g: 100000
    dz_b, _ = _disp_under(_column(cp, top=(1, 1, 0, 0, 0, 1)), lambda g: [(100000, (0, 0, -100.0, 0, 0, 0))], 3, top)
    dz_u, _ = _disp_under(_column(cp, top=(1, 1, 0, 0, 0, 1)), lambda g: [(100000, (0, 0, -100.0, 0, 0, 0))], 3, top, braced=False)
    assert dz_b == pytest.approx(dz_u, rel=2e-5)                  # strongback EA ~ 1e-5 of the column's
    # twist at the brace point held
    rz_b, _ = _disp_under(_column(cp), lambda g: [(_mid(g), (0, 0, 0, 0, 0, 10.0))], 6, _mid)
    rz_u, _ = _disp_under(_column(cp), lambda g: [(_mid(g), (0, 0, 0, 0, 0, 10.0))], 6, _mid, braced=False)
    assert abs(rz_u) > 1e-6 and abs(rz_b) < 1e-6 * abs(rz_u)
    # sway: pinned column, top pushed 1 in in Y by a stiff spring -> the brace carries nothing (mid follows the chord)
    import openseespy.opensees as ops
    out = {}
    for braced in (True, False):
        g = _model(_column(cp, top=(0, 0, 1, 0, 0, 1)), bow=0.0)
        g.column_bracing = braced
        g.build().prepare()
        ops.node(999, 0.0, 0.0, L_COL); ops.fix(999, 1, 1, 1, 1, 1, 1)
        ops.uniaxialMaterial("Elastic", 999, 1.0e3)
        ops.element("zeroLength", 999, 999, 100000, "-mat", 999, 999, "-dir", 1, 2)
        _static([(100000, (0, 1.0e3, 0, 0, 0, 0))])
        out[braced] = (ops.nodeDisp(100000, 2), ops.nodeDisp(_mid(g), 2))
        ops.wipe()
    assert out[True][0] == pytest.approx(out[False][0], rel=1e-9) and out[True][0] == pytest.approx(1.0, rel=1e-5)
    assert out[True][1] == pytest.approx(0.5 * out[True][0], rel=1e-6)


def _peak_axial(braced):
    """Displacement-controlled axial push of the pinned W10X45 (residual none, L/1000 bows): peak load (kip)."""
    import openseespy.opensees as ops
    cp = _cp(Lcx_in=384.0, Lcy_in=192.0, Lb_in=192.0)
    g = _model(_column(cp, top=(1, 1, 0, 0, 0, 1)))
    g.column_bracing = braced
    g.build().prepare()
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    ops.load(100000, 0, 0, -1.0, 0, 0, 0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 100000, 3, -0.01); ops.analysis("Static")
    pk = 0.0
    for _ in range(150):
        if ops.analyze(1) != 0:
            break
        P = ops.getLoadFactor(1)
        pk = max(pk, P)
        if P < 0.8 * pk:
            break
    ops.wipe()
    return pk, g


def test_braced_column_buckles_between_brace_points():
    """Unbraced: weak-axis buckling over 384 in (Euler pi^2 E Iy / L^2 = 104 kip); braced at mid-height: between the
    brace points (Euler on 192 in = 415 kip, AISC E3 Pn 341 kip with KL/r 95.5) -- the GMNIA must reach the
    between-brace-points range, not the full-length one."""
    pu, _ = _peak_axial(False)
    pb, g = _peak_axial(True)

    assert 70.0 < pu < 125.0, pu                                   # 2 sub-elements per column: ~10 % above Euler
    assert 300.0 < pb < 420.0, pb
    assert any("column bracing (NL-R2-33)" in n for n in g.special_log)
