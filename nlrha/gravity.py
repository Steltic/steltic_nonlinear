"""gravity.py -- floor areas, tributaries and live loads for the Chapter 16 gravity case (ASCE 7-22 16.3.2 / 16.3.3).

Earlier builds took each level's area as the bounding box of its diaphragm slaves and a hard-coded 20 psf roof live
load on the top level only. On stepped plans that over-counts (Ex16 L2: 21,952 ft2 against 10,976 ft2 framed; Ex8:
+35%), puts the floor live load on lower roofs (Ex16's gym and link roofs at L2), and spreads the level load equally
over every slave -- including chevron apex nodes held only by braces (Ex1).

Here the framed floor plate is rebuilt from the package's own geometry, the same way the HR engine builds it
(engine3d.floor_area_ft2: bays whose four grid corners exist on the level):
  * grid nodes = diaphragm slaves lying on a column line of that level (an x and a y shared with a column node), so
    mid-span nodes (chevron apexes, link ends) never become bay corners;
  * a bay = a grid node, its nearest grid neighbours in +x and +y, and the diagonal corner -- all present;
  * a bay is ROOF if no bay of the level above covers its centroid (top level: every bay), else FLOOR;
  * live load per bay: floor -> cfg L_by_level[k] or L_floor; roof -> cfg Lr (default 20 psf);
  * each bay's area, live load and dead load are lumped in equal shares to the bay corners that sit on a COLUMN
    (a quarter each when all four do), so the spatial distribution 16.3.3 asks for follows the floor plate and the
    column axial forces follow the tributary areas; nodes that carry no floor, and beam-only grid nodes (Ex16's 7/D,
    a column omitted under a long-span girder), carry no nodal gravity. Loading a beam-only node with the level-average
    dead load would put the floor density on a light roof girder as a point load (Ex16: the 56-ft gym girder yields
    under gravity alone, because the level's seismic weight averages the 75 psf classroom floors with the 24 psf gym
    roof); the girders' own gravity moments are not part of this nodal idealisation (as before).
The level dead load D stays the recorded seismic weight of the level (mass x g: D + cladding + the cfg's extra mass),
distributed in proportion to the tributary floor area.

Roof live load Lr is NOT "L" in ASCE 7 terms; 16.3.2 speaks of L only. It is included here at the 40% factor
(conservative for column compression, P-delta and the 25% exception screen) and listed separately in the table so the
engineer can see its share. A level without detectable bays (skewed or non-orthogonal grids) falls back to the convex
hull of its slaves, loaded on its column nodes only, and says so in the table (`method`).
"""
from __future__ import annotations
import ast, re

_R = 1


def _k(x):
    return round(float(x), _R)


# --------------------------------------------------------------------------- cfg.py (read, never imported)
def cfg_loads(pkg):
    """Live-load inputs from the package's cfg.py (regex + literal_eval; the file imports the engine and is never
    executed here). Keys: L_floor, Lr, L_by_level {k: psf}, source notes. Missing keys -> None."""
    out = dict(L_floor=None, Lr=None, L_by_level={}, sources={})
    path = getattr(pkg, "root", None)
    src = ""
    try:
        src = (path / "cfg.py").read_text(encoding="utf-8", errors="replace") if path is not None else ""
    except Exception:
        src = ""
    num = r"([0-9]+(?:\.[0-9]+)?)"
    for key in ("L_floor", "Lr"):
        mo = re.search(r"(?<![A-Za-z0-9_])['\"]?" + key + r"['\"]?\s*[:=]\s*" + num, src)
        if mo:
            out[key] = float(mo.group(1)); out["sources"][key] = "cfg.py"
    mo = re.search(r"(?<![A-Za-z0-9_])['\"]?L_by_level['\"]?\s*[:=]\s*(\{[^{}]*\})", src)
    if mo:
        try:
            out["L_by_level"] = {int(k): float(v) for k, v in ast.literal_eval(mo.group(1)).items()}
            out["sources"]["L_by_level"] = "cfg.py"
        except Exception:
            pass
    if out["L_floor"] is None and getattr(pkg.basis, "L_floor_psf", None) is not None:
        out["L_floor"] = float(pkg.basis.L_floor_psf); out["sources"]["L_floor"] = pkg.basis.sources.get("L_floor_psf", "package basis")
    return out


# --------------------------------------------------------------------------- geometry
def _column_nodes(pkg):
    from pushover import nonlinear_model as NM
    s = set()
    for e in pkg.model.elements:
        if "etype" in e:
            continue
        if NM.member_kind(pkg, e) == "col":
            s.add(e["n1"]); s.add(e["n2"])
    return s


def _hull_area(pts):
    P = sorted(set(pts))
    if len(P) < 3:
        return 0.0
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(P):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    h = lo[:-1] + up[:-1]
    return abs(sum(h[i][0] * h[(i + 1) % len(h)][1] - h[(i + 1) % len(h)][0] * h[i][1] for i in range(len(h)))) / 2.0


