"""report.py -- nlrha_report.html (self-contained) + nlrha_package.json: the Chapter 16 supplement to the Steltic
AISC package and the Pushover supplement."""
from __future__ import annotations
import base64, datetime, io, json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CSS = """
body{font-family:Georgia,'Times New Roman',serif;max-width:1080px;margin:32px auto;padding:0 20px;color:#1b1b1b;line-height:1.45}
h1{font-size:26px;margin-bottom:2px} h2{font-size:19px;border-bottom:2px solid #333;padding-bottom:3px;margin-top:34px} h3{font-size:15px;margin-bottom:4px}
.sub{color:#555;font-size:14px} table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 14px} th,td{border:1px solid #bbb;padding:4px 7px;text-align:right}
th{background:#eee} td:first-child,th:first-child{text-align:left} .banner{background:#b3261e;color:#fff;padding:10px 14px;font-weight:bold;margin:14px 0;border-radius:4px}
.ok{background:#2e7d32;color:#fff;padding:2px 6px;border-radius:3px;font-size:12px} .ng{background:#b3261e;color:#fff;padding:2px 6px;border-radius:3px;font-size:12px}
.warn{background:#f0ad4e;color:#000;padding:2px 6px;border-radius:3px;font-size:12px} .note{background:#f6f6f6;border-left:4px solid #999;padding:8px 12px;font-size:13px;margin:10px 0}
figure{margin:12px 0} figcaption{font-size:12.5px;color:#444} img{max-width:100%} code{font-family:Consolas,monospace;font-size:12.5px;background:#f2f2f2;padding:1px 3px}
"""


def _png(fig):
    b = io.BytesIO(); fig.savefig(b, format="png", dpi=130, bbox_inches="tight"); plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode("ascii")


def _tag(ok, a="PASS", b="NG"):
    return '<span class="ok">%s</span>' % a if ok else '<span class="ng">%s</span>' % b


def fig_scaling(gm):
    T = np.array(gm["periods"]); tgt = np.array(gm["target"]); mean = np.array(gm["suite_mean_rotd100"])
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for r in gm["selected"]:
        ax.plot(T, r["rotd100_scaled"], color="#9db3cc", lw=0.8)
    ax.plot(T, mean, color="#1a3d7c", lw=2.2, label="suite mean of scaled RotD100 (%d pairs)" % len(gm["selected"]))
    ax.plot(T, tgt, color="#b3261e", lw=2, label="target MCE$_R$ (16.2.1.1 / 11.4.6)")
    ax.plot(T, 0.9 * tgt, color="#b3261e", lw=1, ls="--", label="0.9 × target (16.2.3.2 floor)")
    ax.axvspan(gm["T_lower"], gm["T_upper"], color="#f0ad4e", alpha=0.15, label="period range 16.2.3.1: %.2f–%.2f s" % (gm["T_lower"], gm["T_upper"]))
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("period (s)"); ax.set_ylabel("Sa (g), 5% damped"); ax.grid(alpha=.3, which="both")
    ax.legend(fontsize=8); ax.set_title("Ground-motion selection and amplitude scaling", fontsize=11)
    return _png(fig)


def fig_drift(acc, results):
    n = len(acc["story"]); fig, axs = plt.subplots(1, 2, figsize=(8.4, 4.4), sharey=True)
    for d, ax in enumerate(axs):
        for r in results:
            if r["converged"]:
                ax.plot([100 * v[d] for v in r["peak_story_drift"]], range(1, n + 1), color="#9db3cc", lw=0.8)
        ax.plot([100 * s["mean_X" if d == 0 else "mean_Y"] for s in acc["story"]], range(1, n + 1), color="#1a3d7c", lw=2.2, marker="o", label="suite mean (16.4)")
        ax.axvline(100 * acc["limits"]["mean_limit"], color="#b3261e", lw=1.5, ls="--", label="mean limit 16.4.1.2 = %.2f%%" % (100 * acc["limits"]["mean_limit"]))
        ax.axvline(100 * acc["limits"]["unacceptable_peak"], color="#b3261e", lw=1, ls=":", label="150% of mean limit (16.4.1.1)")
        ax.set_xlabel("peak transient story drift ratio (%%) — %s" % ("X" if d == 0 else "Y")); ax.grid(alpha=.3); ax.legend(fontsize=7.5)
    axs[0].set_ylabel("story"); fig.suptitle("Story drift: each record (grey) and the suite statistic", fontsize=11)
    return _png(fig)


