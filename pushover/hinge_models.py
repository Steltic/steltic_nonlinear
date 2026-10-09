"""hinge_models.py -- turn (section, length, axial load) into a concentrated-plasticity hinge definition.

Every number comes from hinge_params.json (filled by the user from the standards in a MANUAL, RAG-assisted step).
This module only does the arithmetic and hands back specs the model builders turn into OpenSees materials:

  * HingeSpec      beams / columns -> IMKPeakOriented (ModIMKPeakOriented fallback). Cyclic deterioration (NL-10):
                   Lambda (Et = Lambda*My) from hinge_params `cyclic_deterioration` (Lignos & Krawinkler 2011 beams,
                   Lignos et al. 2019 columns, or user-supplied). IMKPeakOriented only deteriorates on load reversals, so
                   the monotonic pushover backbone is unchanged; the NLRHA gets cyclic strength/stiffness deterioration.
  * BraceSpec      buckling braces (AISC 342-22 Table C3.4 or the legacy placeholder) -> Hysteretic corotTruss, or
                   (NLRHA, brace_axial.nlrha_element = "physical_theory") a cambered fibre brace with Steel02 + Fatigue
                   (Uriz & Mahin 2008; Hsiao, Lehman & Roeder 2012) that buckles, degrades and fractures.
  * BRBSpec        buckling-restrained braces (NL-02): AISC 342-22 C3.3 / Table C3.3, Q_CE = Fye*A_core, point C at
                   omega*Q_CE (tension) and beta*omega*Q_CE (compression), Delta_y per Eq. C3-3 -> Steel4 (+MinMax) or
                   Hysteretic corotTruss. Group `brb_axial`.
  * LinkShearSpec  EBF shear links (NL-03): AISC 342-22 C2.1 classification (e vs 1.6 / 2.6 MCE/VCE), Table C2.4 shear
                   backbone and acceptance on the link plastic rotation, Vp = 0.6 Fye Alw; flexure per Table C2.2.
                   Group `ebf_link`.
Groups missing from a job's (collected) params file fall back to the repository template block and every spec built
from a template value carries a flag saying so -- nothing is silently assumed.
"""
from __future__ import annotations
import json, math, os
from dataclasses import dataclass, asdict
from . import sections_db as SDB
from . import params_schema as PS

_HERE = os.path.dirname(os.path.abspath(__file__))
E_KSI = 29000.0


def load_params(path: str | None = None) -> dict:
    """Read the parameter file and annotate its provenance (params_schema.annotate): which value fields
    the user did not supply (prm['_unverified']); the superseded column P-M reduction is replaced by
    AISC 342-22 Eqs. C3-5/C3-6. A builder that USES such a group drops prm['verified'] (mark_used)."""
    with open(path or os.path.join(_HERE, "hinge_params.json"), encoding="utf-8") as f:
        return PS.annotate(json.load(f))


@dataclass
class HingeSpec:
    member: str            # "beam" | "column"
    section: str
    L_in: float
    Fye_ksi: float
    Mpe_kipin: float       # expected plastic moment (axial-reduced for columns)
    theta_y: float         # yield rotation (ASCE 41 Eq. 9-1 / 9-2 form)
    a_pl: float            # plastic rotation at capping (rad)
    b_pl: float            # plastic rotation at loss of gravity capacity (rad)
    c_res: float           # residual strength ratio
    Mc_over_My: float
    IO: float; LS: float; CP: float          # plastic-rotation acceptance limits (rad)
    force_controlled: bool = False
    PG_over_Pye: float = 0.0
    compact: bool = True
    flags: tuple = ()
    Lambda: float = 0.0    # IMK cyclic deterioration (Et = Lambda * My, rad); 0 = none (NL-10)
    c_det: float = 1.0     # IMK deterioration exponent c

    def as_dict(self):
        return asdict(self)


_TEMPLATE = None


def template_params() -> dict:
    """The repository hinge_params.json (template blocks for groups a job's params file may lack)."""
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = load_params()
    return _TEMPLATE


def param_group(prm: dict, name: str):
    """(group dict, from_template) -- the job's group if present, else the repository template block."""
    g = prm.get(name)
    if isinstance(g, dict) and g:
        return g, False
    return template_params().get(name) or {}, True


def _gv(g: dict, key: str, tmpl_group: str, default=None):
    """Group value with per-key fallback to the template (a partially filled group keeps the other keys)."""
    v = g.get(key)
    if v is None:
        v = (template_params().get(tmpl_group) or {}).get(key, default)
    return default if v is None else v


def cyclic_lambda(member: str, p: dict, Fye: float, L_in: float, prm: dict, PG_over_Pye: float = 0.0,
                  rbs: bool = False, Lb_in: float = None) -> tuple:
    """(Lambda, c, flag) for an IMK hinge -- NL-10 / ASCE 7-22 16.3.1.

    hinge_params `cyclic_deterioration.mode`:
      "none"      -> (0, 1): no cyclic deterioration (must then be justified under 16.3.1 -- the report says so)
      "supplied"  -> Lambda_beam / Lambda_column given by the user
      "expressions" (default) -> the template's literature regressions, evaluated with h/tw, bf/2tf, Lb/ry, PG/Pye and
                     Fye (MPa) -- Lignos & Krawinkler (2011) for beams (RBS and other), Lignos et al. (2019) for columns.
    The value is capped at Lambda_max. A group taken from the template is flagged."""
    g, tmpl = param_group(prm, "cyclic_deterioration")
    mode = str(g.get("mode", "none")).lower()
    if mode in ("none", "off", "0", ""):
        return 0.0, 1.0, "cyclic deterioration OFF (cyclic_deterioration.mode=none)"
    if str(prm.get("_analysis", "")).lower() == "nlrha":         # Lambda acts only on reversals: the NSP backbone is unaffected
        PS.mark_used(prm, "cyclic_deterioration", from_template=tmpl)
    c = float(_gv(g, "c_exponent", "cyclic_deterioration", 1.0))
    if mode == "supplied":
        key = "Lambda_column" if member == "column" else ("Lambda_beam_rbs" if rbs and g.get("Lambda_beam_rbs") else "Lambda_beam")
        lam = g.get(key)
        if lam is None:
            return 0.0, 1.0, "cyclic deterioration: mode=supplied but %s missing -> OFF" % key
        return float(lam), c, "cyclic deterioration Lambda=%.3f (%s, supplied)" % (float(lam), key)
    key = "column_expr" if member == "column" else ("beam_rbs_expr" if rbs else "beam_expr")
    expr = _gv(g, key, "cyclic_deterioration")
    if not expr:
        return 0.0, 1.0, "cyclic deterioration: no %s -> OFF" % key
    Lb = Lb_in if Lb_in else L_in
    env = {"h_tw": max(p.get("h_tw", 30.0), 1.0), "bf_2tf": max(p.get("bf_2tf", 6.0), 1.0), "Lb_ry": max(Lb / p["ry"], 1.0),
           "PG_Pye": min(max(PG_over_Pye, 0.0), 0.95), "Fye_MPa": Fye * 6.894757, "L_d": L_in / p["d"], "math": math}
    try:
        lam = float(eval(expr, {"__builtins__": {}}, env))
    except Exception as ex:
        return 0.0, 1.0, "cyclic deterioration: %s failed (%s) -> OFF" % (key, ex)
    lam_max = float(_gv(g, "Lambda_max", "cyclic_deterioration", 1e9))
    lam = max(0.0, min(lam, lam_max))
    return lam, c, "cyclic deterioration Lambda=%.3f rad (%s%s, c=%.2f%s)" % (
        lam, key, ", capped" if lam >= lam_max else "", c, "; TEMPLATE literature values -- verify" if tmpl else "")


def _theta_y(Zx, Fye, L, I, axial_factor=1.0):
    return Zx * Fye * L / (6.0 * E_KSI * I) * axial_factor


def _ductility_class(p: dict, Fye: float, prm: dict, Ca: float = 0.0) -> tuple:
    """'highly' | 'moderately' (between lambda_hd and lambda_md) | 'other' (at or beyond lambda_md), with the
    governing flange / web limits. Limits: AISC 341-22 Table D1.1b with Fye for RyFy and Ca = PG/Pye
    (params_schema.element_slenderness; a user `compactness` block overrides)."""
    el, _ = PS.element_slenderness(p, Fye, Ca, prm)
    cls = {"highly": "highly", "other": "moderately", "non_moderately": "other"}[PS.ductility_class(el)]
    return cls, el["flange"][1] if cls == "highly" else el["flange"][2], el["web"][1] if cls == "highly" else el["web"][2]


def beam_hinge(section: str, L_in: float, prm: dict) -> HingeSpec:
    p = SDB.props(section); bp = prm["beam_flexure"]; mt = prm["material"]
    Fye = mt["Fy_ksi"] * mt["Ry_expected"]
    flags = []
    duct, lam_f, lam_w = _ductility_class(p, Fye, prm)
    compact = duct == "highly"
    if p.get("h_tw_approx"):
        flags.append("h/tw approximated as (d-2tf)/tw")
    if bp.get("mode") == "fr_connection":
        # AISC 342 Table C5.5 form: a = X(section, span, bracing) <= a_max (rad), b and c absolute; IO/LS/CP as fractions of a / b.
        # The hinge represents the FR connection + beam end (RBS: plastic section Z_RBS = Zx - 2 c tf (d - tf), AISC 358 Eq. 5.8-4).
        c_rbs = float(bp.get("rbs_c_in", 0.0)) or float(bp.get("rbs_c_frac_bf", 0.0)) * p["bf"]      # RBS flange cut depth c
        Z = p["Zx"] - 2.0 * c_rbs * p["tf"] * (p["d"] - p["tf"]) if c_rbs > 0 else p["Zx"]
        Lb = L_in / bp["Lb_divisor"] if bp.get("Lb_divisor") else min(L_in, bp["Lb_over_ry"] * p["ry"])
        env = dict(h=p["d"] - 2 * p["tf"], tw=p["tw"], bf=p["bf"], tf=p["tf"], Lb=Lb, ry=p["ry"], L=L_in, d=p["d"], math=math)
        a = min(eval(bp["a_expr"], {}, env), bp["a_max"])
        b = bp["b_abs"]; c = bp["c_residual"]
        mult = 1.0
        for m in bp.get("modifiers", []):
            if m.get("sections") and section.strip().upper() not in [x.upper() for x in m["sections"]]:
                continue
            mult *= float(m["factor"]); flags.append("modifier x%.2f: %s" % (float(m["factor"]), m["why"]))
        if duct != "highly":
            mult *= bp.get("noncompact_reduction", 0.5); flags.append("%s-ductile section: x%.2f (C5.4a.1.a.1(c))" % (duct, bp.get("noncompact_reduction", 0.5)))
        a, b = a * mult, b * mult
        Mce = Z * Fye
        ty = Mce * L_in / (6.0 * E_KSI * p["Ix"])                        # AISC 342 Eq. C2-2, eta = 0, L_CL = span
        flags.append("FR connection table: a=%.4f b=%.4f rad (Lb/ry=%.1f, L/d=%.1f, Z=%.0f in3)" % (a, b, Lb / p["ry"], L_in / p["d"], Z))
        lam, cdet, fl = cyclic_lambda("beam", p, Fye, L_in, prm, rbs=c_rbs > 0, Lb_in=Lb)
        flags.append(fl)
        PS.mark_used(prm, "beam_flexure"); PS.mark_used(prm, "material")
        return HingeSpec("beam", section, L_in, Fye, Mce, ty, a, b, c, bp["Mc_over_My"],
                         IO=bp["IO_frac_of_a"] * a, LS=bp["LS_frac_of_b"] * b, CP=bp["CP_frac_of_b"] * b, compact=compact, flags=tuple(flags),
                         Lambda=lam, c_det=cdet)
    # AISC 342-22 Table C2.2 (member hinge): the row is chosen by the section's flange / web slenderness
    # (line 1 highly ductile, line 2 non-moderately ductile, line 3 interpolate -- lowest value), never by
    # the seismic system. Cells may be given as printed ("9 θy", "0.25 a", "b") or in the field form.
    ty = _theta_y(p["Zx"], Fye, L_in, p["Ix"])                      # AISC 342 Eq. C2-2, eta = 0
    v, extra = _member_row_values(bp, ty, p, Fye, prm, 0.0, flags, kind="beam")
    PS.mark_used(prm, "beam_flexure", extra); PS.mark_used(prm, "material")
    lam, cdet, fl = cyclic_lambda("beam", p, Fye, L_in, prm)
    flags.append(fl)
    return HingeSpec("beam", section, L_in, Fye, p["Zx"] * Fye, ty, v["a"], v["b"], v["c"], bp["Mc_over_My"],
                     IO=v["IO"], LS=v["LS"], CP=v["CP"], compact=compact, flags=tuple(flags), Lambda=lam, c_det=cdet)


