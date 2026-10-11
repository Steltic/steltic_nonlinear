"""NL-R2-22 (round 4): EBF link shear spring keeps the AISC 342-22 Table C2.4 residual strength beyond b.
NL-R2-34: the NLRHA deformation acceptance reads the record PEAK of every hinge (tracked at every committed step).

Table C2.4 (shear-controlled row, as transcribed in pushover/hinge_params.json and checked against the RAG): a = 0.15,
b = 0.17 rad plastic shear deformation, residual strength ratio c = 0.8; Figure C1.1 (Type 1) envelope B-C-D.
Run: PYTHONPATH=. python -m pytest tests/test_nl_r2_22_link_residual.py -q
"""
import copy, inspect, os, sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openseespy.opensees as ops
from pushover import hinge_models as HM, nonlinear_model as NM
from nlrha import run as RN


def _spec(sec="W14X74", e=36.0):
    prm = HM.load_params()
    sh, _, _ = HM.link_specs(sec, e, prm)
    return sh, prm


def _material(sh, prm, tag=11):
    ops.wipe(); ops.model("basic", "-ndm", 1, "-ndf", 1)
    kind = HM.make_link_shear_material(tag, sh, prm)
    ops.testUniaxialMaterial(tag)
    return kind


def _drive(path):
    out = []
    for d in path:
        ops.setStrain(float(d)); out.append(ops.getStress())
    return np.array(out)


def _ramp(points, n=60):
    """Piecewise-linear deformation history through `points` with n sub-steps per leg."""
    path = [0.0]
    for a, b in zip([0.0] + list(points[:-1]), points):
        path += list(np.linspace(a, b, n + 1)[1:])
    return np.array(path)


def test_table_c24_values_in_the_template_match_the_standard():
    """The template ebf_link.shear values are those of AISC 342-22 Table C2.4, shear-controlled row (RAG:
    spec-tbl:AISC_342_22:standard:C2.4): a 0.15, b 0.17, c 0.8, IO 0.005, LS 0.14, CP 0.16."""
    sh = HM.load_params()["ebf_link"]["shear"]
    assert (sh["a"], sh["b"], sh["c"], sh["IO"], sh["LS"], sh["CP"]) == (0.15, 0.17, 0.8, 0.005, 0.14, 0.16)
    s, _ = _spec()
    assert s.c_res == 0.8 and s.b_pl == pytest.approx(0.17 * 36.0) and s.CP == pytest.approx(0.16 * 36.0)


def test_monotonic_backbone_below_a_between_a_and_b_and_beyond_b():
    sh, prm = _spec()
    assert _material(sh, prm) == "Hysteretic"                       # no MinMax wrapper any more
    Vp, dy, a, b = sh.Vp_kip, sh.theta_y, sh.a_pl, sh.b_pl
    e2 = dy + a; e3 = e2 + 0.1 * a                                  # post_cap_frac_of_a = 0.1 (template)
    for sign in (1.0, -1.0):
        _material(sh, prm)
        pts = [0.5 * dy, dy, dy + 0.5 * a, e2, e3, 0.5 * (e3 + dy + b), dy + b + 1e-3, dy + 1.5 * b, dy + 3.0 * b]
        ds = sign * np.unique(np.r_[np.linspace(0.0, dy + 3.0 * b, 1500), pts])
        V = _drive(ds)
        env = np.array([HM.link_shear_envelope(sh, prm, d) for d in ds])
        assert np.allclose(V, env, atol=1e-6 * Vp, rtol=1e-6)        # the OpenSees material IS the closed-form envelope
        f = lambda x: float(abs(V[int(np.argmin(np.abs(np.abs(ds) - x)))]))
        assert f(0.5 * dy) == pytest.approx(0.5 * Vp, rel=1e-6)        # elastic, slope Ks
        assert f(dy) == pytest.approx(Vp, rel=1e-6)                    # B: V_CE = Vp
        assert Vp < f(dy + 0.5 * a) < sh.Vc_over_Vy * Vp               # hardening B -> C (d < a)
        assert f(e2) == pytest.approx(sh.Vc_over_Vy * Vp, rel=1e-3)    # C at Delta_y + a e
        assert f(e3) == pytest.approx(sh.c_res * Vp, rel=1e-3)         # D: residual c Vp (a < d < b)
        assert e3 < dy + b
        assert f(0.5 * (e3 + dy + b)) == pytest.approx(0.8 * Vp, rel=1e-6)
        # beyond b: c Vp, never zero (the former MinMax at Delta_y + b e dropped it to 0)
        for x in (dy + b + 1e-3, dy + 1.5 * b, dy + 3.0 * b):
            assert f(x) == pytest.approx(0.8 * Vp, rel=1e-6)
        assert np.sign(V[-1]) == sign


