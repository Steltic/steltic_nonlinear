"""Integration fixes made when merging fix/nl-elements, fix/nl-pushover, fix/nl-collect and fix/nl-nlrha.

- zeroLength hinge monitor sign (eleResponse "force"[0:6] is the node-1 force = -spring force);
- brace monitor reads the axial force through hinge_models.brace_axial_force (physical-theory braces);
- acceptance census buckets EBF links and BRBs on their own (never as columns / buckled braces);
- DC / LtdS of a level that is NOT EVALUATED / TARGET NOT REACHED stay None (never 0.00);
- parameter provenance of the groups that Collect does not fill (brb_axial, ebf_link, cyclic_deterioration).
"""
import json
import os
from types import SimpleNamespace as _S

import numpy as np
import openseespy.opensees as ops
import pytest


def _single_spring(mat_args, load):
    """node 1 fixed, node 2 free in DOF 1 only, zeroLength with all six -dir 1..6 (as every hinge spring here)."""
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0); ops.node(2, 0.0, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 0, 1, 1, 1, 1, 1)
    ops.uniaxialMaterial(*mat_args)
    ops.uniaxialMaterial("Elastic", 2, 1e9)
    ops.element("zeroLength", 1, 1, 2, "-mat", 1, 2, 2, 2, 2, 2, "-dir", 1, 2, 3, 4, 5, 6)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, float(load), 0, 0, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("Plain"); ops.system("BandGeneral")


def test_zero_length_monitor_sign_elastic_spring():
    """k = 100, load 5 -> deformation 0.05, spring force +5, plastic deformation 0 (was 0.05 - (-5)/100 = 0.10)."""
    from pushover import nonlinear_model as NM
    _single_spring(("Elastic", 1, 100.0), 5.0)
    ops.integrator("LoadControl", 1.0); ops.algorithm("Linear"); ops.analysis("Static")
    assert ops.analyze(1) == 0
    f = ops.eleResponse(1, "force")
    assert f[0] == pytest.approx(-5.0) and f[6] == pytest.approx(5.0)          # node-1 entry is MINUS the spring force
    th, M = NM.zero_length_spring(1, 1)
    assert th == pytest.approx(0.05) and M == pytest.approx(5.0)
    h = dict(kind="beam", dof=1, K0=100.0)
    pl, M2 = NM.hinge_plastic_deformation(1, h)
    assert pl == pytest.approx(0.0, abs=1e-12) and M2 == pytest.approx(5.0)
    ops.wipe()


def test_zero_length_monitor_sign_yielded_spring():
    """Elastic-perfectly-plastic k = 100, Fy = 3, pushed to d = 0.10 -> plastic 0.07 (= d - Fy/k), not 0.13."""
    from pushover import nonlinear_model as NM
    _single_spring(("ElasticPP", 1, 100.0, 0.03), 1.0)
    ops.integrator("DisplacementControl", 2, 1, 0.01); ops.algorithm("Newton"); ops.test("NormDispIncr", 1e-10, 25)
    ops.analysis("Static")
    assert ops.analyze(10) == 0
    pl, M = NM.hinge_plastic_deformation(1, dict(kind="link", dof=1, K0=100.0))
    assert M == pytest.approx(3.0) and pl == pytest.approx(0.07, abs=1e-9)
    ops.wipe()