def _row_abs(row: dict, ty: float, env: dict | None = None) -> dict:
    """a, b, c, IO, LS, CP (rad) of one Table C2.2 / C3.6 line. Expressions (a_expr ...) use `env`."""
    def ev(key):
        e = row.get(key)
        if e is None:
            return None
        if isinstance(e, (int, float)):
            return float(e)
        e = e.replace("PG/Pye", "(PG/Pye)").replace("h/tw", "(h/tw)").replace("L/ry", "(L/ry)")
        return float(eval(e, {}, dict(env or {})))
    a = ev("a_expr") if row.get("a_expr") is not None else PS.row_value(row, "a", thetay=ty)
    b = ev("b_expr") if row.get("b_expr") is not None else PS.row_value(row, "b", thetay=ty)
    c = ev("c_expr") if row.get("c_expr") is not None else PS.row_value(row, "c")
    if a is not None:
        a = max(a, float(row.get("a_min", 0.0) or 0.0))
        if row.get("a_max") is not None: a = min(a, float(row["a_max"]))
    if b is not None:
        b = max(b, float(row.get("b_min", 0.0) or 0.0))
        if row.get("b_max") is not None: b = min(b, float(row["b_max"]))
    if c is not None:
        c = max(0.0, c)
    out = dict(a=a, b=b, c=c)
    for lvl in ("IO", "LS", "CP"):
        out[lvl] = PS.row_value(row, lvl, a=a, b=b, thetay=ty) if a is not None and b is not None else None
    return out


def _member_row_values(blk: dict, ty: float, p: dict, Fye: float, prm: dict, Ca: float, flags: list,
                       kind: str, env: dict | None = None) -> tuple:
    """Line 1 / line 2 / interpolation of AISC 342-22 Table C2.2 (beams) or C3.6 (columns) for this section.
    -> (values, extra unverified notes). When line 2 is not in the file, a section that is not highly
    ductile gets the file's flat reduction -- an approximation, flagged and reported as UNVERIFIED."""
    r1, r2 = PS.rows_of(blk)
    elements, lim_note = PS.element_slenderness(p, Fye, Ca, prm)
    cls = PS.ductility_class(elements)
    v1 = _row_abs(r1, ty, env)
    extra = []
    desc = ", ".join("%s %.2f (hd %.2f, md %.2f)" % (k, l, hd, md) for k, (l, hd, md) in elements.items())
    if cls == "highly":
        v = v1
    elif r2:
        v2 = _row_abs(r2, ty, env)
        v = PS.interpolate_rows(v1, v2, elements)
        flags.append("%s section (%s; %s): %s" % ("non-moderately ductile" if cls == "non_moderately" else "between lambda_hd and lambda_md",
                                                 desc, lim_note, "line 2" if cls == "non_moderately" else "line 3 interpolation, lowest value"))
    else:
        key = "noncompact_reduction" if kind == "beam" else "non_highly_ductile_reduction"
        f = float(blk.get(key, 0.5))
        v = {k: (x * f if k != "c" and x is not None else x) for k, x in v1.items()}
        flags.append("NOT highly ductile (%s) and line 2 (non-moderately ductile) is not in the parameter file: "
                     "flat x%.2f on line 1 -- NOT the table's interpolation" % (desc, f))
        extra.append("rows.non_moderately_ductile (not supplied; flat x%.2f used for %s)" % (f, p.get("AISC_Manual_Label") or kind))
    missing = [k for k in ("a", "b", "c", "IO", "LS", "CP") if v.get(k) is None]
    if missing:
        raise ValueError("%s parameters: %s missing in the parameter file (%s)" % (kind, ", ".join(missing), "AISC 342-22 Table %s" % ("C2.2" if kind == "beam" else "C3.6")))
    return v, extra


def _hss_column_hinge(section: str, p: dict, L_in: float, PG_kip: float, prm: dict) -> HingeSpec:
    """NL-R2-28: HSS column flexural hinge.

    Rectangular HSS: AISC 342-22 Table C3.6 line 4 ("Rectangular HSS and built-up box shapes", columns in
    compression; one line, no highly / moderately ductile split):
        a = 1.1 lam^-1.2 (1 - PG/Pye)^1.8 <= 0.05,  b = 0.5 lam^-0.6 (1 - PG/Pye)^1.2 - 0.01 <= 0.08,  c = 0.25,
        IO = 0.5 a, LS = 0.75 b, CP = b;  note [e]: x0.75 for built-up box columns (not rolled HSS).
    lam = the most slender wall b/t (note [b]: the element giving the lowest deformation), with b = B - 3t and the
    design wall t = 0.93 t_nom (AISC 360-22 B4.1b, B4.2). M_CE with Eqs. C3-5/C3-6 and theta_y with Eq. C3-15,
    exactly as for W columns; PG/Pye above the force-controlled limit -> force-controlled (C3.4). Expressions,
    limits and the HSS material come from the group column_flexure_hss (template fallback, flagged).
    Round HSS / pipe: Table C3.6 has no row -> no hinge, treated as force-controlled for flexure (flagged)."""
    cp = prm["column_flexure"]
    hb, tmpl = param_group(prm, "column_flexure_hss")
    flags = ["HSS column: AISC 342-22 Table C3.6 line 4 (rectangular HSS)%s" % (
        " -- column_flexure_hss not in the parameter file: repository TEMPLATE values" if tmpl else "")]
    mt = prm["material"]
    if hb.get("Fy_ksi") is not None:                     # HSS material (A500 Gr. C) -- not the W-shape `material`
        Fy, Ry = float(hb["Fy_ksi"]), float(hb.get("Ry_expected", 1.0)); src = "column_flexure_hss"
    elif (prm.get("brace_axial") or {}).get("Fy_ksi") is not None:
        Fy, Ry = float(prm["brace_axial"]["Fy_ksi"]), float(prm["brace_axial"].get("Ry_expected", 1.0)); src = "brace_axial (HSS)"
    else:
        Fy, Ry = float(mt["Fy_ksi"]), float(mt["Ry_expected"]); src = "material (W-shape values)"
    Fye = Fy * Ry
    flags.append("HSS Fye = %.1f x %.2f = %.1f ksi (%s)" % (Fy, Ry, Fye, src))
    Pye = p["A"] * Fye
    r = max(0.0, PG_kip) / Pye
    fc_lim = float(cp.get("force_controlled_above_P_over_Pye", 0.6))
    ty0 = _theta_y(p["Zx"], Fye, L_in, p["Ix"], max(1e-6, 1 - r))
    geo = p.get("hss") or {}
    if geo.get("kind") != "rect" or r >= fc_lim:
        if geo.get("kind") != "rect":
            flags.append("%s (%s): AISC 342-22 Table C3.6 has no row for round HSS / pipe columns -> no flexural hinge, "
                         "FORCE-CONTROLLED column" % (section, geo.get("kind", "?")))
        else:
            flags.append("PG/Pye=%.2f >= %.2f -> FORCE-CONTROLLED column (no hinge; check P vs PCL)" % (r, fc_lim))
        PS.mark_used(prm, "column_flexure_hss", from_template=tmpl); PS.mark_used(prm, "column_flexure")
        return HingeSpec("column", section, L_in, Fye, p["Zx"] * Fye, ty0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0,
                         True, r, True, tuple(flags))
    ws = SDB.hss_wall_slenderness(section)
    lam = ws["lam"]
    env = dict(lam=lam, PG=max(0.0, PG_kip), Pye=Pye, L=L_in, ry=p["ry"], math=math)
    row = dict(hb)
    for k in ("a_expr", "b_expr", "c_expr"):
        if isinstance(row.get(k), str):
            row[k] = row[k].replace("PG/Pye", "(PG/Pye)")
    v = _row_abs(row, None, env)
    mult = float(hb.get("built_up_box_factor", 0.75)) if hb.get("built_up_box") else 1.0
    if mult != 1.0:
        flags.append("built-up box column: Table C3.6 note [e] x%.2f" % mult)
        v = {k: (x * mult if k != "c" and x is not None else x) for k, x in v.items()}
    missing = [k for k in ("a", "b", "c", "IO", "LS", "CP") if v.get(k) is None]
    if missing:
        raise ValueError("HSS column parameters: %s missing (column_flexure_hss, AISC 342-22 Table C3.6 line 4)" % ", ".join(missing))
    flags.append("HSS wall b/t=%.1f (t_des=%.3f in): a=%.4f b=%.4f c=%.2f rad at PG/Pye=%.3f" % (lam, ws["t_des"], v["a"], v["b"], v["c"], r))
    PS.mark_used(prm, "column_flexure_hss", from_template=tmpl); PS.mark_used(prm, "column_flexure")
    red = eval(cp["Mpce_axial_reduction"].replace("PG/Pye", "(PG/Pye)"), {}, env)
    Mpe = p["Zx"] * Fye * red
    ty = _theta_y(p["Zx"], Fye, L_in, p["Ix"], red if cp.get("theta_y_uses_Mpce") else 1 - r)
    cd = prm.get("cyclic_deterioration") or (template_params().get("cyclic_deterioration") or {})
    lam_c, cdet = 0.0, 1.0
    if str(cd.get("mode", "none")).lower() == "supplied" and cd.get("Lambda_column_hss") is not None:
        lam_c = float(cd["Lambda_column_hss"]); cdet = float(cd.get("c_exponent", 1.0))
        flags.append("cyclic deterioration Lambda=%.3f (Lambda_column_hss, supplied)" % lam_c)
    else:                                                # the W-shape regressions (Lignos et al.) do not cover HSS
        flags.append("cyclic deterioration OFF for HSS columns (W-shape regressions not applicable; supply "
                     "cyclic_deterioration.Lambda_column_hss with mode=supplied)")
    return HingeSpec("column", section, L_in, Fye, Mpe, ty, v["a"], v["b"], v["c"], float(hb.get("Mc_over_My", cp.get("Mc_over_My", 1.1))),
                     IO=v["IO"], LS=v["LS"], CP=v["CP"], force_controlled=False, PG_over_Pye=r, compact=True,
                     flags=tuple(flags), Lambda=lam_c, c_det=cdet)


