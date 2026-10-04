"""The ASCE 41-23 / AISC 342-22 parameter schema (pushover/params_schema.py, pushover/performance.py) and the
review / frame-type fixes of the nl-collect branch (register items NL-04, NL-06, NL-08, NL-14, NL-15, NL-19,
NL-21, NL-23, NL-27). Every expected number is a hand calculation from the printed table."""
import copy
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pushover import hinge_models as HM, params_schema as PS, performance as PF, sections_db as SDB   # noqa: E402

E = 29000.0
FYE = 55.0                                   # 50 ksi x Ry 1.1 (template material)


def _user(prm):
    """The template as a user would hand it in after the manual check: cited, nothing left unverified."""
    p = {k: v for k, v in copy.deepcopy(prm).items() if not (k.startswith("_") and k != "_README")}
    for g in PS.GROUPS:
        p[g].pop("unverified", None)
        p[g]["source"] = "AISC 342-22 Table C2.2/C3.6/C3.4, printed p. 27-47 (checked by the engineer)"
    p["verified"] = True
    p["source"] = "user-verified"
    return PS.annotate(p)


def _template():
    return HM.load_params(None)


def _ty(sec, L=360.0):
    p = SDB.props(sec)
    return p["Zx"] * FYE * L / (6 * E * p["Ix"])


# ---------------------------------------------------------------- NL-06: the printed form of Table C2.2
def test_printed_cells_are_read():
    assert PS.parse_printed("0.25 a") == (0.25, "a")
    assert PS.parse_printed("b") == (1.0, "b")
    assert PS.parse_printed("9 θy") == (9.0, "thetay")
    assert PS.parse_printed("a = 4 θy") == (4.0, "thetay")
    assert PS.parse_printed("0.7 n Δc") == (0.7, "n_dc")
    assert PS.parse_printed("n ∆T") == (1.0, "n_dT")
    assert PS.parse_printed("1.5 Δc") == (1.5, "dc")
    with pytest.raises(ValueError):
        PS.parse_printed("0.25 q")


def test_table_C2_2_line_1_io_is_a_quarter_of_a():
    """Line 1 prints IO = 0.25 a with a = 9 θy, i.e. IO = 2.25 θy -- not representable as 'x θy' before."""
    prm = _user(_template())
    ty = _ty("W36X232")
    h = HM.beam_hinge("W36X232", 360.0, prm)                       # highly ductile flange and web
    assert h.theta_y == pytest.approx(ty, rel=1e-9)
    assert h.a_pl == pytest.approx(9 * ty) and h.b_pl == pytest.approx(11 * ty) and h.c_res == pytest.approx(0.6)
    assert h.IO == pytest.approx(2.25 * ty) and h.LS == pytest.approx(9 * ty) and h.CP == pytest.approx(11 * ty)
    assert prm["verified"] is True                                 # user-supplied: stays verified


def test_legacy_flat_file_still_builds():
    ex = os.path.join(ROOT, "examples", "Ex22_SMF", "hinge_params_ex22_aisc342.json")
    prm = HM.load_params(ex)
    b = HM.beam_hinge("W36X232", 360.0, prm)
    c = HM.column_hinge("W14X730", 192.0, 651.3, prm)
    assert 0 < b.IO < b.LS < b.CP and 0 < c.IO < c.LS < c.CP


# ---------------------------------------------------------------- NL-14: the row is chosen by the section
def test_row_selection_interpolates_by_flange_and_web_and_takes_the_lowest():
    """W14X109 as a beam: bf/2tf = 8.49 lies between lambda_hd = 0.30 sqrt(E/Fye) = 6.889 and lambda_md = 0.38
    sqrt(E/Fye) = 8.726 (AISC 341-22 Table D1.1b case 1); the web is far below lambda_hd. Line 3: interpolate
    for each element and use the lowest -> the flange governs."""
    prm = _user(_template()); prm["_system"] = "SCBF"
    p = SDB.props("W14X109")
    k = math.sqrt(E / FYE)
    t = (p["bf_2tf"] - 0.30 * k) / (0.38 * k - 0.30 * k)
    assert 0 < t < 1
    ty = _ty("W14X109")
    h = HM.beam_hinge("W14X109", 360.0, prm)
    a = (9 + t * (4 - 9)) * ty
    b = (11 + t * (6 - 11)) * ty
    assert h.a_pl == pytest.approx(a, rel=1e-6) and h.b_pl == pytest.approx(b, rel=1e-6)
    assert h.c_res == pytest.approx(0.6 + t * (0.2 - 0.6), rel=1e-6)
    # IO = 0.25 a on both lines; LS = a (line 1) -> 0.75 a (line 2); CP = b -> a
    assert h.IO == pytest.approx(0.25 * 9 * ty + t * (0.25 * 4 * ty - 0.25 * 9 * ty), rel=1e-6)
    assert h.LS == pytest.approx(9 * ty + t * (0.75 * 4 * ty - 9 * ty), rel=1e-6)
    assert h.CP == pytest.approx(11 * ty + t * (4 * ty - 11 * ty), rel=1e-6)
    assert any("line 3 interpolation" in f for f in h.flags)


