"""Regression tests for the NLRHA review items NL-12, NL-13, NL-17, NL-18, NL-20 and NL-25.

No OpenSees analysis runs here: drift, gravity, acceptance and rendering are exercised on small synthetic packages
and on a fixture extracted from the review's Ex16 run (tests/fixtures/ex16_drift: diaphragm/base geometry and the
11 recorded master-frame histories of nlrha/raw_results.pkl)."""
import json, math, os, sys, types
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
FX = os.path.join(HERE, "fixtures", "ex16_drift")
EX16_JOB = "/home/claude/hubdata/snl_jobs/Ex16_IMF_3levels_Zplan_school"


def _ch16():
    return json.load(open(os.path.join(ROOT, "nlrha", "ch16_params.json")))


def _ex16_pkg():
    g = json.load(open(os.path.join(FX, "geometry.json")))
    model = types.SimpleNamespace(nodes={int(k): tuple(v) for k, v in g["nodes"].items()}, fixes={int(k): v for k, v in g["fixes"].items()},
                                  diaphragms=[(p, m, s) for p, m, s in g["diaphragms"]], elements=[], masses={})
    return types.SimpleNamespace(model=model, schedule={}), g


# --------------------------------------------------------------------------- NL-12 drift at vertically aligned points
def test_ex16_aligned_point_drift_reproduces_review_recomputation():
    """scratch/verify_nl/drift16.py: mean over 11 records, points common to consecutive levels ->
    X/Y 1.44/1.64, 0.66/0.75, 0.40/0.45 % (reported by the old corner-node monitor: 1.44/1.65, 0.82/0.87, 0.94/1.76 %)."""
    from nlrha import drift as DR
    pkg, g = _ex16_pkg()
    frames = np.load(os.path.join(FX, "master_frames.npz"))
    pts_c = DR.drift_points(pkg, hull_only=False, consecutive_only=True)
    A = [DR.drifts_from_master_frames(pkg, frames[k], pts_c)[0] for k in sorted(frames.files)]
    got = np.round(100 * np.mean(A, 0), 2)
    assert got.tolist() == [[1.44, 1.64], [0.66, 0.75], [0.40, 0.45]]
    old = np.round(100 * np.mean(g["reported_peak_story_drift"], 0), 2)
    assert old.tolist() == [[1.44, 1.65], [0.82, 0.87], [0.94, 1.76]]          # the defect the review found
    # the convex-hull reduction used at run time is exact (rigid diaphragms: affine displacement field)
    pts_h = DR.drift_points(pkg, hull_only=True, consecutive_only=True)
    Ah = [DR.drifts_from_master_frames(pkg, frames[k], pts_h)[0] for k in sorted(frames.files)]
    assert np.allclose(A, Ah, atol=1e-12)
    assert sum(len(gr["pairs"]) for st in pts_h for gr in st["groups"]) < sum(len(gr["pairs"]) for st in pts_c for gr in st["groups"])


def test_ex16_double_height_gym_is_monitored():
    """The gym columns run base -> L2 (no L1 node): those aligned points are paired with the base over 288 in."""
    from nlrha import drift as DR
    pkg, g = _ex16_pkg()
    pts = DR.drift_points(pkg)
    s2 = DR.summary(pts)[1]
    assert s2["n_aligned"] == 25 and s2["double_height"] == [dict(bottom_level=0, h_in=288.0, n_aligned=6)]
    frames = np.load(os.path.join(FX, "master_frames.npz"))
    A = [DR.drifts_from_master_frames(pkg, frames[k], pts)[0] for k in sorted(frames.files)]
    got = np.round(100 * np.mean(A, 0), 2)
    assert got[0].tolist() == [1.44, 1.64] and got[2].tolist() == [0.40, 0.45]
    assert got[1, 0] == pytest.approx(1.05, abs=0.006)                         # gym X drift governs story 2