def column_hinge(section: str, L_in: float, PG_kip: float, prm: dict) -> HingeSpec:
    p = SDB.props(section); cp = prm["column_flexure"]; mt = prm["material"]
    if p.get("hss"):                                     # NL-R2-28: HSS / pipe -- no W-shape fields (d, tf, tw, bf)
        return _hss_column_hinge(section, p, L_in, PG_kip, prm)
    Fye = mt["Fy_ksi"] * mt["Ry_expected"]
    Pye = p["A"] * Fye
    r = max(0.0, PG_kip) / Pye
    flags = []
    env = dict(PG=max(0.0, PG_kip), Pye=Pye, L=L_in, ry=p["ry"], h=p["d"] - 2 * p["tf"], tw=p["tw"],
               bf=p["bf"], tf=p["tf"], d=p["d"], math=math)
    if r >= cp["force_controlled_above_P_over_Pye"]:
        PS.mark_used(prm, "column_flexure"); PS.mark_used(prm, "material")
        flags.append("PG/Pye=%.2f >= %.2f -> FORCE-CONTROLLED column (no hinge; check P vs PCL)" % (r, cp["force_controlled_above_P_over_Pye"]))
        return HingeSpec("column", section, L_in, Fye, p["Zx"] * Fye, _theta_y(p["Zx"], Fye, L_in, p["Ix"], 1 - r),
                         0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, True, r, True, tuple(flags))
    # AISC 342-22 Table C3.6, columns in compression: line 1 / line 2 / line 3 by this section's flange and web
    # slenderness with Ca = PG/Pye (note [b]) -- the row is never chosen from the seismic system.
    v, extra = _member_row_values(cp, None, p, Fye, prm, r, flags, kind="column", env=env)
    a, b, c = v["a"], v["b"], v["c"]
    PS.mark_used(prm, "column_flexure", extra); PS.mark_used(prm, "material")
    red = eval(cp["Mpce_axial_reduction"].replace("PG/Pye", "(PG/Pye)"), {}, env)
    Mpe = p["Zx"] * Fye * red
    ty = _theta_y(p["Zx"], Fye, L_in, p["Ix"], red if cp.get("theta_y_uses_Mpce") else 1 - r)   # Eq. C3-15 with M_CE (tau_b = 1)
    if p.get("h_tw_approx"):
        flags.append("h/tw approximated as (d-2tf)/tw")
    lam, cdet, fl = cyclic_lambda("column", p, Fye, L_in, prm, PG_over_Pye=r)
    flags.append(fl)
    return HingeSpec("column", section, L_in, Fye, Mpe, ty, a, b, c, cp["Mc_over_My"],
                     IO=v["IO"], LS=v["LS"], CP=v["CP"],
                     force_controlled=False, PG_over_Pye=r, compact=True, flags=tuple(flags), Lambda=lam, c_det=cdet)


def modimk_args(h: HingeSpec, K0: float, post_cap_ratio: float = 0.15) -> list:
    """ModIMKPeakOriented argument list (after the tag). Lambda_S/C/A/K = h.Lambda (0 = no cyclic deterioration;
    NOTE the legacy ModIMK treats Lambda = 0 as 'no deterioration' too), exponents c = h.c_det.
    theta_pc is set so the descent from Mc reaches the residual c*My over post_cap_ratio*a of rotation."""
    My = h.Mpe_kipin
    Mc = h.Mc_over_My * My
    a_s = ((Mc - My) / max(h.a_pl, 1e-6)) / K0          # hardening ratio relative to K0
    a_s = min(max(a_s, 1e-4), 0.05)
    drop = h.Mc_over_My - h.c_res
    theta_pc = max(post_cap_ratio * h.a_pl * h.Mc_over_My / max(drop, 1e-3), 1e-3)
    lam = float(getattr(h, "Lambda", 0.0) or 0.0); c = float(getattr(h, "c_det", 1.0) or 1.0)
    return [K0, a_s, a_s, My, -My, lam, lam, lam, lam, c, c, c, c,
            h.a_pl, h.a_pl, theta_pc, theta_pc, h.c_res, h.c_res, h.b_pl, h.b_pl, 1.0, 1.0]


def make_imk_material(tag: int, h: HingeSpec, K0: float, post_cap_ratio: float = 0.15):
    """post_cap_ratio: fraction of `a` over which the backbone descends from Mc to the residual c*My.
    0.15 (default) approximates the ASCE 41 near-vertical drop; raising it (e.g. 0.5) is a MODELLING
    CHANGE that eases the descending-branch solve and must be disclosed in the report."""
    """Create the hinge material. OpenSees >= 3.7 renamed ModIMKPeakOriented -> IMKPeakOriented with a
    different argument order; try the new one first and fall back to the old."""
    import openseespy.opensees as ops
    a = modimk_args(h, K0, post_cap_ratio)
    K0_, a_s, _, My, _, *_rest = a
    theta_p, theta_pc, res, theta_u = a[13], a[15], a[17], a[19]
    # IMKPeakOriented: Ke dp+ dpc+ du+ Fy+ FmaxFy+ ResF+ dp- dpc- du- Fy- FmaxFy- ResF- LS LC LA LK cS cC cA cK D+ D-
    lam = float(getattr(h, "Lambda", 0.0) or 0.0); c = float(getattr(h, "c_det", 1.0) or 1.0)
    new = [K0_, theta_p, theta_pc, theta_u, My, h.Mc_over_My, res,
           theta_p, theta_pc, theta_u, My, h.Mc_over_My, res,
           lam, lam, lam, lam, c, c, c, c, 1.0, 1.0]           # NL-10: Et = Lambda*My (0 -> no cyclic deterioration)
    try:
        ops.uniaxialMaterial("IMKPeakOriented", tag, *new)
        return "IMKPeakOriented"
    except Exception:
        ops.uniaxialMaterial("ModIMKPeakOriented", tag, *a)
        return "ModIMKPeakOriented"


# --------------------------------------------------------------------------- braces
@dataclass
class BraceSpec:
    member: str            # "brace"
    section: str
    L_in: float
    A: float
    r: float
    KL_r: float
    Fye_ksi: float
    Pye_kip: float         # expected tension yield  = A*Fye
    Pcre_kip: float        # expected buckling      = 1.14*Fcre*A
    dT: float              # elongation at Pye      = Pye*L/(E*A)
    dc: float              # shortening at Pcre     = Pcre*L/(E*A)
    a_c: float; b_c: float; c_c: float          # compression backbone (in)
    a_t: float; b_t: float; c_t: float          # tension backbone (in)
    IO: float; LS: float; CP: float             # governing (compression) acceptance, in
    IO_t: float; LS_t: float; CP_t: float       # tension acceptance, in
    slenderness_class: str = ""
    flags: tuple = ()
    # duck-typing for the hinge-based post-processing (theta_y / a_pl / b_pl used by acceptance + b-limit)
    @property
    def theta_y(self): return self.dc
    @property
    def a_pl(self): return self.a_c
    @property
    def b_pl(self): return self.b_c
    def as_dict(self): return asdict(self)


def _hss_outside_and_tdes(section: str):
    """Rectangular HSS label (HSS12X12X5/8, HSS5-1/2X5-1/2X3/8) -> (larger outside dimension, tdes);
    A500 design wall tdes = 0.93 tnom (AISC 360-22 B4.2). (None, None) for anything else (NL-R2-28: the
    fractional dimensions '5-1/2' used to fail this parse silently)."""
    g = SDB.parse_hss_label(section)
    if not g or g["kind"] != "rect":
        return None, None
    return max(g["B"], g["H"]), g["t_des"]


