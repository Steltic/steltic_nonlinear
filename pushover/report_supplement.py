"""report_supplement.py -- write pushover_report.html (self-contained, figures inlined) + pushover_package.json.

The HTML is a SUPPLEMENT to Steltic's report.html: it never restates the AISC 360/341 member checks, it adds
the nonlinear-static evidence (capacity curves, idealisation, ASCE 41 target displacements, component
acceptance, mechanism, FEMA P-695 overstrength/ductility) and a comparison table against the linear design basis.
"""
from __future__ import annotations
import base64, datetime, io, json, math, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from . import params_schema as _PS, performance as _PF

CSS = """
body{font-family:Georgia,'Times New Roman',serif;max-width:1050px;margin:32px auto;padding:0 20px;color:#1b1b1b;line-height:1.45}
h1{font-size:26px;margin-bottom:2px} h2{font-size:19px;border-bottom:2px solid #333;padding-bottom:3px;margin-top:34px}
h3{font-size:15px;margin-bottom:4px} .sub{color:#555;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 14px} th,td{border:1px solid #bbb;padding:4px 7px;text-align:right}
th{background:#eee} td:first-child,th:first-child{text-align:left}
.banner{background:#b3261e;color:#fff;padding:10px 14px;font-weight:bold;margin:14px 0;border-radius:4px}
.ok{background:#2e7d32;color:#fff;padding:2px 6px;border-radius:3px;font-size:12px} .ng{background:#b3261e;color:#fff;padding:2px 6px;border-radius:3px;font-size:12px}
.warn{background:#f0ad4e;color:#000;padding:2px 6px;border-radius:3px;font-size:12px}
.note{background:#f6f6f6;border-left:4px solid #999;padding:8px 12px;font-size:13px;margin:10px 0}
figure{margin:12px 0} figcaption{font-size:12.5px;color:#444} img{max-width:100%}
code{font-family:Consolas,monospace;font-size:12.5px;background:#f2f2f2;padding:1px 3px}
"""


def _png(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=130, bbox_inches="tight"); plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def fig_capacity(run, nsp, p695):
    u = np.asarray(run["rec"]["u"]); V = np.asarray(run["rec"]["V"])
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(u, V, color="#1a3d7c", lw=2, label="pushover (%s)" % run["direction"])
    colors = {"BSE-1N": "#2e7d32", "BSE-2N": "#b3261e"}
    for lvl, n in nsp.items():
        ud = n.get("Delta_d", n["target_disp_in"])
        ax.plot([0, n["uy"], ud], [0, n["Vy"], n["Vy"] + n["alpha1"] * n["Ke"] * (ud - n["uy"])],
                "--", color=colors[lvl], lw=1.2, label="bilinear idealisation (%s)" % lvl)
        ax.axvline(n["target_disp_in"], color=colors[lvl], lw=1, ls=":")
        ax.text(n["target_disp_in"], V.max() * 0.05, " δt %s = %.1f in" % (lvl, n["target_disp_in"]), color=colors[lvl],
                rotation=90, va="bottom", fontsize=8.5)
    if p695["V_design_kip"]:
        ax.axhline(p695["V_design_kip"], color="#777", lw=1, ls="-.", label="ELF design base shear V = %.0f kip" % p695["V_design_kip"])
    ax.axhline(0.8 * p695["Vmax_kip"], color="#999", lw=0.8, ls=":")
    ax.set_xlabel("roof displacement (in)"); ax.set_ylabel("base shear (kip)")
    ax.set_title("Capacity curve — push %s (first-mode pattern, P-Δ on)" % run["direction"], fontsize=11)
    ax.grid(alpha=.3); ax.legend(fontsize=8, loc="lower right")
    return _png(fig)


def fig_drift(acc_by_level, heights):
    fig, ax = plt.subplots(figsize=(4.2, 4.4))
    for lvl, acc in acc_by_level.items():
        d = [r["drift_ratio"] * 100 for r in acc["story_drifts"]]
        ax.plot(d, range(1, len(d) + 1), marker="o", label="at δt %s" % lvl)
    ax.set_xlabel("story drift ratio (%)"); ax.set_ylabel("story"); ax.grid(alpha=.3); ax.legend(fontsize=8)
    ax.set_title("Story drift at target displacement", fontsize=10)
    return _png(fig)