def test_story_drifts_picks_aligned_points_on_setback():
    """Two-level set-back: the old monitor compared corner (0,0) of L1 with the far corner of L2; aligned points only."""
    from nlrha import drift as DR
    nodes = {1: (0, 0, 0), 2: (600, 0, 0), 3: (0, 300, 0), 4: (600, 300, 0),
             11: (0, 0, 120), 12: (600, 0, 120), 13: (0, 300, 120), 14: (600, 300, 120), 19: (300, 150, 120),
             21: (0, 0, 240), 23: (0, 300, 240), 25: (300, 0, 240), 26: (300, 300, 240), 29: (150, 150, 240)}
    model = types.SimpleNamespace(nodes=nodes, fixes={n: [1] * 6 for n in (1, 2, 3, 4)}, diaphragms=[(3, 19, [11, 12, 13, 14]), (3, 29, [21, 23, 25, 26])])
    pkg = types.SimpleNamespace(model=model)
    pts = DR.drift_points(pkg)
    assert [st["n_aligned"] for st in pts] == [4, 2]                          # (300, y) of L2 has no point below
    disp = {n: (0.0, 0.0) for n in nodes}
    disp.update({11: (1.2, 0), 12: (1.2, 0), 13: (1.2, 0), 14: (1.2, 0), 21: (2.0, 0), 23: (2.0, 0), 25: (5.0, 0), 26: (5.0, 0)})
    d, at = DR.story_drifts(pts, disp)
    assert d[0, 0] == pytest.approx(1.2 / 120) and d[1, 0] == pytest.approx(0.8 / 120)


# --------------------------------------------------------------------------- NL-13 gravity from the framed floor plate
def _setback_pkg(tmp_path, cfg_text):
    """3x3 grid at 360 in, L1 full (4 bays), L2 one bay (set-back); an off-grid apex node on L1."""
    nodes, elements, schedule = {}, [], {}
    tag = 0
    for k, z in ((0, 0.0), (1, 144.0), (2, 288.0)):
        for i in range(3):
            for j in range(3):
                if k == 2 and (i > 1 or j > 1):
                    continue
                nodes[k * 1000 + i * 10 + j] = (i * 360.0, j * 360.0, z)
    nodes[1999] = (180.0, 0.0, 144.0)                                        # chevron apex (no column)
    for i in range(3):
        for j in range(3):
            for k in (1, 2):
                top = k * 1000 + i * 10 + j
                if top in nodes:
                    tag += 1; elements.append(dict(tag=tag, n1=(k - 1) * 1000 + i * 10 + j, n2=top)); schedule[tag] = {"member": "col", "section": "W14X90"}
    nodes[1500] = (360.0, 360.0, 144.0); nodes[2500] = (180.0, 180.0, 288.0)
    sl1 = [n for n in nodes if 1000 <= n < 1500 or n == 1999]; sl2 = [n for n in nodes if 2000 <= n < 2500]
    G = 386.4
    model = types.SimpleNamespace(nodes=nodes, fixes={n: [1] * 6 for n in nodes if n < 1000}, diaphragms=[(3, 1500, sl1), (3, 2500, sl2)],
                                  elements=elements, masses={1500: [400.0 / G] * 3 + [0] * 3, 2500: [100.0 / G] * 3 + [0] * 3})
    (tmp_path / "cfg.py").write_text(cfg_text)
    basis = types.SimpleNamespace(L_floor_psf=None, sources={})
    return types.SimpleNamespace(model=model, schedule=schedule, root=tmp_path, basis=basis)


