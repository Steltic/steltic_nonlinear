"""
solver.py -- load-factor sweep to collapse (proportional loading), peak detection, mechanism data.

sweep(model, combo, pres, ...) applies the factored combination at lambda = 1, probes the elastic
response, picks the control DOF (largest displacement -- roof drift for lateral cases, a beam
mid-span or column shortening for gravity cases), then drives the structure with adaptive
DisplacementControl: Newton -> ModifiedNewton(-initial) -> KrylovNewton fallbacks, step halving on
failure, step growth on recovery. lambda_u is the peak load factor; the run continues into the
post-peak branch (to `post_peak` * lambda_u) to characterise ductility for the phi_s classification.

Mechanism data recorded at the peak: per-integration-point yield ratio (max fibre strain / eps_y,
from the section deformation and the section extents), per-member "hinge" flags, brace buckling
flags (compression + lateral mid-point offset beyond L/200), storey drifts.
"""
import math, time
import openseespy.opensees as ops
from .loads import lateral_direction


def _solver_settings(tol=1e-6, iters=25):
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", tol, iters, 0); ops.algorithm("Newton")


def _extents(sp):
    """(ymax, zmax) of a section in its local axes for max-strain estimates."""
    if sp["kind"] == "col":                 # depth along y
        return sp.get("d", sp.get("H", 1.0)) / 2, sp.get("bf", sp.get("B", 1.0)) / 2
    if sp["kind"] == "beam":                # depth along z
        return sp.get("bf", sp.get("B", 1.0)) / 2, sp.get("d", sp.get("H", 1.0)) / 2
    return sp.get("H", 1.0) / 2, sp.get("B", 1.0) / 2


def yield_state(model, eps_y):
    """Per element: max yield ratio over its integration points; returns dict tag -> ratio."""
    out = {}
    nip = model.nip
    for e in model.elems:
        sp = model.sec_props[e["secTag"]]
        ymax, zmax = _extents(sp)
        r = 0.0
        for ip in range(1, nip + 1):
            try:
                d = ops.eleResponse(e["tag"], "section", ip, "deformation")
            except Exception:
                continue
            if len(d) >= 3:
                eps, kz, ky = d[0], d[1], d[2]
                em = abs(eps) + abs(kz) * ymax + abs(ky) * zmax
                r = max(r, em / eps_y)
        out[e["tag"]] = r
    return out


