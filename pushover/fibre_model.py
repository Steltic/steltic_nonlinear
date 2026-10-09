"""fibre_model.py -- distributed-plasticity forceBeamColumn + fibre sections for NSP/NLRHA.

Ported from the MC4 fibre mesh-probe fork (proven L1 recipe: nseg=4, nip=5, nf_flange=8,4 / nf_web=16,2).
Braces remain nonlinear corotTruss (BRBs: C3.3 BRB material, NL-02; NLRHA physical-theory braces when configured,
NL-10). EBF links get a Table C2.4 shear spring in series with their fibre chain (NL-03). Panel zones are rigid on this
path (scissors dropped — disclosed). RBS beams are meshed with the reduced section (NL-07, below).
NO member strength degradation in the fibres (Steel01, 1% hardening, no local buckling / fracture) -- stated in
stats["degradation"] / stats["model_warnings"] (NL-10).

Component monitoring (NL-01, ASCE 41-23 7.4.3.3.1 / 7.5.3): every beam and column end that is not
pin-released is registered as a monitored plastic-hinge region (`form="fibre_end"`). Its plastic rotation
is the integral of the plastic curvature over the end region of the member,
    theta_p = sum over the region's forceBeamColumn segments of (theta_J,p - theta_I,p),
read from OpenSees' `plasticDeformation` response (basic chord rotations minus the initial-flexibility
elastic part, i.e. "chord rotation minus elastic", the section-curvature x Lp integral with Lp = the region
length). The region is the end segment (length L/nseg), or for an RBS end the stub plus the reduced
segment. With nseg = 1 the member's own end rotations theta_I,p / theta_J,p are used. The beam/column
a, b, c and IO/LS/CP limits are the ones supplied in hinge_params (AISC 342-22 Tables C5.5 / C3.6 / C2.2 as
the engineer verified them), built by hinge_models.beam_hinge / column_hinge exactly as on the IMK path;
force-controlled columns (P_G/P_ye above the supplied limit) get no deformation monitor.

RBS (NL-07): FR beam ends whose section the design package records as RBS (package_reader.beam_details;
AISC 358-22 5.7 default geometry where the frame is RBS but a section has no recorded cut) are meshed
  joint --[full stub, a]-- --[reduced segment, b]-- ...interior... --[reduced, b]-- --[stub, a]-- joint
and the reduced segment uses fibre sections whose flange width follows the circular cut
(bf - 2 c(x), c(x) from the radius R = (4c^2 + b^2) / 8c) at each integration point (UserDefined Lobatto).

Env knobs (also accepted as kwargs from build_nonlinear):
  SNL_MEMBER_NSEG, SNL_FIBRE_NIP, SNL_FIBRE_NF_FLANGE, SNL_FIBRE_NF_WEB, SNL_FIBRE_RESIDUAL
"""
from __future__ import annotations
import math
import openseespy.opensees as ops
from . import hinge_models as HM
from . import sections_db as SDB
from .nonlinear_model import (
    E_KSI_AL, MAT_BASE, RIGID_T, RIGID_R,
    member_kind, strong_I_slot, strong_rot_dof, _dir_vec, beam_params_for, fr_column_ends,
    element_context, build_brace, link_shear_spring, finish_stats, _pt_mass_balance,
)

SEG_NODE_BASE = 70_000_000
SEG_ELE_BASE = 80_000_000
FIB_SEC_BASE = 90_000_000
FIB_PIN_NODE = 92_000_000
FIB_PIN_ELE = 93_000_000
FIB_HINGE_BASE = 94_000_000   # monitored plastic-hinge regions: FIB_HINGE_BASE + ele*10 + end (registry keys, not elements)
FIB_INT_BASE = 96_000_000     # UserDefined beamIntegration tags of the RBS segments


