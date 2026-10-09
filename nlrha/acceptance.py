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


def drift_limits(ch16, hn_in, H_story_in, rc="I_II", systems=None):
    """16.4.1.2: mean limit = 2 x Table 12.12-1 ('all other structures' row for the Risk Category); for hn > 100 ft also the
    tall-building cap hsx(4.71e-2 - 7.14e-5 hn) >= 0.03 hn (as a ratio: 0.0471 - 7.14e-5*hn_ft, floor 0.03). Ratio limits.
    systems (NL-R2-17): {"X": system, "Y": system} of a mixed-system building -> the limits are also given per direction
    (`by_dir`), each from the Table 12.12-1 row of that direction's system. 16.4.1.2 sends masonry shear-wall systems to
    the 'other structures' row too, so every system in this tool's scope takes the 'all other structures' row."""
    d = ch16["transient_drift"]
    tab = d.get("table_12_12_1_all_other", {}).get(rc, d["table_12_12_1_all_other_RC_I_II"])
    lim = d["factor_on_table_12_12_1"] * tab
    hn_ft = hn_in / 12.0
    tall = None
    if hn_ft > d["tall_height_ft"]:
        tall = max(d["tall_a"] - d["tall_b"] * hn_ft, d["tall_floor"])
        lim = min(lim, tall)
    out = dict(mean_limit=lim, tall_limit=tall, table_12_12_1=tab, risk_category=rc,
               unacceptable_peak=ch16["unacceptable_response"]["peak_drift_factor_of_mean_limit"] * lim)
    if systems:
        out["by_dir"] = {d: dict(system=sy, table_row="all other structures", table_12_12_1=tab, mean_limit=lim,
                                 unacceptable_peak=out["unacceptable_peak"]) for d, sy in systems.items()}
    return out


def _dir_limit(lim, j, key="mean_limit"):
    """NL-R2-17: the limit for direction index j (0 = X, 1 = Y) -- per direction for a mixed-system building."""
    bd = lim.get("by_dir") or {}
    return (bd.get("XY"[j]) or {}).get(key, lim[key])


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


def column_Pn(section, L_in, Fy=50.0, K=1.0, Lcx=None, Lcy=None):
    """AISC 360-22 E3 nominal compressive strength, flexural buckling about the governing axis: Lc/r = max(Lcx/rx,
    Lcy/ry) with the per-axis effective lengths Lc = KL of E2 when given (NL-R2-25: the HR design's bracing, see
    design_column_lengths), else K x member length about the weaker axis. Slender-element reduction of E7 not
    applied -- W columns in seismic frames are non-slender."""
    p = SDB.props(section)
    if Lcx or Lcy:
        KLr = max((Lcx or K * L_in) / p["rx"], (Lcy or K * L_in) / p["ry"])
    else:
        r = min(p["rx"], p["ry"]); KLr = K * L_in / r
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


