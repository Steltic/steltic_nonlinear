"""The three feedback change-set builders on the shipped Ex22 outputs (no OpenSees, no network)."""
import json, os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")

from snl import feedback as F  # noqa: E402


def _rc3_twin():
    """Ex22 with the Chapter 16 limits of a Risk Category III building (the drift relief needs RC I-III)."""
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_RC3")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "feedback"))
    nl = json.load(open(os.path.join(job, "nlrha", "nlrha_package.json")))
    nl["limits"].update(risk_category="III", mean_limit=0.03, table_12_12_1=0.015)
    json.dump(nl, open(os.path.join(job, "nlrha", "nlrha_package.json"), "w"))
    cfg = open(os.path.join(job, "cfg.py")).read().replace("Ie=1.5", "Ie=1.25")
    open(os.path.join(job, "cfg.py"), "w").write(cfg)
    return job


def test_read_job_roles_groups_and_tons():
    jd = F.read_job(EX22)
    assert len(jd["levels"]) == 6 and len(jd["elements"]) == 558
    g = jd["groups"]
    assert set(g) == set(jd["members"])                                  # every calc_package member id is a model group
    assert g["lateral_col-W14X730"]["levels"] == [1, 2] and g["roof-W24X76"]["n"] == 38
    assert g["floor-W40X277"]["moment"] and not g["floor-W27X94"]["moment"]
    t = F.steel_tons(jd)
    assert 1300 < t["total"] < 1400 and t["lateral"] < t["total"]
    st = F.status(jd)
    assert st["risk_category"] == "IV" and st["nlrha_verdict"] == "ACCEPTABLE" and st["ch16_complete"]


def test_drift_plan_rc_iv_shows_the_prize_but_is_not_eligible():
    p = F.drift_plan(F.read_job(EX22))
    assert not p["eligible"] and any("Risk Category IV" in r for r in p["reasons"])
    n = p["numbers"]
    assert abs(n["nlrha_mean_drift"] - 0.01463) < 1e-4 and n["nlrha_limit"] == 0.02 and abs(n["margin"] - 0.269) < 0.01
    assert abs(n["linear_drift"] - 0.0091) < 1e-6 and n["linear_limit"] == 0.01
    assert "27%" in n["prize"]


def test_drift_plan_rc_iii_relief_and_brief():
    jd = F.read_job(_rc3_twin())
    p = F.drift_plan(jd)
    assert p["eligible"], p["reasons"]
    n, r = p["numbers"], p["relief"]
    assert abs(n["scale"] - 0.9 * 0.03 / 0.014627) < 0.01
    assert r["linear_target"] == n["new_cfg_drift_limit"] and r["linear_target"] <= r["nlrha_limit"]
    assert r["clause"] == "ASCE 7-22 16.1.2" and r["nlrha_verdict"] == "ACCEPTABLE" and r["risk_category"] == "III"
    b = p["brief"]
    assert b.startswith("=== SNL FEEDBACK LOOP: drift ===") and "drift_relief_16_1_2 = {" in b and ("cfg['drift_limit'] = %.4f" % r["linear_target"]) in b
    json.loads(b.split("drift_relief_16_1_2 = ", 1)[1].split("\n", 1)[0])       # the block in the brief is valid JSON
    # options: a lower target fraction gives a lower target
    p2 = F.drift_plan(jd, {"target_fraction": 0.8})
    assert p2["numbers"]["new_cfg_drift_limit"] < n["new_cfg_drift_limit"]


