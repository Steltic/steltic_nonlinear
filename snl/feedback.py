"""feedback.py -- the three re-design loops the nonlinear analyses feed back into HR Steel.

Pure functions over a finished SNL job folder (the Steltic package plus pushover/, nlrha/ and
ddm_results.json). Each loop reads what the analyses measured, decides whether the building is
eligible, builds a machine-derived CHANGE SET and the exact brief HR Steel's design agent applies
through its *Continue* path. Nothing here talks to a server; snl/loop.py does that.

    drift      Design drift to the measured response instead of C_d (ASCE 7-22 16.1.2, RC I-III only).
    resize     Resize by system role -- the DDM / NLRHA / pushover strain picture joined to the
               member-based D/C of calc_package.json.
    mechanism  Mechanism shaping through SCWB and panel zones -- pushover component acceptance and the
               AISC 342 C5.4a.1.a.1 panel-zone modifier mapped back to HR Steel inputs.

Every number in a plan is read from the package files and carried into the brief with its source, so
the design agent never has to invent the Chapter 16 result it relies on.
"""
from __future__ import annotations
import csv, json, math, os, re

LOOPS = {
    "drift": "Design drift to the measured response",
    "resize": "Resize by system role",
    "mechanism": "Mechanism shaping through SCWB and panel zones",
}
BRIEF_HEADER = "=== SNL FEEDBACK LOOP: %s ==="

# thresholds (all overridable through options)
DEFAULTS = dict(
    target_fraction=0.90,        # drift: aim the re-run NLRHA mean drift at this fraction of the 16.4.1.2 limit
    min_gain=0.03,               # drift: below this relative gain the loop is not worth a re-design
    dc_low=0.55, dc_high=0.95,   # resize: member D/C bands
    strain_elastic=1.0,          # resize: DDM max eps/eps_y below which a group never yields at collapse
    strain_hinged=3.0,           # resize: DDM ratio at which the DDM counts a hinge
    hinged_share=0.5,            # resize: share of the group hinged at collapse -> bottleneck
    nl_cp_high=0.80, nl_cp_low=0.25,   # resize: NLRHA mean-peak D/C against CP
    drift_governed=0.90,         # resize: linear drift utilisation above which lateral groups are held
    scwb_target=1.5,             # mechanism: required column-to-beam ratio at a storey that hinged its columns
    pz_low=0.6, pz_high=0.9,     # mechanism: AISC 342 C5.4a.1.a.1(b) V_pz / V_ye window
)


# ------------------------------------------------------------------------------------------------ files
def _json(path, default=None):
    if not os.path.exists(path):
        return default
    s = open(path, encoding="utf-8").read()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s))


def section_weight_lb_ft(section, A=None):
    """lb/ft of a rolled section from its AISC label (W14X730 -> 730); A (in^2) x 3.4 otherwise."""
    m = re.match(r"^(?:W|S|M|HP|C|MC)\s*\d+(?:\.\d+)?X(\d+(?:\.\d+)?)$", str(section).strip().upper().replace(" ", ""))
    if m:
        return float(m.group(1))
    if A:
        return float(A) * 490.0 / 144.0
    return None


def _cfg_values(text):
    """The cfg.py scalars a plan needs, by regex (cfg.py is never exec'd here)."""
    out = {}
    for key in ("drift_limit", "rho"):
        m = re.search(r"\b%s\s*=\s*([0-9.]+)" % key, text)
        if m:
            out[key] = float(m.group(1))
    for key in ("Ie", "Cd", "R", "SDS", "SD1"):
        m = re.search(r"\b%s\s*=\s*([0-9.]+)" % key, text) or re.search(r'"%s"\s*:\s*([0-9.]+)' % key, text)
        if m:
            out[key] = float(m.group(1))
    m = re.search(r"system\s*=\s*['\"]([^'\"]+)['\"]", text)
    if m:
        out["system"] = m.group(1)
    out["has_relief"] = "drift_relief_16_1_2" in text
    m = re.search(r"risk[\s_]*category\s*[:=]?\s*['\"]?\b(IV|III|II|I)\b", text, flags=re.I)
    if m:
        out["risk_category"] = m.group(1).upper()
    return out


