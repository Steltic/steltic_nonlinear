"""NL-R2-32: minor-axis (y-axis) column hinges in the concentrated-plasticity (IMK) model.

AISC 342-22 C3.4a.2.a.1: Mpcey by Eqs. C3-5/C3-6 with Mpey, or (wide-flange Exception) Eqs. C3-7/C3-8; Table C3.6 a/b/c
and IO/LS/CP are not axis-specific; theta_y by Eq. C3-15 with Iy. Independent uniaxial springs per axis."""
import json, math, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_nl_pushover_fixes import _portal, _fr_params          # noqa: E402  (one-bay portal fixture)


def _prm(minor=None):
    prm = _fr_params()
    if minor is not None:
        prm["column_flexure"]["minor_axis_hinge"] = minor
    return prm


# --------------------------------------------------------------------------- spec arithmetic
@pytest.mark.parametrize("ratio", [0.10, 0.50])
def test_minor_spec_w_shape_c3_7_c3_8(ratio):
    from pushover import hinge_models as HM, sections_db as SDB
    prm = _prm()
    sec, L = "W14X211", 156.0
    p = SDB.props(sec)
    Fye = prm["material"]["Fy_ksi"] * prm["material"]["Ry_expected"]
    PG = ratio * p["A"] * Fye
    maj = HM.column_hinge(sec, L, PG, prm)
    mn = HM.column_hinge_minor(sec, L, PG, prm, major=maj)
    assert mn is not None and not mn.force_controlled
    red = min(1 - ratio / 4.0, 1.5 * (1 - ratio))                      # Eq. C3-7 below 0.4, Eq. C3-8 above
    assert red == pytest.approx(1 - ratio / 4.0 if ratio < 0.4 else 1.5 * (1 - ratio))
    Mpey = Fye * min(p["Zy"], 1.6 * p["Sy"])                           # AISC 360-22 F6 yielding with Fye
    assert mn.Mpe_kipin == pytest.approx(Mpey * red, rel=1e-9)
    assert mn.theta_y == pytest.approx(Mpey * red * L / (6 * 29000.0 * p["Iy"]), rel=1e-9)      # Eq. C3-15 with Iy
    for k in ("a_pl", "b_pl", "c_res", "IO", "LS", "CP", "Mc_over_My", "Lambda", "c_det", "PG_over_Pye"):
        assert getattr(mn, k) == getattr(maj, k), k                      # Table C3.6 is not axis-specific
    assert mn.Mpe_kipin < maj.Mpe_kipin and mn.theta_y != maj.theta_y
    assert any("MINOR (y) axis" in f for f in mn.flags)


def test_minor_spec_switch_and_force_controlled():
    from pushover import hinge_models as HM, sections_db as SDB
    sec, L = "W14X211", 156.0
    assert HM.column_hinge_minor(sec, L, 100.0, _prm(False)) is None
    assert HM.column_hinge_minor(sec, L, 100.0, _prm("false")) is None
    assert HM.minor_axis_hinge_on(_prm()) and HM.minor_axis_hinge_on({}) and not HM.minor_axis_hinge_on(_prm("off"))
    prm = _prm()
    p = SDB.props(sec); Fye = prm["material"]["Fy_ksi"] * prm["material"]["Ry_expected"]
    assert HM.column_hinge(sec, L, 0.7 * p["A"] * Fye, prm).force_controlled
    assert HM.column_hinge_minor(sec, L, 0.7 * p["A"] * Fye, prm) is None          # P_G/P_ye > 0.6: elastic both axes


def test_minor_spec_override_expression():
    from pushover import hinge_models as HM, sections_db as SDB
    prm = _prm(); prm["column_flexure"]["Mpce_axial_reduction_minor"] = "1.0"
    mn = HM.column_hinge_minor("W14X211", 156.0, 300.0, prm)
    p = SDB.props("W14X211"); Fye = prm["material"]["Fy_ksi"] * prm["material"]["Ry_expected"]
    assert mn.Mpe_kipin == pytest.approx(Fye * min(p["Zy"], 1.6 * p["Sy"]))


def test_minor_spec_rect_hss_uses_zy_and_c3_5_c3_6():
    from pushover import hinge_models as HM, sections_db as SDB
    prm = _prm()
    sec, L, PG = "HSS12X8X1/2", 156.0, 150.0
    maj = HM.column_hinge(sec, L, PG, prm)
    mn = HM.column_hinge_minor(sec, L, PG, prm, major=maj)
    p = SDB.props(sec)
    r = maj.PG_over_Pye
    red = min(1 - r / 2.0, 9.0 / 8.0 * (1 - r))
    assert mn.Mpe_kipin == pytest.approx(maj.Fye_ksi * p["Zy"] * red, rel=1e-9)
    assert maj.Mpe_kipin == pytest.approx(maj.Fye_ksi * p["Zx"] * red, rel=1e-9)
    assert (mn.a_pl, mn.b_pl, mn.CP) == (maj.a_pl, maj.b_pl, maj.CP)
    assert HM.column_hinge_minor("HSS10.000X0.500", L, PG, prm) is None             # round HSS: no Table C3.6 row


# --------------------------------------------------------------------------- model
def _build(minor=None, push_y=None):
    import openseespy.opensees as ops
    from pushover import nonlinear_model as NM
    pkg = _portal()
    prm = _prm(minor)
    loads, _ = NM.gravity_loads(pkg, prm, verbose=False)
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=False, plasticity="imk")
    return pkg, prm, PG, hinges, stats


