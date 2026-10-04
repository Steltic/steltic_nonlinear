"""drift.py -- ASCE 7-22 16.4.1.2 transient story drift at vertically aligned points.

16.4.1.2 (verified against the converted ASCE 7-22 text, pdf p. 250): "The transient story drift ratio shall be
computed as the absolute value of the largest difference of the deflections of vertically aligned points at the top
and bottom of the story under consideration along any of the edges of the structure within a single response history
analysis."

Earlier builds monitored two (x, y)-sorted slave nodes per level. On set-backs and stepped plans those two nodes are
not vertically aligned (Ex16: corner B at (1680,1008) on L1, (2352,1344) on L2, (1008,672) on L3), and on an L-plan
the wing tip was never monitored (Ex8). Here every plan point (x, y) that exists on the level at the top of a story is
paired with the SAME (x, y) on the nearest level below that has it (or the fixed base). Pairs whose bottom is not the
next level down (double-height spaces such as Ex16's gym, base to L2) are kept and divided by their own height. The
story drift is the maximum over all pairs.

For levels tied by a rigid diaphragm the in-plane slave displacement is affine in (x, y), so the difference field of a
pair group (same top and bottom level, same height) is affine too and its maximum absolute value over the group is
reached at a vertex of the group's convex hull. Only those vertices are monitored -- the result is identical to the
maximum over every aligned point (asserted in tests/test_nlrha_fixes.py), at a fraction of the nodeDisp calls. Interior
points are kept when a level is not a rigid diaphragm (the option exists for flexible-diaphragm packages).
"""
from __future__ import annotations
import numpy as np

_R = 1        # coordinates rounded to 0.1 in when matching vertically aligned points


def _key(xyz):
    return (round(float(xyz[0]), _R), round(float(xyz[1]), _R))


def _hull(pts):
    """Indices of the convex-hull vertices of a list of 2-D points (Andrew's monotone chain; collinear points dropped).
    Fewer than three distinct points: all of them."""
    P = sorted(set(pts))
    if len(P) <= 2:
        return [pts.index(p) for p in P]
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in P:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(P):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:                                   # all collinear: keep the two extremes
        hull = [P[0], P[-1]]
    return [pts.index(p) for p in hull]


def level_points(pkg):
    """[(k, z, master, {xy: node})] with k = 0 for the base (fixed nodes at the lowest elevation) and 1..NF for the
    diaphragm levels of the package (pushover.nonlinear_model.levels order)."""
    from pushover import nonlinear_model as NM
    lv = NM.levels(pkg)
    nodes = pkg.model.nodes
    fixed = [n for n in pkg.model.fixes if n in nodes]
    out = []
    if fixed:
        z0 = min(nodes[n][2] for n in fixed)
        base = {}
        for n in fixed:
            if abs(nodes[n][2] - z0) < 1e-6:
                base.setdefault(_key(nodes[n]), n)
        out.append((0, z0, None, base))
    else:
        out.append((0, 0.0, None, {}))
    for k, z, master, slaves in lv:
        out.append((k, z, master, {_key(nodes[n]): n for n in slaves}))
    return out


def drift_points(pkg, rigid=True, hull_only=True, consecutive_only=False):
    """Pairs of vertically aligned points per story.

    Returns a list (one per story i = 1..NF, story i between level i-1 and level i) of dicts
        dict(story=i, h=H_i, groups=[dict(bot_level=j, h=z_i - z_j, pairs=[(top_node, bot_node, (x, y)), ...],
                                          n_aligned=<all aligned points in the group>)], n_aligned=..)
    bot_node is the base node for j = 0 (displacement zero for a fixed base, read anyway).
    hull_only (rigid diaphragms): keep only the convex-hull vertices of each group (exact, see module doc).
    consecutive_only: drop the double-height pairs (bottom below level i-1) -- the review's Ex16 recomputation
    (scratch/verify_nl/drift16.py) used points common to consecutive levels only; kept for that comparison."""
    L = level_points(pkg)
    out = []
    for i in range(1, len(L)):
        k, z, master, top = L[i]
        groups = {}
        for xy, nt in top.items():
            for j in range(i - 1, -1 if not consecutive_only else i - 2, -1):
                if xy in L[j][3]:
                    groups.setdefault(j, []).append((nt, L[j][3][xy], xy))
                    break
        gl = []
        for j in sorted(groups, reverse=True):
            pairs = groups[j]
            n_all = len(pairs)
            if rigid and hull_only and len(pairs) > 3:
                idx = _hull([p[2] for p in pairs])
                pairs = [pairs[q] for q in idx]
            gl.append(dict(bot_level=j, h=z - L[j][1], pairs=pairs, n_aligned=n_all))
        out.append(dict(story=i, level=k, h=z - L[i - 1][1], groups=gl, n_aligned=sum(g["n_aligned"] for g in gl)))
    return out