def fig_hinges(run, hinges, acc, lvl):
    """Elevation-style census: yielded beam vs column hinges per level at the target displacement."""
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    zs = [c["z_in"] / 12 for c in acc["census"]]
    ax.barh([z - 0.9 for z in zs], [c["beam_yielded"] for c in acc["census"]], height=1.7, color="#1a3d7c", label="beam hinges yielded")
    ax.barh([z + 0.9 for z in zs], [c["col_yielded"] for c in acc["census"]], height=1.7, color="#b3261e", label="column hinges yielded")
    if any(c.get("brace_elements") for c in acc["census"]):
        ax.barh([z + 2.7 for z in zs], [c.get("brace_buckled", 0) for c in acc["census"]], height=1.7, color="#e0a100", label="braces buckled (beyond δc)")
        ax.barh([z + 4.5 for z in zs], [c.get("brace_yielded_T", 0) for c in acc["census"]], height=1.7, color="#2f8f7a", label="braces yielded in tension")
    if any(c.get("brb_elements") for c in acc["census"]):         # NL-02: BRBs yield in compression and tension, they do not buckle
        ax.barh([z + 2.7 for z in zs], [c.get("brb_yielded_C", 0) for c in acc["census"]], height=1.7, color="#7a4fa3", label="BRBs yielded in compression")
        ax.barh([z + 4.5 for z in zs], [c.get("brb_yielded_T", 0) for c in acc["census"]], height=1.7, color="#2f8f7a", label="BRBs yielded in tension")
    if any(c.get("link_hinges") for c in acc["census"]):          # NL-03: EBF link shear springs
        ax.barh([z - 2.7 for z in zs], [c.get("link_yielded", 0) for c in acc["census"]], height=1.7, color="#5b8c2a", label="EBF links yielded in shear")
    ax.set_xlabel("number of yielded hinges (θpl > 0.5 θy)"); ax.set_ylabel("hinge elevation (ft)")
    ax.set_title("Mechanism census at δt %s (roof u = %s in)" % (lvl, _f(acc["roof_disp_in"], 1) if acc.get("roof_disp_in") is not None else "— target not reached"), fontsize=10)
    ax.grid(alpha=.3, axis="x"); ax.legend(fontsize=8)
    return _png(fig)


def _fdc(x):
    """D/C with enough digits that a small non-zero ratio never prints as 0.00 (read as 'nothing monitored')."""
    return _f(x, 3) if isinstance(x, (int, float)) and 0 < x < 0.01 else _f(x)


def _f(x, nd=2):
    if x is None: return "—"
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)): return "∞" if x == float("inf") else "—"
    return ("%%.%df" % nd) % x if isinstance(x, (int, float)) else str(x)


def _hm_txt(n):
    """NL-R2-12: the higher-mode test result for one NSP column."""
    hm = n.get("higher_modes") or {}
    if hm.get("max_ratio") is None:
        return '<span class="warn">NOT EVALUATED</span> %s' % hm.get("reason", "no modal data")
    lab = {"significant": '<span class="ng">significant</span>', "not_significant": '<span class="ok">not significant</span>'}.get(
        hm.get("status"), '<span class="warn">NOT EVALUATED</span>')
    return "%.2f (story %d; %d modes, %.0f%% mass, %s) → %s" % (hm["max_ratio"], hm["story_max"], hm["n_modes_used"],
                                                                100 * (hm.get("cum_mass_frac") or 0), hm.get("combination", ""), lab)


def _nsp_txt(n):
    st = n.get("nsp_status") or ("not_permitted" if n.get("nsp_permitted") is False else "not_evaluated")
    cls = {"permitted": "ok", "not_permitted": "ng", "permitted_with_LDP": "ng"}.get(st, "warn")
    word = {"permitted": "permitted", "not_permitted": "NOT permitted — NDP required",
            "permitted_with_LDP": "NOT permitted alone — supplementary LDP required", "not_evaluated": "NOT EVALUATED"}[st]
    return '<span class="%s">%s</span>' % (cls, word)


def _hm_table(nsp):
    """Per-story ratios of the higher-mode test (NL-R2-12), one table per direction."""
    hm = (nsp.get("BSE-1N") or next(iter(nsp.values()), {})).get("higher_modes") or {}
    if not hm.get("ratios"):
        return ""
    rows = "".join("<tr><td>%d</td><td>%.0f</td><td>%.0f</td><td>%s</td></tr>"
                   % (r["story"], r["V_modal_kip"], r["V_mode1_kip"], _tag(r["ratio"] <= hm["limit"], "%.2f" % r["ratio"], "%.2f" % r["ratio"]))
                   for r in hm["ratios"])
    return ("<h3>Higher-mode significance — ASCE 41-23 §7.3.2.1 item 2</h3><p>Response-spectrum story shears from the pushover "
            "model's modes (initial stiffness, diaphragm level masses): %d modes in eigen order to %.1f%% mass participation in "
            "this direction, combined by %s, against the first-mode-only shears; spectrum %s. Higher-mode effects are "
            "significant where a ratio exceeds 1.30 (then the NSP must be supplemented by an LDP). Result: <b>%s</b> — %s.</p>"
            "<table><tr><th>Story</th><th>V 90%%-mass MRSA (kip)</th><th>V first mode (kip)</th><th>ratio</th></tr>%s</table>"
            % (hm["n_modes_used"], 100 * (hm.get("cum_mass_frac") or 0), hm.get("combination", ""), hm.get("spectrum", ""),
               hm.get("status", "").replace("_", " ").upper(), hm.get("reason", ""), rows))


