"""NL-R2-28: HSS members (Ex21: HSS10X10X5/16 gravity columns, HSS5-1/2X5-1/2X3/8 braces) -- label parsing,
numeric shapes-table fields, the AISC 342-22 Table C3.6 line 4 column hinge, and the DDM fibre builders.
No analysis beyond building a section in a scratch OpenSees domain."""
import math, os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pushover import sections_db as SDB
from pushover import hinge_models as HM


@pytest.mark.parametrize("label,kind,dims", [
    ("HSS5-1/2X5-1/2X3/8", "rect", (5.5, 5.5, 0.375)),
    ("HSS10X10X5/16", "rect", (10.0, 10.0, 0.3125)),
    ("HSS12X8X1/2", "rect", (12.0, 8.0, 0.5)),
    ("HSS3-1/2X2-1/2X1/4", "rect", (3.5, 2.5, 0.25)),
    ("HSS20X12X1", "rect", (20.0, 12.0, 1.0)),
    ("HSS6.625X0.280", "round", (6.625, 0.28)),
    ("HSS5.563X.258", "round", (5.563, 0.258)),
])
def test_parse_hss_label(label, kind, dims):
    g = SDB.parse_hss_label(label)
    assert g["kind"] == kind
    got = (g["H"], g["B"], g["t_nom"]) if kind == "rect" else (g["OD"], g["t_nom"])
    assert got == pytest.approx(dims)
    assert g["t_des"] == pytest.approx(0.93 * g["t_nom"])           # AISC 360-22 B4.2


def test_every_hss_and_pipe_label_in_the_table_parses():
    import csv
    bad = []
    with open(SDB._CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lab = r["AISC_Manual_Label"]
            if lab.upper().startswith(("HSS", "PIPE")) and not SDB.parse_hss_label(lab):
                bad.append(lab)
    assert not bad, bad[:10]
    assert SDB.parse_hss_label("W10X45") is None


def test_hss_props_numeric_fields():
    p = SDB.props("HSS10X10X5/16")
    assert p["Type"] == "HSS" and p["hss"]["kind"] == "rect"
    for k in ("d", "tw", "bf", "tf", "Cw", "rts", "ho"):             # '–' in the table -> None, never a string
        assert p[k] is None
    assert isinstance(p["A"], float) and isinstance(p["Zx"], float)
    ws = SDB.hss_wall_slenderness("HSS10X10X5/16")
    assert ws["lam"] == pytest.approx((10 - 3 * 0.290625) / 0.290625)  # 31.4, the Manual's b/t
    w = SDB.props("W10X45")
    assert "hss" not in w and isinstance(w["d"], float)


def test_column_hinge_hss_table_c36_line4():
    prm = HM.load_params()
    L, PG = 384.0, 40.0
    h = HM.column_hinge("HSS10X10X5/16", L, PG, prm)
    p = SDB.props("HSS10X10X5/16")
    Fye = 50.0 * 1.3                                                  # A500 Gr. C, AISC 341-22 Table A3.2
    r = PG / (p["A"] * Fye)
    lam = (10 - 3 * 0.290625) / 0.290625
    a = min(1.1 * lam ** -1.2 * (1 - r) ** 1.8, 0.05)
    b = min(0.5 * lam ** -0.6 * (1 - r) ** 1.2 - 0.01, 0.08)
    assert not h.force_controlled
    assert h.Fye_ksi == pytest.approx(Fye)
    assert h.a_pl == pytest.approx(a) and h.b_pl == pytest.approx(b) and h.c_res == pytest.approx(0.25)
    assert (h.IO, h.LS, h.CP) == pytest.approx((0.5 * a, 0.75 * b, b))
    red = min(1 - r / 2, 9 / 8 * (1 - r))                             # Eqs. C3-5 / C3-6
    assert h.Mpe_kipin == pytest.approx(p["Zx"] * Fye * red)
    assert h.Lambda == 0.0                                            # no HSS regression -> OFF, flagged
    assert any("Table C3.6 line 4" in f for f in h.flags)
    assert "column_flexure_hss" in prm["_used_unverified"]
    # a job parameter file without the group (every collected file so far): template values, flagged
    prm2 = HM.load_params(); prm2.pop("column_flexure_hss")
    h2 = HM.column_hinge("HSS10X10X5/16", L, PG, prm2)
    assert h2.a_pl == pytest.approx(a) and any("TEMPLATE" in f for f in h2.flags)
    assert prm2["verified"] is False
    # above the force-controlled limit: no hinge
    hf = HM.column_hinge("HSS10X10X5/16", L, 0.7 * p["A"] * Fye, prm)
    assert hf.force_controlled and hf.a_pl == 0.0


def test_column_hinge_round_hss_is_force_controlled():
    h = HM.column_hinge("HSS6.625X0.280", 300.0, 10.0, HM.load_params())
    assert h.force_controlled
    assert any("no row for round HSS" in f for f in h.flags)


def test_column_hinge_w_unchanged():
    prm = HM.load_params()
    h = HM.column_hinge("W10X45", 384.0, 40.0, prm)
    assert "column_flexure_hss" not in prm["_used_unverified"]
    assert h.Fye_ksi == pytest.approx(55.0)


def test_brace_spec_fractional_hss_wall_slenderness():
    b = HM.brace_spec("HSS5-1/2X5-1/2X3/8", 300.0, HM.load_params())
    assert any(f.startswith("HSS b/t=12.77") for f in b.flags)        # (5.5 - 3*0.34875)/0.34875
    assert HM._hss_outside_and_tdes("HSS5-1/2X5-1/2X3/8") == pytest.approx((5.5, 0.34875))
    assert HM._hss_outside_and_tdes("W10X45") == (None, None)


def test_ddm_fibre_builder_hss_labels():
    from steltic_ddm import sections_fiber as F
    assert F.hss_dims("HSS5-1/2X5-1/2X3/8") == pytest.approx((5.5, 5.5, 0.375))
    assert F.hss_dims("HSS6.625X0.280") is None
    OD, t = F.round_hss_dims("Pipe5STD")
    assert OD == pytest.approx(5.563, rel=0.01) and t == pytest.approx(0.258 * 0.93, rel=0.02)
    import openseespy.opensees as ops
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    bld = F.FiberSectionBuilder(ops, Fy=50.0, residual="none")
    out = bld.build(1, "HSS5-1/2X5-1/2X3/8", "brace")
    assert out["A"] == pytest.approx(6.88) and out["H"] == 5.5
    out = bld.build(2, "HSS6.625X0.280", "col")
    assert out["nfib"] > 0
    # a second Fy gets its own material (HSS Fye in the pushover fibre path)
    n0 = len(bld._mat_cache)
    bld.hss_rect(3, "HSS10X10X5/16", residual="none", Fy=65.0)
    assert len(bld._mat_cache) == n0 + 1
    ops.wipe()