def test_no_moderately_ductile_row_and_imf_columns_build():
    """An IMF (Ex16) has W14X109 columns that are not highly ductile; with both printed lines of Table C3.6
    in the file the column is interpolated -- no 'moderately ductile' row is needed or asked for."""
    prm = _user(_template()); prm["_system"] = "IMF"
    nm = prm["column_flexure"]["rows"]["non_moderately_ductile"]
    nm.update(a_expr="0.5*(1-PG/Pye)", b_expr="1.0*(1-PG/Pye)")        # test values in the printed-line form
    p = SDB.props("W14X109"); Pye = p["A"] * FYE; PG = 0.1 * Pye
    c = HM.column_hinge("W14X109", 168.0, PG, prm)
    h, tw, ry = p["d"] - 2 * p["tf"], p["tw"], p["ry"]
    a1 = min(5.5 * (h / tw) ** -0.95 * (168 / ry) ** -0.5 * 0.9 ** 2.4, 0.07)
    a2 = min(0.5 * 0.9, 1e9)
    k = math.sqrt(E / FYE)
    t = (p["bf_2tf"] - 0.30 * k) / (0.08 * k)
    assert c.a_pl == pytest.approx(min(a1 + t * (a2 - a1), a1), rel=1e-6)
    assert c.IO == pytest.approx(0.5 * c.a_pl, rel=1e-6)
    from snl import collect
    f = dict(system="IMF", moment_frame=True, rbs=False)
    assert "Moderately ductile'" not in collect.row_for("column_flexure", f)[1]
    assert "Non-moderately ductile" in collect.row_for("column_flexure", f)[1]
    assert "Non-moderately ductile" in collect.row_for("beam_flexure", dict(f, moment_frame=False))[1]


def test_missing_line_2_is_a_loud_fallback_not_a_silent_one():
    prm = _template(); prm["_system"] = "IMF"
    c = HM.column_hinge("W14X109", 168.0, 100.0, prm)
    assert any("line 2 (non-moderately ductile) is not in the parameter file" in f for f in c.flags)
    assert prm["verified"] is False
    assert any("rows.non_moderately_ductile" in x for x in prm["_used_unverified"]["column_flexure"])


def test_web_limits_follow_the_frame_type():
    p = SDB.props("W24X55"); k = math.sqrt(E / FYE)
    el, _ = PS.element_slenderness(p, FYE, 0.0, {"_system": "SMF"})
    assert el["web"][1] == pytest.approx(2.5 * k) and el["web"][2] == pytest.approx(5.4 * k)          # D1.1b case 11
    el, _ = PS.element_slenderness(p, FYE, 0.0, {"_system": "SCBF"})
    assert el["web"][1] == pytest.approx(2.45 * k) and el["web"][2] == pytest.approx(3.76 * k)        # case 14
    el, _ = PS.element_slenderness(p, FYE, 0.2, {"_system": "SCBF"})
    assert el["web"][1] == pytest.approx(2.26 * (1 - 0.38 * 0.2) * k)


# ---------------------------------------------------------------- NL-04: Table C3.4 braces reachable
def test_template_brace_is_table_C3_4_and_printed_cells_are_used():
    prm = _user(_template())
    prm["brace_axial"]["compression"]["n_expr"] = "6.0"
    prm["brace_axial"]["tension"]["n_expr"] = "8.0"
    b = HM.brace_spec("HSS8X8X1/2", 200.0, prm)
    assert b.slenderness_class == "C3.4_rect_HSS"
    assert b.IO == pytest.approx(1.5 * b.dc) and b.LS == pytest.approx(0.7 * 6 * b.dc) and b.CP == pytest.approx(6 * b.dc)
    assert b.IO_t == pytest.approx(1.5 * b.dT) and b.LS_t == pytest.approx(0.7 * 8 * b.dT) and b.CP_t == pytest.approx(8 * b.dT)
    assert b.c_c == pytest.approx(0.2) and b.c_t == pytest.approx(1.0)
    prm["brace_axial"]["tension_only"] = True                     # Table C3.4 note [d]
    b2 = HM.brace_spec("HSS8X8X1/2", 200.0, prm)
    assert b2.CP == pytest.approx(b.CP / 2)


