"""NL-R2-37: welded built-up square box columns, labels BOX<B>X<t> (outer width B, plate thickness t, in).

The October 2026 HR designs (Ex23, Ex29) use them for the braced-core columns that no rolled W14 can carry. The HR
design toolkit registers them at cfg import (stk_ext.groupD.register_box -> box_props), so they are in the design
model but not in aisc_shapes.csv; the nonlinear tools stopped with "section 'BOX34X4' not in aisc_shapes.csv".

Properties use the toolkit's formulas exactly, so the nonlinear models keep the design model's stiffness:
    A = B^2 - (B - 2t)^2,  I = (B^4 - (B - 2t)^4) / 12,  Z = (B^3 - (B - 2t)^3) / 4,  S = 2I / B,
    J = t (B - t)^3 (thin-walled closed section),  r = sqrt(I / A).
Geometry is reported as a rectangular hollow shape (kind "rect") flagged built_up: the wall is a plate, so the design
thickness is t itself (no 0.93 A500 factor) and the flat wall width is the clear distance between plates, b = B - 2t
(AISC 342-22 Table C3.6 defines h for built-up welded sections as the clear distance between flanges).
Material: four ASTM A572 Gr. 50 plates (the toolkit's design basis), Ry = 1.1 (AISC 341-22 Table A3.2, plates).
Column hinges take AISC 342-22 Table C3.6 line 4 ("Rectangular HSS and built-up box shapes") with note [e]: the values
are multiplied by 0.75 for columns with built-up box shapes (hinge_models._hss_column_hinge)."""
import math
import re

_RX = re.compile(r"^BOX(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$")
MATERIAL = dict(Fy=50.0, Ry=1.1, spec="ASTM A572 Gr. 50 plate (welded built-up box), AISC 341-22 Table A3.2")
NOTE_E = 0.75                     # AISC 342-22 Table C3.6 note [e]


def parse(label):
    """'BOX34X4' -> dict(kind='rect', H, B, t_nom, t_des, built_up=True); None for any other label."""
    s = str(label or "").strip().upper().replace(" ", "")
    m = _RX.match(s)
    if not m:
        return None
    B, t = float(m.group(1)), float(m.group(2))
    if t <= 0 or 2 * t >= B:
        return None
    return dict(kind="rect", H=B, B=B, t_nom=t, t_des=t, built_up=True, label=s)


def props(label):
    """Section properties in the aisc_shapes.csv keys (floats), or None when the label is not a built-up box."""
    g = parse(label)
    if not g:
        return None
    B, t = g["B"], g["t_nom"]
    bi = B - 2 * t
    A = B * B - bi * bi
    I = (B ** 4 - bi ** 4) / 12.0
    Z = (B ** 3 - bi ** 3) / 4.0
    S = 2 * I / B
    r = math.sqrt(I / A)
    return dict(A=A, Ix=I, Iy=I, Zx=Z, Zy=Z, Sx=S, Sy=S, rx=r, ry=r, J=t * (B - t) ** 3, d=B, bf=B, tf=t, tw=t,
                W=3.4 * A, Type="BOX", built_up_box=True)


def csv_row(label):
    """Built-up box or registered built-up I-section as an aisc_shapes.csv row (steltic_ddm.sections_fiber.shape)."""
    p = props(label)
    if p is None:
        p = i_section(label)
        if p is None:
            return None
    row = dict(p)
    row["AISC_Manual_Label"] = str(label).strip().upper().replace(" ", "")
    return row


# ---------------------------------------------------------------------------------- built-up (welded plate) I-sections
# NL-R2-38: a welded plate girder (e.g. Ex19's Level-2 transfer girder PG84X1108: d 84, flanges 30 x 4, web 1 1/8 in) is
# registered by the cfg in the engine catalog (A, Ix, Iy, J only), and its label does not encode the plates. The job
# declares the plates in <job>/nl_sections.json:
#     {"PG84X1108": {"kind": "I", "d": 84.0, "bf": 30.0, "tf": 4.0, "tw": 1.125,
#                    "material": "ASTM A572 Gr. 50 plates", "source": "cfg.py PG"}}
# Properties use the cfg's own formulas (so the stiffness matches the design model). The section is then treated as a
# doubly symmetric I-shape (W-shape paths: fibre w_shape, AISC 342 Table C2.2 / C3.6 by its own h/tw and bf/2tf); A572
# Gr. 50 plates have the same Fy 50 / Ry 1.1 as A992 (AISC 341-22 Table A3.2).
_BUILTUP_I = {}


def i_props(d, bf, tf, tw):
    hw = d - 2 * tf
    A = 2 * bf * tf + hw * tw
    Ix = 2 * bf * tf * ((d - tf) / 2) ** 2 + tw * hw ** 3 / 12 + 2 * bf * tf ** 3 / 12
    Iy = 2 * tf * bf ** 3 / 12 + hw * tw ** 3 / 12
    J = (2 * bf * tf ** 3 + hw * tw ** 3) / 3
    Zx = bf * tf * (d - tf) + tw * hw ** 2 / 4
    Zy = tf * bf ** 2 / 2 + hw * tw ** 2 / 4
    return dict(A=A, Ix=Ix, Iy=Iy, J=J, Zx=Zx, Zy=Zy, Sx=2 * Ix / d, Sy=2 * Iy / bf, rx=math.sqrt(Ix / A),
                ry=math.sqrt(Iy / A), d=d, bf=bf, tf=tf, tw=tw, W=3.4 * A, Type="BUILTUP_I", built_up_i=True)


def register_job_sections(job_dir):
    """Read <job_dir>/nl_sections.json (if present) into the built-up I-section registry. Returns the labels."""
    import json
    import os
    p = os.path.join(str(job_dir), "nl_sections.json")
    if not os.path.exists(p):
        return []
    out = []
    for lab, g in (json.load(open(p)) or {}).items():
        if str(lab).startswith("_") or not isinstance(g, dict) or str(g.get("kind", "I")).upper() != "I":
            continue
        key = str(lab).strip().upper().replace(" ", "")
        pr = i_props(float(g["d"]), float(g["bf"]), float(g["tf"]), float(g["tw"]))
        pr["custom_note"] = "%s (%s)" % (g.get("material", "built-up plates"), g.get("source", "nl_sections.json"))
        _BUILTUP_I[key] = pr
        out.append(key)
    return out


def i_section(label):
    """Registered built-up I-section properties (copy), or None."""
    p = _BUILTUP_I.get(str(label or "").strip().upper().replace(" ", ""))
    return dict(p) if p else None