def test_gravity_framed_area_roofs_and_tributaries(tmp_path):
    from nlrha import model as MD
    pkg = _setback_pkg(tmp_path, "cfg = dict(L_floor=50.0, Lr=25.0)\n")
    loads, tab, split = MD.ch16_gravity(pkg, _ch16())
    t1, t2 = tab
    assert (t1["area_ft2"], t1["floor_ft2"], t1["roof_ft2"]) == (3600, 900, 2700)   # the set-back roof is a roof
    assert (t2["area_ft2"], t2["roof_ft2"]) == (900, 900)
    assert t1["L0_psf"] == 50.0 and t1["Lr_psf"] == 25.0 and split["basis"]["Lr_from"] == "cfg.py"
    # 0.5 x 0.4 x (50 x 900 + 25 x 2700) / 1000 = 22.5 kip at L1; 0.2 x 25 x 900 / 1000 = 4.5 kip at L2
    assert t1["Lexp_kip"] == pytest.approx(22.5, abs=0.05) and t2["Lexp_kip"] == pytest.approx(4.5, abs=0.05)
    assert 1999 not in loads                                               # off-grid apex node carries no gravity
    assert -sum(loads.values()) == pytest.approx(400 + 100 + 22.5 + 4.5, abs=0.01)
    # tributary: the L1 corner (0,0) has a quarter bay, the centre column (360,360) four quarters
    assert split["ratio"] == pytest.approx(27.0 / 500.0) and split["exception_applies"] and not split["no_live_case_needed"]
    l_nl, _, s_nl = MD.ch16_gravity(pkg, _ch16(), with_live=False)
    assert s_nl["sum_Lexp"] == 0.0 and -sum(l_nl.values()) == pytest.approx(500.0)
    assert l_nl[1011] / l_nl[1000] == pytest.approx(4.0, rel=1e-9)        # dead load by tributary area


def test_gravity_no_live_required_when_heavy_live(tmp_path):
    """L0 > 100 psf over more than 25% of the area -> the 16.3.2 exception does not apply (both conditions checked)."""
    from nlrha import model as MD
    pkg = _setback_pkg(tmp_path, "cfg = dict(L_floor=125.0, Lr=20.0)\n")
    loads, tab, split = MD.ch16_gravity(pkg, _ch16())
    assert tab[0]["Lexp_floor_kip"] == pytest.approx(0.5 * 0.8 * 125 * 900 / 1000, abs=0.05)   # 80% factor above 100 psf
    assert split["share_L0_lt_100"] == pytest.approx(3600 / 4500) and split["exception_applies"] is True     # 900 of 4500 ft2 at 125 psf
    pkg2 = _setback_pkg(tmp_path, "cfg = dict(L_floor=40.0, Lr=20.0, L_by_level={1: 150.0})\n")     # per-level override
    t2 = MD.ch16_gravity(pkg2, _ch16())[1]
    assert t2[0]["L0_psf"] == 150.0


@pytest.mark.skipif(not os.path.isdir(EX16_JOB), reason="saved Ex16 job not available")
def test_ex16_gravity_before_after():
    """Review: Ex16 L2 bounding box 21,952 ft2 against ~11,000; ratio 0.30 'no-live REQUIRED'. Framed: 10,976 ft2."""
    from pushover import package_reader as PR
    from nlrha import model as MD
    pkg = PR.load(EX16_JOB)
    loads, tab, split = MD.ch16_gravity(pkg, _ch16())
    assert [t["area_ft2"] for t in tab] == [7840, 10976, 4704]
    assert tab[1]["floor_ft2"] == 4704 and tab[1]["roof_ft2"] == 6272                 # gym + link roofs at Lr
    assert split["ratio"] == pytest.approx(0.1316, abs=0.002) and not split["no_live_case_needed"]


# --------------------------------------------------------------------------- NL-25 HHT algorithmic damping, SF bounds
def test_hht_algorithmic_damping():
    from nlrha.run import hht_algorithmic_damping as H
    assert abs(H(1.0, 0.1, 1.0)) < 1e-9                                       # Newmark average acceleration
    assert H(0.9, 0.1, 1.0) == pytest.approx(0.00211, abs=0.0001)               # free-vibration check in OpenSees: 0.205%
    assert H(0.9, 0.02, 1.0) < 1e-4 and H(0.9, 0.2, 1.0) > H(0.9, 0.1, 1.0)