def level_bays(pkg):
    """Per diaphragm level: dict(k, z, master, slaves, bays=[dict(corners=(n00, n10, n01, n11), x0, x1, y0, y1, area_in2)],
    grid_nodes, column_nodes)."""
    from pushover import nonlinear_model as NM
    nodes = pkg.model.nodes
    cols = _column_nodes(pkg)
    out = []
    for k, z, master, slaves in NM.levels(pkg):
        xy = {}
        for n in slaves:
            xy.setdefault((_k(nodes[n][0]), _k(nodes[n][1])), n)
        cxy = [(_k(nodes[n][0]), _k(nodes[n][1])) for n in slaves if n in cols]
        colX = {p[0] for p in cxy}; colY = {p[1] for p in cxy}
        G = {p: n for p, n in xy.items() if p[0] in colX and p[1] in colY}
        rows, colsx = {}, {}
        for (x, y) in G:
            rows.setdefault(y, []).append(x); colsx.setdefault(x, []).append(y)
        for v in rows.values(): v.sort()
        for v in colsx.values(): v.sort()
        bays = []
        for (x, y), n in G.items():
            r = rows[y]; c = colsx[x]
            ix = r.index(x); iy = c.index(y)
            if ix + 1 >= len(r) or iy + 1 >= len(c):
                continue
            x1 = r[ix + 1]; y1 = c[iy + 1]
            if (x1, y1) not in G:
                continue
            bays.append(dict(corners=(n, G[(x1, y)], G[(x, y1)], G[(x1, y1)]), x0=x, x1=x1, y0=y, y1=y1, area_in2=(x1 - x) * (y1 - y)))
        out.append(dict(k=k, z=z, master=master, slaves=list(slaves), bays=bays, grid_nodes=G, column_nodes=[n for n in slaves if n in cols]))
    return out


def _covered(bay, bays_above):
    cx = 0.5 * (bay["x0"] + bay["x1"]); cy = 0.5 * (bay["y0"] + bay["y1"])
    return any(b["x0"] - 1e-6 <= cx <= b["x1"] + 1e-6 and b["y0"] - 1e-6 <= cy <= b["y1"] + 1e-6 for b in bays_above)


def tributary(pkg, live_psf=None, roof_live_psf=None):
    """Floor plate, tributaries and unreduced live load per level.

    Returns (levels, info): levels = [dict(k, z, master, slaves, area_ft2, floor_ft2, roof_ft2, L0_floor_psf, Lr_psf,
    L0_kip (unreduced live, floors), Lr_kip (unreduced roof live), node_area {node: ft2}, node_L0 {node: kip floor live},
    node_Lr {node: kip roof live}, method, bays)], info = cfg_loads(..) plus defaults used."""
    cl = cfg_loads(pkg)
    Lf_default = live_psf if live_psf is not None else (cl["L_floor"] if cl["L_floor"] is not None else 50.0)
    Lr = roof_live_psf if roof_live_psf is not None else (cl["Lr"] if cl["Lr"] is not None else 20.0)
    info = dict(cl, L_floor_used=Lf_default, Lr_used=Lr,
                L_floor_basis=("argument" if live_psf is not None else cl["sources"].get("L_floor", "default 50 psf (no cfg L_floor)")),
                Lr_basis=("argument" if roof_live_psf is not None else cl["sources"].get("Lr", "default 20 psf (no cfg Lr)")))
    LB = level_bays(pkg)
    nodes = pkg.model.nodes
    out = []
    for i, lb in enumerate(LB):
        k = lb["k"]; top = (i == len(LB) - 1)
        Lf = cl["L_by_level"].get(k, Lf_default) if live_psf is None else live_psf
        above = LB[i + 1]["bays"] if not top else []
        node_area, node_L0, node_Lr = {}, {}, {}
        colset = set(lb["column_nodes"])
        floor_in2 = roof_in2 = 0.0
        if lb["bays"]:
            method = "bays"
            for b in lb["bays"]:
                roof = top or not _covered(b, above)
                a_ft2 = b["area_in2"] / 144.0
                if roof:
                    roof_in2 += b["area_in2"]
                else:
                    floor_in2 += b["area_in2"]
                tgt = [n for n in b["corners"] if n in colset] or list(b["corners"])
                sh = a_ft2 / len(tgt)
                for n in tgt:
                    node_area[n] = node_area.get(n, 0.0) + sh
                    if roof:
                        node_Lr[n] = node_Lr.get(n, 0.0) + Lr * sh / 1000.0
                    else:
                        node_L0[n] = node_L0.get(n, 0.0) + Lf * sh / 1000.0
        else:
            method = "convex-hull fallback (no orthogonal bays found)"
            pts = [(nodes[n][0], nodes[n][1]) for n in lb["slaves"]]
            a_in2 = _hull_area(pts)
            tgt = lb["column_nodes"] or lb["slaves"]
            if top or not LB[i + 1]["slaves"]:
                roof_in2 = a_in2
            else:
                floor_in2 = a_in2
            for n in tgt:
                node_area[n] = a_in2 / 144.0 / len(tgt)
                if roof_in2:
                    node_Lr[n] = Lr * a_in2 / 144.0 / len(tgt) / 1000.0
                else:
                    node_L0[n] = Lf * a_in2 / 144.0 / len(tgt) / 1000.0
        out.append(dict(k=k, z=lb["z"], master=lb["master"], slaves=lb["slaves"], area_ft2=(floor_in2 + roof_in2) / 144.0,
                        floor_ft2=floor_in2 / 144.0, roof_ft2=roof_in2 / 144.0, L0_floor_psf=Lf, Lr_psf=Lr,
                        L0_kip=sum(node_L0.values()), Lr_kip=sum(node_Lr.values()), node_area=node_area, node_L0=node_L0, node_Lr=node_Lr,
                        method=method, bays=len(lb["bays"])))
    return out, info
