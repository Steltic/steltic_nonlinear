"""model.py -- the NLRHA model is the Pushover Analyst's hinge model (steltic_pushover.nonlinear_model) plus
Chapter 16 gravity (16.3.2), Rayleigh damping <= 2.5% (16.3.5) on the elastic elements + mass, and modal data
for the period range (16.2.3.1). Nothing is re-derived from the Steltic package here -- one model, three analyses.
"""
from __future__ import annotations
import math
import numpy as np
import openseespy.opensees as ops
from pushover import nonlinear_model as NM

G_IN = 386.4


def ch16_gravity(pkg, ch16, live_psf=None, roof_live_psf=20.0):
    """16.3.2: 1.0 D + 0.5 L, L = 40% of unreduced live (<= 100 psf) / 80% (> 100 psf). D from the recorded seismic
    mass (D + cladding). Spread equally over each level's column nodes (same idealisation as the pushover tool)."""
    g = ch16["gravity"]
    lv = NM.levels(pkg)
    Lpsf = live_psf if live_psf is not None else (pkg.basis.L_floor_psf or 50.0)
    loads, table, sumD, sumL = {}, [], 0.0, 0.0
    for k, z, master, slaves in lv:
        WD = pkg.model.masses.get(master, [0] * 6)[0] * G_IN
        xs = [pkg.model.nodes[n][0] for n in slaves]; ys = [pkg.model.nodes[n][1] for n in slaves]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys)) / 144.0
        L0 = roof_live_psf if k == len(lv) else Lpsf
        f = g["live_factor_gt100psf"] if L0 > 100 else g["live_factor_le100psf"]
        Lexp = g["combination_factor"] * f * L0 * area / 1000.0            # 0.5 x (0.4 or 0.8) x L0
        QG = WD + Lexp
        sumD += WD; sumL += Lexp
        for n in slaves:
            loads[n] = loads.get(n, 0.0) - QG / len(slaves)
        table.append(dict(level=k, z_in=z, D_kip=round(WD, 1), Lexp_kip=round(Lexp, 1), QG_kip=round(QG, 1), nodes=len(slaves)))
    no_live_case_needed = not (sumL <= g["exception_live_over_dead"] * sumD and Lpsf < 100)
    return loads, table, dict(sum_D=sumD, sum_Lexp=sumL, ratio=sumL / sumD, no_live_case_needed=no_live_case_needed)


def build(pkg, prm, ch16, PG, member_nseg=None, plasticity=None):
    """Nonlinear model (same builder as the pushover) -> hinges registry + element lists for damping.

    Product default for NLRHA CLI is ModIMK (imk); fibre via ladder climb / --plasticity fibre.
    Override via args / SNL_* env / numerics.plasticity.
    """
    import os
    if member_nseg is not None:
        os.environ["SNL_MEMBER_NSEG"] = str(member_nseg)
    if plasticity is not None:
        os.environ["SNL_PLASTICITY"] = str(plasticity)
    hinges, stats = NM.build_nonlinear(pkg, prm, PG, verbose=True,
                                       member_nseg=member_nseg, plasticity=plasticity)
    # Fibre: all forceBeamColumn tags for Rayleigh region. IMK: elastic_ele_tags (RBS extras) or pack tags.
    if stats.get("plasticity") == "fibre" and stats.get("fibre_eles"):
        elastic_eles = list(stats["fibre_eles"])
    elif stats.get("elastic_ele_tags"):
        elastic_eles = list(stats["elastic_ele_tags"])
    else:
        elastic_eles = [e["tag"] for e in pkg.model.elements if "etype" not in e and NM.member_kind(pkg, e) in ("col", "beam")]
        nseg = max(1, int(stats.get("member_nseg") or os.environ.get("SNL_MEMBER_NSEG") or 1))
        if nseg > 1:
            from pushover.nonlinear_model import SEG_ELE_BASE
            for e in pkg.model.elements:
                if "etype" in e or NM.member_kind(pkg, e) not in ("col", "beam"):
                    continue
                for si in range(1, nseg):
                    elastic_eles.append(SEG_ELE_BASE + e["tag"] * 100 + si)
    return hinges, stats, elastic_eles


def modal(pkg, nmodes=12):
    """Periods + effective modal mass fractions in X and Y (for the period range and the 90% rule)."""
    ops.wipeAnalysis()
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    w2 = ops.eigen("-genBandArpack", nmodes)
    lv = NM.levels(pkg)
    mk = {k: pkg.model.masses[m][0] for k, z, m, s in lv}; Mtot = sum(mk.values())
    modes = []
    for i, w in enumerate(w2):
        T = 2 * math.pi / math.sqrt(max(w, 1e-12))
        fr = {}
        Jk = {k: pkg.model.masses[m][5] for k, z, m, s in lv}
        px = {k: ops.nodeEigenvector(m, i + 1, 1) for k, z, m, s in lv}; py = {k: ops.nodeEigenvector(m, i + 1, 2) for k, z, m, s in lv}
        pr = {k: ops.nodeEigenvector(m, i + 1, 6) for k, z, m, s in lv}
        Mn = sum(mk[k] * (px[k] ** 2 + py[k] ** 2) + Jk[k] * pr[k] ** 2 for k in mk)      # full generalised mass (x, y, torsion)
        for d, phi in (("X", px), ("Y", py)):
            Ln = sum(mk[k] * phi[k] for k in phi)
            fr[d] = (Ln ** 2 / Mn / Mtot) if Mn > 0 else 0.0
        modes.append(dict(mode=i + 1, T=T, fx=fr["X"], fy=fr["Y"]))
    T1x = max(modes, key=lambda m: m["fx"])["T"]; T1y = max(modes, key=lambda m: m["fy"])["T"]
    cx = cy = 0.0; T90 = None
    for m in modes:                                 # cumulative mass, both directions -> the period at which both reach 90%
        cx += m["fx"]; cy += m["fy"]
        if cx >= 0.9 and cy >= 0.9:
            T90 = m["T"]; break
    return dict(modes=modes, T1x=T1x, T1y=T1y, T90=T90, cum_x=cx, cum_y=cy)


def set_damping(xi, T1, elastic_eles, T_upper_ratio=0.2):
    """Rayleigh damping xi at T1 and at 0.2 T1; mass-proportional on all nodes, initial-stiffness-proportional only
    on the elastic beam-column elements (the rigid hinge springs would otherwise attract spurious damping forces)."""
    w1 = 2 * math.pi / T1; w2 = 2 * math.pi / (T_upper_ratio * T1)
    a0 = xi * 2 * w1 * w2 / (w1 + w2); a1 = xi * 2 / (w1 + w2)
    ops.rayleigh(a0, 0.0, 0.0, 0.0)                              # mass-proportional, whole model
    ops.region(99, "-ele", *elastic_eles, "-rayleigh", 0.0, 0.0, a1, 0.0)   # K_init-proportional, elastic elements only
    return dict(xi=xi, a0=a0, a1=a1, T1=T1, T2=T_upper_ratio * T1)
