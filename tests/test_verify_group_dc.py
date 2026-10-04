"""Independent-review regression: a (kind, section, level) acceptance group reports the largest MEMBER D/C, each
member against its own limits (pushover NSP 7.5.3 and NLRHA 16.4.2.2).

Columns of one section on one level carry different Table C3.6 limits (a, b vary with P_G/P_ye), and beams of one
section different C5.5 limits (span, Lb, RBS cut). Before this fix the group D/C was the D/C of the member with the
LARGEST deformation, against that member's limits, so a more heavily loaded column with a smaller deformation but a
much smaller limit was missed (hand case below: reported CP D/C 0.33, true 1.25)."""
import os
import sys
import types
from types import SimpleNamespace as S

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def _sp(CP):
    return S(IO=CP / 4.0, LS=0.75 * CP, CP=CP, theta_y=0.008, a_pl=CP, b_pl=CP)


def test_nsp_group_dc_is_the_largest_member_dc():
    from pushover import postprocess as PP
    hinges = {1: dict(kind="col", section="W14X90", z=120.0, spec=_sp(0.06)),      # light column: big theta, big limit
              2: dict(kind="col", section="W14X90", z=120.0, spec=_sp(0.012))}     # heavy column: smaller theta, small limit
    run = dict(rec=dict(u=[0.0, 1.0, 2.0], V=[0.0, 50.0, 100.0], story_u=[[0.0], [1.0], [2.0]],
                        hinge_pl=[[0.0, 0.0], [0.0, 0.0], [0.02, 0.015]], col_N=[]),
               hinge_tags=[1, 2], heights=[120.0], n_moment_frame_members=1)
    a = PP.acceptance(run, hinges, 2.0, "BSE-2N")
    g = a["groups"][0]
    assert a["status"] == PP.EVALUATED
    assert g["theta_pl_max"] == pytest.approx(0.02)                    # largest deformation still reported
    assert a["worst_DC"]["CP"] == pytest.approx(0.015 / 0.012)         # 1.25 (was 0.02 / 0.06 = 0.33)
    assert a["worst_DC"]["LS"] == pytest.approx(0.015 / 0.009)
    assert a["worst_DC"]["IO"] == pytest.approx(0.015 / 0.003)
    assert g["CP"] == pytest.approx(0.012) and g["theta_at_governing"] == pytest.approx(0.015)
    assert PP.level_verdict(a, "CP") is False


def test_nlrha_group_dc_uses_each_elements_own_limits():
    from test_nlrha_fixes import _rec, _eval
    res = []
    for i in range(11):
        r = _rec(i)
        r["peak_def"] = {11: 0.03, 12: 0.02}
        r["signed_def"] = {11: (0.03, -0.01), 12: (0.02, -0.01)}
        r["specs"] = {11: S(CP=0.06, b_pl=0.07), 12: S(CP=0.016, b_pl=0.025)}
        r["hinges_meta"] = {11: dict(kind="col", section="W14X90", z=144.0), 12: dict(kind="col", section="W14X90", z=144.0)}
        res.append(r)
    acc = _eval(res, planned=[dict(record="R%d" % i) for i in range(11)])
    row = [g for g in acc["deformation_groups"] if g["kind"] == "col"][0]
    assert row["DC_CP"] == pytest.approx(0.02 / 0.016)                 # 1.25 (was 0.03 / 0.06 = 0.50)
    assert row["DC_valid"] == pytest.approx(0.02 / 0.025)              # 0.80 (element 11: 0.03 / 0.07 = 0.43)
    assert row["Qu_rad"] == pytest.approx(0.02) and row["CP"] == pytest.approx(0.016)
    assert row["Qu_rad_max"] == pytest.approx(0.03)
    assert acc["verdict"]["deformation_ok"] is False and acc["verdict"]["valid_range_ok"] is True
