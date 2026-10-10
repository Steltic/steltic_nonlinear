"""run.py -- one ASCE 7-22 Chapter 16 response history: gravity (16.3.2) -> damping (16.3.5) -> bidirectional
uniform excitation (16.2.4) -> HHT-alpha (default) or Newmark integration with adaptive time step -> peak/mean bookkeeping for 16.4.

Record status (result["status"]):
  completed       -- the whole analysed window (record start, or the trimmed start, to the checked end + free vibration;
                     NL-R2-18 record_window) was integrated; every step converged.
  nonconvergence  -- the analytical solution failed to converge (16.4.1.1 item 1): the time step was halved down to
                     dt/32 or 12 consecutive attempts failed, or the gravity stage failed. Unacceptable response.
  incomplete      -- every committed step converged but the work budget ran out (step count or wall time). This is NOT
                     16.4.1.1(1): the solution did not fail to converge, the run was cut short. Peaks are lower bounds.
                     Earlier builds aborted after 41 converged fallback micro-steps ("crawl abort") and counted that as
                     non-convergence; now repeated fallback steps shrink the time step instead, and only the explicit
                     budget can cut a record short -- reported as such, never as non-convergence.
result["converged"] stays True only for `completed` (callers that only know converged/not keep working).
"""
from __future__ import annotations
import math, time
import numpy as np
import openseespy.opensees as ops
from pushover import nonlinear_model as NM
from pushover import hinge_models as HM
from . import model as MD
from . import drift as DR
from . import ground_motions as GM

G_IN = 386.4


def _apply_gravity(loads):
    ops.wipeAnalysis()
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for n, pz in loads.items():
        ops.load(n, 0.0, 0.0, pz, 0.0, 0.0, 0.0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.1); ops.analysis("Static")
    ok = ops.analyze(10)
    ops.loadConst("-time", 0.0)
    return ok


def _edge_nodes(pkg):
    """DEPRECATED (NL-12): two (x, y)-sorted slave nodes per level. Not vertically aligned on set-backs and blind to
    wings; the drift is now computed at vertically aligned points (nlrha/drift.py). Kept for old scripts only."""
    out = []
    for k, z, master, slaves in NM.levels(pkg):
        pts = sorted(slaves, key=lambda n: (pkg.model.nodes[n][0], pkg.model.nodes[n][1]))
        out.append((k, z, master, pts[0], pts[-1]))
    return out


def hht_algorithmic_damping(alpha, dt, T):
    """Equivalent viscous damping ratio that the HHT-alpha integrator (OpenSees convention: alpha in [2/3, 1],
    gamma = 1.5 - alpha, beta = (2 - alpha)^2 / 4; alpha = 1 is Newmark average acceleration, no numerical damping)
    adds to an undamped SDOF of period T at step dt. From the eigenvalues of the one-step amplification matrix:
    lambda = exp(-xi w dt +- i w_d dt) -> xi = -Re(ln lambda) / |ln lambda|. Disclosed next to the 16.3.5 viscous damping."""
    if T <= 0 or dt <= 0:
        return 0.0
    g = 1.5 - alpha; b = (2.0 - alpha) ** 2 / 4.0
    w = 2 * math.pi / T; k = w * w; m = 1.0
    # unknowns x1 = (u1, v1, a1); equations: m a1 + k (alpha u1 + (1 - alpha) u0) = 0 ; Newmark u/v updates
    Amat = np.array([[alpha * k, 0.0, m], [1.0, 0.0, -b * dt * dt], [0.0, 1.0, -g * dt]])
    cols = []
    for x0 in np.eye(3):
        u0, v0, a0 = x0
        rhs = np.array([-(1 - alpha) * k * u0, u0 + dt * v0 + dt * dt * (0.5 - b) * a0, v0 + dt * (1 - g) * a0])
        cols.append(np.linalg.solve(Amat, rhs))
    lam = np.linalg.eigvals(np.array(cols).T)
    osc = [l for l in lam if abs(l.imag) > 1e-12]
    if not osc:
        return float("nan")
    l = max(osc, key=lambda z: abs(z)); ln = np.log(l)
    return float(-ln.real / abs(ln))


def transient_step(h):
    """One transient step of size h. NL-R2-20 (residual): after a FAILED step, force a domainChanged cycle
    (ops.domainChange) so the retry starts from the committed state.

    A failed ops.analyze reverts the nodes and elements (Domain::revertToLastCommit) and the integrator vectors, but in
    a transient analysis with the Transformation constraint handler the DOF groups of MP-constrained nodes (rigid-
    diaphragm / equalDOF slaves) keep state from the failed iterations: the retry then starts from a corrupted state of
    every diaphragm slave's own DOFs (UZ, RX, RY). After a diverged step this is garbage (Ex20 Gilroy #3: one Newton
    iteration of the retry gave |du| ~1e5..1e26 rad at the work-point / joint rotations, every halving failed again ->
    non-convergence); after a merely non-converged step the retry converges to a WRONG state (tests/
    test_nlrha_retry_state.py: a 4-node frame with one equalDOF slave). domainChange rebuilds the DOF groups and
    re-reads the integrator's response vectors from the committed nodal state (OpenSees 3.7/3.8; HHT and Newmark).
    Steps that converge are untouched, so records without a failed step are bit-identical."""
    ok = ops.analyze(1, h)
    if ok != 0:
        ops.domainChange()
    return ok