def test_resize_plan_joins_system_role_to_member_dc():
    jd = F.read_job(EX22)
    p = F.resize_plan(jd)
    rows = {r["id"]: r for r in p["rows"]}
    assert p["drift_governed"] and abs(p["drift_utilisation"] - 0.91) < 1e-6
    # the roof W24X76: D/C 0.73 but 38 of 38 hinged at collapse -> the bottleneck
    assert rows["roof-W24X76"]["verdict"] == "upsize" and rows["roof-W24X76"]["bottleneck"] and rows["roof-W24X76"]["proposed"] == "W24X84"
    assert rows["roof-W24X76"]["ddm_ratio"] > 9 and rows["roof-W24X76"]["ddm_hinges"] == 38
    # gravity columns with a low D/C and no yielding anywhere -> one step lighter
    assert rows["gravity_col-W14X90"]["verdict"] == "downsize" and rows["gravity_col-W14X90"]["proposed"] == "W14X82"
    # lateral columns at D/C 0.18 are heavily used at collapse -> keep (a second look, no resize)
    assert rows["lateral_col-W14X730"]["verdict"] == "keep" and rows["lateral_col-W14X730"]["bottleneck"]
    # moment beams with a low D/C are held while the building is drift-governed
    assert rows["roof-W33X141"]["verdict"] == "hold"
    ids = {c["id"] for c in p["change_set"]}
    assert "roof-W24X76" in ids and "gravity_col-W14X90" in ids and "roof-W33X141" not in ids
    assert p["verify"]["kind"] == "ddm_only" and "1.4D" in p["verify"]["combos"]
    b = p["brief"]
    assert b.startswith("=== SNL FEEDBACK LOOP: resize ===") and "roof-W24X76 at levels [6] (38 members): W24X76 -> W24X84" in b and "HELD" in b


def test_section_neighbours_step_the_family():
    assert F.section_neighbours("W14X730") == ("W14X665", "W14X808")
    assert F.section_neighbours("W40X277")[1] not in ("W40X278",)          # the 277 / 278 twins are not a step
    assert F.section_neighbours("HSS8X8X1/2") == (None, None)
    assert F.section_weight_lb_ft("W14X730") == 730 and abs(F.section_weight_lb_ft("HSS", A=10) - 34.03) < 0.1


def test_mechanism_plan_reads_the_panel_zone_modifier():
    jd = F.read_job(EX22)
    p = F.mechanism_plan(jd)
    assert p["eligible"] and p["storeys"] == []                            # Ex22 hinged no columns above the base
    pz = p["panel_zone"]
    assert pz["modifiers"][0]["factor"] == 0.8
    joints = {j["joint"]: j["action"] for j in pz["modifiers"][0]["joints"]}
    assert joints["interior Y-face joints"] == "doublers" and joints["corner joints"] == "over-strong" and joints["interior X-face joints"] == "marginal"
    b = p["brief"]
    assert "B. PANEL ZONES" in b and "interior Y-face joints (1.1-1.4 now)" in b and "STRONGER than the balanced range" in b
    assert "by_joint" in b and "A. STRONG-COLUMN" not in b


def test_mechanism_plan_with_column_hinging():
    jd = F.read_job(EX22)
    po = json.loads(json.dumps(jd["pushover"]))
    for c in po["directions"]["X"]["acceptance"]["BSE-2N"]["census"]:
        if c["z_in"] == 360:
            c["col_yielded"] = 12
    jd["pushover"] = po
    p = F.mechanism_plan(jd, {"scwb_target": 1.7})
    assert [s["level"] for s in p["storeys"]] == [2] and p["storeys"][0]["scwb_target"] == 1.7 and "X" in p["storeys"][0]["dirs"]
    assert "A. STRONG-COLUMN" in p["brief"] and ">= 1.70" in p["brief"]


def test_cleared_params_only_when_every_joint_is_inside_the_window():
    prm = F.read_job(EX22)["params"]
    good = {"capacity_design": {"panel_zone": {"by_joint": [{"joint": "a", "doubler_in": 0.75, "V_pz_over_V_ye": 0.85}, {"joint": "b", "doubler_in": 0, "V_pz_over_V_ye": 0.62}]}}}
    p, why = F.cleared_params(prm, good)
    assert p["beam_flexure"]["modifiers"] == [] and why.startswith("cleared x0.80")
    bad = {"capacity_design": {"panel_zone": {"by_joint": [{"joint": "a", "doubler_in": 0.75, "V_pz_over_V_ye": 1.1}]}}}
    p, why = F.cleared_params(prm, bad)
    assert len(p["beam_flexure"]["modifiers"]) == 1 and "still outside" in why
    p, why = F.cleared_params(prm, {})
    assert len(p["beam_flexure"]["modifiers"]) == 1 and "no capacity_design.panel_zone.by_joint" in why


def test_plans_without_analyses_are_not_eligible():
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "bare")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "nlrha", "pushover", "ddm_*", "feedback"))
    jd = F.read_job(job)
    for k in F.LOOPS:
        p = F.plan(jd, k)
        assert not p["eligible"] and p["reasons"]
    assert json.dumps(F.plan_as_json(F.all_plans(jd)))