def _lobatto01(n):
    """Gauss-Lobatto points and weights mapped to [0, 1] (weights sum to 1)."""
    import numpy as np
    from numpy.polynomial import legendre as Lg
    n = max(2, int(n))
    c = [0.0] * (n - 1) + [1.0]                       # P_{n-1}
    inner = np.sort(np.real(Lg.legroots(Lg.legder(c)))) if n > 2 else np.array([])
    x = np.concatenate(([-1.0], inner, [1.0]))
    w = 2.0 / (n * (n - 1) * Lg.legval(x, c) ** 2)
    return [float(v) for v in (x + 1.0) / 2.0], [float(v) for v in w / 2.0]


def rbs_cut_depth(x, b, c):
    """Depth of the circular RBS cut at distance x from the cut centre (AISC 358-22 Fig. 5.1):
    radius R = (4c^2 + b^2) / (8c); depth(x) = c - (R - sqrt(R^2 - x^2)); 0 at x = +-b/2."""
    if c <= 0 or abs(x) >= 0.5 * b:
        return 0.0
    R = (4.0 * c * c + b * b) / (8.0 * c)
    return max(0.0, c - (R - math.sqrt(max(R * R - x * x, 0.0))))


def _w_reduced(builder, secTag, label, cut, nf_flange, nf_web, residual):
    """Fibre W section (depth along local z, beam orientation) with both flanges trimmed by `cut` at each tip
    (flange width bf - 2 cut). Same fibre layout and residual-stress pattern as FiberSectionBuilder.w_shape."""
    from steltic_ddm.sections_fiber import shape, _f, G_KSI
    r = shape(label)
    d, tw, bf, tf = (_f(r["d"]), _f(r["tw"]), _f(r["bf"]), _f(r["tf"]))
    J = _f(r["J"]) or 1.0
    hw = d - 2 * tf
    bfr = bf - 2.0 * cut
    if bfr <= 0.2 * bf:
        raise ValueError("RBS cut %.3f in leaves %.3f in of a %.3f in flange (%s)" % (cut, bfr, bf, label))
    Af, Aw = bf * tf, hw * tw
    res = builder.residual if residual is None else residual
    if res == "lehigh":
        rc = builder.sigma_rc; rt = rc * Af / (Af + Aw)
        def sig_fl(zfrac): return rt + (-rc - rt) * zfrac
        sig_web = rt
    elif res == "eccs":
        a = 0.5 if d / bf <= 1.2 else 0.3
        def sig_fl(zfrac): return a * (1 - 2 * zfrac)
        sig_web = a
    else:
        def sig_fl(zfrac): return 0.0
        sig_web = 0.0
    ops.section("Fiber", secTag, "-GJ", G_KSI * J * (2 * bfr * tf + Aw) / (2 * Af + Aw))
    nb, nt = nf_flange
    dz = bfr / nb
    for i in range(nb):
        zc = -bfr / 2 + (i + 0.5) * dz
        mt = builder._mat(sig_fl(abs(zc) / (bf / 2)))
        for j in range(nt):
            for sgn in (+1, -1):
                yc = sgn * (d / 2 - (j + 0.5) * tf / nt)
                ops.fiber(zc, yc, dz * tf / nt, mt)
    nwy, nwz = nf_web
    mt = builder._mat(sig_web)
    dy = hw / nwy; dzw = tw / nwz
    for j in range(nwy):
        yc = -hw / 2 + (j + 0.5) * dy
        for q in range(nwz):
            zc = -tw / 2 + (q + 0.5) * dzw
            ops.fiber(zc, yc, dy * dzw, mt)
    builder.log.append((secTag, label, "W-RBS", 2 * nb * nt + nwy * nwz, "cut=%.3f in residual=%s" % (cut, res)))


