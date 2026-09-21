"""run.py -- one ASCE 7-22 Chapter 16 response history: gravity (16.3.2) -> damping (16.3.5) -> bidirectional
uniform excitation (16.2.4) -> HHT-alpha (default) or Newmark integration with adaptive time step -> peak/mean bookkeeping for 16.4.
"""
from __future__ import annotations
import math, time
import numpy as np
import openseespy.opensees as ops
from pushover import nonlinear_model as NM
from . import model as MD

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
    """Two diagonally opposite corner nodes per level, for the 16.4.1.2 'along the edges' drift."""
    out = []
    for k, z, master, slaves in NM.levels(pkg):
        pts = sorted(slaves, key=lambda n: (pkg.model.nodes[n][0], pkg.model.nodes[n][1]))
        out.append((k, z, master, pts[0], pts[-1]))
    return out


def arias_window(a1, a2, dt, lo=0.001, hi=0.995):
    """Time window holding the [lo, hi] fraction of the Arias intensity of the stronger component -- the quiet head
    and tail of a record are skipped (the run still adds free vibration after the window)."""
    ia = np.cumsum(np.asarray(a1) ** 2 + np.asarray(a2) ** 2); ia /= ia[-1]
    i0 = int(np.searchsorted(ia, lo)); i1 = int(np.searchsorted(ia, hi))
    return i0 * dt, i1 * dt