def test_scale_factor_bounds_default():
    from nlrha import ground_motions as GM
    idx, recs = GM.library()
    gm, chosen = GM.select_and_scale(recs, 1.0, 0.6, 8.0, 0.2, 3.0, n_select=11, verbose=False)
    assert gm["sf_bounds"] == [0.25, 4.0]
    assert all(0.25 <= r["sf_shape"] <= 4.0 for r in chosen) or gm["sf_note"]
    from nlrha import cli
    ns = types.SimpleNamespace
    assert cli._sf_bounds(ns(sf_bounds=None)) == "default" and cli._sf_bounds(ns(sf_bounds="none")) is None
    assert cli._sf_bounds(ns(sf_bounds="0.5-3")) == (0.5, 3.0)


# --------------------------------------------------------------------------- NL-17 / NL-18 / NL-20 acceptance
class _Spec:
    def __init__(self):
        self.CP = 0.05; self.b_pl = 0.06


def _col_pkg():
    nodes = {1: (0.0, 0.0, 0.0), 2: (0.0, 0.0, 144.0), 99: (0.0, 0.0, 144.0)}
    model = types.SimpleNamespace(nodes=nodes, fixes={1: [1] * 6}, diaphragms=[(3, 99, [2])], elements=[dict(tag=7, n1=1, n2=2)], masses={99: [1.0] * 6})
    return types.SimpleNamespace(model=model, schedule={7: {"member": "col", "section": "W14X90"}}, root=None,
                                 basis=types.SimpleNamespace(L_floor_psf=None, sources={}))


def _rec(i, status="completed", drift=0.01, P=300.0, Mx=2000.0, My=200.0):
    r = dict(record="R%d" % i, label="rec %d" % i, sf=1.0, converged=(status == "completed"), status=status, reason=status,
             seconds=1.0, steps=10, heights=[144.0], peak_story_drift=[[drift, drift]], peak_roof_in=[1.0, 1.0], residual_drift=[0.001],
             peak_def={}, signed_def={}, specs={}, hinges_meta={}, peak_colN={7: P},
             col_env={7: dict(Pc=P, Pt=-50.0, Mmaj=Mx, Mmin=My, conc_c=(P, Mx, My), conc_t=(-50.0, 0.5 * Mx, 0.5 * My))},
             col_grav={7: dict(P=200.0, Mmaj=0.0, Mmin=0.0)},
             col_flexure={7: dict(major="deformation", minor="deformation", modelled=dict(major="hinge", minor="elastic"))},
             drift_method="aligned_points")
    if status == "nonconvergence":
        r.update(peak_story_drift=[[0.0, 0.0]])
    return r


def _eval(results, planned=None, rc="I_II", suite_size=11, early=None):
    from nlrha import acceptance as AC
    split = dict(sum_D=1000.0, sum_Lexp=200.0, no_live_case_needed=False)
    return AC.evaluate(results, _col_pkg(), _ch16(), {7: 200.0}, split, 1.5, Ie=1.0, rc=rc, prm={"material": {"Fy_ksi": 50.0, "Ry_expected": 1.1}},
                       planned=planned, suite_size=suite_size, early_abort=early)


def test_column_strengths_hand_calc():
    """W14X90, L = 12 ft, Fy = 50: E3 Fcr = 0.658^(Fy/Fe) Fy with KL/r = 144/3.70 -> phi Pn ~ 1067 kip;
    F3 noncompact flange (bf/2tf 10.2 > 9.15): Mn = 637 k-ft (AISC Manual Table 3-2 phi Mpx 573 k-ft)."""
    from nlrha import acceptance as AC
    Pn, KLr = AC.column_Pn("W14X90", 144.0, Fy=50.0)
    Fe = math.pi ** 2 * 29000 / (144.0 / 3.70) ** 2
    assert KLr == pytest.approx(144.0 / 3.70, rel=1e-3)
    assert Pn == pytest.approx(0.658 ** (50.0 / Fe) * 50.0 * 26.5, rel=2e-3)
    Mnx, Mny, notes = AC.column_Mn("W14X90", 144.0, Fy=50.0)
    assert 0.9 * Mnx / 12 == pytest.approx(573.0, rel=0.01)