def brace_state(model):
    """Per brace member: axial force (kip, +tension) and lateral offset of the mid node (in)."""
    out = {}
    for mtag, chain in model.sub_nodes.items():
        m = model._bt[mtag]
        if m.kind != "brace":
            continue
        first = [e for e in model.elems if e["mtag"] == mtag][0]
        try:
            N = ops.eleResponse(first["tag"], "basicForce")[0]
        except Exception:
            N = 0.0
        mid = chain[len(chain) // 2]
        d = ops.nodeDisp(mid)
        a = ops.nodeDisp(chain[0]); b = ops.nodeDisp(chain[-1])
        off = math.sqrt(sum((d[i] - 0.5 * (a[i] + b[i])) ** 2 for i in range(3)))
        L = first["L"] * (len(chain) - 1)
        out[mtag] = dict(N=N, offset=off, L=L, buckled=(N < 0 and off > L / 200.0))
    return out


def member_state(model, ys):
    """Per member: (max yield ratio over its sub-elements, fraction along the member of the worst sub-element)."""
    out = {}
    for e in model.elems:
        r = ys.get(e["tag"], 0.0)
        n = max(1, len(model.sub_nodes[e["mtag"]]) - 1)
        cur = out.get(e["mtag"])
        if cur is None or r > cur[0]:
            out[e["mtag"]] = (r, (e["s"] + 0.5) / n)
    return out


def _frame(model, eps_y, step, lam, d, ys=None, bs=None):
    """Viewer frame: masters, members at/over 0.5 eps_y with the worst sub-element location, buckled braces."""
    ys = ys if ys is not None else yield_state(model, eps_y)
    bs = bs if bs is not None else brace_state(model)
    return dict(step=step, lam=lam, d=d, disp={t: ops.nodeDisp(t) for t in model.masters},
                mem={m: (r, f) for m, (r, f) in member_state(model, ys).items() if r >= 0.5},
                buckled=[t for t, b in bs.items() if b["buckled"]])


def storey_drifts(model, dirn):
    dof = 1 if dirn == "X" else 2
    ms = model.masters
    disp = [ops.nodeDisp(t, dof) for t in ms]
    z = [model.nm.nodes[t][2] for t in ms]
    dr = []
    prev_d, prev_z = 0.0, 0.0
    for d, zz in zip(disp, z):
        dr.append((d - prev_d) / max(zz - prev_z, 1e-9)); prev_d, prev_z = d, zz
    return disp, dr


def sweep(model, combo, pres, dlam=0.02, max_steps=600, post_peak=0.85, disp_cap_factor=60.0,
          verbose=True, snapshot_every=3, time_limit=None, post_peak_steps=8, plateau_frac=0.02, frame_every=2):
    label, fD, fL, fLr, lat, col_only = combo
    t0 = time.time()
    model.build().prepare()
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    W = model.apply_gravity(fD, fL, fLr, pres)
    model.apply_lateral(lat)
    _solver_settings()
    ldir, lsgn = lateral_direction(lat)
    eps_y = model.Fy / model.builder.E

    # ---- elastic probe at lambda = 0.05 -------------------------------------------------------
    ops.integrator("LoadControl", 0.05); ops.analysis("Static")
    if ops.analyze(1) != 0:
        raise RuntimeError("elastic probe failed for %s" % label)
    if ldir:
        top = model.masters[-1]; cdof = 1 if ldir == "X" else 2
        cnode = top
    else:
        # gravity case: control the largest VERTICAL beam deflection (never a brace bow or a sway DOF)
        best, cnode, cdof = 0.0, None, 3
        for e in model.elems:
            if e["kind"] != "beam" or e["s"] != model.nsub_beam // 2:
                continue
            t = e["n1"]
            v = abs(ops.nodeDisp(t, 3))
            if v > best:
                best, cnode = v, t
    d_probe = ops.nodeDisp(cnode, cdof)
    if abs(d_probe) < 1e-12:
        raise RuntimeError("zero probe displacement -- control DOF not excited")
    du0 = d_probe / 0.05 * dlam
    du = du0
    d_cap = abs(d_probe / 0.05) * disp_cap_factor
    ops.integrator("DisplacementControl", cnode, cdof, du)

    hist = [(0.05, d_probe)]
    lam_max, step_at_max, d_at_max = 0.05, 0, d_probe; hi_at_max = 0
    first_yield = None
    snap = dict(yield_ratio={}, braces={}, drifts=None, disp=None, step=0)
    frames = []                                   # viewer frames every `frame_every` steps (+ the peak)
    disp_hist = {}                                # hist index -> master displacements (cheap; lets the peak frame be exact)
    fails = 0; consecutive_fail = 0
    log = []; plateau = False
    for step in range(1, max_steps + 1):
        ok = ops.analyze(1)
        if ok != 0:
            consecutive_fail += 1; fails += 1
            # cheap fallbacks first (failed steps are where the time goes): Krylov, then halve the step
            ops.algorithm("KrylovNewton"); ops.test("NormDispIncr", 1e-5, 30, 0)
            ok = ops.analyze(1)
            ops.algorithm("Newton"); ops.test("NormDispIncr", 1e-6, 25, 0)
            if ok != 0:
                du *= 0.5
                if abs(du) < abs(du0) / 64:
                    log.append("step %d: step size exhausted -- stopping" % step); break
                ops.integrator("DisplacementControl", cnode, cdof, du)
                log.append("step %d: halved step to %.3g" % (step, du))
                if consecutive_fail > 12:
                    log.append("too many failures"); break
                continue
        if step_at_max and step - step_at_max > post_peak_steps:
            log.append("post-peak budget (%d steps) used at step %d" % (post_peak_steps, step)); break
        consecutive_fail = 0
        if abs(du) < abs(du0):
            du = min(abs(du) * 1.5, abs(du0)) * (1 if du0 > 0 else -1)
            ops.integrator("DisplacementControl", cnode, cdof, du)
        lam = ops.getLoadFactor(1); d = ops.nodeDisp(cnode, cdof)
        hist.append((lam, d))
        hi = len(hist) - 1                        # index of this converged step in hist (failed steps are not counted)
        disp_hist[hi] = {t: ops.nodeDisp(t) for t in model.masters}
        ys = None
        if (first_yield is None and step % 2 == 0) or hi % frame_every == 0:
            ys = yield_state(model, eps_y)
            if first_yield is None and max(ys.values(), default=0.0) >= 1.0:
                first_yield = lam
        if hi % frame_every == 0:
            frames.append(_frame(model, eps_y, hi, lam, d, ys=ys))
        if lam > lam_max:
            lam_max, step_at_max, d_at_max = lam, step, d; hi_at_max = hi
            if step - snap["step"] >= snapshot_every:
                snap = dict(yield_ratio=(ys if ys is not None else yield_state(model, eps_y)), braces=brace_state(model),
                            drifts=(storey_drifts(model, ldir) if ldir else None), step=step, hist_i=hi,
                            disp={t: ops.nodeDisp(t) for t in model.masters})
        elif lam < post_peak * lam_max and step > step_at_max + 3:
            log.append("post-peak branch reached %.0f%% of lambda_u at step %d" % (100 * post_peak, step)); break
        if abs(d) > d_cap:
            log.append("control displacement cap reached at step %d" % step); break
        # plateau rule: tangent stiffness over the last 10 steps below `plateau_frac` of the elastic
        # stiffness -> a plastic plateau (mechanism / squash with hardening); lambda_u is taken here
        if len(hist) > 12 and lam >= lam_max - 1e-9:
            l0, d0 = hist[-11]
            k_el = 0.05 / abs(d_probe)
            k_t = (lam - l0) / max(abs(d - d0), 1e-12)
            if k_t < plateau_frac * k_el:
                plateau = True
                if snap["step"] < step - 1:
                    snap = dict(yield_ratio=yield_state(model, eps_y), braces=brace_state(model),
                                drifts=(storey_drifts(model, ldir) if ldir else None), step=step, hist_i=len(hist) - 1,
                                disp={t: ops.nodeDisp(t) for t in model.masters})
                log.append("plateau: tangent stiffness %.1f%% of elastic at step %d -- lambda_u taken at the plateau" % (100 * k_t / k_el, step)); break
        if time_limit and time.time() - t0 > time_limit:
            log.append("time limit reached at step %d" % step); break
        if verbose and step % 10 == 0:
            print("   %-38s step %4d  lambda %.3f  d %.3f  (%.0f s)" % (label[:38], step, lam, d, time.time() - t0), flush=True)
    # make sure lambda_u itself is a viewer frame: exact masters (recorded every step); member states from the closest
    # earlier record (a frame or the peak snapshot, at most frame_every-1 steps before the peak)
    if hi_at_max and all(f["step"] != hi_at_max for f in frames) and hi_at_max < len(hist):
        src = max([f for f in frames if f["step"] <= hi_at_max], key=lambda f: f["step"], default=None)
        hi_pk = snap.get("hist_i", -1)
        if hi_pk <= hi_at_max and (src is None or hi_pk >= src["step"]) and snap.get("disp"):
            mem = {m: (r, f) for m, (r, f) in member_state(model, snap["yield_ratio"]).items() if r >= 0.5}
            buck = [t for t, b in snap["braces"].items() if b["buckled"]]
        elif src is not None:
            mem, buck = src["mem"], src["buckled"]
        else:
            mem, buck = {}, []
        frames.append(dict(step=hi_at_max, lam=hist[hi_at_max][0], d=hist[hi_at_max][1], disp=disp_hist.get(hi_at_max, snap.get("disp") or {}),
                           mem=mem, buckled=buck))
        frames.sort(key=lambda f: f["step"])
    # post-peak ductility: lambda at 1.25 * d_at_max (if reached)
    lam_125 = None
    for lam, d in hist:
        if abs(d) >= 1.25 * abs(d_at_max):
            lam_125 = lam; break
    return dict(label=label, lambda_u=lam_max, step_at_max=step_at_max, d_at_max=d_at_max,
                first_yield=first_yield, lam_at_1p25d=lam_125, hist=hist, control=(cnode, cdof),
                lateral=(ldir, lsgn), gravity_kip=W, steps=len(hist), fails=fails, log=log, plateau=plateau,
                snapshot=snap, frames=frames, seconds=round(time.time() - t0, 1))


def classify(res, model):
    """Mechanism classification from the peak snapshot."""
    snap = res["snapshot"]
    yr = snap.get("yield_ratio", {})
    bt = model._bt
    hinges = {}         # mtag -> max ratio
    for e in model.elems:
        r = yr.get(e["tag"], 0.0)
        hinges[e["mtag"]] = max(hinges.get(e["mtag"], 0.0), r)
    hinge_members = [t for t, r in hinges.items() if r >= 3.0]
    yielded_members = [t for t, r in hinges.items() if r >= 1.0]
    by_kind = {}
    for t in hinge_members:
        k = bt[t].role
        by_kind[k] = by_kind.get(k, 0) + 1
    buckled = [t for t, s in snap.get("braces", {}).items() if s.get("buckled")]
    ductile_post = res.get("plateau", False) or (res["lam_at_1p25d"] is not None and res["lam_at_1p25d"] >= 0.9 * res["lambda_u"])
    nbeam_h = by_kind.get("floor", 0) + by_kind.get("roof", 0)
    if buckled and not nbeam_h:
        mech = "brace buckling (%d brace%s) governs the peak" % (len(buckled), "s" if len(buckled) > 1 else "")
        cls = "instability"
    elif buckled and nbeam_h:
        mech = "brace buckling (%d braces) with %d beam member%s at hinge level at the peak (proportional scaling also scales gravity)" % (
            len(buckled), nbeam_h, "s" if nbeam_h > 1 else "")
        cls = "instability"
    elif by_kind.get("lateral_col", 0) + by_kind.get("gravity_col", 0) > 0 and (by_kind.get("floor", 0) + by_kind.get("roof", 0)) == 0:
        mech = "column yielding / inelastic instability (%d column member%s at hinge level)" % (
            by_kind.get("lateral_col", 0) + by_kind.get("gravity_col", 0), "s" if by_kind.get("lateral_col", 0) + by_kind.get("gravity_col", 0) > 1 else "")
        cls = "instability"
    elif (by_kind.get("floor", 0) + by_kind.get("roof", 0)) >= 3 and ductile_post:
        mech = "beam plastic mechanism (%d beam members at hinge level)" % (by_kind.get("floor", 0) + by_kind.get("roof", 0))
        cls = "ductile"
    elif (by_kind.get("floor", 0) + by_kind.get("roof", 0)) >= 1:
        mech = "beam yielding (%d beam members at hinge level), limited post-peak ductility" % (by_kind.get("floor", 0) + by_kind.get("roof", 0))
        cls = "ductile" if ductile_post else "limited-ductility"
    else:
        yr_roles = {}
        for t in yielded_members:
            k = bt[t].role
            yr_roles[k] = yr_roles.get(k, 0) + 1
        ncol = yr_roles.get("lateral_col", 0) + yr_roles.get("gravity_col", 0)
        secs = sorted({bt[t].section for t in yielded_members if bt[t].kind == "col"})
        if ncol and ncol >= 0.6 * max(len(yielded_members), 1):
            mech = "inelastic column instability -- %d column%s yielded at the peak (%s), no beam hinge" % (
                ncol, "s" if ncol > 1 else "", ", ".join(secs[:4]))
        elif yr_roles.get("brace", 0):
            mech = "brace yielding at the peak (%d braces), no beam hinge" % yr_roles["brace"]
        elif yielded_members:
            mech = "partial yielding (%s) without a developed hinge at the peak" % ", ".join("%d %s" % (n, k) for k, n in yr_roles.items())
        else:
            mech = "peak reached while elastic (geometric instability)"
        cls = "instability"
    return dict(mechanism=mech, cls=cls, hinge_members=hinge_members, yielded_members=yielded_members,
                hinges_by_role=by_kind, buckled_braces=buckled, ductile_post_peak=ductile_post)