def brace_spec(section: str, L_in: float, prm: dict, Lc_in: float = None) -> BraceSpec:
    """Lc_in (NL-R2-19): the buckling length the HR design used (brace_length / brace_length_factor, X crossing);
    default K_effective x L_in. L_in (work-point length) still sets the axial stiffness."""
    p = SDB.props(section); bp = prm["brace_axial"]
    Fye = bp["Fy_ksi"] * bp["Ry_expected"]
    A, r = p["A"], min(p["rx"], p["ry"])
    KLr = (Lc_in if Lc_in else bp["K_effective"] * L_in) / r
    Fe = math.pi ** 2 * E_KSI / KLr ** 2
    Fcre = (0.658 ** (Fye / Fe)) * Fye if KLr <= 4.71 * math.sqrt(E_KSI / Fye) else 0.877 * Fe     # AISC 360 E3 form with Fye
    Pye = A * Fye; Pcre = bp["Pcr_expected_factor"] * Fcre * A
    k = E_KSI * A / L_in
    dT, dc = Pye / k, Pcre / k
    flags = []
    if bp.get("mode") == "table_C3_4":
        # AISC 342-22 Table C3.4 rectangular HSS: n expressions; d = n*delta; f residual; IO/LS/CP as printed.
        lam_hd_coef = float(bp.get("lambda_hd_coef", 0.65))  # D1.1a Case 2 walls of rect. HSS
        B, tdes = _hss_outside_and_tdes(section)
        if B and tdes and tdes > 0:
            lam = (B - 3.0 * tdes) / tdes   # Spec. B4.1b rectangular HSS wall slenderness
            lam_hd = lam_hd_coef * math.sqrt(E_KSI / Fye)
            lam_ratio = max(lam / lam_hd, 1e-6)
            flags.append("HSS b/t=%.2f, lambda_hd=%.2f, lambda/lambda_hd=%.3f (tdes=0.93*tnom)" % (lam, lam_hd, lam_ratio))
        else:
            lam_ratio = float(bp.get("lambda_over_lambda_hd_default", 1.0))
            flags.append("HSS wall thickness not parsed; using lambda/lambda_hd=%.3f" % lam_ratio)
        slend = KLr / math.sqrt(E_KSI / Fye)  # (Lc/r) / sqrt(E/Fy) with Fy~Fye basis as in C3.4 print
        env = dict(lam_ratio=lam_ratio, lam_over_lam_hd=lam_ratio, slend=slend, KLr=KLr, Lc_r=KLr, Fye=Fye, E=E_KSI,
                   sqrt=math.sqrt, math=math)
        n_c = float(eval(bp["compression"]["n_expr"], {"__builtins__": {}}, env))
        n_t = float(eval(bp["tension"]["n_expr"], {"__builtins__": {}}, env))
        n_c = max(n_c, 1e-6); n_t = max(n_t, 1e-6)
        # Table C3.4 cells as printed ('1.5 Δc', '0.7 n Δc', 'n Δc', f) or as fields (params_schema.c34_side)
        sc, st_ = PS.c34_side(bp["compression"]), PS.c34_side(bp["tension"])
        div = 2.0 if bp.get("tension_only") else 1.0            # Table C3.4 note [d]: tension-only bracing, values / 2.0
        if div != 1.0:
            flags.append("tension-only bracing: Table C3.4 values divided by 2.0 (note [d])")
        # Map C3.4 (d,f) to Hysteretic: brief post-buckling plateau then residual at total d = n*delta.
        a_plateau = float(bp["compression"].get("a_plateau_over_dc", 0.05))
        a_c = a_plateau * dc
        b_c = n_c * dc / div
        c_c = sc["f"]
        a_t = float(bp["tension"].get("a_plateau_over_dT", 0.05)) * dT
        b_t = n_t * dT / div
        c_t = st_["f"]
        IO = sc["IO_over"] * dc / div
        LS = sc["LS_frac_of_n"] * n_c * dc / div
        CP = sc["CP_frac_of_n"] * n_c * dc / div
        IO_t = st_["IO_over"] * dT / div
        LS_t = st_["LS_frac_of_n"] * n_t * dT / div
        CP_t = st_["CP_frac_of_n"] * n_t * dT / div
        PS.mark_used(prm, "brace_axial")
        if IO > LS: IO = LS
        if IO_t > LS_t: IO_t = LS_t
        cls = "C3.4_rect_HSS"
        flags.append("AISC 342-22 Table C3.4 rectangular HSS: n_c=%.3f n_t=%.3f KL/r=%.1f slend=%.3f" % (n_c, n_t, KLr, slend))
        return BraceSpec("brace", section, L_in, A, r, KLr, Fye, Pye, Pcre, dT, dc,
                         a_c=a_c, b_c=b_c, c_c=c_c, a_t=a_t, b_t=b_t, c_t=c_t,
                         IO=IO, LS=LS, CP=CP, IO_t=IO_t, LS_t=LS_t, CP_t=CP_t,
                         slenderness_class=cls, flags=tuple(flags))
    sl, st = 4.2 * math.sqrt(E_KSI / Fye), 2.1 * math.sqrt(E_KSI / Fye)
    cS, cK = bp["compression"]["slender"], bp["compression"]["stocky"]
    if KLr >= sl: w, cls = 1.0, "slender"
    elif KLr <= st: w, cls = 0.0, "stocky"
    else: w, cls = (KLr - st) / (sl - st), "intermediate"
    def mix(key): return cK[key] + w * (cS[key] - cK[key])
    tp = bp["tension"]
    PS.mark_used(prm, "brace_axial", ["mode (legacy ASCE 41-17 Table 9-8 stocky/slender form; AISC 342-22 Table C3.4 needs mode=table_C3_4)"])
    flags = ["brace backbone is a PLACEHOLDER (ASCE 41-17 Table 9-8 form) -- verify against AISC 342-22",
             "HSS local slenderness (b/t) not checked: aisc_shapes.csv carries no wall thickness for HSS"]
    return BraceSpec("brace", section, L_in, A, r, KLr, Fye, Pye, Pcre, dT, dc,
                     a_c=mix("a_over_dc") * dc, b_c=mix("b_over_dc") * dc, c_c=mix("c"),
                     a_t=tp["a_over_dT"] * dT, b_t=tp["b_over_dT"] * dT, c_t=tp["c"],
                     IO=mix("IO_over_dc") * dc, LS=mix("LS_over_dc") * dc, CP=mix("CP_over_dc") * dc,
                     IO_t=tp["IO_over_dT"] * dT, LS_t=tp["LS_over_dT"] * dT, CP_t=tp["CP_over_dT"] * dT,
                     slenderness_class=cls, flags=tuple(flags))


def make_brace_material(tag: int, b: BraceSpec, prm: dict):
    """Hysteretic trilinear envelope (force-deformation) for a corotTruss brace:
    tension  : (Pye, dT) -> (h*Pye, dT + a_t) -> (c_t*Pye, b_t)   then descends to zero
    compress : (Pcre, dc) -> (Pcre, dc + a_c) -> (c_c*Pcre, b_c)  then descends to zero
    Given as STRESS-strain for the truss: divide forces by A and deformations by L."""
    import openseespy.opensees as ops
    A, L = b.A, b.L_in
    h = prm["brace_axial"]["tension"]["hardening_ratio"]
    s1p, e1p = b.Pye_kip / A, b.dT / L
    s2p, e2p = h * b.Pye_kip / A, (b.dT + b.a_t) / L
    s3p, e3p = b.c_t * b.Pye_kip / A, max(b.b_t, b.dT + b.a_t * 1.05) / L
    s1n, e1n = -b.Pcre_kip / A, -b.dc / L
    s2n, e2n = -b.Pcre_kip / A, -(b.dc + b.a_c) / L
    s3n, e3n = -b.c_c * b.Pcre_kip / A, -max(b.b_c, (b.dc + b.a_c) * 1.05) / L
    ops.uniaxialMaterial("Hysteretic", tag, s1p, e1p, s2p, e2p, s3p, e3p, s1n, e1n, s2n, e2n, s3n, e3n, 1.0, 1.0, 0.0, 0.0, 0.0)
    return "Hysteretic"


# --------------------------------------------------------------------------- panel zones (scissors / joint rotational spring)
NU_STEEL = 0.3
G_KSI = E_KSI / (2.0 * (1.0 + NU_STEEL))


@dataclass
class PanelZoneSpec:
    """Scissors-style joint rotational spring (kip-in, rad). One spring per FR framing plane at a joint."""
    joint: int
    dof: int                 # global rot DOF 4=RX or 5=RY
    col_section: str
    beam_sections: tuple
    dc: float                # column depth (in)
    tp: float                # panel thickness tw + doublers (in)
    db: float                # governing beam depth (in)
    Fy_ksi: float
    K_theta: float           # elastic rotational stiffness kip-in/rad
    material: str            # "elastic" | "hysteretic"
    My: float = 0.0          # first yield moment of trilinear (hysteretic)
    theta_y: float = 0.0
    Mp: float = 0.0          # plastic / second corner
    theta_p: float = 0.0
    Mr: float = 0.0          # residual
    theta_r: float = 0.0
    flags: tuple = ()

    def as_dict(self):
        return asdict(self)


def panel_zone_mode(prm: dict) -> str:
    pz = prm.get("panel_zones") or {}
    return str(pz.get("mode", "rigid")).lower()


def _pz_block(prm: dict) -> dict:
    return prm.get("panel_zones") or {}


def panel_zone_props(col_section: str, beam_sections: list, prm: dict) -> dict:
    """Column web panel geometry + Gupta–Krawinkler elastic K_theta (kip-in/rad).
    K_theta ≈ G * tp * dc * db  with gamma≈relative joint rotation (scissors idealisation)."""
    pz = _pz_block(prm)
    cp = SDB.props(col_section)
    doubler = float(pz.get("doubler_t_in", 0.0) or 0.0)
    dc = float(cp["d"])
    tp = float(cp["tw"]) + doubler
    dbs = [float(SDB.props(s)["d"]) for s in beam_sections if s]
    db = sum(dbs) / len(dbs) if dbs else dc
    Fy = float((prm.get("material") or {}).get("Fy_ksi", 50.0))
    K_override = pz.get("K_theta")
    if K_override is not None:
        K_theta = float(K_override)
        flags = ("K_theta overridden in panel_zones.K_theta",)
    else:
        K_theta = G_KSI * tp * dc * db
        flags = ("K_theta = G*tp*dc*db (scissors / Gupta–Krawinkler elastic)",)
    return dict(dc=dc, tp=tp, db=db, Fy_ksi=Fy, K_theta=K_theta, flags=flags,
                bf_c=float(cp["bf"]), tf_c=float(cp["tf"]))


def panel_zone_spec(joint: int, dof: int, col_section: str, beam_sections: list, prm: dict) -> PanelZoneSpec:
    """Build PanelZoneSpec for one FR joint plane. material from panel_zones.material (elastic|hysteretic)."""
    pz = _pz_block(prm)
    mat_kind = str(pz.get("material", "elastic")).lower()
    geo = panel_zone_props(col_section, beam_sections, prm)
    flags = list(geo["flags"])
    My = Mp = Mr = theta_y = theta_p = theta_r = 0.0
    if mat_kind == "hysteretic":
        # Gupta–Krawinkler trilinear (moment–rotation of scissors spring): Vy=0.55 Fy dc tp;
        # flange contribution raises Vp; gamma_y = Fy/(√3 G); M = V*db.
        Vy = 0.55 * geo["Fy_ksi"] * geo["dc"] * geo["tp"]
        Vp = Vy * (1.0 + 3.0 * geo["bf_c"] * geo["tf_c"] ** 2 / max(geo["db"] * geo["dc"] * geo["tp"], 1e-9))
        gamma_y = geo["Fy_ksi"] / (math.sqrt(3.0) * G_KSI)
        My = Vy * geo["db"]
        Mp = Vp * geo["db"]
        theta_y = gamma_y
        theta_p = float(pz.get("theta_p_over_thy", 4.0)) * gamma_y
        Mr = float(pz.get("c_residual", 0.9)) * Mp
        theta_r = float(pz.get("theta_r_over_thy", 100.0)) * gamma_y
        # keep envelope corners strictly increasing in |rotation|
        if theta_p <= theta_y:
            theta_p = theta_y * 1.05
        if theta_r <= theta_p:
            theta_r = theta_p * 1.05
        K_theta = My / max(theta_y, 1e-12)
        flags.append("Hysteretic trilinear Gupta–Krawinkler (Vy=0.55 Fy dc tp; Vp with flange term)")
        # optional absolute overrides (kip-in / rad)
        hb = pz.get("hysteretic") or {}
        if hb.get("s1p") is not None:
            My, theta_y = float(hb["s1p"]), float(hb["e1p"])
            Mp, theta_p = float(hb["s2p"]), float(hb["e2p"])
            Mr, theta_r = float(hb["s3p"]), float(hb["e3p"])
            K_theta = My / max(theta_y, 1e-12)
            flags.append("hysteretic envelope overridden from panel_zones.hysteretic")
    else:
        mat_kind = "elastic"
        K_theta = geo["K_theta"]
    return PanelZoneSpec(joint, dof, col_section, tuple(beam_sections), geo["dc"], geo["tp"], geo["db"],
                         geo["Fy_ksi"], K_theta, mat_kind, My, theta_y, Mp, theta_p, Mr, theta_r, tuple(flags))