def test_fc_check_combines_axial_and_flexure_and_counteracting_case():
    from nlrha import acceptance as AC
    cap = dict(phiPn=1000.0, phiTn=1200.0, phiMnx=6000.0, phiMny=3000.0, MCEx=8000.0, MCEy=4000.0, Pye=1450.0)
    flex = dict(major="force", minor="force", modelled={})
    Qns = dict(P=200.0, Mmaj=0.0, Mmin=0.0)
    # Pr = (1.2 + 0.12*1.5) * 160 + 40 + 1.3 * (500 - 200) = 220.8 + 40 + 390 = 650.8 ; Mr = 1.3 * 1500 = 1950
    c = AC._fc_column_check(dict(Pc=500.0, Pt=None, Mmaj=1500.0, Mmin=0.0), Qns, cap, flex, 0.8, 1.5, 1.0, 1.3)
    assert c["demand"] == pytest.approx(650.8) and c["DC_axial"] == pytest.approx(0.6508)
    assert c["DC_H1_comp"] == pytest.approx(0.6508 + 8 / 9 * 1950 / 6000)                  # H1-1a
    # counteracting gravity: (0.9 - 0.18) * 160 - 1.3 * (200 - (-150)) = 115.2 - 455 = -339.8 -> tension 339.8
    t = AC._fc_column_check(dict(Pc=None, Pt=-150.0, Mmaj=0.0, Mmin=0.0), Qns, cap, flex, 0.8, 1.5, 1.0, 1.3)
    assert t["demand_tension"] == pytest.approx(339.8) and t["DC_H1_tens"] == pytest.approx(339.8 / 1200 + 8 / 9 * 0.0)
    # deformation-controlled flexure: analysed moment against M_CE, not amplified
    d = AC._fc_column_check(dict(Pc=210.0, Pt=None, Mmaj=4000.0, Mmin=0.0), Qns, cap, dict(major="deformation", minor="force", modelled={"major": "hinge"}), 0.8, 1.5, 1.0, 1.3)
    r = d["demand"] / 1000.0
    assert r >= 0.2 and d["DC_H1_comp"] == pytest.approx(r + 8 / 9 * 4000 / 8000)          # H1-1a, M_CE = 8000
    e = AC._fc_column_check(dict(Pc=210.0, Pt=None, Mmaj=0.0, Mmin=5000.0), Qns, cap, dict(major="deformation", minor="deformation", modelled={"minor": "elastic"}), 0.8, 1.5, 1.0, 1.3)
    assert e["flags"] and "ELASTIC" in e["flags"][0]


def test_flexure_makes_fc_check_stricter_than_axial_only():
    acc = _eval([_rec(i) for i in range(11)], planned=[dict(record="R%d" % i) for i in range(11)])
    row = acc["force_controlled_columns"][0]
    assert row["DC"] > row["DC_axial"] * 1.5 and row["DC_envelope"] >= row["DC"] - 1e-12
    assert acc["verdict"]["status"] in ("ACCEPTABLE", "NOT ACCEPTABLE")


def test_crawl_abort_is_incomplete_not_nonconvergence():
    from nlrha import acceptance as AC
    assert AC.record_status(dict(converged=False, reason="crawl abort at t=12.3 s (41 fallback micro-advances)")) == "incomplete"
    assert AC.record_status(dict(converged=False, reason="non-convergence at t=3.0 s")) == "nonconvergence"
    res = [_rec(i) for i in range(10)] + [_rec(10, status="incomplete", drift=0.005)]
    acc = _eval(res, planned=[dict(record="R%d" % i) for i in range(11)], rc="III")
    v = acc["verdict"]
    assert v["n_unacceptable"] == 0 and v["n_incomplete"] == 1 and v["status"] == "INCOMPLETE" and v["overall"] is False
    p = acc["per_record"][-1]
    assert p["incomplete"] and p["peak_drift"] is None and p["peak_drift_lower_bound"] == pytest.approx(0.005)
    # a partial history that already exceeds 150% of the limit is unacceptable on its own (lower bound)
    res[-1] = _rec(10, status="incomplete", drift=0.08)
    acc = _eval(res, planned=[dict(record="R%d" % i) for i in range(11)], rc="III")
    assert acc["per_record"][-1]["unacceptable"] and acc["verdict"]["status"] == "NOT ACCEPTABLE"


