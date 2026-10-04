"""params_schema.py -- the ASCE 41-23 / AISC 342-22 schema of the component-parameter file (hinge_params.json).

What this module owns, and why it is separate from hinge_models.py (which only does the arithmetic):

1. PROVENANCE (register item NL-19). The user verifies every component value against the standard in a
   manual step and supplies it in the parameter file. A value that did NOT come from the user -- a
   template placeholder, a value Collect copied from the template, a MOCK value -- must never travel under
   a corpus citation. `annotate()` works out, per group, which value fields are not user-supplied:
     * the group's explicit `unverified` list (the repository template ships every field in it; the user
       removes a field once checked against the printed table; "*" = the whole group);
     * a group written by `snl collect` (it has a `quotes` dict): every value field without a quote;
     * a group with no `source`, or one whose source/basis says PLACEHOLDER / TEMPLATE / MOCK;
     * the superseded column P-M reduction 1.18(1 - P/Py) <= 1 (Equation C-C3-5 of the AISC 342-22
       Commentary, "used by earlier editions of ASCE/SEI 41") -- replaced here by AISC 342-22 Eqs. C3-5/C3-6.
   When a hinge builder USES a group with such fields, `mark_used()` records it and drops the effective
   `verified` flag, so every report that reads prm["verified"] shows the red UNVERIFIED banner and
   `unverified_text()` names the fields.

2. THE PRINTED FORM of the tables (NL-06). A cell may be given exactly as printed: "0.25 a", "a", "b",
   "0.75 b", "9 θy", "1.5 Δc", "0.7 n Δc", "n Δc" -- or in the numeric field form (IO_frac_of_a,
   a_over_thetay, IO_over_dc, LS_frac_of_n ...). Both are read by `row_value()` / `c34_side()`.

3. ROW SELECTION BY SECTION (NL-14). AISC 342-22 Tables C2.2 and C3.6 print two rows -- "1. Highly ductile
   (lambda <= lambda_hd)" and "2. Non-moderately ductile (lambda >= lambda_md)" -- and "3. Other: linear
   interpolation between the values on lines 1. and 2. for flange [...] and web slenderness [...], and the
   lowest resulting value shall be used". There is no "moderately ductile" row and the row does not depend
   on the seismic system. `element_slenderness()` gives (lambda, lambda_hd, lambda_md) for the flange and
   the web from AISC 341-22 Table D1.1b with RyFy -> Fye and alpha_s Pr -> PG (AISC 342-22 Table C3.6
   note [b]); `interpolate_rows()` applies line 3.
"""
from __future__ import annotations

import math
import re

SCHEMA = "ASCE41-23/AISC342-22"
E_KSI = 29000.0
GROUPS = ("material", "beam_flexure", "column_flexure", "brace_axial",
          # nl-elements groups (NL-02 / NL-03 / NL-10): never collected, supplied manually by the user
          "brb_axial", "ebf_link", "cyclic_deterioration")
TEMPLATE_NOTE = "(group not in the parameter file: repository TEMPLATE values used)"

# keys inside a group that describe the values rather than being values the engine computes with
_META = {"basis", "note", "notes", "source", "quotes", "mode", "table", "unverified", "element", "decode_note",
         "Lb_note", "rbs_note", "modifier_note", "Mc_note", "modifier_checks", "modifiers", "used",
         # switches / topology, not component values
         "nlrha_element", "detect", "element_tags", "exclude_tags", "sections_note",
         # design facts read from the HR package, not table values
         "Lb_over_ry", "Lb_divisor", "rbs_c_in", "rbs_c_frac_bf"}
_FLAG_WORDS = re.compile(r"PLACEHOLDER|TEMPLATE|\bMOCK\b", re.I)