def _column_info(pkg, hinges, stats, PG=None, prm=None):
    """Per column element: the element tags at its i and j ends (sub-divided members: first and last segment), the
    local force indices of the major / minor moments, how each flexural axis is MODELLED (hinge / elastic / fibre) and
    how it is CLASSIFIED (AISC 342-22 C3.4 / ASCE 41-23 7.5): flexure is deformation-controlled for
    P_G/P_ye <= 0.6 and force-controlled above (such columns get no hinge, pushover.hinge_models.column_hinge)."""
    from pushover import hinge_models as HM
    try:
        tags = set(ops.getEleTags())
    except Exception:                                               # noqa: BLE001
        tags = set()
    nseg = max(1, int(stats.get("member_nseg") or 1))
    fibre = stats.get("plasticity") == "fibre"
    out = {}
    for e in pkg.model.elements:
        if "etype" in e or NM.member_kind(pkg, e) != "col":
            continue
        c = e["tag"]
        tj = NM.SEG_ELE_BASE + c * 100 + (nseg - 1) if nseg > 1 else c
        if tags and tj not in tags:
            tj = c
        slot = NM.strong_I_slot(pkg, e, "col")
        maj = (5, 11) if slot == "Iz" else (4, 10); mnr = (4, 10) if slot == "Iz" else (5, 11)
        ends = {h["end"] for h in hinges.values() if h.get("ele") == c and h.get("kind") == "col"}
        fc_col = False
        sec = pkg.schedule.get(c, {}).get("section")
        if sec and prm is not None:
            try:
                L = math.dist(pkg.model.nodes[e["n1"]], pkg.model.nodes[e["n2"]])
                fc_col = bool(HM.column_hinge(sec, L, (PG or {}).get(c, 0.0), prm).force_controlled)
            except Exception:                                       # noqa: BLE001
                fc_col = False
        cls = "force" if fc_col else "deformation"
        modelled = dict(major=("fibre" if fibre else ("hinge" if ends else "elastic")), minor=("fibre" if fibre else "elastic"))
        try:                                                        # proxy strengths, only to pick the concurrent instants
            from pushover import sections_db as SDB
            pp = SDB.props(sec); Fye = float(((prm or {}).get("material") or {}).get("Fy_ksi", 50.0)) * float(((prm or {}).get("material") or {}).get("Ry_expected", 1.1))
            proxy = (pp["A"] * Fye, pp["Zx"] * Fye, pp["Zy"] * Fye)
        except Exception:                                           # noqa: BLE001
            proxy = None
        out[c] = dict(ti=c, tj=tj, maj=maj, mnr=mnr, hinged_ends=sorted(ends), flexure=dict(major=cls, minor=cls, modelled=modelled), proxy=proxy)
    return out


def _column_forces(ci):
    """(P compression +, |M major|, |M minor|) envelope over both ends of one column, plus the two end triplets
    [(P, |Mmaj|, |Mmin|) at end i, at end j] (concurrent values), from localForce."""
    fi = ops.eleResponse(ci["ti"], "localForce") or []
    fj = fi if ci["tj"] == ci["ti"] else (ops.eleResponse(ci["tj"], "localForce") or [])
    if len(fi) < 12 or len(fj) < 12:
        return None
    P = fi[0]                                                       # end-i local N: +ve = compression
    ti = (fi[0], abs(fi[ci["maj"][0]]), abs(fi[ci["mnr"][0]])); tj = (-fj[6], abs(fj[ci["maj"][1]]), abs(fj[ci["mnr"][1]]))
    return P, max(ti[1], tj[1]), max(ti[2], tj[2]), (ti, tj)


def link_state_note(hinges):
    """NL-R2-22: '' or '; EBF links at the last converged state: ...' -- how many link shear springs (AISC 342-22 Table
    C2.4 backbone) are at or past the capping deformation a (strength loss begins) or past b (strength lost), with the
    largest plastic shear rotation gamma_p = (|d| - Delta_y) / e. Read at the last committed state."""
    rows = []
    for t, h in hinges.items():
        if h.get("kind") != "link":
            continue
        d, V = NM.zero_length_spring(t, 3)
        s = h["spec"]
        g, a, b = (abs(d) - s.theta_y) / s.e_in, s.a_pl / s.e_in, s.b_pl / s.e_in
        # past b: once the MinMax wrapper (make_link_shear_material) has failed the link carries zero shear for good, but
        # its deformation can come back below Delta_y + b e (Ex8 Gilroy: 5 links with V = 0 at d = 5.7-6.1 in < 6.21 in
        # while the storey drift ran to 5.7 %), so a failed link is recognised by V = 0 beyond a (on the Table C2.4
        # envelope the force beyond a is >= c Vp > 0)
        lost = g >= b or (g >= 0.98 * a and abs(V) <= 1e-6 * max(abs(s.Vp_kip), 1.0))
        rows.append((g, a, b, h["ele"], lost))
    if not rows:
        return ""
    n_a = sum(1 for g, a, b, _, lost in rows if g >= 0.98 * a or lost)
    n_b = sum(1 for g, a, b, _, lost in rows if lost)
    g, a, b, ele, _ = max(rows)
    if not n_a:
        return ""
    return ("; EBF links at the last converged state: %d of %d at or past the capping rotation a (strength loss, AISC 342-22 Table C2.4), "
            "%d past b (shear strength lost); max gamma_p %.3f rad (link %s; a %.3f, b %.3f rad)" % (n_a, len(rows), n_b, g, ele, a, b))


