"""model.py -- the NLRHA model is the Pushover Analyst's hinge model (steltic_pushover.nonlinear_model) plus
Chapter 16 gravity (16.3.2), Rayleigh damping <= 2.5% (16.3.5) on the elastic elements + mass, and modal data
for the period range (16.2.3.1). Nothing is re-derived from the Steltic package here -- one model, three analyses.

The builder is told it is building for the NLRHA (prm["_analysis"] = "nlrha"), which switches the CBF braces to the
physical-theory fibre brace with fatigue when brace_axial.nlrha_element = "physical_theory" (NL-10). BRBs (NL-02) and
EBF links (NL-03) come from the same builder. degradation_statement() writes the ASCE 7-22 16.3.1 statement of what
strength / stiffness degradation the model contains (stats["degradation_16_3_1"]) for the report.
"""
from __future__ import annotations
import math
import numpy as np
import openseespy.opensees as ops
from pushover import nonlinear_model as NM

G_IN = 386.4


def ch16_gravity(pkg, ch16, live_psf=None, roof_live_psf=None, with_live=True):
    """ASCE 7-22 16.3.2 expected gravity, applied as nodal loads on the nonlinear model.

    with_live=True : 1.0 D + 0.5 L, L = 80% of unreduced live loads > 100 psf, 40% of all other unreduced live.
    with_live=False: 1.0 D (the "without live load" case of 16.3.2).
    D per level = recorded seismic weight (mass x g: D + cladding + the cfg's extra mass). L from the framed floor
    plate (nlrha/gravity.py): floor bays at cfg L_by_level[k] / L_floor, roof bays (top level and every set-back /
    lower roof) at cfg Lr; each bay lumped in equal shares to its column corners, so gravity follows the tributary
    areas (16.3.3).

    Exception check (16.3.2): the no-live case may be skipped only if sum(0.5 L) <= 25% of sum(D) AND the live load
    intensity L0 is below 100 psf over at least 75% of the structure's area. Both conditions are evaluated from the
    per-bay loads and reported (`ratio`, `share_L0_lt_100`).

    Returns (loads {node: Pz kip, -ve down}, table [per level], split dict)."""
    from . import gravity as GR
    g = ch16["gravity"]
    trib, info = GR.tributary(pkg, live_psf=live_psf, roof_live_psf=roof_live_psf)
    loads, table, sumD, sumL = {}, [], 0.0, 0.0
    area_all = area_lt100 = 0.0
    f_le, f_gt, c = g["live_factor_le100psf"], g["live_factor_gt100psf"], g["combination_factor"]
    for lv in trib:
        WD = NM.level_mass(pkg, lv["master"], [s for (_k, _z, _m, s) in NM.levels(pkg) if _m == lv["master"]][0])["m"] * G_IN   # R2 patch
        A = sum(lv["node_area"].values())
        fL = f_gt if lv["L0_floor_psf"] > 100 else f_le
        fR = f_gt if lv["Lr_psf"] > 100 else f_le
        Lexp_floor = c * fL * lv["L0_kip"]; Lexp_roof = c * fR * lv["Lr_kip"]
        Lexp = (Lexp_floor + Lexp_roof) if with_live else 0.0
        for n, a in lv["node_area"].items():
            pz = WD * a / A if A > 0 else 0.0
            if with_live:
                pz += c * fL * lv["node_L0"].get(n, 0.0) + c * fR * lv["node_Lr"].get(n, 0.0)
            loads[n] = loads.get(n, 0.0) - pz
        sumD += WD; sumL += Lexp_floor + Lexp_roof
        area_all += lv["area_ft2"]
        area_lt100 += (lv["floor_ft2"] if lv["L0_floor_psf"] < 100 else 0.0) + (lv["roof_ft2"] if lv["Lr_psf"] < 100 else 0.0)
        table.append(dict(level=lv["k"], z_in=lv["z"], area_ft2=round(lv["area_ft2"]), floor_ft2=round(lv["floor_ft2"]), roof_ft2=round(lv["roof_ft2"]),
                          L0_psf=lv["L0_floor_psf"], Lr_psf=lv["Lr_psf"], D_kip=round(WD, 1),
                          Lexp_floor_kip=round(Lexp_floor, 1), Lexp_roof_kip=round(Lexp_roof, 1), Lexp_kip=round(Lexp, 1),
                          QG_kip=round(WD + Lexp, 1), nodes=len(lv["node_area"]), method=lv["method"]))
    ratio = sumL / sumD if sumD else float("inf")
    share = area_lt100 / area_all if area_all else 0.0
    exception = (ratio <= g["exception_live_over_dead"]) and (share >= 0.75)
    split = dict(sum_D=sumD, sum_Lexp=(sumL if with_live else 0.0), sum_Lexp_with_live=sumL, ratio=ratio, share_L0_lt_100=share,
                 exception_applies=exception, no_live_case_needed=not exception, with_live=with_live,
                 basis=dict(L_floor_psf=info["L_floor_used"], L_floor_from=info["L_floor_basis"], Lr_psf=info["Lr_used"], Lr_from=info["Lr_basis"],
                            L_by_level=info["L_by_level"], area="framed bays from the package geometry (nlrha/gravity.py)",
                            roof_live_in_L="roof live Lr included at the 40% factor (conservative; 16.3.2 names L only)"))
    return loads, table, split


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
    prm_b = dict(prm); prm_b["_analysis"] = "nlrha"                 # NL-10: physical-theory braces etc. for the NLRHA only
    hinges, stats = NM.build_nonlinear(pkg, prm_b, PG, verbose=True,
                                       member_nseg=member_nseg, plasticity=plasticity)
    if isinstance(prm, dict) and not prm_b.get("verified"):          # provenance (NL-19): a builder used a non-user-supplied
        prm["verified"] = False                                      # value on the NLRHA copy -> the run's effective flag drops
        prm["_used_unverified"] = prm_b.get("_used_unverified") or prm.get("_used_unverified") or {}
    stats["degradation_16_3_1"] = degradation_statement(prm, stats)
    stats["lambda_summary"] = lambda_summary(hinges)                 # NL-R2-05: the Lambda values the run built
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


