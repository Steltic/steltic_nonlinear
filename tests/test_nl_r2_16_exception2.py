"""NL-R2-16: ASCE 7-22 16.4.2.1 Exception 2 for column axial force limited by a yield mechanism.

Synthetic frames only (no OpenSees): the mechanism statics Emc of the column line, the Eqs. (16.4-3)/(16.4-4)
check, the qualification rules, the verdict basis and the default check reported beside it, and the roof snow S."""
import json, math, os, sys, types
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from nlrha import acceptance as AC  # noqa: E402

NS = types.SimpleNamespace


def _ch16():
    return json.load(open(os.path.join(ROOT, "nlrha", "ch16_params.json")))


def _frame(beam_release=None, hinged=(201, 202), brace=False, y_beam=False):
    """One X bay (360 in), two storeys (144 in). Columns 101 (1-11), 102 (2-12), 103 (11-21), 104 (12-22);
    beams 201 (11-12), 202 (21-22); optional brace 301 (1 -> 12) and a Y beam 203 (12-13) with column 105 (3-13)."""
    nodes = {1: (0.0, 0.0, 0.0), 2: (360.0, 0.0, 0.0), 11: (0.0, 0.0, 144.0), 12: (360.0, 0.0, 144.0), 21: (0.0, 0.0, 288.0), 22: (360.0, 0.0, 288.0)}
    els = [dict(tag=101, n1=1, n2=11), dict(tag=102, n1=2, n2=12), dict(tag=103, n1=11, n2=21), dict(tag=104, n1=12, n2=22),
           dict(tag=201, n1=11, n2=12, release=(beam_release or {}).get(201)), dict(tag=202, n1=21, n2=22, release=(beam_release or {}).get(202))]
    sch = {t: {"member": "col", "section": "W14X90"} for t in (101, 102, 103, 104)}
    sch.update({201: {"member": "beam", "section": "W24X76"}, 202: {"member": "beam", "section": "W24X76"}})
    meta, specs = {}, {}
    beam = NS(section="W24X76", Mc_over_My=1.1, Mpe_kipin=10000.0)
    for b in hinged:
        for end in (1, 2):
            t = b * 10 + end
            meta[t] = dict(kind="beam", ele=b, end=end, section="W24X76", z=144.0, rbs_offset_in=0.0); specs[t] = beam
    if brace:
        els.append(dict(tag=301, n1=1, n2=12)); sch[301] = {"member": "brace", "section": "HSS8X8X1/2"}
        meta[301] = dict(kind="brace", ele=301, end=0, section="HSS8X8X1/2", z=144.0, rbs_offset_in=None)
        specs[301] = brace if not isinstance(brace, bool) else NS(section="HSS8X8X1/2", Pye_kip=500.0, Pcre_kip=300.0)
    if y_beam:
        nodes[3] = (360.0, 360.0, 0.0); nodes[13] = (360.0, 360.0, 144.0)
        els += [dict(tag=105, n1=3, n2=13), dict(tag=203, n1=12, n2=13)]
        sch[105] = {"member": "col", "section": "W14X90"}; sch[203] = {"member": "beam", "section": "W24X76"}
        yb = NS(section="W24X76", Mc_over_My=1.1, Mpe_kipin=5000.0)
        for end in (1, 2):
            meta[2030 + end] = dict(kind="beam", ele=203, end=end, section="W24X76", z=144.0, rbs_offset_in=0.0); specs[2030 + end] = yb
    model = NS(nodes=nodes, fixes={1: [1] * 6, 2: [1] * 6}, diaphragms=[], elements=els, masses={}, equal_dofs=[])
    return NS(model=model, schedule=sch, root=None, basis=NS(L_floor_psf=None, sources={})), meta, specs


PRM = {"material": {"Fy_ksi": 50.0, "Ry_expected": 1.1}, "brace_axial": {"tension": {"hardening_ratio": 1.0}}}