def run_record(pkg, prm, ch16, PG, loads, rec, xi, elastic_eles_cb, dt_max=0.02, free_vib_s=5.0, rec_every=5, verbose=True,
               sample_brace=None, integrator="hht"):
    """Build a fresh model and run one scaled pair. Returns peaks/histories for the acceptance module."""
    t0 = time.time()
    hinges, stats, elastic = MD.build(pkg, prm, ch16, PG)
    ok = _apply_gravity(loads)
    if ok != 0:
        return dict(record=rec["id"], converged=False, reason="gravity stage failed")
    modal = MD.modal(pkg, 6)
    T1 = max(modal["T1x"], modal["T1y"])
    damp = MD.set_damping(xi, T1, elastic)
    # bidirectional excitation, identical factor on both components (16.2.3.2), components per 16.2.4 orientation
    ax = rec["a1"] if rec["x_comp"] == 1 else rec["a2"]; ay = rec["a2"] if rec["x_comp"] == 1 else rec["a1"]
    dt_rec = rec["dt"]; sf = rec["sf"]
    ops.timeSeries("Path", 11, "-dt", dt_rec, "-values", *(ax * G_IN * sf).tolist())
    ops.timeSeries("Path", 12, "-dt", dt_rec, "-values", *(ay * G_IN * sf).tolist())
    ops.pattern("UniformExcitation", 11, 1, "-accel", 11)
    ops.pattern("UniformExcitation", 12, 2, "-accel", 12)
    ops.wipeAnalysis()
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-6, 30, 0); ops.algorithm("Newton")
    if integrator == "newmark":
        ops.integrator("Newmark", 0.5, 0.25)                        # average acceleration, no numerical damping
    else:
        ops.integrator("HHT", 0.9)                                  # alpha = 0.9: second-order accurate, damps the spurious high modes of stiff hinge springs
    ops.analysis("Transient")
    dt = dt_max                                                   # Path series interpolates the record; dt_max ~ T_lower/15
    # HR08 M051: tighten crawl abort — fallback micro-advances can reset consec_fail forever near ModIMK drops (~0.1 s sim/wall-h).
    dt_cur = dt_max; n_ok_since_cut = 0; consec_fail = 0; crawl_fallback = 0; dt_floor = dt_max / 32.0
    next_rec = rec_every * dt_max; next_hist = 5 * dt_max                 # time-based recording (the step is adaptive)
    t_start, t_sig = arias_window(ax, ay, dt_rec)
    t_end = t_sig + free_vib_s
    t = 0.0
    lv = _edge_nodes(pkg); H = [lv[0][1]] + [lv[i][1] - lv[i - 1][1] for i in range(1, len(lv))]
    hz = sorted(hinges); K0 = {t: hinges[t]["K0"] for t in hz}
    cols = [e["tag"] for e in pkg.model.elements if "etype" not in e and NM.member_kind(pkg, e) == "col"]
    peak_drift = np.zeros((len(lv), 2)); peak_roof = np.zeros(2)
    peak_def = {t: 0.0 for t in hz}; signed_def = {t: (0.0, 0.0) for t in hz}       # (max positive, max negative)
    peak_colN = {c: 0.0 for c in cols}
    hist_t, hist_roof = [], []
    brace_hist = []
    frames_t, frames_story, frames_brace, frames_ag = [], [], [], []       # viewer frames (every rec_every steps)
    # ...and the plastic deformation of EVERY hinge at each of those frames, in `hz` order. The loop
    # below already computes `v` per hinge to build peak_def, so keeping it costs no extra
    # eleResponse call. Without it the viewer had nothing per-step for beam and column hinges and
    # fell back to the record peak: every frame -- t = 0 included -- was painted with the worst the
    # record ever reached, so "play" opened on a structure already fully hinged.
    frames_hinge = []
    masters = [m for k, z, m, nA, nB in lv]
    braces = [t for t in hz if hinges[t]["kind"] == "brace"]
    n_rec = len(ax)
    step = 0; fails = 0; converged = True; reason = "completed"
    while t < t_end - 1e-9:
        ok = ops.analyze(1, dt_cur)
        if ok != 0:
            adv = dt_cur / 4
            for alg in (("ModifiedNewton", "-initial"), ("KrylovNewton",), ("NewtonLineSearch",)):
                ops.algorithm(*alg); ops.test("NormDispIncr", 1e-5, 100, 0)
                ok = ops.analyze(1, adv)
                if ok == 0:
                    break
            ops.algorithm("Newton"); ops.test("NormDispIncr", 1e-6, 30, 0)
            if ok != 0:
                # adaptive time step: halve persistently and retry the same instant (domain is still at the last committed state)
                fails += 1; consec_fail += 1; n_ok_since_cut = 0
                dt_cur *= 0.5
                if dt_cur < dt_floor or consec_fail > 12:
                    converged = False; reason = "non-convergence at t=%.2f s (dt %.2e s, %d consecutive failures)" % (t, dt_cur * 2, consec_fail); break
                continue
            t += adv; consec_fail = 0; crawl_fallback += 1
            # abort soft-crawl: many tiny fallback advances without a clean Newton step
            if crawl_fallback > 40:
                converged = False; reason = "crawl abort at t=%.2f s (%d fallback micro-advances)" % (t, crawl_fallback); break
        else:
            t += dt_cur; consec_fail = 0; n_ok_since_cut += 1
            # Do not zero crawl_fallback while still on a cut step — otherwise ModIMK/FBC
            # soft-crawl can reset forever (L2 ConcentratedPlasticity hygiene; cascade order unchanged).
            if dt_cur >= dt_max * 0.999:
                crawl_fallback = 0
            if dt_cur < dt_max and n_ok_since_cut >= 8:                  # grow back towards the nominal step
                dt_cur = min(dt_max, dt_cur * 2); n_ok_since_cut = 0
        step += 1
        # drifts at both edges, both directions (16.4.1.2)
        prev = np.zeros((2, 2))
        for i, (k, z, master, nA, nB) in enumerate(lv):
            uA = np.array([ops.nodeDisp(nA, 1), ops.nodeDisp(nA, 2)]); uB = np.array([ops.nodeDisp(nB, 1), ops.nodeDisp(nB, 2)])
            dA = np.abs(uA - prev[0]) / H[i]; dB = np.abs(uB - prev[1]) / H[i]
            peak_drift[i] = np.maximum(peak_drift[i], np.maximum(dA, dB))
            prev = np.array([uA, uB])
        roof = lv[-1][2]; ur = np.array([ops.nodeDisp(roof, 1), ops.nodeDisp(roof, 2)])
        peak_roof = np.maximum(peak_roof, np.abs(ur))
        if t >= next_hist - 1e-9:
            hist_t.append(t); hist_roof.append(ur.tolist()); next_hist += 5 * dt_max
        if t >= next_rec - 1e-9:
            next_rec += rec_every * dt_max
            frames_t.append(round(t, 3))
            frames_story.append([[round(ops.nodeDisp(m, 1), 3), round(ops.nodeDisp(m, 2), 3), round(ops.nodeDisp(m, 6), 6)] for m in masters])
            frames_brace.append([round(ops.eleResponse(b, "deformation")[0], 4) for b in braces])
            ir = min(int(t / dt_rec), n_rec - 1); frames_ag.append([round(float(ax[ir] * sf), 4), round(float(ay[ir] * sf), 4)])
            hinge_row = []
            for tg in hz:
                h = hinges[tg]
                if h["kind"] == "brace":
                    d = ops.eleResponse(tg, "deformation"); v = d[0] if d else 0.0
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
                    d = ops.eleResponse(tg, "deformation"); f = ops.eleResponse(tg, "force"); j = h["dof"] - 1
                    v = (d[j] - f[j] / K0[tg]) if len(d) >= 6 else 0.0
                peak_def[tg] = max(peak_def[tg], abs(v))
                p, n = signed_def[tg]; signed_def[tg] = (max(p, v), min(n, v))
                hinge_row.append(round(v, 6))
            frames_hinge.append(hinge_row)
            for c in cols:
                f = ops.eleResponse(c, "localForce"); peak_colN[c] = max(peak_colN[c], f[0] if f else 0.0)
            if sample_brace and sample_brace in hinges:
                d = ops.eleResponse(sample_brace, "deformation"); f = ops.eleResponse(sample_brace, "axialForce")
                brace_hist.append((d[0] if d else 0.0, f[0] if f else 0.0))
    # residual drift (structure at rest after free vibration)
    prev = np.zeros((2, 2)); resid = np.zeros(len(lv))
    for i, (k, z, master, nA, nB) in enumerate(lv):
        uA = np.array([ops.nodeDisp(nA, 1), ops.nodeDisp(nA, 2)]); uB = np.array([ops.nodeDisp(nB, 1), ops.nodeDisp(nB, 2)])
        resid[i] = max(np.max(np.abs(uA - prev[0])), np.max(np.abs(uB - prev[1]))) / H[i]; prev = np.array([uA, uB])
    out = dict(record=rec["id"], label="%s %s (%s)" % (rec.get("earthquake") or rec["id"], rec.get("station") or "", rec.get("year") or "?"), sf=sf, x_comp=rec["x_comp"],
               converged=converged, reason=reason, steps=step, fails=fails, seconds=time.time() - t0, t_window=(t_start, t_sig), T1x=modal["T1x"], T1y=modal["T1y"],
               damping=damp, peak_story_drift=peak_drift.tolist(), peak_roof_in=peak_roof.tolist(), residual_drift=resid.tolist(),
               peak_def=peak_def, signed_def=signed_def, peak_colN=peak_colN, hist_t=hist_t, hist_roof=hist_roof, brace_hist=brace_hist,
               frames=dict(t=frames_t, story=frames_story, brace_tags=braces, brace=frames_brace, ag=frames_ag,
                           hinge_tags=list(hz), hinge=frames_hinge, masters=masters),
               hinges_meta={t: dict(kind=hinges[t]["kind"], section=hinges[t]["section"], z=hinges[t]["z"], ele=hinges[t]["ele"], end=hinges[t]["end"]) for t in hz},
               specs={t: hinges[t]["spec"] for t in hz}, heights=H, stats=stats)
    if verbose:
        print("[nlrha] %-40s sf=%.2f  %s  steps=%d fails=%d  max drift X %.2f%% Y %.2f%%  roof %.1f/%.1f in  (%.0f s)"
              % (out["label"][:40], sf, "ok " if converged else "NC ", step, fails, 100 * peak_drift[:, 0].max(), 100 * peak_drift[:, 1].max(),
                 peak_roof[0], peak_roof[1], out["seconds"]))
    return out


