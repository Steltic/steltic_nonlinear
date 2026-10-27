"""NL-R2-36 (equalDOF ties replayed), NL-R2-37 (built-up box columns), NL-R2-38 (built-up I-sections from nl_sections.json).

October 2026 HR designs: Ex20 ties its transfer-truss bottom chord to the truss nodes with equalDOF (the gravity solve was
singular without the ties); Ex23 / Ex29 use BOX<B>X<t> core columns; Ex19 a PG84X1108 welded transfer girder."""
import json
import math

import openseespy.opensees as ops
import pytest

from pushover import builtup_box as BB
from pushover import constraints as CN
from pushover import hinge_models as HM
from pushover import sections_db as SDB


# ------------------------------------------------------------------ NL-R2-37 built-up box
def test_box_props_match_the_design_toolkit():
    p = BB.props("BOX34X4")                       # stk_ext.groupD.box_props(34, 4)
    bi = 34 - 8
    assert p["A"] == pytest.approx(34 ** 2 - bi ** 2)
    assert p["Ix"] == pytest.approx((34 ** 4 - bi ** 4) / 12)
    assert p["Zx"] == pytest.approx((34 ** 3 - bi ** 3) / 4)
    assert p["J"] == pytest.approx(4 * 30 ** 3)
    assert p["rx"] == pytest.approx(math.sqrt(p["Ix"] / p["A"]))
    assert BB.parse("HSS16X16X5/8") is None and BB.parse("BOX10X5") is None   # 2t >= B is not a box


def test_box_through_sections_db():
    p = SDB.props("box34x4")
    assert p["hss"]["built_up"] and p["hss"]["t_des"] == 4.0
    ws = SDB.hss_wall_slenderness("BOX34X4")
    assert ws["lam"] == pytest.approx((34 - 8) / 4)          # clear distance between plates, B - 2t
    m = SDB.hss_material("BOX34X4")
    assert (m["Fy"], m["Ry"], m["kind"]) == (50.0, 1.1, "rect")   # A572 Gr. 50 plates, AISC 341-22 Table A3.2


def _prm_with_library_row():
    prm = HM.load_params(None)
    prm["column_flexure"].setdefault("other_lines", {})["rect_HSS_builtup_box"] = dict(
        a_expr="1.1*lam**-1.2*(1-PG/Pye)**1.8", b_expr="0.5*lam**-0.6*(1-PG/Pye)**1.2-0.01", c=0.25,
        IO="0.5 a", LS="0.75 b", CP="b", a_max=0.05, b_max=0.08)
    prm["column_flexure"]["other_lines"]["builtup_box_factor_note_e"] = 0.75
    prm.pop("column_flexure_hss", None)
    return prm


def test_box_column_hinge_note_e_and_plate_material():
    prm = _prm_with_library_row()
    h = HM.column_hinge("BOX34X4", 180.0, 3000.0, prm)
    assert h.Fye_ksi == pytest.approx(55.0)
    # b/t 6.5 hits both caps (0.05, 0.08); note [e] x 0.75
    assert h.a_pl == pytest.approx(0.05 * 0.75) and h.b_pl == pytest.approx(0.08 * 0.75) and h.CP == pytest.approx(0.06)
    assert any("note [e] x0.75" in f for f in h.flags)
    assert any("verified library row" in f or "rect_HSS_builtup_box" in f for f in h.flags)
    assert not prm.get("_used_unverified", {}).get("column_flexure_hss")
    hr = HM.column_hinge("HSS16X16X5/8", 180.0, 300.0, prm)       # rolled HSS: no note [e]
    assert not any("note [e]" in f for f in hr.flags)


# ------------------------------------------------------------------ NL-R2-38 built-up I-section
def test_builtup_i_from_nl_sections_json(tmp_path):
    (tmp_path / "nl_sections.json").write_text(json.dumps({
        "_README": "test", "PG84X1108": {"kind": "I", "d": 84.0, "bf": 30.0, "tf": 4.0, "tw": 1.125}}))
    assert BB.register_job_sections(tmp_path) == ["PG84X1108"]
    p = SDB.props("PG84X1108")
    hw = 84 - 8
    assert p["A"] == pytest.approx(2 * 30 * 4 + hw * 1.125)          # Ex19 cfg.py formulas
    assert p["Ix"] == pytest.approx(2 * 30 * 4 * (80 / 2) ** 2 + 1.125 * hw ** 3 / 12 + 2 * 30 * 4 ** 3 / 12)
    assert p["Zx"] == pytest.approx(30 * 4 * 80 + 1.125 * hw ** 2 / 4)
    from steltic_ddm.sections_fiber import shape
    assert float(shape("PG84X1108")["d"]) == 84.0


# ------------------------------------------------------------------ NL-R2-36 equalDOF ties
def test_normalise_dedupes_and_skips_bad():
    ties = CN.normalise([[1, 2, 1, 2, 3], [1, 2, 1, 2, 3], ["x", 2, 1], [5, 6]])
    assert ties == [(1, 2, (1, 2, 3))]


def _cantilever_with_hanging_node(tie):
    """Column 1-2 fixed at 1; node 3 coincident with 2 carries a beam 3-4 that rests on nothing else."""
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    for t, xyz in {1: (0, 0, 0), 2: (0, 0, 100), 3: (0, 0, 100), 4: (100, 0, 100)}.items():
        ops.node(t, *xyz)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(4, 0, 0, 1, 0, 0, 0)
    ops.geomTransf("Linear", 1, 0, 1, 0); ops.geomTransf("Linear", 2, 0, 0, 1)
    ops.element("elasticBeamColumn", 1, 1, 2, 10.0, 29000.0, 11200.0, 100.0, 100.0, 100.0, 1)
    ops.element("elasticBeamColumn", 2, 3, 4, 10.0, 29000.0, 11200.0, 100.0, 100.0, 100.0, 2)
    n, skipped = CN.apply(ops, [[2, 3, 1, 2, 3, 4, 5, 6]] if tie else [], [])
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(4, 0, 0, -1.0, 0, 0, 0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 20); ops.algorithm("Newton"); ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    ok = ops.analyze(1)
    ops.wipe()
    return ok, n


def test_tie_makes_the_hanging_chord_solvable():
    assert _cantilever_with_hanging_node(tie=True) == (0, 1)
    assert _cantilever_with_hanging_node(tie=False)[0] != 0


def test_tie_to_a_diaphragm_slave_is_skipped():
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0, 0, 0); ops.node(2, 0, 0, 0)
    n, skipped = CN.apply(ops, [[1, 2, 1, 2]], rd_slaves=[2])
    ops.wipe()
    assert n == 0 and "rigid-diaphragm slave" in skipped[0]


def test_ddm_reader_keeps_the_last_build_ties(tmp_path):
    from steltic_ddm import ingest
    f = tmp_path / "model_opensees.py"
    f.write_text("ops.wipe()\nops.equalDOF(1, 2, 1, 2, 3)\nops.wipe()\nops.node(1, 0.0, 0.0, 0.0)\n"
                 "ops.equalDOF(5, 6, 1, 2, 3, 4, 5, 6)\n")
    eq = []
    ingest.parse_replay(str(f), eq)
    assert eq == [[5, 6, 1, 2, 3, 4, 5, 6]]
