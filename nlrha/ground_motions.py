"""ground_motions.py -- record library, response spectra, ASCE 7-22 Chapter 16 target spectrum, period range,
selection and amplitude scaling (Sections 16.2.1 / 16.2.2 / 16.2.3.1 / 16.2.3.2 / 16.2.4).

Clause numbers and rules here were read against the user's converted ASCE 7-22 text (pdf pages 248-251);
see ch16_params.json for the exact statements the code implements.
"""
from __future__ import annotations
import json, math, os, re
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
G_IN = 386.4


# --------------------------------------------------------------------------- records
def read_at2(path):
    """PEER NGA .AT2: 4 header lines (line 4 holds NPTS, DT), then accelerations in g."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    hdr = lines[3]
    m = re.search(r"(\d+)\s*[, ]\s*([0-9.]+(?:E[-+]?\d+)?)", hdr)
    if m and "NPTS" in hdr.upper():
        npts, dt = int(m.group(1)), float(m.group(2))
    else:                                    # alternate PEER header "NPTS=  7991, DT=   .0050 SEC"
        m1 = re.search(r"NPTS\s*=\s*(\d+)", hdr); m2 = re.search(r"DT\s*=\s*([0-9.]+)", hdr)
        npts, dt = int(m1.group(1)), float(m2.group(1))
    data = np.array(" ".join(lines[4:]).split(), dtype=float)[:npts]
    return dt, data


def library(set_dir=None):
    set_dir = set_dir or os.path.join(os.path.dirname(_HERE), "records", "p695_farfield")
    idx = json.load(open(os.path.join(set_dir, "index.json")))
    recs = []
    for r in idx["records"]:
        dt1, a1 = read_at2(os.path.join(set_dir, r["comp1"])); dt2, a2 = read_at2(os.path.join(set_dir, r["comp2"]))
        n = min(len(a1), len(a2)); assert abs(dt1 - dt2) < 1e-9
        recs.append(dict(r, dt=dt1, a1=a1[:n], a2=a2[:n], npts=n, duration_s=n * dt1))
    return idx, recs


# --------------------------------------------------------------------------- spectra
def sdof_peak(acc_g, dt, periods, xi=0.05):
    """Peak pseudo-acceleration (g) of 5%-damped linear SDOFs -- piecewise-exact (Nigam-Jennings) integration,
    vectorised over periods."""
    T = np.asarray(periods, float); w = 2 * np.pi / T; wd = w * np.sqrt(1 - xi ** 2)
    e = np.exp(-xi * w * dt); s, c = np.sin(wd * dt), np.cos(wd * dt)
    A11 = e * (c + xi / np.sqrt(1 - xi ** 2) * s); A12 = e * s / wd
    A21 = -e * w / np.sqrt(1 - xi ** 2) * s;     A22 = e * (c - xi / np.sqrt(1 - xi ** 2) * s)
    w2 = w ** 2; w3 = w ** 3
    B11 = e * ((2 * xi ** 2 - 1) / (w2 * dt) + xi / w) * s / wd + e * (2 * xi / (w3 * dt) + 1 / w2) * c - 2 * xi / (w3 * dt)
    B12 = -e * ((2 * xi ** 2 - 1) / (w2 * dt)) * s / wd - e * (2 * xi / (w3 * dt)) * c - 1 / w2 + 2 * xi / (w3 * dt)
    B21 = e * ((2 * xi ** 2 - 1) / (w2 * dt) + xi / w) * (c - xi / np.sqrt(1 - xi ** 2) * s) - e * (2 * xi / (w3 * dt) + 1 / w2) * (wd * s + xi * w * c) + 1 / (w2 * dt)
    B22 = -e * ((2 * xi ** 2 - 1) / (w2 * dt)) * (c - xi / np.sqrt(1 - xi ** 2) * s) + e * (2 * xi / (w3 * dt)) * (wd * s + xi * w * c) - 1 / (w2 * dt)
    ag = np.asarray(acc_g, float) * G_IN
    u = np.zeros_like(T); v = np.zeros_like(T); umax = np.zeros_like(T)
    for i in range(len(ag) - 1):
        u, v = (A11 * u + A12 * v + B11 * ag[i] + B12 * ag[i + 1],
                A21 * u + A22 * v + B21 * ag[i] + B22 * ag[i + 1])
        umax = np.maximum(umax, np.abs(u))
    return umax * w2 / G_IN                 # pseudo-acceleration in g


def rotd100(a1, a2, dt, periods, step_deg=10):
    """Maximum-direction spectrum (16.2.3.2): envelope over rotation angles of the two horizontal components."""
    best = np.zeros(len(periods))
    for th in np.deg2rad(np.arange(0, 180, step_deg)):
        a = a1 * math.cos(th) + a2 * math.sin(th)
        best = np.maximum(best, sdof_peak(a, dt, periods))
    return best


# --------------------------------------------------------------------------- target spectrum
def design_spectrum(T, SDS, SD1, TL):
    """ASCE 7-22 Sec. 11.4.5 general design response spectrum shape (used through 11.4.6 for MCE_R = 1.5x)."""
    T = np.asarray(T, float); Ts = SD1 / SDS; T0 = 0.2 * Ts
    Sa = np.where(T < T0, SDS * (0.4 + 0.6 * T / T0), np.where(T <= Ts, SDS, np.where(T <= TL, SD1 / T, SD1 * TL / T ** 2)))
    return Sa


def target_mcer(T, SDS, SD1, TL):
    """16.2.1.1 Method 1 -> 11.4.6: MCE_R spectrum = 1.5 x design response spectrum (5% damped)."""
    return 1.5 * design_spectrum(T, SDS, SD1, TL)


def period_range(T1x, T1y, T90, factor_upper=2.0):
    """16.2.3.1: upper bound >= 2 x largest first-mode period (1.5x if justified); lower bound <= 0.2 x smallest
    first-mode period AND low enough to include the modes giving 90% mass participation."""
    Tmax, Tmin = max(T1x, T1y), min(T1x, T1y)
    upper = factor_upper * Tmax
    lower = min(0.2 * Tmin, T90 if T90 else 0.2 * Tmin)
    return lower, upper


# --------------------------------------------------------------------------- selection + scaling
def select_and_scale(recs, SDS, SD1, TL, T_lower, T_upper, n_select=11, periods=None, verbose=True):
    """Amplitude scaling per 16.2.3.2: one factor per pair (both components), such that the suite mean of the
    maximum-direction spectra generally matches or exceeds the target and is >= 90% of it at every period in
    [T_lower, T_upper]. Selection: the n pairs whose RotD100 shape best fits the target (smallest log-ratio
    dispersion over the range) -- a spectral-shape criterion consistent with 16.2.2's 'spectral shape similar'."""
    periods = periods if periods is not None else np.geomspace(max(0.05, 0.5 * T_lower), 1.2 * T_upper, 40)
    inrange = (periods >= T_lower) & (periods <= T_upper)
    tgt = target_mcer(periods, SDS, SD1, TL)
    for r in recs:
        r["rotd100"] = rotd100(r["a1"], r["a2"], r["dt"], periods)
        r["sa1"] = sdof_peak(r["a1"], r["dt"], periods); r["sa2"] = sdof_peak(r["a2"], r["dt"], periods)
        lr = np.log(tgt[inrange] / r["rotd100"][inrange])
        r["sf_shape"] = float(np.exp(lr.mean()))             # geometric-mean fit of the record to the target
        r["shape_misfit"] = float(lr.std())
    chosen = sorted(recs, key=lambda r: r["shape_misfit"])[:n_select]
    # start from individual shape-fit factors, then a common multiplier so that mean >= 0.9 target everywhere and >= target on average
    for r in chosen:
        r["sf"] = r["sf_shape"]
    def suite_mean():
        return np.mean([r["sf"] * r["rotd100"] for r in chosen], axis=0)
    mean = suite_mean()
    k = max(1.0, float(np.max(0.9 * tgt[inrange] / mean[inrange])), float(np.mean(tgt[inrange]) / np.mean(mean[inrange])))
    for r in chosen:
        r["sf"] *= k
    mean = suite_mean()
    ratio = mean[inrange] / tgt[inrange]
    # 16.2.4 orientation: alternate comp1 -> X / Y so the per-direction mean component spectra stay within +-10% of the overall mean
    for i, r in enumerate(chosen):
        r["x_comp"] = 1 if i % 2 == 0 else 2
    def dir_means():
        mx = np.mean([r["sf"] * (r["sa1"] if r["x_comp"] == 1 else r["sa2"]) for r in chosen], axis=0)
        my = np.mean([r["sf"] * (r["sa2"] if r["x_comp"] == 1 else r["sa1"]) for r in chosen], axis=0)
        return mx, my
    mx, my = dir_means(); mall = 0.5 * (mx + my)
    devx = float(np.max(np.abs(mx[inrange] / mall[inrange] - 1))); devy = float(np.max(np.abs(my[inrange] / mall[inrange] - 1)))
    # simple greedy swap to reduce the worst deviation
    for _ in range(3):
        if max(devx, devy) <= 0.10:
            break
        best = None
        for r in chosen:
            r["x_comp"] = 3 - r["x_comp"]; mx2, my2 = dir_means(); m2 = 0.5 * (mx2 + my2)
            d = max(float(np.max(np.abs(mx2[inrange] / m2[inrange] - 1))), float(np.max(np.abs(my2[inrange] / m2[inrange] - 1))))
            if best is None or d < best[0]:
                best = (d, r)
            r["x_comp"] = 3 - r["x_comp"]
        if best and best[0] < max(devx, devy):
            best[1]["x_comp"] = 3 - best[1]["x_comp"]; mx, my = dir_means(); mall = 0.5 * (mx + my)
            devx = float(np.max(np.abs(mx[inrange] / mall[inrange] - 1))); devy = float(np.max(np.abs(my[inrange] / mall[inrange] - 1)))
        else:
            break
    out = dict(periods=periods.tolist(), target=tgt.tolist(), T_lower=T_lower, T_upper=T_upper,
               suite_mean_rotd100=mean.tolist(), min_ratio_in_range=float(ratio.min()), mean_ratio_in_range=float(ratio.mean()),
               passes_90pct=bool(ratio.min() >= 0.90 - 1e-6), orientation_dev_x=devx, orientation_dev_y=devy,
               orientation_ok=(max(devx, devy) <= 0.10), common_multiplier=k,
               selected=[dict(id=r["id"], earthquake=r["earthquake"], station=r["station"], M=r["M"], r_rup_km=r["r_rup_km"],
                              site_class=r["site_class"], dt=r["dt"], duration_s=r["duration_s"], sf=r["sf"], shape_misfit=r["shape_misfit"],
                              x_comp=r["x_comp"], comp1=r["comp1"], comp2=r["comp2"], pga_g=r["pga_g"],
                              rotd100_scaled=(r["sf"] * r["rotd100"]).tolist()) for r in chosen])
    if verbose:
        print("[gm] %d pairs selected; period range %.2f-%.2f s; suite mean/target min %.3f mean %.3f (>=0.90: %s); "
              "orientation dev X %.2f Y %.2f (<=0.10: %s); scale factors %.2f-%.2f"
              % (len(chosen), T_lower, T_upper, ratio.min(), ratio.mean(), out["passes_90pct"], devx, devy, out["orientation_ok"],
                 min(r["sf"] for r in chosen), max(r["sf"] for r in chosen)))
    return out, chosen