def test_mechanism_statics_of_moment_frame_column_line():
    """Emc of a column = sum of the plastic beam shears 2 x 1.1 Mpe / L delivered at and above its top (16.4.2.1 Exc. 2)."""
    pkg, meta, specs = _frame()
    res, note = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    V = 2 * 1.1 * 10000.0 / 360.0
    assert note is None
    assert res[101]["qualifies"] and res[101]["Emc_c"] == pytest.approx(2 * V) and res[101]["Emc_t"] == pytest.approx(2 * V)
    assert res[103]["Emc_c"] == pytest.approx(V) and res[104]["Emc_t"] == pytest.approx(V)
    # +X sway compresses the leeward (+x) column and lifts the windward one
    assert res[102]["governing_c"].startswith("sway +X") and res[101]["governing_c"].startswith("sway -X")
    # hinges at the RBS centres: the plastic shear acts over the clear length between them
    for m in meta.values():
        m["rbs_offset_in"] = 20.0
    res2, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    assert res2[103]["Emc_c"] == pytest.approx(2 * 1.1 * 10000.0 / 320.0)


def test_columns_that_do_not_qualify():
    # beam pinned both ends at the upper level, no hinge: no yielding component delivers seismic force to 103 / 104
    pkg, meta, specs = _frame(beam_release={202: ["-releasey", 3]}, hinged=(201,))
    res, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    assert not res[103]["qualifies"] and "no yielding component" in res[103]["reason"]
    assert res[101]["qualifies"] and res[101]["Emc_c"] == pytest.approx(2 * 1.1 * 10000.0 / 360.0)
    # moment-connected beam end without a hinge (elastic / force-controlled beam): the force is not mechanism-limited
    pkg, meta, specs = _frame(hinged=(202,))
    res, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    assert not res[101]["qualifies"] and "no yielding hinge" in res[101]["reason"]
    assert res[103]["qualifies"]                                      # above the elastic beam the line is fine
    # vertical equalDOF on the line
    pkg, meta, specs = _frame()
    pkg.model.equal_dofs = [[21, 22, 3]]
    res, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    assert not res[101]["qualifies"] and "equalDOF" in res[101]["reason"]


def test_brace_capacities_F2_3_analyses_and_brb():
    """Brace 1 -> 12 (sin = 144 / 387.8): +X stretches it (h Pye), -X compresses it (Pcre, analysis (a); 0.3 Pcre (b))."""
    pkg, meta, specs = _frame(beam_release={201: ["-releasey", 3], 202: ["-releasey", 3]}, hinged=(), brace=True)
    res, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    s = 144.0 / math.hypot(360.0, 144.0)
    assert res[102]["qualifies"] and res[102]["Emc_c"] == pytest.approx(500.0 * s) and res[102]["Emc_t"] == pytest.approx(300.0 * s)
    assert "analysis (a)" in res[102]["governing_t"]
    assert not res[101]["qualifies"]                                  # the brace lands on 101's base node: not above its top
    brb = NS(section="BRB", Pye_kip=400.0, Pcre_kip=400.0, omega=1.3, beta=1.1)
    pkg, meta, specs = _frame(beam_release={201: ["-releasey", 3], 202: ["-releasey", 3]}, hinged=(), brace=brb)
    res, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    assert res[102]["Emc_c"] == pytest.approx(1.3 * 400.0 * s) and res[102]["Emc_t"] == pytest.approx(1.1 * 1.3 * 400.0 * s)


def test_column_common_to_two_frames_takes_both_mechanisms():
    """AISC 341-22 D1.4a: simultaneous inelasticity of the X and the Y frame (sway in both directions together)."""
    pkg, meta, specs = _frame(hinged=(201,), y_beam=True, beam_release={202: ["-releasey", 3]})
    res, _ = AC.mechanism_axial_bounds(pkg, meta, specs, PRM)
    Vx, Vy = 2 * 1.1 * 10000.0 / 360.0, 2 * 1.1 * 5000.0 / 360.0
    assert res[102]["Emc_c"] == pytest.approx(Vx + Vy) and "+X" in res[102]["governing_c"] and "Y" in res[102]["governing_c"]


