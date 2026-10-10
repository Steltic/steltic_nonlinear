"""NL-R2-30: which moments enter the 16.4.2.1 force-controlled column check.

AISC 342-22 C3.4a.2b (nonlinear procedures): "Columns or braces classified as deformation-controlled for flexure shall
also satisfy Equations C3-9, C3-10, and C3-11 when the column or brace is in compression except that values for mx and
my shall be taken as unity" (C3-9: MCx = MCEx when flexure is deformation-controlled). So a deformation-controlled
moment is combined with the axial force in compression (analysed moment, M_CE, m = 1) and NOT in net tension; a
force-controlled moment (C3.4b.2b) stays in both equations, transformed like the axial force."""
import pytest

from nlrha import acceptance as AC

CAP = dict(phiPn=1000.0, phiTn=1200.0, phiMnx=6000.0, phiMny=3000.0, MCEx=8000.0, MCEy=4000.0, Pye=1450.0)
QNS = dict(P=200.0, Mmaj=0.0, Mmin=0.0)
DEF = dict(major="deformation", minor="deformation", modelled={"major": "hinge", "minor": "elastic"})
FRC = dict(major="force", minor="force", modelled={})
ARGS = (0.8, 1.5, 1.0, 1.3)          # frac_D, SMS, Ie, gamma


def test_deformation_controlled_moments_enter_the_compression_check_with_m_1_and_MCE():
    c = AC._fc_column_check(dict(Pc=500.0, Pt=None, Mmaj=4000.0, Mmin=1000.0), QNS, CAP, DEF, *ARGS)
    r = c["demand"] / CAP["phiPn"]                                   # 650.8 / 1000
    assert r == pytest.approx(0.6508)
    # C3-9 with C3-13 (= H1-1a): r + 8/9 (MUx / MCEx + MUy / MCEy), analysed moments not amplified
    assert c["DC_H1_comp"] == pytest.approx(r + 8 / 9 * (4000 / 8000 + 1000 / 4000))
    assert c["Mr_maj"] == 4000.0 and c["Mr_min"] == 1000.0


def test_deformation_controlled_moments_are_not_combined_with_net_tension():
    # (0.9 - 0.18) 160 - 1.3 (200 - (-150)) = -339.8 -> 339.8 kip net tension
    t = AC._fc_column_check(dict(Pc=None, Pt=-150.0, Mmaj=7000.0, Mmin=3500.0), QNS, CAP, DEF, *ARGS)
    assert t["demand_tension"] == pytest.approx(339.8)
    assert t["DC_H1_tens"] == pytest.approx(339.8 / 1200.0)          # axial tension only
    # large uplift: 115.2 - 1.3 (200 + 1000) = -1444.8 kip -> the tension case governs and says why no moment is in it
    g = AC._fc_column_check(dict(Pc=None, Pt=-1000.0, Mmaj=800.0, Mmin=0.0), QNS, CAP, DEF, *ARGS)
    assert g["DC"] == pytest.approx(1444.8 / 1200.0)
    assert g["governing"].startswith("axial tension") and "C3.4a.2b" in g["governing"]


def test_force_controlled_moments_stay_in_the_tension_check():
    t = AC._fc_column_check(dict(Pc=None, Pt=-150.0, Mmaj=1500.0, Mmin=0.0), QNS, CAP, FRC, *ARGS)
    Mr2 = 1.3 * 1500.0                                               # Mns = 0: only the transformed seismic part
    assert t["DC_H1_tens"] == pytest.approx(339.8 / 1200.0 + 8 / 9 * Mr2 / 6000.0)
    assert t["governing"].startswith("H1-1 tension")
    # mixed: the force-controlled minor axis stays, the deformation-controlled major axis drops out in tension
    mix = dict(major="deformation", minor="force", modelled={"major": "hinge"})
    m = AC._fc_column_check(dict(Pc=None, Pt=-150.0, Mmaj=7000.0, Mmin=500.0), QNS, CAP, mix, *ARGS)
    assert m["DC_H1_tens"] == pytest.approx(339.8 / 1200.0 + 8 / 9 * (1.3 * 500.0) / 3000.0)


def test_small_axial_ratio_without_moment_is_the_plain_ratio():
    # a small net tension (Eq. 16.4-2) and no moment: D/C = T / phi Tn
    t = AC._fc_column_check(dict(Pc=None, Pt=60.0, Mmaj=0.0, Mmin=0.0), QNS, CAP, DEF, *ARGS)
    T = 1.3 * (200.0 - 60.0) - 0.72 * 160.0                          # 182 - 115.2 = 66.8 kip tension
    assert t["demand_tension"] == pytest.approx(T)
    assert T / 1200.0 < 0.2 and t["DC_H1_tens"] == pytest.approx(T / 1200.0)   # not H1-1b's r / 2


def test_record_dc_tension_instant_with_large_hinge_moments_does_not_govern():
    ev = dict(conc_c=(300.0, 2000.0, 500.0), conc_t=(-150.0, 9000.0, 4500.0), Pc=300.0, Pt=-150.0)
    rdc = AC._record_column_dc(ev, QNS, CAP, DEF, *ARGS)
    comp = AC._fc_column_check(dict(Pc=300.0, Pt=None, Mmaj=2000.0, Mmin=500.0), QNS, CAP, DEF, *ARGS)["DC_H1_comp"]
    assert rdc == pytest.approx(max(comp, 339.8 / 1200.0, AC._fc_column_check(dict(Pc=300.0, Pt=-150.0, Mmaj=0.0, Mmin=0.0),
                                                                                    QNS, CAP, DEF, *ARGS)["DC_C3_10"]))