def lambda_summary(hinges):
    """NL-R2-05: the IMK cyclic-deterioration Lambda the model actually carries, per member kind:
    {kind: {n, n_zero, min, max}} over the beam / column hinges (fibre models carry none -> {})."""
    out = {}
    for h in hinges.values():
        sp = h.get("spec")
        if h.get("kind") not in ("beam", "col") or sp is None or not hasattr(sp, "Lambda"):
            continue
        out.setdefault(h["kind"], []).append(float(getattr(sp, "Lambda") or 0.0))
    return {k: dict(n=len(v), n_zero=sum(1 for x in v if x <= 0.0), min=round(min(v), 4), max=round(max(v), 4)) for k, v in out.items()}


def degradation_statement(prm, stats):
    """ASCE 7-22 16.3.1 statement (NL-10): 'Degradation in element strength or stiffness shall be included in the
    hysteretic models unless it can be demonstrated that response is not sufficient to produce these effects.'

    Returns dict(items=[(component, modelled text, ok)], demonstrated=bool, text=str). `demonstrated` is True only when
    every component family present has its degradation modelled (or, for BRBs, is covered by AISC 342-22 Commentary E3:
    no strength/stiffness degradation expected). It never claims a demonstration the model does not make."""
    deg = stats.get("degradation") or {}
    items = []
    plast = deg.get("plasticity", stats.get("plasticity"))
    lam_mode = str((prm.get("cyclic_deterioration") or {}).get("mode") or "").lower()
    if plast == "fibre":
        items.append(("Beams / columns", deg.get("members", "fibre: no strength degradation"), False))
    else:
        on = "deterioration ON" in deg.get("members", "")
        items.append(("Beams / columns (IMK hinges)", deg.get("members", "?") + ("" if on else
                      " -- 16.3.1 NOT satisfied for these members unless the record peaks stay below the capping rotation a"), on))
    if "braces" in deg:
        items.append(("Buckling braces", deg["braces"], "physical-theory" in deg["braces"]))
    if "brb" in deg:
        items.append(("Buckling-restrained braces", deg["brb"], True))
    if "links" in deg:
        items.append(("EBF links (shear)", deg["links"], True))
    demonstrated = all(ok for _, _, ok in items)
    text = ("ASCE 7-22 16.3.1 -- degradation in the hysteretic models: " + "; ".join("%s: %s" % (c, t) for c, t, _ in items)
            + (". All component families present include strength/stiffness degradation." if demonstrated else
               ". NOT DEMONSTRATED for: %s." % ", ".join(c for c, _, ok in items if not ok)))
    if lam_mode == "" and plast != "fibre":
        text += " (cyclic_deterioration taken from the repository template -- literature Lambda expressions, verify.)"
    return dict(items=[dict(component=c, modelled=t, ok=ok) for c, t, ok in items], demonstrated=demonstrated, text=text)


def _mass_dofs():
    """[(node, [m1..m6])] for every node carrying mass (floor / CoM nodes, brace nodes, seeded tiny masses). The full
    mass matrix is needed: the eigenvectors are orthogonal with respect to it, and leaving the brace nodes out inflates
    the participation of the brace-local modes (Ex30: 118 %)."""
    out = []
    for n in ops.getNodeTags():
        try:
            m = list(ops.nodeMass(n))
        except Exception:                                           # noqa: BLE001
            continue
        if any(v > 0.0 for v in m):
            out.append((n, m))
    return out


