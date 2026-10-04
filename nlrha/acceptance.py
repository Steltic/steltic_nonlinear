"""acceptance.py -- ASCE 7-22 Section 16.4 evaluation of a suite of response histories (rules in ch16_params.json)."""
from __future__ import annotations
import math, re
import numpy as np
from pushover import sections_db as SDB

E_KSI = 29000.0


def risk_category(pkg, override=None):
    """'I_II' | 'III' | 'IV' -- from --risk-category, else cfg.py text ('Risk Category IV', RC III, risk="IV"), else Ie."""
    if override:
        o = override.upper().replace(" ", "")
        return "I_II" if o in ("I", "II", "I_II", "I/II") else o
    try:
        src = (pkg.root / "cfg.py").read_text(encoding="utf-8", errors="replace")
        mo = re.search(r"(?:risk[\s_]*category|\bRC)\s*[:=]?\s*['\"]?\b(IV|III|II|I)\b", src, re.I)
        if mo:
            v = mo.group(1).upper(); return "I_II" if v in ("I", "II") else v
    except Exception:
        pass
    Ie = getattr(pkg.basis, "Ie", None) or 1.0
    return "IV" if Ie >= 1.5 - 1e-9 else ("III" if Ie >= 1.25 - 1e-9 else "I_II")


def drift_limits(ch16, hn_in, H_story_in, rc="I_II"):
    """16.4.1.2: mean limit = 2 x Table 12.12-1 ('all other structures' row for the Risk Category); for hn > 100 ft also the
    tall-building cap hsx(4.71e-2 - 7.14e-5 hn) >= 0.03 hn (as a ratio: 0.0471 - 7.14e-5*hn_ft, floor 0.03). Ratio limits."""
    d = ch16["transient_drift"]
    tab = d.get("table_12_12_1_all_other", {}).get(rc, d["table_12_12_1_all_other_RC_I_II"])
    lim = d["factor_on_table_12_12_1"] * tab
    hn_ft = hn_in / 12.0
    tall = None
    if hn_ft > d["tall_height_ft"]:
        tall = max(d["tall_a"] - d["tall_b"] * hn_ft, d["tall_floor"])
        lim = min(lim, tall)
    return dict(mean_limit=lim, tall_limit=tall, table_12_12_1=tab, risk_category=rc,
                unacceptable_peak=ch16["unacceptable_response"]["peak_drift_factor_of_mean_limit"] * lim)


def record_status(r):
    """completed | nonconvergence | incomplete | not_run. Results written before NL-17 carry no `status`: a
    "crawl abort" there was a run of CONVERGED micro-steps cut short by a wall-time heuristic -> incomplete."""
    st = r.get("status")
    if st:
        return st
    if r.get("converged"):
        return "completed"
    if str(r.get("reason", "")).startswith("crawl abort"):
        return "incomplete"
    return "nonconvergence"


