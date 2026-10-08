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
    if ok is None:
        return '<span class="warn">not computed</span>'
    return '<span class="ok">%s</span>' % a if ok else '<span class="ng">%s</span>' % b


def _pct(v, nd=2):
    """'1.23%' -- or 'not computed' for None / NaN (NL-20: never 'nan%' or a fallback '0.00%')."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "not computed"
    return ("%.*f%%" % (nd, 100 * x)) if np.isfinite(x) else "not computed"


def _num(v, fmt="%.2f", none="—"):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return none
    return (fmt % x) if np.isfinite(x) else none


def _verdict_tag(v):
    st = v.get("status") or ("ACCEPTABLE" if v.get("overall") else "NOT ACCEPTABLE")
    cls = {"ACCEPTABLE": "ok", "NOT ACCEPTABLE": "ng"}.get(st, "warn")
    return '<span class="%s">%s</span>' % (cls, st)


def fig_scaling(gm):
    T = np.array(gm["periods"]); tgt = np.array(gm["target"]); mean = np.array(gm["suite_mean_rotd100"])
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for r in gm["selected"]:
        ax.plot(T, r["rotd100_scaled"], color="#9db3cc", lw=0.8)
    ax.plot(T, mean, color="#1a3d7c", lw=2.2, label="suite mean of scaled RotD100 (%d pairs)" % len(gm["selected"]))
    ax.plot(T, tgt, color="#b3261e", lw=2, label="target: " + (gm.get("target_label") or "MCE$_R$ (16.2.1.1 / 11.4.6)")[:60])
    ax.plot(T, 0.9 * tgt, color="#b3261e", lw=1, ls="--", label="0.9 × target (16.2.3.2 floor)")
    ax.axvspan(gm["T_lower"], gm["T_upper"], color="#f0ad4e", alpha=0.15, label="period range 16.2.3.1: %.2f–%.2f s" % (gm["T_lower"], gm["T_upper"]))
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("period (s)"); ax.set_ylabel("Sa (g), 5% damped"); ax.grid(alpha=.3, which="both")
    ax.legend(fontsize=8); ax.set_title("Ground-motion selection and amplitude scaling", fontsize=11)
    return _png(fig)


def fig_drift(acc, results):
    n = len(acc["story"]); fig, axs = plt.subplots(1, 2, figsize=(8.4, 4.4), sharey=True)
    for d, ax in enumerate(axs):
        for r in results:
            if r.get("converged") and r.get("peak_story_drift"):
                ax.plot([100 * v[d] for v in r["peak_story_drift"]], range(1, n + 1), color="#9db3cc", lw=0.8)
        means = [s["mean_X" if d == 0 else "mean_Y"] for s in acc["story"]]
        if all(m is not None for m in means):
            ax.plot([100 * m for m in means], range(1, n + 1), color="#1a3d7c", lw=2.2, marker="o", label="suite mean (16.4)")
        else:
            ax.text(0.5, 0.5, "suite statistic not computed\n(no completed acceptable record)", transform=ax.transAxes, ha="center", va="center", color="#b3261e")
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


def _gravity_text(ch16, grav_table, grav_split, acc):
    nlc = acc.get("no_live_case") or {}
    req = nlc.get("required", grav_split.get("no_live_case_needed"))
    if "share_L0_lt_100" not in grav_split:                     # results analysed before NL-13
        lg = ((acc.get("meta") or {}).get("legacy_gravity") or {}).get("split_framed") or {}
        return ("<b>these results used the pre-NL-13 gravity</b>: bounding-box level areas, a 20 psf live load on the top roof only, loads spread equally over every slave "
                "(Σ0.5L/ΣD = %.2f). On the framed floor plate Σ0.5L/ΣD = %s → no-live case %s. Re-run for the framed-area loads."
                % (grav_split["ratio"], _num(lg.get("ratio")), "required" if req else "not required (exception)"))
    return ("1.0 D + 0.5 L, L = %.0f%% of unreduced (≤100 psf) on the framed floor plate (%.0f ft<sup>2</sup>; floors at L<sub>0</sub>, roofs and set-back roofs at L<sub>r</sub> %s psf, "
            "included conservatively); each bay lumped to its column corners · Σ0.5L/ΣD = %.2f, L<sub>0</sub> &lt; 100 psf over %.0f%% of the area → no-live case %s"
            % (100 * ch16["gravity"]["live_factor_le100psf"], sum(t.get("area_ft2", 0) for t in grav_table), (grav_split.get("basis") or {}).get("Lr_psf", "20"),
               grav_split["ratio"], 100 * grav_split["share_L0_lt_100"], "required" if req else "not required (exception)"))


def _alg_damping_text(results, ch16):
    ad = next((r.get("algorithmic_damping") for r in results if r.get("algorithmic_damping")), None)
    if not ad:
        return "%s (algorithmic damping not quantified for these results)" % (ch16["damping"].get("integrator") or "HHT").upper()
    if ad.get("alpha") is None:
        return "Newmark average acceleration: no algorithmic damping"
    tot = 100 * ch16["damping"]["xi_used"] + 100 * (ad.get("xi_T1") or 0)
    return ("HHT α = %.2f at dt %.4f s: algorithmic damping %.3f%% at T<sub>1</sub>, %.3f%% at 0.2 T<sub>1</sub> (equivalent viscous, from the amplification matrix); "
            "viscous + algorithmic at T<sub>1</sub> = %.2f%% %s"
            % (ad["alpha"], ch16["damping"].get("dt_s", 0.02), 100 * (ad.get("xi_T1") or 0), 100 * (ad.get("xi_02T1") or 0), tot,
               "(≤ 2.5%)" if tot <= 100 * ch16["damping"]["xi_max"] + 1e-9 else '<span class="warn">exceeds the 2.5% of 16.3.5 — reduce dt or use Newmark</span>'))


_MODEL_STAT_KEYS = ("plasticity", "member_nseg", "col", "beam", "brace", "brace_nonlinear", "brb", "brace_physical_theory", "links",
                    "force_controlled", "released_ends", "panel_zone_mode", "panel_zones", "rbs_remesh_beams", "degradation", "lambda_summary")


def _model_stats(results):
    """NL-R2-05: the element census of the model the records ran on (what the 16.1.4 criteria document reports)."""
    st = next((r.get("stats") for r in results if isinstance(r.get("stats"), dict) and r["stats"]), None) or {}
    return {k: st[k] for k in _MODEL_STAT_KEYS if k in st}


def write(outdir, pkg, ch16, prm, gm, results, acc, grav_table, grav_split, modal, elapsed_s, pushover_pkg=None):
    os.makedirs(outdir, exist_ok=True); b = pkg.basis; ts = datetime.datetime.now().isoformat(timespec="seconds")
    v = acc["verdict"]; H = []
    H.append("<style>%s</style><title>NLRHA supplement — %s</title>" % (CSS, pkg.name))
    H.append("<h1>Nonlinear response history (ASCE 7-22 Chapter 16) supplement — %s</h1>" % pkg.name)
    H.append('<div class="sub">Supplement to the Steltic AISC 360/341 package <code>%s</code> and its pushover supplement · generated %s · Non Linear Dynamic Bot prototype · %.0f s</div>' % (pkg.root.name, ts, elapsed_s))
    deg = next((r.get("stats", {}).get("degradation_16_3_1") for r in results if r.get("stats", {}).get("degradation_16_3_1")), None)
    if not prm.get("verified"):
        H.append('<div class="banner">UNVERIFIED COMPONENT PARAMETERS — the hinge and brace backbones come from steltic_pushover/hinge_params.json (verified=false, ASCE 41 / AISC 342 placeholders). '
                 '%s The Chapter 16 procedure itself (spectrum, period range, scaling, gravity, damping, acceptance rules) was read against the converted ASCE 7-22 text (pdf pp. 248–251).</div>'
                 % ("16.3.1 degradation: %s." % ("modelled for every component family" if deg["demonstrated"] else "NOT demonstrated — see section 1") if deg else
                    "Cyclic deterioration status unknown (results predate the 16.3.1 statement)."))
    H.append('<div class="note"><b>Not for construction.</b> Prototype output produced by an AI-driven tool. Chapter 16 also requires the Chapter 12 linear analysis (16.1.2 — the Steltic package) and independent design review (16.5). Every result must be independently checked and sealed by a licensed professional engineer.</div>')

    H.append("<h2>1. Verdict</h2><table><tr><th>16.4 criterion</th><th>Result</th><th>Clause · pdf p.</th></tr>")
    H.append("<tr><td>Suite (16.2.2: ≥ 11 motions)</td><td>%d of %d records run · %d completed · %d failed to converge · %d incomplete (time-out) · %d not run %s</td><td>16.2.2 · 249</td></tr>"
             % (v["n_records"], v.get("n_suite", v["n_records"]), v.get("n_completed", v["n_records"]), v.get("n_nonconvergence", 0), v.get("n_incomplete", 0), v.get("n_not_run", 0),
                _tag(not (v.get("n_incomplete") or v.get("n_not_run") or v.get("n_suite", 11) < ch16["n_motions"]["min"]), "complete", "incomplete")))
    H.append("<tr><td>Unacceptable responses (16.4.1.1)</td><td>%d of %d records · allowed %d %s</td><td>16.4.1.1 · 250</td></tr>" % (v["n_unacceptable"], v["n_records"], v["unacceptable_allowed"], _tag(v["unacceptable_ok"])))
    H.append("<tr><td>Mean transient story drift ≤ limit (vertically aligned points)</td><td>max mean %s vs limit %s %s</td><td>16.4.1.2 · 250</td></tr>" % (_pct(v.get("mean_drift_max")), _pct(acc["limits"]["mean_limit"]), _tag(v["mean_drift_ok"])))
    H.append("<tr><td>Deformation-controlled elements (mean vs CP · vs valid range b)</td><td>%s · %s</td><td>16.4.2.2 · 251</td></tr>" % (_tag(v["deformation_ok"], "CP ok", "CP exceeded"), _tag(v["valid_range_ok"], "within b", "beyond b")))
    H.append("<tr><td>Force-controlled columns: axial + concurrent flexure (H1-1), Eqs. (1.2+0.12S<sub>MS</sub>)D+0.5L+1.3I<sub>e</sub>(Q<sub>u</sub>−Q<sub>ns</sub>) and (0.9−0.12S<sub>MS</sub>)D+1.3I<sub>e</sub>(Q<sub>u</sub>−Q<sub>ns</sub>) ≤ φBR<sub>n</sub></td><td>worst D/C %s %s</td><td>16.4.2.1 · 251</td></tr>"
             % (_num(v.get("worst_FC_DC")), _tag(v["force_controlled_ok"])))
    H.append("<tr><td>Residual drift (> 240 ft only)</td><td>%s</td><td>16.4.1.3 · 250</td></tr>" % ("n/a — h<sub>n</sub> = %.0f ft" % (acc["hn_in"] / 12) if not v["residual_applicable"] else _tag(v["residual_ok"])))
    nlc = acc.get("no_live_case") or {}
    H.append("<tr><td>Gravity without live load, 1.0 D (16.3.2)</td><td>%s</td><td>16.3.2 · 250</td></tr>"
             % ("not required (exception applies)" if not nlc.get("required") and not nlc.get("run") else
                ("run: %s" % _verdict_tag(nlc["verdict"])) if nlc.get("run") else '<span class="ng">REQUIRED, NOT RUN</span>'))
    H.append("<tr><td><b>Overall</b></td><td><b>%s</b></td><td>16.4</td></tr></table>" % _verdict_tag(v))
    if v.get("not_evaluated"):
        H.append('<div class="note"><b>Not evaluated / incomplete:</b><ul>%s</ul></div>' % "".join("<li>%s</li>" % w for w in v["not_evaluated"]))
    nr = acc.get("records_not_run") or []
    ea = (acc.get("meta") or {}).get("early_abort")
    if nr:
        H.append('<div class="note"><b>Records not run (%d of %d):</b> %s.%s</div>' % (len(nr), v.get("n_suite", len(nr)), "; ".join(str(x.get("label")) for x in nr),
                 (" Basis: %s." % ea["basis"]) if ea else ""))
    if deg:      # NL-10: ASCE 7-22 16.3.1 statement of the degradation in the hysteretic models (pushover.nonlinear_model / nlrha.model)
        H.append('<div class="%s"><b>16.3.1 Degradation in the hysteretic models — %s.</b><ul>%s</ul></div>'
                 % ("note" if deg["demonstrated"] else "banner", "included for every component family present" if deg["demonstrated"] else "NOT DEMONSTRATED",
                    "".join("<li>%s: %s %s</li>" % (i["component"], i["modelled"], "" if i["ok"] else "<b>[not modelled]</b>") for i in deg["items"])))

    H.append("<h2>2. Design basis and Chapter 16 inputs</h2><table><tr><th>Item</th><th>Value</th><th>Basis</th></tr>")
    SMS, SM1 = 1.5 * b.SDS, 1.5 * b.SD1
    for lab, val, src in (("System", b.system, "cfg.py"), ("S<sub>DS</sub> / S<sub>D1</sub> (g)", "%.2f / %.3f" % (b.SDS, b.SD1), "cfg.py"),
                          ("S<sub>MS</sub> / S<sub>M1</sub> (g) = 1.5 × design", "%.2f / %.3f" % (SMS, SM1), "16.2.1.1 → 11.4.6 (pdf 248, 107)"),
                          ("R / C<sub>d</sub> / Ω<sub>0</sub> / I<sub>e</sub>", "%s / %s / %s / %s" % (b.R, b.Cd, b.Om0, b.Ie), "Table 12.2-1 row of the package"),
                          ("T<sub>1X</sub> / T<sub>1Y</sub> of hinge model (s)", "%.3f / %.3f" % (modal["T1x"], modal["T1y"]), "eigen, 16.2.3.1"),
                          ("Period range for scaling (s)", "%.2f – %.2f" % (gm["T_lower"], gm["T_upper"]), "16.2.3.1: ≤0.2 T<sub>min</sub> & 90% mass … ≥ 2 T<sub>max</sub>"),
                          ("Viscous damping", "%.1f%% Rayleigh at T<sub>1</sub> and 0.2 T<sub>1</sub> (elastic elements + mass)" % (100 * ch16["damping"]["xi_used"]), "16.3.5 (≤ 2.5%)"),
                          ("Gravity in the analysis", _gravity_text(ch16, grav_table, grav_split, acc), "16.3.2, 16.3.3"),
                          ("Numerical damping of the integrator", _alg_damping_text(results, ch16), "16.3.5 (disclosure)"),
                          ("Story drift", "absolute difference of the deflections of vertically aligned points at the top and bottom of each story (every plan point of the level above paired with the same point on the nearest level below; double-height spaces divided by their own height); maximum over the points"
                           + ("" if not any(r.get("drift_note") for r in results) else " — <b>these results predate it: drifts recomputed from the recorded master frames (rigid-body kinematics, frame sampling)</b>"), "16.4.1.2"),
                          ("P-Δ", "column P-Δ transforms retained from the design model; gravity on all column nodes", "16.3.3"),
                          ("Accidental torsion", "not applied (no Type 1 irregularity declared in the package)", "16.3.4")):
        H.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (lab, val, src))
    H.append("</table>")

    if grav_table and "area_ft2" in grav_table[0]:
        H.append("<h3>Gravity per level (16.3.2, with live load)</h3><table><tr><th>Level</th><th>z (in)</th><th>framed area (ft<sup>2</sup>) floor · roof</th><th>L<sub>0</sub> · L<sub>r</sub> (psf)</th><th>D (kip)</th><th>0.5L floor · roof (kip)</th><th>Q<sub>G</sub> (kip)</th><th>loaded nodes</th><th>area from</th></tr>")
        for t in grav_table:
            H.append("<tr><td>%s</td><td>%.0f</td><td>%s · %s</td><td>%s · %s</td><td>%.1f</td><td>%.1f · %.1f</td><td>%.1f</td><td>%s</td><td>%s</td></tr>"
                     % (t["level"], t["z_in"], t.get("floor_ft2"), t.get("roof_ft2"), _num(t.get("L0_psf"), "%.0f"), _num(t.get("Lr_psf"), "%.0f"), t["D_kip"],
                        t.get("Lexp_floor_kip", 0.0), t.get("Lexp_roof_kip", 0.0), t["QG_kip"], t.get("nodes"), t.get("method", "")))
        H.append("</table>")

    H.append("<h2>3. Ground motions (16.2)</h2>")
    H.append('<figure><img src="%s"><figcaption>Selected pairs (grey), suite mean of the maximum-direction spectra (blue) vs the MCE<sub>R</sub> target and its 90%% floor over the scaling range. Suite mean / target: min %.3f, mean %.3f %s. Orientation check 16.2.4 (±10%%): X %.2f, Y %.2f %s.</figcaption></figure>'
             % (fig_scaling(gm), gm["min_ratio_in_range"], gm["mean_ratio_in_range"], _tag(gm["passes_90pct"]), gm["orientation_dev_x"], gm["orientation_dev_y"], _tag(gm["orientation_ok"])))
    H.append("<table><tr><th>#</th><th>Earthquake · station</th><th>M</th><th>R<sub>rup</sub> km</th><th>Site</th><th>dt (s)</th><th>duration (s)</th><th>scale factor</th><th>comp → X</th><th>shape misfit σ<sub>ln</sub></th><th>pulse</th></tr>")
    _f = lambda v, fmt: (fmt % v) if isinstance(v, (int, float)) else "—"
    for i, r in enumerate(gm["selected"]):
        H.append("<tr><td>%d</td><td>%s · %s</td><td>%s</td><td>%s</td><td>%s</td><td>%.4g</td><td>%.1f</td><td>%.2f</td><td>%s</td><td>%.2f</td><td>%s</td></tr>"
                 % (i + 1, r.get("earthquake") or r["id"], r.get("station") or "", _f(r.get("M"), "%.1f"), _f(r.get("r_rup_km"), "%.1f"), r.get("site_class") or "—", r["dt"], r["duration_s"], r["sf"], r["comp1" if r["x_comp"] == 1 else "comp2"], r["shape_misfit"], "yes" if r.get("pulse") else ""))
    sets = "; ".join("%s (%s pairs)" % (x.get("set"), x.get("n")) for x in gm.get("sets") or []) or "FEMA P-695 far-field set (22 pairs, PEER NGA as distributed by ATC-63)"
    if gm.get("deagg"):
        d = gm["deagg"]
        site_note = ("Target: %s. Selection ranked by spectral-shape fit plus a 16.2.2 consistency penalty on M and R against the disaggregation mean (M %.2f, R %.0f km, ε %.2f); %d records from a library of %d."
                     % (gm.get("target_label"), d.get("M") or 0, d.get("R_km") or 0, d.get("eps") or 0, len(gm["selected"]), gm.get("n_library", 0)))
        if gm.get("pulse_fraction"):
            site_note += " Near-fault share: %.0f%% of the suite reserved for pulse-type records (%d selected)%s." % (100 * gm["pulse_fraction"], gm.get("n_pulse", 0), (" — " + gm["pulse_note"]) if gm.get("pulse_note") else "")
    else:
        site_note = ("Target: %s. Selection by spectral-shape fit to the target over the scaling range; site class of the target from the package (S<sub>DS</sub>, S<sub>D1</sub>), not a site-specific study — 16.2.2 consistency of M / R / tectonic regime with the controlling hazard is <b>not</b> verified (open item; run <code>nlrha hazard</code> and <code>--target mcer|cs</code>)."
                     % (gm.get("target_label") or "MCE_R = 1.5 x design spectrum"))
    sfb = gm.get("sf_bounds")
    sf_txt = (" Scale factors bounded to %.2f–%.2f before scaling%s." % (sfb[0], sfb[1], (" — <b>%s</b>" % gm["sf_note"]) if gm.get("sf_note") else "")) if sfb else " Scale factors <b>unbounded</b> (no --sf-bounds)."
    H.append("</table><p>Record set: %s. One amplitude factor per pair; no spectral matching.%s %s</p>" % (sets, sf_txt, site_note))

    H.append("<h2>4. Response per record</h2><table><tr><th>Record</th><th>SF</th><th>status</th><th>peak story drift</th><th>peak roof X / Y (in)</th><th>residual drift</th><th>unacceptable?</th><th>steps · s</th></tr>")
    st_lab = {"completed": "completed", "nonconvergence": "<span class='ng'>NO — failed to converge</span>", "incomplete": "<span class='warn'>incomplete (time-out)</span>"}
    for p in acc["per_record"]:
        st = p.get("status") or ("completed" if p["converged"] else "nonconvergence")
        pk = ("%.2f%%" % (100 * p["peak_drift"])) if p.get("peak_drift") is not None else (("≥ %.2f%% (partial)" % (100 * p["peak_drift_lower_bound"])) if p.get("peak_drift_lower_bound") is not None else "not computed")
        roof = p.get("peak_roof_in")
        H.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d · %s</td></tr>"
                 % (p["label"], _num(p.get("sf")), st_lab.get(st, st) + (" (retried at dt/2)" if p.get("retry") else ""), pk,
                    ("%.1f / %.1f" % tuple(roof)) if roof else "—", _pct(p.get("residual"), 3) if p.get("residual") is not None else "—",
                    ("<span class='ng'>YES</span> " + "; ".join(p["flags"])) if p["unacceptable"] else ("<span class='warn'>not decided</span> — " + str(p.get("reason") or "")) if p.get("incomplete") else "no",
                    p.get("steps") or 0, _num(p.get("seconds"), "%.0f")))
    for x in acc.get("records_not_run") or []:
        H.append("<tr><td>%s</td><td>—</td><td><span class='warn'>NOT RUN</span></td><td>not computed</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>" % x.get("label"))
    H.append("</table>")
    H.append('<figure><img src="%s"><figcaption>Peak transient story drift per record and the suite statistic per 16.4 (mean, or 120%% of median ≥ mean when one unacceptable record is excluded). Limit = 2 × %.3f (Table 12.12-1 "all other structures", Risk Category %s) and, for h<sub>n</sub> > 100 ft, the 16.4.1.2 height formula (%.2f%%).</figcaption></figure>'
             % (fig_drift(acc, results), acc["limits"].get("table_12_12_1", 0.02), acc["limits"].get("risk_category", "I_II").replace("_", "/"), 100 * acc["limits"]["mean_limit"]))
    fb = fig_brace(results, pkg.name)
    if fb: H.append('<figure><img src="%s"><figcaption>Axial force–deformation history of one brace under one record (brace model and its degradation: see the 16.3.1 statement in section 1).</figcaption></figure>' % fb)

    H.append("<h2>5. Element acceptance (16.4.2)</h2><h3>Deformation-controlled — mean of the per-record peaks (Q<sub>u</sub>) vs CP and vs the valid modelling range b</h3>")
    H.append("<table><tr><th>Group</th><th>Section</th><th>elev. (in)</th><th>n</th><th>Q<sub>u</sub></th><th>CP limit</th><th>b (valid range)</th><th>D/C CP</th><th>D/C valid</th></tr>")
    for r in acc["deformation_groups"]:
        if r["kind"] == "brace":
            H.append("<tr><td>brace</td><td>%s</td><td>%d</td><td>%d</td><td>%.2f in comp · %.2f in tens</td><td>%.2f · %.2f in</td><td>%.2f · %.2f in</td><td>%.2f</td><td>%.2f</td></tr>"
                     % (r["section"], r["z_in"], r["n"], r["Qu_comp_in"], r["Qu_tens_in"], r["CP_comp"], r["CP_tens"], r["b_comp"], r["b_tens"], r["DC_CP"], r["DC_valid"]))
        elif r["kind"] == "link":         # NL-03: link shear spring -- plastic transverse displacement (in) = gamma_p x e
            H.append("<tr><td>EBF link (shear)</td><td>%s</td><td>%d</td><td>%d</td><td>%.3f in (γ<sub>p</sub>·e)</td><td>%.3f in</td><td>%.3f in</td><td>%.2f</td><td>%.2f</td></tr>"
                     % (r["section"], r["z_in"], r["n"], r["Qu_rad"], r["CP"], r["b"], r["DC_CP"], r["DC_valid"]))
        elif r["Qu_rad"] > 1e-5:
            H.append("<tr><td>%s hinge</td><td>%s</td><td>%d</td><td>%d</td><td>%.4f rad</td><td>%.4f</td><td>%.4f</td><td>%.2f</td><td>%.2f</td></tr>"
                     % (r["kind"], r["section"], r["z_in"], r["n"], r["Qu_rad"], r["CP"], r["b"], r["DC_CP"], r["DC_valid"]))
    H.append("</table><h3>Force-controlled — columns: axial (critical) with concurrent flexure (AISC 360-22 H1-1 ≡ AISC 342-22 C3-9/C3-12/C3-13; φ = 0.9, B = 1.0, F<sub>y</sub> from the component parameters)</h3>")
    H.append("<table><tr><th>Section</th><th>elev. (in)</th><th>P<sub>u</sub> / P<sub>ns</sub> (kip)</th><th>P<sub>r</sub> (16.4-1) / T<sub>r</sub> (16.4-2)</th><th>φP<sub>n</sub> (E3) · KL/r</th>"
             "<th>M<sub>u</sub> major · minor (k-in)</th><th>flexure major · minor (class / model)</th><th>M capacity major · minor</th><th>D/C axial · H1 comp · H1 tens · C3-10</th><th>D/C · governing</th></tr>")
    for r in acc["force_controlled_columns"]:
        H.append("<tr><td>%s</td><td>%.0f</td><td>%s / %s</td><td>%s / %s</td><td>%s · %s</td><td>%s · %s</td><td>%s · %s</td><td>%s · %s</td><td>%s · %s · %s · %s</td><td>%s %s<br><small>%s</small></td></tr>"
                 % (r["section"], r["z_in"], _num(r.get("Qu"), "%.0f"), _num(r.get("Qns"), "%.0f"), _num(r.get("demand"), "%.0f"), _num(r.get("demand_tension"), "%.0f"),
                    _num(r.get("phiBRn"), "%.0f"), _num(r.get("KLr"), "%.0f"), _num(r.get("Mu_maj"), "%.0f"), _num(r.get("Mu_min"), "%.0f"),
                    "%s / %s" % (r.get("flexure_major", "—"), (r.get("modelled") or {}).get("major", "?")), "%s / %s" % (r.get("flexure_minor", "—"), (r.get("modelled") or {}).get("minor", "?")),
                    ("M<sub>CE</sub> %s" if r.get("flexure_major") == "deformation" else "φM<sub>n</sub> %s") % _num(r.get("MCEx") if r.get("flexure_major") == "deformation" else r.get("phiMnx"), "%.0f"),
                    ("M<sub>CE</sub> %s" if r.get("flexure_minor") == "deformation" else "φM<sub>n</sub> %s") % _num(r.get("MCEy") if r.get("flexure_minor") == "deformation" else r.get("phiMny"), "%.0f"),
                    _num(r.get("DC_axial")), _num(r.get("DC_H1_comp")), _num(r.get("DC_H1_tens")), _num(r.get("DC_C3_10")),
                    _num(r.get("DC")), _tag(r["DC"] <= 1.0, "ok", "NG"), r.get("governing", "") + "".join("<br><span class='ng'>%s</span>" % f for f in (r.get("flags") or []))))
    H.append("</table>")
    H.append('<p class="note">Q<sub>ns</sub> is the gravity state of the nonlinear model itself (16.3.2 loads), split into D and 0.5L by the level totals. '
             'Flexure is classified per AISC 342-22 C3.4: deformation-controlled for P<sub>G</sub>/P<sub>ye</sub> ≤ 0.6 (analysed moment with the expected strength M<sub>CE</sub>, F<sub>ye</sub> = R<sub>y</sub>F<sub>y</sub>, C3.4b.2.b with m = 1), '
             'force-controlled above (transformed by the 16.4.2.1 equations, resisted by φM<sub>n</sub>: F2 with L<sub>b</sub> = column length and C<sub>b</sub> = 1, F3/F6). '
             'A deformation-controlled axis modelled elastic (the minor axis of concentrated-hinge columns) whose moment exceeds M<sub>CE</sub> is flagged: the model cannot represent that yielding (16.3.1). '
             'Peaks of P, M<sub>major</sub> and M<sub>minor</sub> are taken independently per record (not concurrent) — conservative.%s</p>'
             % ((" Notes: " + "; ".join(acc["fc_notes"])) if acc.get("fc_notes") else ""))
    if nlc.get("run"):
        vn = nlc["verdict"]
        H.append("<h3>Analysis without live load (1.0 D, 16.3.2)</h3><table><tr><th>Criterion</th><th>Result</th></tr>")
        H.append("<tr><td>Unacceptable responses</td><td>%d of %d (allowed %d) %s</td></tr>" % (vn["n_unacceptable"], vn["n_records"], vn["unacceptable_allowed"], _tag(vn["unacceptable_ok"])))
        H.append("<tr><td>Mean story drift</td><td>max %s %s</td></tr>" % (_pct(vn.get("mean_drift_max")), _tag(vn["mean_drift_ok"])))
        H.append("<tr><td>Deformation-controlled · valid range</td><td>%s · %s</td></tr>" % (_tag(vn["deformation_ok"], "CP ok", "CP exceeded"), _tag(vn["valid_range_ok"], "within b", "beyond b")))
        H.append("<tr><td>Force-controlled columns</td><td>worst D/C %s %s</td></tr>" % (_num(vn.get("worst_FC_DC")), _tag(vn["force_controlled_ok"])))
        H.append("<tr><td>Case verdict</td><td>%s</td></tr></table>" % _verdict_tag(vn))

    H.append("<h2>6. What this adds to the linear and pushover packages</h2>")
    if pushover_pkg:
        H.append("<table><tr><th>Quantity</th><th>Steltic (linear, R-reduced)</th><th>Pushover (ASCE 41 NSP)</th><th>NLRHA (Ch. 16, MCE<sub>R</sub>)</th></tr>")
        for d in ("X", "Y"):
            P = pushover_pkg["directions"].get(d, {})
            n2 = P.get("nsp", {}).get("BSE-2N", {}); a2 = P.get("acceptance", {}).get("BSE-2N", {})
            md = [s["mean_%s" % d] for s in acc["story"] if s.get("mean_%s" % d) is not None]
            mean_d = max(md) if md else None
            roofs = [r["peak_roof_in"][0 if d == "X" else 1] for r in results if r.get("converged") and r.get("peak_roof_in")]
            H.append("<tr><td>MCE<sub>R</sub>-level roof displacement — %s</td><td>C<sub>d</sub>δ<sub>e</sub>: see Ch. 8 of report.html</td><td>δ<sub>t</sub> BSE-2N = %s in</td><td>mean of peaks = %s in</td></tr>"
                     % (d, _num(n2.get("target_disp_in"), "%.1f", "not computed"), _num(float(np.mean(roofs)) if roofs else None, "%.1f", "not computed")))
            H.append("<tr><td>Max story drift at MCE<sub>R</sub> — %s</td><td>—</td><td>%s</td><td>%s (suite statistic)</td></tr>" % (d, _pct(a2.get("max_story_drift")), _pct(mean_d)))
            H.append("<tr><td>Overstrength / demand — %s</td><td>V = %s kip (R = %s)</td><td>Ω = %.1f, V<sub>max</sub> = %.0f kip</td><td>direct MCE<sub>R</sub> demand; no R, Ω<sub>0</sub>, C<sub>d</sub></td></tr>"
                     % (d, b.V_design_kip, b.R, P.get("p695", {}).get("Omega", float("nan")), P.get("p695", {}).get("Vmax_kip", float("nan"))))
        H.append("</table>")
    H.append("<h2>7. Open items before this supplement is issued</h2><ol>")
    for it in ("Component backbones are unverified placeholders (steltic_pushover/hinge_params.json) — retrieve ASCE 41-23 / AISC 342-22 through Query file manager; confirm the cyclic-deterioration parameters (16.3.1).",
               ("Ground-motion selection uses spectral-shape fit to a code spectrum; 16.2.2 consistency with the site's controlling M, R and tectonic regime needs the project hazard (or a site-specific Method 2 spectrum)." if not gm.get("deagg")
                else "Ground motions were ranked against the site disaggregation (16.2.2) and scaled to a site-specific target; the tectonic-regime match and any pulse-type share rest on the library's metadata — confirm them against the project hazard report."),
               "Gravity follows the framed floor plate (bays rebuilt from the package geometry, each bay lumped to its column corners; girder gravity moments are not modelled); the no-live-load case (16.3.2) is %s here%s." % (
                   "required" if nlc.get("required", grav_split["no_live_case_needed"]) else "not required (exception: Σ0.5L ≤ 25% ΣD and L<sub>0</sub> &lt; 100 psf over ≥ 75% of the area)",
                   (" and " + ("was run" if nlc.get("run") else "was NOT run — the verdict cannot be ACCEPTABLE")) if nlc.get("required", grav_split["no_live_case_needed"]) else ""),
               "Accidental torsion is not applied (16.3.4 — only where a Type 1 irregularity exists); inherent eccentricity is whatever the diaphragm master/mass placement in the package gives.",
               "Force-controlled column check: AISC 360 E3 / F2–F6 / H1-1 nominal strengths computed here (F<sub>y</sub> = %s ksi from the component parameters, K = 1, L<sub>b</sub> = column length, C<sub>b</sub> = 1); connections, splices and base plates are not checked." % (((prm.get("material") or {}).get("Fy_ksi")) or 50),
               "16.1.4 documentation and 16.5 independent design review are procedural requirements outside this tool."):
        H.append("<li>%s</li>" % it)
    H.append("</ol>")
    with open(os.path.join(outdir, "nlrha_report.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(H))
    pk = dict(building=pkg.name, generated=ts, ch16=ch16, component_params_verified=bool(prm.get("verified")), basis=vars(b) | {"sources": b.sources},
              modal=modal, ground_motions={k: v for k, v in gm.items() if k not in ("selected",)} | {"selected": [{k: v for k, v in r.items() if k != "rotd100_scaled"} for r in gm["selected"]]},
              gravity=dict(table=grav_table, split=grav_split), per_record=acc["per_record"], story=acc["story"], deformation_groups=acc["deformation_groups"],
              force_controlled_columns=acc["force_controlled_columns"], verdict=acc["verdict"], limits=acc["limits"],
              acceptance=acc, meta=acc.get("meta") or {}, degradation_16_3_1=deg,
              model_stats=_model_stats(results),
              per_record_fc=acc.get("per_record_fc") or [],
              governing_fc_records=acc.get("governing_fc_records") or [],
              records_not_run=acc.get("records_not_run") or [], no_live_case=acc.get("no_live_case") or {},
              results=[{"converged": r.get("converged"), "status": r.get("status") or ("completed" if r.get("converged") else ("incomplete" if str(r.get("reason", "")).startswith("crawl abort") else "nonconvergence")),
                        "label": r.get("label"), "record": r.get("record"), "reason": r.get("reason"), "retry": r.get("retry"),
                        "drift_points": r.get("drift_points"), "peak_drift_at": r.get("peak_drift_at"), "algorithmic_damping": r.get("algorithmic_damping")} for r in results])
    with open(os.path.join(outdir, "nlrha_package.json"), "w", encoding="utf-8") as f:
        json.dump(pk, f, indent=1, default=str)
    return os.path.join(outdir, "nlrha_report.html")
