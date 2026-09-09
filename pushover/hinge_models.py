"""hinge_models.py -- turn (section, length, axial load) into a concentrated-plasticity hinge definition.

Every number comes from hinge_params.json (which the Grok Bot fills from retrieved spec text). This module
only does the arithmetic and hands back a HingeSpec the model builder turns into a ModIMKPeakOriented
uniaxialMaterial. Cyclic deterioration is disabled (monotonic pushover) -- Lambda = 0.
"""
from __future__ import annotations
import json, math, os
from dataclasses import dataclass, asdict
from . import sections_db as SDB

_HERE = os.path.dirname(os.path.abspath(__file__))
E_KSI = 29000.0


def load_params(path: str | None = None) -> dict:
    with open(path or os.path.join(_HERE, "hinge_params.json"), encoding="utf-8") as f:
        return json.load(f)


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

    def as_dict(self):
        return asdict(self)


def _theta_y(Zx, Fye, L, I, axial_factor=1.0):
    return Zx * Fye * L / (6.0 * E_KSI * I) * axial_factor


def _ductility_class(p: dict, Fye: float, prm: dict, Ca: float = 0.0) -> tuple:
    """'highly' | 'moderately' | 'other' from the flange and web width-to-thickness limits in prm["compactness"]
    (AISC 341 Table D1.1 form, Fye in place of RyFy, Ca = PG/Pye for webs). Falls back to the legacy 52/sqrt(Fye) rule."""
    cp = prm.get("compactness")
    if not cp:
        lam_f, lam_w = 52.0 / math.sqrt(Fye), 418.0 / math.sqrt(Fye)
        return ("highly" if (p.get("bf_2tf", 0) <= lam_f and p.get("h_tw", 0) <= lam_w) else "other"), lam_f, lam_w
    k = math.sqrt(E_KSI / Fye)
    f_hd, f_md = cp["flange_hd"] * k, cp["flange_md"] * k
    if Ca <= cp.get("web_Ca_break", 0.114):
        w_hd = cp["web_hd_lowCa"] * (1 - cp.get("web_hd_lowCa_k", 1.04) * Ca) * k
        w_md = cp["web_md_lowCa"] * (1 - cp.get("web_md_lowCa_k", 1.04) * Ca) * k
    else:
        w_hd = max(cp["web_hd_highCa"] * (cp.get("web_hd_highCa_c", 2.68) - Ca), cp["web_hd_floor"]) * k
        w_md = max(cp["web_md_highCa"] * (cp.get("web_md_highCa_c", 2.68) - Ca), cp["web_md_floor"]) * k
    bf, hw = p.get("bf_2tf", 0), p.get("h_tw", 0)
    if bf <= f_hd and hw <= w_hd:
        return "highly", f_hd, w_hd
    if bf <= f_md and hw <= w_md:
        return "moderately", f_md, w_md
    return "other", f_md, w_md


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
        return HingeSpec("beam", section, L_in, Fye, Mce, ty, a, b, c, bp["Mc_over_My"],
                         IO=bp["IO_frac_of_a"] * a, LS=bp["LS_frac_of_b"] * b, CP=bp["CP_frac_of_b"] * b, compact=compact, flags=tuple(flags))
    red = 1.0 if compact else bp.get("noncompact_reduction", 0.5)
    if not compact:
        flags.append("%s-ductile: flat %.2f reduction applied (interpolate per standard)" % (duct, red))
    ty = _theta_y(p["Zx"], Fye, L_in, p["Ix"])
    a, b = bp["a_over_thetay"] * ty * red, bp["b_over_thetay"] * ty * red
    return HingeSpec("beam", section, L_in, Fye, p["Zx"] * Fye, ty, a, b, bp["c_residual"], bp["Mc_over_My"],
                     IO=bp["IO_over_thetay"] * ty * red, LS=bp["LS_over_thetay"] * ty * red,
                     CP=bp["CP_over_thetay"] * ty * red, compact=compact, flags=tuple(flags))