def test_legacy_brace_form_is_never_verified():
    prm = _user(_template())
    prm["brace_axial"] = json.load(open(os.path.join(ROOT, "examples", "Ex22_SMF", "pushover", "hinge_params_used.json")))["brace_axial"]
    prm["brace_axial"]["source"] = "someone said so, p. 1"
    HM.brace_spec("HSS8X8X1/2", 200.0, prm)
    assert prm["verified"] is False and "brace_axial" in prm["_used_unverified"]


# ---------------------------------------------------------------- NL-19: provenance and the P-M reduction
def test_template_values_never_pass_as_verified():
    prm = _template()
    assert set(prm["_unverified"]) == set(PS.GROUPS)
    HM.beam_hinge("W36X232", 360.0, prm)
    assert prm["verified"] is False and "beam_flexure" in PS.unverified_text(prm)


def test_collect_written_group_flags_unquoted_fields_even_if_the_file_says_verified():
    prm = _user(_template())
    cf = prm["column_flexure"]
    cf["quotes"] = {k: "x" for k in PS.value_fields(cf) if k not in ("Mc_over_My", "force_controlled_above_P_over_Pye")}
    prm["_annotated"] = False
    PS.annotate(prm)
    assert prm["_unverified"]["column_flexure"] == ["Mc_over_My", "force_controlled_above_P_over_Pye"]
    HM.column_hinge("W14X730", 192.0, 651.3, prm)
    assert prm["verified"] is False and prm["_verified_claimed"] is True


def test_superseded_column_PM_reduction_is_replaced_by_C3_5_C3_6():
    """1.18 (1 - P/Py) <= 1 is AISC 342-22 Commentary Eq. C-C3-5 ('earlier editions of ASCE/SEI 41');
    Eqs. C3-5 / C3-6 give (1 - P/2Pye) below 0.2 and 9/8 (1 - P/Pye) above."""
    prm = _user(_template())
    prm["column_flexure"]["Mpce_axial_reduction"] = "min(1.0, 1.18*(1-PG/Pye))"
    prm["_annotated"] = False
    PS.annotate(prm)
    p = SDB.props("W14X730"); Pye = p["A"] * FYE
    for r, want in ((0.1, 0.95), (0.3, 9 / 8 * 0.7)):
        c = HM.column_hinge("W14X730", 192.0, r * Pye, prm)
        assert c.Mpe_kipin / (p["Zx"] * FYE) == pytest.approx(want, rel=1e-9)
    assert "Mpce_axial_reduction" in prm["_unverified"]["column_flexure"] and prm["verified"] is False


# ---------------------------------------------------------------- NL-15: BPON levels by Risk Category
def test_bpon_levels_by_risk_category():
    assert PF.bpon_levels("II") == ("LS", "CP") and PF.bpon_levels("I_II") == ("LS", "CP")
    assert PF.bpon_levels("III") == ("DC", "LtdS")
    assert PF.bpon_levels("IV") == ("IO", "LS")


def test_damage_control_and_limited_safety_limits():
    # Table 2-1: DC halfway IO-LS, LtdS halfway LS-CP; Section 7.5.3.2.2: DC at the a point -> the lower
    assert PF.dc_limit(0.01, 0.05) == pytest.approx(0.03)
    assert PF.dc_limit(0.01, 0.05, a=0.02) == pytest.approx(0.02)
    assert PF.ltds_limit(0.05, 0.07) == pytest.approx(0.06)
    acc = {"groups": [dict(kind="beam", section="W", z_in=0, IO=0.01, LS=0.05, CP=0.07, theta_pl_max=0.033)], "worst_DC": {}}
    PF.augment_groups(acc)
    g = acc["groups"][0]
    assert g["DC_DC"] == pytest.approx(1.1) and g["DC_LtdS"] == pytest.approx(0.55)
    assert acc["worst_DC"]["DC"] == pytest.approx(1.1)


def test_four_analyses_sheet_uses_the_same_mapping():
    from snl import compare
    assert compare.RC_BPON["III"] == ("DC", "LtdS") and "Damage Control" in compare.RC_BPON_NOTE["III"]


# ---------------------------------------------------------------- NL-08: frame type from structure
EX4_CALC = {"capacity_design": {"system": "BRBF"},
            "connections": [{"type": "simple shear (single-plate) beam-to-column / beam-to-girder, pinned (model releases major-axis moment)"},
                            {"type": "BRB pin-ended brace-to-gusset (story 1), welded 1-1/4 in A572-50 gusset"}],
            "members": [{"inputs": {"kind": "brace", "section": "BRB-Asc22.5"}}]}