def test_early_abort_lists_records_not_run_and_basis():
    planned = [dict(record="R%d" % i, label="rec %d" % i) for i in range(11)]
    early = dict(basis="ASCE 7-22 16.4.1.1: 2 record(s) failed to converge (item 1) > 1 unacceptable permitted")
    acc = _eval([_rec(0, "nonconvergence"), _rec(1, "nonconvergence")], planned=planned, early=early)
    v = acc["verdict"]
    assert v["status"] == "NOT ACCEPTABLE" and v["n_not_run"] == 9 and len(acc["records_not_run"]) == 9
    assert any("not run" in w and "16.4.1.1" in w for w in v["not_evaluated"])
    assert v["mean_drift_max"] is None and all(s["mean_X"] is None for s in acc["story"])
    # 9 good records, 2 not run: never ACCEPTABLE on a partial suite
    acc = _eval([_rec(i) for i in range(9)], planned=planned)
    assert acc["verdict"]["status"] == "INCOMPLETE" and acc["verdict"]["n_not_run"] == 2


def test_run_suite_early_abort_is_decision_based(monkeypatch):
    from nlrha import cli, run as RN
    calls = []
    def fake(job):
        calls.append(job)
        return dict(record=job, converged=False, status="nonconvergence", reason="nc")
    monkeypatch.setattr(RN, "run_record_worker", fake)
    jobs = list(range(11)); labels = [(i, "rec %d" % i) for i in range(11)]
    res, early = cli._run_suite(jobs, labels, early_nc=1, allowed=1, parallel=1)     # RC I/II: abort after 2 NC, not 1
    assert len(res) == 2 and early["n_skipped"] == 9 and len(early["records_not_run"]) == 9 and "16.4.1.1" in early["basis"]
    calls.clear()
    def inc(job):
        return dict(record=job, converged=False, status="incomplete", reason="time-out")
    monkeypatch.setattr(RN, "run_record_worker", inc)
    res, early = cli._run_suite(jobs, labels, early_nc=2, allowed=1, parallel=1)     # time-outs never trigger it
    assert len(res) == 11 and early is None
    res, early = cli._run_suite(jobs, labels, early_nc=0, allowed=0, parallel=1)     # default: off
    assert early is None


def test_no_live_case_required_but_not_run_withholds_verdict():
    from nlrha import acceptance as AC
    acc = _eval([_rec(i, Mx=100.0, My=10.0) for i in range(11)], planned=[dict(record="R%d" % i) for i in range(11)])
    assert acc["verdict"]["status"] == "ACCEPTABLE"
    AC.combine_no_live(acc, None, required=True)
    assert acc["verdict"]["status"] == "INCOMPLETE" and not acc["verdict"]["overall"]
    acc = _eval([_rec(i, Mx=100.0, My=10.0) for i in range(11)], planned=[dict(record="R%d" % i) for i in range(11)])
    acc_nl = _eval([_rec(i, drift=0.07, Mx=100.0, My=10.0) for i in range(11)], planned=[dict(record="R%d" % i) for i in range(11)])
    AC.combine_no_live(acc, acc_nl, required=True)
    assert acc["verdict"]["status"] == "NOT ACCEPTABLE" and acc["no_live_case"]["run"]


def test_nan_rendered_as_not_computed():
    from nlrha import report as RP
    from snl import compare as CMP
    assert RP._pct(float("nan")) == "not computed" and RP._pct(None) == "not computed" and RP._pct(0.0123) == "1.23%"
    assert CMP.pct(float("nan")) == "not computed"
    assert "warn" in RP._tag(None)