def _f(v):
    """float or None (NaN / inf / None -> None) -- NaN never travels into a report as a number (NL-20)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def column_Pn(section, L_in, Fy=50.0, K=1.0):
    """AISC 360-22 E3 nominal compressive strength (flexural buckling about the weaker axis, K = 1, Lc = member
    length; slender-element reduction of E7 not applied -- W columns in seismic frames are non-slender)."""
    p = SDB.props(section); r = min(p["rx"], p["ry"]); KLr = K * L_in / r
    Fe = math.pi ** 2 * E_KSI / KLr ** 2
    Fcr = (0.658 ** (Fy / Fe)) * Fy if KLr <= 4.71 * math.sqrt(E_KSI / Fy) else 0.877 * Fe
    return Fcr * p["A"], KLr


def column_Mn(section, Lb_in, Fy=50.0, Cb=1.0):
    """AISC 360-22 nominal flexural strengths of a doubly symmetric I-shape column: major axis F2 (yielding, LTB with
    Lb = member length and Cb = 1.0, conservative) and F3 (flange local buckling, noncompact flanges); minor axis F6
    (min(Fy Zy, 1.6 Fy Sy), F6-2 for noncompact flanges). Other shapes: plastic moment with a flag."""
    p = SDB.props(section); E = E_KSI; notes = []
    Mpx = Fy * p["Zx"]; Mpy = min(Fy * p["Zy"], 1.6 * Fy * p["Sy"])
    if str(p.get("Type", "W")).upper() not in ("W", "M", "HP", "S") or not p.get("rts") or not p.get("ho"):
        notes.append("%s: F2/F3/F6 I-shape provisions not applicable; Mn taken as the plastic moment (LTB / local buckling not evaluated)" % section)
        return Mpx, Mpy, notes
    ry, rts, ho, Sx, J = p["ry"], p["rts"], p["ho"], p["Sx"], p["J"]
    Lp = 1.76 * ry * math.sqrt(E / Fy)
    jc = J / (Sx * ho)
    Lr = 1.95 * rts * E / (0.7 * Fy) * math.sqrt(jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * Fy / E) ** 2))
    if Lb_in <= Lp:
        Mnx = Mpx
    elif Lb_in <= Lr:
        Mnx = min(Mpx, Cb * (Mpx - (Mpx - 0.7 * Fy * Sx) * (Lb_in - Lp) / (Lr - Lp)))
    else:
        Fcr = Cb * math.pi ** 2 * E / (Lb_in / rts) ** 2 * math.sqrt(1 + 0.078 * jc * (Lb_in / rts) ** 2)
        Mnx = min(Mpx, Fcr * Sx)
    lam = p.get("bf_2tf") or p["bf"] / (2 * p["tf"]); lpf = 0.38 * math.sqrt(E / Fy); lrf = 1.0 * math.sqrt(E / Fy)
    if lam > lpf:                                                   # F3-1 / F6-2 noncompact flanges
        f = min(1.0, (lam - lpf) / (lrf - lpf))
        Mnx = min(Mnx, Mpx - (Mpx - 0.7 * Fy * Sx) * f)
        Mpy = min(Mpy, Fy * p["Zy"] - (Fy * p["Zy"] - 0.7 * Fy * p["Sy"]) * f)
        if lam > lrf:
            notes.append("%s: slender flanges (bf/2tf %.1f) -- F3-2 / F6-3 not evaluated" % (section, lam))
    return Mnx, Mpy, notes


def _material(prm):
    mt = (prm or {}).get("material") or {}
    Fy = float(mt.get("Fy_ksi", 50.0)); Ry = float(mt.get("Ry_expected", 1.1))
    return Fy, Ry


def _fc_column_check(Q, Qns, cap, flex, frac_D, SMS, Ie, gamma):
    """16.4.2.1 for one column with concurrent flexure (NL-18).

    Q   : dict(Pc, Pt, Mmaj, Mmin) -- mean (suite) or record peak demands; Pc compression +, Pt most tensile (signed)
    Qns : dict(P, Mmaj, Mmin) gravity-only demands of the same model (16.3.2 gravity state)
    cap : dict(phiPn, phiTn, phiMnx, phiMny, MCEx, MCEy, Pye)
    flex: dict(major/minor = "force" | "deformation", modelled=dict(major/minor = "hinge" | "elastic" | "fibre"))
    Eq. (16.4-1): (1.2 + 0.12 SMS) D + 0.5 L + 1.3 Ie (Qu - Qns)  [compression]
    Eq. (16.4-2): (0.9 - 0.12 SMS) D + 1.3 Ie (Qu - Qns)           [counteracting gravity: tension / uplift]
    (cited by content: in the converted corpus these numbers collide with the drift equation 16.4-1.)
    Flexure classification per AISC 342-22 C3.4 (ASCE 41-23 7.5): deformation-controlled for P_G/P_ye <= 0.6, else
    force-controlled. Force-controlled flexure is transformed the same way as the axial force and resisted by phi Mn
    (AISC 360 F2/F3/F6). Deformation-controlled flexure enters with the analysed moment itself and the expected
    strength M_CE, per AISC 342-22 C3.4b.2.b (Eqs. C3-9..C3-11 with m = 1). Interaction per AISC 360-22 H1-1
    (identical to AISC 342-22 C3-9 with C3-12 / C3-13); C3-10 (|P_UF| / Pye <= 0.75) where flexure is
    deformation-controlled. A deformation-controlled axis MODELLED elastic (e.g. the minor axis of concentrated-hinge
    columns) whose demand exceeds M_CE is flagged: yielding the model cannot represent (16.3.1)."""
    k1 = 1.2 + 0.12 * SMS; k2 = 0.9 - 0.12 * SMS
    def split(qns):
        D = qns * frac_D
        return D, qns - D                                         # (D, 0.5 L) parts of the gravity demand
    Pns = Qns.get("P", 0.0) or 0.0
    D, hL = split(Pns)
    Pc_u = Q.get("Pc"); Pt_u = Q.get("Pt")
    Pr1 = k1 * D + hL + gamma * Ie * max((Pc_u if Pc_u is not None else Pns) - Pns, 0.0)
    Pr2 = k2 * D - gamma * Ie * max(Pns - (Pt_u if Pt_u is not None else Pns), 0.0)     # compression +; < 0 = net tension
    def mdem(axis, eq):
        Mu = Q.get(axis); Mns = abs(Qns.get(axis) or 0.0)
        if Mu is None:
            return None
        if flex[{"Mmaj": "major", "Mmin": "minor"}[axis]] == "deformation":
            return Mu                                              # deformation-controlled: analysed moment, not amplified
        Dm, hLm = split(Mns)
        return (k1 * Dm + hLm if eq == 1 else k2 * Dm) + gamma * Ie * max(Mu - Mns, 0.0)
    def mcap(axis):
        if flex[{"Mmaj": "major", "Mmin": "minor"}[axis]] == "deformation":
            return cap["MCEx"] if axis == "Mmaj" else cap["MCEy"]
        return cap["phiMnx"] if axis == "Mmaj" else cap["phiMny"]
    def h1(Pr, Pc, mx, my):
        if mx is None or my is None:
            return None
        m = mx / mcap("Mmaj") + my / mcap("Mmin")
        r = Pr / Pc if Pc else float("inf")
        return r + 8.0 / 9.0 * m if r >= 0.2 else r / 2.0 + m
    mx1, my1 = mdem("Mmaj", 1), mdem("Mmin", 1); mx2, my2 = mdem("Mmaj", 2), mdem("Mmin", 2)
    flags = []
    mod = flex.get("modelled") or {}
    for axis, key, ce in (("major", "Mmaj", "MCEx"), ("minor", "Mmin", "MCEy")):
        if flex[axis] == "deformation" and mod.get(axis) == "elastic" and Q.get(key) is not None and Q[key] > cap[ce]:
            flags.append("%s-axis moment %.0f k-in > M_CE %.0f k-in but that axis is modelled ELASTIC: yielding not represented (16.3.1) -- model a %s-axis hinge or the column is NG"
                         % (axis, Q[key], cap[ce], axis))
    dc_axial = Pr1 / cap["phiPn"]
    dc_c = h1(max(Pr1, 0.0), cap["phiPn"], mx1, my1)
    dc_t = h1(-Pr2, cap["phiTn"], mx2, my2) if Pr2 < 0 else None
    dc_310 = (max(Pr1, 0.0) / (0.75 * cap["Pye"])) if (flex["major"] == "deformation" or flex["minor"] == "deformation") else None
    cands = [(dc_c, "H1-1 compression, Eq. (1.2+0.12SMS)D+0.5L+1.3Ie(Qu-Qns)"), (dc_t, "H1-1 tension, Eq. (0.9-0.12SMS)D+1.3Ie(Qu-Qns)"),
             (dc_310, "AISC 342 C3-10 |P|/Pye <= 0.75")]
    cands = [c for c in cands if c[0] is not None]
    if cands:
        DC, gov = max(cands, key=lambda c: c[0])
    else:
        DC, gov = dc_axial, "axial only (column moments not recorded by this run)"
    return dict(demand=Pr1, demand_tension=(-Pr2 if Pr2 < 0 else 0.0), Mr_maj=mx1, Mr_min=my1, DC=DC, DC_axial=dc_axial, DC_H1_comp=dc_c,
                DC_H1_tens=dc_t, DC_C3_10=dc_310, governing=gov, moments_recorded=bool(cands) and mx1 is not None, flags=flags)


def _record_column_dc(ev, Qns, cap, flex, frac_D, SMS, Ie, gamma):
    """One record, one column: the 16.4.2.1 interaction at the most utilised CONCURRENT (P, Mmaj, Mmin) instants
    (compression and tension), and the pure-axial checks at the record's axial peaks. None if the run did not record
    concurrent forces (older build)."""
    if not ev or ev.get("conc_c") is None:
        return None
    cc = ev["conc_c"]; ct = ev.get("conc_t")
    out = []
    c1 = _fc_column_check(dict(Pc=cc[0], Pt=None, Mmaj=cc[1], Mmin=cc[2]), Qns, cap, flex, frac_D, SMS, Ie, gamma)
    out.append(c1["DC_H1_comp"])
    if ct is not None:
        c2 = _fc_column_check(dict(Pc=None, Pt=ct[0], Mmaj=ct[1], Mmin=ct[2]), Qns, cap, flex, frac_D, SMS, Ie, gamma)
        out.append(c2["DC_H1_tens"])
    c3 = _fc_column_check(dict(Pc=ev.get("Pc"), Pt=ev.get("Pt"), Mmaj=0.0, Mmin=0.0), Qns, cap, flex, frac_D, SMS, Ie, gamma)
    out += [c3["DC_axial"], c3["DC_H1_tens"], c3["DC_C3_10"]]
    vals = [v for v in out if v is not None]
    return max(vals) if vals else None


def _column_caps(pkg, c, prm, phi_col, B):
    sec = pkg.schedule.get(c, {}).get("section"); e = next(e for e in pkg.model.elements if e["tag"] == c)
    L = math.dist(pkg.model.nodes[e["n1"]], pkg.model.nodes[e["n2"]])
    Fy, Ry = _material(prm)
    Pn, KLr = column_Pn(sec, L, Fy=Fy)
    Mnx, Mny, notes = column_Mn(sec, L, Fy=Fy)
    Mcx, Mcy, _ = column_Mn(sec, L, Fy=Ry * Fy)                  # expected strengths M_CE (Fye = Ry Fy), AISC 342 C3.3a.2
    A = SDB.props(sec)["A"]
    return sec, e, L, KLr, notes, dict(phiPn=phi_col * B * Pn, phiTn=phi_col * B * Fy * A, phiMnx=phi_col * B * Mnx, phiMny=phi_col * B * Mny,
                                       MCEx=Mcx, MCEy=Mcy, Pye=Ry * Fy * A, Pn=Pn, Fy=Fy)


def _per_record_fc_and_governing(results, per, col_table, grav_split, SMS, Ie, ch16, caps=None):
    """Per-record FC D/C on suite force-controlled columns + ordered governing refine candidates.

    Governing FC refine records = motions behind the suite-worst FC column, ranked by
    per-record D/C on that column. Accepted (converged, not Ch.16-unacceptable) first.
    """
    if not col_table:
        return [], []
    fc = ch16["force_controlled"]
    denom = grav_split["sum_D"] + grav_split["sum_Lexp"]
    frac_D = grav_split["sum_D"] / denom if denom else 1.0
    by_ele = {r["ele"]: r for r in col_table}
    gov_ele = max(col_table, key=lambda r: r["DC"])["ele"]
    per_record_fc = []
    candidates = []
    for i, (r, p) in enumerate(zip(results, per)):
        suite_index = i + 1
        peak = r.get("peak_colN") or {}
        env = r.get("col_env") or {}
        cols_out = []
        worst = None
        for ele, srow in by_ele.items():
            Qu = peak.get(ele)
            if Qu is None:
                continue
            Qu = float(Qu)
            rdc = _record_column_dc(env.get(ele), srow["_Qns"], caps[ele], srow["_flex"], frac_D, SMS, Ie, fc["gamma"]) if (caps and ele in caps) else None
            if rdc is not None:
                DC = rdc
            else:
                Qns = srow["Qns"]
                D = Qns * frac_D
                dem = (1.2 + 0.12 * SMS) * D + (Qns - D) + fc["gamma"] * Ie * max(Qu - Qns, 0.0)
                phiBRn = srow["phiBRn"]
                DC = (dem / phiBRn) if phiBRn else None
            cols_out.append(dict(ele=ele, Qu=Qu, DC=DC))
            if DC is not None:
                worst = DC if worst is None else max(worst, DC)
        gov_dc = next((c["DC"] for c in cols_out if c["ele"] == gov_ele), None)
        gov_qu = peak.get(gov_ele)
        entry = dict(
            suite_index=suite_index,
            record=r.get("record") if r.get("record") is not None else p.get("record"),
            label=r.get("label") or p.get("label"),
            converged=bool(r.get("converged")),
            unacceptable=bool(p.get("unacceptable")),
            columns=cols_out,
            worst_DC=worst,
            governing_ele=gov_ele,
            governing_ele_Qu=(float(gov_qu) if gov_qu is not None else None),
            governing_ele_DC=gov_dc,
        )
        per_record_fc.append(entry)
        if gov_dc is not None:
            candidates.append(dict(
                suite_index=suite_index,
                record=entry["record"],
                label=entry["label"],
                ele=gov_ele,
                DC=float(gov_dc),
                Qu=entry["governing_ele_Qu"],
                unacceptable=entry["unacceptable"],
                converged=entry["converged"],
            ))
    candidates.sort(key=lambda c: (
        0 if (c.get("converged") and not c.get("unacceptable")) else 1,
        -float(c["DC"]),
    ))
    return per_record_fc, candidates


def evaluate(results, pkg, ch16, PG16, grav_split, SMS, Ie=1.0, phi_col=0.9, B=1.0, rc="I_II", prm=None, planned=None, suite_size=None,
             early_abort=None):
    """results: list of run_record outputs. Returns the 16.4 scorecard.

    planned    : the records the suite was meant to run (list of dicts with `record`/`id` and `label`); any not in
                 `results` are listed as not run (early abort) -- never silently dropped (NL-17).
    suite_size : number of motions in the scaled suite (16.2.2 needs >= 11).
    early_abort: dict(basis=..) when the CLI stopped the suite early.
    Verdict status: ACCEPTABLE | NOT ACCEPTABLE | INCOMPLETE. NOT ACCEPTABLE is reported as soon as it is decided
    (more unacceptable records than 16.4.1.1 permits, or a failed criterion over the completed records); otherwise any
    record not completed or not run, a suite below 11 motions or a check that could not be computed gives INCOMPLETE.
    `overall` is True only for ACCEPTABLE."""
    heights = next((r["heights"] for r in results if r.get("heights")), None)
    if heights is None:                                            # no record reached the analysis stage
        from pushover import nonlinear_model as NM
        zs = [z for k, z, m, sl in NM.levels(pkg)]
        heights = [b - a for a, b in zip([0.0] + zs[:-1], zs)]
    hn = sum(heights)
    lim = drift_limits(ch16, hn, heights, rc)
    n_story = len(heights)
    # ---- per-record unacceptable-response screen (16.4.1.1)
    per = []
    for r in results:
        st = record_status(r)
        flags = []
        lower = st == "incomplete"
        has_peaks = "peak_story_drift" in r
        if st == "nonconvergence":
            flags.append("non-convergence (%s)" % r.get("reason"))
        pk = _f(np.max(r["peak_story_drift"])) if (has_peaks and st in ("completed", "incomplete")) else None
        if pk is not None and pk > lim["unacceptable_peak"]:
            flags.append("peak story drift %.2f%% > 150%% of mean limit (%.2f%%)%s" % (100 * pk, 100 * lim["unacceptable_peak"], " -- lower bound, record incomplete" if lower else ""))
        beyond = []
        if st in ("completed", "incomplete") and r.get("peak_def"):
            for t, v in r["peak_def"].items():
                s = r["specs"][t]; meta = r["hinges_meta"][t]
                p, n = r["signed_def"][t]
                if meta["kind"] == "brace":
                    if -n > s.b_c or p > s.b_t: beyond.append(t)
                elif v > s.b_pl:
                    beyond.append(t)
        if beyond:
            flags.append("%d deformation-controlled elements beyond the valid modelling range (b)%s" % (len(beyond), " -- record incomplete" if lower else ""))
        res = r.get("residual_drift")
        per.append(dict(record=r["record"], label=r.get("label"), sf=r.get("sf"), converged=bool(r.get("converged")), status=st,
                        peak_drift=(pk if st == "completed" else None), peak_drift_lower_bound=(pk if lower else None),
                        peak_roof_in=(r.get("peak_roof_in") if st == "completed" else None),
                        residual=(_f(max(res)) if (st == "completed" and res) else None),
                        unacceptable=bool(flags), incomplete=(lower and not flags), flags=flags, reason=r.get("reason"),
                        seconds=r.get("seconds"), steps=r.get("steps", 0), retry=r.get("retry")))
    run_ids = {p["record"] for p in per}
    not_run = []
    for q in (planned or []):
        rid = q.get("record", q.get("id"))
        if rid not in run_ids:
            not_run.append(dict(record=rid, label=q.get("label") or q.get("earthquake") or rid))
    n_unacc = sum(1 for p in per if p["unacceptable"])
    n_incomplete = sum(1 for p in per if p["incomplete"])
    acc_runs = [r for r, p in zip(results, per) if p["status"] == "completed" and not p["unacceptable"]]
    allowed = ch16["unacceptable_response"].get("max_unacceptable", {}).get(rc, ch16["unacceptable_response"]["max_unacceptable_RC_I_II"])
    # ---- mean drift (16.4): mean of all if none unacceptable; else 120% median (>= mean of acceptable) of the acceptable set
    def suite_stat(values):                       # values: array (n_records, ...) from acceptable runs
        v = np.asarray(values, float)
        if n_unacc == 0:
            return v.mean(axis=0)
        return np.maximum(1.2 * np.median(v, axis=0), v.mean(axis=0))
    drifts = np.array([r["peak_story_drift"] for r in acc_runs]) if acc_runs else None          # (n, story, 2)
    mean_drift = suite_stat(drifts) if acc_runs else None
    story_rows = [dict(story=i + 1, h_in=heights[i],
                       mean_X=(_f(mean_drift[i, 0]) if acc_runs else None), mean_Y=(_f(mean_drift[i, 1]) if acc_runs else None),
                       max_X=(_f(drifts[:, i, 0].max()) if acc_runs else None), max_Y=(_f(drifts[:, i, 1].max()) if acc_runs else None),
                       ok=(bool(max(mean_drift[i]) <= lim["mean_limit"]) if acc_runs else None)) for i in range(n_story)]
    resid_ok_runs = [r for r in acc_runs if all(_f(x) is not None for x in r.get("residual_drift", []))]
    resid = np.array([r["residual_drift"] for r in resid_ok_runs]); mean_resid = suite_stat(resid) if len(resid_ok_runs) else None
    tall240 = hn / 12.0 > ch16["residual_drift"]["height_ft"]
    # ---- deformation-controlled elements (16.4.2.2): mean peak deformation by group vs CP and vs b
    # Each element is checked against ITS OWN limits: the members of one (kind, section, level) group can carry
    # different CP / b (columns: Table C3.6 a, b vary with P_G/P_ye; beams: span, Lb, RBS cut), so the element
    # with the largest mean deformation is not necessarily the governing one. The row shows the governing
    # element's demand and limits; Qu_*_max is the largest mean deformation in the group.
    groups = {}
    nan = float("nan")
    for r in acc_runs:
        for t, v in r["peak_def"].items():
            meta = r["hinges_meta"][t]; s = r["specs"][t]
            key = (meta["kind"], meta["section"], round(meta["z"]))
            g = groups.setdefault(key, dict(kind=meta["kind"], section=meta["section"], z_in=round(meta["z"]), n=0, peaks=[], comp=[], tens=[], lims=[]))
            g["peaks"].append(v)
            g["lims"].append([s.CP, s.b_pl] + [(x if x is not None else nan) for x in (getattr(s, "CP_t", None), getattr(s, "b_t", None))])
            if meta["kind"] == "brace":
                p, n = r["signed_def"][t]; g["comp"].append(-n); g["tens"].append(p)

    def _ratio(q, lim):
        return np.where(lim > 0, q / np.where(lim > 0, lim, 1.0), 0.0)
    rows = []
    for g in groups.values():
        nr = len(acc_runs)
        peaks = np.array(g["peaks"]).reshape(nr, -1)
        lims = np.array(g["lims"], dtype=float).reshape(nr, -1, 4)[0]          # per element: CP, b, CP_t, b_t
        CP, b, CP_t, b_t = lims[:, 0], lims[:, 1], lims[:, 2], lims[:, 3]
        if g["kind"] == "brace":
            comp = suite_stat(np.array(g["comp"]).reshape(nr, -1)); tens = suite_stat(np.array(g["tens"]).reshape(nr, -1))
            dcp_c, dcp_t = _ratio(comp, CP), _ratio(tens, CP_t)
            dcv_c, dcv_t = _ratio(comp, b), _ratio(tens, b_t)
            jc, jt = int(np.argmax(dcp_c)), int(np.argmax(dcp_t))
            rows.append(dict(kind="brace", section=g["section"], z_in=g["z_in"], n=peaks.shape[1],
                             Qu_comp_in=float(comp[jc]), Qu_tens_in=float(tens[jt]),
                             Qu_comp_in_max=float(comp.max()), Qu_tens_in_max=float(tens.max()),
                             CP_comp=float(CP[jc]), CP_tens=float(CP_t[jt]), b_comp=float(b[jc]), b_tens=float(b_t[jt]),
                             DC_CP=float(max(dcp_c.max(), dcp_t.max())), DC_valid=float(max(dcv_c.max(), dcv_t.max()))))
        else:
            means = suite_stat(peaks)                   # per element suite statistic (16.4: mean, or 120% median)
            dcp, dcv = _ratio(means, CP), _ratio(means, b)
            j = int(np.argmax(dcp))
            rows.append(dict(kind=g["kind"], section=g["section"], z_in=g["z_in"], n=peaks.shape[1], Qu_rad=float(means[j]),
                             Qu_rad_max=float(means.max()), CP=float(CP[j]), b=float(b[j]),
                             DC_CP=float(dcp.max()), DC_valid=float(dcv.max())))
    rows.sort(key=lambda r: (r["kind"], r["z_in"]))
    # ---- force-controlled columns (16.4.2.1) with concurrent flexure (NL-18)
    fc = ch16["force_controlled"]
    denom = grav_split["sum_D"] + grav_split["sum_Lexp"]
    frac_D = grav_split["sum_D"] / denom if denom else 1.0
    colN = {}
    env_all = {}
    for r in acc_runs:
        for c, v in r["peak_colN"].items():
            colN.setdefault(c, []).append(v)
        for c, ev in (r.get("col_env") or {}).items():
            env_all.setdefault(c, []).append(ev)
    grav0 = {}
    for r in acc_runs:
        for c, gv in (r.get("col_grav") or {}).items():
            grav0.setdefault(c, gv)
    flexmap = {}
    for r in acc_runs:
        for c, fl in (r.get("col_flexure") or {}).items():
            flexmap.setdefault(c, fl)
    col_rows = []; caps = {}; fc_notes = set(); moments_missing = False
    for c, vals in colN.items():
        sec, e, L, KLr, notes, cap = _column_caps(pkg, c, prm, phi_col, B)
        fc_notes.update(notes); caps[c] = cap
        evs = env_all.get(c) or []
        Qns = grav0.get(c) or dict(P=PG16.get(c, 0.0), Mmaj=0.0, Mmin=0.0)      # older runs: elastic-model PG, no moments
        flex = flexmap.get(c) or dict(major="force", minor="force", modelled={})
        mean = lambda key: (_f(suite_stat(np.array([ev[key] for ev in evs if ev.get(key) is not None]))) if any(ev.get(key) is not None for ev in evs) else None)
        Q = dict(Pc=(mean("Pc") if evs else _f(suite_stat(np.array(vals)))), Pt=mean("Pt") if evs else None,
                 Mmaj=mean("Mmaj") if evs else None, Mmin=mean("Mmin") if evs else None)
        chk = _fc_column_check(Q, Qns, cap, flex, frac_D, SMS, Ie, fc["gamma"])          # non-concurrent envelope of mean peaks
        rdcs = [_record_column_dc((r.get("col_env") or {}).get(c), Qns, cap, flex, frac_D, SMS, Ie, fc["gamma"]) for r in acc_runs]
        if rdcs and all(v is not None for v in rdcs):
            dc_conc = _f(suite_stat(np.array(rdcs)))
            env_info = dict(DC_envelope=chk["DC"], governing_envelope=chk["governing"])
            chk = dict(chk, DC=dc_conc, governing="16.4 mean of the per-record interaction at concurrent (P, M) instants (both 16.4.2.1 equations, H1-1); "
                       "non-concurrent envelope of mean peaks: %.2f (%s)" % (env_info["DC_envelope"], env_info["governing_envelope"]), **env_info)
        if not chk["moments_recorded"]:
            moments_missing = True
        col_rows.append(dict(ele=c, section=sec, z_in=pkg.model.nodes[e["n1"]][2], Qu=Q["Pc"], Qu_tension=Q["Pt"], Qns=Qns.get("P", 0.0),
                             Mu_maj=Q["Mmaj"], Mu_min=Q["Mmin"], Mns_maj=Qns.get("Mmaj"), Mns_min=Qns.get("Mmin"),
                             flexure_major=flex["major"], flexure_minor=flex["minor"], modelled=flex.get("modelled") or {}, phiBRn=cap["phiPn"], phiTn=cap["phiTn"],
                             phiMnx=cap["phiMnx"], phiMny=cap["phiMny"], MCEx=cap["MCEx"], MCEy=cap["MCEy"], Fy=cap["Fy"], KLr=KLr,
                             _Qns=Qns, _flex=flex, **chk))
    # group the worst per (section, z)
    best = {}
    for r in col_rows:
        k = (r["section"], round(r["z_in"]))
        if k not in best or r["DC"] > best[k]["DC"]:
            best[k] = r
    col_table = sorted(best.values(), key=lambda r: (r["z_in"], r["section"]))
    per_record_fc, governing_fc_records = _per_record_fc_and_governing(
        results, per, col_table, grav_split, SMS, Ie, ch16, caps=caps)
    for r in col_table:
        r.pop("_Qns", None); r.pop("_flex", None)
    if governing_fc_records:
        primary = next(
            (c for c in governing_fc_records if c.get("converged") and not c.get("unacceptable")),
            governing_fc_records[0],
        )
        # Annotate suite-governing column with the preferred refine suite index.
        gov_row = max(col_table, key=lambda r: r["DC"]) if col_table else None
        if gov_row is not None:
            gov_row["suite_index"] = primary["suite_index"]
            gov_row["governing_record"] = primary.get("record")
            gov_row["governing_record_DC"] = primary.get("DC")
    # ---- verdict
    n_run = len(results)
    n_suite = suite_size if suite_size is not None else (len(planned) if planned else n_run)
    suite_gaps, not_evaluated = [], []
    if n_incomplete:
        suite_gaps.append("%d record(s) incomplete (work budget exhausted; every committed step converged) -- re-run them" % n_incomplete)
    if not_run:
        suite_gaps.append("%d record(s) of the suite not run%s" % (len(not_run), (" (early abort: %s)" % early_abort.get("basis")) if early_abort else ""))
    if n_suite < ch16["n_motions"]["min"]:
        suite_gaps.append("suite of %d motions < %d required by 16.2.2" % (n_suite, ch16["n_motions"]["min"]))
    elif n_run + len(not_run) < n_suite:
        suite_gaps.append("only %d of the %d suite records were selected for this run" % (n_run + len(not_run), n_suite))
    not_evaluated += suite_gaps
    if not acc_runs:
        not_evaluated.append("no completed acceptable record: suite statistics (mean drift, element demands) not computed")
    if moments_missing and col_table:
        not_evaluated.append("column moments not recorded (results from an older build): 16.4.2.1 combined axial + flexure not evaluated -- re-run")
    mean_ok = all(s["ok"] for s in story_rows) if acc_runs else None
    fc_ok = (bool(col_table) and all(r["DC"] <= 1.0 for r in col_table)) if acc_runs else None
    if fc_ok and moments_missing:
        fc_ok = None                              # axial-only pass is not a pass of the combined check (an axial-only failure stays decisive)
    verdict = dict(
        n_records=n_run, n_suite=n_suite, n_not_run=len(not_run), n_incomplete=n_incomplete, n_completed=sum(1 for p in per if p["status"] == "completed"),
        n_nonconvergence=sum(1 for p in per if p["status"] == "nonconvergence"),
        n_unacceptable=n_unacc, unacceptable_allowed=allowed, unacceptable_ok=(n_unacc <= allowed),
        mean_drift_ok=mean_ok, mean_drift_max=(_f(np.nanmax(mean_drift)) if acc_runs else None),
        deformation_ok=(all(r["DC_CP"] <= 1.0 for r in rows) if acc_runs else None), valid_range_ok=(all(r["DC_valid"] <= 1.0 for r in rows) if acc_runs else None),
        force_controlled_ok=fc_ok,
        worst_FC_DC=(max((r["DC"] for r in col_table), default=None)),
        residual_applicable=tall240, residual_ok=(None if not tall240 else (bool(np.max(mean_resid) <= ch16["residual_drift"]["limit"]) if mean_resid is not None else None)))
    crit_fail = [k for k in ("mean_drift_ok", "deformation_ok", "force_controlled_ok") if verdict[k] is False] + (["residual_ok"] if (tall240 and verdict["residual_ok"] is False) else [])
    if not verdict["unacceptable_ok"]:
        status = "NOT ACCEPTABLE"                 # decided: more unacceptable records than 16.4.1.1 permits; the rest cannot change it
    elif crit_fail and not suite_gaps:
        status = "NOT ACCEPTABLE"
    elif not_evaluated:
        status = "INCOMPLETE"
        if crit_fail:
            not_evaluated.append("on the records completed so far these criteria fail: %s" % ", ".join(crit_fail))
    else:
        status = "ACCEPTABLE"
    verdict["status"] = status
    verdict["not_evaluated"] = not_evaluated
    verdict["decided"] = status != "INCOMPLETE"
    verdict["overall"] = status == "ACCEPTABLE"
    return dict(limits=lim, per_record=per, story=story_rows, mean_residual=(mean_resid.tolist() if mean_resid is not None else None),
                deformation_groups=rows, force_controlled_columns=col_table, verdict=verdict, hn_in=hn,
                per_record_fc=per_record_fc, governing_fc_records=governing_fc_records, records_not_run=not_run,
                fc_notes=sorted(fc_notes), drift_method=("aligned_points" if all(r.get("drift_method") == "aligned_points" for r in results if record_status(r) == "completed") else "legacy_corner_nodes"))


def combine_no_live(acc, acc_nl, required):
    """16.3.2: both gravity cases govern. `acc` is the 1.0D + 0.5L evaluation, `acc_nl` the 1.0D one (None if not
    run). Returns acc with acc["no_live_case"] and the verdict updated: NOT ACCEPTABLE if either case fails, INCOMPLETE
    if the no-live case is required but was not run (or is itself incomplete)."""
    v = acc["verdict"]
    info = dict(required=bool(required), run=acc_nl is not None)
    if acc_nl is not None:
        vn = acc_nl["verdict"]
        info.update(verdict=vn, story=acc_nl["story"], per_record=acc_nl["per_record"], force_controlled_columns=acc_nl["force_controlled_columns"],
                    deformation_groups=acc_nl["deformation_groups"], records_not_run=acc_nl.get("records_not_run"))
        for k in ("mean_drift_max", "worst_FC_DC"):
            a, b = v.get(k), vn.get(k)
            v[k + "_with_live"] = a
            v[k] = max([x for x in (a, b) if x is not None], default=None)
        for k in ("unacceptable_ok", "mean_drift_ok", "deformation_ok", "valid_range_ok", "force_controlled_ok"):
            a, b = v.get(k), vn.get(k)
            v[k] = (None if (a is None or b is None) else (a and b)) if not (a is False or b is False) else False
        v["n_unacceptable_no_live"] = vn["n_unacceptable"]
        if vn["status"] == "NOT ACCEPTABLE" or v["status"] == "NOT ACCEPTABLE":
            v["status"] = "NOT ACCEPTABLE"
        elif vn["status"] == "INCOMPLETE" or v["status"] == "INCOMPLETE":
            v["status"] = "INCOMPLETE"
            v["not_evaluated"] = list(v.get("not_evaluated") or []) + ["no-live case: " + "; ".join(vn.get("not_evaluated") or [])]
    elif required:
        v["not_evaluated"] = list(v.get("not_evaluated") or []) + ["16.3.2 analysis without live load (1.0 D) is required (the exception does not apply) but was not run"]
        if v["status"] == "ACCEPTABLE":
            v["status"] = "INCOMPLETE"
    v["decided"] = v["status"] != "INCOMPLETE"
    v["overall"] = v["status"] == "ACCEPTABLE"
    v["no_live_case"] = dict(required=info["required"], run=info["run"], status=(info.get("verdict") or {}).get("status"))
    acc["no_live_case"] = info
    return acc
