"""
loads.py -- factored load combinations and their application to the GMNIA model.

Combinations come from Steltic's own design_pipeline.combos(cfg) so the DDM sweeps scale EXACTLY the
ASCE 7-22 §2.3 cases the member design used: (label, fD, fL, fLr, lateral{k:(fx,fy,mz)}, col_only).
Gravity (NL-R2-03) is distributed by Steltic's own static_model (hr_gravity_geometry + _case_pieces): bay by bay
with the package's floor_system / deck_span / infill_dir / infill_spacing (one-way strips onto the lines
perpendicular to the deck span, modelled infill beams, virtual-infill reactions as point loads on the girders),
roof bays from roof_levels / setbacks, level live/roof factors and cladding exactly as the member design; each
GMNIA beam element takes the pieces over its own range on the parent span. beam_udl (two-way 45-degree
tributary) is the fallback when the static model cannot be built (portal frames, synthetic cfgs). Lateral forces
and accidental-torsion moments go to the rigid-diaphragm master nodes, as in Steltic.

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


def hr_gravity_geometry(cfg):
    """NL-R2-03: the HR static model's load-path geometry -- parent grid spans, framed bays with roof flags,
    modelled infill beams and the per-bay distribution mode (static_model.bay_modes: two-way / one-way with the
    declared deck span and infill lines / default). Builds the static model in OpenSees (wipes the domain), so call
    it BEFORE the GMNIA build. Raises when the HR engine is not importable or the cfg cannot be built."""
    import static_model as SM
    model = SM.build_static(cfg, "Linear", 1)
    return dict(model=model, modes=SM.bay_modes(cfg, model))


def hr_member_location(geo, nm, member):
    """Where a DDM beam member sits in the HR load path: ('span', (k, dir, i, j), sA, sB, Lp) on a parent grid
    span, ('infill', etag, sA, sB, L) for a modelled infill beam, or None (sloped / off-level: no floor load,
    as in HR). sA / sB are the positions of member.n1 / n2 along the span (in)."""
    import static_model as SM
    M = geo["model"]
    if member.n1 not in nm.nodes or member.n2 not in nm.nodes:
        return None
    (x1, y1, z1), (x2, y2, z2) = nm.nodes[member.n1], nm.nodes[member.n2]
    k = SM._level_of(z1, M["z"])
    if k is None or k == 0 or k != SM._level_of(z2, M["z"]):
        return None
    for key, sp in M["spans"].get(k, {}).items():
        sa = SM._on_span(x1, y1, sp)
        if sa is None:
            continue
        sb = SM._on_span(x2, y2, sp)
        if sb is None or abs(sb - sa) < 1e-6:
            continue
        return ("span", (k,) + key, sa, sb, sp[6])
    tol = SM._TOL
    for (kk, _i, _j), lst in M["infill"].items():
        if kk != k:
            continue
        for (_run, _u, bm) in lst:
            A, B = bm["xyzA"], bm["xyzB"]
            near = lambda p, x, y: abs(p[0] - x) <= tol and abs(p[1] - y) <= tol
            Lb = bm["L"]
            if near(A, x1, y1) and near(B, x2, y2):
                return ("infill", bm["etag"], 0.0, Lb, Lb)
            if near(B, x1, y1) and near(A, x2, y2):
                return ("infill", bm["etag"], Lb, 0.0, Lb)
    return None


def beam_udl(cfg, nm, pres, member, seg_index, nseg, fD, fL, fLr):
    """kip/in on sub-element seg_index (0..nseg-1) of a grid beam -- LEGACY two-way 45-degree tributary, used only
    when hr_gravity_geometry is unavailable (NL-R2-03: it ignores one-way decks / infill and overloads such beams)."""
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
