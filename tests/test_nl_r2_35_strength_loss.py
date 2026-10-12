"""NL-R2-35: a fibre pushover whose beams/columns cannot degrade must still explain every drop of its capacity curve.

Ex29 (dual SMF + BRBF, fibre NSP, round 2): base shear fell 2158 -> 1237 kip (X) and 1997 -> 932 kip (Y) in one step
while the report said "member strength degradation is NOT modelled". The drops are BRBs reaching b (MinMax removal,
AISC 342-22 E3.2b(c) / Table C3.3). These tests pin (1) the MinMax behaviour the attribution relies on (force -> 0,
recorded deformation frozen), (2) the attribution itself, (3) the corrected disclosure text, (4) the report table."""
import os, sys
from types import SimpleNamespace
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pushover import postprocess as PP, hinge_models as HM, nonlinear_model as NM, report_supplement as RS


def _run(V, forces, pl=None, tags=(11, 12)):
    n = len(V)
    return dict(rec=dict(u=[float(i) for i in range(n)], V=list(V), hinge_M=forces,
                         hinge_pl=pl if pl is not None else [[0.0] * len(tags) for _ in range(n)]),
                hinge_tags=list(tags))


def _hinges():
    return {11: dict(ele=1329, kind="brace", section="BRB-Asc12", z=1318.0, brb=True, spec=SimpleNamespace(b_t=5.0, b_pl=5.0)),
            12: dict(ele=7, kind="beam", section="W27X114", z=240.0, form="fibre_end", spec=SimpleNamespace(b_pl=0.05))}


def test_drop_attributed_to_brb_removal():
    V = [0, 50, 90, 100, 100.5, 55, 57]
    f = [[0, None], [200, None], [400, None], [480, None], [490, None], [0.0, None], [0.0, None]]
    s = PP.strength_loss_events(_run(V, f), _hinges())
    assert s["n_drops"] == 1 and s["n_unattributed"] == 0
    d = s["drops"][0]
    assert d["step"] == 5 and d["after_peak"] and abs(d["frac_Vmax"] - 45.5 / 100.5) < 1e-9
    c = d["causes"][0]
    assert c["ele"] == 1329 and c["brb"] and c["force_before"] == 490 and c["force_after"] == 0.0
    assert "Table C3.3" in c["why"] and "BRB 1329" in s["text"][0]
    assert len(s["losses"]) == 1                                  # the zero force afterwards is not a new loss


def test_unattributed_and_small_drops():
    V = [0, 50, 100, 97, 60, 61]                                  # 3 % (ignored) then 37 % with no component loss
    f = [[0, None], [200, None], [400, None], [390, None], [380, None], [381, None]]
    s = PP.strength_loss_events(_run(V, f), _hinges())
    assert [d["step"] for d in s["drops"]] == [4] and s["n_unattributed"] == 1
    assert "NOT attributed" in s["text"][0]


def test_fibre_end_passing_b_is_not_a_strength_loss_but_a_spring_is():
    V = [0, 50, 100, 60]
    pl = [[0, 0], [0, 0.01], [0, 0.04], [0, 0.06]]
    f = [[0, None]] * 4
    assert PP.strength_loss_events(_run(V, f, pl), _hinges())["n_unattributed"] == 1      # fibre end: never a cause
    h = _hinges(); h[12] = dict(h[12], form=None)                                        # IMK spring: passing b is
    s = PP.strength_loss_events(_run(V, f, pl), h)
    assert s["n_unattributed"] == 0 and "passed b" in s["drops"][0]["causes"][0]["why"]


def test_minmax_brb_force_zero_and_deformation_frozen_past_b():
    """The assumption the attribution rests on: a Steel4+MinMax BRB pulled past Delta_y + b carries exactly zero force and
    its recorded deformation (corotTruss 'deformation' = L x material strain) freezes just below b."""
    import openseespy.opensees as ops
    prm = HM.load_params()
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 3)
    L = 200.0
    ops.node(1, 0, 0, 0); ops.node(2, L, 0, 0)
    ops.fix(1, 1, 1, 1); ops.fix(2, 0, 1, 1)
    spec = HM.brb_spec("BRB-Asc10", L, prm, A_model=14.5, pkg_data={"BRB-ASC10": {"Fysc_ksi": 42.0}})
    assert HM.make_brb_material(1, spec, prm) == "Steel4+MinMax"
    ops.element("corotTruss", 1, 1, 2, spec.A_model, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 1.0, 0, 0)
    ops.constraints("Plain"); ops.numberer("Plain"); ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-8, 50); ops.algorithm("Newton")
    du = spec.b_t / 40.0
    ops.integrator("DisplacementControl", 2, 1, du); ops.analysis("Static")
    F, D = [], []
    for _ in range(48):
        assert ops.analyze(1) == 0
        F.append(ops.eleResponse(1, "axialForce")[0]); D.append(ops.eleResponse(1, "deformation")[0])
    ops.wipe()
    i_fail = next(i for i, f in enumerate(F) if abs(f) < 1e-9)
    assert F[i_fail - 1] > 0.9 * spec.Pye_kip                      # was carrying (hardened) yield force
    assert all(abs(f) < 1e-9 for f in F[i_fail:])                   # removed for good
    assert D[-1] < spec.b_t and abs(D[-1] - D[i_fail]) < 1e-9       # frozen below b although the node kept moving


