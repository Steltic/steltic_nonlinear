"""performance.py -- the BPON structural performance levels per Risk Category, and their acceptance limits.

ASCE 41-23 Table 2-5 (Basic Performance Objective Equivalent to New Building Standards, BPON), read in the
corpus (ASCE_41_23.md, Table 2-5):

    Risk Category   BSE-1N                                   BSE-2N
    I and II        Life Safety (3-C)                        Collapse Prevention (5-D)
    III             Damage Control (2-B)                     Limited Safety (4-D)
    IV              Immediate Occupancy (1-A)                Life Safety (3-C)

Damage Control and Limited Safety are intermediate levels (ASCE 41-23 Table 2-1, S-2 and S-4):
  "Acceptance criteria [...] based on the Damage Control Structural Performance Level shall be taken as
   halfway between those for Immediate Occupancy and Life Safety";
  "[...] Limited Safety [...] shall be taken halfway between those for Life Safety and Collapse Prevention".
For the nonlinear procedures, Section 7.5.3.2.2 adds: "Acceptance criteria for Damage Control shall be taken
as the a or e point specified in the tables that specify force-deformation curve modeling parameters [...].
Acceptance criteria for Limited Safety shall be the average of the acceptance criteria for Life Safety and
Collapse Prevention."  This tool takes the Damage Control limit as the LOWER of the two readings -- half-way
IO-LS (Table 2-1) and the `a` point (Section 7.5.3.2.2; hinges only, a brace backbone has no printed a point)
-- and Limited Safety as the average of LS and CP (both clauses agree).
"""
from __future__ import annotations

RC_LEVELS = {"I_II": ("LS", "CP"), "III": ("DC", "LtdS"), "IV": ("IO", "LS")}
LEVEL_NAMES = {"IO": "Immediate Occupancy", "DC": "Damage Control", "LS": "Life Safety",
               "LtdS": "Limited Safety", "CP": "Collapse Prevention"}
HAZARDS = ("BSE-1N", "BSE-2N")
CITATION = "ASCE 41-23 Table 2-5 (BPON); DC/LtdS per Table 2-1 and Section 7.5.3.2.2"


def normalise_rc(rc) -> str:
    """'I', 'II', 'I_II', 'I/II', 1, 2 -> 'I_II'; 'III' -> 'III'; 'IV' -> 'IV'. Unknown -> 'I_II'."""
    s = str(rc or "").strip().upper().replace("RC", "").replace(" ", "")
    if s in ("III", "3"):
        return "III"
    if s in ("IV", "4"):
        return "IV"
    return "I_II"


def bpon_levels(rc) -> tuple:
    """(level at BSE-1N, level at BSE-2N) for the Risk Category."""
    return RC_LEVELS[normalise_rc(rc)]


def level_for(rc, hazard: str) -> str:
    l1, l2 = bpon_levels(rc)
    return l1 if hazard == "BSE-1N" else l2


def describe(rc) -> str:
    l1, l2 = bpon_levels(rc)
    return "RC %s: %s at BSE-1N, %s at BSE-2N (%s)" % (normalise_rc(rc).replace("_", "/"), LEVEL_NAMES[l1], LEVEL_NAMES[l2], CITATION)


def risk_category(pkg, override=None) -> str:
    """Same resolution as the NLRHA (--risk-category, else cfg.py text, else Ie)."""
    if override:
        return normalise_rc(override)
    try:
        from nlrha import acceptance as AC
        return normalise_rc(AC.risk_category(pkg, None))
    except Exception:                                             # noqa: BLE001
        ie = getattr(getattr(pkg, "basis", None), "Ie", None) or 1.0
        return "IV" if ie >= 1.5 else ("III" if ie >= 1.25 else "I_II")


def dc_limit(IO: float, LS: float, a=None) -> float:
    half = 0.5 * (IO + LS)
    return min(half, a) if a and a > 0 else half


def ltds_limit(LS: float, CP: float) -> float:
    return 0.5 * (LS + CP)


def _ratio(th, lim):
    return th / lim if lim and lim > 0 else float("nan")


def augment(acc: dict, run: dict, hinges: dict) -> dict:
    """Add the DC and LtdS limits and D/C ratios to one hazard level's acceptance dict (from
    postprocess.acceptance), hinge by hinge at the same analysis step, and the group/worst values."""
    i = acc.get("step", 0)
    try:
        pl = run["rec"]["hinge_pl"][i]
        tags = run["hinge_tags"]
    except (KeyError, IndexError, TypeError):
        return augment_groups(acc)
    by_key = {(g["kind"], g["section"], g["z_in"]): g for g in acc.get("groups") or []}
    for g in by_key.values():
        g.update(DC=None, LtdS=None, DC_DC=0.0, DC_LtdS=0.0)
    for j, t in enumerate(tags):
        h = hinges[t]; s = h["spec"]
        th = abs(pl[j])
        if h["kind"] == "brace":
            IO, LS, CP = (s.IO_t, s.LS_t, s.CP_t) if pl[j] > 0 else (s.IO, s.LS, s.CP)
            a = None                                              # no printed a point on a brace backbone
        else:
            IO, LS, CP = s.IO, s.LS, s.CP
            a = getattr(s, "a_pl", None)
        g = by_key.get((h["kind"], h["section"], round(h["z"])))
        if g is None:
            continue
        dcl, lsl = dc_limit(IO, LS, a), ltds_limit(LS, CP)
        g["DC"] = dcl if g["DC"] is None else min(g["DC"], dcl)
        g["LtdS"] = lsl if g["LtdS"] is None else min(g["LtdS"], lsl)
        g["DC_DC"] = max(g["DC_DC"], _ratio(th, dcl))
        g["DC_LtdS"] = max(g["DC_LtdS"], _ratio(th, lsl))
    _worst(acc)
    return acc


def augment_groups(acc: dict) -> dict:
    """For an acceptance dict written before this module: DC/LtdS from the group limits (no a point)."""
    for g in acc.get("groups") or []:
        if "DC_DC" in g and "DC_LtdS" in g:
            continue
        IO, LS, CP, th = g.get("IO") or 0.0, g.get("LS") or 0.0, g.get("CP") or 0.0, g.get("theta_pl_max") or 0.0
        g["DC"], g["LtdS"] = dc_limit(IO, LS), ltds_limit(LS, CP)
        g["DC_DC"], g["DC_LtdS"] = _ratio(th, g["DC"]), _ratio(th, g["LtdS"])
    _worst(acc)
    return acc


def _worst(acc):
    w = acc.setdefault("worst_DC", {})
    for k in ("DC", "LtdS"):
        vals = [g.get("DC_" + k) for g in acc.get("groups") or [] if isinstance(g.get("DC_" + k), (int, float)) and g.get("DC_" + k) == g.get("DC_" + k)]
        w[k] = max(vals, default=0.0)


def augment_package(po: dict) -> dict:
    """pushover_package.json from any version -> every acceptance block carries DC / LtdS."""
    for d in ((po or {}).get("directions") or {}).values():
        for acc in (d.get("acceptance") or {}).values():
            if isinstance(acc, dict) and not {"DC", "LtdS"} <= set(acc.get("worst_DC") or {}):
                augment_groups(acc)
    return po