def test_frame_type_is_not_read_from_the_word_moment():
    from snl import collect
    ft = collect.frame_type("BRBF", EX4_CALC)
    assert ft["moment_frame"] is False and ft["braced"] and ft["brb"] and ft["kind"] == "brbf"
    ft = collect.frame_type("", EX4_CALC)                                 # no system: the structure decides
    assert ft["moment_frame"] is False and ft["braced"]
    imf = {"capacity_design": {"SCWB": {}, "panel_zone": {}}, "connections": [{"type": "IMF beam-to-column, prequalified RBS (AISC 358-22 Ch. 5)"}]}
    assert collect.frame_type("IMF", imf)["moment_frame"] is True
    assert collect.frame_type("", imf)["moment_frame"] is True
    dual = dict(EX4_CALC, connections=EX4_CALC["connections"] + imf["connections"], capacity_design={"SCWB": {}})
    assert collect.frame_type("SCBF", dual)["kind"] == "dual"


# ---------------------------------------------------------------- NL-21 / NL-23: the review
def _ev(**over):
    ev = {"summary": {"nlrha": {"verdict": "ACCEPTABLE", "n_records": 11, "n_unacceptable": 0, "mean_drift_max": 0.013},
                      "pushover": {"bpon_levels": ["IO", "LS"], "bpon_ok": True, "nsp_permitted": True, "params_verified": True},
                      "ddm": {"n_pass": 11, "n_checked": 11, "gate_ok": True, "governing": "1.2D+1.6L", "phi_lambda": 1.2}},
          "pushover": {"params_verified": True, "directions": {"X": {"acceptance": {"BSE-1N": {"groups": [{"kind": "beam"}]}}}}}}
    for path, v in over.items():
        cur = ev
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = v
    return ev


def test_review_verdict_is_derived_from_the_results():
    from snl import review
    assert review.results_verdict(_ev())["status"] == "PASS"
    rv = review.results_verdict(_ev(**{"summary.ddm.n_pass": 10}))
    assert rv["status"] == "FAIL" and "DDM" in rv["failures"][0]
    rv = review.results_verdict(_ev(**{"pushover.directions": {"X": {"acceptance": {"BSE-1N": {"groups": []}}}}}))
    assert rv["status"] == "INCOMPLETE" and "NOT EVALUATED" in rv["not_evaluated"][0]
    md = review.mock_review(_ev(**{"summary.ddm.n_pass": 10}))
    assert "Nothing is required" not in md and "lighter design" not in md and "FAIL" in md
    assert "Nothing is required" in review.mock_review(_ev())


def test_review_says_loudly_that_no_model_is_configured(tmp_path, monkeypatch):
    from snl import llm, review
    monkeypatch.delenv("STELTIC_LLM_MODEL", raising=False); monkeypatch.delenv("STELTIC_LLM_BASE_URL", raising=False)
    c = llm.connection()
    assert c["mock"] and not c["mock_requested"] and "no model is configured" in c["mock_reason"]
    monkeypatch.setenv("STELTIC_LLM_MODEL", "gpt-x")
    assert "STELTIC_LLM_BASE_URL is empty" in llm.connection()["mock_reason"]
    job = tmp_path / "job"; job.mkdir()
    (job / "snl_summary.json").write_text(json.dumps(_ev()["summary"]))
    monkeypatch.delenv("STELTIC_LLM_MODEL", raising=False); monkeypatch.delenv("RAG_API_URL", raising=False)
    import io
    buf = io.StringIO()
    r = review.run(str(job), emit=review.Emitter(buf))
    assert r["ok"] and r["review_md"].startswith("> **NO MODEL CONFIGURED")
    assert any('"warning"' in l and "NO MODEL CONFIGURED" in l for l in buf.getvalue().splitlines())
    assert "NO MODEL CONFIGURED" in open(job / "review.html", encoding="utf-8").read()


def test_force_controlled_checks_cite_16_4_2_1():
    from snl import review
    md = review.mock_review(_ev(**{"nlrha": {"verdict": {"force_controlled_ok": True}}}))
    assert "16.4.2.1" in md and "16.4.2.2" not in md


# ---------------------------------------------------------------- NL-27: why the DDM is stricter
def test_ddm_sheet_states_the_equivalent_member_limit():
    from steltic_ddm import report_ddm
    runs = [{"phi": {"phi_s": 0.80, "cls": "HR-G", "beta_T": 3.0}}, {"phi": {"phi_s": None, "cls": "seismic"}}]
    eq = report_ddm.member_equivalence(runs, [{"role": "gravity_col", "section": "W14X120", "DC": 0.965}])
    assert eq[0]["limit"] == pytest.approx(0.80 / 0.90, abs=1e-3)
    html = report_ddm.member_equivalence_html(eq)
    assert "0.89" in html and "0.965" in html and "expected" in html