# AISC 342-22 Commentary Eq. C-C3-5 (superseded; "Earlier editions of ASCE/SEI 41 used Equation C-C3-5")
_SUPERSEDED_MPCE = re.compile(r"1\.18\s*\*?\s*\(\s*1\s*-\s*\(?\s*PG\s*/\s*Pye")
# AISC 342-22 Section C3.4a.2 Eqs. C3-5 / C3-6 (major axis): (1 - |P|/2Pye) Mpex for |P|/Pye < 0.2 kappa,
# 9/8 (1 - |P|/Pye) Mpex otherwise. The two lines cross at |P|/Pye = 0.2, so with kappa = 1 the piecewise
# form equals the minimum of the two (the lower, i.e. conservative, reading for kappa < 1 as well).
MPCE_AISC342 = "min(1 - PG/(2*Pye), 9/8*(1 - PG/Pye))"


# ---------------------------------------------------------------- provenance (NL-19)
def value_fields(blk: dict, prefix: str = "") -> list:
    """Dotted names of the value leaves of a group (numbers, expressions, printed cells)."""
    out = []
    for k, v in (blk or {}).items():
        if k in _META or k.startswith("_") or k.endswith("_note"):
            continue
        name = prefix + k
        if isinstance(v, dict):
            out += value_fields(v, name + ".")
        elif isinstance(v, (int, float, str)) and not isinstance(v, bool) or v is None:
            out.append(name)
    return out


def _covered(field: str, listed) -> bool:
    for x in listed:
        if x == "*" or field == x or field.startswith(x + "."):
            return True
    return False


def group_unverified(gid: str, blk: dict, top_source: str = "") -> list:
    """The value fields of one group that the user did not supply (see the module docstring).
    `top_source` is the file's top-level `source`, used for a group without its own (files written
    before this schema cited once, at the top)."""
    if not isinstance(blk, dict):
        return []
    fields = value_fields(blk)
    out = set()
    listed = blk.get("unverified")
    if isinstance(listed, str):
        listed = [listed]
    if listed:
        out |= {f for f in fields if _covered(f, listed)}
    if isinstance(blk.get("quotes"), dict):                      # written by `snl collect`
        q = blk["quotes"]
        out |= {f for f in fields if f not in q}
    src = str(blk.get("source") or "").strip() or str(top_source or "").strip()
    if not src or _FLAG_WORDS.search(src) or _FLAG_WORDS.search(str(blk.get("basis") or "")):
        out |= set(fields)
    expr = blk.get("Mpce_axial_reduction")
    if gid == "column_flexure" and isinstance(expr, str) and _SUPERSEDED_MPCE.search(expr):
        out.add("Mpce_axial_reduction")
    return sorted(out)


def annotate(prm: dict) -> dict:
    """Record per-group unverified fields (prm['_unverified']) and replace the superseded column P-M
    reduction by AISC 342-22 Eqs. C3-5/C3-6. Mutates and returns prm; the file on disk is not touched."""
    if not isinstance(prm, dict) or prm.get("_annotated"):
        return prm
    prm["_annotated"] = True
    prm["_verified_claimed"] = bool(prm.get("verified"))
    unv = {}
    for gid in GROUPS:
        f = group_unverified(gid, prm.get(gid), prm.get("source") if prm.get("verified") else "")
        if f:
            unv[gid] = f
    prm["_unverified"] = unv
    cf = prm.get("column_flexure")
    if isinstance(cf, dict) and isinstance(cf.get("Mpce_axial_reduction"), str) and _SUPERSEDED_MPCE.search(cf["Mpce_axial_reduction"]):
        prm.setdefault("_engine_notes", []).append(
            "column_flexure.Mpce_axial_reduction '%s' is the superseded ASCE 41 form (AISC 342-22 Commentary Eq. C-C3-5); "
            "replaced by AISC 342-22 Eqs. C3-5/C3-6: %s" % (cf["Mpce_axial_reduction"], MPCE_AISC342))
        cf["_Mpce_superseded"] = cf["Mpce_axial_reduction"]
        cf["Mpce_axial_reduction"] = MPCE_AISC342
    prm.setdefault("_used_unverified", {})
    return prm