def test_cyclic_beyond_b_keeps_residual_strength_both_ways():
    sh, prm = _spec()
    _material(sh, prm)
    Vp, dy, b = sh.Vp_kip, sh.theta_y, sh.b_pl
    amp = dy + 1.5 * b
    path = _ramp([amp, -amp, amp, -amp, amp, -0.5 * dy], n=400)
    V = _drive(path)
    for k in range(1, 6):                                             # each reversal point (|d| = amp)
        i = k * 400
        assert abs(V[i]) == pytest.approx(0.8 * Vp, rel=1e-3) and np.sign(V[i]) == np.sign(path[i])
    energy = float(np.sum(0.5 * (V[1:] + V[:-1]) * np.diff(path)))
    assert energy > 4 * 0.8 * Vp * b                                  # stable, full loops
    # after the cycles, a push beyond the previous excursions runs along the residual plateau (never zero)
    more = _drive(np.linspace(-0.5 * dy, 2.0 * amp, 600))
    beyond = np.linspace(-0.5 * dy, 2.0 * amp, 600) > amp + 1e-6
    assert np.allclose(more[beyond], 0.8 * Vp, rtol=1e-6)
    _drive(np.linspace(2.0 * amp, -2.0 * amp, 1200))
    assert ops.getStress() == pytest.approx(-0.8 * Vp, rel=1e-6)


def test_link_element_in_series_with_flexure_keeps_residual_beyond_b():
    """build_link_imk (shear spring + IMK flexural end springs): a push to 2 b e ends at c Vp, not at zero."""
    from test_nl_elements import _single_link
    hinges, stats = _single_link()
    t = [t for t, h in hinges.items() if h["kind"] == "link"][0]
    sh = hinges[t]["spec"]
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(2, 0, 0, 1.0, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("UmfPack"); ops.test("NormDispIncr", 1e-9, 50); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 2, 3, 0.004); ops.analysis("Static")
    u = 0.0
    while u < sh.theta_y + 2.0 * sh.b_pl:
        assert ops.analyze(1) == 0
        u = ops.nodeDisp(2, 3)
    ops.reactions()
    assert -ops.nodeReaction(1, 3) == pytest.approx(sh.c_res * sh.Vp_kip, rel=0.02)
    v, V = NM.hinge_plastic_deformation(t, hinges[t])
    assert v > sh.b_pl and V == pytest.approx(sh.c_res * sh.Vp_kip, rel=0.02)


def test_fibre_path_uses_the_same_material():
    src = inspect.getsource(NM.link_shear_spring)
    assert "HM.make_link_shear_material" in src
    assert "MinMax" not in inspect.getsource(HM.make_link_shear_material).split('"""')[-1]


def test_residual_above_capping_strength_is_clamped():
    sh, prm = _spec()
    sh2 = copy.deepcopy(sh); sh2.c_res = sh.Vc_over_Vy + 0.3
    _material(sh2, prm)
    V = _drive(np.linspace(0, sh.theta_y + 3 * sh.b_pl, 800))
    assert V.max() <= sh.Vc_over_Vy * sh.Vp_kip * (1 + 1e-9)          # no hardening branch past D