def test_exception_2_equations_hand_calc():
    """(16.4-3): (1.2 + 0.12 SMS) D + 0.5 L + 0.2 S + Emc;  (16.4-4): (0.9 - 0.12 SMS) D - Emc (net tension), factor 1.0."""
    cap = dict(phiPn=1000.0, phiTn=1200.0, Pye=1450.0)
    x = AC._exc2_column_check(400.0, 300.0, dict(P=200.0), cap, 0.8, 1.5, S_kip=10.0)
    assert x["Pr_16_4_3"] == pytest.approx(1.38 * 160.0 + 40.0 + 2.0 + 400.0)
    assert x["Tr_16_4_4"] == pytest.approx(300.0 - 0.72 * 160.0)
    assert x["DC_16_4_3"] == pytest.approx(x["Pr_16_4_3"] / 1000.0) and x["DC_16_4_4"] == pytest.approx(x["Tr_16_4_4"] / 1200.0)
    assert x["DC"] == pytest.approx(max(x["DC_16_4_3"], x["DC_16_4_4"], x["DC_C3_10"])) and "16.4-3" in x["governing"]
    y = AC._exc2_column_check(50.0, 50.0, dict(P=200.0), cap, 0.8, 1.5)
    assert y["DC_16_4_4"] is None and y["Tr_16_4_4"] == 0.0                # gravity exceeds the uplift


def _recs(n=11, Pc=300.0, Pt=-80.0, M=9000.0, flex="deformation"):
    out = []
    for i in range(n):
        env = {c: dict(Pc=Pc, Pt=Pt, Mmaj=M, Mmin=100.0, conc_c=(Pc, M, 100.0), conc_t=(Pt, M, 100.0)) for c in (101, 102, 103, 104)}
        out.append(dict(record="R%d" % i, label="rec %d" % i, sf=1.0, converged=True, status="completed", reason="", seconds=1.0, steps=10,
                        heights=[144.0, 144.0], peak_story_drift=[[0.01, 0.01], [0.01, 0.01]], peak_roof_in=[1.0, 1.0], residual_drift=[0.001, 0.001],
                        peak_def={}, signed_def={}, specs={}, hinges_meta={}, peak_colN={c: Pc for c in env}, col_env=env,
                        col_grav={c: dict(P=200.0, Mmaj=0.0, Mmin=0.0) for c in env},
                        col_flexure={c: dict(major=flex, minor=flex, modelled=dict(major="hinge", minor="elastic")) for c in env},
                        drift_method="aligned_points", stats=dict(plasticity="imk")))
    return out


def _evaluate(results, ch16=None, meta=None, specs=None, pkg=None):
    p, m, s = _frame()
    pkg = pkg or p
    for r in results:                                                 # the analysed hinges (beam hinges only)
        r["hinges_meta"] = meta if meta is not None else m; r["specs"] = specs if specs is not None else s
    split = dict(sum_D=1000.0, sum_Lexp=250.0, no_live_case_needed=False)
    return AC.evaluate(results, pkg, ch16 or _ch16(), {c: 200.0 for c in (101, 102, 103, 104)}, split, 1.5, Ie=1.0, rc="I_II", prm=PRM,
                       planned=[dict(record="R%d" % i) for i in range(len(results))], suite_size=len(results), snow_col={})