def mark_used(prm: dict, gid: str, extra: list | None = None, from_template: bool = False) -> None:
    """A hinge builder used group `gid`: if any of its fields is not user-supplied, the run's parameters
    are UNVERIFIED (the effective flag every report reads), whatever the file claimed. `from_template`: the
    builder had to take the group from the repository template because the file lacks it (brb_axial,
    ebf_link, cyclic_deterioration, brace_axial.physical_theory) -- always unverified."""
    if not isinstance(prm, dict):
        return
    if not prm.get("_annotated"):
        annotate(prm)
    fields = ([] if from_template else list((prm.get("_unverified") or {}).get(gid) or [])) + list(extra or [])
    if from_template:
        fields.insert(0, TEMPLATE_NOTE)
    if not fields:
        return
    used = prm.setdefault("_used_unverified", {})
    cur = used.setdefault(gid, [])
    for f in fields:
        if f not in cur:
            cur.append(f)
    prm["verified"] = False


def unverified_text(prm: dict, used_only: bool = True, limit: int = 600) -> str:
    """'beam_flexure: all 14 values; column_flexure: Mc_over_My, ...' -- for banners and summaries.
    used_only: only the groups a hinge builder actually used in this run (falls back to all groups)."""
    m = (prm.get("_used_unverified") if used_only else None) or prm.get("_unverified") or {}
    parts = []
    for g, v in m.items():
        if not v:
            continue
        allf = set(value_fields(prm.get(g) or {}))
        plain = [f for f in v if f in allf]
        if allf and set(plain) >= allf:
            txt = "all %d values (no user-verified value)" % len(allf)
            rest = [f for f in v if f not in allf]
            if rest:
                txt += "; " + ", ".join(rest)
        else:
            txt = ", ".join(v)
        parts.append("%s: %s" % (g, txt))
    s = "; ".join(parts)
    return s[:limit] + ("…" if len(s) > limit else "")


# ---------------------------------------------------------------- the printed form (NL-06)
_THETA = r"(?:θ\s*y|theta_?y|thy|q\s*y)"
_DELTA_C = r"(?:Δ\s*c|∆\s*c|delta_?c|dc|D\s*c)"
_DELTA_T = r"(?:Δ\s*T|∆\s*T|delta_?T|dT|D\s*T)"


def parse_printed(cell) -> tuple:
    """A printed cell -> (coefficient, base). base in {None, 'a', 'b', 'thetay', 'n', 'n_dc', 'n_dT', 'dc', 'dT'}.
    '0.25 a' -> (0.25, 'a'); 'b' -> (1.0, 'b'); '9 θy' -> (9.0, 'thetay'); '0.7 n Δc' -> (0.7, 'n_dc');
    'n ∆T' -> (1.0, 'n_dT'); '1.5 Δc' -> (1.5, 'dc'); 0.6 -> (0.6, None)."""
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return float(cell), None
    s = str(cell or "").strip()
    s = re.sub(r"^[a-zA-Z]+\s*=\s*", "", s) if re.match(r"^(a|b|c|d|f)\s*=", s) else s
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)?\s*(.*?)\s*$", s)
    coef = float(m.group(1)) if m and m.group(1) else 1.0
    rest = (m.group(2) if m else s).strip()
    if not rest:
        return coef, None
    if re.fullmatch(r"n\s*" + _DELTA_C, rest):
        return coef, "n_dc"
    if re.fullmatch(r"n\s*" + _DELTA_T, rest):
        return coef, "n_dT"
    if re.fullmatch(_DELTA_C, rest):
        return coef, "dc"
    if re.fullmatch(_DELTA_T, rest):
        return coef, "dT"
    if re.fullmatch(_THETA, rest):
        return coef, "thetay"
    if rest in ("a", "b", "n"):
        return coef, rest
    raise ValueError("cannot read the printed cell %r" % (cell,))


def row_value(row: dict, name: str, a=None, b=None, thetay=None):
    """One of a, b, c, IO, LS, CP from a table row in either form. Returns None when the row has none."""
    if name in row and row[name] is not None:
        coef, base = parse_printed(row[name])
        ref = {None: 1.0, "a": a, "b": b, "thetay": thetay}.get(base)
        if ref is None:
            raise ValueError("%s = %r needs %s, which is not known yet" % (name, row[name], base))
        return coef * ref
    if name == "c":
        for k in ("c_residual", "c"):
            if k in row and row[k] is not None:
                return float(row[k])
        return None
    for suffix, ref in (("_over_thetay", thetay), ("_frac_of_a", a), ("_frac_of_b", b)):
        k = name + suffix
        if k in row and row[k] is not None:
            if ref is None:
                raise ValueError("%s needs %s" % (k, suffix))
            return float(row[k]) * ref
    return None