def _reached_txt(n):
    """NL-R2-24: a push short of delta_t is NOT ACCEPTABLE only after a genuine strength loss / rotation b; a numerical
    stop (or the drift cap) above 0.8 Vmax is NOT EVALUATED."""
    if n.get("reached_target", True):
        return _tag(True, "yes")
    sf = n.get("target_shortfall") or {}
    if sf and sf.get("kind") in ("numerical", "drift_cap"):
        return '<span class="warn">NO — NOT EVALUATED</span> %s' % sf.get("text", "")
    return _tag(False, txt_ng="NO — TARGET NOT REACHED: NOT ACCEPTABLE") + (" %s" % sf["text"] if sf.get("text") else "")


def _tag(ok, txt_ok="PASS", txt_ng="NG"):
    return '<span class="ok">%s</span>' % txt_ok if ok else '<span class="ng">%s</span>' % txt_ng


def write(outdir, pkg, prm, runs, results, gravity_table, hinge_stats, elapsed_s):
    os.makedirs(outdir, exist_ok=True)
    b = pkg.basis
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    H = []
    H.append("<style>%s</style><title>Pushover supplement — %s</title>" % (CSS, pkg.name))
    H.append("<h1>Nonlinear static (pushover) supplement — %s</h1>" % pkg.name)
    H.append('<div class="sub">Supplement to the Steltic AISC 360/341 design package <code>%s</code> · generated %s · '
             'Pushover Analyst prototype · analysis time %.0f s</div>' % (pkg.root.name, ts, elapsed_s))
    if not prm.get("verified"):
        _which = _PS.unverified_text(prm)
        H.append('<div class="banner">UNVERIFIED MODELLING PARAMETERS — %s Values not supplied and verified by the user: %s. '
                 'Do not rely on any acceptance ratio below until every value the model uses has been checked against the printed '
                 'ASCE 41-23 / AISC 342-22 tables. Source note: %s</div>'
                 % ("the parameter file claims verified=true, but some values the model used are not user-verified."
                    if prm.get("_verified_claimed") else "the parameter file has verified=false.",
                    _which or "the whole file (no per-field record)", prm.get("source", "")))
    H.append('<div class="note"><b>Not for construction.</b> Prototype output produced by an AI-driven tool from an automatically converted '
             'analysis model. Every result must be independently checked and sealed by a licensed professional engineer.</div>')
    if hinge_stats and hinge_stats.get("model_warnings"):      # NL-02/03/10: element-model disclosures from the builder
        H.append('<div class="banner">MODEL LIMITATIONS / FALLBACKS<ul>%s</ul></div>' % "".join("<li>%s</li>" % w for w in hinge_stats["model_warnings"]))

    # 1 basis
    H.append("<h2>1. Design basis carried over from the linear package</h2>")
    H.append("<table><tr><th>Item</th><th>Value</th><th>Source in package</th></tr>")
    for k, lab in (("system", "Seismic force-resisting system"), ("SDS", "S<sub>DS</sub> (g)"), ("SD1", "S<sub>D1</sub> (g)"), ("R", "R"),
                   ("Cd", "C<sub>d</sub>"), ("Om0", "Ω<sub>0</sub>"), ("Ie", "I<sub>e</sub>"), ("W_kip", "Effective seismic weight W (kip)"),
                   ("V_design_kip", "ELF design base shear V (kip)"), ("T_design_s", "Design period T (s)"), ("L_floor_psf", "Floor live load (psf)")):
        H.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (lab, _f(getattr(b, k)), b.sources.get(k, "—")))
    H.append("</table>")
    _pz = (prm.get("panel_zones") or {}).get("mode", "rigid")
    _npz = hinge_stats.get("panel_zones", 0) if hinge_stats else 0
    if (hinge_stats or {}).get("plasticity") == "fibre":
        H.append("<p>Model converted from <code>model_opensees.py</code>: %d nodes, %d elements → distributed-plasticity forceBeamColumn "
                 "fibre members (%d columns, %d beams, %d released ends kept pinned; %d force-controlled columns, no deformation monitor). "
                 "Monitored plastic-hinge regions for acceptance: %d beam ends and %d column ends (plastic curvature integrated over the "
                 "end region), %d braces. RBS beams: %d (%d ends with the circular flange cut in the fibres%s). Column P-Δ transforms and "
                 "rigid diaphragms retained from the linear model; panel zones <code>rigid</code> on this path.</p>"
                 % (len(pkg.model.nodes), len(pkg.model.elements), hinge_stats["col"], hinge_stats["beam"], hinge_stats["released_ends"],
                    hinge_stats["force_controlled"], hinge_stats.get("monitored_beam_ends", 0), hinge_stats.get("monitored_col_ends", 0),
                    hinge_stats.get("brace_nonlinear", 0), hinge_stats.get("rbs_beams", 0), hinge_stats.get("rbs_ends", 0),
                    ("; %d with the AISC 358-22 5.7 default geometry" % hinge_stats["rbs_default_geometry"]) if hinge_stats.get("rbs_default_geometry") else ""))
        if hinge_stats.get("unmonitored"):
            H.append('<div class="banner">%d beam/column members have NO deformation monitor (no backbone could be built): %s</div>'
                     % (len(hinge_stats["unmonitored"]), "; ".join("%s %s" % (u["section"], u["why"][:60]) for u in hinge_stats["unmonitored"][:6])))
    else:
        H.append("<p>Model converted from <code>model_opensees.py</code>: %d nodes, %d elements → %d concentrated plastic hinges "
                 "(%d columns, %d beams, %d force-controlled columns without hinge, %d released ends kept pinned). Column P-Δ transforms and "
                 "rigid diaphragms retained from the linear model; hinge springs K<sub>0</sub> = 10·6EI/L with interior stiffness ×11/10; "
                 "panel zones <code>%s</code>%s.</p>"
                 % (len(pkg.model.nodes), len(pkg.model.elements), sum(len(r["hinge_tags"]) for r in runs.values()) // max(len(runs), 1),
                    hinge_stats["col"], hinge_stats["beam"], hinge_stats["force_controlled"], hinge_stats["released_ends"],
                    _pz, (" (%d FR joints)" % _npz) if str(_pz).lower() == "scissors" else ""))
    H.append("<h3>Gravity load present during the push: %s</h3><table><tr><th>Level</th><th>z (in)</th><th>Q<sub>D</sub> (kip)</th>"
             "<th>0.25 Q<sub>L</sub> (kip)</th><th>Q<sub>G</sub> applied (kip)</th><th>column nodes</th><th>footprint (ft²)</th></tr>" % prm["gravity_for_pushover"]["expr"])
    for r in gravity_table:
        H.append("<tr><td>%d</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%.0f</td><td>%d</td><td>%d</td></tr>"
                 % (r["level"], r["z_in"], r["WD_kip"], r["QL25_kip"], r["QG_kip"], r["nodes"], r["area_ft2"]))
    H.append("</table>")

    # 2 per direction
    for d, run in runs.items():
        R = results[d]
        H.append("<h2>2%s. Push %s — capacity curve and ASCE 41 NSP</h2>" % (d.lower(), d))
        H.append('<figure><img src="%s"><figcaption>Figure. Base shear vs roof displacement, bilinear idealisation to Δ<sub>d</sub> = min(δ<sub>t</sub>, Δ at V<sub>max</sub>) (ASCE 41-23 §7.4.3.2.5, Figure 7-3) '
                 'and target displacements for BSE-1N (design) and BSE-2N (MCE<sub>R</sub>). Run stopped: %s.</figcaption></figure>'
                 % (fig_capacity(run, R["nsp"], R["p695"]), run["stop_reason"]))
        H.append("<p>Load pattern: mode %d of the hinge model (T<sub>1</sub> = %.3f s, %.0f%% of mass in %s); lateral force at each level "
                 "∝ m<sub>k</sub>φ<sub>k</sub> (§7.4.3.2.3). Control node: roof diaphragm master.</p>"
                 % (run["pattern"]["mode"], run["pattern"]["T1"], 100 * run["pattern"]["meff_frac"], d))
        H.append("<table><tr><th>NSP quantity</th>" + "".join("<th>%s</th>" % l for l in R["nsp"]) + "</tr>")
        rows = (("Spectral S<sub>XS</sub> / S<sub>X1</sub> (g)", lambda n: "%.2f / %.2f" % (n["SXS"], n["SX1"])),
                ("Effective period T<sub>e</sub> = T<sub>i</sub>√(K<sub>i</sub>/K<sub>e</sub>) (s), Eq. (7-28)", lambda n: _f(n["Te"], 3)),
                ("K<sub>i</sub> / K<sub>e</sub> (kip/in)", lambda n: "%.0f / %.0f" % (n["Ki"], n["Ke"])),
                ("Idealised V<sub>y</sub> (kip) · Δ<sub>y</sub> (in)", lambda n: "%.0f · %.2f" % (n["Vy"], n["uy"])),
                ("Post-yield slope α<sub>1</sub>", lambda n: _f(n["alpha1"], 3)),
                ("S<sub>a</sub>(T<sub>e</sub>) (g)", lambda n: _f(n["Sa"], 3)),
                ("C<sub>0</sub> (Γ<sub>1</sub>φ<sub>roof</sub>) · C<sub>1</sub> · C<sub>2</sub>", lambda n: "%.3f · %.3f · %.3f" % (n["C0"], n["C1"], n["C2"])),
                ("Strength ratio μ<sub>strength</sub> = S<sub>a</sub>C<sub>m</sub>/(V<sub>y</sub>/W), Eq. (7-32)", lambda n: "%s (C<sub>m</sub> %s, %s)" % (_f(n["mu_strength"], 3), _f(n.get("Cm"), 2), n.get("Cm_basis", ""))),
                ("μ<sub>max</sub> (Eq. 7-33, α<sub>e</sub> Eq. 7-34) → μ<sub>strength</sub> &lt; μ<sub>max</sub> (§7.3.2.1 item 1)?", lambda n: "%s → %s" % (_f(n["mu_max"], 2), _tag(n.get("nsp_strength_ok", n["nsp_permitted"]), "yes", "NO — use NDP"))),
                ("Higher modes (§7.3.2.1 item 2): max story shear 90%-mass MRSA / first mode (limit 1.30)", _hm_txt),
                ("<b>NSP applicability (§7.3.2.1)</b>", _nsp_txt),
                ("<b>Target displacement δ<sub>t</sub> (in) · /H</b>, Eq. (7-29)", lambda n: "<b>%.2f</b> · %.2f%%" % (n["target_disp_in"], 100 * n["target_over_H"])),
                ("Push reached δ<sub>t</sub>? (§7.4.3.3.1)", _reached_txt),
                ("Curve pushed to ≥ 1.5 δ<sub>t</sub>?", lambda n: _tag(n["reached_150pct"], "yes", "NO — extend push")))
        for lab, fn in rows:
            H.append("<tr><td>%s</td>%s</tr>" % (lab, "".join("<td>%s</td>" % fn(n) for n in R["nsp"].values())))
        H.append("</table>")
        H.append(_hm_table(R["nsp"]))

        p = R["p695"]
        H.append("<h3>FEMA P-695-style factors from this curve</h3><table><tr><th>Factor</th><th>Value</th><th>Design basis</th><th>Reading</th></tr>")
        H.append("<tr><td>Overstrength Ω = V<sub>max</sub>/V</td><td>%s (V<sub>max</sub> = %.0f kip, V<sub>max</sub>/W = %.2f)</td><td>Ω<sub>0</sub> = %s</td><td>%s</td></tr>"
                 % (_f(p["Omega"]), p["Vmax_kip"], p["Vmax_over_W"], _f(b.Om0, 1),
                    "system overstrength far exceeds the tabulated Ω<sub>0</sub> — heavy drift/serviceability-governed sections; capacity-design forces bounded by Ω<sub>0</sub>Q<sub>E</sub> are not an upper bound here"
                    if (p["Omega"] or 0) > 1.5 * (b.Om0 or 3) else "within the usual range of the tabulated Ω<sub>0</sub>"))
        H.append("<tr><td>Period-based ductility μ<sub>T</sub> = δ<sub>u</sub>/δ<sub>y,eff</sub></td><td>%s (δ<sub>u</sub> = %.1f in %s; δ<sub>y,eff</sub> = %.2f in, T = %.2f s)</td><td>R = %s, C<sub>d</sub> = %s</td><td>%s</td></tr>"
                 % (_f(p["mu_T"]), p["delta_u_in"], p["delta_u_basis"], p["delta_y_eff_in"], p["T_used_s"], _f(b.R, 0), _f(b.Cd, 1),
                    "μ<sub>T</sub> ≥ 3 is the P-695 threshold for full spectral-shape credit" if p["mu_T"] >= 3 else "limited ductility — review hinge parameters and mechanism"))
        t = run.get("tail", {})
        _st = t.get("status")
        if _st == "not_needed":                         # packages written before NL-22: classify from the curve itself
            _st = "captured" if run["rec"]["V"][-1] <= 0.8 * p["Vmax_kip"] else "lower_bound"
        tail_txt = {"captured": "<span class='ok'>captured</span> the curve fell to 0.8 V<sub>max</sub>",
                    "lower_bound": "<span class='ng'>LOWER BOUND</span> solver stopped before 0.8 V<sub>max</sub>",
                    "max_drift": "<span class='warn'>max drift</span> reached the drift cap before 0.8 V<sub>max</sub>",
                    "component_limit": "<span class='ok'>component limit</span> hinges reached rotation <em>b</em> (loss of gravity capacity) before 0.8 V<sub>max</sub>; δ<sub>u</sub> taken there (P-695 non-simulated collapse rule)"}.get(_st, "—")
        tried = ", ".join("%s (%d steps, u %.1f→%.1f in, V/V<sub>max</sub> %.2f)" % (x["strategy"], x["steps"], x["u_from"], x["u_to"], x["V_end_over_Vmax"]) for x in t.get("tried", [])) \
            or ("none needed" if _st in ("captured", "component_limit") else "none (escalation disabled or not applicable)")
        H.append("<tr><td>Descending branch (δ<sub>u</sub> basis)</td><td colspan='3'>%s · escalation tried: %s</td></tr>" % (tail_txt, tried))
        if prm.get("numerics", {}).get("post_cap_ratio_overridden"):
            H.append("<tr><td>Modelling change disclosed</td><td colspan='3'><span class='warn'>post_cap_ratio = %.2f</span> — hinges descend from M<sub>c</sub> to the residual over %.0f%% of <em>a</em> instead of the default 15%% (near-vertical ASCE 41 drop). Approved by the user to capture the descending branch; affects δ<sub>u</sub>/μ<sub>T</sub> and α<sub>2</sub>, not δ<sub>t</sub> or acceptance below capping.</td></tr>"
                     % (prm["numerics"]["post_cap_ratio"], 100 * prm["numerics"]["post_cap_ratio"]))
        H.append("</table>")

        # acceptance
        H.append("<h3>Component acceptance at the target displacements</h3>")
        _rc = prm.get("_risk_category") or "I_II"
        H.append("<p>BPON structural performance levels — %s.</p>" % _PF.describe(_rc))
        for lvl, acc in R["acc"].items():
            perf = _PF.level_for(_rc, lvl)                           # ASCE 41-23 Table 2-5 by Risk Category
            st = acc.get("status", "evaluated")
            if st != "evaluated":
                H.append('<p><b>%s → δ<sub>t</sub> = %.2f in · performance level %s (%s): <span class="ng">%s</span></b> — %s</p>'
                         % (lvl, acc.get("target_disp_in", R["nsp"][lvl]["target_disp_in"]), perf, _PF.LEVEL_NAMES[perf],
                            "TARGET NOT REACHED — NOT ACCEPTABLE" if st == "target_not_reached" else "NOT EVALUATED", acc.get("note", "")))
                if acc.get("at_last_converged"):
                    lc = acc["at_last_converged"]
                    H.append('<p class="note">Diagnostic only, NOT at δ<sub>t</sub>: last converged roof u = %.2f in, worst D/C IO %s / LS %s / CP %s.</p>'
                             % (lc["roof_disp_in"], _f(lc["worst_DC"].get("IO")), _f(lc["worst_DC"].get("LS")), _f(lc["worst_DC"].get("CP"))))
                continue
            if perf not in acc["worst_DC"]:
                _PF.augment_groups(acc)
            H.append("<p><b>%s → δ<sub>t</sub> = %.2f in (step %d) · performance level checked: %s (%s) · worst D/C: IO %s, DC %s, LS %s, LtdS %s, CP %s %s · "
                     "max story drift %.2f%% · max column axial %s kip</b> · monitored: %s</p>"
                     % (lvl, acc["roof_disp_in"], acc["step"], perf, _PF.LEVEL_NAMES[perf], _fdc(acc["worst_DC"]["IO"]), _fdc(acc["worst_DC"]["DC"]),
                        _fdc(acc["worst_DC"]["LS"]), _fdc(acc["worst_DC"]["LtdS"]), _fdc(acc["worst_DC"]["CP"]),
                        _tag(acc["worst_DC"][perf] is not None and acc["worst_DC"][perf] <= 1.0), 100 * acc["max_story_drift"], _f(acc["col_N_max_kip"], 0),
                        ", ".join("%s %d" % (k, v) for k, v in (acc.get("monitored") or {}).items() if v)))
            H.append("<table><tr><th>Hinge group</th><th>Section</th><th>elev. (in)</th><th>hinges</th><th>yielded</th><th>θ<sub>pl,max</sub> (rad) · braces: |Δ|<sub>max</sub> (in) · BRB: total |Δ| (in) · links: γ<sub>p</sub>·e (in)</th>"
                     "<th>IO limit</th><th>LS limit</th><th>CP limit</th><th>%s limit</th><th>D/C IO</th><th>D/C LS</th><th>D/C CP</th><th>D/C %s</th></tr>" % (perf, perf))
            for g in acc["groups"]:
                if g["n_yielded"] == 0 and g["theta_pl_max"] < 1e-4:
                    continue
                H.append("<tr><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%.4f</td><td>%.4f</td><td>%.4f</td><td>%.4f</td><td>%.4f</td><td>%.2f</td><td>%.2f</td><td>%.2f</td><td><b>%.2f</b></td></tr>"
                         % ("BRB" if g.get("brb") else ("link (shear, in)" if g["kind"] == "link" else g["kind"]), g["section"], g["z_in"], g["n"], g["n_yielded"], g["theta_pl_max"], g["IO"], g["LS"], g["CP"], g.get(perf) or 0.0,
                            g["DC_IO"], g["DC_LS"], g["DC_CP"], g.get("DC_" + perf) or 0.0))
            H.append("</table>")
            H.append('<figure><img src="%s"><figcaption>Mechanism census at %s — a beam-hinging (strong-column) mechanism shows blue bars at every level and red '
                     'only at the base; red bars mid-height indicate column hinging (story mechanism) and must be reconciled with the AISC 341 SCWB check in the '
                     'main report.</figcaption></figure>' % (fig_hinges(run, R["hinges"], acc, lvl), lvl))
        H.append('<figure><img src="%s"><figcaption>Story drift ratios at the target displacements (compare with the C<sub>d</sub>-amplified design drift '
                 'and the 2%% ASCE 7 limit reported in Chapter 8 of the main report).</figcaption></figure>' % fig_drift(R["acc"], run["heights"]))

    # 3 comparison
    H.append("<h2>3. What this adds to the AISC design package</h2>")
    H.append("<table><tr><th>Question the linear package cannot answer</th><th>Pushover evidence</th><th>Where it lands in the main report</th></tr>")
    for d, run in runs.items():
        R = results[d]; n1 = R["nsp"]["BSE-1N"]; n2 = R["nsp"]["BSE-2N"]; p = R["p695"]
        H.append("<tr><td>Actual overstrength vs Ω<sub>0</sub> = %s (%s)</td><td>Ω = %s</td><td>Ch. 9 capacity design — Ω<sub>0</sub>Q<sub>E</sub> column/collector forces</td></tr>"
                 % (_f(b.Om0, 1), d, _f(p["Omega"])))
        H.append("<tr><td>Does a ductile (beam-hinging) mechanism form? (%s)</td><td>%d beam / %d column hinges yielded at δ<sub>t</sub> BSE-2N</td><td>Ch. 9 SCWB ratio</td></tr>"
                 % (d, sum(c["beam_yielded"] for c in R["acc"]["BSE-2N"]["census"]), sum(c["col_yielded"] for c in R["acc"]["BSE-2N"]["census"])))
        _md = R["acc"]["BSE-2N"].get("max_story_drift")
        H.append("<tr><td>Inelastic drift demand at MCE<sub>R</sub> (%s)</td><td>roof δ<sub>t</sub>/H = %.2f%%, max story %s</td><td>Ch. 8 drift (C<sub>d</sub>δ<sub>e</sub>/I<sub>e</sub>)</td></tr>"
                 % (d, 100 * n2["target_over_H"], ("%.2f%%" % (100 * _md)) if _md is not None else "— (δ<sub>t</sub> not reached)"))
        def _lvl(a, k):
            return _fdc(a["worst_DC"].get(k)) if a.get("status", "evaluated") == "evaluated" else ("TARGET NOT REACHED" if a.get("status") == "target_not_reached" else "NOT EVALUATED")
        _l1, _l2 = _PF.bpon_levels(prm.get("_risk_category") or "I_II")
        H.append("<tr><td>Component deformation acceptance (%s)</td><td>%s D/C %s at BSE-1N · %s D/C %s at BSE-2N</td><td>Ch. 6 member D/C (strength only)</td></tr>"
                 % (d, _l1, _lvl(R["acc"]["BSE-1N"], _l1), _l2, _lvl(R["acc"]["BSE-2N"], _l2)))
    H.append("</table>")

    # 4 verification list
    H.append("<h2>4. Items the Pushover Analyst must verify before this supplement is issued</h2><ol>")
    pz_mode = (prm.get("panel_zones") or {}).get("mode", "rigid")
    pz_mat = (prm.get("panel_zones") or {}).get("material", "elastic")
    n_pz = (hinge_stats or {}).get("panel_zones", 0)
    if str(pz_mode).lower() == "scissors":
        pz_item = ("Panel zones: scissors-style joint rotational springs (mode=<code>scissors</code>, material=<code>%s</code>, "
                   "%d FR joints) at the centreline between the column node and FR-beam attachment node; "
                   "IMK end hinges retained. Not a full 8-bar Krawinkler model (no geometric rigid offsets). "
                   "Do not also apply the C5.4a PZ ductility modifier. Bare frame, no composite slab — consider NIST GCR 17-917-46v2."
                   % (pz_mat, n_pz))
    else:
        pz_item = ("Panel zones rigid, no composite-slab stiffness, bare centreline model — same idealisation as the linear package; "
                   "set <code>panel_zones.mode=scissors</code> in hinge_params for opt-in flexible PZ (ATC-114); consider NIST GCR 17-917-46v2.")
    items = [("Component parameters: values not supplied and verified by the user — %s. Check them against the printed AISC 342-22 tables "
              "(Tables C2.2 / C3.6 / C3.4 / C5.5, both ductility lines) and remove them from the group's <code>unverified</code> list."
              % (_PS.unverified_text(prm) or "the file has verified=false")) if not prm.get("verified") else
             "Component parameters: every value the model used is user-supplied (%s)." % str(prm.get("source") or "")[:200],
             "NSP per ASCE 41-23: idealisation §7.4.3.2.5 (Figure 7-3), T<sub>e</sub> Eq. (7-28), δ<sub>t</sub> Eq. (7-29), C<sub>1</sub> (7-30), C<sub>2</sub> (7-31), μ<sub>strength</sub> (7-32), μ<sub>max</sub> (7-33), α<sub>e</sub> (7-34), C<sub>m</sub> Table 7-4, C<sub>0</sub> = Γ<sub>1</sub>φ<sub>roof</sub> (§7.4.3.3.2 first option); α<sub>PΔ</sub> approximated by the first-storey stability coefficient.",
             "Site class for C<sub>1</sub> assumed %s (a = %d); T<sub>L</sub> ignored in the spectrum; BSE-2N taken as 1.5 × design spectrum — confirm with the project hazard." % (prm["nsp"]["default_site_class"], prm["nsp"]["C1_site_factor_a"][prm["nsp"]["default_site_class"]]),
             "Gravity in the push distributed equally to column nodes per level (footprint from node extents); replace with tributary loads from model_static.py for irregular plans.",
             pz_item,
             "Force-controlled actions (column axial, connection welds/bolts) reported as P/P<sub>ye</sub> only; run the Eq. 7-38 check with γχ factors.",
             "Higher-mode check (ASCE 41-23 §7.3.2.1 item 2), computed here from the pushover model's modes: %s. Where significant, "
             "the NSP is permitted only with a supplementary LDP (not part of this supplement)." % "; ".join(
                 "%s: %s" % (d, (lambda hm: ("max ratio %.2f at story %d → %s" % (hm["max_ratio"], hm["story_max"], hm["status"].replace("_", " ")))
                                 if hm.get("max_ratio") is not None else "NOT EVALUATED (%s)" % hm.get("reason", ""))(
                     (results[d]["nsp"].get("BSE-1N") or {}).get("higher_modes") or {})) for d in runs)]
    H += ["<li>%s</li>" % i for i in items]
    H.append("</ol>")
    html = "\n".join(H)
    with open(os.path.join(outdir, "pushover_report.html"), "w", encoding="utf-8") as f:
        f.write(html)
    # JSON package (no per-step arrays except the curves)
    pk = dict(building=pkg.name, generated=ts, params_verified=bool(prm.get("verified")), params_source=prm.get("source"),
              params_unverified=prm.get("_used_unverified") or {}, params_verified_claimed=bool(prm.get("_verified_claimed")),
              risk_category=prm.get("_risk_category"), bpon_levels=list(_PF.bpon_levels(prm.get("_risk_category") or "I_II")),
              numerics=prm.get("numerics", {}),
              basis=vars(b) | {"sources": b.sources}, hinge_stats=hinge_stats, gravity=gravity_table, directions={})
    for d, run in runs.items():
        R = results[d]
        pk["directions"][d] = dict(T1=run["pattern"]["T1"], mode=run["pattern"]["mode"], meff_frac=run["pattern"]["meff_frac"],
                                   stop_reason=run["stop_reason"], tail=run.get("tail"), curve_u_in=[round(x, 4) for x in run["rec"]["u"]],
                                   curve_V_kip=[round(x, 2) for x in run["rec"]["V"]], nsp=R["nsp"], p695=R["p695"],
                                   acceptance={k: {kk: vv for kk, vv in a.items()} for k, a in R["acc"].items()})
        with open(os.path.join(outdir, "curve_%s.csv" % d), "w") as f:
            f.write("roof_disp_in,base_shear_kip\n" + "\n".join("%.5f,%.3f" % (u, V) for u, V in zip(run["rec"]["u"], run["rec"]["V"])))
    with open(os.path.join(outdir, "pushover_package.json"), "w", encoding="utf-8") as f:
        json.dump(pk, f, indent=1, default=str)
    return os.path.join(outdir, "pushover_report.html")