def decode_tag(t):
    """Steltic node tag k*100000 + i*100 + j -> (i, j, k)."""
    t = int(t)
    return ((t % 100000) // 100, t % 100, t // 100000)


# ------------------------------------------------------------------------------------------------ the job
def read_job(job):
    """Everything the three loops read, in one dict. Missing analyses come back as None -- never invented."""
    from pushover import package_reader as PR
    job = os.path.abspath(job)
    pkg = PR.load(job)
    cfg_text = open(os.path.join(job, "cfg.py"), encoding="utf-8", errors="replace").read() if os.path.exists(os.path.join(job, "cfg.py")) else ""
    cfgv = _cfg_values(cfg_text)
    nodes = pkg.model.nodes
    zs = sorted({round(z, 3) for (_, _, z) in nodes.values()})
    levels = [z for z in zs if z > 1e-6]                                       # level k (1-based) -> levels[k-1]
    # roles: the DDM rule (lateral_col = column on a line a brace or an unreleased beam frames into)
    lateral_nodes = set()
    for e in pkg.model.elements:
        kind = pkg.schedule.get(e["tag"], {}).get("member")
        if kind == "brace" or (kind == "beam" and not e.get("release")):
            lateral_nodes.add(e["n1"]); lateral_nodes.add(e["n2"])
    lat_lines = {decode_tag(t)[:2] for t in lateral_nodes}
    top = max(levels) if levels else None
    elements = []
    for e in pkg.model.elements:
        sch = pkg.schedule.get(e["tag"])
        if not sch:
            continue
        kind, sec = sch["member"], sch["section"]
        i, j, k = decode_tag(e["n1"])
        z1 = nodes.get(e["n1"], (0, 0, 0))[2]; z2 = nodes.get(e["n2"], (0, 0, 0))[2]
        if kind == "col":
            role = "lateral_col" if (i, j) in lat_lines else "gravity_col"
            level = _level_of(levels, max(z1, z2))                            # the storey the column serves
        elif kind == "beam":
            role = "roof" if (top is not None and abs(z1 - top) < 1e-6) else "floor"
            level = _level_of(levels, z1)
        else:
            role, level = "brace", _level_of(levels, max(z1, z2))
        elements.append(dict(tag=e["tag"], kind=kind, section=sec, role=role, level=level, z_lo=min(z1, z2), z_hi=max(z1, z2),
                             moment=(kind == "beam" and not e.get("release")), length_in=float(sch.get("length_in") or 0),
                             combo=sch.get("governing_combo", ""), A=e.get("A")))
    groups = {}
    for el in elements:
        g = groups.setdefault("%s-%s" % (el["role"], el["section"]), dict(id="%s-%s" % (el["role"], el["section"]), role=el["role"], kind=el["kind"],
                                                                          section=el["section"], n=0, levels=set(), length_in=0.0, tags=[], moment=False, z=set()))
        g["n"] += 1; g["levels"].add(el["level"]); g["length_in"] += el["length_in"]; g["tags"].append(el["tag"]); g["moment"] |= el["moment"]
        g["z"].add(round(el["z_lo"])); g["z"].add(round(el["z_hi"]))
        w = section_weight_lb_ft(el["section"], el.get("A"))
        g["lb_ft"] = w
    for g in groups.values():
        g["levels"] = sorted(g["levels"]); g["z"] = sorted(g["z"])
        g["tons"] = round(g["length_in"] / 12.0 * (g["lb_ft"] or 0.0) / 2000.0, 2)
    calc = pkg.calc or {}
    members = {m.get("id"): m for m in calc.get("members", []) if isinstance(m, dict)}
    from . import compare as C
    st = C.steltic_facts(job)
    return dict(job=job, pkg=pkg, name=pkg.name, cfg_text=cfg_text, cfg=cfgv, levels=levels, elements=elements, groups=groups,
                calc=calc, members=members, steltic=st,
                nlrha=_json(os.path.join(job, "nlrha", "nlrha_package.json")),
                pushover=_json(os.path.join(job, "pushover", "pushover_package.json")),
                params=_json(os.path.join(job, "pushover", "hinge_params_used.json"), {}),
                ddm=_json(os.path.join(job, "ddm_results.json")),
                summary=_json(os.path.join(job, "snl_summary.json"), {}),
                snl_run=_json(os.path.join(job, "snl_run.json")))


def _level_of(levels, z):
    for k, zl in enumerate(levels, start=1):
        if abs(zl - z) < 1e-3:
            return k
    return None


def steel_tons(jd):
    """Total short tons of the scheduled members and the lateral share (lateral columns + moment beams + braces)."""
    tot = sum(g["tons"] for g in jd["groups"].values())
    lat = sum(g["tons"] for g in jd["groups"].values() if g["role"] in ("lateral_col", "brace") or g["moment"])
    return dict(total=round(tot, 1), lateral=round(lat, 1))


def risk_category(jd):
    nl = jd.get("nlrha") or {}
    rc = (nl.get("limits") or {}).get("risk_category")
    if rc:
        return rc
    rcc = jd["cfg"].get("risk_category")
    if rcc:
        return "I_II" if rcc in ("I", "II") else rcc
    Ie = jd["cfg"].get("Ie") or (jd["pkg"].basis.Ie or 1.0)
    return "IV" if Ie >= 1.5 else ("III" if Ie >= 1.25 else "I_II")


# ------------------------------------------------------------------------------------------------ loop 1: drift
def drift_plan(jd, options=None):
    o = dict(DEFAULTS, **(options or {}))
    nl = jd.get("nlrha"); st = jd["steltic"]
    plan = dict(kind="drift", title=LOOPS["drift"], eligible=False, reasons=[], numbers={}, sources={})
    rc = risk_category(jd); plan["risk_category"] = rc
    if not nl:
        plan["reasons"].append("no Chapter 16 result in nlrha/nlrha_package.json -- run the NLRHA first")
        return plan
    V, L = nl["verdict"], nl["limits"]
    mean16, lim16 = V.get("mean_drift_max"), L.get("mean_limit")
    lin = st.get("drift_X") or st.get("drift_Y")
    lin_max = max(max(st["drift_X"] or [0]), max(st["drift_Y"] or [0])) / 100.0 if lin else None
    lin_lim = (st.get("drift_limit_pct") or 0) / 100.0 or None
    cfg_dl = jd["cfg"].get("drift_limit") or (lin_lim if lin_lim else 0.02)
    rho_factor = (cfg_dl / lin_lim) if (lin_lim and cfg_dl) else 1.0             # drift_limit / reported allowable (rho for MF-only SDC D-F)
    plan["numbers"] = dict(linear_drift=lin_max, linear_limit=lin_lim, cfg_drift_limit=cfg_dl, rho_factor=round(rho_factor, 3),
                           nlrha_mean_drift=mean16, nlrha_limit=lim16, nlrha_verdict="ACCEPTABLE" if V.get("overall") else "NOT ACCEPTABLE",
                           table_12_12_1=L.get("table_12_12_1"), margin=(1 - mean16 / lim16) if (mean16 and lim16) else None,
                           target_fraction=o["target_fraction"])
    plan["sources"] = dict(linear="report.html Chapter 8 (C_d delta_e / I_e per storey)", nlrha="nlrha/nlrha_package.json verdict.mean_drift_max vs limits.mean_limit (16.4.1.2)",
                           clause="ASCE 7-22 16.1.2: the 12.12.1 drift limits need not apply for Risk Category I-III when a Chapter 16 analysis is performed")
    if not (mean16 and lim16 and lin_max and lin_lim):
        plan["reasons"].append("drift numbers incomplete (need the report's Chapter 8 table and the NLRHA verdict)")
        return plan
    scale = o["target_fraction"] * lim16 / mean16
    new_eff = lin_max * scale                                   # expected linear drift once the NLRHA mean sits at target_fraction x limit
    new_cfg = round(min(new_eff * rho_factor, lim16), 4)
    plan["numbers"].update(scale=round(scale, 3), new_linear_target=round(new_eff, 4), new_cfg_drift_limit=new_cfg,
                           prize="Chapter 16 measured %.2f%% of a %.2f%% limit: %.0f%% of the margin the linear design never saw"
                                 % (100 * mean16, 100 * lim16, 100 * (1 - mean16 / lim16)))
    if rc == "IV":
        plan["reasons"].append("Risk Category IV: 16.1.2 keeps the 12.12.1 drift limits -- no relief (the numbers show the size of the prize)")
    if not V.get("overall"):
        plan["reasons"].append("the Chapter 16 verdict is NOT ACCEPTABLE -- a relief cannot rest on it")
    if scale <= 1.0 + o["min_gain"]:
        plan["reasons"].append("the NLRHA already sits within %.0f%% of its limit -- nothing to encash" % (100 * o["min_gain"]))
    if jd["cfg"].get("has_relief"):
        plan["reasons"].append("cfg.py already carries drift_relief_16_1_2 -- this design is a relieved one; verify it instead of relaxing it again")
    plan["eligible"] = not plan["reasons"]
    plan["relief"] = dict(clause="ASCE 7-22 16.1.2", nlrha_job=jd["name"], nlrha_run=(jd.get("snl_run") or {}).get("finished") or nl.get("generated"),
                          nlrha_mean_drift=round(mean16, 5), nlrha_limit=round(lim16, 5), nlrha_verdict=plan["numbers"]["nlrha_verdict"],
                          nlrha_records=V.get("n_records"), table_12_12_1=L.get("table_12_12_1"), linear_target=new_cfg,
                          previous_drift_limit=cfg_dl, risk_category=rc,
                          note="Linear target = measured linear drift x (%.2f x Ch.16 limit / Ch.16 mean) = %.4f x %.3f; the relaxed design is provisional until Chapter 16 is re-run on it."
                               % (o["target_fraction"], lin_max, scale))
    plan["tons"] = steel_tons(jd)
    plan["brief"] = drift_brief(plan, jd)
    plan["verify"] = dict(kind="nlrha", note="re-run the Chapter 16 suite on the candidate; pass = 16.4 verdict ACCEPTABLE with the mean drift at or below the limit")
    return plan


def drift_brief(plan, jd):
    n = plan["numbers"]; r = plan["relief"]
    lines = [BRIEF_HEADER % "drift", "",
             "The Nonlinear module ran the ASCE 7-22 Chapter 16 NLRHA on this design (job %s): suite-mean MCE_R story drift %.2f%% against the 16.4.1.2 limit of %.2f%% (verdict %s, %s records). "
             "The linear design is drift-governed at %.2f%% of a %.2f%% allowable (C_d delta_e / I_e). Under 16.1.2 the 12.12.1 drift limits need not apply for Risk Category %s, so the linear drift target is reset to the measured response."
             % (jd["name"], 100 * n["nlrha_mean_drift"], 100 * n["nlrha_limit"], n["nlrha_verdict"], r.get("nlrha_records"), 100 * n["linear_drift"], 100 * n["linear_limit"], plan["risk_category"].replace("_", "/")),
             "",
             "DO EXACTLY THIS:",
             "1. Add this block to cfg VERBATIM (top-level key) and set cfg['drift_limit'] = %.4f:" % r["linear_target"],
             "   drift_relief_16_1_2 = " + json.dumps(r, indent=None),
             "2. Lighten the lateral frame until the amplified drift C_d delta_e / I_e lands just under %.2f%% of story height in BOTH directions (the reported allowable will read %.2f%% after the rho division, if any): shallower / lighter SMF beams first, then column groups, keeping the section families and level grouping. Every strength check (D/C <= 1.0, target 0.85-0.95), SCWB ratio, panel-zone check and connection must still pass; keep gravity members, loads, seismic parameters and everything else identical."
             % (100 * n["new_cfg_drift_limit"], 100 * n["new_linear_target"]),
             "3. Record the relief in calc_package['capacity_design']['drift_relief_16_1_2'] (the block above plus the new drifts per direction).",
             "4. Re-run pipeline.design_and_report, re-derive the affected AISC capacities into calc_package.json, run consistency.check, re-render with report.build_report.",
             "5. Finish with a table: SMF beam and column sizes by level group BEFORE -> AFTER, steel weight before/after (this design: %.1f tons total, %.1f tons lateral), governing D/C, drift X/Y vs the new target."
             % (plan["tons"]["total"], plan["tons"]["lateral"]),
             "",
             "Do NOT alter the numbers in the relief block. If preflight reports a 16.1.2 ERROR, stop and report it. The Nonlinear module re-runs Chapter 16 on the result; it is provisional until that passes."]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ loop 2: resize
def _family(section):
    m = re.match(r"^([A-Z]+\d+(?:\.\d+)?)X([\d.]+)$", str(section).upper().replace(" ", ""))
    return (m.group(1), float(m.group(2))) if m else (None, None)


def section_neighbours(section):
    """(lighter, heavier) AISC shapes of the same nominal depth family, from pushover/aisc_shapes.csv."""
    fam, wt = _family(section)
    if fam is None:
        return None, None
    try:
        from pushover import sections_db as SD
        rows = SD._table()
    except Exception:
        return None, None
    same = []
    for lab in rows:
        f2, w2 = _family(lab)
        if f2 == fam and w2 is not None:
            same.append((w2, lab))
    same.sort()
    lighter = [lab for w, lab in same if w <= 0.96 * wt]          # a real step, not the 277 / 278 twins of the table
    heavier = [lab for w, lab in same if w >= 1.04 * wt]
    return (lighter[-1] if lighter else None), (heavier[0] if heavier else None)


def resize_plan(jd, options=None):
    o = dict(DEFAULTS, **(options or {}))
    plan = dict(kind="resize", title=LOOPS["resize"], eligible=False, reasons=[], rows=[], change_set=[], sources={})
    dd, nl, po = jd.get("ddm"), jd.get("nlrha"), jd.get("pushover")
    if not (dd or nl or po):
        plan["reasons"].append("no nonlinear result in the job folder (ddm_results.json, nlrha/, pushover/)")
        return plan
    mt = {(r["role"], r["section"]): r for r in (dd or {}).get("member_table", [])}
    lam = {r["label"]: r for r in (dd or {}).get("runs", [])}                  # combo label -> lambda_u, phi check
    nl_groups = (nl or {}).get("deformation_groups", [])
    fc_cols = (nl or {}).get("force_controlled_columns", [])
    po_groups = []
    if po:
        for d in po["directions"].values():
            po_groups += d["acceptance"].get("BSE-2N", {}).get("groups", [])
    st = jd["steltic"]
    lin_max = max(max(st["drift_X"] or [0]), max(st["drift_Y"] or [0])) if st.get("drift_X") else None
    util = (lin_max / st["drift_limit_pct"]) if (lin_max and st.get("drift_limit_pct")) else None
    drift_governed = bool(util and util >= o["drift_governed"] and not jd["cfg"].get("has_relief"))
    plan["drift_utilisation"] = util; plan["drift_governed"] = drift_governed
    fc_by_tag = {}
    tag_group = {t: g["id"] for g in jd["groups"].values() for t in g["tags"]}
    for c in fc_cols:
        gid = tag_group.get(c.get("ele"))
        if gid:
            fc_by_tag[gid] = max(fc_by_tag.get(gid, 0.0), c.get("DC") or 0.0)
    for gid, g in sorted(jd["groups"].items()):
        m = jd["members"].get(gid) or {}
        dc = m.get("DC") if isinstance(m.get("DC"), (int, float)) else None
        d = mt.get((g["role"], g["section"]))
        zset = set(g["z"])
        def _match(x):
            return x.get("section") == g["section"] and (x.get("kind") == g["kind"]) and (round(x.get("z_in", -1)) in zset or not zset)
        nlg = [x for x in nl_groups if _match(x)]
        pog = [x for x in po_groups if _match(x)]
        row = dict(id=gid, role=g["role"], kind=g["kind"], section=g["section"], n=g["n"], levels=g["levels"], tons=g["tons"], moment=g["moment"],
                   DC=dc, combo=(m.get("inputs") or {}).get("governing_combo") or "", limit_state=m.get("limit_state"),
                   ddm_ratio=d.get("ratio") if d else None, ddm_yielded=d.get("yielded") if d else None, ddm_hinges=d.get("hinges") if d else None,
                   ddm_combo=d.get("combo") if d else None,
                   nl_dc_cp=max((x.get("DC_CP") or 0) for x in nlg) if nlg else None, nl_fc_dc=fc_by_tag.get(gid),
                   po_dc_cp=max((x.get("DC_CP") or 0) for x in pog) if pog else None,
                   po_yielded=sum(x.get("n_yielded") or 0 for x in pog) if pog else None)
        run = lam.get(row["ddm_combo"] or "") or {}
        row["ddm_lambda_u"] = run.get("lambda_u"); row["ddm_check"] = (run.get("check") or [None, None])[1] if run else None
        row.update(_classify(row, o, drift_governed))
        lighter, heavier = section_neighbours(g["section"])
        row["proposed"] = heavier if row["verdict"] == "upsize" else (lighter if row["verdict"] == "downsize" else None)
        plan["rows"].append(row)
    plan["change_set"] = [dict(id=r["id"], role=r["role"], section=r["section"], levels=r["levels"], proposed=r["proposed"], verdict=r["verdict"], reason=r["reason"], n=r["n"])
                          for r in plan["rows"] if r["verdict"] in ("upsize", "downsize") and r["proposed"]]
    plan["held"] = [dict(id=r["id"], reason=r["reason"]) for r in plan["rows"] if r["verdict"] == "hold"]
    plan["sources"] = dict(DC="design/calc_package.json members[].DC (group envelope, AISC 360 LRFD)",
                           ddm="ddm_results.json member_table (max eps/eps_y at the peak of the governing combination, yielded / hinged counts)",
                           nlrha="nlrha/nlrha_package.json deformation_groups (mean peak vs CP) and force_controlled_columns",
                           pushover="pushover/pushover_package.json acceptance BSE-2N groups (n_yielded, D/C CP)",
                           sections="pushover/aisc_shapes.csv: one step within the same nominal-depth family")
    if not plan["change_set"]:
        plan["reasons"].append("no group meets the resize rules (every group either sits in the 0.55-0.95 D/C band, is drift-held, or shows no system-level signal)")
    plan["eligible"] = not plan["reasons"]
    plan["tons"] = steel_tons(jd)
    combos = sorted({r["ddm_combo"] for r in plan["rows"] if r["verdict"] in ("upsize", "downsize") and r.get("ddm_combo")})
    plan["verify"] = dict(kind="ddm_only", combos=combos,
                          note="HR Steel re-checks every LRFD limit state; the DDM is re-run with --only on the governing combination(s) of the changed groups (%s) instead of the full sweep" % (", ".join(combos) or "none recorded"))
    plan["brief"] = resize_brief(plan, jd)
    return plan


def _classify(r, o, drift_governed):
    dc, ratio, hinges, n = r["DC"], r["ddm_ratio"], r["ddm_hinges"], r["n"]
    hinged_share = (hinges / n) if (hinges is not None and n) else None
    lateral = r["role"] in ("lateral_col", "brace") or r["moment"]
    lam = r.get("ddm_lambda_u")
    lam_txt = (" at lambda_u = %.2f" % lam) if isinstance(lam, (int, float)) else ""
    # bottleneck: the system hinges most of the group at collapse, or the NLRHA drives it near CP, while the code check sits well below 1.0
    if hinged_share is not None and hinged_share >= o["hinged_share"]:
        why = "DDM: max strain %.1fx yield, %d of %d hinged at collapse under %s%s" % (ratio or 0, hinges or 0, n, r.get("ddm_combo") or "?", lam_txt)
        if dc is not None and dc >= o["dc_high"]:
            return dict(verdict="upsize", bottleneck=True, reason=why + "; D/C %.2f already high" % dc)
        return dict(verdict="upsize", bottleneck=True, reason=why + "; code D/C only %s -- the system, not the member check, is asking for more" % ("%.2f" % dc if dc is not None else "n/a"))
    if r["nl_dc_cp"] is not None and r["nl_dc_cp"] >= o["nl_cp_high"]:
        return dict(verdict="upsize", bottleneck=True, reason="NLRHA: mean peak rotation at %.0f%% of CP" % (100 * r["nl_dc_cp"]))
    if r["nl_fc_dc"] is not None and r["nl_fc_dc"] >= o["dc_high"]:
        return dict(verdict="upsize", bottleneck=True, reason="NLRHA force-controlled column D/C %.2f (16.4.2.1)" % r["nl_fc_dc"])
    if ratio is not None and ratio >= o["strain_hinged"] and (hinges or 0) > 0:
        return dict(verdict="keep", bottleneck=True, reason="DDM: %.1fx yield with %d of %d hinged under %s%s -- heavily used but not the mechanism; take a second look, no resize proposed"
                    % (ratio, hinges or 0, n, r.get("ddm_combo") or "?", lam_txt))
    # over-designed: a low code D/C with no system signal at all
    quiet = ((ratio is None or ratio < o["strain_elastic"]) and (r["nl_dc_cp"] is None or r["nl_dc_cp"] < o["nl_cp_low"])
             and (r["po_dc_cp"] is None or r["po_dc_cp"] < o["nl_cp_low"]) and not (hinges or 0) and not (r["ddm_yielded"] or 0))
    if dc is not None and dc < o["dc_low"] and quiet:
        if lateral and drift_governed:
            return dict(verdict="hold", bottleneck=False, reason="D/C %.2f and elastic at collapse, but the building is drift-governed (%s) -- run the drift loop first, then resize"
                        % (dc, "linear drift at the limit"))
        if r["role"] == "lateral_col":
            return dict(verdict="downsize", bottleneck=False, reason="D/C %.2f, elastic at collapse -- sized by drift / SCWB, not strength; one step lighter, SCWB and drift to be re-checked" % dc)
        return dict(verdict="downsize", bottleneck=False, reason="D/C %.2f with no yielding in any nonlinear analysis -- the code check is the only thing asking for this size" % dc)
    if dc is not None and dc < o["dc_low"] and (ratio is not None and ratio >= o["strain_elastic"]):
        return dict(verdict="keep", bottleneck=False, reason="D/C %.2f but the DDM yields it (%.1fx) -- the system uses it" % (dc, ratio))
    return dict(verdict="keep", bottleneck=False, reason="within the %.2f-%.2f D/C band or no signal" % (o["dc_low"], o["dc_high"]))


def resize_brief(plan, jd):
    lines = [BRIEF_HEADER % "resize", "",
             "The Nonlinear module joined the system-level picture (DDM strain ratios and hinges at collapse, NLRHA mean rotation vs CP, pushover component acceptance) to the member-based D/C of calc_package.json for job %s. "
             "The two orderings differ: the groups below carry a code check the system is not asking for, or are the actual bottleneck. Steel now: %.1f tons total, %.1f tons lateral."
             % (jd["name"], plan["tons"]["total"], plan["tons"]["lateral"]),
             "", "CHANGE SET -- apply every line exactly, same role, same levels, nothing else:"]
    for c in plan["change_set"]:
        lines.append("  %s at levels %s (%d members): %s -> %s  [%s: %s]" % (c["id"], c["levels"], c["n"], c["section"], c["proposed"], c["verdict"].upper(), c["reason"]))
    if plan["held"]:
        lines.append("")
        lines.append("HELD (do not touch): " + "; ".join("%s (%s)" % (h["id"], h["reason"]) for h in plan["held"]))
    lines += ["",
              "Then: re-run pipeline.design_and_report, re-derive every affected AISC capacity into calc_package.json (member ids change with the sections), re-check SCWB and panel zones where a column or SMF beam changed, run consistency.check, re-render with report.build_report.",
              "STOP at the first line that pushes any D/C above 0.95, any SCWB ratio below 1.0 or the drift above its limit, and say which line caused it -- do not substitute a different size.",
              "Finish with a table: group, levels, section BEFORE -> AFTER, D/C before -> after, steel weight before/after, drift X/Y vs limit.",
              "The Nonlinear module re-verifies with the DDM on the governing combination(s): %s." % (", ".join(plan["verify"]["combos"]) or "as recorded")]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ loop 3: mechanism
def mechanism_plan(jd, options=None):
    o = dict(DEFAULTS, **(options or {}))
    plan = dict(kind="mechanism", title=LOOPS["mechanism"], eligible=False, reasons=[], storeys=[], panel_zone=None, sources={})
    po = jd.get("pushover")
    if not po:
        plan["reasons"].append("no pushover result (pushover/pushover_package.json)")
        return plan
    levels = jd["levels"]
    hinged = {}
    for dname, d in po["directions"].items():
        acc = d["acceptance"].get("BSE-2N") or {}
        for c in acc.get("census", []):
            z = c.get("z_in", 0)
            if z <= 1e-6:
                continue                                                      # column-base hinges of a fixed base are the intended mechanism
            k = _level_of(levels, z)
            cy, by = c.get("col_yielded") or 0, c.get("beam_yielded") or 0
            if cy > 0:
                e = hinged.setdefault(k, dict(level=k, z_in=z, col_yielded=0, beam_yielded=0, col_hinges=c.get("col_hinges"), beam_hinges=c.get("beam_hinges"), dirs=[]))
                e["col_yielded"] = max(e["col_yielded"], cy); e["beam_yielded"] = max(e["beam_yielded"], by); e["dirs"].append(dname)
    plan["storeys"] = [hinged[k] for k in sorted(hinged)]
    for s in plan["storeys"]:
        s["scwb_target"] = o["scwb_target"]
        s["reading"] = ("column hinging with %d beam ends yielded: a storey mechanism forming" % s["beam_yielded"] if s["beam_yielded"]
                        else "columns yielded before the beams at this level: the local SCWB ratio did not produce the global mechanism")
    # panel zones: the AISC 342 Table C5.5 modifier machinery
    mods = []
    bf = (jd.get("params") or {}).get("beam_flexure") or {}
    for m in bf.get("modifiers", []) or []:
        why = str(m.get("why", ""))
        if float(m.get("factor", 1.0)) < 1.0 and re.search(r"panel|V_?PZ|C5\.4a\.1\.a\.1\s*\(b\)", why, flags=re.I):
            joints = []
            for jm in re.finditer(r"((?:corner|interior|exterior|perimeter|end|edge)[^,;()]*?joints?)\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", why, flags=re.I):
                lo_, hi_ = float(jm.group(2)), float(jm.group(3))
                joints.append(dict(joint=jm.group(1).strip(), ratio=[lo_, hi_],
                                   action=("doublers" if lo_ > o["pz_high"] else ("over-strong" if hi_ < o["pz_low"] else "marginal"))))
            if not joints:
                joints = [dict(joint=j.strip(), ratio=None, action="doublers") for j in
                          sorted(set(re.findall(r"((?:corner|interior|exterior|perimeter|end|edge)[^,;()]*?joints?)", why, flags=re.I)))]
            mods.append(dict(factor=float(m.get("factor")), why=why, joints=joints, sections=m.get("sections")))
    cp = (jd.get("calc") or {}).get("capacity_design") or {}
    pz = cp.get("panel_zone") if isinstance(cp, dict) else None
    scwb = cp.get("SCWB") if isinstance(cp, dict) else None
    if mods:
        plan["panel_zone"] = dict(modifiers=mods, design_block=pz, window=[o["pz_low"], o["pz_high"]],
                                  reading="the pushover applied a x%.2f reduction to the beam-hinge a and b (AISC 342 C5.4a.1.a.1(b)) because V_pz/V_ye fell outside %.1f-%.1f at the joints named; doublers (or a heavier column) at those joints clear it"
                                          % (mods[0]["factor"], o["pz_low"], o["pz_high"]))
    plan["scwb_design"] = scwb
    plan["sources"] = dict(census="pushover/pushover_package.json directions[*].acceptance['BSE-2N'].census (col_yielded / beam_yielded per elevation)",
                           modifiers="pushover/hinge_params_used.json beam_flexure.modifiers (factor, why)",
                           design="design/calc_package.json capacity_design.SCWB and capacity_design.panel_zone")
    if not plan["storeys"] and not mods:
        plan["reasons"].append("no column hinging above the base at BSE-2N and no panel-zone modifier in force -- the local SCWB rule produced the intended mechanism")
    plan["eligible"] = not plan["reasons"]
    plan["tons"] = steel_tons(jd)
    plan["verify"] = dict(kind="pushover", note="re-push the candidate with the modifier cleared where the recorded doublers put V_pz/V_ye in the window; pass = no column yielding above the base at the named storeys and the modifier cleared")
    plan["brief"] = mechanism_brief(plan, jd)
    return plan


def mechanism_brief(plan, jd):
    lines = [BRIEF_HEADER % "mechanism", "",
             "The Nonlinear module's pushover (ASCE 41 NSP with AISC 342 component acceptance) of job %s read the mechanism at the BSE-2N target displacement and the AISC 342 C5.4a.1.a.1 panel-zone conditions. Map the findings back to the design inputs as follows; change nothing else."
             % jd["name"], ""]
    if plan["storeys"]:
        lines.append("A. STRONG-COLUMN / WEAK-BEAM (AISC 341 E3.4a) -- storeys where the columns yielded at BSE-2N:")
        for s in plan["storeys"]:
            lines.append("  level %s (z = %.0f in, push %s): %d column ends yielded, %d beam ends yielded -- %s. Raise the column-to-beam ratio at every joint of this storey to sum(M*pc)/sum(M*pb) >= %.2f (heavier column group, or trim the beam Z where the beams are over-strong). Record the ratio achieved per storey in capacity_design['SCWB']['by_story']."
                         % (s["level"], s["z_in"], "/".join(s["dirs"]), s["col_yielded"], s["beam_yielded"], s["reading"], s["scwb_target"]))
        lines.append("")
    if plan["panel_zone"]:
        pzp = plan["panel_zone"]
        lines.append("B. PANEL ZONES (AISC 341 E3.6e / 360 J10.6; AISC 342 C5.4a.1.a.1(b)) -- the pushover reduced the beam-hinge a and b by x%.2f because:" % pzp["modifiers"][0]["factor"])
        for m in pzp["modifiers"]:
            lines.append("  \"%s\"" % m["why"])
        allj = [j for m in pzp["modifiers"] for j in m["joints"]]
        need = [j for j in allj if j["action"] in ("doublers", "marginal")]
        strong = [j for j in allj if j["action"] == "over-strong"]
        lo_, hi_ = pzp["window"]
        if need:
            lines.append("  Add doubler plates (or a heavier column) at: %s, so that V_pz/V_ye (AISC 342 Eq. C5-21 with Spec. J10.6(a)) sits within %.1f-%.1f at every SMF joint. Keep the design panel-zone check (Ru <= phiRn) satisfied."
                         % ("; ".join("%s (%s)" % (j["joint"], ("%.2g-%.2g now" % tuple(j["ratio"])) if j["ratio"] else "outside the window") for j in need), lo_, hi_))
        if strong:
            lines.append("  Panel zones STRONGER than the balanced range (V_pz/V_ye below %.1f) at: %s -- doublers would make this worse; either accept the reduction there or rebalance (lighter column flange / heavier beam) and say which."
                         % (lo_, "; ".join("%s (%s)" % (j["joint"], ("%.2g-%.2g" % tuple(j["ratio"])) if j["ratio"] else "?") for j in strong)))
        if not need and not strong:
            lines.append("  Add doubler plates where needed so that V_pz/V_ye sits within %.1f-%.1f at every SMF joint (AISC 342 Eq. C5-21 with Spec. J10.6(a)); keep Ru <= phiRn." % (lo_, hi_))
        lines.append("  Record per joint group in capacity_design['panel_zone']['by_joint']: joint, column, beams, Ru_kip, phiRn_kip, doubler_in (0 where none), V_pz_over_V_ye. The Nonlinear module reads doubler_in and V_pz_over_V_ye to clear the modifier before it re-pushes.")
        lines.append("")
    lines += ["Then: re-run pipeline.design_and_report, re-derive every affected capacity (columns, doublers, connections) into calc_package.json, run consistency.check, re-render with report.build_report.",
              "Finish with: column sizes by level BEFORE -> AFTER, SCWB ratio per storey before -> after, doubler thickness per joint group, V_pz/V_ye per joint group, steel weight before/after (%.1f tons now), drift X/Y vs limit." % plan["tons"]["total"]]
    return "\n".join(lines)


def cleared_params(params, candidate_calc, window=(0.6, 0.9)):
    """The hinge-parameter file for the re-push: the C5.4a.1.a.1(b) modifier is dropped only when the candidate
    package records every joint group with V_pz/V_ye inside the window. Returns (params, decision text)."""
    p = json.loads(json.dumps(params or {}))
    bf = p.get("beam_flexure") or {}
    mods = bf.get("modifiers") or []
    pz = ((candidate_calc or {}).get("capacity_design") or {}).get("panel_zone") or {}
    by_joint = pz.get("by_joint") if isinstance(pz, dict) else None
    keep, dropped, why = [], [], []
    for m in mods:
        is_pz = float(m.get("factor", 1.0)) < 1.0 and re.search(r"panel|V_?PZ|C5\.4a\.1\.a\.1\s*\(b\)", str(m.get("why", "")), flags=re.I)
        if not is_pz:
            keep.append(m); continue
        if not by_joint:
            keep.append(m); why.append("kept x%.2f: the candidate package has no capacity_design.panel_zone.by_joint list" % float(m.get("factor")))
            continue
        bad = []
        for j in by_joint:
            v = j.get("V_pz_over_V_ye")
            if not isinstance(v, (int, float)) or not (window[0] - 1e-9 <= float(v) <= window[1] + 1e-9):
                bad.append("%s: %s" % (j.get("joint", "?"), v))
        if bad:
            keep.append(m); why.append("kept x%.2f: V_pz/V_ye still outside %.1f-%.1f at %s" % (float(m.get("factor")), window[0], window[1], "; ".join(bad)))
        else:
            dropped.append(m); why.append("cleared x%.2f: %d joint groups report V_pz/V_ye within %.1f-%.1f (doublers %s)"
                                          % (float(m.get("factor")), len(by_joint), window[0], window[1], ", ".join("%s in" % j.get("doubler_in") for j in by_joint)))
    if mods:
        bf["modifiers"] = keep
        mc = bf.get("modifier_checks")
        if isinstance(mc, dict) and dropped:
            mc["(b) panel zone"] = "cleared by the SNL mechanism loop: " + why[-1]
        p["beam_flexure"] = bf
    return p, "; ".join(why) or "no panel-zone modifier to clear"


# ------------------------------------------------------------------------------------------------ dispatch
def plan(jd, kind, options=None):
    return {"drift": drift_plan, "resize": resize_plan, "mechanism": mechanism_plan}[kind](jd, options)


def all_plans(jd, options=None):
    return {k: plan(jd, k, options) for k in LOOPS}


def status(jd):
    """What the loops can stand on: which analyses exist and whether the Chapter 16 run is complete."""
    nl = jd.get("nlrha"); run = jd.get("snl_run") or {}
    steps = run.get("steps") or {}
    return dict(name=jd["name"], risk_category=risk_category(jd), levels=len(jd["levels"]), groups=len(jd["groups"]), tons=steel_tons(jd),
                nlrha=bool(nl), nlrha_verdict=(("ACCEPTABLE" if nl["verdict"].get("overall") else "NOT ACCEPTABLE") if nl else None),
                nlrha_mean_drift=(nl["verdict"].get("mean_drift_max") if nl else None), nlrha_limit=(nl["limits"].get("mean_limit") if nl else None),
                pushover=bool(jd.get("pushover")), ddm=bool(jd.get("ddm")), finished=run.get("finished"),
                steps={k: (v.get("returncode") if isinstance(v, dict) else None) for k, v in steps.items()},
                ch16_complete=bool(nl) and steps.get("nlrha", {}).get("returncode", 0 if nl else None) == 0,
                relief=jd["cfg"].get("has_relief"), drift=jd["steltic"].get("drift_limit_pct"))


def plan_as_json(p):
    """A plan without anything json.dumps cannot take."""
    return json.loads(json.dumps(p, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