def make_panel_zone_material(tag: int, spec: PanelZoneSpec):
    """Uniaxial material for a scissors PZ rotational spring (stress=moment kip-in, strain=rad)."""
    import openseespy.opensees as ops
    if spec.material == "hysteretic":
        s1p, e1p = spec.My, spec.theta_y
        s2p, e2p = spec.Mp, spec.theta_p
        s3p, e3p = spec.Mr, spec.theta_r
        ops.uniaxialMaterial("Hysteretic", tag,
                             s1p, e1p, s2p, e2p, s3p, e3p,
                             -s1p, -e1p, -s2p, -e2p, -s3p, -e3p,
                             1.0, 1.0, 0.0, 0.0, 0.0)
        return "Hysteretic"
    ops.uniaxialMaterial("Elastic", tag, spec.K_theta)
    return "Elastic"


# =========================================================================== NL-02 buckling-restrained braces
@dataclass
class BRBSpec(BraceSpec):
    """Buckling-restrained brace (AISC 342-22 C3.3 / E3). Duck-types BraceSpec so the brace monitors and the ASCE 41 /
    Chapter 16 acceptance code treat it like any brace. ALL deformations stored here are TOTAL axial deformations
    (Delta_y + plastic), because the brace monitors record the total axial deformation of the truss:
        IO = (1 + IO_over_dy) Delta_y, LS = (1 + LS_over_dy) Delta_y, CP = (1 + CP_over_dy) Delta_y   (Table C3.3 plastic
        limits shifted by Delta_y), a_c = a_t = (1 + a_over_dy) Delta_y, b_c = b_t = (1 + b_over_dy) Delta_y.
    dc = dT = Delta_y (Eq. C3-3). Pye_kip = Pcre_kip = Q_CE = Fye*A_core (C3.3a.1: P_CE = T_CE = core area x Fye)."""
    Asc: float = 0.0             # yielding core area (in^2) -- NOT the HR truss area
    Fysc_ksi: float = 0.0
    Ry: float = 1.0
    omega: float = 1.0           # strain-hardening adjustment (tension point C = omega*Q_CE)
    beta: float = 1.0            # compression adjustment (compression point C = beta*omega*Q_CE)
    K_axial: float = 0.0         # elastic axial stiffness (kip/in) = Q_CE / Delta_y
    A_model: float = 0.0         # element area used in the corotTruss (stress = force / A_model)
    material: str = "Steel4"
    sources: tuple = ()


def brb_package_data(calc: dict, cfg: dict = None) -> dict:
    """Structured BRB data from the HR package. Sources, most specific first (a value found earlier is kept):
      1. calc_package members[*].inputs (kind brace, section BRB-*): Asc_in2, Fysc_ksi, KF, model_area_in2
      2. NL-R2-10: cfg['brb'] -- the HR engine's own BRB input (static_model reads it for F4.2a): Asc (number or
         {label: in2}), Fysc (the adjusted-strength value; Fysc_min the design value), Ry, omega, beta, KF
      3. NL-R2-10: calc_package capacity_design.BRB_adjusted_strengths (HR engine output): by_group[*] {label, Asc_in2,
         Fysc_ksi (number or the coupon range [min, max]), omega, beta, T_adj_kip}, top-level omega / beta / KF
      4. capacity_design.adjusted_brace_strengths.by_story (older agent field names): beta = C/T, omega*Ry = T/Pysc,
         omega from the basis text -> Ry.
    A Fysc range is taken at its UPPER end (Fysc,max) -- the end HR's adjusted strengths (T = omega Ry Fysc,max Asc,
    AISC 341-22 F4.2a) use, conservative for the capacity-designed / force-controlled actions -- and the source says so.
      per label -> {Asc, Fysc_ksi, KF, model_area, omega, beta, Ry, src: {key: where}}; "_global" -> {omega, beta, Ry, KF, basis}
    Missing items are simply absent (brb_spec falls back to hinge_params, or refuses, and says so)."""
    import re
    out = {}
    norm = lambda lab: str(lab or "").strip().upper().replace(" ", "")

    def put(lab, key, v, src):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            d = out.setdefault(norm(lab), {})
            if key not in d:
                d[key] = float(v); d.setdefault("src", {})[key] = src

    for m in (calc or {}).get("members") or []:
        inp = m.get("inputs") or {}
        sec = str(inp.get("section") or "")
        if not SDB.is_brb(sec):
            continue
        out.setdefault(norm(sec), {})
        for k_src, k in (("Asc_in2", "Asc"), ("Fysc_ksi", "Fysc_ksi"), ("KF", "KF"), ("model_area_in2", "model_area")):
            put(sec, k, inp.get(k_src), "calc_package members[%s].inputs.%s" % (m.get("id"), k_src))
    g = {}
    cb = (cfg or {}).get("brb") if isinstance(cfg, dict) else None
    labels = set(out)
    if isinstance(cb, dict):                                               # 2. cfg['brb'] (NL-R2-10)
        asc = cb.get("Asc")
        labels |= {norm(k) for k in asc} if isinstance(asc, dict) else set()
        fy = cb.get("Fysc")
        fy_txt = ("cfg['brb']['Fysc'] = %.1f ksi%s -- the HR adjusted-strength value" % (fy, (" (Fysc_min %.1f ksi = design phi Pysc; Fysc,max used)" % cb["Fysc_min"]) if isinstance(cb.get("Fysc_min"), (int, float)) else "")) if isinstance(fy, (int, float)) else None
        for lab in labels:
            put(lab, "Asc", asc.get(lab, {norm(k): v for k, v in asc.items()}.get(lab)) if isinstance(asc, dict) else asc, "cfg['brb']['Asc']")
            put(lab, "Fysc_ksi", fy, fy_txt)
            put(lab, "KF", cb.get("KF"), "cfg['brb']['KF']")
        for k in ("omega", "beta", "Ry", "KF"):
            if isinstance(cb.get(k), (int, float)) and cb[k] > 0:
                g[k] = float(cb[k]); g.setdefault("src", {})[k] = "cfg['brb']['%s']" % k
        if cb.get("basis"):
            g["basis"] = str(cb["basis"])[:300]
    cd = (calc or {}).get("capacity_design") or {}
    bas = cd.get("BRB_adjusted_strengths") or {}
    if isinstance(bas, dict):                                              # 3. BRB_adjusted_strengths (NL-R2-10)
        for gid, r in (bas.get("by_group") or {}).items():
            if not isinstance(r, dict) or not r.get("label"):
                continue
            lab = r["label"]; where = "capacity_design.BRB_adjusted_strengths.by_group[%s]" % gid
            put(lab, "Asc", r.get("Asc_in2"), where + ".Asc_in2")
            fy = r.get("Fysc_ksi"); fy_used = None
            if isinstance(fy, (list, tuple)) and fy and all(isinstance(v, (int, float)) for v in fy):
                fy_used = float(max(fy)); put(lab, "Fysc_ksi", fy_used, where + ".Fysc_ksi range %s ksi -> Fysc,max %.1f (the end the HR adjusted strengths use)" % (list(fy), fy_used))
            elif isinstance(fy, (int, float)):
                fy_used = float(fy); put(lab, "Fysc_ksi", fy_used, where + ".Fysc_ksi")
            put(lab, "omega", r.get("omega"), where + ".omega"); put(lab, "beta", r.get("beta"), where + ".beta")
            d = out.get(norm(lab)) or {}
            om, fyl, a = d.get("omega") or bas.get("omega"), d.get("Fysc_ksi"), d.get("Asc")
            if isinstance(r.get("T_adj_kip"), (int, float)) and om and fyl and a:     # T = omega Ry Fysc Asc -> Ry
                put(lab, "Ry", round(r["T_adj_kip"] / (om * fyl * a), 3), where + ".T_adj_kip / (omega Fysc Asc)")
        for k in ("omega", "beta", "KF"):
            if k not in g and isinstance(bas.get(k), (int, float)) and bas[k] > 0:
                g[k] = float(bas[k]); g.setdefault("src", {})[k] = "capacity_design.BRB_adjusted_strengths.%s" % k
        if "basis" not in g and (bas.get("Fysc_range_ksi") or bas.get("note")):
            g["basis"] = str(bas.get("Fysc_range_ksi") or bas.get("note"))[:300]
        if "KF" in g:
            for lab in list(out):
                put(lab, "KF", g["KF"], g["src"]["KF"])
    adj = cd.get("adjusted_brace_strengths") or {}                         # 4. older field names
    rows = [r for r in adj.get("by_story") or [] if all(isinstance(r.get(k), (int, float)) and r.get(k) for k in ("Pysc_kip", "adjusted_T_kip", "adjusted_C_kip"))]
    basis = str(adj.get("basis") or "")
    if rows:
        r = rows[0]
        g.setdefault("beta", r["adjusted_C_kip"] / r["adjusted_T_kip"])
        g["omega_Ry"] = r["adjusted_T_kip"] / r["Pysc_kip"]
        mo = re.search(r"omega\s*=\s*([0-9]+(?:\.[0-9]+)?)", basis, re.I)
        if mo:
            g.setdefault("omega", float(mo.group(1)))
            g.setdefault("Ry", g["omega_Ry"] / float(mo.group(1)))
        mb = re.search(r"beta\s*=\s*([0-9]+(?:\.[0-9]+)?)", basis, re.I)
        if mb:
            g["beta_text"] = float(mb.group(1))
        g.setdefault("basis", basis[:300])
    if g:
        out["_global"] = g
    return out


