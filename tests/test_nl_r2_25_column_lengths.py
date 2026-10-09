"""NL-R2-25: the 16.4.2.1 force-controlled column check uses the HR design's per-axis unbraced lengths (AISC 360-22 E2 /
F2) from design/calc_package.json; without them it keeps K = 1 and the member length (the previous default).
Ex33: W24X76 crane column, 456 in, weak-axis brace points 0/168/336/456 in -> Lcy = Lb = 168 in, KL/r = 87.5 and
phi Pn = 575.9 kip, which is the HR design's phiPn_weak_Lc168_kip (was KL/r = 237.5, phi Pn = 89.7 kip)."""
import math, os, sys
from types import SimpleNamespace
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from nlrha import acceptance as AC


def _pkg(members, z2=456.0, sec="W24X76"):
    model = SimpleNamespace(nodes={1: (0.0, 0.0, 0.0), 2: (0.0, 0.0, z2)}, elements=[dict(tag=27, n1=1, n2=2)])
    return SimpleNamespace(model=model, schedule={27: dict(section=sec)}, calc=dict(members=members))


_EX33 = dict(id="lateral_col-W24X76", inputs=dict(role="lateral_col", section="W24X76", length_in=456.0,
                                                  brace_points_in=[0.0, 168.0, 336.0, 456.0], Lb_in=120.0))
_PRM = {"material": {"Fy_ksi": 50.0, "Ry_expected": 1.1}}


def test_ex33_brace_points_give_the_design_weak_axis_length():
    sec, e, L, KLr, notes, cap = AC._column_caps(_pkg([_EX33]), 27, _PRM, 0.9, 1.0)
    assert (cap["Lcx"], cap["Lcy"], cap["Lb"]) == (456.0, 168.0, 168.0)
    assert KLr == pytest.approx(168.0 / 1.92, rel=1e-3)                       # 87.5, weak axis governs (456/9.69 = 47)
    assert cap["phiPn"] == pytest.approx(575.9, abs=0.5)                      # HR calc_package phiPn_weak_Lc168_kip
    Mn, _, _ = AC.column_Mn("W24X76", 168.0, Fy=50.0)
    assert cap["phiMnx"] == pytest.approx(0.9 * Mn)
    assert "brace_points_in" in cap["length_source"] and any("Lcy 168" in n for n in notes)


def test_element_inside_the_brace_points_is_cut_at_its_own_span():
    pkg = _pkg([_EX33]); pkg.model.nodes[1] = (0.0, 0.0, 336.0)               # upper segment element, 336-456 in
    sec, e, L, KLr, notes, cap = AC._column_caps(pkg, 27, _PRM, 0.9, 1.0)
    assert L == 120.0 and cap["Lcy"] == 120.0 and cap["Lb"] == 120.0


def test_per_axis_lengths_need_a_matching_member_length():
    rec = dict(id="lateral_col-W10X45", inputs=dict(kind="col", role="lateral_col", section="W10X45", length_in=384.0,
                                                     Lb_in=192.0, Lcx_in=384.0, Lcy_in=192.0))
    _, _, L, KLr, _, cap = AC._column_caps(_pkg([rec], z2=384.0, sec="W10X45"), 27, _PRM, 0.9, 1.0)
    assert (cap["Lcx"], cap["Lcy"], cap["Lb"]) == (384.0, 192.0, 192.0)
    # a group record of another length is not this element's bracing -> previous default
    _, _, L, KLr, _, cap = AC._column_caps(_pkg([rec], z2=300.0, sec="W10X45"), 27, _PRM, 0.9, 1.0)
    assert (cap["Lcy"], cap["Lb"]) == (300.0, 300.0) and "no per-axis bracing" in cap["length_source"]


def test_no_design_data_keeps_the_previous_default():
    for members in ([], [dict(id="x", inputs=dict(role="lateral_col", section="W24X76", length_in=456.0, Lc_in=200.0))]):
        _, _, L, KLr, notes, cap = AC._column_caps(_pkg(members), 27, _PRM, 0.9, 1.0)
        Pn, KLr0 = AC.column_Pn("W24X76", 456.0, Fy=50.0)
        Mn, _, _ = AC.column_Mn("W24X76", 456.0, Fy=50.0)
        assert KLr == pytest.approx(KLr0) and cap["phiPn"] == pytest.approx(0.9 * Pn) and cap["phiMnx"] == pytest.approx(0.9 * Mn)
        assert KLr == pytest.approx(237.5, abs=0.1)                           # the R5 Ex33 value


def test_most_conservative_of_several_matching_records():
    a = dict(id="a", inputs=dict(kind="col", role="gravity_col", section="W10X45", length_in=384.0, Lcy_in=128.0))
    b = dict(id="b", inputs=dict(kind="col", role="lateral_col", section="W10X45", length_in=384.0, Lcy_in=192.0))
    dl = AC.design_column_lengths(_pkg([a, b], z2=384.0, sec="W10X45"), "W10X45", 0.0, 384.0, 384.0)
    assert dl["Lcy"] == 192.0 and "[b]" in dl["source"]


def test_column_Pn_per_axis():
    Pn, KLr = AC.column_Pn("W24X76", 456.0, Fy=50.0, Lcx=456.0, Lcy=168.0)
    assert KLr == pytest.approx(max(456.0 / 9.69, 168.0 / 1.92), rel=1e-3)
    Pn2, KLr2 = AC.column_Pn("W24X76", 456.0, Fy=50.0, Lcx=456.0, Lcy=40.0)      # strong axis governs
    assert KLr2 == pytest.approx(456.0 / 9.69, rel=1e-3)