def run_record_worker(args):
    """multiprocessing entry: (package_path, params_path, ch16, PG, loads, rec, xi, dt, free_vib, sample_brace) -> result dict."""
    package_path, params_path, ch16, PG, loads, rec, xi, dt, free_vib, sample_brace = args[:10]
    integrator = args[10] if len(args) > 10 else "hht"
    from pushover import package_reader as PR, hinge_models as HM
    pkg = PR.load(package_path); prm = HM.load_params(params_path)
    out = run_record(pkg, prm, ch16, PG, loads, rec, xi, None, dt_max=dt, free_vib_s=free_vib, sample_brace=sample_brace, integrator=integrator)
    if not out.get("converged") and "gravity" not in out.get("reason", ""):
        # one automatic retry at half the time step: separates numerical loss of convergence from a genuine dynamic instability
        # (16.4.1.1 counts the record as unacceptable only if it fails again). Disclosed in the record's `retry` field.
        first = dict(reason=out.get("reason"), dt=dt, fails=out.get("fails"))
        out = run_record(pkg, prm, ch16, PG, loads, rec, xi, None, dt_max=dt / 2, free_vib_s=free_vib, sample_brace=sample_brace, integrator=integrator)
        out["retry"] = dict(first_attempt=first, dt=dt / 2, converged=out.get("converged"))
    return out