def node_set(points):
    s = set()
    for st in points:
        for g in st["groups"]:
            for nt, nb, xy in g["pairs"]:
                s.add(nt); s.add(nb)
    return sorted(s)


def story_drifts(points, disp):
    """disp: {node: (ux, uy)} at one instant -> (n_story, 2) drift ratios and the governing (x, y) per story/direction."""
    n = len(points)
    d = np.zeros((n, 2)); at = [[None, None] for _ in range(n)]
    for i, st in enumerate(points):
        for g in st["groups"]:
            h = g["h"]
            for nt, nb, xy in g["pairs"]:
                ut = disp[nt]; ub = disp[nb]
                for c in (0, 1):
                    v = abs(ut[c] - ub[c]) / h
                    if v > d[i, c]:
                        d[i, c] = v; at[i][c] = xy
    return d, at


def drifts_from_master_frames(pkg, story_frames, points=None, levels_xy=None):
    """Recompute 16.4.1.2 drifts from recorded master frames (ux, uy, rz per diaphragm level) by rigid-body kinematics,
    u(p) = (ux - rz (y - ym), uy + rz (x - xm)); base points have zero displacement. story_frames: (n_frames, NF, 3).
    Used by `nlrha report` to correct suites run with the old corner-node monitor, and by the regression test that
    reproduces the review's Ex16 recomputation. Returns (peak (NF, 2), residual-at-last-frame (NF,))."""
    from pushover import nonlinear_model as NM
    pts = points if points is not None else drift_points(pkg, hull_only=False)
    lv = NM.levels(pkg)
    mxy = {k: np.array(pkg.model.nodes[m][:2]) for k, z, m, s in lv}
    S = np.asarray(story_frames, float)
    lvl_of = {}
    for k, z, m, slaves in lv:
        for n in slaves:
            lvl_of[n] = k
    def u(node, xy):
        k = lvl_of.get(node)
        if k is None:                                   # base node
            return np.zeros((S.shape[0], 2))
        ux, uy, rz = S[:, k - 1, 0], S[:, k - 1, 1], S[:, k - 1, 2]
        m = mxy[k]
        return np.stack([ux - rz * (xy[1] - m[1]), uy + rz * (xy[0] - m[0])], 1)
    peak = np.zeros((len(pts), 2)); resid = np.zeros(len(pts))
    for i, st in enumerate(pts):
        for g in st["groups"]:
            for nt, nb, xy in g["pairs"]:
                dlt = np.abs(u(nt, xy) - u(nb, xy)) / g["h"]
                peak[i] = np.maximum(peak[i], dlt.max(0))
                resid[i] = max(resid[i], float(dlt[-1].max()))
    return peak, resid


def summary(points):
    """Short per-story description for the report: points monitored / aligned points, double-height pairs."""
    rows = []
    for st in points:
        dh = [g for g in st["groups"] if g["bot_level"] != st["level"] - 1]
        rows.append(dict(story=st["story"], h_in=st["h"], n_aligned=st["n_aligned"], n_monitored=sum(len(g["pairs"]) for g in st["groups"]),
                         double_height=[dict(bottom_level=g["bot_level"], h_in=g["h"], n_aligned=g["n_aligned"]) for g in dh]))
    return rows