def fig_brace(results, label):
    r = next((r for r in results if r.get("brace_hist")), None)
    if not r: return None
    h = np.array(r["brace_hist"]); fig, ax = plt.subplots(figsize=(5.2, 4))
    ax.plot(h[:, 0], h[:, 1], color="#1a3d7c", lw=0.9); ax.axhline(0, color="#999", lw=0.6); ax.axvline(0, color="#999", lw=0.6)
    ax.set_xlabel("axial deformation (in)"); ax.set_ylabel("axial force (kip)"); ax.grid(alpha=.3)
    ax.set_title("Sample brace hysteresis — %s, %s" % (label, r["label"][:30]), fontsize=10)
    return _png(fig)


def write(outdir, pkg, ch16, prm, gm, results, acc, grav_table, grav_split, modal, elapsed_s, pushover_pkg=None):
    os.makedirs(outdir, exist_ok=True); b = pkg.basis; ts = datetime.datetime.now().isoformat(timespec="seconds")
    v = acc["verdict"]; H = []
    H.append("<style>%s</style><title>NLRHA supplement — %s</title>" % (CSS, pkg.name))
    H.append("<h1>Nonlinear response history (ASCE 7-22 Chapter 16) supplement — %s</h1>" % pkg.name)
    H.append('<div class="sub">Supplement to the Steltic AISC 360/341 package <code>%s</code> and its pushover supplement · generated %s · Non Linear Dynamic Bot prototype · %.0f s</div>' % (pkg.root.name, ts, elapsed_s))
    if not prm.get("verified"):
        H.append('<div class="banner">UNVERIFIED COMPONENT PARAMETERS — the hinge and brace backbones come from steltic_pushover/hinge_params.json (verified=false, ASCE 41 / AISC 342 placeholders). '
                 'Cyclic deterioration is OFF in this prototype although 16.3.1 requires it unless shown not to govern. The Chapter 16 procedure itself (spectrum, period range, scaling, gravity, damping, acceptance rules) was read against the converted ASCE 7-22 text (pdf pp. 248–251).</div>')
    H.append('<div class="note"><b>Not for construction.</b> Prototype output produced by an AI-driven tool. Chapter 16 also requires the Chapter 12 linear analysis (16.1.2 — the Steltic package) and independent design review (16.5). Every result must be independently checked and sealed by a licensed professional engineer.</div>')

    H.append("<h2>1. Verdict</h2><table><tr><th>16.4 criterion</th><th>Result</th><th>Clause · pdf p.</th></tr>")
    H.append("<tr><td>Unacceptable responses (16.4.1.1)</td><td>%d of %d records · allowed %d %s</td><td>16.4.1.1 · 250</td></tr>" % (v["n_unacceptable"], v["n_records"], v["unacceptable_allowed"], _tag(v["unacceptable_ok"])))
    H.append("<tr><td>Mean transient story drift ≤ limit</td><td>max mean %.2f%% vs limit %.2f%% %s</td><td>16.4.1.2 · 250</td></tr>" % (100 * (v["mean_drift_max"] or 0), 100 * acc["limits"]["mean_limit"], _tag(v["mean_drift_ok"])))
    H.append("<tr><td>Deformation-controlled elements (mean vs CP · vs valid range b)</td><td>%s · %s</td><td>16.4.2.2 · 251</td></tr>" % (_tag(v["deformation_ok"], "CP ok", "CP exceeded"), _tag(v["valid_range_ok"], "within b", "beyond b")))
    H.append("<tr><td>Force-controlled columns (1.2+0.12S<sub>MS</sub>)D+0.5L+1.3I<sub>e</sub>(Q<sub>u</sub>−Q<sub>ns</sub>) ≤ φBR<sub>n</sub></td><td>%s</td><td>16.4.2.1 · 251</td></tr>" % _tag(v["force_controlled_ok"]))
    H.append("<tr><td>Residual drift (> 240 ft only)</td><td>%s</td><td>16.4.1.3 · 250</td></tr>" % ("n/a — h<sub>n</sub> = %.0f ft" % (acc["hn_in"] / 12) if not v["residual_applicable"] else _tag(v["residual_ok"])))
    H.append("<tr><td><b>Overall</b></td><td><b>%s</b></td><td>16.4</td></tr></table>" % _tag(v["overall"], "ACCEPTABLE", "NOT ACCEPTABLE"))

    H.append("<h2>2. Design basis and Chapter 16 inputs</h2><table><tr><th>Item</th><th>Value</th><th>Basis</th></tr>")
    SMS, SM1 = 1.5 * b.SDS, 1.5 * b.SD1
    for lab, val, src in (("System", b.system, "cfg.py"), ("S<sub>DS</sub> / S<sub>D1</sub> (g)", "%.2f / %.3f" % (b.SDS, b.SD1), "cfg.py"),
                          ("S<sub>MS</sub> / S<sub>M1</sub> (g) = 1.5 × design", "%.2f / %.3f" % (SMS, SM1), "16.2.1.1 → 11.4.6 (pdf 248, 107)"),
                          ("R / C<sub>d</sub> / Ω<sub>0</sub> / I<sub>e</sub>", "%s / %s / %s / %s" % (b.R, b.Cd, b.Om0, b.Ie), "Table 12.2-1 row of the package"),
                          ("T<sub>1X</sub> / T<sub>1Y</sub> of hinge model (s)", "%.3f / %.3f" % (modal["T1x"], modal["T1y"]), "eigen, 16.2.3.1"),
                          ("Period range for scaling (s)", "%.2f – %.2f" % (gm["T_lower"], gm["T_upper"]), "16.2.3.1: ≤0.2 T<sub>min</sub> & 90% mass … ≥ 2 T<sub>max</sub>"),
                          ("Viscous damping", "%.1f%% Rayleigh at T<sub>1</sub> and 0.2 T<sub>1</sub> (elastic elements + mass)" % (100 * ch16["damping"]["xi_used"]), "16.3.5 (≤ 2.5%)"),
                          ("Gravity in the analysis", "1.0 D + 0.5 L, L = %.0f%% of unreduced (≤100 psf) · Σ0.5L/ΣD = %.2f → no-live case %s" % (100 * ch16["gravity"]["live_factor_le100psf"], grav_split["ratio"], "required" if grav_split["no_live_case_needed"] else "not required (exception)"), "16.3.2"),
                          ("P-Δ", "column P-Δ transforms retained from the design model; gravity on all column nodes", "16.3.3"),
                          ("Accidental torsion", "not applied (no Type 1 irregularity declared in the package)", "16.3.4")):
        H.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (lab, val, src))
    H.append("</table>")

    H.append("<h2>3. Ground motions (16.2)</h2>")
    H.append('<figure><img src="%s"><figcaption>Selected pairs (grey), suite mean of the maximum-direction spectra (blue) vs the MCE<sub>R</sub> target and its 90%% floor over the scaling range. Suite mean / target: min %.3f, mean %.3f %s. Orientation check 16.2.4 (±10%%): X %.2f, Y %.2f %s.</figcaption></figure>'
             % (fig_scaling(gm), gm["min_ratio_in_range"], gm["mean_ratio_in_range"], _tag(gm["passes_90pct"]), gm["orientation_dev_x"], gm["orientation_dev_y"], _tag(gm["orientation_ok"])))
    H.append("<table><tr><th>#</th><th>Earthquake · station</th><th>M</th><th>R<sub>rup</sub> km</th><th>Site</th><th>dt (s)</th><th>duration (s)</th><th>scale factor</th><th>comp → X</th><th>shape misfit σ<sub>ln</sub></th></tr>")
    for i, r in enumerate(gm["selected"]):
        H.append("<tr><td>%d</td><td>%s · %s</td><td>%.1f</td><td>%.1f</td><td>%s</td><td>%.4g</td><td>%.1f</td><td>%.2f</td><td>%s</td><td>%.2f</td></tr>"
                 % (i + 1, r["earthquake"], r["station"], r["M"], r["r_rup_km"], r["site_class"], r["dt"], r["duration_s"], r["sf"], r["comp1" if r["x_comp"] == 1 else "comp2"], r["shape_misfit"]))
    H.append("</table><p>Record set: FEMA P-695 far-field set (22 pairs, PEER NGA as distributed by ATC-63). Selection by spectral-shape fit to the target over the scaling range; one amplitude factor per pair; no spectral matching. Site class of the target: from the package (S<sub>DS</sub>, S<sub>D1</sub>), not a site-specific study — 16.2.2 consistency of M / R / tectonic regime with the controlling hazard is <b>not</b> verified by this prototype (open item).</p>")

    H.append("<h2>4. Response per record</h2><table><tr><th>Record</th><th>SF</th><th>converged</th><th>peak story drift</th><th>peak roof X / Y (in)</th><th>residual drift</th><th>unacceptable?</th><th>steps · s</th></tr>")
    for p in acc["per_record"]:
        H.append("<tr><td>%s</td><td>%.2f</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d · %.0f</td></tr>"
                 % (p["label"], p["sf"], ("yes" if p["converged"] else "NO") + (" (retried at dt/2)" if p.get("retry") else ""), ("%.2f%%" % (100 * p["peak_drift"])) if p["converged"] else "—",
                    ("%.1f / %.1f" % tuple(p["peak_roof_in"])) if p["peak_roof_in"] else "—", ("%.3f%%" % (100 * p["residual"])) if p["residual"] is not None else "—",
                    ("<span class='ng'>YES</span> " + "; ".join(p["flags"])) if p["unacceptable"] else "no", p["steps"], p["seconds"]))
    H.append("</table>")
    H.append('<figure><img src="%s"><figcaption>Peak transient story drift per record and the suite statistic per 16.4 (mean, or 120%% of median ≥ mean when one unacceptable record is excluded). Limit = 2 × %.3f (Table 12.12-1 "all other structures", Risk Category %s) and, for h<sub>n</sub> > 100 ft, the 16.4.1.2 height formula (%.2f%%).</figcaption></figure>'
             % (fig_drift(acc, results), acc["limits"].get("table_12_12_1", 0.02), acc["limits"].get("risk_category", "I_II").replace("_", "/"), 100 * acc["limits"]["mean_limit"]))
    fb = fig_brace(results, pkg.name)
    if fb: H.append('<figure><img src="%s"><figcaption>Axial force–deformation history of one brace under one record (Hysteretic backbone, no cyclic deterioration — see banner).</figcaption></figure>' % fb)

    H.append("<h2>5. Element acceptance (16.4.2)</h2><h3>Deformation-controlled — mean of the per-record peaks (Q<sub>u</sub>) vs CP and vs the valid modelling range b</h3>")
    H.append("<table><tr><th>Group</th><th>Section</th><th>elev. (in)</th><th>n</th><th>Q<sub>u</sub></th><th>CP limit</th><th>b (valid range)</th><th>D/C CP</th><th>D/C valid</th></tr>")
    for r in acc["deformation_groups"]:
        if r["kind"] == "brace":
            H.append("<tr><td>brace</td><td>%s</td><td>%d</td><td>%d</td><td>%.2f in comp · %.2f in tens</td><td>%.2f · %.2f in</td><td>%.2f · %.2f in</td><td>%.2f</td><td>%.2f</td></tr>"
                     % (r["section"], r["z_in"], r["n"], r["Qu_comp_in"], r["Qu_tens_in"], r["CP_comp"], r["CP_tens"], r["b_comp"], r["b_tens"], r["DC_CP"], r["DC_valid"]))
        elif r["Qu_rad"] > 1e-5:
            H.append("<tr><td>%s hinge</td><td>%s</td><td>%d</td><td>%d</td><td>%.4f rad</td><td>%.4f</td><td>%.4f</td><td>%.2f</td><td>%.2f</td></tr>"
                     % (r["kind"], r["section"], r["z_in"], r["n"], r["Qu_rad"], r["CP"], r["b"], r["DC_CP"], r["DC_valid"]))
    H.append("</table><h3>Force-controlled — braced-frame and gravity columns, axial (critical; φ = 0.9 per AISC 360, B = 1.0)</h3>")
    H.append("<table><tr><th>Section</th><th>elev. (in)</th><th>Q<sub>u</sub> mean peak (kip)</th><th>Q<sub>ns</sub> gravity (kip)</th><th>Demand (16.4-1)</th><th>φBR<sub>n</sub> (E3, K=1, weak axis)</th><th>KL/r</th><th>D/C</th></tr>")
    for r in acc["force_controlled_columns"]:
        H.append("<tr><td>%s</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%.2f %s</td></tr>"
                 % (r["section"], r["z_in"], r["Qu"], r["Qns"], r["demand"], r["phiBRn"], r["KLr"], r["DC"], _tag(r["DC"] <= 1.0, "ok", "NG")))
    H.append("</table>")

    H.append("<h2>6. What this adds to the linear and pushover packages</h2>")
    if pushover_pkg:
        H.append("<table><tr><th>Quantity</th><th>Steltic (linear, R-reduced)</th><th>Pushover (ASCE 41 NSP)</th><th>NLRHA (Ch. 16, MCE<sub>R</sub>)</th></tr>")
        for d in ("X", "Y"):
            P = pushover_pkg["directions"].get(d, {})
            n2 = P.get("nsp", {}).get("BSE-2N", {}); a2 = P.get("acceptance", {}).get("BSE-2N", {})
            mean_d = max(s["mean_%s" % d] for s in acc["story"]) if acc["story"] else float("nan")
            roofs = [r["peak_roof_in"][0 if d == "X" else 1] for r in results if r["converged"]]
            H.append("<tr><td>MCE<sub>R</sub>-level roof displacement — %s</td><td>C<sub>d</sub>δ<sub>e</sub>: see Ch. 8 of report.html</td><td>δ<sub>t</sub> BSE-2N = %.1f in</td><td>mean of peaks = %.1f in</td></tr>"
                     % (d, n2.get("target_disp_in", float("nan")), float(np.mean(roofs)) if roofs else float("nan")))
            H.append("<tr><td>Max story drift at MCE<sub>R</sub> — %s</td><td>—</td><td>%.2f%%</td><td>%.2f%% (suite statistic)</td></tr>" % (d, 100 * a2.get("max_story_drift", float("nan")), 100 * mean_d))
            H.append("<tr><td>Overstrength / demand — %s</td><td>V = %s kip (R = %s)</td><td>Ω = %.1f, V<sub>max</sub> = %.0f kip</td><td>direct MCE<sub>R</sub> demand; no R, Ω<sub>0</sub>, C<sub>d</sub></td></tr>"
                     % (d, b.V_design_kip, b.R, P.get("p695", {}).get("Omega", float("nan")), P.get("p695", {}).get("Vmax_kip", float("nan"))))
        H.append("</table>")
    H.append("<h2>7. Open items before this supplement is issued</h2><ol>")
    for it in ("Component backbones are unverified placeholders (steltic_pushover/hinge_params.json) — retrieve ASCE 41-23 / AISC 342-22 through Query file manager; add cyclic deterioration (16.3.1).",
               "Ground-motion selection uses spectral-shape fit to a code spectrum; 16.2.2 consistency with the site's controlling M, R and tectonic regime needs the project hazard (or a site-specific Method 2 spectrum).",
               "Gravity is spread equally over each level's column nodes; the no-live-load case (16.3.2) is run only when the exception does not apply — %s here." % ("required" if grav_split["no_live_case_needed"] else "not required"),
               "Accidental torsion is not applied (16.3.4 — only where a Type 1 irregularity exists); inherent eccentricity is whatever the diaphragm master/mass placement in the package gives.",
               "Force-controlled check uses AISC 360 E3 nominal strength computed here (Fy = 50, K = 1, weak axis) — a design value the bot must retrieve, and connections/base plates are not checked.",
               "16.1.4 documentation and 16.5 independent design review are procedural requirements outside this tool."):
        H.append("<li>%s</li>" % it)
    H.append("</ol>")
    with open(os.path.join(outdir, "nlrha_report.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(H))
    pk = dict(building=pkg.name, generated=ts, ch16=ch16, component_params_verified=bool(prm.get("verified")), basis=vars(b) | {"sources": b.sources},
              modal=modal, ground_motions={k: v for k, v in gm.items() if k not in ("selected",)} | {"selected": [{k: v for k, v in r.items() if k != "rotd100_scaled"} for r in gm["selected"]]},
              gravity=dict(table=grav_table, split=grav_split), per_record=acc["per_record"], story=acc["story"], deformation_groups=acc["deformation_groups"],
              force_controlled_columns=acc["force_controlled_columns"], verdict=acc["verdict"], limits=acc["limits"],
              acceptance=acc, meta=acc.get("meta") or {},
              per_record_fc=acc.get("per_record_fc") or [],
              governing_fc_records=acc.get("governing_fc_records") or [],
              results=[{"converged": r.get("converged"), "label": r.get("label"), "record": r.get("record"),
                        "reason": r.get("reason")} for r in results])
    with open(os.path.join(outdir, "nlrha_package.json"), "w", encoding="utf-8") as f:
        json.dump(pk, f, indent=1, default=str)
    return os.path.join(outdir, "nlrha_report.html")