# ----------------------------------------------------------------------------- NL-R2-34: peaks, not the last state
def test_update_peaks_keeps_the_envelope():
    peak, signed = {1: 0.0, 2: 0.0}, {1: (0.0, 0.0), 2: (0.0, 0.0)}
    for vals in ([0.1, -0.2], [0.7, -0.1], [0.2, 0.3], [-0.4, 0.0]):
        RN.update_peaks(peak, signed, [1, 2], vals)
    assert peak == {1: 0.7, 2: 0.3} and signed == {1: (0.7, -0.4), 2: (0.3, -0.2)}


def test_run_record_tracks_hinge_peaks_every_committed_step():
    """The peak is folded in right after each committed step, before (and independently of) the viewer-frame sampling
    (every rec_every * dt_max of analysis time), which only stores what was already computed."""
    src = inspect.getsource(RN.run_record)
    i_step = src.index("step += 1")
    i_peak = src.index("update_peaks(peak_def, signed_def, hz, hvals)")
    i_frame = src.index("if t >= next_rec - 1e-9:")
    assert i_step < i_peak < i_frame
    assert src.count("hinge_deformations(") == 1 and "peak_def[tg] = max(" not in src
    assert "link_state_note(hinges, peak_def)" in src


def test_frame_sampling_misses_a_link_peak_that_every_step_tracking_keeps():
    """A link driven past b and back within one viewer-frame interval: sampling every 5th step (the former
    behaviour) reads a deformation below b; every-step tracking keeps the peak, and the 16.4.1.1 screen flags it."""
    sh, prm = _spec()
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0, 0, 0); ops.node(2, 0, 0, 0)
    ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 1, 1, 0, 1, 1, 1)
    HM.make_link_shear_material(77, sh, prm)
    ops.element("zeroLength", 9001, 1, 2, "-mat", 77, 77, 77, 77, 77, 77, "-dir", 1, 2, 3, 4, 5, 6)
    hinges = {9001: dict(kind="link", ele=4, spec=sh, dof=3, K0=sh.Ks)}
    hz, K0 = [9001], {9001: sh.Ks}
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.sp(2, 3, 1.0)
    ops.system("UmfPack"); ops.numberer("Plain"); ops.constraints("Transformation"); ops.test("NormDispIncr", 1e-10, 10)
    ops.algorithm("Newton")
    target = [0.0] + list(np.linspace(0.5, sh.theta_y + sh.b_pl + 0.4, 9)) + [sh.theta_y + 0.5 * (sh.a_pl + sh.b_pl)] * 6
    every, sampled = ({9001: 0.0}, {9001: (0.0, 0.0)}), ({9001: 0.0}, {9001: (0.0, 0.0)})
    for k in range(1, len(target)):
        ops.integrator("LoadControl", target[k] - target[k - 1]); ops.analysis("Static"); assert ops.analyze(1) == 0
        vals = RN.hinge_deformations(hz, hinges, K0)
        RN.update_peaks(*every, hz, vals)
        if k % 5 == 0:                                                # the former frame-only update
            RN.update_peaks(*sampled, hz, vals)
    assert every[0][9001] > sh.b_pl > sampled[0][9001]
    from test_nlrha_fixes import _rec, _eval
    recs = [_rec(i) for i in range(11)]
    for r, pk in [(recs[0], every)] + [(r, sampled) for r in recs[1:]]:      # one hinge set in every record
        r.update(peak_def=dict(pk[0]), signed_def=dict(pk[1]), specs={9001: sh},
                 hinges_meta={9001: dict(kind="link", section="W14X74", z=144.0)})
    acc = _eval(recs, planned=[dict(record="R%d" % i) for i in range(11)])
    f0 = " ".join(acc["per_record"][0]["flags"]); f1 = " ".join(acc["per_record"][1]["flags"])
    assert "beyond the valid modelling range (b)" in f0 and acc["per_record"][0]["unacceptable"]
    assert "beyond the valid modelling range (b)" not in f1