def test_brace_monitor_reads_physical_theory_force_element():
    """A physical-theory brace registers a zero-stiffness monitor truss; its force comes from h['force_ele']."""
    from pushover import nonlinear_model as NM
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 3)
    ops.node(1, 0, 0, 0); ops.node(2, 100.0, 0, 0)
    ops.fix(1, 1, 1, 1); ops.fix(2, 0, 1, 1)
    ops.uniaxialMaterial("Elastic", 1, 29000.0); ops.uniaxialMaterial("Elastic", 2, 1e-9)
    ops.element("truss", 10, 1, 2, 1.0, 1)          # carries the force (stands in for the first fibre segment)
    ops.element("truss", 11, 1, 2, 1.0, 2)          # monitor truss (the registered tag)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 29.0, 0, 0)
    ops.constraints("Plain"); ops.numberer("Plain"); ops.system("BandGeneral")
    ops.integrator("LoadControl", 1.0); ops.algorithm("Linear"); ops.analysis("Static"); ops.analyze(1)
    d, P = NM.hinge_plastic_deformation(11, dict(kind="brace", force_ele=10))
    assert d == pytest.approx(0.1) and P == pytest.approx(29.0)
    d, P = NM.hinge_plastic_deformation(10, dict(kind="brace"))
    assert P == pytest.approx(29.0)
    ops.wipe()


def _spec(kind, brb=False):
    if kind == "brace":
        s = _S(IO=1.0, LS=2.0, CP=3.0, IO_t=1.0, LS_t=2.0, CP_t=3.0, dc=0.5, dT=0.5, theta_y=0.5, a_pl=1.0, b_pl=3.0)
        if brb:
            s.Asc = 10.0
        return s
    if kind == "link":
        return _S(IO=0.18, LS=5.0, CP=5.8, theta_y=0.19, a_pl=5.4, b_pl=6.1)
    return _S(IO=0.01, LS=0.03, CP=0.04, theta_y=0.008, a_pl=0.02, b_pl=0.04)


def test_census_buckets_links_and_brbs():
    from pushover import postprocess as PP
    from pushover import performance as PF
    tk = [(1, "beam", False), (2, "col", False), (3, "link", False), (4, "brace", True), (5, "brace", True), (6, "brace", False)]
    pl = [0.02, 0.0, 0.5, -0.8, 0.9, -0.7]
    u = [0, 1, 2, 3]
    run = dict(rec=dict(u=u, V=list(np.linspace(0, 100, 4)), story_u=[[x] for x in u],
                        hinge_pl=[[0.0] * 6] * 3 + [pl], col_N=[]),
               hinge_tags=[t for t, _, _ in tk], heights=[120.0], n_moment_frame_members=1)
    hinges = {t: dict(kind=k, section=("BRB-Asc10" if b else "W"), z=120.0, spec=_spec(k, b), brb=b) for t, k, b in tk}
    a = PP.acceptance(run, hinges, 2.5, "BSE-2N")
    assert a["status"] == PP.EVALUATED
    assert a["monitored"] == dict(beam=1, col=1, brace=1, brb=2, link=1, other=0)
    c = a["census"][0]
    assert (c["col_hinges"], c["col_yielded"]) == (1, 0)                   # the link is NOT a column hinge
    assert (c["link_hinges"], c["link_yielded"]) == (1, 1)
    assert (c["brb_elements"], c["brb_yielded_C"], c["brb_yielded_T"]) == (2, 1, 1)
    assert (c["brace_elements"], c["brace_buckled"]) == (1, 1)                # BRBs never counted as buckled
    g = {(x["kind"], x["section"]): x for x in a["groups"]}
    assert g[("brace", "BRB-Asc10")]["brb"] and "TOTAL" in g[("brace", "BRB-Asc10")]["monitor"]
    assert g[("link", "W")]["units"] == "in" and g[("link", "W")]["DC_IO"] == pytest.approx(0.5 / 0.18)
    PF.augment(a, run, hinges)
    assert a["worst_DC"]["DC"] is not None and a["worst_DC"]["LtdS"] is not None


