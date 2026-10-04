"""sections_db.py -- AISC Shapes Database v16 lookups (aisc_shapes.csv, same file steltic ships)."""
import csv, os, re
from functools import lru_cache

_HERE = os.path.dirname(os.path.abspath(__file__))
_CSV = os.path.join(_HERE, "aisc_shapes.csv")


@lru_cache(maxsize=1)
def _table():
    out = {}
    with open(_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lab = row["AISC_Manual_Label"].strip().upper().replace(" ", "")
            rec = {}
            for k, v in row.items():
                if k == "AISC_Manual_Label":
                    continue
                try:
                    rec[k] = float(v)
                except (TypeError, ValueError):
                    rec[k] = v
            out[lab] = rec
    return out


# ATC-114 cruciform approx (cfg.py eng.SEC CRUC_LO/UP = W+WT). Not in AISC manual.
# Geometry (d,tw,bf,tf) and plastic moduli from PRIMARY W for scissors PZ + IMK hinges;
# A/Ix/Iy/J match elastic pack combined properties. DISCLOSE vs true cruciform PZ/hinge.
_CUSTOM = {
    "CRUC_LO": {  # W24X229 + WT12X88
        "alias_primary": "W24X229",
        "A": 93.0, "Ix": 7890.0, "Iy": 970.0, "J": 63.2,
        "note": "ATC114 cruciform approx: primary W24X229 geo/Z; combined A/I/J",
    },
    "CRUC_UP": {  # W24X176 + WT12X51.5
        "alias_primary": "W24X176",
        "A": 66.8, "Ix": 5739.7, "Iy": 683.0, "J": 27.43,
        "note": "ATC114 cruciform approx: primary W24X176 geo/Z; combined A/I/J",
    },
}


_BRB_RE = re.compile(r"^BRB[-_ ]*(?:A\s*SC|ASC|CORE|A)?[-_ =]*([0-9]+(?:\.[0-9]+)?)", re.I)


def is_brb(section) -> bool:
    """True for a buckling-restrained-brace label (HR writes "BRB-Asc22.5"; also "BRB22.5", "BRB-A22.5")."""
    return bool(section) and str(section).strip().upper().replace(" ", "").startswith("BRB")


def parse_brb_label(section):
    """Core area Asc (in^2) encoded in a BRB label, or None when the label carries no number.

    NL-02: the HR package labels BRBs "BRB-Asc22.5" (steel-core area Asc = 22.5 in^2). That number is the
    YIELDING core area. The HR elastic truss area is KF*Asc (KF ~ 1.5, the stiffness modification factor for the
    stiffer non-yielding ends) and must never be used as the yielding area."""
    if not is_brb(section):
        return None
    m = _BRB_RE.match(str(section).strip())
    return float(m.group(1)) if m else None


def link_shear_props(section: str, Fye: float) -> dict:
    """EBF link section quantities (kip, in).

    Vp = 0.6*Fye*Alw with Alw = (d - 2tf)*tw  -- AISC 341-22 F3.5b.2 (Eq. F3-2) with Fye in place of Fy, which is
    AISC 342-22 C2.3a.2 (expected shear strength of a shear-yielding beam = Vpe of Seismic Provisions F3).
    Mp = Zx*Fye -- AISC 342-22 C2.3a.1 (no lateral-torsional buckling reduction for a link of length e).
    As = d*tw  -- effective shear area of AISC 342-22 Commentary Eq. C-E2-2 (link elastic shear stiffness)."""
    p = props(section)
    Alw = (p["d"] - 2.0 * p["tf"]) * p["tw"]
    return dict(d=p["d"], tw=p["tw"], tf=p["tf"], bf=p["bf"], Zx=p["Zx"], Ix=p["Ix"], A=p["A"],
                Alw=Alw, As=p["d"] * p["tw"], Vp=0.6 * Fye * Alw, Mp=p["Zx"] * Fye)


def props(section: str) -> dict:
    """Section properties (in, in^2, in^4). Adds h/tw and bf/2tf compactness ratios (h ~ d - 2tf here;
    the tabulated h/tw is not in this csv, so the ratio is approximate and flagged as such).
    BRB labels are not rolled shapes: they raise a KeyError that names the BRB path (hinge_models.brb_spec)."""
    t = _table()
    key = section.strip().upper().replace(" ", "")
    if is_brb(key):
        raise KeyError(f"section {section!r} is a buckling-restrained brace (core area label), not an AISC shape -- "
                       "build it with hinge_models.brb_spec (NL-02)")
    if key in _CUSTOM:
        cust = _CUSTOM[key]
        prim = cust["alias_primary"].strip().upper().replace(" ", "")
        if prim not in t:
            raise KeyError(f"custom section {section!r} primary {prim!r} not in aisc_shapes.csv")
        p = dict(t[prim])
        for k in ("A", "Ix", "Iy", "J"):
            if k in cust:
                p[k] = float(cust[k])
        p["custom_section"] = key
        p["custom_note"] = cust.get("note", "")
        # fall through to compactness ratios below
    elif key not in t:
        raise KeyError(f"section {section!r} not in aisc_shapes.csv")
    else:
        p = dict(t[key])
    if all(k in p and isinstance(p[k], float) for k in ("d", "tw", "bf", "tf")):
        p["h_tw"] = (p["d"] - 2.0 * p["tf"]) / p["tw"]        # approx: clear web ~ d - 2tf (no fillets)
        p["bf_2tf"] = p["bf"] / (2.0 * p["tf"])
        p["h_tw_approx"] = True
    return p


def find_by_props(A: float, Ix: float, tol=0.02):
    """Reverse lookup (elasticBeamColumn args -> W-shape) when member_schedule.csv is missing."""
    best = None
    for lab, p in _table().items():
        if not isinstance(p.get("A"), float) or not isinstance(p.get("Ix"), float):
            continue
        if abs(p["A"] - A) <= tol * A and abs(p["Ix"] - Ix) <= tol * Ix:
            best = lab
            break
    return best