def brb_spec(section: str, L_in: float, prm: dict, A_model: float = None, pkg_data: dict = None) -> BRBSpec:
    """BRB backbone + acceptance (NL-02). Value precedence for every quantity: hinge_params `brb_axial` (the user's
    manually verified values; per-section overrides in brb_axial.sections[label]) > the HR package (calc_package
    inputs / adjusted strengths) > the label (Asc) > documented fallbacks that are FLAGGED.

    Core area Asc: label "BRB-Asc22.5" / package Asc_in2 / sections[label].Asc. The HR truss area (KF*Asc) is used only
    as the stiffness area, never as the yielding area.
    Delta_y (AISC 342-22 Eq. C3-3): with brb_axial.Lcore_over_L and Aconn_over_Acore given,
        Delta_y = Q_CE*Lcore/(E*Acore) + 2*Q_CE*Lconn/(E*Aconn), Lconn = (L - Lcore)/2;
      otherwise Delta_y = Q_CE / K with K = E*A_model/L (the HR elastic truss: A_model = KF*Asc), or E*KF*Asc/L.
    Backbone (Figure C1.1, Table C3.3): B = (Delta_y, Q_CE); C = (Delta_y + a, omega*Q_CE) tension and
    (Delta_y + a, beta*omega*Q_CE) compression; a = b = 13.3 Delta_y, c = 1.0 -> loss of strength at b (E3.2b(c))."""
    g, tmpl = param_group(prm, "brb_axial")
    lab = str(section).strip().upper().replace(" ", "")
    per = {str(k).strip().upper().replace(" ", ""): v for k, v in (g.get("sections") or {}).items()}.get(lab) or {}
    pkd = (pkg_data or {}).get(lab) or {}
    glob = (pkg_data or {}).get("_global") or {}
    flags = []
    if tmpl:
        flags.append("brb_axial group not in this params file -> repository TEMPLATE values (AISC 342-22 Table C3.3 as transcribed; verify)")

    def pick(key, pkg_val=None, pkg_src=None):
        for src, v in (("hinge_params sections[%s]" % lab, per.get(key)), ("hinge_params brb_axial", g.get(key)),
                       ("HR package" + (": " + pkg_src if pkg_src else ""), pkg_val)):
            if v is not None:
                return float(v), src
        return None, None
    psrc = pkd.get("src") or {}; gsrc = glob.get("src") or {}             # NL-R2-10: where each package value came from
    pk = lambda key: (pkd.get(key), psrc.get(key)) if pkd.get(key) is not None else (glob.get(key), gsrc.get(key))

    Asc, src = pick("Asc", *pk("Asc"))
    if Asc is None:
        Asc = SDB.parse_brb_label(lab); src = "label %s" % section if Asc else None
    if not Asc:
        raise ValueError("BRB %r: core area Asc unknown (no number in the label, no package Asc_in2, no "
                         "brb_axial.sections[%r].Asc) -- cannot build the BRB (NL-02)" % (section, section))
    flags.append("Asc=%.3f in2 (%s)" % (Asc, src))
    Fysc, src = pick("Fysc_ksi", *pk("Fysc_ksi"))
    if Fysc is None:
        raise ValueError("BRB %r: core yield stress Fysc unknown (set brb_axial.Fysc_ksi, or a package Fysc: members inputs "
                         "Fysc_ksi, cfg['brb']['Fysc'] or capacity_design.BRB_adjusted_strengths) -- refusing to guess (NL-02)" % section)
    flags.append("Fysc=%.1f ksi (%s)" % (Fysc, src))
    omega, s_om = pick("omega", *pk("omega"))
    beta, s_be = pick("beta", *pk("beta"))
    Ry, s_ry = pick("Ry", *pk("Ry"))
    fb = g.get("omega_beta_fallback") or (template_params().get("brb_axial") or {}).get("omega_beta_fallback") or {}
    if omega is None:
        omega, s_om = float(fb.get("omega", 1.3)), "FALLBACK AISC 342-22 C3.3a.1 linear-analysis value (no test data supplied)"
    if beta is None:
        beta, s_be = float(fb.get("beta", 1.1)), "FALLBACK AISC 342-22 C3.3a.1 linear-analysis value (no test data supplied)"
    if Ry is None:
        Ry, s_ry = 1.0, "FALLBACK 1.0 (no Ry supplied; Fysc taken as expected)"
    for nm_, v, s_ in (("omega", omega, s_om), ("beta", beta, s_be), ("Ry", Ry, s_ry)):
        flags.append("%s=%.3f (%s)" % (nm_, v, s_))
    if "FALLBACK" in (s_om or "") or "FALLBACK" in (s_be or ""):
        flags.append("WARNING: omega/beta not from qualification testing -- AISC 342-22 C3.3a.1 permits 1.3/1.1 for LINEAR analysis only")
    PS.mark_used(prm, "brb_axial", [x for x, s_ in (("omega (FALLBACK 1.3, C3.3a.1 linear-analysis value)", s_om),
                                                     ("beta (FALLBACK 1.1, C3.3a.1 linear-analysis value)", s_be),
                                                     ("Ry (FALLBACK 1.0)", s_ry)) if "FALLBACK" in (s_ or "")],
                 from_template=tmpl)
    Fye = Ry * Fysc
    Qce = Fye * Asc                                                       # C3.3a.1: P_CE = T_CE = A_core * Fye
    KF, s_kf = pick("KF", *pk("KF"))
    Lc_L = g.get("Lcore_over_L", per.get("Lcore_over_L")); Ac_r = g.get("Aconn_over_Acore", per.get("Aconn_over_Acore"))
    if Lc_L and Ac_r:
        Lcore = float(Lc_L) * L_in; Lconn = 0.5 * (L_in - Lcore); Aconn = float(Ac_r) * Asc
        dy = Qce * Lcore / (E_KSI * Asc) + 2.0 * Qce * Lconn / (E_KSI * Aconn)          # Eq. C3-3
        flags.append("Delta_y by Eq. C3-3 (Lcore/L=%.3f, Aconn/Acore=%.2f)" % (float(Lc_L), float(Ac_r)))
    elif KF is not None:
        dy = Qce * L_in / (E_KSI * KF * Asc)
        flags.append("Delta_y = Q_CE*L/(E*KF*Asc), KF=%.3f (%s) -- Eq. C3-3 with core/connection lengths folded into KF" % (KF, s_kf))
    elif A_model:
        dy = Qce * L_in / (E_KSI * A_model)
        flags.append("Delta_y = Q_CE*L/(E*A_model), A_model=%.3f in2 = HR elastic truss area (KF=%.3f implied)" % (A_model, A_model / Asc))
    else:
        dy = Qce * L_in / (E_KSI * Asc)
        flags.append("WARNING: no KF / truss area / core length -> Delta_y over the full work-point length with A=Asc (too flexible)")
    K = Qce / dy
    A_el = float(A_model) if A_model else Asc * (KF or 1.0)
    num = lambda k, d: float(_gv(g, k, "brb_axial", d))
    a, b, c = num("a_over_dy", 13.3) * dy, num("b_over_dy", 13.3) * dy, num("c_residual", 1.0)
    IO, LS, CP = (1 + num("IO_over_dy", 3.0)) * dy, (1 + num("LS_over_dy", 10.0)) * dy, (1 + num("CP_over_dy", 13.3)) * dy
    mat = str(_gv(g, "material", "brb_axial", "Steel4"))
    flags.append("BRB C3.3: Q_CE=%.0f kip, Delta_y=%.3f in, point C T=%.0f / C=%.0f kip at %.2f in; IO/LS/CP (total) %.2f/%.2f/%.2f in; %s"
                 % (Qce, dy, omega * Qce, beta * omega * Qce, dy + a, IO, LS, CP, mat))
    return BRBSpec("brace", section, L_in, A_el, 0.0, 0.0, Fye, Qce, Qce, dy, dy,
                   a_c=dy + a, b_c=dy + b, c_c=c, a_t=dy + a, b_t=dy + b, c_t=c,
                   IO=IO, LS=LS, CP=CP, IO_t=IO, LS_t=LS, CP_t=CP, slenderness_class="BRB", flags=tuple(flags),
                   Asc=Asc, Fysc_ksi=Fysc, Ry=Ry, omega=omega, beta=beta, K_axial=K, A_model=A_el, material=mat,
                   sources=(s_om, s_be, s_ry))


INNER_MAT_OFFSET = 3_000_000      # base material of a wrapped (MinMax / Fatigue) material: tag + offset


def make_brb_material(tag: int, b: BRBSpec, prm: dict) -> str:
    """Uniaxial stress-strain material for a BRB corotTruss of area b.A_model and length b.L_in
    (stress = force / A_model, strain = deformation / L).

    "Steel4" (default; Zsarnoczay's BRB material): asymmetric kinematic hardening calibrated so the MONOTONIC curve
       passes through B (Q_CE at Delta_y) and C: tension omega*Q_CE, compression beta*omega*Q_CE at Delta_y + a
       (b_k = (omega - 1)/a_over_dy, b_kc = (beta*omega - 1)/a_over_dy); Bauschinger loops (R0); wrapped in MinMax at the
       total deformation Delta_y + b (Table C3.3 b) -> strength lost beyond b (Figure C1.1 point E).
    "Hysteretic": exact trilinear C3.3 envelope with the E3.2b(c) post-b descent at the negative elastic slope to ~0."""
    import openseespy.opensees as ops
    g, _ = param_group(prm, "brb_axial")
    A, L = b.A_model, b.L_in
    E_mat = b.K_axial * L / A                                    # material modulus reproducing K = Q_CE / Delta_y
    fy = b.Pye_kip / A
    ey = b.dT / L
    a_dy = (b.a_t - b.dT) / b.dT
    if b.material.lower() == "hysteretic":
        s1, e1 = fy, ey
        e2 = b.a_t / L
        e3 = e2 + 0.98 * b.omega * fy / E_mat
        e3n = e2 + 0.98 * b.beta * b.omega * fy / E_mat
        ops.uniaxialMaterial("Hysteretic", tag, s1, e1, b.omega * fy, e2, 0.02 * b.omega * fy, e3,
                             -s1, -e1, -b.beta * b.omega * fy, -e2, -0.02 * b.beta * b.omega * fy, -e3n, 1.0, 1.0, 0.0, 0.0, 0.0)
        return "Hysteretic"
    R0 = float(_gv(g, "R0", "brb_axial", 20.0))
    bk = max((b.omega - 1.0) / a_dy, 1e-5); bkc = max((b.beta * b.omega - 1.0) / a_dy, 1e-5)
    inner = tag + INNER_MAT_OFFSET
    ops.uniaxialMaterial("Steel4", inner, fy, E_mat, "-asym", "-kin", bk, R0, 0.925, 0.15, bkc, R0, 0.925, 0.15)
    eu = b.b_t / L
    ops.uniaxialMaterial("MinMax", tag, inner, "-min", -eu, "-max", eu)
    return "Steel4+MinMax"


# =========================================================================== NL-03 EBF links
@dataclass
class LinkShearSpec:
    """Shear spring of an EBF link (AISC 342-22 C2 / E2; Table C2.4). Deformation unit = relative transverse displacement
    of the link ends (in); the acceptance limits are the Table C2.4 plastic shear deformations (rad) x e, so a D/C on
    this spring is the link plastic rotation gamma_p over the permissible gamma_p.
    theta_y (Delta_y), a_pl, b_pl, IO, LS, CP: inches (plastic, beyond Delta_y) -- the monitors subtract V/K0."""
    member: str
    section: str
    e_in: float
    Fye_ksi: float
    Vp_kip: float          # V_CE = Vpe = 0.6 Fye Alw
    Mp_kipin: float        # M_CE = Zx Fye
    rho: float             # e / (M_CE / V_CE)
    link_class: str        # "shear" | "intermediate" | "flexure"
    Ks: float              # spring stiffness G*As/e (kip/in)
    Ke: float              # link elastic shear stiffness 12EI/(e^3 (1+eta)) (Eq. C-E2-1) -- the alpha_h reference
    theta_y: float         # Delta_y = Vp / Ks (in)
    a_pl: float
    b_pl: float
    c_res: float
    Vc_over_Vy: float
    IO: float; LS: float; CP: float
    gamma: dict = None     # the rotation limits actually applied (rad)
    flags: tuple = ()

    def as_dict(self):
        return asdict(self)