def modal(pkg, nmodes=12):
    """Periods + effective modal mass fractions in X and Y (for the period range and the 90% rule).

    NL-R2-09 fix 2: participation from the FULL lumped mass matrix of the analysis model (every node that carries
    mass, all six DOFs) -- not from level masses read at the diaphragm masters. The brace nodes now carry their own mass
    and the level mass may sit on a centre-of-mass node, so a master-only sum is no longer consistent with the
    eigenvectors (it reported 118 % cumulative mass on Ex30). Mtot is the model's total translational mass per direction."""
    md = _mass_dofs()
    n_max = max(1, sum(1 for n, m in md for v in m if v > 0.0) - 1)   # ARPACK needs fewer modes than mass DOFs
    # Gold-run fix (Ex21): ARPACK can stop at its iteration limit for one mode count and converge in seconds for a larger
    # one (a larger Krylov subspace; Ex21 failed at 12 modes and converged at 24 and 48), so a failed count moves on to the
    # next one; the dense solver is the last resort, only when no count gave a usable solution.
    res, tried, failed = None, [], []
    for nm_try in (nmodes, 2 * nmodes, 4 * nmodes, 8 * nmodes):       # brace-local modes can crowd the first 12
        nm_try = min(nm_try, n_max)
        if nm_try in tried:
            break
        tried.append(nm_try)
        r = _modal_n(md, nm_try, dense=False)
        if r is None:
            failed.append(nm_try)
            print("   note: ARPACK eigen solution not usable for %d modes -- trying more modes" % nm_try, flush=True)
            continue
        res = r; res["n_modes"] = nm_try
        if res["T90"] is not None:
            break
    if res is None:
        res = _modal_n(md, failed[0], dense=True)
        res["n_modes"] = failed[0]
    return res


DENSE_EIGEN_MAX_GB = 2.0          # the dense fallback below: K, M and work ~ 3 n^2 doubles


def _participation(md, w2):
    """[(T, fx, fy)] for the solved modes, or None when the eigen solution is not usable: a non-finite or non-positive
    eigenvalue, a non-finite eigenvector, a non-positive modal mass, or a participation that overflows / exceeds the
    total mass. Gold-run fix (Ex21 on the PC run, Python 3.14): ARPACK stopped at its iteration limit ("Maximum number
    of iteration reached") without raising and returned eigenvectors of ~1e160, so L**2 overflowed and the whole
    NLRHA step crashed."""
    Mtot = {"X": sum(m[0] for n, m in md), "Y": sum(m[1] for n, m in md)}
    out = []
    try:
        for i, w in enumerate(w2):
            if not (math.isfinite(w) and w > 0.0):
                return None
            T = 2 * math.pi / math.sqrt(w)
            Mn = 0.0; L = {"X": 0.0, "Y": 0.0}
            for n, m in md:
                phi = ops.nodeEigenvector(n, i + 1)
                Mn += sum(m[d] * phi[d] * phi[d] for d in range(min(6, len(phi))))
                L["X"] += m[0] * phi[0]; L["Y"] += m[1] * phi[1]
            if not (math.isfinite(Mn) and Mn > 0.0 and all(math.isfinite(v) for v in L.values())):
                return None
            fr = {d: (L[d] ** 2 / Mn / Mtot[d]) if Mtot[d] > 0 else 0.0 for d in L}
            if any(not math.isfinite(v) or v > 1.0 + 1e-6 for v in fr.values()):
                return None
            out.append((T, fr["X"], fr["Y"]))
    except (OverflowError, ValueError, ZeroDivisionError):
        return None
    if sum(o[1] for o in out) > 1.0 + 1e-3 or sum(o[2] for o in out) > 1.0 + 1e-3:
        return None
    return out


def _modal_n(md, nmodes, dense=True):
    """Modal results for `nmodes` modes (ARPACK). An unusable ARPACK solution -> None when dense=False, otherwise the
    dense -fullGenLapack solver where its memory (DENSE_EIGEN_MAX_GB) allows (it is slow: minutes for ~4000 DOF)."""
    ops.wipeAnalysis()
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    try:
        w2 = ops.eigen("-genBandArpack", nmodes)
    except Exception:                                               # noqa: BLE001
        w2 = []
    part = _participation(md, w2) if w2 and len(w2) == nmodes else None
    if part is None and not dense:
        return None
    if part is None:
        # the dense solver when ARPACK did not converge -- only where the memory it needs is reasonable
        try:
            ndof = int(ops.systemSize())
        except Exception:                                           # noqa: BLE001
            ndof = 6 * len(ops.getNodeTags())
        gb = 3.0 * 8.0 * float(ndof) ** 2 / 1e9
        if gb > DENSE_EIGEN_MAX_GB:
            raise RuntimeError("modal analysis: ARPACK did not return a usable solution for %d modes and the dense "
                               "fallback would need ~%.1f GB for %d DOF" % (nmodes, gb, ndof))
        print("   note: ARPACK eigen solution not usable (%d modes) -- dense -fullGenLapack fallback, %d DOF (~%.2f GB)"
              % (nmodes, ndof, gb), flush=True)
        ops.wipeAnalysis()
        ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
        w2 = ops.eigen("-fullGenLapack", nmodes)
        part = _participation(md, w2)
        if part is None:
            raise RuntimeError("modal analysis: no usable eigen solution (ARPACK and the dense solver) for %d modes" % nmodes)
    modes = []
    for i, (T, fx, fy) in enumerate(part):
        modes.append(dict(mode=i + 1, T=T, fx=fx, fy=fy))
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