def test_imk_column_has_springs_about_both_axes():
    import openseespy.opensees as ops
    from pushover import nonlinear_model as NM
    pkg, prm, PG, hinges, stats = _build()
    cols = {1, 2}
    maj = {t: h for t, h in hinges.items() if h["ele"] in cols and h.get("axis") != "minor"}
    mnr = {t: h for t, h in hinges.items() if h["ele"] in cols and h.get("axis") == "minor"}
    assert len(maj) == 4 and len(mnr) == 4 and stats["col_minor_hinges"] == 4 and stats["column_minor_axis"] == "hinge"
    e = next(x for x in pkg.model.elements if x["tag"] == 1)
    L = math.dist(pkg.model.nodes[1], pkg.model.nodes[3])
    for t, h in mnr.items():
        zl = h["zl"]
        assert t == zl + NM.MINOR_KEY_OFF and zl in maj                  # same zeroLength as the major spring
        assert {h["dof"], maj[zl]["dof"]} == {4, 5}
        assert h["K0"] == pytest.approx(NM.N_STIFF * 6 * e["E"] * e["Iy"] / L)
        assert h["spec"].Mpe_kipin < maj[zl]["spec"].Mpe_kipin
    # hinge_plastic_deformation reads the minor key through its zeroLength (no element with the key's tag)
    tags = set(ops.getEleTags())
    for t, h in mnr.items():
        assert t not in tags and h["zl"] in tags
        v, M = NM.hinge_plastic_deformation(t, h)
        assert abs(v) < 1e-12 and abs(M) < 1e-6


def test_switch_off_restores_major_only_model():
    pkg, prm, PG, hinges, stats = _build(minor=False)
    assert not any(h.get("axis") == "minor" for h in hinges.values())
    assert stats["col_minor_hinges"] == 0 and stats["column_minor_axis"].startswith("elastic")
    assert sum(1 for h in hinges.values() if h["kind"] == "col") == 4


def _push_y(minor, drift=0.04, nstep=80):
    """Static push of the portal about the columns' MINOR axis (Y sway): returns (peak |M| at the column base
    minor spring or element end, Mpcey of that column)."""
    import openseespy.opensees as ops
    from pushover import nonlinear_model as NM, hinge_models as HM
    pkg, prm, PG, hinges, stats = _build(minor=minor)
    h = 168.0
    ops.timeSeries("Linear", 11); ops.pattern("Plain", 11, 11); ops.load(5, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 5, 2, drift * h / nstep); ops.analysis("Static")
    Mpk = 0.0
    for _ in range(nstep):
        assert ops.analyze(1) == 0
        f = ops.eleResponse(1, "localForce")                            # minor moment of column 1 at end i: Iy slot -> local My
        Mpk = max(Mpk, abs(f[4]), abs(f[10]))
    mn = HM.column_hinge_minor("W14X132", h, PG.get(1, 0.0), _prm())
    rot = None
    if minor is not False:
        t = next(t for t, x in hinges.items() if x["ele"] == 1 and x.get("axis") == "minor" and x["end"] == 1)
        rot = NM.hinge_plastic_deformation(t, hinges[t])[0]
    ops.wipe()
    return Mpk, mn, rot


def test_minor_axis_moment_is_capped_by_the_spring():
    M_on, mn, rot = _push_y(None)
    M_off, _, _ = _push_y(False)
    assert M_on <= mn.Mc_over_My * mn.Mpe_kipin * 1.02                  # bounded by the IMK capping strength
    assert M_on >= 0.95 * mn.Mpe_kipin                                   # and the column did yield about y
    assert M_off > 1.5 * mn.Mpe_kipin                                    # elastic minor axis: unbounded (the finding)
    assert abs(rot) > 0.005                                              # minor plastic rotation is monitored


def test_column_info_reports_minor_hinge():
    from nlrha import run as RUN
    pkg, prm, PG, hinges, stats = _build()
    ci = RUN._column_info(pkg, hinges, stats, PG, prm)
    assert ci[1]["flexure"]["modelled"] == dict(major="hinge", minor="hinge")
    pkg, prm, PG, hinges, stats = _build(minor=False)
    ci = RUN._column_info(pkg, hinges, stats, PG, prm)
    assert ci[1]["flexure"]["modelled"] == dict(major="hinge", minor="elastic")


def test_pushover_groups_keep_minor_axis_apart():
    from pushover import postprocess as PP
    pkg, prm, PG, hinges, stats = _build()
    tags = sorted(t for t, h in hinges.items() if h["kind"] == "col")
    run = dict(hinge_tags=tags, heights=[168.0],
               rec=dict(hinge_pl=[[0.01 if hinges[t].get("axis") == "minor" else 0.0 for t in tags]], story_u=[[1.0]], col_N=[[0.0, 0.0]]))
    table, worst, census, drifts, colN = PP._census_drifts(run, hinges, 0)
    axes = sorted((g.get("axis") or "major", g["n"]) for g in table)
    assert axes == [("major", 2), ("major", 2), ("minor", 2), ("minor", 2)]
    assert all(g["DC_CP"] > 0 for g in table if g.get("axis") == "minor")
    assert all(g["DC_CP"] == 0 for g in table if not g.get("axis"))


def test_criteria_text_states_the_switch():
    from nlrha import design_criteria as DC
    on = DC._minor_axis_text({}, dict(nls=dict(plasticity="imk", column_minor_axis="hinge", col_minor_hinges=8), pos=None))
    off = DC._minor_axis_text({"minor_axis_hinge": False}, dict(nls=None, pos=None))
    assert "independent uniaxial spring" in on and "8 springs" in on and "minor_axis_hinge = false" in on
    assert "ELASTIC" in off
