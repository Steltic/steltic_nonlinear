"""
loads.py -- factored load combinations and their application to the GMNIA model.

Combinations come from Steltic's own design_pipeline.combos(cfg) so the DDM sweeps scale EXACTLY the
ASCE 7-22 §2.3 cases the member design used: (label, fD, fL, fLr, lateral{k:(fx,fy,mz)}, col_only).
Gravity is applied as the same two-way (45-degree) tributary line loads static_model.apply_gravity
uses (per beam sub-element, plus cladding on single-bay perimeter beams); lateral forces and
accidental-torsion moments go to the rigid-diaphragm master nodes, as in Steltic.

Pruning (default): 1.4D ; 1.2D+1.6L+0.5Lr ; 1.2D+1.6Lr+0.5L ; the +/-X and +/-Y strength lateral cases
for wind (if present) and for the rho*E seismic pattern with the accidental-torsion sign the elastic
envelope found governing (here: '+' by default -- both are run when --torsion both), plus the 0.9D
uplift companions for braced buildings. Omega0 [col] cases are optional (seismic supplement).
"""
import re
from .ingest import decode_tag


def steltic_combos(cfg, nm=None):
    from . import portal_adapter as PA
    if PA.is_portal(cfg):
        return PA.portal_combos(cfg, nm=nm)
    import design_pipeline as DP
    return DP.combos(cfg)


def prune(cases, policy="default", torsion="plus", include_om0=False):
    if policy == "all":
        return list(cases)
    # portal seismic labels are "(1.2+0.2SDS)D+E" — keep them
    from . import portal_adapter as PA
    keep = []
    for c in cases:
        lab = c[0]
        col_only = c[5]
        if col_only and not include_om0:
            continue
        if lab in ("1.4D",) or lab.startswith("1.2D+1.6L") or lab.startswith("1.2D+1.6Lr") or lab.startswith("1.2D+1.0S"):
            keep.append(c); continue
        if "W" in lab and ("1.2D" in lab or "0.9D" in lab):
            keep.append(c); continue                          # all 8 wind cases (4 strength + 4 uplift)
        if "rhoE" in lab:
            t = "t+" if torsion == "plus" else "t-"
            if torsion == "both" or t in lab:
                keep.append(c); continue
        if lab.endswith("+E") or "D+E" in lab:
            keep.append(c); continue
        if col_only and include_om0:
            keep.append(c)
    return keep


def lateral_direction(lat):
    """Dominant lateral direction and sign of a combination: ('X'|'Y'|None, +1|-1)."""
    fx = sum(v[0] for v in lat.values()); fy = sum(v[1] for v in lat.values())
    if abs(fx) < 1e-9 and abs(fy) < 1e-9:
        return None, 0
    if abs(fx) >= abs(fy):
        return "X", (1 if fx > 0 else -1)
    return "Y", (1 if fy > 0 else -1)


def present_sets(nm):
    pres = {}
    for t in nm.nodes:
        if t % 100000 == 99999:
            continue
        i, j, k = decode_tag(t)
        pres.setdefault(k, set()).add((i, j))
    return pres


def _bays_adjacent(present_k, i, j, dirn):
    n = 0
    if dirn == "X":
        for jj in (j - 1, j):
            if all(c in present_k for c in ((i, jj), (i + 1, jj), (i, jj + 1), (i + 1, jj + 1))): n += 1
    else:
        for ii in (i - 1, i):
            if all(c in present_k for c in ((ii, j), (ii + 1, j), (ii, j + 1), (ii + 1, j + 1))): n += 1
    return n


def beam_udl(cfg, nm, pres, member, seg_index, nseg, fD, fL, fLr):
    """kip/in on sub-element seg_index (0..nseg-1) of a grid beam -- Steltic static_model.apply_gravity."""
    from . import portal_adapter as PA
    if PA.is_portal(cfg):
        return PA.portal_beam_udl(cfg, nm, member, seg_index, nseg, fD, fL, fLr)
    i, j, k = decode_tag(member.n1)
    NF = len(cfg["heights"])
    if not (1 <= k <= NF):
        return 0.0
    roof = (k == NF)
    extra = cfg.get("extra_mass_floors", {})
    byD = cfg.get("D_by_level") or {}
    pD = (byD.get(k) if k in byD else (cfg["D_roof"] if roof else cfg["D_floor"])) + extra.get(k, 0.0)
    byL = cfg.get("L_by_level") or {}
    pL = 0.0 if roof else (byL.get(k) if k in byL else cfg["L_floor"])
    pLr = (cfg.get("snow") or 20.0) if roof else 0.0
    p = fD * pD + fL * pL + fLr * pLr
    nb = _bays_adjacent(pres.get(k, set()), i, j, member.dirn)
    SX, SY = cfg["SX"], cfg["SY"]
    other = SY if member.dirn == "X" else SX
    wcap = other / 2.0
    th = cfg["heights"][k - 1] / 12.0
    th = th / 2.0 if roof else th
    clad = cfg.get("clad", 0.0)
    wclad = fD * clad * th / 12000.0 if (clad and nb == 1) else 0.0
    x1, y1, _ = nm.nodes[member.n1]; x2, y2, _ = nm.nodes[member.n2]
    L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    s0 = L * seg_index / nseg; s1 = L * (seg_index + 1) / nseg; smid = 0.5 * (s0 + s1)
    width_in = min(smid, L - smid, wcap)
    return nb * p * (width_in / 12.0) / 12000.0 + wclad


def combo_summary(c):
    label, fD, fL, fLr, lat, col_only = c
    d, s = lateral_direction(lat)
    V = sum(abs(v[0]) + abs(v[1]) for v in lat.values())
    return dict(label=label, fD=fD, fL=fL, fLr=fLr, lateral_dir=d, sign=s, base_shear_kip=round(V, 1),
                col_only=col_only, kind=("gravity" if d is None else ("wind" if "W" in label else "seismic")))