def _truss_axial(tag):
    """NL-R2-22: axial force (kip, + tension) of a truss element."""
    f = ops.eleResponse(tag, "axialForce")
    return float(f[0]) if f else 0.0


def arias_window(a1, a2, dt, lo=0.001, hi=0.995):
    """Times at which the Arias intensity of the two components together reaches the fractions lo and hi. NL-R2-18:
    without trimming the run integrates from t = 0 and uses only the end (checked, see record_window); the head is
    skipped only when record trimming is switched on."""
    ia = np.cumsum(np.asarray(a1) ** 2 + np.asarray(a2) ** 2); ia /= ia[-1]
    i0 = int(np.searchsorted(ia, lo)); i1 = int(np.searchsorted(ia, hi))
    return i0 * dt, i1 * dt


# NL-R2-18: the record window. Default ("off"): integrate from t = 0 to the 99.5 % Arias point + free vibration, as
# before, but the end cut is now CHECKED: the 5 %-damped spectra of the motion the analysis actually sees (both
# components and the maximum direction, observed only up to the end of the analysis) must stay within SPECTRAL_TOL of
# the spectra of the full record followed by FV_REF_S of free vibration (the finding's measure: the cut changed
# Sa(4 s) of Superstition Hills El Centro by 11.9 %), over the record's scaling period range (16.2.3.2); otherwise the
# window is widened step by step back to the full record and then the free vibration lengthened up to FV_REF_S
# (long-period oscillators can peak after the record ends). Optional trimming (off by default; schedule doc 8): "standard" 0.1-99.5 % / "aggressive" 0.5-99 % Arias of
# both components together, on the head, the tail or both; trimmed ends start / stop at a zero crossing of the stronger
# component with a 0.5 s cosine taper, the motion is zero after the window (true free vibration), the same spectral
# check and widening apply, the scale factors stay those of the full record, and every window is disclosed (16.1.4).
TRIM_MODES = {"off": (0.0, 0.995), "standard": (0.001, 0.995), "aggressive": (0.005, 0.99)}
SPECTRAL_TOL = 0.02
TAPER_S = 0.5
WIDEN_STEPS = 6                                  # excluded Arias fractions halved up to 6 times, then the full record
FV_REF_S = 30.0                                  # free vibration after the full record in the reference spectra


def _cos_taper(n):
    return 0.5 * (1.0 - np.cos(np.pi * np.arange(n) / max(n, 1)))