def build_fibre(pkg, prm, PG, verbose=True, nseg=4, nip=5, nf_flange=(8, 4), nf_web=(16, 2), residual="none"):
    """Distributed-plasticity NLRHA probe: forceBeamColumn + fibre W/HSS sections; no IMK end springs.

    Braces remain nonlinear corotTruss (same as IMK path). Released major-axis beam/col ends use
    rigid zeroLength pins (GMNIA-style). Plasticity is along the member via fibres + Lobatto IP.
    """
    from steltic_ddm.sections_fiber import FiberSectionBuilder
    from steltic_ddm import sections_fiber as SFB
    m = pkg.model
    ops.wipe(); ops.model("basic", "-ndm", m.ndm, "-ndf", m.ndf)
    for t, xyz in m.nodes.items():
        ops.node(t, *xyz)
    for t, fl in m.fixes.items():
        ops.fix(t, *fl)
    tiny = 1e-8 * min((v[0] for v in m.masses.values() if v[0] > 0), default=1.0)   # R2 patch (NL-R2-01b): masters carry explicit 0.0 masses when the CoM node holds the mass
    for t in m.nodes:
        ops.mass(t, *([tiny] * 6))
    for t, mv in m.masses.items():
        ops.mass(t, *[mv[i] + tiny for i in range(6)])
    for t, (ty, vx, vy, vz) in m.transfs.items():
        ops.geomTransf(ty, t, vx, vy, vz)
    for t, a in m.materials.items():
        if t not in (1, 2):
            ops.uniaxialMaterial(*a)
    ops.uniaxialMaterial("Elastic", 1, RIGID_T)
    ops.uniaxialMaterial("Elastic", 2, RIGID_R)
    mt = (prm.get("material") or {})
    Fy = float(mt.get("Fy_ksi", 50.0)) * float(mt.get("Ry_expected", 1.0))
    builder = FiberSectionBuilder(ops, Fy=Fy, hardening=0.01, residual=residual, elastic=False, mat_tag0=1000)
    # denser fibre grid than default GMNIA (8,2)/(12,1) — probe "finer mesh for plasticity"
    builder_nf = dict(nf_flange=nf_flange, nf_web=nf_web)
    hinges = {}
    mat = MAT_BASE
    stats = dict(col=0, beam=0, brace=0, brace_nonlinear=0, force_controlled=0, released_ends=0,
                 panel_zones=0, panel_zone_mode="rigid", plasticity="fibre", member_nseg=nseg,
                 fibre_eles=[], fibre_secs=0, monitored_ends=0, monitored_beam_ends=0, monitored_col_ends=0,
                 rbs_beams=0, rbs_ends=0, rbs_default_geometry=0, rbs_unfit=[], beam_detail_notes=[],
                 hinge_monitor="fibre_end: plastic curvature integrated over the end region (plasticDeformation)")
    sec_cache = {}  # (section, kind) -> secTag
    nseg = max(1, int(nseg))
    pin_count = 0

    def _fibre_sec(sec, kind, Fy_hss=None):
        key = (str(sec).upper(), kind)
        if key in sec_cache:
            return sec_cache[key]
        tag = FIB_SEC_BASE + len(sec_cache) + 1
        axis = "y" if kind == "col" else "z"
        lab = str(sec)
        try:
            if lab.upper().startswith("HSS") and SFB.hss_dims(lab):     # NL-R2-28: fractional labels; HSS Fye (A500)
                builder.hss_rect(tag, lab, n_per_side=max(8, nf_web[0] // 2), n_thick=2, residual=residual, Fy=Fy_hss)
            elif lab.upper().startswith(("HSS", "PIPE")):
                builder.hss_round(tag, lab, Fy=Fy_hss)
            else:
                builder.w_shape(tag, lab, axis=axis, nf_flange=nf_flange, nf_web=nf_web, residual=residual)
        except Exception as ex:
            # fallback elastic properties from element if shape missing
            raise RuntimeError("fibre section %s (%s): %s" % (lab, kind, ex))
        ops.beamIntegration("Lobatto", tag, tag, nip)
        sec_cache[key] = tag
        stats["fibre_secs"] += 1
        return tag

    def _fibre_sec_cut(sec, cut):
        """Beam fibre section with the RBS flange trimmed by `cut` (in) at each tip."""
        if cut <= 1e-3:
            return _fibre_sec(sec, "beam")
        key = (str(sec).upper(), "beam", round(cut, 3))
        if key in sec_cache:
            return sec_cache[key]
        tag = FIB_SEC_BASE + len(sec_cache) + 1
        _w_reduced(builder, tag, str(sec), cut, nf_flange, nf_web, residual)
        sec_cache[key] = tag
        stats["fibre_secs"] += 1
        return tag

    rbs_int_cache = {}

    def _rbs_integration(sec, geo):
        """UserDefined Lobatto integration over one RBS segment (length b): the section at each point carries
        the circular-cut flange width there, the narrowest (c) at the centre."""
        key = (str(sec).upper(), round(geo["b_in"], 3), round(geo["c_in"], 3))
        if key in rbs_int_cache:
            return rbs_int_cache[key]
        xs, ws = _lobatto01(nip)
        secs = [_fibre_sec_cut(sec, rbs_cut_depth((x - 0.5) * geo["b_in"], geo["b_in"], geo["c_in"])) for x in xs]
        tag = FIB_INT_BASE + len(rbs_int_cache) + 1
        ops.beamIntegration("UserDefined", tag, len(xs), *secs, *xs, *ws)
        rbs_int_cache[key] = tag
        return tag

    def _pin(grid, dup, released_dofs):
        nonlocal pin_count
        pin_count += 1
        dirs = [d for d in range(1, 7) if d not in released_dofs]
        mats = [1 if d <= 3 else 2 for d in dirs]
        zl = FIB_PIN_ELE + pin_count
        ops.element("zeroLength", zl, grid, dup, "-mat", *mats, "-dir", *dirs)
        return zl

    ctx = element_context(pkg, prm)
    for e in m.elements:
        if "etype" in e:
            kind = member_kind(pkg, e); sec = pkg.schedule.get(e["tag"], {}).get("section")
            if e["etype"] in ("Truss", "truss", "corotTruss") and kind == "brace" and sec and str(sec).upper() != "GHOST":
                mat = build_brace(pkg, e, sec, prm, mat, hinges, stats, ctx)        # buckling brace / BRB (NL-02) / physical theory (NL-10)
            else:
                ops.element(*e["raw"])
            stats["brace"] += 1; continue
        kind = member_kind(pkg, e)
        sec = pkg.schedule.get(e["tag"], {}).get("section")
        p1, p2 = m.nodes[e["n1"]], m.nodes[e["n2"]]
        d, L = _dir_vec(p1, p2)
        if kind == "brace" and sec:
            mat = build_brace(pkg, e, sec, prm, mat, hinges, stats, ctx)
            stats["brace"] += 1
            continue
        if sec is None or kind not in ("col", "beam"):
            args = [e["A"], e["E"], e["G"], e["J"], e["Iy"], e["Iz"], e["transf"]] + (e["release"] or [])
            ops.element("elasticBeamColumn", e["tag"], e["n1"], e["n2"], *args); stats["brace"] += 1
            continue
        slot = strong_I_slot(pkg, e, kind)
        rel = e["release"] or []
        flag = "-releasey" if slot == "Iy" else "-releasez"
        relz = int(rel[rel.index(flag) + 1]) if flag in rel else 0
        hinge_i, hinge_j = relz not in (1, 3), relz not in (2, 3)
        dof = strong_rot_dof(pkg, e, kind)
        binfo = None
        if kind == "col":
            spec = HM.column_hinge(sec, L, PG.get(e["tag"], 0.0), prm)
        elif ctx["links"].get(e["tag"]) and not ctx["links"][e["tag"]].get("skipped"):
            # NL-03 EBF link: its fibre end regions are monitored with the link FLEXURAL acceptance (Table C2.2 row 1,
            # interpolated per the C2.2 footnote -- ~0 for a shear-controlled link), not the C5.5 FR-beam rows; no RBS cut.
            fr_i = fr_j = False
            spec = HM.link_specs(sec, L, prm)[1]
        else:
            fr_i, fr_j = fr_column_ends(pkg, e)
            prm_b, binfo = beam_params_for(pkg, prm, sec, fr=(fr_i or fr_j))
            try:
                spec = HM.beam_hinge(sec, L, prm_b)
            except Exception as ex:                     # no backbone for this section: NOT monitored, said loudly
                spec = None
                stats.setdefault("unmonitored", []).append(dict(ele=e["tag"], section=sec, kind=kind, why=str(ex)[:160]))
            for note in binfo.get("notes", ()):
                if note not in stats["beam_detail_notes"]:
                    stats["beam_detail_notes"].append(note)
        if spec is not None and spec.force_controlled:  # still fibre (distributed) but no deformation monitor
            stats["force_controlled"] += 1
        end1, end2 = e["n1"], e["n2"]
        lk = ctx["links"].get(e["tag"])
        if lk and not lk.get("skipped") and kind == "beam":
            # NL-03 EBF link: shear spring (Table C2.4 backbone) in series with the fibre chain (flexural yielding in fibres)
            end1, mat = link_shear_spring(e, p1, p2, sec, prm, mat, hinges, stats, tiny=tiny)
        # major-axis releases -> pin (no rotational continuity); unreleased -> continuous fibre
        if not hinge_i:
            end1 = FIB_PIN_NODE + e["tag"] * 10 + 1; ops.node(end1, *p1); ops.mass(end1, *([tiny] * 6))
            _pin(e["n1"], end1, {dof}); stats["released_ends"] += 1
        if not hinge_j:
            end2 = FIB_PIN_NODE + e["tag"] * 10 + 2; ops.node(end2, *p2); ops.mass(end2, *([tiny] * 6))
            _pin(e["n2"], end2, {dof}); stats["released_ends"] += 1
        secTag = _fibre_sec(sec, kind, Fy_hss=(spec.Fye_ksi if (spec is not None and SDB.parse_hss_label(sec)) else None))
        # stations along the member: (s0, s1, "full"|"rbs")
        geo = (binfo or {}).get("rbs")
        rbs_i = rbs_j = False
        if geo is not None and (hinge_i or hinge_j):
            a_r, b_r = geo["a_in"], geo["b_in"]
            if 2.0 * (a_r + b_r) < L - 1e-6:
                rbs_i, rbs_j = fr_i, fr_j                   # the cut only at FR beam-to-COLUMN ends
            else:
                stats["rbs_unfit"].append(dict(ele=e["tag"], section=sec, L=L, a=a_r, b=b_r))
        segs, lo, hi, tail = [], 0.0, L, []
        if rbs_i:
            segs += [(0.0, a_r, "full"), (a_r, a_r + b_r, "rbs")]; lo = a_r + b_r
        if rbs_j:
            tail = [(L - a_r - b_r, L - a_r, "rbs"), (L - a_r, L, "full")]; hi = L - a_r - b_r
        n_int = max(2, nseg - 2) if (rbs_i or rbs_j) else nseg
        segs += [(lo + (hi - lo) * k / n_int, lo + (hi - lo) * (k + 1) / n_int, "full") for k in range(n_int)]
        segs += tail
        chain = [end1]
        for si in range(1, len(segs)):
            f = segs[si][0] / L
            xyz = [p1[k] + (p2[k] - p1[k]) * f for k in range(3)]
            mid = SEG_NODE_BASE + e["tag"] * 100 + si
            ops.node(mid, *xyz); ops.mass(mid, *([tiny] * 6))
            chain.append(mid)
        chain.append(end2)
        seg_tags = []
        for si, (s0, s1, kd) in enumerate(segs):
            etag = e["tag"] if si == 0 else (SEG_ELE_BASE + e["tag"] * 100 + si)
            integ = secTag if kd == "full" else _rbs_integration(sec, geo)
            ops.element("forceBeamColumn", etag, chain[si], chain[si + 1], e["transf"], integ, "-iter", 20, 1e-8)
            stats["fibre_eles"].append(etag); seg_tags.append(etag)
        if rbs_i or rbs_j:
            stats["rbs_beams"] += 1; stats["rbs_ends"] += int(rbs_i) + int(rbs_j)
            if "AISC 358-22 5.7" in str(geo.get("source", "")):
                stats["rbs_default_geometry"] += 1
        # monitored plastic-hinge regions (NL-01). Basic system of a 3-D forceBeamColumn:
        # [eps, theta_zI, theta_zJ, theta_yI, theta_yJ, phi]; beams bend strong-axis about local y (fibre depth
        # along local z), columns about local z (depth along local y).
        comp = ((3, 4) if kind == "beam" else (1, 2)) if m.ndm == 3 else (1, 2)
        if spec is not None and not spec.force_controlled:
            for end, on, rbs_end in ((1, hinge_i, rbs_i), (2, hinge_j, rbs_j)):
                if not on:
                    continue
                if len(seg_tags) == 1:
                    region, mode = seg_tags, ("endI" if end == 1 else "endJ")
                    Lp = L
                else:
                    k = 2 if rbs_end else 1
                    idx = list(range(k)) if end == 1 else list(range(len(segs) - k, len(segs)))
                    region, mode = [seg_tags[q] for q in idx], "integral"
                    Lp = sum(segs[q][1] - segs[q][0] for q in idx)
                hk = FIB_HINGE_BASE + e["tag"] * 10 + end
                is_link = bool(lk and not lk.get("skipped") and kind == "beam")
                hinges[hk] = dict(ele=e["tag"], end=end, kind=kind, section=("%s link" % sec) if is_link else sec,
                                  dof=dof, K0=None, mat=None,
                                  form="fibre_end", segs=region, comp=comp, mode=mode, Lp_in=Lp, rbs=rbs_end,
                                  rbs_offset_in=(a_r + 0.5 * b_r) if rbs_end else 0.0,          # NL-R2-16: RBS centre (Emc beam shear)
                                  node=e["n1"] if end == 1 else e["n2"], z=p1[2] if end == 1 else p2[2], spec=spec)
                stats["monitored_ends"] += 1
                stats["monitored_%s_ends" % ("beam" if kind == "beam" else "col")] += 1
        stats[kind] += 1
    stats["panel_zone_registry"] = {}
    for perp, master, slaves in m.diaphragms:
        ops.rigidDiaphragm(perp, master, *slaves)
    _pt_mass_balance(pkg, ctx, stats)
    finish_stats(stats, ctx, prm, "fibre")
    if verbose:
        nfib = sum(x[3] for x in builder.log) if builder.log else 0
        print("[nonlinear_model] FIBRE plasticity: cols %d beams %d braces %d (nl %d, BRB %d, physical-theory %d) EBF links %d nseg=%d nip=%d secs=%d fibres~%d released_ends=%d"
              % (stats["col"], stats["beam"], stats["brace"], stats["brace_nonlinear"], stats.get("brb", 0), stats.get("brace_physical_theory", 0),
                 stats.get("links", 0), nseg, nip, stats["fibre_secs"], nfib, stats["released_ends"]))
        for w in stats.get("model_warnings") or []:
            print("  !! " + w)
        print("[nonlinear_model] FIBRE monitored hinge regions: %d (beam ends %d, column ends %d; force-controlled cols %d) | RBS beams %d (ends %d, AISC 358 default geometry %d, unfit %d)"
              % (stats["monitored_ends"], stats["monitored_beam_ends"], stats["monitored_col_ends"], stats["force_controlled"],
                 stats["rbs_beams"], stats["rbs_ends"], stats["rbs_default_geometry"], len(stats["rbs_unfit"])))
        for note in stats["beam_detail_notes"][:12]:
            print("[nonlinear_model]   beam detail: %s" % note)
    return hinges, stats