def test_dc_ltds_none_when_not_evaluated_or_target_not_reached():
    from pushover import postprocess as PP
    from pushover import performance as PF
    u = [0, 1, 2]
    run = dict(rec=dict(u=u, V=[0, 50, 60], story_u=[[x] for x in u], hinge_pl=[[0.0]] * 3, col_N=[]),
               hinge_tags=[1], heights=[120.0], n_moment_frame_members=1)
    hinges = {1: dict(kind="beam", section="W", z=120.0, spec=_spec("beam"))}
    a = PF.augment(PP.acceptance(run, hinges, 5.0, "BSE-2N"), run, hinges)          # push ends at 2 in < 5 in
    assert a["status"] == PP.TARGET_NOT_REACHED and a["worst_DC"]["DC"] is None and a["worst_DC"]["LtdS"] is None
    assert PP.level_verdict(a, "LtdS") is False
    run["n_moment_frame_members"] = 1
    hinges = {1: dict(kind="brace", section="HSS", z=120.0, spec=_spec("brace"))}
    a = PF.augment(PP.acceptance(run, hinges, 1.0, "BSE-1N"), run, hinges)           # MF without beam/col monitors
    assert a["status"] == PP.NOT_EVALUATED and a["worst_DC"]["DC"] is None and PP.level_verdict(a, "DC") is None


def _prm_without(*groups):
    from pushover import hinge_models as HM
    prm = json.loads(json.dumps(HM.load_params()))
    for g in groups:
        prm.pop(g, None)
    for k in [k for k in prm if k.startswith("_")]:
        prm.pop(k)
    # pretend the user verified everything that IS in the file
    for g in ("material", "beam_flexure", "column_flexure", "brace_axial"):
        prm[g]["source"] = "user-verified (test)"; prm[g].pop("unverified", None)
    prm["verified"] = True; prm["source"] = "user-verified (test)"
    return HM.PS.annotate(prm)


def test_groups_not_in_the_file_are_reported_unverified():
    """brb_axial / ebf_link / cyclic_deterioration come from the template when the file lacks them: the run's
    effective `verified` drops and the banner names the group."""
    from pushover import hinge_models as HM
    from pushover import params_schema as PS
    prm = _prm_without("brb_axial", "ebf_link", "cyclic_deterioration")
    assert not prm["_unverified"]
    HM.beam_hinge("W24X84", 300.0, prm)                                      # NSP: cyclic Lambda does not count
    assert prm["verified"] is True
    prm["_analysis"] = "nlrha"
    HM.beam_hinge("W24X84", 300.0, prm)
    assert prm["verified"] is False and PS.TEMPLATE_NOTE in prm["_used_unverified"]["cyclic_deterioration"]
    prm = _prm_without("brb_axial", "ebf_link", "cyclic_deterioration")
    HM.brb_spec("BRB-Asc10.0", 200.0, prm, A_model=15.0, pkg_data={"BRB-ASC10.0": {"Fysc_ksi": 42.0}})
    u = prm["_used_unverified"]["brb_axial"]
    assert prm["verified"] is False and PS.TEMPLATE_NOTE in u and any("omega (FALLBACK" in x for x in u)
    assert "brb_axial" in PS.unverified_text(prm)
    prm = _prm_without("ebf_link")
    HM.link_specs("W14X74", 36.0, prm)
    assert prm["verified"] is False and "ebf_link" in prm["_used_unverified"]


def test_user_supplied_brb_group_stays_verified():
    from pushover import hinge_models as HM
    prm = _prm_without()
    g = prm["brb_axial"]
    g.update(Fysc_ksi=42.0, Ry=1.0, omega=1.36, beta=1.1, KF=1.5, source="supplier test report (test)")
    g.pop("unverified", None)
    prm.pop("_annotated", None); HM.PS.annotate(prm)
    HM.brb_spec("BRB-Asc10.0", 200.0, prm, A_model=15.0, pkg_data={})
    assert prm["verified"] is True and "brb_axial" not in (prm.get("_used_unverified") or {})


def test_template_new_groups_are_flagged_unverified():
    from pushover import hinge_models as HM
    t = HM.load_params()
    for g in ("brb_axial", "ebf_link", "cyclic_deterioration"):
        assert t[g]["unverified"] == ["*"] and t[g]["source"] == ""
        assert t["_unverified"].get(g), g
    assert t["brace_axial"]["mode"] == "table_C3_4" and t["brace_axial"]["nlrha_element"] == "physical_theory"