def test_verdict_uses_exception_2_and_reports_the_default_check():
    """The finding: every hinging moment-frame column fails C3-9 / H1-1 with the analysed moment at M_CE, while the
    axial-only check passes. With Exception 2 the axial force (mechanism-limited) is checked by 16.4-3 / 16.4-4."""
    acc = _evaluate(_recs())
    v = acc["verdict"]
    base = next(r for r in acc["force_controlled_columns"] if r["z_in"] == 0.0)
    e = base["exception_2"]
    assert e["qualifies"] and base["fc_basis"].startswith("16.4.2.1 Exception 2")
    assert base["DC_default"] > 1.0 and base["DC"] < 1.0 and base["DC"] == pytest.approx(e["DC"])
    # Emc = mechanism statics (2 beams) -- above the analysed suite max (300 - 200 = 100 kip)
    assert e["Emc_mech_c"] == pytest.approx(2 * 2.2 * 10000.0 / 360.0) and e["Emc_c"] == pytest.approx(e["Emc_mech_c"]) and e["analysed_max_c"] == pytest.approx(100.0)
    assert v["force_controlled_ok"] is True and v["worst_FC_DC_default"] > 1.0
    x2 = v["FC_exception_2"]
    assert x2["used"] and x2["n_columns"] == 2 and "W14X90 @ z 0 in" in x2["members"]
    # switched off -> every column by the default check, identical numbers
    ch = _ch16(); ch["force_controlled"]["exception_2"]["apply"] = False
    acc0 = _evaluate(_recs(), ch16=ch)
    base0 = next(r for r in acc0["force_controlled_columns"] if r["z_in"] == 0.0)
    assert base0["DC"] == pytest.approx(base["DC_default"]) and "exception_2" not in base0
    assert acc0["verdict"]["force_controlled_ok"] is False and not acc0["verdict"]["FC_exception_2"]["used"]


def test_emc_never_below_the_analysed_maximum_and_force_controlled_flexure_keeps_default():
    acc = _evaluate(_recs(Pc=900.0, Pt=-500.0))                     # analysed 700 kip > mechanism 122 kip at the top storey
    top = next(r for r in acc["force_controlled_columns"] if r["z_in"] == 144.0)["exception_2"]
    assert top["qualifies"] and top["Emc_c"] == pytest.approx(700.0) and top["Emc_t"] == pytest.approx(700.0)
    assert top["Emc_basis"].startswith("analysed suite maximum")
    acc = _evaluate(_recs(flex="force"))
    row = acc["force_controlled_columns"][0]
    assert not row["exception_2"]["qualifies"] and "force-controlled" in row["exception_2"]["reason"] and row["DC"] == row["DC_default"]
    assert not acc["verdict"]["FC_exception_2"]["used"] and acc["verdict"]["FC_exception_2"]["not_applied"]


def test_roof_snow_axial_of_the_column_lines(tmp_path):
    """0.2 S of Eq. (16.4-3): roof snow on the roof bays, lumped to the bay corners, carried down the column line."""
    from test_nlrha_fixes import _setback_pkg
    from nlrha import gravity as GR
    pkg = _setback_pkg(tmp_path, "cfg = dict(L_floor=50.0, Lr=25.0, snow=30.0)\n")
    S, basis = GR.column_snow_axial(pkg)
    nodes = pkg.model.nodes
    col = {(nodes[e["n1"]][0], nodes[e["n1"]][1], nodes[e["n1"]][2]): e["tag"] for e in pkg.model.elements}
    assert S[col[(0.0, 0.0, 0.0)]] == pytest.approx(30.0 * 225.0 / 1000.0)          # L2 roof quarter bay; its L1 bay is a floor
    assert S[col[(720.0, 720.0, 0.0)]] == pytest.approx(30.0 * 225.0 / 1000.0)      # set-back L1 roof quarter bay
    assert S[col[(360.0, 360.0, 0.0)]] == pytest.approx(30.0 * (3 * 225.0 + 225.0) / 1000.0)   # 3 L1 roof quarters + L2 corner
    assert "30.0 psf" in basis
    assert GR.column_snow_axial(_setback_pkg(tmp_path, "cfg = dict(L_floor=50.0)\n")) == ({}, "S = 0 (no roof snow load in cfg.py)")
