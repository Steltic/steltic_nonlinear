"""Review D3 (NL-R2-28): HSS / pipe material by shape type, the same in the DDM and the pushover / NLRHA.
Rectangular HSS A500 Gr. C Fy 50 / Ry 1.3, round HSS A500 Gr. C Fy 46 / Ry 1.3, pipe A53 Gr. B Fy 35 / Ry 1.6
(Ry: AISC 341-22 Table A3.2). Before: the DDM built round HSS / pipe at the frame Fy (50) and the pushover / NLRHA at
the rectangular-HSS Fye 50 x 1.3 -- 9-43 % too strong."""
import os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ROUND, PIPE, RECT = "HSS6.625X0.280", "PIPE5STD", "HSS5X5X3/8"


def test_hss_material_by_shape_type():
    from pushover import sections_db as SDB
    assert SDB.hss_material(RECT)["Fy"] == 50.0 and SDB.hss_material(RECT)["Ry"] == 1.3
    assert SDB.hss_material(ROUND)["Fy"] == 46.0 and SDB.hss_material(ROUND)["Ry"] == 1.3
    m = SDB.hss_material(PIPE)
    assert (m["Fy"], m["Ry"], m["kind"]) == (35.0, 1.6, "pipe") and abs(m["Fye"] - 56.0) < 1e-9
    assert SDB.hss_material("W14X90") is None


class _Rec:
    def __init__(self):
        self.steel = {}
    def uniaxialMaterial(self, kind, tag, *a):
        if kind == "Steel01":
            self.steel[tag] = a[0]
    def section(self, *a):
        pass
    def fiber(self, *a):
        pass


@pytest.mark.parametrize("label,Fy", [(RECT, 50.0), (ROUND, 46.0), (PIPE, 35.0)])
def test_ddm_fibre_sections_use_the_hss_nominal_fy(label, Fy):
    """The DDM builds every HSS / pipe at its own nominal Fy, also when the frame (W-shape) Fy differs."""
    from pushover import sections_db as SDB
    if label != RECT and SDB.props(label) is None:
        pytest.skip("%s not in the shape table" % label)
    from steltic_ddm.sections_fiber import FiberSectionBuilder
    rec = _Rec()
    FiberSectionBuilder(rec, Fy=36.0, residual="none").build(1, label, "brace")
    assert set(rec.steel.values()) == {Fy}


def test_pushover_round_and_pipe_use_their_expected_strength():
    from pushover import hinge_models as HM
    prm = HM.template_params()
    for label, Fye in ((ROUND, 46.0 * 1.3), (PIPE, 35.0 * 1.6), (RECT, 50.0 * 1.3)):
        assert abs(HM.brace_spec(label, 300.0, prm).Fye_ksi - Fye) < 1e-9
    h = HM.column_hinge(PIPE, 144.0, 10.0, prm)
    assert abs(h.Fye_ksi - 56.0) < 1e-9 and any("A53" in f for f in h.flags)
    assert abs(HM.column_hinge(ROUND, 144.0, 10.0, prm).Fye_ksi - 59.8) < 1e-9


def test_ebf_brace_capacity_round_hss_fy():
    from nlrha import acceptance as AC
    from pushover import hinge_models as HM
    prm = HM.template_params()
    assert AC.ebf_brace_capacity(ROUND, 120.0, prm)["Fy"] == 46.0
    assert AC.ebf_brace_capacity(PIPE, 120.0, prm)["Fy"] == 35.0
    assert AC.ebf_brace_capacity(RECT, 120.0, prm)["Fy"] == 50.0