def _stats(**kw):
    st = dict(col=4, beam=4, brace=0, brace_nonlinear=0, released_ends=0, force_controlled=0, panel_zones=0)
    st.update(kw)
    return st


def test_fibre_disclosure_with_and_without_strength_losing_components():
    prm = HM.load_params()
    st = NM.finish_stats(_stats(), {}, prm, "fibre")               # SMF only: text unchanged (byte-identical)
    assert st["model_warnings"] == ["FIBRE NSP: member strength degradation is NOT modelled (no local buckling / fracture in the "
                                    "fibres); use --plasticity imk for the AISC 342 backbones with post-capping strength loss"]
    assert "no member-level strength loss" in st["degradation"]["members"]
    st = NM.finish_stats(_stats(brace=80, brace_nonlinear=80, brb=80), {}, prm, "fibre")
    w = " ".join(st["model_warnings"])
    assert "beam/column strength degradation is NOT modelled" in w and "BRBs (80)" in w and "strength-loss events" in w
    assert "no member-level strength loss" not in st["degradation"]["members"]
    assert st["degradation"]["members"].startswith("beams / columns: NONE")
    from nlrha import model as MD                                  # the 16.3.1 statement still says fibre members do not degrade
    assert not MD.degradation_statement(prm, st)["demonstrated"]


def test_report_table_and_package_json():
    V = [0, 50, 100, 55, 20]
    f = [[0, None], [200, None], [400, None], [0.0, None], [0.0, None]]
    s = PP.strength_loss_events(_run(V, f), _hinges())
    assert s["n_drops"] == 2 and s["n_unattributed"] == 1
    html = RS._strength_loss_html(s, dict(plasticity="fibre"))
    assert "Strength-loss events" in html and "BRB 1329" in html and "NOT attributed" in html and "E3.2b(c)" in html
    js = RS._sle_json(s)
    assert js["n_drops"] == 2 and js["drops"][0]["causes"][0]["ele"] == 1329
    import json
    json.dumps(js)
    assert RS._strength_loss_html(dict(drops=[]), {}) == ""


def test_removal_at_several_levels_in_one_step_is_flagged():
    """Ex29 Y (current main): 10 BRBs of storeys 7-11 removed in the single step 34 -- flagged as step-size dependent."""
    h = _hinges(); h[13] = dict(h[11], ele=1335, z=1472.0)
    V = [0, 50, 100, 47]
    f = [[0, None, 0], [300, None, 300], [600, None, 650], [0.0, None, 0.0]]
    s = PP.strength_loss_events(_run(V, f, tags=(11, 12, 13)), h)
    d = s["drops"][0]
    assert d["multi_level"] and d["n_levels"] == 2 and "ONE step" in s["text"][0] and "step size" in s["text"][0]
    assert "ONE step" in RS._strength_loss_html(s, dict(plasticity="fibre"))
    f[3][2] = 640.0                                                   # only one level loses its BRB: not flagged
    s = PP.strength_loss_events(_run(V, f, tags=(11, 12, 13)), h)
    assert not s["drops"][0]["multi_level"] and "ONE step" not in s["text"][0]


def test_link_drop_attributed_at_the_capping_point_and_at_b():
    """NL-R2-22 (round 4): an EBF link keeps its residual c Vp (no MinMax) -- its force never collapses, so a link-caused
    drop is attributed by its plastic deformation crossing the capping point a (strength from Vc to c Vp) or b, as an
    IMK hinge passing b is; it is not left 'NOT attributed'."""
    h = {21: dict(ele=284, kind="link", section="W14X74", z=168.0, spec=SimpleNamespace(a_pl=0.30, b_pl=0.34, e_in=2.0))}
    V = [0, 50, 100, 101, 80, 81, 70]
    f = [[0.0], [200.0], [300.0], [310.0], [250.0], [250.0], [248.0]]            # Vc 310 -> c Vp 250: never below 5 %
    pl = [[0.0], [0.0], [0.1], [0.29], [0.31], [0.32], [0.36]]
    s = PP.strength_loss_events(_run(V, f, pl, tags=(21,)), h)
    assert s["n_drops"] == 2 and s["n_unattributed"] == 0
    c0 = s["drops"][0]["causes"][0]; c1 = s["drops"][1]["causes"][0]
    assert s["drops"][0]["step"] == 4 and c0["ele"] == 284 and "capping point a" in c0["why"] and "c Vp" in c0["why"]
    assert s["drops"][1]["step"] == 6 and "passed b" in c1["why"] and "residual c Vp kept" in c1["why"]
    # without the link deformation crossing, the same drop stays unattributed (no force collapse)
    s = PP.strength_loss_events(_run(V, f, [[0.0]] * 7, tags=(21,)), h)
    assert s["n_unattributed"] == 2


def test_link_disclosure_no_longer_says_minmax():
    srcs = NM.fibre_strength_loss_sources(dict(links=12))
    assert len(srcs) == 1 and "MinMax" not in srcs[0] and "c Vp" in srcs[0] and "Table C2.4" in srcs[0]
    s = PP.strength_loss_events(_run([0, 50, 100, 55], [[0, None], [200, None], [400, None], [0.0, None]]), _hinges())
    html = RS._strength_loss_html(s, dict(plasticity="fibre"))
    assert "EBF links past the capping deformation a fall" in html and "links past b are removed" not in html