def _len(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 and math.isfinite(x) else None


def design_column_lengths(pkg, section, z1, z2, L):
    """NL-R2-25: the unbraced lengths the HR design used for this column, per axis (AISC 360-22 E2: Lc = K L for
    buckling about each axis; F2: Lb between points braced against lateral displacement of the compression flange or
    twist of the cross section). Source: design/calc_package.json members[*] with a column role/kind and this section:
      * inputs.brace_points_in (elevations of the column's brace points, e.g. girts / inner-flange braces / crane
        level): the element's span [z1, z2] is cut at the points inside it -> Lcy = Lb = the longest piece, Lcx =
        inputs.Lcx_in or K L (strong axis spans the full member, frame-plane stability by the DAM, K = 1);
      * inputs.Lcx_in / Lcy_in / Lb_in (per-axis lengths), used when the member's length_in equals this element's
        length (a group record of a different length is not this element's bracing).
    Several matching records (e.g. lateral and gravity groups of one section) -> the longest lengths (conservative).
    None when the package gives nothing for this column: the caller keeps K = 1 and the member length about both axes
    and Lb = member length (the previous, conservative default)."""
    calc = getattr(pkg, "calc", None) or {}
    best = None
    sec_n = str(section or "").strip().upper().replace(" ", "")
    for m in calc.get("members") or []:
        inp = (m or {}).get("inputs") or {}
        role = str(inp.get("role") or "") + " " + str(inp.get("kind") or "")
        if "col" not in role.lower() or str(inp.get("section") or "").strip().upper().replace(" ", "") != sec_n:
            continue
        K = _len(inp.get("K")) or 1.0
        Lcx = _len(inp.get("Lcx_in")); Lcy = _len(inp.get("Lcy_in")) or _len(inp.get("Lc_weak_in")); Lb = _len(inp.get("Lb_in"))
        pts = inp.get("brace_points_in")
        src = "calc_package members[%s].inputs" % m.get("id")
        if isinstance(pts, (list, tuple)) and len(pts) >= 2 and all(_len(x) is not None or x in (0, 0.0) for x in pts):
            pts = sorted(float(x) for x in pts)
            if not (pts[0] - 1.0 <= min(z1, z2) and max(z1, z2) <= pts[-1] + 1.0):
                continue
            lo, hi = min(z1, z2), max(z1, z2)
            cuts = [lo] + [x for x in pts if lo + 1e-6 < x < hi - 1e-6] + [hi]
            seg = max(b - a for a, b in zip(cuts[:-1], cuts[1:]))
            rec = dict(Lcx=Lcx or K * L, Lcy=seg, Lb=seg, K=K, source="%s.brace_points_in %s (element %.0f-%.0f in)" % (
                src, "/".join("%.0f" % x for x in pts), lo, hi))
        elif Lcx or Lcy or Lb:
            Lm = _len(inp.get("length_in"))
            if Lm is None or abs(Lm - L) > 1.0:
                continue
            rec = dict(Lcx=Lcx or K * L, Lcy=Lcy or K * L, Lb=Lb or Lcy or L, K=K,
                       source="%s.%s" % (src, "/".join(k for k in ("Lcx_in", "Lcy_in", "Lb_in") if _len(inp.get(k)))))
        else:
            continue
        if best is None or (rec["Lcy"], rec["Lb"], rec["Lcx"]) > (best["Lcy"], best["Lb"], best["Lcx"]):
            best = rec
    return best


def _column_caps(pkg, c, prm, phi_col, B):
    sec = pkg.schedule.get(c, {}).get("section"); e = next(e for e in pkg.model.elements if e["tag"] == c)
    p1, p2 = pkg.model.nodes[e["n1"]], pkg.model.nodes[e["n2"]]
    L = math.dist(p1, p2)
    Fy, Ry = _material(prm)
    dl = design_column_lengths(pkg, sec, p1[2], p2[2], L)          # NL-R2-25: the design's per-axis bracing
    if dl:
        Pn, KLr = column_Pn(sec, L, Fy=Fy, Lcx=dl["Lcx"], Lcy=dl["Lcy"])
        Lb = dl["Lb"]
    else:
        Pn, KLr = column_Pn(sec, L, Fy=Fy)
        Lb = L
    Mnx, Mny, notes = column_Mn(sec, Lb, Fy=Fy)
    Mcx, Mcy, _ = column_Mn(sec, Lb, Fy=Ry * Fy)                 # expected strengths M_CE (Fye = Ry Fy), AISC 342 C3.3a.2
    notes = list(notes)
    if dl:
        notes.append("%s: Lcx %.0f / Lcy %.0f / Lb %.0f in from the HR design (%s)" % (sec, dl["Lcx"], dl["Lcy"], dl["Lb"], dl["source"]))
    A = SDB.props(sec)["A"]
    return sec, e, L, KLr, notes, dict(phiPn=phi_col * B * Pn, phiTn=phi_col * B * Fy * A, phiMnx=phi_col * B * Mnx, phiMny=phi_col * B * Mny,
                                       MCEx=Mcx, MCEy=Mcy, Pye=Ry * Fy * A, Pn=Pn, Fy=Fy,
                                       Lcx=(dl or {}).get("Lcx", L), Lcy=(dl or {}).get("Lcy", L), Lb=Lb,
                                       length_source=(dl or {}).get("source", "member length, K = 1 (no per-axis bracing in the package)"))


# --------------------------------------------------------------------------- NL-R2-16: ASCE 7-22 16.4.2.1 Exception 2
# Exception 2 of 16.4.2.1: a force-controlled action limited by the formation of a yield mechanism (other than shear
# in structural walls) need only satisfy Eqs. (16.4-3) (1.2 + 0.12 SMS) D + 0.5 L + 0.2 S + Emc <= phi B Rn and
# (16.4-4) (0.9 - 0.12 SMS) D + Emc <= phi B Rn -- load factor 1.0 on Emc, the capacity-limited earthquake effect of
# the yielding components developing their plastic capacity (material standard, or rational analysis with expected
# properties and strain hardening). phi and B are those of 16.4.2.1 (unchanged). Implemented for COLUMN AXIAL FORCE:
# Emc = statics of the column line above the column with every yielding component that frames into it at its
# capacity in the model (beam hinges at their capping moment Mc = (Mc/My) Mpe, Fye = Ry Fy; braces at h x Pye in
# tension and Pcre / 0.3 Pcre in compression = AISC 341-22 F2.3 analyses (a) / (b); BRBs at omega Qce / beta omega
# Qce), one sway direction per analysis, X, Y and both together (AISC 341-22 D1.4a / F2.3, columns common to
# intersecting frames). Applied moments are neglected in this axial check as AISC 341-22 D1.4a(b) permits (no
# member loads act on the columns between lateral supports in this model); the column's flexural hinging stays a
# deformation-controlled action (16.4.2.2).
_SWAYS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _released_major(pkg, e, slot="Iy"):
    """(i_released, j_released) for the strong axis, the same reading nonlinear_model uses (beams: -releasey)."""
    rel = e.get("release") or []
    flag = "-releasey" if slot == "Iy" else "-releasez"
    code = int(rel[rel.index(flag) + 1]) if flag in rel else 0
    return code in (1, 3), code in (2, 3)


def mechanism_axial_bounds(pkg, hinges_meta, specs, prm, plasticity=None):
    """NL-R2-16 / 16.4.2.1 Exception 2: capacity-limited seismic axial force Emc of every column.

    Returns {col_tag: dict(qualifies, reason, Emc_c, Emc_t, n_fuses, governing_c, governing_t)}: Emc_c / Emc_t =
    largest compression / tension (kip, seismic part only) delivered to the column by the components framing into
    its column line at and above its top, every component at its capacity in the model. A column does NOT qualify
    (default 16.4-1/16.4-2 check stays) when anything delivering vertical force to that line is not a yielding
    component with a bounded backbone (elastic moment-connected beam end, elastic brace, unknown element, vertical
    equalDOF), when no yielding component delivers seismic force at all (e.g. gravity columns), when the column is
    inclined. Fibre plasticity: the beam capacity is still taken as (Mc/My) Mpe of the AISC 342 backbone; the fibres
    harden without a cap (1 %), so evaluate() never lets Emc fall below the analysed suite maximum."""
    from pushover import nonlinear_model as NM
    nodes = pkg.model.nodes
    beam_end, brace, links = {}, {}, set()
    for t, m in (hinges_meta or {}).items():
        k = m.get("kind")
        if k == "beam":
            beam_end[(m["ele"], m.get("end"))] = (specs[t], float(m.get("rbs_offset_in") or 0.0))
        elif k == "brace":
            brace[m["ele"]] = specs[t]
        elif k == "link":
            links.add(m["ele"])
    bp = (prm or {}).get("beam_flexure") or {}
    old_meta = not any("rbs_offset_in" in m for m in (hinges_meta or {}).values())
    if old_meta and (plasticity == "fibre" or int(bp.get("rbs_segments") or 0) == 7):
        # results recorded before the hinge offset was kept: RBS-centre hinges (fibre RBS / 7-segment remesh) sit at
        # a + b/2 from the joint -> the same package geometry the model builder used (NM.beam_params_for)
        for kk, (s, off) in list(beam_end.items()):
            try:
                geo = NM.beam_params_for(pkg, prm, s.section, fr=True)[1].get("rbs")
            except Exception:                                       # noqa: BLE001
                geo = None
            if geo:
                beam_end[kk] = (s, geo["a_in"] + 0.5 * geo["b_in"])
    h_t = float((((prm or {}).get("brace_axial") or {}).get("tension") or {}).get("hardening_ratio", 1.0) or 1.0)
    kind = {e["tag"]: NM.member_kind(pkg, e) for e in pkg.model.elements}
    at_node = {}
    for e in pkg.model.elements:
        at_node.setdefault(e["n1"], []).append(e); at_node.setdefault(e["n2"], []).append(e)
    vert_tied = set()
    for a in (getattr(pkg.model, "equal_dofs", None) or []):          # equalDOF(master, slave, dofs...) tying vertical DOF 3
        if len(a) > 2 and 3 in [int(x) for x in a[2:] if str(x).lstrip("-").isdigit()]:
            vert_tied.update((int(a[0]), int(a[1])))
    cols = [e for e in pkg.model.elements if "etype" not in e and kind[e["tag"]] == "col"]
    key = lambda n: (round(nodes[n][0], 1), round(nodes[n][1], 1))
    stack_nodes = {}
    for e in cols:
        if key(e["n1"]) == key(e["n2"]):
            stack_nodes.setdefault(key(e["n1"]), set()).update((e["n1"], e["n2"]))

    def contributions(n, line, analysis):
        """[(component tag, f(d) -> downward force on node n under sway d)] for one node; raises ValueError with the
        reason when a component delivering vertical force there is not a bounded yielding component."""
        out = []
        pn = nodes[n]
        for e in at_node.get(n, []):
            t = e["tag"]; m = e["n2"] if e["n1"] == n else e["n1"]; pm = nodes[m]
            k = kind[t]
            if k == "col" and "etype" not in e:
                if m in line and key(m) == key(n):
                    continue                                         # the column line itself
                raise ValueError("inclined or offset column %s frames into the column line" % t)
            L = math.dist(pn, pm)
            u = ((pm[0] - pn[0]) / L, (pm[1] - pn[1]) / L)
            if k == "beam" and "etype" not in e:
                caps = []
                ri, rj = _released_major(pkg, e)
                for end, rel in ((1, ri), (2, rj)):
                    h = beam_end.get((t, end))
                    if h is not None:
                        s, off = h
                        caps.append((s.Mc_over_My * s.Mpe_kipin, off))
                    elif rel:
                        caps.append((0.0, 0.0))
                    else:
                        raise ValueError("beam %s end %d is moment-connected but has no yielding hinge (elastic or force-controlled)" % (t, end))
                Mtot = caps[0][0] + caps[1][0]
                if Mtot <= 0.0:
                    continue                                         # pinned both ends: gravity shear only (in Qns)
                V = Mtot / max(L - caps[0][1] - caps[1][1], 1e-6)   # plastic shear of the beam (hinges at the RBS centres)
                out.append((t, (lambda d, V=V, u=u: -V * float(np.sign(u[0] * d[0] + u[1] * d[1])))))
            elif k == "brace":
                s = brace.get(t)
                if s is None:
                    raise ValueError("brace %s has no nonlinear axial model (elastic brace)" % t)
                bot, top = (n, m) if pn[2] < pm[2] else (m, n)
                p_bt = (nodes[top][0] - nodes[bot][0], nodes[top][1] - nodes[bot][1])
                if hasattr(s, "omega"):                              # BRB: AISC 342 C3.3 adjusted strengths
                    Nt = s.omega * s.Pye_kip; Nc = s.beta * s.omega * s.Pye_kip
                else:                                                # AISC 341-22 F2.3 (a) expected / (b) post-buckling
                    Nt = h_t * s.Pye_kip; Nc = s.Pcre_kip * (0.3 if analysis == "b" else 1.0)
                dz = (pn[2] - pm[2]) / L
                out.append((t, (lambda d, Nt=Nt, Nc=Nc, p=p_bt, dz=dz:
                                (Nt if (p[0] * d[0] + p[1] * d[1]) > 1e-9 else (-Nc if (p[0] * d[0] + p[1] * d[1]) < -1e-9 else 0.0)) * dz)))
            else:
                raise ValueError("element %s (%s) frames into the column line and is not a modelled yielding component" % (t, e.get("etype") or k))
        return out

    res = {}
    for c in cols:
        t = c["tag"]
        if key(c["n1"]) != key(c["n2"]):
            res[t] = dict(qualifies=False, reason="inclined column: no vertical column line to take the mechanism statics on"); continue
        line = stack_nodes[key(c["n1"])]
        z_top = max(nodes[c["n1"]][2], nodes[c["n2"]][2])
        above = sorted(n for n in line if nodes[n][2] >= z_top - 1e-6)
        try:
            if any(n in vert_tied for n in above):
                raise ValueError("a node of the column line carries a vertical equalDOF")
            best_c = (0.0, None); best_t = (0.0, None); fuses = set()
            for analysis in (("a", "b") if brace else ("a",)):
                comps = [cc for n in above for cc in contributions(n, line, analysis)]
                fuses.update(tg for tg, _ in comps)
                for d in _SWAYS:
                    F = sum(f(d) for _, f in comps)
                    lab = "sway %s%s%s" % ("+X" if d[0] > 0 else ("-X" if d[0] < 0 else ""), "+Y" if d[1] > 0 else ("-Y" if d[1] < 0 else ""),
                                           (", F2.3 analysis (%s)" % analysis) if brace else "")
                    if F > best_c[0]:
                        best_c = (F, lab)
                    if -F > best_t[0]:
                        best_t = (-F, lab)
        except ValueError as ex:
            res[t] = dict(qualifies=False, reason=str(ex)); continue
        if not fuses:
            res[t] = dict(qualifies=False, reason="no yielding component delivers seismic axial force to this column line: the action is not limited by a yield mechanism")
            continue
        res[t] = dict(qualifies=True, reason=None, Emc_c=best_c[0], Emc_t=best_t[0], governing_c=best_c[1], governing_t=best_t[1],
                      n_fuses=len(fuses), links=bool(links & fuses))
    return res, None


def _exc2_column_check(Emc_c, Emc_t, Qns, cap, frac_D, SMS, S_kip=0.0):
    """16.4.2.1 Exception 2 for a column axial force limited by a yield mechanism (NL-R2-16):
    Eq. (16.4-3): (1.2 + 0.12 SMS) D + 0.5 L + 0.2 S + Emc <= phi B Rn   (compression, Rn = Pn of AISC 360 E3)
    Eq. (16.4-4): (0.9 - 0.12 SMS) D + Emc <= phi B Rn                   (Emc counteracting gravity: net tension vs
                                                                          phi B Fy Ag, AISC 360 D2)
    load factor 1.0 on Emc; phi, B as in 16.4.2.1. D and 0.5 L split from the model's gravity state Qns as in the
    default check. AISC 342-22 C3-10 (|P| / Pye <= 0.75) is kept on the Exception-2 compression (conservative)."""
    k1 = 1.2 + 0.12 * SMS; k2 = 0.9 - 0.12 * SMS
    Pns = Qns.get("P", 0.0) or 0.0
    D = Pns * frac_D; hL = Pns - D
    Pr3 = k1 * D + hL + 0.2 * (S_kip or 0.0) + Emc_c
    Pr4 = k2 * D - Emc_t                                            # compression +; < 0 = net tension
    dc3 = Pr3 / cap["phiPn"]
    dc4 = (-Pr4 / cap["phiTn"]) if Pr4 < 0 else None
    dc310 = max(Pr3, 0.0) / (0.75 * cap["Pye"])
    cands = [(dc3, "16.4.2.1 Exception 2, Eq. (16.4-3) (1.2+0.12SMS)D+0.5L+0.2S+Emc <= phi B Pn"),
             (dc4, "16.4.2.1 Exception 2, Eq. (16.4-4) (0.9-0.12SMS)D+Emc (net tension) <= phi B Tn"),
             (dc310, "AISC 342 C3-10 |P|/Pye <= 0.75 on the Eq. (16.4-3) compression")]
    DC, gov = max([c for c in cands if c[0] is not None], key=lambda c: c[0])
    return dict(Pr_16_4_3=Pr3, Tr_16_4_4=(-Pr4 if Pr4 < 0 else 0.0), DC_16_4_3=dc3, DC_16_4_4=dc4, DC_C3_10=dc310, DC=DC, governing=gov,
                Emc_c=Emc_c, Emc_t=Emc_t, S_kip=S_kip or 0.0)


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
             early_abort=None, snow_col=None):
    """results: list of run_record outputs. Returns the 16.4 scorecard.

    planned    : the records the suite was meant to run (list of dicts with `record`/`id` and `label`); any not in
                 `results` are listed as not run (early abort) -- never silently dropped (NL-17).
    suite_size : number of motions in the scaled suite (16.2.2 needs >= 11).
    early_abort: dict(basis=..) when the CLI stopped the suite early.
    snow_col   : {column tag: roof snow axial S, kip} for Eq. (16.4-3) (NL-R2-16); None -> from cfg.py (gravity.column_snow_axial).
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
    by_dir = getattr(getattr(pkg, "basis", None), "by_dir", None) or {}          # NL-R2-17: mixed systems (12.2.2)
    lim = drift_limits(ch16, hn, heights, rc, systems=({d: v.get("system") for d, v in by_dir.items()} if by_dir else None))
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
        if pk is not None and not lim.get("by_dir") and pk > lim["unacceptable_peak"]:
            flags.append("peak story drift %.2f%% > 150%% of mean limit (%.2f%%)%s" % (100 * pk, 100 * lim["unacceptable_peak"], " -- lower bound, record incomplete" if lower else ""))
        elif pk is not None and lim.get("by_dir"):                  # NL-R2-17: each direction against its own limit
            pkd = np.asarray(r["peak_story_drift"], float).reshape(len(r["peak_story_drift"]), -1)
            for j in range(min(2, pkd.shape[1])):
                pj = _f(np.max(pkd[:, j])); uj = _dir_limit(lim, j, "unacceptable_peak")
                if pj is not None and pj > uj:
                    flags.append("peak story drift %.2f%% (%s, %s) > 150%% of mean limit (%.2f%%)%s" % (100 * pj, "XY"[j], lim["by_dir"]["XY"[j]].get("system"), 100 * uj,
                                                                                               " -- lower bound, record incomplete" if lower else ""))
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
                       ok=((bool(all(mean_drift[i][j] <= _dir_limit(lim, j) for j in range(len(mean_drift[i])))) if lim.get("by_dir") else
                            bool(max(mean_drift[i]) <= lim["mean_limit"])) if acc_runs else None)) for i in range(n_story)]
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
    # NL-R2-16: ASCE 7-22 16.4.2.1 Exception 2 for column axial force limited by a yield mechanism (user decision 7 Oct;
    # ch16_params force_controlled.exception_2.apply). Default check (16.4-1/16.4-2 with H1-1) reported beside it.
    ex2_cfg = fc.get("exception_2") or {}
    ex2_on = bool(ex2_cfg.get("apply", True))
    ex2, ex2_note, snow_basis = {}, None, None
    if ex2_on and acc_runs:
        r0 = next((r for r in acc_runs if r.get("hinges_meta") is not None), acc_runs[0])
        try:
            ex2, ex2_note = mechanism_axial_bounds(pkg, r0.get("hinges_meta") or {}, r0.get("specs") or {}, prm, plasticity=(r0.get("stats") or {}).get("plasticity"))
        except Exception as ex:                                     # noqa: BLE001 -- never lose the default check
            ex2, ex2_note = {}, "Exception 2 not evaluated (%s: %s)" % (type(ex).__name__, ex)
        if snow_col is None:
            try:
                from . import gravity as GR
                snow_col, snow_basis = GR.column_snow_axial(pkg)
            except Exception as ex:                                 # noqa: BLE001
                snow_col, snow_basis = {}, "S not computed (%s) -- taken as 0" % ex
        else:
            snow_basis = "S per column supplied by the caller"
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
        chk = dict(chk, DC_default=chk["DC"], governing_default=chk["governing"], fc_basis="16.4.2.1 Eqs. (16.4-1)/(16.4-2) with H1-1")
        if ex2_on:
            eb = ex2.get(c) or dict(qualifies=False, reason=ex2_note or "no mechanism bound for this column")
            info = dict(qualifies=bool(eb.get("qualifies")), reason=eb.get("reason"))
            if info["qualifies"] and (flex.get("major") != "deformation" or flex.get("minor") != "deformation"):
                info.update(qualifies=False, reason="flexure is force-controlled (P_G/P_ye > 0.6, AISC 342 C3.4): the moment is not a yielding-component action, the H1-1 interaction stays")
            if info["qualifies"] and chk.get("flags"):
                info.update(qualifies=False, reason="a deformation-controlled axis modelled elastic exceeds M_CE: the yield mechanism is not represented by the model (16.3.1)")
            if info["qualifies"]:
                pns = Qns.get("P", 0.0) or 0.0
                pc_max = max([ev["Pc"] for ev in evs if ev.get("Pc") is not None] or [max(vals)])
                pt_min = min([ev["Pt"] for ev in evs if ev.get("Pt") is not None] or [pns])
                a_c, a_t = max(pc_max - pns, 0.0), max(pns - pt_min, 0.0)
                # Emc: the mechanism statics with the model capacities, never below what the analysis delivered (suite max
                # of the record peaks: dynamic effects, fibre hardening beyond Mc/My) -- conservative.
                info.update(Emc_mech_c=eb["Emc_c"], Emc_mech_t=eb["Emc_t"], governing_c=eb.get("governing_c"), governing_t=eb.get("governing_t"), n_fuses=eb.get("n_fuses"),
                            analysed_max_c=a_c, analysed_max_t=a_t, Emc_c=max(eb["Emc_c"], a_c), Emc_t=max(eb["Emc_t"], a_t),
                            Emc_basis=("mechanism statics" if (a_c <= eb["Emc_c"] + 1e-6 and a_t <= eb["Emc_t"] + 1e-6) else
                                       "analysed suite maximum (above the mechanism statics %.0f / %.0f kip: dynamic effects or hardening beyond Mc/My)" % (eb["Emc_c"], eb["Emc_t"])))
            if info["qualifies"]:
                x = _exc2_column_check(info["Emc_c"], info["Emc_t"], Qns, cap, frac_D, SMS, (snow_col or {}).get(c, 0.0))
                info.update(x)
                # An exception RELAXES the requirement ("need only satisfy"): a qualifying column is acceptable when it meets
                # EITHER the default equations or Eqs. (16.4-3)/(16.4-4). Exception 2 is used only where it gives the
                # lower D/C; where the full-mechanism E_mc exceeds the analysed demand (e.g. braces far from their capacity in
                # a low-seismic building) the default check governs and Exception 2 is reported for information.
                info["used"] = bool(x["DC"] < chk["DC_default"])
                if info["used"]:
                    chk = dict(chk, DC=x["DC"], governing=x["governing"] + " [default 16.4-1/16.4-2 + H1-1: %.2f]" % chk["DC_default"],
                               fc_basis="16.4.2.1 Exception 2, Eqs. (16.4-3)/(16.4-4)")
                else:
                    chk = dict(chk, governing=chk["governing"] + " [Exception 2 not needed: 16.4-3/16.4-4 D/C %.2f >= default]" % x["DC"])
            chk["exception_2"] = info
        if not chk["moments_recorded"] and not (chk.get("exception_2") or {}).get("qualifies"):
            moments_missing = True
        col_rows.append(dict(ele=c, section=sec, z_in=pkg.model.nodes[e["n1"]][2], Qu=Q["Pc"], Qu_tension=Q["Pt"], Qns=Qns.get("P", 0.0),
                             Mu_maj=Q["Mmaj"], Mu_min=Q["Mmin"], Mns_maj=Qns.get("Mmaj"), Mns_min=Qns.get("Mmin"),
                             flexure_major=flex["major"], flexure_minor=flex["minor"], modelled=flex.get("modelled") or {}, phiBRn=cap["phiPn"], phiTn=cap["phiTn"],
                             phiMnx=cap["phiMnx"], phiMny=cap["phiMny"], MCEx=cap["MCEx"], MCEy=cap["MCEy"], Fy=cap["Fy"], KLr=KLr,
                             Lcx_in=cap["Lcx"], Lcy_in=cap["Lcy"], Lb_in=cap["Lb"], length_source=cap["length_source"],
                             _Qns=Qns, _flex=flex, **chk))
    # group the worst per (section, z)
    best = {}; dflt = {}
    for r in col_rows:
        k = (r["section"], round(r["z_in"]))
        if k not in best or r["DC"] > best[k]["DC"]:
            best[k] = r
        dflt[k] = max(dflt.get(k, 0.0), r.get("DC_default", r["DC"]))
    for k, r in best.items():
        r["DC_default_group_max"] = dflt[k]                        # NL-R2-16: worst default-check D/C of the group
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
        worst_FC_DC_default=(max((r.get("DC_default_group_max", r["DC"]) for r in col_table), default=None)),
        FC_exception_2=_exc2_summary(col_table, ex2_on, ex2_note, snow_basis),
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


def _exc2_summary(col_table, on, note, snow_basis):
    """NL-R2-16: which columns were accepted under 16.4.2.1 Exception 2, for the verdict, the report and the 16.1.4 document."""
    used = [r for r in col_table if (r.get("exception_2") or {}).get("qualifies") and (r.get("exception_2") or {}).get("used", True)]
    return dict(clause="ASCE 7-22 16.4.2.1 Exception 2, Eqs. (16.4-3)/(16.4-4)", apply=bool(on), used=bool(used), n_columns=len(used),
                members=["%s @ z %.0f in" % (r["section"], r["z_in"]) for r in used],
                not_applied=["%s @ z %.0f in: %s" % (r["section"], r["z_in"], (r.get("exception_2") or {}).get("reason")) for r in col_table
                             if on and not (r.get("exception_2") or {}).get("qualifies")],
                note=note, snow_basis=snow_basis)


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
        for k in ("mean_drift_max", "worst_FC_DC", "worst_FC_DC_default"):
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
