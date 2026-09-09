"""RBS 7-segment remesh helpers for FR SMF beams (L1 PERFORM-like).

Gated by hinge_params beam_flexure.rbs_segments == 7 plus a per-section
geometry dict (e.g. rbs_geometry_nist_fig_2_11b with a_in/b_in/c_in).

Layout between joint nodes (length L), RBS both ends (a, b, c in inches):

  s=0                a          a+b/2       a+b              L-(a+b)     L-(a+b/2)   L-a            L
  |--- seg1 full ----|--seg2 red--|--seg3 red--|--- seg4 full ---|--seg5 red--|--seg6 red--|---seg7 full---|
                              ^ ZL IMK @ RBS i                              ^ ZL IMK @ RBS j

Plastic hinges (ModIMK zeroLength) sit at RBS centres (offset a+b/2 from each
joint). Intermediate segment joints are continuous (no IMK). Keeps the current
ZL architecture — no forceBeamColumn / fibre.

Reduced-section formula (disclose in L1 notes):
  bf_r = bf - 2*c
  A_r  = A - 4*c*tf                         # both flanges, both tips
  y_f  = (d - tf)/2
  Iweb = tw * (d - 2*tf)^3 / 12
  Ifl  = 2 * (bf*tf^3/12 + bf*tf*y_f^2)
  Ifl_r= 2 * (bf_r*tf^3/12 + bf_r*tf*y_f^2)
  Ix_r = Ix_tab * (Iweb + Ifl_r) / (Iweb + Ifl)   # strong-axis (beam Iy slot)
  Iy_r = Iy_tab * (bf_r/bf)^3                     # weak-axis approx (flange-dominated)
  J_r  = J_tab * (A_r/A)                          # mild torsion scale; disclose
"""
from __future__ import annotations
import math
from . import sections_db as SDB

# Tag spaces (avoid collision with HN/ZL/MAT/PZ bases in nonlinear_model)
INT_NODE_BASE = 70_000_000   # INT_NODE_BASE + ele*20 + k
SEG_ELE_BASE = 80_000_000    # SEG_ELE_BASE + ele*10 + seg_idx (1..7); seg4 keeps original ele tag


def rbs_geometry_for_section(section: str, bp: dict) -> dict | None:
    """Return {a_in,b_in,c_in} for section from any rbs_geometry_* dict in beam_flexure, else None."""
    key = section.strip().upper().replace(" ", "")
    for k, v in bp.items():
        if not str(k).startswith("rbs_geometry") or not isinstance(v, dict):
            continue
        for sk, geo in v.items():
            if str(sk).startswith("note"):
                continue
            if str(sk).strip().upper().replace(" ", "") == key and isinstance(geo, dict):
                if all(x in geo for x in ("a_in", "b_in", "c_in")):
                    return dict(a_in=float(geo["a_in"]), b_in=float(geo["b_in"]), c_in=float(geo["c_in"]))
    # fallback: global a/b/c if provided
    if bp.get("rbs_a_in") and bp.get("rbs_b_in"):
        c = float(bp.get("rbs_c_in") or 0.0)
        if c <= 0 and bp.get("rbs_c_frac_bf"):
            p = SDB.props(section)
            c = float(bp["rbs_c_frac_bf"]) * p["bf"]
        return dict(a_in=float(bp["rbs_a_in"]), b_in=float(bp["rbs_b_in"]), c_in=c)
    return None


def want_rbs_remesh(kind: str, section: str | None, hinge_i: bool, hinge_j: bool, prm: dict) -> dict | None:
    """If this FR beam should get 7-seg RBS remesh, return geometry; else None."""
    if kind != "beam" or not section or not (hinge_i and hinge_j):
        return None
    bp = prm.get("beam_flexure") or {}
    nseg = int(bp.get("rbs_segments") or 0)
    if nseg != 7:
        return None
    return rbs_geometry_for_section(section, bp)


def reduced_props(section: str, c_in: float, A: float, I_strong: float, I_weak: float, J: float) -> dict:
    """Approx reduced A / strong-I / weak-I / J for flange cut depth c each tip."""
    p = SDB.props(section)
    bf, tf, tw, d = p["bf"], p["tf"], p["tw"], p["d"]
    c = float(c_in)
    if c <= 0 or c >= 0.5 * bf - 1e-6:
        return dict(A=A, I_strong=I_strong, I_weak=I_weak, J=J, bf_r=bf, formula="c<=0 or invalid; full section")
    bf_r = bf - 2.0 * c
    A_r = A - 4.0 * c * tf
    y_f = 0.5 * (d - tf)
    h_web = max(d - 2.0 * tf, 1e-6)
    Iweb = tw * h_web ** 3 / 12.0
    Ifl = 2.0 * (bf * tf ** 3 / 12.0 + bf * tf * y_f ** 2)
    Ifl_r = 2.0 * (bf_r * tf ** 3 / 12.0 + bf_r * tf * y_f ** 2)
    ratio = (Iweb + Ifl_r) / max(Iweb + Ifl, 1e-12)
    Ix_r = I_strong * ratio
    Iy_r = I_weak * (bf_r / bf) ** 3
    J_r = J * (A_r / max(A, 1e-12))
    return dict(A=A_r, I_strong=Ix_r, I_weak=Iy_r, J=J_r, bf_r=bf_r, ratio_I=ratio,
                formula="bf_r=bf-2c; A_r=A-4c*tf; Ix_r=Ix*(Iweb+Ifl_r)/(Iweb+Ifl); Iy_r=Iy*(bf_r/bf)^3; J_r=J*(A_r/A)")


def segment_stations(L: float, a: float, b: float) -> list[tuple[float, float, str]]:
    """Return 7 (s0, s1, kind) stations along [0,L]; kind in {'full','rbs'}."""
    if a < 0 or b < 0 or 2.0 * (a + b) >= L - 1e-6:
        raise ValueError("RBS a,b do not fit in member length L=%.3f (need 2(a+b) < L; a=%.3f b=%.3f)" % (L, a, b))
    s = [0.0, a, a + 0.5 * b, a + b, L - (a + b), L - (a + 0.5 * b), L - a, L]
    kinds = ["full", "rbs", "rbs", "full", "rbs", "rbs", "full"]
    return [(s[i], s[i + 1], kinds[i]) for i in range(7)]


def xyz_along(p1, p2, s: float, L: float):
    t = s / L
    return [p1[i] + t * (p2[i] - p1[i]) for i in range(3)]


def remesh_tags(ele_tag: int):
    """Node / element tag helpers for one remeshed beam."""
    def inode(k: int) -> int:
        return INT_NODE_BASE + ele_tag * 20 + k

    def seg_ele(idx: int) -> int:
        # idx 1..7; middle (4) keeps the original ele tag for damping / schedule identity
        if idx == 4:
            return ele_tag
        return SEG_ELE_BASE + ele_tag * 10 + idx

    return inode, seg_ele