def _interp_factors(rho: float, g: dict):
    """(f_shear, f_flex): weights of the Table C2.4 (shear) and Table C2.2 (flexure) values for e/(M/V) = rho.
    AISC 342-22 C2.1: shear-controlled rho <= 1.6, flexure-controlled rho >= 2.6, shear-flexure between. The tables'
    footnotes (Table C2.2 [c]: 'Linearly interpolate values to 0.0 when Lv <= 1.6 MCE/VCE') give the linear transition;
    the C2.4 counterpart is taken as the mirror image. mode "none" applies both tables unscaled."""
    mode = str(g.get("interpolation", "linear_1.6_2.6")).lower()
    if mode == "none":
        return 1.0, 1.0
    f_flex = min(max((rho - 1.6) / 1.0, 0.0), 1.0)
    return 1.0 - f_flex, f_flex


def link_specs(section: str, e_in: float, prm: dict):
    """(LinkShearSpec, HingeSpec for the two flexural end springs, info dict) for an EBF link of length e.

    Expected strengths (C2.3a): Fye = Ry*Fy (hinge_params material); V_CE = 0.6 Fye Alw (Alw = (d-2tf) tw), M_CE = Zx Fye.
    Classification (C2.1): rho = e/(M_CE/V_CE): <= 1.6 shear, >= 2.6 flexure, else shear-flexure.
    Shear spring: Ks = G*As/e (As = d*tw, Commentary Eq. C-E2-2) so the series flexure + spring stiffness equals Eq.
    C-E2-1. Backbone (Figure C1.1): B (Vp, Delta_y = Vp/Ks), C = B + a*e with slope alpha_h*Ke (C2.4a.2.b: alpha_h = 6%
    of the elastic slope permitted), capped at Vc_over_Vy_max*Vp; descent to c*Vp; strength lost beyond b*e.
    Flexural end springs: Table C2.2 row 1 (a = 9 theta_y, b = 11 theta_y, c = 0.6, IO 0.25a, LS a, CP b) with
    theta_y = Mp e/(6EI), scaled by f_flex; for a shear-controlled link the flexural acceptance tends to 0, i.e. flexural
    yielding of a shear link is not a permitted deformation (limit 1e-5 rad), while the modelling a/b are kept unscaled
    so the spring stays numerically regular."""
    g, tmpl = param_group(prm, "ebf_link")
    PS.mark_used(prm, "ebf_link", from_template=tmpl); PS.mark_used(prm, "material")
    mt = prm.get("material") or {}
    Fye = float(mt.get("Fy_ksi", 50.0)) * float(mt.get("Ry_expected", 1.1))
    lp = SDB.link_shear_props(section, Fye)
    p = SDB.props(section)
    Vp, Mp = lp["Vp"], lp["Mp"]
    rho = e_in / (Mp / Vp)
    cls = "shear" if rho <= 1.6 else ("flexure" if rho >= 2.6 else "intermediate")
    f_sh, f_fl = _interp_factors(rho, g)
    G = 11200.0                                                                  # AISC 342-22 Commentary C-E2-2 (ksi)
    Ks = G * lp["As"] / e_in
    EI = E_KSI * p["Ix"]
    eta = 12.0 * EI / (e_in ** 2 * G * lp["As"])                                 # Eq. C-E2-2
    Ke = 12.0 * EI / (e_in ** 3 * (1.0 + eta))                                   # Eq. C-E2-1
    sh = g.get("shear") or {}
    tsh = (template_params().get("ebf_link") or {}).get("shear") or {}
    gv = lambda k, d: float(sh.get(k, tsh.get(k, d)) if sh.get(k, tsh.get(k, d)) is not None else d)
    a_r, b_r, c_r = gv("a", 0.15), gv("b", 0.17), gv("c", 0.8)
    IO_r, LS_r, CP_r = gv("IO", 0.005), gv("LS", 0.14), gv("CP", 0.16)
    alpha_h, cap = gv("alpha_h", 0.06), gv("Vc_over_Vy_max", 1.5)
    flags = ["link e=%.1f in, rho=e/(Mce/Vce)=%.2f -> %s-controlled (AISC 342-22 C2.1); Vp=%.0f kip, Mp=%.0f kip-in, Fye=%.1f ksi"
             % (e_in, rho, cls, Vp, Mp, Fye)]
    if tmpl:
        flags.append("ebf_link group not in this params file -> repository TEMPLATE values (Tables C2.4 / C2.2 as transcribed; verify)")
    dy = Vp / Ks
    a_in, b_in = a_r * e_in, b_r * e_in
    Vc = min(1.0 + alpha_h * Ke * a_in / Vp, cap)
    flags.append("shear spring: Ks=%.0f kip/in (G*d*tw/e), Ke=%.0f kip/in (Eq. C-E2-1, eta=%.2f), Vc/Vy=%.2f (alpha_h=%.2f, cap %.2f)"
                 % (Ks, Ke, eta, Vc, alpha_h, cap))
    eps = 1e-5
    gam = dict(a=a_r, b=b_r, IO=IO_r * f_sh, LS=LS_r * f_sh, CP=CP_r * f_sh)
    if f_sh < 1.0:
        flags.append("shear-flexure interpolation f_shear=%.2f on the Table C2.4 ACCEPTANCE values (%s); modelling a/b kept as "
                     "printed (physical web capacity)" % (f_sh, g.get("interpolation", "linear_1.6_2.6")))
    shear = LinkShearSpec("link", section, e_in, Fye, Vp, Mp, rho, cls, Ks, Ke, dy,
                          a_pl=a_in, b_pl=b_in, c_res=c_r, Vc_over_Vy=Vc,
                          IO=max(IO_r * f_sh * e_in, eps), LS=max(LS_r * f_sh * e_in, eps), CP=max(CP_r * f_sh * e_in, eps),
                          gamma=gam, flags=tuple(flags))
    fx = g.get("flexure") or {}
    tfx = (template_params().get("ebf_link") or {}).get("flexure") or {}
    fv = lambda k, d: float(fx.get(k, tfx.get(k, d)) if fx.get(k, tfx.get(k, d)) is not None else d)
    ty = Mp * e_in / (6.0 * EI)
    a_f, b_f = fv("a_over_thetay", 9.0) * ty, fv("b_over_thetay", 11.0) * ty
    IOf = max(fv("IO_frac_of_a", 0.25) * a_f * f_fl, eps); LSf = max(fv("LS_frac_of_a", 1.0) * a_f * f_fl, eps)
    CPf = max(fv("CP_frac_of_b", 1.0) * b_f * f_fl, eps)
    fflags = ["link flexural end spring (Table C2.2 row 1, theta_y=Mp e/(6EI)=%.5f), acceptance x f_flex=%.2f%s"
              % (ty, f_fl, " -> flexural yielding of a shear-controlled link not permitted (limit 1e-5 rad)" if f_fl <= 0 else "")]
    lam, cdet, fl = cyclic_lambda("beam", p, Fye, e_in, prm)
    fflags.append(fl)
    flex = HingeSpec("beam", section, e_in, Fye, Mp, ty, a_f, b_f, fv("c", 0.6), fv("Mc_over_My", 1.1),
                     IO=IOf, LS=LSf, CP=CPf, compact=True, flags=tuple(fflags), Lambda=lam, c_det=cdet)
    info = dict(section=section, e_in=round(e_in, 2), rho=round(rho, 3), link_class=cls, Vp_kip=round(Vp, 1), Mp_kipin=round(Mp),
                f_shear=round(f_sh, 3), f_flex=round(f_fl, 3), Ks=round(Ks), Ke=round(Ke), Vc_over_Vy=round(Vc, 3))
    return shear, flex, info


def make_link_shear_material(tag: int, s: LinkShearSpec, prm: dict) -> str:
    """Force-deformation (kip, in) material of the link shear spring: Hysteretic (full, non-pinched loops; shear links
    show stable hysteresis until web fracture) with the Figure C1.1 envelope B-C-D, wrapped in MinMax at Delta_y + b*e
    (loss of strength beyond b). Optional cyclic damage via ebf_link.shear.damage1 / damage2 (Hysteretic damfc1/2)."""
    import openseespy.opensees as ops
    g, _ = param_group(prm, "ebf_link")
    sh = g.get("shear") or {}
    Vy, dy = s.Vp_kip, s.theta_y
    e2 = dy + s.a_pl
    drop = max(float(sh.get("post_cap_frac_of_a", 0.1)) * s.a_pl, 1e-4)
    e3 = e2 + drop
    d1, d2 = float(sh.get("damage1", 0.0) or 0.0), float(sh.get("damage2", 0.0) or 0.0)
    inner = tag + INNER_MAT_OFFSET
    ops.uniaxialMaterial("Hysteretic", inner, Vy, dy, s.Vc_over_Vy * Vy, e2, s.c_res * Vy, e3,
                         -Vy, -dy, -s.Vc_over_Vy * Vy, -e2, -s.c_res * Vy, -e3, 1.0, 1.0, d1, d2, 0.0)
    eu = dy + s.b_pl
    ops.uniaxialMaterial("MinMax", tag, inner, "-min", -eu, "-max", eu)
    return "Hysteretic+MinMax"


# NL-R2-13: lateral systems this module has no element model for. A package declaring one is REFUSED (NOT EVALUATED)
# instead of being modelled with the wrong mechanism: an STMF's truss chords between web joints otherwise look exactly
# like EBF links (both ends are brace work points), and Ex28 ran as an EBF with 1248 chord "links" and a complete-looking
# verdict. The special segment of an STMF (AISC 341-22 E4: chord flexure/shear + X-diagonal yielding/buckling within
# the segment) has no nonlinear model here.
import re as _re
# NL-R2-L2: plate shear walls. The web plates (or the steel-concrete composite wall panels) carry the storey shear;
# the HR model represents them with rigid zones / elastic panels this module has no section for, and there is no
# nonlinear web model here (tension-field strips for an SPSW, a composite wall fibre / panel model for a C-PSW).
# Before NL-R2-L2 the pushover crashed at the model build (fibre section RIGID_ZONE not in aisc_shapes.csv).
_PSW_SYSTEMS = (
    (_re.compile(r"\bSPSW\b|STEEL\s+PLATE\s+SHEAR\s+WALL|SPECIAL\s+PLATE\s+SHEAR\s+WALL", _re.I),
     "special plate shear wall (SPSW, AISC 341-22 F5): the steel web plates, which provide the inelastic deformation "
     "through web-plate (tension-field) yielding, have no nonlinear model in this module (AISC 342-22 C6 steel plate "
     "shear walls not implemented) -- the analysis is NOT EVALUATED"),
    (_re.compile(r"\bC{1,2}-?PSW\b|COMPOSITE\s+PLATE\s+SHEAR\s+WALL|SPEEDCORE", _re.I),
     "composite plate shear wall (C-PSW/CF, CC-PSW/CF; AISC 341-22 H7 / H8): the concrete-filled steel wall panels and "
     "filled composite coupling beams have no nonlinear model in this module -- the analysis is NOT EVALUATED"),
)
UNSUPPORTED_SYSTEMS = (
    (_re.compile(r"\bSTMF\b|SPECIAL\s+TRUSS\s+MOMENT", _re.I),
     "special truss moment frame (STMF): the special segment (AISC 341-22 E4) has no nonlinear element model in this "
     "module, and its truss chords would otherwise be taken as EBF links -- the analysis is NOT EVALUATED"),
) + _PSW_SYSTEMS
# Systems the DDM (GMNIA, steltic_ddm) cannot model either. The STMF is not among them: the DDM models the truss as a
# frame and never takes chords as links (find_links with the declared system, NL-R2-13).
DDM_UNSUPPORTED_SYSTEMS = _PSW_SYSTEMS