def c34_side(side: dict) -> dict:
    """Table C3.4 compression or tension block -> IO_over (x Delta), LS_frac_of_n, CP_frac_of_n, f.
    Accepts the printed cells ('1.5 Δc', '0.7 n Δc', 'n Δc', f) or the field form (IO_over_dc / IO_over_dT,
    LS_frac_of_n, CP_frac_of_n, f_residual)."""
    out = {}
    io = side.get("IO")
    if io is not None:
        coef, base = parse_printed(io)
        if base not in ("dc", "dT", None):
            raise ValueError("Table C3.4 IO cell %r: expected a multiple of the yield deformation" % (io,))
        out["IO_over"] = coef
    else:
        out["IO_over"] = float(side.get("IO_over_dc", side.get("IO_over_dT", 1.5)))
    for lvl, dflt in (("LS", 0.7), ("CP", 1.0)):
        cell = side.get(lvl)
        if cell is not None:
            coef, base = parse_printed(cell)
            if base not in ("n_dc", "n_dT", "n"):
                raise ValueError("Table C3.4 %s cell %r: expected a fraction of n Delta" % (lvl, cell))
            out[lvl + "_frac_of_n"] = coef
        else:
            out[lvl + "_frac_of_n"] = float(side.get(lvl + "_frac_of_n", dflt))
    out["f"] = float(side.get("f", side.get("f_residual")))
    return out


# ---------------------------------------------------------------- row selection by section (NL-14)
def _web_case(prm: dict) -> str:
    """'moment_frame' (AISC 341-22 Table D1.1b case 11), 'other' (case 14) or 'unknown'."""
    cp = prm.get("compactness") or {}
    wc = str(cp.get("web_case") or "auto").lower()
    if wc in ("moment_frame", "other"):
        return wc
    sysname = str(prm.get("_system") or "").upper()
    if not sysname:
        return "unknown"
    return "moment_frame" if any(s in sysname for s in ("SMF", "IMF", "OMF", "MOMENT", "STMF")) else "other"


