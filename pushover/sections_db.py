"""sections_db.py -- AISC Shapes Database v16 lookups (aisc_shapes.csv, same file steltic ships)."""
import csv, os
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


def props(section: str) -> dict:
    """Section properties (in, in^2, in^4). Adds h/tw and bf/2tf compactness ratios (h ~ d - 2tf here;
    the tabulated h/tw is not in this csv, so the ratio is approximate and flagged as such)."""
    t = _table()
    key = section.strip().upper().replace(" ", "")
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