def column_hinge(section: str, L_in: float, PG_kip: float, prm: dict) -> HingeSpec:
    p = SDB.props(section); cp = prm["column_flexure"]; mt = prm["material"]
    Fye = mt["Fy_ksi"] * mt["Ry_expected"]
    Pye = p["A"] * Fye
    r = max(0.0, PG_kip) / Pye
    flags = []
    env = dict(PG=max(0.0, PG_kip), Pye=Pye, L=L_in, ry=p["ry"], h=p["d"] - 2 * p["tf"], tw=p["tw"], math=math)
    if r >= cp["force_controlled_above_P_over_Pye"]:
        flags.append("PG/Pye=%.2f >= %.2f -> FORCE-CONTROLLED column (no hinge; check P vs PCL)" % (r, cp["force_controlled_above_P_over_Pye"]))
        return HingeSpec("column", section, L_in, Fye, p["Zx"] * Fye, _theta_y(p["Zx"], Fye, L_in, p["Ix"], 1 - r),
                         0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, True, r, True, tuple(flags))
    duct, _, _ = _ductility_class(p, Fye, prm, Ca=r)
    a = max(cp.get("a_min", 0.0), eval(cp["a_expr"].replace("PG/Pye", "(PG/Pye)").replace("h/tw", "(h/tw)").replace("L/ry", "(L/ry)"), {}, env))
    b = max(0.0, eval(cp["b_expr"].replace("PG/Pye", "(PG/Pye)").replace("h/tw", "(h/tw)").replace("L/ry", "(L/ry)"), {}, env))
    if "a_max" in cp: a = min(a, cp["a_max"])
    if "b_max" in cp: b = min(b, cp["b_max"])
    c = max(0.0, eval(cp["c_expr"].replace("PG/Pye", "(PG/Pye)"), {}, env))
    if duct != "highly":
        f = cp.get("non_highly_ductile_reduction", 0.5); a, b = a * f, b * f
        flags.append("%s-ductile column section: x%.2f applied (interpolate to the non-moderately-ductile row per standard)" % (duct, f))
    red = eval(cp["Mpce_axial_reduction"].replace("PG/Pye", "(PG/Pye)"), {}, env)
    Mpe = p["Zx"] * Fye * red
    ty = _theta_y(p["Zx"], Fye, L_in, p["Ix"], red if cp.get("theta_y_uses_Mpce") else 1 - r)   # Eq. C3-15 with M_CE (tau_b = 1)
    if p.get("h_tw_approx"):
        flags.append("h/tw approximated as (d-2tf)/tw")
    return HingeSpec("column", section, L_in, Fye, Mpe, ty, a, b, c, cp["Mc_over_My"],
                     IO=cp["IO_frac_of_a"] * a, LS=cp["LS_frac_of_b"] * b, CP=cp["CP_frac_of_b"] * b,
                     force_controlled=False, PG_over_Pye=r, compact=True, flags=tuple(flags))


def modimk_args(h: HingeSpec, K0: float, post_cap_ratio: float = 0.15) -> list:
    """ModIMKPeakOriented argument list (after the tag). Monotonic: all Lambda = 0 (no cyclic deterioration).
    theta_pc is set so the descent from Mc reaches the residual c*My over post_cap_ratio*a of rotation."""
    My = h.Mpe_kipin
    Mc = h.Mc_over_My * My
    a_s = ((Mc - My) / max(h.a_pl, 1e-6)) / K0          # hardening ratio relative to K0
    a_s = min(max(a_s, 1e-4), 0.05)
    drop = h.Mc_over_My - h.c_res
    theta_pc = max(post_cap_ratio * h.a_pl * h.Mc_over_My / max(drop, 1e-3), 1e-3)
    return [K0, a_s, a_s, My, -My, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0,
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
    new = [K0_, theta_p, theta_pc, theta_u, My, h.Mc_over_My, res,
           theta_p, theta_pc, theta_u, My, h.Mc_over_My, res,
           0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
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
    """Parse rectangular HSS label like HSS12X12X5/8 -> (B_out, tdes). A500 design wall tdes=0.93*tnom (AISC Manual)."""
    s = section.strip().upper().replace(" ", "")
    if not s.startswith("HSS"):
        return None, None
    body = s[3:]
    parts = body.split("X")
    if len(parts) < 3:
        return None, None
    try:
        B = float(parts[0]); H = float(parts[1])
        t_tok = parts[2]
        if "/" in t_tok:
            a, b = t_tok.split("/", 1); tnom = float(a) / float(b)
        else:
            tnom = float(t_tok)
    except ValueError:
        return None, None
    tdes = 0.93 * tnom
    return max(B, H), tdes


def brace_spec(section: str, L_in: float, prm: dict) -> BraceSpec:
    p = SDB.props(section); bp = prm["brace_axial"]
    Fye = bp["Fy_ksi"] * bp["Ry_expected"]
    A, r = p["A"], min(p["rx"], p["ry"])
    KLr = bp["K_effective"] * L_in / r
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
        env = dict(lam_ratio=lam_ratio, slend=slend, KLr=KLr, Fye=Fye, E=E_KSI, math=math)
        n_c = float(eval(bp["compression"]["n_expr"], {"__builtins__": {}}, env))
        n_t = float(eval(bp["tension"]["n_expr"], {"__builtins__": {}}, env))
        n_c = max(n_c, 1e-6); n_t = max(n_t, 1e-6)
        # Map C3.4 (d,f) to Hysteretic: brief post-buckling plateau then residual at total d = n*delta.
        a_plateau = float(bp["compression"].get("a_plateau_over_dc", 0.05))
        a_c = a_plateau * dc
        b_c = n_c * dc
        c_c = float(bp["compression"]["f_residual"])
        a_t = float(bp["tension"].get("a_plateau_over_dT", 0.05)) * dT
        b_t = n_t * dT
        c_t = float(bp["tension"]["f_residual"])
        IO = float(bp["compression"].get("IO_over_dc", 1.5)) * dc
        LS = float(bp["compression"].get("LS_frac_of_n", 0.7)) * n_c * dc
        CP = n_c * dc
        IO_t = float(bp["tension"].get("IO_over_dT", 1.5)) * dT
        LS_t = float(bp["tension"].get("LS_frac_of_n", 0.7)) * n_t * dT
        CP_t = n_t * dT
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
