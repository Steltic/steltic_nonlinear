"""acceptance.py -- ASCE 7-22 Section 16.4 evaluation of a suite of response histories (rules in ch16_params.json)."""
from __future__ import annotations
import math, re
import numpy as np
from pushover import sections_db as SDB

E_KSI = 29000.0


def risk_category(pkg, override=None):
    """'I_II' | 'III' | 'IV' -- from --risk-category, else cfg.py text ('Risk Category IV', RC III, risk="IV"), else Ie."""
    if override:
        o = override.upper().replace(" ", "")
        return "I_II" if o in ("I", "II", "I_II", "I/II") else o
    try:
        src = (pkg.root / "cfg.py").read_text(encoding="utf-8", errors="replace")
        mo = re.search(r"(?:risk[\s_]*category|\bRC)\s*[:=]?\s*['\"]?\b(IV|III|II|I)\b", src, re.I)
        if mo:
            v = mo.group(1).upper(); return "I_II" if v in ("I", "II") else v
    except Exception:
        pass
    Ie = getattr(pkg.basis, "Ie", None) or 1.0
    return "IV" if Ie >= 1.5 - 1e-9 else ("III" if Ie >= 1.25 - 1e-9 else "I_II")


def drift_limits(ch16, hn_in, H_story_in, rc="I_II"):
    """16.4.1.2: mean limit = 2 x Table 12.12-1 ('all other structures' row for the Risk Category); for hn > 100 ft also the
    tall-building cap hsx(4.71e-2 - 7.14e-5 hn) >= 0.03 hn (as a ratio: 0.0471 - 7.14e-5*hn_ft, floor 0.03). Ratio limits."""
    d = ch16["transient_drift"]
    tab = d.get("table_12_12_1_all_other", {}).get(rc, d["table_12_12_1_all_other_RC_I_II"])
    lim = d["factor_on_table_12_12_1"] * tab
    hn_ft = hn_in / 12.0
    tall = None
    if hn_ft > d["tall_height_ft"]:
        tall = max(d["tall_a"] - d["tall_b"] * hn_ft, d["tall_floor"])
        lim = min(lim, tall)
    return dict(mean_limit=lim, tall_limit=tall, table_12_12_1=tab, risk_category=rc,
                unacceptable_peak=ch16["unacceptable_response"]["peak_drift_factor_of_mean_limit"] * lim)


def column_Pn(section, L_in, Fy=50.0, K=1.0):
    """AISC 360 E3 nominal compressive strength (weak axis, K=1) -- design-value formula, flagged for retrieval."""
    p = SDB.props(section); r = min(p["rx"], p["ry"]); KLr = K * L_in / r
    Fe = math.pi ** 2 * E_KSI / KLr ** 2
    Fcr = (0.658 ** (Fy / Fe)) * Fy if KLr <= 4.71 * math.sqrt(E_KSI / Fy) else 0.877 * Fe
    return Fcr * p["A"], KLr


