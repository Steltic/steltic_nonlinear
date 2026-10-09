"""
solver.py -- load-factor sweep to collapse (proportional loading), peak detection, mechanism data.

sweep(model, combo, pres, ...) applies the factored combination at lambda = 1, probes the elastic
response, picks the control DOF (largest displacement -- roof drift for lateral cases, a beam
mid-span or column shortening for gravity cases; NL-R2-02: measured from the lambda = 0 equilibrium, and pushed in
the direction of the applied lateral pattern), then drives the structure with adaptive
DisplacementControl: Newton -> KrylovNewton fallback, step halving on failure (line search at the smallest step),
step growth on recovery. NL-R2-27: when the step is exhausted on the RISING branch, a load-controlled step decides
what happened -- equilibrium exists at a higher lambda (a bifurcation the control DOF does not see, e.g. a beam
twisting): the sweep continues in load control and returns to displacement control when that fails; no equilibrium:
the sweep ends at a reported limit (limit point or unstable bifurcation, with the tangent ratio). Every sweep returns
`termination` (limit / post-peak / plateau / disp-cap / budget / numerical); budget and numerical ends are lower bounds.
lambda_u is the peak load factor; the run continues into the
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
        if e.get("yield_fn"):                     # NL-02/03: BRB truss / EBF link shear spring (deformation / yield deformation)
            out[e["tag"]] = e["yield_fn"](); continue
        if not e.get("secTag"):
            continue
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
        if m.kind != "brace" or mtag in getattr(model, "nonbuckling", ()):     # BRBs do not buckle (NL-02)
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
        buck = N < 0 and off > L / 200.0
        if mtag in (getattr(model, "x_ties", None) or {}) and len(chain) >= 5:
            # NL-R2-28 (DDM): diagonal connected at the crossing -- each half buckles between its end and the crossing
            k = len(chain) // 2
            for lo, hi in ((0, k), (k, len(chain) - 1)):
                q = ops.nodeDisp(chain[(lo + hi) // 2]); u = ops.nodeDisp(chain[lo]); v = ops.nodeDisp(chain[hi])
                oh = math.sqrt(sum((q[i] - 0.5 * (u[i] + v[i])) ** 2 for i in range(3)))
                off = max(off, oh)
                buck = buck or (N < 0 and oh > (L / 2) / 200.0)
        out[mtag] = dict(N=N, offset=off, L=L, buckled=buck)
    return out


def member_state(model, ys):
    """Per member: (max yield ratio over its sub-elements, fraction along the member of the worst sub-element)."""
    out = {}
    for e in model.elems:
        if not e.get("secTag"):
            continue
        r = ys.get(e["tag"], 0.0)
        n = max(1, len(model.sub_nodes[e["mtag"]]) - 1)
        cur = out.get(e["mtag"])
        if cur is None or r > cur[0]:
            out[e["mtag"]] = (r, (e["s"] + 0.5) / n)
    return out



def _track_nodes(model):
    """Diaphragm masters, or portal eave/apex nodes when no diaphragms."""
    if model.masters:
        return list(model.masters)
    # portal: unique highest-z nodes per (rounded x,y) wall/roof line — keep eaves + apex
    by = {}
    for t, (x, y, z) in model.nm.nodes.items():
        if t in model.nm.fixes:
            continue
        key = (round(x, 0), round(y, 0))
        if key not in by or z > by[key][1]:
            by[key] = (t, z)
    nodes = [t for t, z in sorted(by.values(), key=lambda tz: tz[1])]
    return nodes[-8:] if len(nodes) > 8 else nodes  # cap for viewer

def _frame(model, eps_y, step, lam, d, ys=None, bs=None):
    """Viewer frame: masters, members at/over 0.5 eps_y with the worst sub-element location, buckled braces."""
    ys = ys if ys is not None else yield_state(model, eps_y)
    bs = bs if bs is not None else brace_state(model)
    return dict(step=step, lam=lam, d=d, disp={t: ops.nodeDisp(t) for t in _track_nodes(model)},
                mem={m: (r, f) for m, (r, f) in member_state(model, ys).items() if r >= 0.5},
                buckled=[t for t, b in bs.items() if b["buckled"]])


def storey_drifts(model, dirn):
    dof = 1 if dirn == "X" else 2
    ms = _track_nodes(model)
    disp = [ops.nodeDisp(t, dof) for t in ms]
    z = [model.nm.nodes[t][2] for t in ms]
    dr = []
    prev_d, prev_z = 0.0, 0.0
    for d, zz in zip(disp, z):
        dr.append((d - prev_d) / max(zz - prev_z, 1e-9)); prev_d, prev_z = d, zz
    return disp, dr


BRIDGE_FRACS = (0.02, 0.1, 0.5)                # NL-R2-27 (review D1): load-control bridge steps, SMALLEST first
BRIDGE_EQ_TOL = 1e-4                           # RelativeNormUnbalance tolerance of every load-controlled step
BRIDGE_JUMP = 3.0                              # max control-DOF jump of a load-controlled step, x the sweep dlambda x elastic slope


def _converge(first=True, line_search=True, equilibrium=False):
    """One analysis step with the fallback chain (NL-R2-27): Newton, then KrylovNewton, then Newton with a line search.
    first=False skips the plain Newton (already tried by the caller); line_search=False stops after Krylov (the cheap
    chain used while halving the displacement step, as before). Restores Newton / the base test. -> 0 if converged.
    equilibrium=True (review D1, every LOAD-controlled step): convergence is judged on the out-of-balance force
    (RelativeNormUnbalance), never on the displacement increment -- a Krylov / line-search iteration can stall with tiny
    displacement increments far from equilibrium (von Mises truss: accepted a state with a bar resultant of 1411 against
    an applied 3.8), which NormDispIncr takes as converged."""
    chain = ([("Newton", (), 1e-6, 25)] if first else []) + [("KrylovNewton", (), 1e-5, 30)] + (
        [("NewtonLineSearch", ("-type", "Bisection"), 1e-5, 40)] if line_search else [])
    ok = -1
    for alg, args, tol, it in chain:
        ops.algorithm(alg, *args)
        if equilibrium:
            ops.test("RelativeNormUnbalance", BRIDGE_EQ_TOL, it, 0)
        else:
            ops.test("NormDispIncr", tol, it, 0)
        ok = ops.analyze(1)
        if ok == 0:
            break
    ops.algorithm("Newton"); ops.test("NormDispIncr", 1e-6, 25, 0)
    return ok


def _jump_ok(d0, d1, dlam, slope, sgn):
    """Review D1: a load-controlled step (at most dlam / 2) is a continuation of the SAME equilibrium path only when the
    control DOF moved in the push direction and by no more than BRIDGE_JUMP x the sweep's dlam x the elastic slope, i.e.
    no more than three full displacement-control steps (a snap to a far branch moves it by orders of magnitude more, or
    backwards; von Mises truss: 17.6 against 0.12 allowed)."""
    dd = (d1 - d0) * sgn
    return dd > -1e-12 * max(1.0, abs(d0)) and abs(d1 - d0) <= BRIDGE_JUMP * dlam * abs(slope)


def _bridge(dlam, cnode=None, cdof=None, slope=None, sgn=1):
    """NL-R2-27: from the last converged state, try load-controlled steps of BRIDGE_FRACS * dlam (smallest first).
    A step is accepted only when it converged on the out-of-balance force (an equilibrium state) AND the control DOF moved
    forward by a plausible amount (_jump_ok) -- then equilibrium exists at a HIGHER load factor on the same path and the
    displacement-control failure was not a limit point. -> (dl, None) accepted; (None, None) no equilibrium found;
    (None, why) a converged step was REJECTED (snap to another branch) -- the domain then holds that rejected, committed
    state and the caller must stop analysing (the sweep ends at a limit at the last accepted lambda)."""
    for f in BRIDGE_FRACS:
        d0 = ops.nodeDisp(cnode, cdof) if cnode is not None else None
        ops.integrator("LoadControl", f * dlam)
        if _converge(equilibrium=True) != 0:
            continue
        if cnode is None or slope is None:
            return f * dlam, None
        d1 = ops.nodeDisp(cnode, cdof)
        if _jump_ok(d0, d1, dlam, slope, sgn):
            return f * dlam, None
        return None, ("load-controlled step +%.3g reached equilibrium only by a jump of the control DOF of %.3g "
                      "(allowed %.3g in the push direction) -- snap-through to another branch" % (
                          f * dlam, d1 - d0, BRIDGE_JUMP * dlam * abs(slope)))
    return None, None


def _tangent_ratio(hist, slope, n=3):
    """Secant stiffness of the last n converged steps relative to the elastic (probe) stiffness, or None."""
    if len(hist) < n + 1 or not slope:
        return None
    (l0, d0), (l1, d1) = hist[-n - 1], hist[-1]
    if abs(d1 - d0) < 1e-12:
        return None
    return ((l1 - l0) / abs(d1 - d0)) * abs(slope)


def sweep(model, combo, pres, dlam=0.02, max_steps=600, post_peak=0.85, disp_cap_factor=60.0,
          verbose=True, snapshot_every=3, time_limit=None, post_peak_steps=8, plateau_frac=0.02, frame_every=2,
          max_bridges=10):
    label, fD, fL, fLr, lat, col_only = combo
    t0 = time.time()
    def _setup():
        model.build().prepare()
        ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
        W = model.apply_gravity(fD, fL, fLr, pres)
        model.apply_lateral(lat)
        _solver_settings()
        ops.integrator("LoadControl", 0.0); ops.analysis("Static")
        return W
    W = _setup()
    ldir, lsgn = lateral_direction(lat)
    eps_y = model.Fy / model.builder.E

    # NL-R2-02: equilibrium at lambda = 0 first. The GMNIA model can carry a lambda-INDEPENDENT state (Lehigh residual
    # stresses with bows/out-of-plumb: Ex13 roof +0.011 in, brace -27 kip at zero load); the control DOF, its sign and
    # the step size must come from the load-proportional part d(lam) - d(0), never from the total displacement.
    def _zero_state():
        ops.integrator("LoadControl", 0.0)
        if ops.analyze(1) != 0:
            return None
        return {t: ops.nodeDisp(t) for t in ops.getNodeTags()}
    u_zero = _zero_state()
    if u_zero is None:                           # rewind; the probe then measures from the unloaded geometry
        W = _setup(); u_zero = {}

    # ---- elastic probe (adaptive: soft systems may fail at 0.05) ------------------------------
    lam_probe = None
    for trial in (0.05, 0.02, 0.01, 0.005, 0.002):
        ops.integrator("LoadControl", trial)
        if ops.analyze(1) == 0:
            lam_probe = trial
            break
        # rewind failed attempt before retrying a smaller step
        W = _setup()
        if u_zero and _zero_state() is None:
            W = _setup(); u_zero = {}
    if lam_probe is None:
        raise RuntimeError("elastic probe failed for %s" % label)
    if verbose and lam_probe < 0.05:
        print("   note %-40s elastic probe used lambda=%.3f (0.05 failed)" % (label[:40], lam_probe))
    def _inc(t, dof):                            # load-proportional displacement at the probe
        return ops.nodeDisp(t, dof) - (u_zero.get(t) or [0.0] * 6)[dof - 1]
    if ldir:
        if model.masters:
            top = model.masters[-1]
        else:
            # portal / no diaphragm: control the highest free node
            tops = sorted(((z, t) for t, (x, y, z) in model.nm.nodes.items()
                           if t not in model.nm.fixes), reverse=True)
            top = tops[0][1] if tops else max(model.nm.nodes)
        cdof = 1 if ldir == "X" else 2
        cnode = top
    else:
        # gravity case: control the largest VERTICAL beam deflection (never a brace bow or a sway DOF)
        best, cnode, cdof = 0.0, None, 3
        for e in model.elems:
            if e["kind"] != "beam" or e["s"] != e.get("nseg", model.nsub_beam) // 2:     # NL-R2-27: per-beam chain
                continue
            t = e["n1"]
            v = abs(_inc(t, 3))
            if v > best:
                best, cnode = v, t
    d_probe = ops.nodeDisp(cnode, cdof)
    d_zero = (u_zero.get(cnode) or [0.0] * 6)[cdof - 1]
    slope = (d_probe - d_zero) / lam_probe        # control displacement per unit lambda (load-proportional part)
    if abs(slope) * lam_probe < 1e-12:
        raise RuntimeError("zero probe displacement -- control DOF not excited")
    # lateral cases: push in the direction of the applied lateral pattern (lsgn), with the incremental stiffness
    sgn = lsgn if ldir else (1 if slope > 0 else -1)
    du0 = sgn * abs(slope) * dlam
    du = du0
    d_cap = abs(slope) * disp_cap_factor
    ops.integrator("DisplacementControl", cnode, cdof, du)

    hist = [(lam_probe, d_probe)]
    lam_max, step_at_max, d_at_max = lam_probe, 0, d_probe; hi_at_max = 0
    first_yield = None
    snap = dict(yield_ratio={}, braces={}, drifts=None, disp=None, step=0)
    frames = []                                   # viewer frames every `frame_every` steps (+ the peak)
    disp_hist = {}                                # hist index -> master displacements (cheap; lets the peak frame be exact)
    fails = 0; consecutive_fail = 0
    log = []; plateau = False
    # NL-R2-27: how the sweep ended. kind: "limit" (no equilibrium above lambda_u: limit point or unstable bifurcation),
    # "post-peak" / "plateau" / "disp-cap" (structural ends past or at the peak), "budget" (time / step budget used while
    # lambda was still rising: lambda_u is a lower bound), "numerical" (the solver gave up without evidence of a limit).
    termination = None
    mode, dl_lc, bridges = "dc", None, 0
    for step in range(1, max_steps + 1):
        if mode == "lc":
            # NL-R2-27: load-controlled continuation after the displacement control was exhausted at a bifurcation
            # (Ex13: inelastic lateral-torsional buckling of a beam -- the control DOF does not see the new mode and the
            # displacement-controlled Newton diverges, while load control converges and lambda keeps rising)
            # review D1: size the load step so that the EXPECTED control-DOF increment at the current secant stiffness stays
            # within half the jump limit -- a softening path is then followed, only a real snap trips _jump_ok
            kt_now = _tangent_ratio(hist, slope, n=1)
            if kt_now and kt_now > 0:
                dl_lc = min(dl_lc, max(0.5 * BRIDGE_JUMP * dlam * kt_now, BRIDGE_FRACS[0] * dlam))
            ops.integrator("LoadControl", dl_lc)
            d_before = ops.nodeDisp(cnode, cdof)
            ok = _converge(equilibrium=True)
            if ok == 0 and not _jump_ok(d_before, ops.nodeDisp(cnode, cdof), dlam, slope, sgn):
                # review D1: equilibrium reached only by snapping to another branch -- the last accepted state is the limit
                lam_now = hist[-1][0]
                kt = _tangent_ratio(hist, slope)
                termination = dict(kind="limit", lam=lam_now, tangent=kt,
                                   detail="load control above lambda %.3f snaps (control DOF jump %.3g) -- limit point / "
                                          "unstable bifurcation" % (lam_now, ops.nodeDisp(cnode, cdof) - d_before))
                log.append("step %d: load-controlled step snapped to another branch -- limit at lambda %.3f -- stopping" % (step, lam_now))
                break
            if ok != 0:
                fails += 1
                mode = "dc"; du = du0 / 4
                ops.integrator("DisplacementControl", cnode, cdof, du)
                log.append("step %d: load control failed at dlambda %.3g -- back to displacement control (%.3g)" % (step, dl_lc, du))
                continue
            dl_lc = min(dl_lc * 1.5, dlam / 2)
        else:
            ok = ops.analyze(1)
        if ok != 0:
            consecutive_fail += 1; fails += 1
            # cheap fallbacks first (failed steps are where the time goes): Krylov, then halve the step; the line search
            # is kept for the smallest step and the load-control bridge
            ok = _converge(first=False, line_search=abs(du) * 0.5 < abs(du0) / 64)
            if ok != 0:
                du *= 0.5
                if abs(du) < abs(du0) / 64:
                    lam_now = hist[-1][0]
                    if lam_now < lam_max - 1e-9:
                        log.append("step %d: step size exhausted on the post-peak branch -- stopping" % step)
                        termination = dict(kind="post-peak", lam=lam_now,
                                           detail="displacement control exhausted past the peak (lambda %.3f < lambda_u %.3f)" % (lam_now, lam_max))
                        break
                    dlb, snapped = (None, None) if bridges >= max_bridges else _bridge(dlam, cnode, cdof, slope, sgn)
                    if dlb is None:
                        kt = _tangent_ratio(hist, slope)
                        what = ("limit point (tangent %.0f%% of elastic)" % (100 * kt) if kt is not None and kt < 0.25 else
                                "unstable bifurcation / sudden loss of stiffness (tangent %s of elastic just before)" % (
                                    "%.0f%%" % (100 * kt) if kt is not None else "n/a"))
                        if bridges >= max_bridges:
                            termination = dict(kind="numerical", lam=lam_now, tangent=kt,
                                               detail="solver gave up after %d load-control bridges at lambda %.3f" % (bridges, lam_now))
                            log.append("step %d: step size exhausted after %d bridges -- stopping (numerical, lambda_u is a lower bound)" % (step, bridges))
                        elif snapped:
                            termination = dict(kind="limit", lam=lam_now, tangent=kt,
                                               detail="no equilibrium above lambda %.3f on the same path: %s -- %s" % (lam_now, snapped, what))
                            log.append("step %d: step size exhausted, load control snaps -- %s at lambda %.3f -- stopping" % (step, what, lam_now))
                        else:
                            termination = dict(kind="limit", lam=lam_now, tangent=kt,
                                               detail="no equilibrium above lambda %.3f: displacement control exhausted and load "
                                                      "control failed from dlambda %.3g to %.3g -- %s" % (lam_now, BRIDGE_FRACS[0] * dlam, BRIDGE_FRACS[-1] * dlam, what))
                            log.append("step %d: step size exhausted -- %s at lambda %.3f -- stopping" % (step, what, lam_now))
                        break
                    bridges += 1; mode, dl_lc = "lc", dlb; du = du0
                    ops.integrator("DisplacementControl", cnode, cdof, du)    # re-armed for the return from load control
                    log.append("step %d: displacement control exhausted at lambda %.3f -- load control converged at "
                               "+%.3g (not a limit point), continuing in load control" % (step, lam_now, dlb))
                    ok = 0
                else:
                    ops.integrator("DisplacementControl", cnode, cdof, du)
                    log.append("step %d: halved step to %.3g" % (step, du))
                    if consecutive_fail > 12:
                        log.append("too many failures")
                        termination = dict(kind="numerical", lam=hist[-1][0], detail="too many consecutive failures"); break
                    continue
        if step_at_max and step - step_at_max > post_peak_steps:
            log.append("post-peak budget (%d steps) used at step %d" % (post_peak_steps, step))
            termination = dict(kind="post-peak", lam=hist[-1][0], detail="post-peak step budget used"); break
        consecutive_fail = 0
        if mode == "dc" and abs(du) < abs(du0):
            du = min(abs(du) * 1.5, abs(du0)) * (1 if du0 > 0 else -1)
            ops.integrator("DisplacementControl", cnode, cdof, du)
        lam = ops.getLoadFactor(1); d = ops.nodeDisp(cnode, cdof)
        hist.append((lam, d))
        hi = len(hist) - 1                        # index of this converged step in hist (failed steps are not counted)
        disp_hist[hi] = {t: ops.nodeDisp(t) for t in _track_nodes(model)}
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
                            disp={t: ops.nodeDisp(t) for t in _track_nodes(model)})
        elif lam < post_peak * lam_max and step > step_at_max + 3:
            log.append("post-peak branch reached %.0f%% of lambda_u at step %d" % (100 * post_peak, step))
            termination = dict(kind="post-peak", lam=lam, detail="post-peak branch reached %.0f%% of lambda_u" % (100 * post_peak)); break
        if mode == "lc" and step_at_max and lam < lam_max - 1e-9:
            mode = "dc"                                     # never descend in load control (cannot happen; safety)
        if abs(d - d_zero) > d_cap:
            log.append("control displacement cap reached at step %d" % step)
            termination = dict(kind="disp-cap", lam=lam, detail="control displacement cap (%.0f x the elastic slope)" % disp_cap_factor); break
        # plateau rule: tangent stiffness over the last 10 steps below `plateau_frac` of the elastic
        # stiffness -> a plastic plateau (mechanism / squash with hardening); lambda_u is taken here
        if len(hist) > 12 and lam >= lam_max - 1e-9:
            l0, d0 = hist[-11]
            k_el = 1.0 / abs(slope)                # NL-R2-02: incremental elastic stiffness (lambda per in)
            k_t = (lam - l0) / max(abs(d - d0), 1e-12)
            if k_t < plateau_frac * k_el:
                plateau = True
                if snap["step"] < step - 1:
                    snap = dict(yield_ratio=yield_state(model, eps_y), braces=brace_state(model),
                                drifts=(storey_drifts(model, ldir) if ldir else None), step=step, hist_i=len(hist) - 1,
                                disp={t: ops.nodeDisp(t) for t in _track_nodes(model)})
                log.append("plateau: tangent stiffness %.1f%% of elastic at step %d -- lambda_u taken at the plateau" % (100 * k_t / k_el, step))
                termination = dict(kind="plateau", lam=lam, tangent=k_t / k_el, detail="plastic plateau"); break
        if time_limit and time.time() - t0 > time_limit:
            log.append("time limit reached at step %d" % step)
            termination = dict(kind=("budget" if lam >= lam_max - 1e-9 else "post-peak"), lam=lam, detail="time limit"); break
        if verbose and step % 10 == 0:
            print("   %-38s step %4d  lambda %.3f  d %.3f  (%.0f s)" % (label[:38], step, lam, d, time.time() - t0), flush=True)
    if termination is None:
        lam_end = hist[-1][0]
        termination = dict(kind=("budget" if lam_end >= lam_max - 1e-9 else "post-peak"), lam=lam_end,
                           detail="max steps (%d) used" % max_steps)
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
        if abs(d - d_zero) >= 1.25 * abs(d_at_max - d_zero):
            lam_125 = lam; break
    return dict(label=label, lambda_u=lam_max, step_at_max=step_at_max, d_at_max=d_at_max,
                first_yield=first_yield, lam_at_1p25d=lam_125, hist=hist, control=(cnode, cdof),
                lateral=(ldir, lsgn), gravity_kip=W, lam_probe=lam_probe, d_zero=d_zero, probe_slope=slope, steps=len(hist), fails=fails, log=log, plateau=plateau,
                snapshot=snap, frames=frames, seconds=round(time.time() - t0, 1), termination=termination, bridges=bridges)


def classify(res, model):
    """Mechanism classification from the peak snapshot."""
    snap = res["snapshot"]
    yr = snap.get("yield_ratio", {})
    bt = model._bt
    hinges = {}         # mtag -> max ratio
    for e in model.elems:
        if not e.get("secTag"):
            continue
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
    if not res.get("step_at_max"):
        # NL-R2-02: lambda never rose above the elastic probe -> a solver/control stop, not a structural limit point
        mech = ("NUMERICAL: the load factor never rose above the elastic probe (lambda = %.3f) -- solver/control stop, "
                "not a structural peak; the combination is NOT EVALUATED" % res["lambda_u"])
        return dict(mechanism=mech, cls="numerical", hinge_members=hinge_members, yielded_members=yielded_members,
                    hinges_by_role=by_kind, buckled_braces=buckled, ductile_post_peak=False)
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