def analysed_motion(a1, a2, dt, win):
    """The two components as the analysis applies them (record orientation): the record itself without trimming;
    with trimming the window slice, cosine-tapered at each trimmed end (zero beyond its end)."""
    a1 = np.asarray(a1, float); a2 = np.asarray(a2, float)
    if not win or not win.get("trimmed"):
        return a1, a2
    i_s, i_e = int(win["i_start"]), int(win["i_end"])
    b1 = a1[i_s:i_e].copy(); b2 = a2[i_s:i_e].copy()
    nt = min(int(round(TAPER_S / dt)), max(1, (i_e - i_s) // 4))      # (a window shorter than 4 tapers keeps its middle)
    if win.get("taper_head") and nt > 1:
        r = _cos_taper(nt); b1[:nt] *= r; b2[:nt] *= r
    if win.get("taper_tail") and nt > 1:
        r = _cos_taper(nt)[::-1]; b1[-nt:] *= r; b2[-nt:] *= r
    return b1, b2


def _observed(b, n_obs):
    """The motion seen by an analysis that stops after n_obs samples (zero after the series ends)."""
    return b[:n_obs] if len(b) >= n_obs else np.concatenate([b, np.zeros(n_obs - len(b))])


def _window_at(a1, a2, dt, ia, lo, hi, trim_head, trim_tail, free_vib_s):
    n = len(a1)
    strong = a1 if float(np.sum(a1 ** 2)) >= float(np.sum(a2 ** 2)) else a2
    zc = np.where(strong[:-1] * strong[1:] <= 0)[0]               # a crossing between k and k + 1
    nt = int(round(TAPER_S / dt))                                 # the taper lies OUTSIDE the Arias window (quiet motion only)
    i_s = 0
    if trim_head and lo > 0:
        i0 = int(np.searchsorted(ia, lo)) - nt; k = zc[zc <= i0]
        i_s = int(k[-1]) if (len(k) and i0 > 0) else 0
    i1 = n if hi >= 1.0 else min(int(np.searchsorted(ia, hi)), n)
    if trim_tail:
        k = zc[zc >= i1 + nt]
        i_e = n if hi >= 1.0 else (int(k[0]) + 1 if len(k) else n)
        i_cut = i_e
    else:
        i_e = n; i_cut = i1
    i_cut = max(i_cut, i_s + 1)
    t_end_rel = (i_cut - i_s) * dt + free_vib_s
    return dict(i_start=i_s, i_end=i_e, i_cut=i_cut, arias=(lo if trim_head else 0.0, hi), t_offset=i_s * dt, t_start=i_s * dt, t_sig=i_cut * dt,
                t_end=i_s * dt + t_end_rel, t_end_rel=t_end_rel, trimmed=bool(i_s > 0 or i_e < n), taper_head=bool(i_s > 0), taper_tail=bool(trim_tail and i_e < n))


def record_window(a1, a2, dt, free_vib_s=5.0, periods=None, ref=None, mode="off", ends="both", tol=SPECTRAL_TOL):
    """NL-R2-18: the analysed window of one record pair (record orientation) and its spectral check.
    periods: the record's scaling period range (16.2.3.2), the check is skipped (and says so) without it;
    ref: (Sa1, Sa2, Sa_maxdir) at `periods` of the full record + FV_REF_S free vibration (default: computed). Times are
    on the record's own axis; t_end_rel = what the analysis integrates (window + free vibration, which the widening
    may lengthen). Returns a json-safe dict (window, check, widening steps)."""
    mode = (mode or "off").lower()
    if mode not in TRIM_MODES:
        raise ValueError("record trimming mode %r: use off, standard or aggressive" % mode)
    a1 = np.asarray(a1, float); a2 = np.asarray(a2, float); n = len(a1)
    ia = np.cumsum(a1 ** 2 + a2 ** 2); ia = ia / ia[-1]
    lo0, hi0 = TRIM_MODES[mode]
    trim_head = mode != "off" and ends in ("both", "head")
    trim_tail = mode != "off" and ends in ("both", "tail")
    if not trim_tail:
        hi0 = TRIM_MODES["off"][1]                               # an untrimmed tail keeps the (checked) 99.5 % end
    P = None if periods is None else np.asarray(periods, float)
    if P is not None and len(P):
        if ref is None:
            nz = int(round(FV_REF_S / dt))
            ref = GM.pair_spectra(np.concatenate([a1, np.zeros(nz)]), np.concatenate([a2, np.zeros(nz)]), dt, P)
        ref = [np.asarray(x, float) for x in ref]
    base = None
    if mode != "off":                                            # the checked untrimmed window: trimming never runs longer
        base = record_window(a1, a2, dt, free_vib_s, periods=P, ref=ref, mode="off", tol=tol)
    ladder = [(lo0 / 2 ** k, 1.0 - (1.0 - hi0) / 2 ** k, free_vib_s) for k in range(WIDEN_STEPS + 1)] + [(0.0, 1.0, free_vib_s)]
    fv = free_vib_s
    while fv < FV_REF_S - 1e-9:
        fv = min(FV_REF_S, max(2 * fv, 1.0)); ladder.append((0.0, 1.0, fv))
    steps = []
    win = None
    for k, (lo, hi, fv) in enumerate(ladder):
        last = k == len(ladder) - 1
        win = _window_at(a1, a2, dt, ia, lo, hi, trim_head, trim_tail, fv)
        win["free_vib_s"] = fv
        if base is not None and win["t_end_rel"] >= base["t_end_rel"] - 1e-9:
            # widened back to the length of the untrimmed window: analyse that one (already checked)
            out = dict(base, mode=mode, ends=ends, untrimmed_s=base["t_end_rel"], saved_s=0.0,
                       note="not trimmed: the %.0f %% spectral check needs a window as long as the untrimmed one (%d trimmed window(s) tried)" % (100 * tol, len(steps)))
            out["check"] = dict(base["check"], trim_steps=steps)
            return out
        if P is None or not len(P):
            break
        b1, b2 = analysed_motion(a1, a2, dt, win)
        n_obs = int(round(win["t_end_rel"] / dt)) + 1
        got = GM.pair_spectra(_observed(b1, n_obs), _observed(b2, n_obs), dt, P)
        worst = (-1.0, None, None)
        for lab, g_, r_ in zip(("comp 1", "comp 2", "max-direction"), got, ref):
            ch = np.abs(g_ / np.maximum(r_, 1e-12) - 1.0); j = int(np.argmax(ch))
            if ch[j] > worst[0]:
                worst = (float(ch[j]), lab, float(P[j]))
        steps.append(dict(arias=[win["arias"][0], win["arias"][1]], t_start=round(win["t_start"], 3), t_sig=round(win["t_sig"], 3), free_vib_s=fv,
                          max_change=worst[0], at=worst[1], T=worst[2]))
        if worst[0] <= tol or last:
            break
    win["mode"] = mode; win["ends"] = ends if mode != "off" else "none"; win["free_vib_requested_s"] = free_vib_s
    win["record_s"] = n * dt; win["taper_s"] = TAPER_S if (win["taper_head"] or win["taper_tail"]) else 0.0
    if steps:
        fin = steps[-1]
        win["check"] = dict(tol=tol, ok=bool(fin["max_change"] <= tol), max_change=fin["max_change"], at=fin["at"], T=fin["T"],
                            periods=[float(P[0]), float(P[-1])], n_widened=len(steps) - 1, steps=steps,
                            full_record=bool(win["i_start"] == 0 and win["i_cut"] >= n))
    else:
        win["check"] = dict(tol=tol, ok=None, note="not checked: no scaling period range given")
    win["arias"] = list(win["arias"])
    win["untrimmed_s"] = base["t_end_rel"] if base is not None else win["t_end_rel"]
    win["saved_s"] = win["untrimmed_s"] - win["t_end_rel"]
    return win


# NL-R2-15: the per-record wall-time budget scales with the record and the model instead of a fixed 1800 s (which cut
# the 91.5-s Imperial Valley Delta record at t = 25-28 s on the braced Ex18 / Ex30 while every step was converging).
# auto = max(4 h, BUDGET_S_PER_STEP_NODE x nominal steps (t_end / dt) x model nodes). Calibration (round 2 suites, 1 core,
# dt 0.01, IMK models): completed records ran at 1.1e-4 (SMF Ex22, 887 nodes) to 1.9e-4 s per step per node (braced
# Ex18, 1,846 nodes; Ex30, 1,374 nodes); 6e-4 is ~3x the slowest. Ex18 Delta (9,650 steps x 1,846 nodes = 3.0 h) -> the
# 4 h floor; its dt/2 retry -> 5.9 h; a 5,000-node model on the same record -> 8 h.
BUDGET_FLOOR_S = 4 * 3600.0
BUDGET_S_PER_STEP_NODE = 6e-4


def record_wall_budget(setting, t_end, dt_max, n_nodes):
    """(seconds, basis) of the wall-time budget of one record (NL-R2-15). `setting`: None -> env
    SNL_NLRHA_RECORD_BUDGET_S, else "auto"; "auto" -> scaled to the record window and the model size (above);
    a number -> that many seconds; 0 -> unlimited (seconds 0)."""
    import os
    if setting is None:
        setting = os.environ.get("SNL_NLRHA_RECORD_BUDGET_S") or "auto"
    if str(setting).strip().lower() != "auto":
        try:
            s = float(setting)
            return (s, "fixed %.0f s" % s) if s > 0 else (0.0, "unlimited")
        except ValueError:
            pass
    n_nom = max(1, int(math.ceil(t_end / dt_max)))
    scaled = BUDGET_S_PER_STEP_NODE * n_nom * max(1, int(n_nodes or 0))
    s = max(BUDGET_FLOOR_S, scaled)
    return s, ("auto: max(%.0f s, %.0e s x %d nominal steps x %d nodes = %.0f s)" % (BUDGET_FLOOR_S, BUDGET_S_PER_STEP_NODE, n_nom, int(n_nodes or 0), scaled))


def run_record(pkg, prm, ch16, PG, loads, rec, xi, elastic_eles_cb, dt_max=0.02, free_vib_s=5.0, rec_every=5, verbose=True,
               sample_brace=None, integrator="hht", step_budget_factor=40.0, wall_budget_s=None):
    """Build a fresh model and run one scaled pair. Returns peaks/histories for the acceptance module.

    Work budget (NL-17): at most `step_budget_factor` x the nominal number of steps of ops.analyze calls, and at most
    `wall_budget_s` seconds (NL-R2-15: None -> env SNL_NLRHA_RECORD_BUDGET_S, else "auto" = scaled to the record window
    and the model size, see record_wall_budget; a number = fixed; 0 = unlimited). Running out of budget ends the record
    as status "incomplete" (not non-convergence)."""
    t0 = time.time()
    hinges, stats, elastic = MD.build(pkg, prm, ch16, PG)
    try:
        n_nodes = len(ops.getNodeTags())
    except Exception:                                               # noqa: BLE001
        n_nodes = len(getattr(pkg.model, "nodes", {}) or {})
    ok = _apply_gravity(loads)
    if ok != 0:
        return dict(record=rec["id"], label=rec.get("earthquake") or rec["id"], sf=rec.get("sf"), x_comp=rec.get("x_comp"), converged=False,
                    status="nonconvergence", reason="gravity stage failed", steps=0, fails=0, seconds=time.time() - t0)
    colinfo = _column_info(pkg, hinges, stats, PG, prm)
    col_grav = {}
    for c, ci in colinfo.items():                                   # Qns of 16.4.2.1: the gravity state of THIS model
        f = _column_forces(ci)
        if f is not None:
            col_grav[c] = dict(P=f[0], Mmaj=f[1], Mmin=f[2])
    # NL-R2-22: EBF braces are elastic force-controlled members (AISC 341-22 Table A-1.7.3): gravity axial force (Qns) and
    # the record envelope of the axial force at every committed step, + tension (eleResponse axialForce of the corotTruss)
    ebf_br = sorted((stats.get("ebf_braces") or {}).keys())
    ebf_env = {b: dict(Pg=_truss_axial(b), Pt=-1e30, Pc=-1e30) for b in ebf_br}
    modal = MD.modal(pkg, 6)
    T1 = max(modal["T1x"], modal["T1y"])
    damp = MD.set_damping(xi, T1, elastic)
    # bidirectional excitation, identical factor on both components (16.2.3.2), components per 16.2.4 orientation
    dt_rec = rec["dt"]; sf = rec["sf"]
    # NL-R2-18: the analysed window (checked end cut; optional trimming) -- chosen before the run by nlrha.cli
    win = rec.get("window") or record_window(rec["a1"], rec["a2"], dt_rec, free_vib_s)
    b1, b2 = analysed_motion(rec["a1"], rec["a2"], dt_rec, win)
    ax = b1 if rec["x_comp"] == 1 else b2; ay = b2 if rec["x_comp"] == 1 else b1
    toff = float(win.get("t_offset") or 0.0)                     # analysis t = 0 is record time toff (trimmed head)
    ops.timeSeries("Path", 11, "-dt", dt_rec, "-values", *(ax * G_IN * sf).tolist())
    ops.timeSeries("Path", 12, "-dt", dt_rec, "-values", *(ay * G_IN * sf).tolist())
    ops.pattern("UniformExcitation", 11, 1, "-accel", 11)
    ops.pattern("UniformExcitation", 12, 2, "-accel", 12)
    ops.wipeAnalysis()
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    # NL-R2-21 (R5): displacement-increment tolerance 1e-5 in (on 10k+ DOF braced models Newton stalls at a round-off floor of
    # 1e-6..1e-5 in) and NO fallback algorithms by default: ModifiedNewton -initial never converged, and KrylovNewton /
    # NewtonLineSearch "converged" on the displacement test while out of equilibrium, committing states that blew up a few
    # steps later and biased completed records low (Ex22: peaks up to 30 % higher without them). A failed Newton step is
    # retried with Newton at a halved step. numerics.nlrha_fallback = "legacy" restores the old ladder,
    # numerics.nlrha_disp_tol the old tolerance.
    num = (prm.get("numerics") or {}) if isinstance(prm, dict) else {}
    tol = float(num.get("nlrha_disp_tol", 1e-5))
    ladder = (("ModifiedNewton", "-initial"), ("KrylovNewton",), ("NewtonLineSearch",)) if num.get("nlrha_fallback") == "legacy" else ()
    ops.test("NormDispIncr", tol, 30, 0); ops.algorithm("Newton")
    if integrator == "newmark":
        ops.integrator("Newmark", 0.5, 0.25)                        # average acceleration, no numerical damping
        alg_damp = dict(integrator="Newmark average acceleration", alpha=None, xi_T1=0.0, xi_02T1=0.0)
    else:
        ops.integrator("HHT", 0.9)                                  # alpha = 0.9: second-order accurate, damps the spurious high modes of stiff hinge springs
        alg_damp = dict(integrator="HHT", alpha=0.9, xi_T1=hht_algorithmic_damping(0.9, dt_max, T1),
                        xi_02T1=hht_algorithmic_damping(0.9, dt_max, 0.2 * T1))
    ops.analysis("Transient")
    dt = dt_max                                                   # Path series interpolates the record; dt_max ~ T_lower/15
    dt_cur = dt_max; n_ok_since_cut = 0; consec_fail = 0; crawl = 0; n_fallback = 0; dt_floor = dt_max / 32.0; dt_min_used = dt_max
    next_rec = rec_every * dt_max; next_hist = 5 * dt_max                 # time-based recording (the step is adaptive)
    t_start, t_sig = win["t_start"], win["t_sig"]                # record time axis
    t_end = win["t_end_rel"]                                      # analysis time axis (window + free vibration)
    wall_budget_s, budget_basis = record_wall_budget(wall_budget_s, t_end, dt_max, n_nodes)
    t = 0.0
    lv = NM.levels(pkg)
    H = [lv[0][1]] + [lv[i][1] - lv[i - 1][1] for i in range(1, len(lv))]
    pts = DR.drift_points(pkg)                                    # 16.4.1.2: vertically aligned points (NL-12)
    pnodes = DR.node_set(pts)
    hz = sorted(hinges); K0 = {t: hinges[t]["K0"] for t in hz}
    cols = list(colinfo)
    peak_drift = np.zeros((len(lv), 2)); peak_at = [[None, None] for _ in lv]; peak_roof = np.zeros(2)
    peak_def = {t: 0.0 for t in hz}; signed_def = {t: (0.0, 0.0) for t in hz}       # (max positive, max negative)
    peak_colN = {c: 0.0 for c in cols}
    col_env = {c: dict(Pc=-1e30, Pt=1e30, Mmaj=0.0, Mmin=0.0, conc_c=None, conc_t=None, _uc=-1.0, _ut=-1.0) for c in cols}
    hist_t, hist_roof = [], []
    brace_hist = []
    frames_t, frames_story, frames_brace, frames_ag = [], [], [], []       # viewer frames (every rec_every steps)
    # ...and the plastic deformation of EVERY hinge at each of those frames, in `hz` order. The loop
    # below already computes `v` per hinge to build peak_def, so keeping it costs no extra
    # eleResponse call. Without it the viewer had nothing per-step for beam and column hinges and
    # fell back to the record peak: every frame -- t = 0 included -- was painted with the worst the
    # record ever reached, so "play" opened on a structure already fully hinged.
    frames_hinge = []
    masters = [m for k, z, m, s in lv]
    braces = [t for t in hz if hinges[t]["kind"] == "brace"]
    n_rec = len(ax)
    n_nominal = max(1, int(math.ceil(t_end / dt_max)))
    step_budget = int(step_budget_factor * n_nominal) if step_budget_factor else 0
    calls = 0
    step = 0; fails = 0; status = "completed"; reason = "completed"

    def _analyze(h):
        nonlocal calls
        calls += 1
        return transient_step(h)                                   # NL-R2-20 (residual): clean state for the retry

    while t < t_end - 1e-9:
        if (step_budget and calls >= step_budget) or (wall_budget_s and time.time() - t0 > wall_budget_s):
            status = "incomplete"
            reason = ("incomplete (time-out) at t=%.2f of %.2f s: work budget exhausted (%d analyze calls, budget %s; %.0f s wall, budget %s [%s]); "
                      "all %d committed steps converged (smallest dt %.2e s) -- NOT a 16.4.1.1(1) non-convergence"
                      % (t + toff, t_end + toff, calls, step_budget or "none", time.time() - t0, ("%.0f s" % wall_budget_s) if wall_budget_s else "none", budget_basis, step, dt_min_used))
            break
        ok = _analyze(dt_cur)
        if ok != 0:
            adv = dt_cur / 4
            for alg in ladder:
                ops.algorithm(*alg); ops.test("NormDispIncr", max(tol, 1e-5), 100, 0)
                ok = _analyze(adv)
                if ok == 0:
                    break
            ops.algorithm("Newton"); ops.test("NormDispIncr", tol, 30, 0)
            if ok != 0:
                # adaptive time step: halve persistently and retry the same instant (domain is still at the last committed state)
                fails += 1; consec_fail += 1; n_ok_since_cut = 0
                dt_cur *= 0.5
                if dt_cur < dt_floor or consec_fail > 12:
                    status = "nonconvergence"
                    reason = "non-convergence at t=%.2f s (dt %.2e s, %d consecutive failures)" % (t + toff, dt_cur * 2, consec_fail); break
                continue
            # a converged fallback micro-step is a valid equilibrium state (16.4.1.1(1) is about failing to converge,
            # not about slowness). Repeated fallbacks mean Newton cannot take the current step: shrink it (NL-17)
            # instead of aborting the record.
            t += adv; consec_fail = 0; crawl += 1; n_fallback += 1; dt_min_used = min(dt_min_used, adv)
            if crawl >= 4 and dt_cur > dt_floor:
                dt_cur = max(dt_cur * 0.5, dt_floor); crawl = 0; n_ok_since_cut = 0
        else:
            t += dt_cur; consec_fail = 0; n_ok_since_cut += 1; crawl = 0; dt_min_used = min(dt_min_used, dt_cur)
            if dt_cur < dt_max and n_ok_since_cut >= 8:                  # grow back towards the nominal step
                dt_cur = min(dt_max, dt_cur * 2); n_ok_since_cut = 0
        step += 1
        for b in ebf_br:                                            # NL-R2-22: EBF brace axial envelope (every step)
            nb = _truss_axial(b); ev = ebf_env[b]
            if nb > ev["Pt"]:
                ev["Pt"] = nb
            if -nb > ev["Pc"]:
                ev["Pc"] = -nb
        # 16.4.1.2 drift at vertically aligned points, both directions (NL-12)
        disp = {n: (ops.nodeDisp(n, 1), ops.nodeDisp(n, 2)) for n in pnodes}
        dnow, at = DR.story_drifts(pts, disp)
        for i in range(len(lv)):
            for c in (0, 1):
                if dnow[i, c] > peak_drift[i, c]:
                    peak_drift[i, c] = dnow[i, c]; peak_at[i][c] = at[i][c]
        roof = lv[-1][2]; ur = np.array([ops.nodeDisp(roof, 1), ops.nodeDisp(roof, 2)])
        peak_roof = np.maximum(peak_roof, np.abs(ur))
        if t >= next_hist - 1e-9:
            hist_t.append(t + toff); hist_roof.append(ur.tolist()); next_hist += 5 * dt_max   # NL-R2-18: record time axis
        if t >= next_rec - 1e-9:
            next_rec += rec_every * dt_max
            frames_t.append(round(t + toff, 3))
            frames_story.append([[round(ops.nodeDisp(m, 1), 3), round(ops.nodeDisp(m, 2), 3), round(ops.nodeDisp(m, 6), 6)] for m in masters])
            frames_brace.append([round(ops.eleResponse(b, "deformation")[0], 4) for b in braces])
            ir = min(int(t / dt_rec), n_rec - 1); frames_ag.append([round(float(ax[ir] * sf), 4), round(float(ay[ir] * sf), 4)])
            hinge_row = []
            for tg in hz:
                h = hinges[tg]
                if h["kind"] == "brace":
                    d = ops.eleResponse(tg, "deformation"); v = d[0] if d else 0.0
                elif h.get("form") == "fibre_end":            # fibre beam/column end region (pushover NL-01)
                    v = NM.fibre_end_rotation(h)
                elif h.get("form") == "fbc_cp":
                    # ConcentratedPlasticity end IP: Uniaxial M–θ_p (comp 0). Subtract My/Ke elastic.
                    ip = h.get("sec_ip", 1); jc = h.get("sec_comp", 0)
                    d = ops.eleResponse(h["ele"], "section", ip, "deformation")
                    f = ops.eleResponse(h["ele"], "section", ip, "force")
                    if d and len(d) > jc:
                        fj = f[jc] if (f and len(f) > jc) else 0.0
                        v = d[jc] - fj / K0[tg]
                    else:
                        v = 0.0
                else:
                    th, M = NM.zero_length_spring(tg, h["dof"])      # spring force = node-2 force (sign fix)
                    v = th - M / K0[tg]
                peak_def[tg] = max(peak_def[tg], abs(v))
                p, n = signed_def[tg]; signed_def[tg] = (max(p, v), min(n, v))
                hinge_row.append(round(v, 6))
            frames_hinge.append(hinge_row)
            for c in cols:
                f = _column_forces(colinfo[c])
                if f is None:
                    continue
                ev = col_env[c]
                ev["Pc"] = max(ev["Pc"], f[0]); ev["Pt"] = min(ev["Pt"], f[0]); ev["Mmaj"] = max(ev["Mmaj"], f[1]); ev["Mmin"] = max(ev["Mmin"], f[2])
                px = colinfo[c].get("proxy")
                if px:                                              # concurrent (P, Mmaj, Mmin) at the most utilised instant / end
                    for trip in f[3]:
                        mm = trip[1] / px[1] + trip[2] / px[2]
                        uc = max(trip[0], 0.0) / px[0] + mm; ut = max(-trip[0], 0.0) / px[0] + mm
                        if uc > ev["_uc"]:
                            ev["_uc"] = uc; ev["conc_c"] = trip
                        if trip[0] < 0 and ut > ev["_ut"]:
                            ev["_ut"] = ut; ev["conc_t"] = trip
                peak_colN[c] = max(peak_colN[c], f[0])
            if sample_brace and sample_brace in hinges:
                d = ops.eleResponse(sample_brace, "deformation")
                brace_hist.append((d[0] if d else 0.0, HM.brace_axial_force(sample_brace, hinges[sample_brace])))   # NL-10: physical-theory braces
    if status == "nonconvergence":                                  # NL-R2-22: say when the EBF links have lost strength
        reason += link_state_note(hinges)
    # residual drift (structure at rest after free vibration) -- only meaningful when the record completed
    if status == "completed":
        disp = {n: (ops.nodeDisp(n, 1), ops.nodeDisp(n, 2)) for n in pnodes}
        rd, _ = DR.story_drifts(pts, disp); resid = rd.max(axis=1)
    else:
        resid = np.full(len(lv), np.nan)
    col_env = {c: dict(Pc=(v["Pc"] if v["Pc"] > -1e29 else None), Pt=(v["Pt"] if v["Pt"] < 1e29 else None), Mmaj=v["Mmaj"], Mmin=v["Mmin"],
                       conc_c=v["conc_c"], conc_t=v["conc_t"]) for c, v in col_env.items()}
    out = dict(record=rec["id"], label="%s %s (%s)" % (rec.get("earthquake") or rec["id"], rec.get("station") or "", rec.get("year") or "?"), sf=sf, x_comp=rec["x_comp"],
               converged=(status == "completed"), status=status, reason=reason, steps=step, fails=fails, fallback_steps=n_fallback, analyze_calls=calls,
               budget=dict(wall_s=wall_budget_s, basis=budget_basis, step_calls=step_budget, n_nodes=n_nodes),
               dt_min=dt_min_used, t_reached=t + toff, t_end=t_end + toff, seconds=time.time() - t0, t_window=(t_start, t_sig), T1x=modal["T1x"], T1y=modal["T1y"],
               window={k: v for k, v in win.items() if k != "check"} | dict(check={k: v for k, v in (win.get("check") or {}).items() if k != "steps"}),
               damping=damp, algorithmic_damping=alg_damp, solver=dict(newton_disp_tol_in=tol, fallback="legacy ladder" if ladder else "none (Newton with dt halving)"), peak_story_drift=peak_drift.tolist(), peak_drift_at=peak_at, drift_method="aligned_points",
               drift_points=DR.summary(pts), peak_roof_in=peak_roof.tolist(), residual_drift=resid.tolist(),
               peak_def=peak_def, signed_def=signed_def, peak_colN=peak_colN, col_env=col_env, col_grav=col_grav,
               ebf_brace_env={b: dict(Pg=v["Pg"], Pt=max(v["Pt"], v["Pg"]), Pc=max(v["Pc"], -v["Pg"])) for b, v in ebf_env.items()},
               col_flexure={c: ci["flexure"] for c, ci in colinfo.items()}, hist_t=hist_t, hist_roof=hist_roof, brace_hist=brace_hist,
               frames=dict(t=frames_t, story=frames_story, brace_tags=braces, brace=frames_brace, ag=frames_ag,
                           hinge_tags=list(hz), hinge=frames_hinge, masters=masters),
               hinges_meta={t: dict(kind=hinges[t]["kind"], section=hinges[t]["section"], z=hinges[t]["z"], ele=hinges[t]["ele"], end=hinges[t]["end"],
                                    rbs_offset_in=hinges[t].get("rbs_offset_in")) for t in hz},     # NL-R2-16: hinge offset for the Emc beam shear
               specs={t: hinges[t]["spec"] for t in hz}, heights=H, stats=stats)
    if verbose:
        print("[nlrha] %-40s sf=%.2f  %s  steps=%d fails=%d fallback=%d  max drift X %.2f%% Y %.2f%%  roof %.1f/%.1f in  (%.0f s)%s"
              % (out["label"][:40], sf, {"completed": "ok ", "nonconvergence": "NC ", "incomplete": "INC"}[status], step, fails, n_fallback,
                 100 * peak_drift[:, 0].max(), 100 * peak_drift[:, 1].max(), peak_roof[0], peak_roof[1], out["seconds"],
                 "" if status == "completed" else "  -- " + reason))
    return out


def run_record_worker(args):
    """multiprocessing entry: (package_path, params_path, ch16, PG, loads, rec, xi, dt, free_vib, sample_brace[, integrator[, budget]])
    -> result dict. budget = dict(step_budget_factor=.., wall_budget_s=..)."""
    package_path, params_path, ch16, PG, loads, rec, xi, dt, free_vib, sample_brace = args[:10]
    integrator = args[10] if len(args) > 10 else "hht"
    budget = (args[11] if len(args) > 11 else None) or {}
    from pushover import package_reader as PR, hinge_models as HM
    pkg = PR.load(package_path); prm = HM.load_params(params_path)
    out = run_record(pkg, prm, ch16, PG, loads, rec, xi, None, dt_max=dt, free_vib_s=free_vib, sample_brace=sample_brace, integrator=integrator, **budget)
    if out.get("status") in ("nonconvergence", "incomplete") and "gravity" not in out.get("reason", ""):
        # one automatic retry at half the time step: separates numerical loss of convergence (or a time-out) from a
        # genuine dynamic instability (16.4.1.1 counts the record as unacceptable only if it fails to converge again).
        # Disclosed in the record's `retry` field.
        first = dict(reason=out.get("reason"), status=out.get("status"), dt=dt, fails=out.get("fails"))
        out = run_record(pkg, prm, ch16, PG, loads, rec, xi, None, dt_max=dt / 2, free_vib_s=free_vib, sample_brace=sample_brace, integrator=integrator, **budget)
        out["retry"] = dict(first_attempt=first, dt=dt / 2, converged=out.get("converged"), status=out.get("status"))
    return out