def evaluate(results, pkg, ch16, PG16, grav_split, SMS, Ie=1.0, phi_col=0.9, B=1.0, rc="I_II"):
    """results: list of run_record outputs. Returns the 16.4 scorecard."""
    ok_runs = [r for r in results if r["converged"]]
    heights = results[0]["heights"]; hn = sum(heights)
    lim = drift_limits(ch16, hn, heights, rc)
    n_story = len(heights)
    # ---- per-record unacceptable-response screen (16.4.1.1)
    per = []
    for r in results:
        flags = []
        if not r["converged"]:
            flags.append("non-convergence (%s)" % r["reason"])
        pk = float(np.max(r["peak_story_drift"])) if r["converged"] else float("nan")
        if r["converged"] and pk > lim["unacceptable_peak"]:
            flags.append("peak story drift %.2f%% > 150%% of mean limit (%.2f%%)" % (100 * pk, 100 * lim["unacceptable_peak"]))
        beyond = []
        if r["converged"]:
            for t, v in r["peak_def"].items():
                s = r["specs"][t]; meta = r["hinges_meta"][t]
                p, n = r["signed_def"][t]
                if meta["kind"] == "brace":
                    if -n > s.b_c or p > s.b_t: beyond.append(t)
                elif v > s.b_pl:
                    beyond.append(t)
        if beyond:
            flags.append("%d deformation-controlled elements beyond the valid modelling range (b)" % len(beyond))
        per.append(dict(record=r["record"], label=r["label"], sf=r["sf"], converged=r["converged"], peak_drift=pk,
                        peak_roof_in=r.get("peak_roof_in"), residual=max(r["residual_drift"]) if r["converged"] else None,
                        unacceptable=bool(flags), flags=flags, seconds=r["seconds"], steps=r["steps"], retry=r.get("retry")))
    n_unacc = sum(1 for p in per if p["unacceptable"])
    acc_runs = [r for r, p in zip(results, per) if not p["unacceptable"]]
    allowed = ch16["unacceptable_response"].get("max_unacceptable", {}).get(rc, ch16["unacceptable_response"]["max_unacceptable_RC_I_II"])
    # ---- mean drift (16.4): mean of all if none unacceptable; else 120% median (>= mean of acceptable) of the acceptable set
    def suite_stat(values):                       # values: array (n_records, ...) from acceptable runs
        v = np.asarray(values, float)
        if n_unacc == 0:
            return v.mean(axis=0)
        return np.maximum(1.2 * np.median(v, axis=0), v.mean(axis=0))
    drifts = np.array([r["peak_story_drift"] for r in acc_runs])          # (n, story, 2)
    mean_drift = suite_stat(drifts) if len(acc_runs) else np.full((n_story, 2), np.nan)
    story_rows = [dict(story=i + 1, h_in=heights[i], mean_X=float(mean_drift[i, 0]), mean_Y=float(mean_drift[i, 1]),
                       max_X=float(drifts[:, i, 0].max()) if len(acc_runs) else None, max_Y=float(drifts[:, i, 1].max()) if len(acc_runs) else None,
                       ok=bool(max(mean_drift[i]) <= lim["mean_limit"])) for i in range(n_story)]
    resid = np.array([r["residual_drift"] for r in acc_runs]); mean_resid = suite_stat(resid) if len(acc_runs) else None
    tall240 = hn / 12.0 > ch16["residual_drift"]["height_ft"]
    # ---- deformation-controlled elements (16.4.2.2): mean peak deformation by group vs CP and vs b
    groups = {}
    for r in acc_runs:
        for t, v in r["peak_def"].items():
            meta = r["hinges_meta"][t]; s = r["specs"][t]
            key = (meta["kind"], meta["section"], round(meta["z"]))
            g = groups.setdefault(key, dict(kind=meta["kind"], section=meta["section"], z_in=round(meta["z"]), n=0, peaks=[], comp=[], tens=[],
                                            CP=s.CP, b=s.b_pl, CP_t=getattr(s, "CP_t", None), b_t=getattr(s, "b_t", None)))
            g["peaks"].append(v)
            if meta["kind"] == "brace":
                p, n = r["signed_def"][t]; g["comp"].append(-n); g["tens"].append(p)
    rows = []
    for g in groups.values():
        peaks = np.array(g["peaks"]).reshape(len(acc_runs), -1) if len(acc_runs) else np.zeros((0, 1))
        worst_elem_mean = float(peaks.mean(axis=0).max()) if peaks.size else 0.0      # mean over records of the worst element? -> per element mean, then max over elements
        if g["kind"] == "brace":
            comp = np.array(g["comp"]).reshape(len(acc_runs), -1); tens = np.array(g["tens"]).reshape(len(acc_runs), -1)
            qc = float(comp.mean(axis=0).max()); qt = float(tens.mean(axis=0).max())
            rows.append(dict(kind="brace", section=g["section"], z_in=g["z_in"], n=peaks.shape[1], Qu_comp_in=qc, Qu_tens_in=qt,
                             CP_comp=g["CP"], CP_tens=g["CP_t"], b_comp=g["b"], b_tens=g["b_t"],
                             DC_CP=max(qc / g["CP"], qt / g["CP_t"]), DC_valid=max(qc / g["b"], qt / g["b_t"])))
        else:
            rows.append(dict(kind=g["kind"], section=g["section"], z_in=g["z_in"], n=peaks.shape[1], Qu_rad=worst_elem_mean,
                             CP=g["CP"], b=g["b"], DC_CP=worst_elem_mean / g["CP"] if g["CP"] > 0 else 0.0,
                             DC_valid=worst_elem_mean / g["b"] if g["b"] > 0 else 0.0))
    rows.sort(key=lambda r: (r["kind"], r["z_in"]))
    # ---- force-controlled columns (16.4.2.1): (1.2+0.12 SMS)D + 0.5L + 1.3 Ie (Qu - Qns) <= phi B Rn
    fc = ch16["force_controlled"]
    colN = {}
    for r in acc_runs:
        for c, v in r["peak_colN"].items():
            colN.setdefault(c, []).append(v)
    frac_D = grav_split["sum_D"] / (grav_split["sum_D"] + grav_split["sum_Lexp"])
    col_rows = []
    for c, vals in colN.items():
        Qu = float(np.mean(vals)); Qns = PG16.get(c, 0.0)
        D = Qns * frac_D; L16 = (Qns - D) / ch16["gravity"]["combination_factor"]        # L of 16.3.2 (before the 0.5)
        dem = (1.2 + 0.12 * SMS) * D + 0.5 * L16 + fc["gamma"] * Ie * max(Qu - Qns, 0.0)
        sec = pkg.schedule.get(c, {}).get("section"); e = next(e for e in pkg.model.elements if e["tag"] == c)
        L = math.dist(pkg.model.nodes[e["n1"]], pkg.model.nodes[e["n2"]])
        Pn, KLr = column_Pn(sec, L)
        col_rows.append(dict(ele=c, section=sec, z_in=pkg.model.nodes[e["n1"]][2], Qu=Qu, Qns=Qns, demand=dem, phiBRn=phi_col * B * Pn, KLr=KLr,
                             DC=dem / (phi_col * B * Pn)))
    # group the worst per (section, z)
    best = {}
    for r in col_rows:
        k = (r["section"], round(r["z_in"]))
        if k not in best or r["DC"] > best[k]["DC"]:
            best[k] = r
    col_table = sorted(best.values(), key=lambda r: (r["z_in"], r["section"]))
    verdict = dict(
        n_records=len(results), n_unacceptable=n_unacc, unacceptable_allowed=allowed, unacceptable_ok=(n_unacc <= allowed),
        mean_drift_ok=all(s["ok"] for s in story_rows), mean_drift_max=float(np.nanmax(mean_drift)) if len(acc_runs) else None,
        deformation_ok=all(r["DC_CP"] <= 1.0 for r in rows), valid_range_ok=all(r["DC_valid"] <= 1.0 for r in rows),
        force_controlled_ok=all(r["DC"] <= 1.0 for r in col_table),
        residual_applicable=tall240, residual_ok=(None if not tall240 else bool(np.max(mean_resid) <= ch16["residual_drift"]["limit"])))
    verdict["overall"] = verdict["unacceptable_ok"] and verdict["mean_drift_ok"] and verdict["deformation_ok"] and verdict["force_controlled_ok"] and (verdict["residual_ok"] in (None, True))
    return dict(limits=lim, per_record=per, story=story_rows, mean_residual=(mean_resid.tolist() if mean_resid is not None else None),
                deformation_groups=rows, force_controlled_columns=col_table, verdict=verdict, hn_in=hn)