def declared_systems(basis) -> list:
    """Every system string the design basis declares: system, and system_X / system_Y where the reader carries them."""
    out = []
    for k in ("system", "system_X", "system_Y"):
        v = getattr(basis, k, None) if basis is not None else None
        if v and str(v) not in out:
            out.append(str(v))
    return out


def unsupported_system(basis, engine: str = "nonlinear") -> str | None:
    """NL-R2-13 / NL-R2-L2: the refusal message when the declared system is one this module cannot model, else None.
    engine="ddm": only the systems the GMNIA design-by-analysis cannot model either (plate shear walls)."""
    for s in declared_systems(basis):
        for rx, why in (DDM_UNSUPPORTED_SYSTEMS if engine == "ddm" else UNSUPPORTED_SYSTEMS):
            if rx.search(s):
                return "system %r not supported: %s." % (s, why)
    return None


class UnsupportedSystem(RuntimeError):
    """Raised by the model builder for a system in UNSUPPORTED_SYSTEMS (NL-R2-13)."""


def is_ebf_system(system) -> bool:
    return bool(system) and any(t in str(system).upper() for t in ("EBF", "ECCENTRIC"))


def find_links(nodes: dict, members: list, prm: dict, system: str = None, notes: list = None) -> dict:
    """EBF link census (NL-03). members: iterable of dicts {tag, kind ('col'|'beam'|'brace'|...), section, n1, n2,
    released (bool: major-axis end release present)}. Returns {tag: info} for the beam segments taken as links:

      split-K / V EBF (centre link, only when the system is declared eccentrically braced -- NL-R2-13): an unreleased
        beam segment whose BOTH end nodes are brace work points and neither end is a column node -- 'the component
        between these points' of AISC 342-22 E2.1. In any other system (truss chords, stacked chevrons) such a segment
        is a beam; it is counted in `notes` and never turned into a link silently;
      D / column-adjacent EBF (only when the system is declared eccentrically braced): an unreleased beam segment from a
        column node to a node where exactly one brace lands and no column, with e <= 2.6 Mp/Vp (a longer segment is a
        flexure-controlled beam and is modelled as one);
      plus hinge_params ebf_link.element_tags (manual), minus ebf_link.exclude_tags. ebf_link.detect = "off" disables
      the automatic rules."""
    import collections
    g, _ = param_group(prm, "ebf_link")
    mode = str(g.get("detect", "auto")).lower()
    manual = {int(t) for t in (g.get("element_tags") or [])}
    excl = {int(t) for t in (g.get("exclude_tags") or [])}
    braces_at = collections.Counter(); col_nodes = set()
    for m in members:
        if m["kind"] == "brace":
            braces_at[m["n1"]] += 1; braces_at[m["n2"]] += 1
        elif m["kind"] == "col":
            col_nodes.update((m["n1"], m["n2"]))
    ebf = is_ebf_system(system)
    n_not_ebf = 0
    mt = prm.get("material") or {}
    Fye = float(mt.get("Fy_ksi", 50.0)) * float(mt.get("Ry_expected", 1.1))
    out = {}
    for m in members:
        if m["kind"] != "beam" or m["tag"] in excl:
            continue
        sec = m.get("section")
        if not sec or str(sec).upper() in ("GHOST", "?"):
            continue
        p1, p2 = nodes[m["n1"]], nodes[m["n2"]]
        e = math.dist(p1, p2)
        rule = None
        if m["tag"] in manual:
            rule = "manual (ebf_link.element_tags)"
        elif mode != "off" and not m.get("released"):
            b1, b2 = braces_at[m["n1"]], braces_at[m["n2"]]
            c1, c2 = m["n1"] in col_nodes, m["n2"] in col_nodes
            if b1 and b2 and not c1 and not c2:
                if ebf:
                    rule = "between brace work points"
                else:                                   # NL-R2-13: not an EBF -> a beam, never a silent link
                    n_not_ebf += 1
                    continue
            elif ebf and ((c1 and not c2 and b2 == 1) or (c2 and not c1 and b1 == 1)):
                try:
                    lp = SDB.link_shear_props(sec, Fye)
                except KeyError:
                    continue
                if e <= 2.6 * lp["Mp"] / lp["Vp"]:
                    rule = "column-adjacent link (EBF, e <= 2.6 Mp/Vp)"
        if rule is None:
            continue
        if abs(p2[2] - p1[2]) > 0.05 * e:
            out[m["tag"]] = dict(tag=m["tag"], section=sec, e_in=e, rule=rule, skipped="link not horizontal -- modelled as a beam (flagged)")
            continue
        try:
            lp = SDB.link_shear_props(sec, Fye)
        except KeyError:
            continue
        rho = e / (lp["Mp"] / lp["Vp"])
        out[m["tag"]] = dict(tag=m["tag"], section=sec, e_in=round(e, 2), rule=rule, rho=round(rho, 3),
                             link_class="shear" if rho <= 1.6 else ("flexure" if rho >= 2.6 else "intermediate"))
    if n_not_ebf and notes is not None:
        notes.append("%d beam segments between two brace work points modelled as BEAMS, not EBF links: the system (%s) is not "
                     "declared eccentrically braced (NL-R2-13); list real links in hinge_params ebf_link.element_tags"
                     % (n_not_ebf, system or "not declared"))
    return out


def find_links_pkg(pkg, prm: dict, member_kind, notes: list = None) -> dict:
    """find_links on a pushover Package (elasticBeamColumn beams; braces may be raw trusses)."""
    mem = []
    for e in pkg.model.elements:
        k = member_kind(pkg, e)
        rel = e.get("release") or []
        mem.append(dict(tag=e["tag"], kind=k, section=pkg.schedule.get(e["tag"], {}).get("section"), n1=e["n1"], n2=e["n2"],
                        released=("-releasey" in rel and int(rel[rel.index("-releasey") + 1]) != 0)))
    sysd = " / ".join(declared_systems(pkg.basis))                 # NL-R2-13: system, system_X, system_Y
    return find_links(pkg.model.nodes, mem, prm, sysd, notes=notes)


def link_census_summary(links: dict) -> dict:
    import collections
    c = collections.Counter((v["section"], v.get("e_in"), v.get("link_class", "?")) for v in links.values() if not v.get("skipped"))
    return dict(n=sum(c.values()), skipped=sum(1 for v in links.values() if v.get("skipped")),
                groups=[dict(section=s, e_in=e, link_class=k, n=n) for (s, e, k), n in sorted(c.items(), key=str)])


# =========================================================================== NL-10 physical-theory brace (NLRHA)
def brace_element_form(prm: dict) -> str:
    """'truss' (Hysteretic corotTruss, the ASCE 41 / AISC 342 backbone -- NSP) or 'physical_theory' (cambered fibre brace
    with Steel02 + Fatigue -- NLRHA when brace_axial.nlrha_element says so and the builder runs for the NLRHA)."""
    if str(prm.get("_analysis", "")).lower() != "nlrha":
        return "truss"
    bp = prm.get("brace_axial") or {}
    v = bp.get("nlrha_element")
    if v is None:
        v = (template_params().get("brace_axial") or {}).get("nlrha_element", "truss")
    return "physical_theory" if str(v).lower() in ("physical_theory", "fibre", "fiber", "fatigue") else "truss"


def brace_fatigue_params(section: str, KLr: float, Fye: float, prm: dict) -> dict:
    """Fatigue (Coffin-Manson) parameters for the physical-theory brace fibres: brace_axial.physical_theory.{eps0, m}
    or the eps0 expression (template: Hsiao, Lehman & Roeder 2012 for rectangular HSS,
    eps0 = 0.291 (KL/r)^-0.484 (w/t)^-0.613 (E/Fy)^0.303, m = -0.3). Flagged as literature values."""
    bp = prm.get("brace_axial") or {}
    pt = dict((template_params().get("brace_axial") or {}).get("physical_theory") or {})
    pt.update(bp.get("physical_theory") or {})
    if not bp.get("physical_theory"):
        PS.mark_used(prm, "brace_axial", ["physical_theory (not in the parameter file: repository TEMPLATE literature values)"])
    else:
        PS.mark_used(prm, "brace_axial")
    flags = []
    B, tdes = _hss_outside_and_tdes(section)
    wt = ((B - 3.0 * tdes) / tdes) if (B and tdes) else 20.0
    if pt.get("eps0") is not None:
        eps0 = float(pt["eps0"]); flags.append("Fatigue eps0=%.4f (supplied)" % eps0)
    else:
        env = dict(KLr=max(KLr, 1.0), wt=max(wt, 1.0), E=E_KSI, Fy=Fye, math=math)
        eps0 = float(eval(pt.get("eps0_expr", "0.091"), {"__builtins__": {}}, env))
        flags.append("Fatigue eps0=%.4f from %s (literature; verify)" % (eps0, pt.get("eps0_expr")))
    m = float(pt.get("m", -0.3))
    return dict(eps0=eps0, m=m, camber=float(pt.get("camber_over_L", 1.0 / 1000.0)), nseg=int(pt.get("nseg", 4)),
                nip=int(pt.get("nip", 4)), b=float(pt.get("hardening", 0.003)), R0=float(pt.get("R0", 20.0)), flags=flags,
                element="forceBeamColumn" if str(pt.get("element", "dispBeamColumn")).lower().startswith("force") else "dispBeamColumn")


def brace_axial_force(tag: int, h: dict) -> float:
    """Axial force (kip, +tension) of a registered brace: the truss's axialForce, or, for a physical-theory brace
    (h['force_ele'] set), the basic axial force of its first fibre segment (the registered tag is a zero-stiffness
    monitor truss that only measures the end-to-end deformation)."""
    import openseespy.opensees as ops
    if h.get("force_ele"):
        f = ops.eleResponse(h["force_ele"], "basicForce")
        return f[0] if f else 0.0
    f = ops.eleResponse(tag, "axialForce")
    return f[0] if f else 0.0