def element_slenderness(p: dict, Fye: float, Ca: float, prm: dict) -> tuple:
    """-> ({'flange': (lam, lam_hd, lam_md), 'web': (lam, lam_hd, lam_md)}, note) for a W-shape.

    Default limits: AISC 341-22 Table D1.1b (read in the corpus), with RyFy -> Fye and Ca = PG/Pye
    (AISC 342-22 Table C3.6 note [b]):
      case 1  flanges of rolled I-shapes   lam_hd = 0.30 sqrt(E/Fye)          lam_md = 0.38 sqrt(E/Fye)
      case 11 webs, moment-frame beams/cols lam_hd = 2.5 (1-Ca)^2.3 sqrt(E/Fye) lam_md = 5.4 (1-Ca)^2.3 sqrt(E/Fye)
      case 14 webs, all other members  Ca <= 0.113: 2.45 (1-1.04Ca) / 3.76 (1-3.05Ca) sqrt(E/Fye)
                                       Ca >  0.113: 2.26 (1-0.38Ca) / 2.61 (1-0.49Ca) sqrt(E/Fye), both >= 1.56 sqrt(E/Fye)
    A user `compactness` block overrides them (the legacy AISC 341-16 form with flange_hd / web_hd_lowCa ...
    is still read, for files written before this schema)."""
    k = math.sqrt(E_KSI / Fye)
    lam_f, lam_w = p.get("bf_2tf", 0.0), p.get("h_tw", 0.0)
    cp = prm.get("compactness") or {}
    Ca = max(0.0, float(Ca or 0.0))
    if "web_hd_lowCa" in cp:                                     # legacy (pre-ASCE 41-23 schema) block
        f_hd, f_md = cp["flange_hd"] * k, cp["flange_md"] * k
        if Ca <= cp.get("web_Ca_break", 0.114):
            w_hd = cp["web_hd_lowCa"] * (1 - cp.get("web_hd_lowCa_k", 1.04) * Ca) * k
            w_md = cp["web_md_lowCa"] * (1 - cp.get("web_md_lowCa_k", 1.04) * Ca) * k
        else:
            w_hd = max(cp["web_hd_highCa"] * (cp.get("web_hd_highCa_c", 2.68) - Ca), cp["web_hd_floor"]) * k
            w_md = max(cp["web_md_highCa"] * (cp.get("web_md_highCa_c", 2.68) - Ca), cp["web_md_floor"]) * k
        return {"flange": (lam_f, f_hd, f_md), "web": (lam_w, w_hd, w_md)}, "user compactness block (legacy form)"
    f_hd = float(cp.get("flange_hd", 0.30)) * k
    f_md = float(cp.get("flange_md", 0.38)) * k
    case = _web_case(prm)

    def case11():
        return 2.5 * (1 - Ca) ** 2.3 * k, 5.4 * (1 - Ca) ** 2.3 * k

    def case14():
        if Ca <= 0.113:
            hd, md = 2.45 * (1 - 1.04 * Ca), 3.76 * (1 - 3.05 * Ca)
        else:
            hd, md = 2.26 * (1 - 0.38 * Ca), 2.61 * (1 - 0.49 * Ca)
        return max(hd, 1.56) * k, max(md, 1.56) * k
    if case == "moment_frame":
        w_hd, w_md = case11(); note = "AISC 341-22 Table D1.1b cases 1 / 11 (moment frame)"
    elif case == "other":
        w_hd, w_md = case14(); note = "AISC 341-22 Table D1.1b cases 1 / 14"
    else:
        (h11, m11), (h14, m14) = case11(), case14()
        w_hd, w_md = min(h11, h14), min(m11, m14)
        note = "AISC 341-22 Table D1.1b case 1 / min(case 11, case 14): frame type not known to the hinge builder"
    w_md = max(w_md, w_hd * 1.0001)                            # keep the interpolation interval non-empty
    f_md = max(f_md, f_hd * 1.0001)
    return {"flange": (lam_f, f_hd, f_md), "web": (lam_w, w_hd, w_md)}, note


def ductility_class(elements: dict) -> str:
    """'highly' if every element is at or below lambda_hd, 'non_moderately' if any is at or above lambda_md,
    else 'other' (Table C2.2 / C3.6 line 3)."""
    if all(l <= hd for l, hd, md in elements.values()):
        return "highly"
    if any(l >= md for l, hd, md in elements.values()):
        return "non_moderately"
    return "other"


def interpolate_rows(v1: dict, v2: dict, elements: dict) -> dict:
    """Table C2.2 / C3.6 line 3: linear interpolation between line 1 (at lambda_hd) and line 2 (at lambda_md)
    for flange and web slenderness; the lowest resulting value is used, value by value."""
    out = {}
    for key in v1:
        if v1[key] is None or v2.get(key) is None:
            out[key] = None
            continue
        vals = []
        for lam, hd, md in elements.values():
            t = 0.0 if lam <= hd else (1.0 if lam >= md else (lam - hd) / (md - hd))
            vals.append(v1[key] + t * (v2[key] - v1[key]))
        out[key] = min(vals)
    return out


def rows_of(blk: dict) -> tuple:
    """(line 1, line 2 or None) of a C2.2 / C3.6 group. A legacy flat group is line 1 only."""
    rows = blk.get("rows") if isinstance(blk.get("rows"), dict) else None
    if rows:
        r1 = rows.get("highly_ductile")
        r2 = rows.get("non_moderately_ductile")
        def has(r, k):
            return r.get(k) is not None or r.get(k + "_expr") is not None or r.get(k + "_over_thetay") is not None
        if r2 and not (has(r2, "a") and has(r2, "b")):
            r2 = None                                            # line 2 not supplied (template): flagged fallback
        return r1 or {}, r2
    return blk, None
